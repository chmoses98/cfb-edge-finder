# Prospective Collection & Closing-Line Capture

The scheduled regime that captures model + Kalshi state at defined
pregame checkpoints and preserves a reliable closing-line record.

Research-data collection only. No recommendation, qualification, staking,
or execution logic is part of this — and none may be added under cover of
it.

## 1. Snapshot schedule

| Label | Due window (before kickoff) | Width | Duplicate behavior | Late-run behavior |
|---|---|---|---|---|
| `EARLY_OPEN` | first pregame sighting | n/a | once per (game, ticker) ever | still captured on first sighting, however late |
| `T_7D` | 144–192 h (6–8 d) | 48 h | once | may still be captured while in window |
| `T_3D` | 60–84 h (2.5–3.5 d) | 24 h | once | may still be captured while in window |
| `T_24H` | 18–30 h | 12 h | once | may still be captured while in window |
| `T_6H` | 4–8 h | 4 h | once | may still be captured while in window |
| `T_90` | 60–120 min | 60 min | once | may still be captured while in window |
| `T_60` | 45–75 min | 30 min | once | may still be captured while in window |
| `T_30` | 15–45 min | 30 min | once | may still be captured while in window |
| `CLOSING` | **0 < t ≤ 14 min** | 14 min | once | **never backfilled — MISSED the instant kickoff passes** |

Source of truth: `src/cfb_edge_finder/research/timing.py`.

**Duplicate protection.** A label is due only if it is not already in
`already_captured_labels` for that market ticker, which the scanner reads
from the corpus index (one load per scan attempt). Beyond that, the
canonical `observation_key` — a function of `(season, game_id,
market_ticker, timing_label, model_version, capture_window_version)` —
makes a duplicate physically un-writable: `append_json_rows` rejects it.
Two independent layers, one scheduling and one structural.

