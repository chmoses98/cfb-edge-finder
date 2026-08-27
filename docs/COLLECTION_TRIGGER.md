# Collection Trigger Reliability

How the collector gets invoked, why GitHub's cron alone is not enough,
and what to do when something stops.

## The problem

CLOSING is 14 minutes wide, strictly pre-kickoff, and **unrecoverable**:
once the ball is kicked the closing line is gone permanently. Every other
checkpoint tolerates lateness — T_7D/T_3D/T_24H/T_6H are hours-to-days
wide, and T_90/T_60/T_30 are 60/30/30 minutes wide and may still be
caught late inside their window.

GitHub's scheduled cron cannot protect a 14-minute window. Measured
2026-08-27, against an **hourly** cron, consecutive scheduled collector
runs arrived at gaps of:

```
64  71  52  61  54  56  66  80  49  95  144  171  296  653   (minutes)
```

and after the `*/10` cadence merged at 18:08Z, **zero** scheduled runs
fired in the next three hours. The settlement workflow (`0 */6`) likewise
skipped its 00:00, 12:00 and 18:00 slots the same day.

A 95-minute gap — not even the worst case — destroys a closing line
silently, and every individual run still exits 0.

## The insight

The audit's decisive observation: GitHub's **scheduler** is unreliable,
but its **runner** is not. Every run that started completed normally, in
3.3s idle or 54.6s for a full scan.

So the fix is to stop needing many precise cron firings. A *running* job
can drive the cadence itself and hand off to a successor.

A probe on 2026-08-27T22:02Z confirmed the mechanism works with no
user-supplied credential: a workflow run used the built-in `GITHUB_TOKEN`
to start the next run (run `33120829196`, actor `github-actions[bot]`),
with dispatch-to-start latency under one second.

## Architecture

```
                 ┌──────────────────────────────────────┐
   hourly cron ──►  CONDUCTOR (research-collection-      │
   (restarter)   │  conductor.yml)                       │
                 │   • reads the real schedule           │
                 │   • guards the ~25 min before each    │
                 │     supported kickoff                 │
                 │   • dispatches every 4 min in band    │
                 │   • hands off to a successor          │
                 └───────────────┬──────────────────────┘
                                 │ workflow_dispatch
   */10 cron ────────────────────┼──────────┐
   (fallback)                    │          │
   human dispatch ───────────────┼──────────┤
   (emergency)                   ▼          ▼
                 ┌──────────────────────────────────────┐
                 │  research-capture.yml  (CANONICAL)    │
                 │  concurrency: research-data-write     │
                 │  cancel-in-progress: false            │
                 └──────────────────────────────────────┘
```

**There is one collector.** The conductor dispatches
`research-capture.yml` and does nothing else — no second capture path, no
separate due-label logic, no "external close" implementation. A test
asserts the conductor references none of `resolve_due_labels`,
`price_one_market`, `append_observation`, or `ResearchCorpusRow`.

### Trigger provenance

| Type | Source |
|---|---|
| `EXTERNAL_SCHEDULE` | conductor chain (primary) |
| `GITHUB_SCHEDULE` | `*/10` cron (independent fallback, retained) |
| `MANUAL` | human `workflow_dispatch` (emergency only) |

Conductor and human dispatches both arrive as `workflow_dispatch`, so the
actor disambiguates them. That matters: if they were conflated, a dead
conductor would look alive every time someone pressed Run.

Provenance is operational metadata. It never enters a probability, a
price, or an eligibility decision.

### Concurrency

The conductor is deliberately **not** in `research-data-write`. It spends
most of its life asleep, and a sleeping conductor inside the writers'
group would block every collector run behind it — the fix becoming the
outage. The collector runs it dispatches *do* join that group, so writes
stay serialized exactly as before, `cancel-in-progress: false`.

The conductor also has no `contents: write`. It triggers; it never writes
research data.

## Why the guard band is narrow

Any wait costs runner minutes, so a 24/7 tight loop would be wasteful and
expensive. It is also unnecessary — only CLOSING is both narrow and
unrecoverable, and the `*/10` cron handles everything else even with
substantial drift.

So the tight loop engages only within **25 minutes** of a supported
kickoff. That number is derived, not picked: CLOSING opens at 14 minutes,
and the guard must already have completed a full cycle by then —
14 + 4 (one interval) + ~1 (dispatch + collector) ≈ 20, rounded to 25 for
margin. Kickoffs cluster (noon, 3:30, 7:00), so overlapping bands
collapse into a handful of short windows per game day.

## The anti-runaway invariant

On 2026-08-27T23:01Z one manual conductor dispatch produced 25+ runs at
roughly three per minute. Two defects combined: the conductor could not
read its CFBD credential (it used `Settings()` rather than
`Settings.from_env()`), so it saw zero kickoffs and concluded it had
nothing to guard — and "nothing to guard" then **fell through to an
unconditional self-dispatch**. No collector ran and no research data was
written, but nothing in the repository stopped the chain.

**The invariant now:** a successor is dispatched only when *every* one of
these holds, evaluated in one pure function (`may_dispatch_successor`)
whose default is STOP and which has exactly one `return True`, after all
deny paths:

