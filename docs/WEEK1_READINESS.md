# Week 1 Readiness

State of the research machine going into Week 1, what was found auditing
it end to end, and how to operate it.

Audit performed 2026-08-27, against main `5363c70`.

## Verdict

**WEEK 1 RESEARCH READY WITH PENDING LIVE PROOFS**, with one HIGH item
that is environmental rather than a code defect (GitHub's scheduler; see
below).

The pipeline is correct and the safety boundary holds: 0 actionable
candidates, qualification disabled, no validated threshold artifact,
evidence auto-validation unreachable, no sizing or execution surface.

## The system, end to end

```
CFBD schedule ──► canonical game id ──► Kalshi discovery (4,578 active markets)
                                              │
                                    market semantic parsing
                                              │
                              GameProjectionCache (1 projection per game)
                                              │
                                   many-contract pricing + fees
                                              │
                          prospective observation + snapshot label
                                              │
                        append-only JSONL on the research-data branch
                                              │
                    settlement ──► outcome attribution ──► analytics
                                              │
                          expression grouping ──► candidates
                                              │
                     data-quality gate ──► QUALIFICATION LOCK ──► card (always empty)
```

Every stage fails closed. A missing input yields an explicit state
(`UNSETTLEABLE_*`, `CLOSING_MISSING_*`, `NOT_APPLICABLE_*`,
`LEGACY_SCHEMA_*`) rather than a default that flows onward.

## Workflow cadences

| Workflow | Cron | Concurrency | Observed runtime |
|---|---|---|---|
| Research Capture | `*/10 * * * *` | `research-data-write` | 8s idle, 55s full scan |
| Research Settlement | `0 */6 * * *` | `research-data-write` | ~20s |

`cancel-in-progress: false`, so writers queue rather than clobber. All
three writers share one group; a data-writing workflow cannot modify
application code (it commits only `DURABLE_STORE_PATHS`).

## Schema versions

| Version | Adds | Rows |
|---|---|---|
| `research_corpus_v1` | original prospective schema | 1,724 |
| `research_corpus_v2` | `market_status` | 0 (nothing due yet) |

**Policy** (`schemas/schema_evolution.py`): adding a field downstream
code will REQUIRE bumps the version and registers the field in
`FIELD_INTRODUCED_IN`. Downstream asks `field_expected_in(...)` rather
than testing `is None`, so a missing value classifies as `PRESENT`,
`LEGACY_SCHEMA_FIELD_ABSENT`, or `CURRENT_SCHEMA_DEFECT`. An unknown
version ranks oldest, so a typo can never credit a row with fields it may
not carry.

### Legacy rows are never rewritten

All 1,724 existing rows have `market_status: None` because they were
captured before 2026-08-27T17:44:56Z, when the field was added. They stay
that way. Backfilling `"active"` on the reasoning that they were priced
would fabricate the exact field closing capture relies on to refuse
fabricated quotes. In analytics they remain permanently non-executable,
reported as `LEGACY_SCHEMA_MARKET_STATUS_UNAVAILABLE` — as disqualifying
as a broken quote, but distinguishable from one, so a live collector
regression cannot hide behind them.

## Provenance carried on every current-schema row

model version, training cutoff, calibration version, margin-correction
version, feature version, mapping version, parser/semantics version, fee
schedule version + status, corpus schema version, `captured_at`,
kickoff-at-capture, snapshot label, capture mode, capture window version,
run id.

## Current health (2026-08-27T21:28Z, live)

| | |
|---|---|
| Kalshi markets discovered | 4,578 active (+2 closed) |
| Live status distribution | `{'active': 4578, 'closed': 2}` |
| Eligibility allow-list | `['active']` — **matches** |
| Games scanned | 3,550 |
| Supported (FBS-v-FBS) markets | 930 |
| Games projected | 102 (one projection each) |
| Corpus rows | 1,724 |
| Duplicates / malformed | 0 / 0 |
| API failures | 0 |
| Candidate expressions | 2,316 |
| **Actionable** | **0** |

## Pending live proofs

None of these is a defect. Each needs the calendar to advance, and none
may be closed by fabricating data.

- **First current-schema observation.** The next legitimately-due
  supported capture is T_24H at 2026-08-28T10:00Z. Nothing is due before
  then, so a manual run correctly writes nothing.
- **First genuine CLOSING capture.** Needs a supported game inside the
  14-minute pre-kickoff window.
- **First genuine settlement.** Needs a captured game to finish. Earliest
  supported kickoff is 2026-08-29T16:00Z.
- **Analytics.** Settled-supported n = 0 until the above happens. No code
  change is required for the first settled game to appear.

## Findings

### HIGH — GitHub's scheduler is not meeting the configured cadence

Not a code defect, but the largest live risk to Week 1.