**Numeric buckets deliberately overlap each other** (e.g. 60–75 min is
both `T_90` and `T_60` territory). That is intentional: after a scheduler
outage a single scan landing in an overlap is legitimately due for both,
and each gets its own row under its own key. **CLOSING is deliberately
disjoint** from all of them (14 < 15, `T_30`'s near edge), so no scan can
ever owe both `T_30` and `CLOSING` for the same market at the same
instant. `test_closing_window_is_disjoint_from_every_numeric_bucket`
fails if anyone widens the closing window past that boundary.

## 2. Closing definition

CLOSING is **the executable pregame quote captured inside a bounded
window immediately before kickoff** — not "the last snapshot we happened
to take."

- **Target window**: `0 < minutes_to_kickoff ≤ 14`.
- **Strictly pre-kickoff**: enforced twice, independently — in
  `timing.is_closing_due` (never due at or after kickoff) and again in
  `closing_capture.evaluate_closing_eligibility` (rejects any
  non-positive `minutes_before_kickoff`), so a caller computing its own
  window still cannot write a post-kickoff closing row.
- **Never backfilled**: unlike the numeric buckets, a late run does not
  get to record CLOSING from post-kickoff data. Once kickoff passes
  without a capture, CLOSING is `MISSED_WINDOW` permanently.
- **Market-status requirement**: the market must be `active`. Executable
  status is an **allow-list** (`{"active"}`), so an unfamiliar Kalshi
  status falls through to non-executable rather than being optimistically
  priced. A `None` status is not executable either — absence of evidence
  is not evidence of a tradeable market.
- **Quote requirement**: at least one of executable YES / NO price must
  exist. An `active` market that produced no quote is
  `CLOSING_MISSING_NO_EXECUTABLE_QUOTE`, not a fabricated price.
- **Fallback**: there is none, by design. If no valid executable quote
  exists in the window, the record is an explicit missing state with a
  reason. CLOSING is **never** inferred from `T_30` or any other
  checkpoint (mission section 9). Downstream analysis may *choose* to
  fall back to the nearest pregame row — that is what `closing.py`'s
  `NEAR_CLOSE` grading is for — but that has to be a visible analytical
  decision, not something persistence quietly did.
- **Kickoff-time source**: CFBD, via `GameRecord.kickoff_utc`, carried on
  every row as `kickoff_utc_at_capture` alongside
  `schedule_source_timestamp`.
- **Timezone**: every timestamp in this system is timezone-aware UTC.
  Schemas use pydantic `AwareDatetime`, so a naive datetime is a
  validation error rather than a silent local-time bug. Cron schedules
  are UTC. No local-time conversion happens anywhere in the capture path.

`research/closing.py` (retrospective grading: EXACT ≤10 min, NEAR_CLOSE
≤60 min, else MISSED) is unchanged and complementary — it grades rows
after the fact; `research/closing_capture.py` decides prospectively
whether a row may be written at all.

## 3. Closing completeness accounting

Every supported market that reaches kickoff lands in exactly one
`ClosingStatus`:

| Status | Meaning |
|---|---|
| `CLOSING_CAPTURED` | a CLOSING row exists |
| `CLOSING_PENDING` | kickoff has not passed; still resolvable (only non-terminal state) |
| `CLOSING_MISSING_MARKET_CLOSED` | market was closed/finalized/settled/determined |
| `CLOSING_MISSING_MARKET_SUSPENDED` | market was suspended/paused/halted |
| `CLOSING_MISSING_API_FAILURE` | a data-source failure prevented capture |
| `CLOSING_MISSING_MAPPING_FAILURE` | market never mapped to a scheduled game |
| `CLOSING_MISSING_NO_EXECUTABLE_QUOTE` | market active but produced no quote |
| `CLOSING_MISSING_NO_SCAN_IN_WINDOW` | **our** coverage gap — no scan ran in the window |
| `CLOSING_NOT_APPLICABLE` | unpriced population, or no kickoff ever |

The split between `NO_SCAN_IN_WINDOW` and the market-condition reasons is
the point: one is our scheduling failure, the other is the market's.
Collapsing them would make a broken scheduler look like an illiquid
market. Every missing closing is written to the capture-state log with its
reason — there is no silent missing closing.

## 4. Scheduling cadence

**Every 10 minutes** (`*/10 * * * *`), raised from hourly.

Hourly could not hit the checkpoints this regime exists to capture:

| Label | Width | Hit rate @ hourly | Hit rate @ 10 min |
|---|---|---|---|
| `T_90` | 60 min | ~100% | 100% |
| `T_60` | 30 min | ~50% | 100% |
| `T_30` | 30 min | ~50% | 100% |
| `CLOSING` | 14 min | ~23% | 100% |

Roughly half of all `T_60`/`T_30` checkpoints and **over three quarters
of all closing lines** would have been missed — and a closing line, alone
among the checkpoints, cannot be recovered after kickoff.

At 10 minutes every window is strictly wider than the interval, so each
is hit at least once. `CLOSING`'s 14-minute window tolerates ~4 minutes
of GitHub scheduler drift; the others tolerate 20+.

**Why not 5 minutes**: GitHub's documented `schedule` floor is 5 minutes
and cron accuracy degrades under platform load, so 5 buys little real
margin while doubling API load. **Why not a separate "final approach"
workflow**: cron cannot be made conditional on kickoff proximity, so it
would fire just as often while adding a second writer to race with.

**Why 6× the runs is not 6× the cost.** Both expensive operations are now
deferred until a checkpoint is genuinely due:

- the multi-season CFBD history fetch (`GameProjectionCache(lines_provider=…)`,
  called at most once and only on the first projection);
- the football model itself (lazily built per event, behind the due-label
  check).

Most runs at this cadence have nothing due and skip both, costing only
schedule + FCS + Kalshi discovery. Deferring is **output-neutral** — the
projection is only ever consumed by `price_one_market`, which is only
reached when `due_labels` is non-empty — and the byte-for-byte
equivalence suite against the frozen pre-optimization algorithm still
passes.

Run load: 144 runs/day. Expensive path only on runs that capture.

## 5. Concurrency

All three corpus writers — `research-capture`, `research-settlement`,
`research-weekly-report` — share the **single** group
`research-data-write`, with `cancel-in-progress: false`.

**Deliberate deviation, documented.** Mission section 6 asks for a group
"dedicated to prospective research collection." A group dedicated to
collection alone would serialize this workflow against itself but still
let a collection run and a settlement run interleave on the same branch.
The shared group is the strictly stronger reading of the actual
requirement — *only one run may write to the research corpus at a time* —
so it is what is implemented.
`tests/test_research_workflow_concurrency.py` fails if any
`contents: write` workflow drifts off the shared group.

**Queue, never cancel.** `cancel-in-progress: false` because cancelling
could kill a run between its fsync'd append and its git push, and a
checkpoint missed because we cancelled ourselves is indistinguishable in
the corpus from one the market never offered. Queuing costs at most one
delayed run; cancelling costs an unrecoverable closing line. GitHub keeps
at most one pending run per group; at ~45 s runtime against a 10-minute
interval there is ~13× headroom, so a backlog cannot form in practice.

Secondary defense: `git_durable_store.commit_and_push_with_retry` fetches,
resets to the fresh remote tip, and re-runs the apply function —
recomputing dedup against just-fetched content — for the cases a
concurrency group does not cover (a manual dispatch racing a scheduled
run, a retried job attempt).

## 6. Persistence

- **Branch**: `research-data` (orphan; never `main`).
- **Path**: `data/research/observations/{season}.jsonl`,
  `data/research/capture_state/{season}.jsonl`,
  `data/research/settlements/{season}.jsonl`.
- **Append-only**: existing lines are never rewritten, reordered, or
  re-serialized. One appending, fsync'd batch per file per scan attempt.
- **Canonical key**: `observation_key = f(season, game_id, market_ticker,
  timing_label, model_version, capture_window_version)`. Including
  `timing_label` is what makes each checkpoint its own immutable row;
  including `model_version` means a mid-season model change produces a
  new row rather than overwriting history.
- **Duplicate protection**: exact set membership, no probabilistic
  structure. Rejected duplicates are counted, not silently dropped.
- **Commit strategy**: one commit per run that wrote rows, message
  `research capture: season=… run=… at=…`. Staging is scoped to
  `data/research` with `-f` (the path is gitignored on code branches on
  purpose) — never a bare `git add -A`, which historically committed a
  stray copy of main's source tree and zero real observations.
- Scheduled collection writes **only** research artifacts; it never
  modifies application code.

## 7. Model provenance at every checkpoint

Each row carries: `model_version` (with `ratings_component_version` and
`pricing_engine_version`), `training_cutoff`, `feature_version`,
`mapping_version`, `fee_schedule_version` + `fee_status` +
`fee_verification_status`, `snapshot_schema_version`, `game_id`,
`kalshi_market_ticker`, `kalshi_event_ticker`, `family`, `threshold`,
`side`/`team`, `model_probability`, `executable_yes_price`,
`executable_no_price`, **`market_status`**, `snapshot_timing.label`,
`captured_at`, `kickoff_utc_at_capture`, `schedule_source_timestamp`,
`provenance`, and `run_id`.

A model change mid-season therefore leaves every historical snapshot still
identifying exactly which model generated it.

## 8. Market and model movement

`research/movement.py` assembles per-contract checkpoint sequences.
Market price and model probability are kept as **separate series** and
never pre-combined: blending them makes a market that drifted toward a
static model indistinguishable from a model that drifted toward a static
market — opposite research conclusions from the same numbers.

No CLV, edge, ROI, or qualification judgement is computed. That is a later
milestone.

## 9. Health checks

Emitted per run as a compact JSON report plus a one-line `PERF {…}`
telemetry record (never per-ticker).

**HIGH (fails the run)**: zero markets scanned; zero games scanned;
supported-market collapse below 15% of baseline; mapping-failure rate
≥40%; persistence write-count mismatch; persistence failures; API
failures; **closing capture shortfall** (a CLOSING that was due and did
not land — escalated on its own because it is unrecoverable after
kickoff).

**WARNING**: supported-market drop below 50% of baseline;
mapping-failure rate ≥15%; stale-schedule rejections; **recorded missing
closings** (each with an explicit reason).

**Expected vs unexpected zeros.** Zero *captures* is normal and passes —
most runs at a 10-minute cadence have nothing due. Zero *markets* or zero
*games* is a HIGH failure, because those can only mean a broken data
source.

## 10. Stale-data policy

`scan_logic.guard_capture_allowed` rejects a capture when the game is not
`scheduled` (never create a new pregame snapshot for a started game) or
the schedule source is older than `MAX_SCHEDULE_STALENESS_HOURS` (6 h —
generous against a 10-minute cadence, still catching a stuck fetch).
Rejections are counted as `stale_schedule_failures` and surfaced as a
WARNING, never silently swallowed. Every row records
`schedule_source_timestamp` and `captured_at` so staleness is auditable
after the fact.

## 11. Reschedules, postponements, cancellations

Kickoff changes are handled by re-resolving labels against the **current**
authoritative kickoff:

- Already-captured rows are **immutable** — their `observation_key` is a
  function of `(…, timing_label, model_version, …)` and not of kickoff
  time, so a reschedule cannot rewrite or duplicate them.
- Not-yet-captured labels simply re-evaluate against the new kickoff; no
  separate re-labeling step exists or is needed.
- CLOSING follows the new kickoff automatically.
- `scan_logic.detect_reschedule` ignores sub-15-minute jitter as clock
  noise; `ScheduleChangeRecord` preserves change provenance alongside,
  never instead of, the original capture history.
- A postponed/cancelled game stops being `scheduled`, so the stale-schedule
  guard stops new pregame captures and closing resolves to
  `CLOSING_NOT_APPLICABLE`.

## 12. Manual dispatch

`workflow_dispatch` remains available with `schedule_season` and `no_push`
inputs. Manual runs use the **same** due-label logic, the same duplicate
protection, and the same persistence path — `--trigger-type` is recorded
in telemetry for provenance **only** and is never read by scheduling.
`test_manual_and_scheduled_runs_produce_identical_artifacts` pins this.

Manual runs additionally execute the read-only schedule validator
(`scripts/validate_collection_schedule.py`), which reports due/not-yet-due
labels for the real slate and captures nothing.

## 13. Live validation evidence

See the mission report and the workflow run linked there.

## 14. Deferred

Settlement logic, CLV calculation, ROI analytics, and recommendation
thresholds are all explicitly **not** started in this milestone.