| # | Condition | Stops |
|---|---|---|
| 1 | self-continue enabled | a flagged-off run |
| 2 | a real continuation reason (job budget reached with work ahead) | **the incident's exact path** |
| 3 | a supported kickoff still inside the horizon | guarding nothing |
| 4 | run lived ≥ 10 min | rapid chaining (~20s observed) |
| 5 | generation < 24 | an endless lineage |
| 6 | chain age < 12 h | a chain outliving its game window |

These are deliberately **independent**. A logic error in any one cannot
by itself recreate a storm: conditions 4, 5 and 6 are structural rate and
lifetime bounds that hold regardless of what the planning logic concluded.
Worst case, generation cap × rate floor puts a hard floor of four hours on
what a runaway could even attempt, against ~20 seconds per generation
observed during the incident.

Each condition has its own test, plus a parametrised test asserting that
*any single* failing condition stops the chain.

### Chain lineage

Every conductor carries `chain_id`, `generation`, and `chain_started_at`,
passed to its successor as workflow inputs. Without lineage a runaway is
invisible — every generation looks like a fresh manual dispatch, and the
generation cap and chain lease cannot be enforced at all. The metadata is
small and lives only in workflow inputs and run logs; nothing
high-volume is persisted.

## Trigger SLA

| | |
|---|---|
| Target interval in band | **4 minutes** |
| Observed dispatch latency | **< 1 second** (probe, 2026-08-27T22:02Z) |
| Allowance used in health maths | 30 seconds (pessimistic) |
| Collector runtime, idle | 3.3 s |
| Collector runtime, full scan | 54.6 s |
| Worst case to land a capture | ~5.4 min |
| CLOSING window | 14 min |
| **Minimum closing slack** | **~8.6 min** |

At a 4-minute interval a 14-minute window gets at least three
opportunities even if one is lost entirely.

**This is a target, not a guarantee.** It depends on GitHub Actions
starting a dispatched run promptly and on the conductor chain being
alive. Neither is contractually assured, which is exactly why the `*/10`
cron is retained underneath and why health is reported per trigger.

## Health

`scripts/week1_readiness.py` reports trigger health judged against
football deadlines, not a fixed staleness bar — 40 minutes quiet is fine
at 3am on a Tuesday and an emergency 12 minutes before kickoff.

| State | Meaning |
|---|---|
| `HEALTHY` | recent enough activity to reach the next checkpoint |
| `WARN` | cadence missed, no critical checkpoint threatened yet |
| `HIGH` | a critical checkpoint is approaching with no recent run |
| `MISSED` | a checkpoint's window closed uncovered (permanent for CLOSING) |

Reported **per trigger**, so a dead conductor is visible even when cron
happens to have fired a minute ago — and vice versa.

## Heartbeat ledger

`data/research/heartbeats/<season>.jsonl`, one small row per collector
invocation: trigger type, timings, success, markets discovered, labels
due/captured, duplicates, malformed rows, API failures, source health,
next supported kickoff, next critical checkpoint.

Deliberately carries **no prices, probabilities, or per-market rows** — a
test enforces that. This is operational telemetry, kept separate so the
immutable research corpus stays uncontaminated. A heartbeat write failure
is swallowed: telemetry must never turn an observability problem into a
data-loss problem.

## Settlement: left on cron deliberately

Settlement stays on `0 */6` with no external trigger. Delayed settlement
has **no data-integrity consequence** — a finished game's final score does
not change, and the settlement job is idempotent, so a slot skipped at
18:00 and picked up at 02:00 produces identical rows. The only cost is
that analytics update later. That is not worth a second always-on trigger
chain, and adding one would double the operational surface for no
correctness gain.

If settlement ever became timing-sensitive, the same conductor could
dispatch it — the mechanism is already proven.

## Operating states

### Normal — no babysitting
The conductor chain runs; the `*/10` cron runs underneath it. Nothing to
do. Confirm occasionally with:

```
python scripts/week1_readiness.py --data-repo-dir <corpus> --season 2026
```

Trigger health should read `HEALTHY`, with a recent
`last success [EXTERNAL_SCHEDULE]`.

### Degraded — one trigger unhealthy
If `last success [EXTERNAL_SCHEDULE]` is stale but cron is firing,
capture continues but CLOSING protection is weakened. Restart the chain:

> Actions → **Research Collection Conductor** → Run workflow

If cron is silent but the conductor is alive, no action is needed — the
conductor is the primary path.

### Emergency — a checkpoint is imminent
If trigger health is `HIGH` and a kickoff is minutes away:

> Actions → **Research Capture** → Run workflow

A manual run honours due labels and duplicate protection exactly like any
other trigger, and can never fabricate a CLOSING label or bypass a safety
lock. It is the emergency path, not the daily architecture.

## What this does not fix

The conductor chain is started and restarted by GitHub cron. If the chain
dies *and* cron stays silent for hours, collection stops. Cron's observed
worst gap was 653 minutes, so this is a real residual risk — reduced, not
eliminated.

Removing it entirely needs a trigger outside GitHub (an external
scheduler calling `repository_dispatch` with a fine-grained token, or a
serverless cron). That requires infrastructure this repository does not
have and a credential nobody has issued, so it is documented rather than
half-built. See "Remaining risk" in `docs/WEEK1_READINESS.md`.