Scheduled capture runs, against an hourly cron, arrived at gaps of 64,
71, 52, 61, 54, 56, 66, 80, 49, 95, 144, 171, 296 and 653 minutes. After
the `*/10` cadence merged at 18:08Z, zero scheduled runs fired in the
following three hours. The settlement workflow (`0 */6`) likewise skipped
its 00:00, 12:00 and 18:00 slots on 2026-08-27.

The cadence design assumes roughly 4 minutes of drift tolerance for
CLOSING's 14-minute window. Observed drift is hours. **CLOSING and T_30
capture cannot be relied on from GitHub's scheduler alone**, and a
closing line, unlike every other checkpoint, is unrecoverable after
kickoff.

Mitigation available now: `workflow_dispatch` fires immediately and
reliably. For a Saturday slate, dispatch manually near the final-approach
windows, or drive the workflow from an external scheduler via
`repository_dispatch`. `scripts/week1_readiness.py` now reports collection
staleness so an outage is visible rather than silent.

### MEDIUM — genuine mapping-unresolved rate is 31%

1,401 of 4,578 markets resolve to `TICKER_UNRESOLVED` (this figure already
excludes FCS-vs-FCS, which is a correctly classified population, not a
failure). It does not block Week 1 — all 102 supported games mapped and
priced — but the population deserves classification into ambiguous
naming, new FBS aliases, and parser gaps before deeper weeks.
`scripts/audit_ambiguous_team_mappings.py` exists for this and needs a
network-enabled run.

### MEDIUM — fee schedule cannot be re-verified from this environment

`kalshi_fee_schedule_2026_07_07_taker`, `VERIFIED_CURRENT`, effective
2026-07-07, sourced from Kalshi's published fee schedule. It is the most
recent known schedule, but network policy blocks kalshi.com from the
audit environment, so it could not be re-confirmed against the live
source today. The status was **not** upgraded or restated on inference.
Re-verify from an environment with egress before real money is ever
contemplated.

### LOW — a zero final margin settles as AWAY

`actual_winner = HOME if home_margin > 0 else AWAY`. College football
overtime makes a tied final unreachable in practice, so this is
theoretical, but a 0-margin final would settle silently rather than
refuse.

## Fixed during this audit

1. **Schema version not bumped when `market_status` was added.** Legacy
   rows and future current-schema rows were indistinguishable, so a
   collector regression could not be told from expected absence. Bumped
   to `research_corpus_v2` with a field-introduction registry.
2. **Collector restated the schema version as a literal**, so bumping the
   constant would have left written rows stamped with the old value. It
   now imports the constant.
3. **`fee_schedule_version` was `None` in the data-versions manifest** on
   all 1,724 rows, even where the observation named a verified schedule —
   a provenance hole. Now carries the real label.
4. **A failed `git fetch` was read as "branch does not exist"**, so a
   transient network or auth failure silently continued against an empty
   corpus: every captured label looked due again, and a live dry run
   reported 1,337 captures due where the real answer was 0. It now asks
   the remote with `ls-remote` and fails loudly rather than orphaning.
   Blast radius was verified against a real remote first: the non-forced
   push is rejected and existing rows survive, which is why this was
   MEDIUM and not data loss.
5. **`--no-push` skipped the corpus fetch entirely**, so a rehearsal
   scanned an empty ledger and disagreed with the real run by 1,337
   captures. A rehearsal now rehearses against the real corpus.
6. **Nothing detected a stopped collector.** Added
   `scripts/week1_readiness.py`, which measures staleness from the corpus
   and the cron rather than from any run's own output.

## Week 1 operating procedure

1. **Before the slate.** Run the readiness check with live probes:
   ```
   python scripts/week1_readiness.py --data-repo-dir <corpus> --season 2026 --live
   ```
   It is also wired into the capture workflow on manual dispatch. Confirm
   the live status distribution still matches the allow-list, and that
   staleness is not flagged.
2. **During the slate.** Because of the scheduler finding, do not assume
   `*/10` is firing. Check for recent scheduled runs; dispatch manually
   ahead of T_90/T_60/T_30/CLOSING windows for games that matter.
3. **After each game.** Settlement runs `0 */6`. The first genuine
   `SETTLED_YES`/`SETTLED_NO` is the signal that the settlement path is
   live-proven; the readiness command reports terminal settlement counts
   and any Kalshi cross-check mismatch.
4. **Weekly.** Analytics ingest settled games automatically. Watch
   settled-supported n, CLV n, and calibration n separately — CLV
   requires a CLOSING row linked to a settled game, so it will lag.
5. **Always.** Actionable count must remain 0. A non-zero value is a
   safety defect, not a result: stop and investigate rather than acting
   on it.

## What is still absent by design

Stake sizing and execution do not exist — no module, function, or
parameter. Qualification is disabled unconditionally, no validated
threshold artifact can be produced, and evidence readiness cannot reach
`VALIDATED`. See `docs/RECOMMENDATION_SKELETON.md`.
