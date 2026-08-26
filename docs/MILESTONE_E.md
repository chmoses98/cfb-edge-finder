# Milestone E — Preseason Production/Research Readiness

Builds the durable, autonomous, season-long research-capture machine the
mission requires before the first meaningful 2026 game: persistence that
survives runner destruction, a scheduler that never fabricates or
duplicates a checkpoint, closing/settlement definitions rigorous enough to
support real research conclusions, and reporting/monitoring good enough to
notice when the pipeline breaks. **No recommendation, qualification,
staking, or order-execution logic is enabled anywhere** — see "Recommendation
architecture, hard-disabled" below.

This document is the design record every new module's docstring points
back to. See the final report (delivered separately) for exact test/lint
counts and the launch-readiness checklist table.

## Durable persistence (Part A)

### Architecture chosen: JSONL on a dedicated `research-data` git branch

Three realistic options were weighed:

1. **JSONL + a dedicated git branch** (chosen). No new infrastructure,
   credentials, or service to operate; git's own object store gives free
   history/diffability/provenance; a branch separate from `main` keeps bot
   commits out of reviewed code history entirely.
2. **GitHub Actions artifact + merge step.** Artifacts expire (90-day
   default) and are not designed as a durable store across arbitrarily
   many runs — wrong tool for "forever" retention.
3. **External object storage (S3-compatible) or SQLite-on-a-volume.**
   `docs/STORAGE_STRATEGY.md`'s own V1 recommendation for genuinely large,
   raw, or high-churn data. Deferred exactly as that document said it
   would be once real capture volume existed to justify it — see the
   season-scale estimate below: even the deliberately worst-case volume
   estimate is comfortably within git's line-oriented-text comfort zone,
   so building bucket credentials/infrastructure now would be premature.

Canonical files live at `data/research/{observations,settlements,capture_state}/{season}.jsonl`
on the `research-data` branch (never `main`), plus `data/research/reports/{weekly,season}/`.
Each observation row is a `ResearchCorpusRow` (`schemas/corpus_row.py`) —
one `KalshiResearchObservation` (Milestone D) plus durable-storage identity
and version metadata.

### Append-only / immutable

`research/persistence.py::append_json_rows` only ever appends new lines at
EOF; there is no update/rewrite path anywhere in this milestone. A test
(`tests/test_research_persistence.py::test_rows_are_immutable_on_disk_no_line_ever_rewritten`)
proves a later append never changes bytes already on disk.

### Deterministic dedup (mission section 3)

`research/identity.py::observation_key` is `sha256(season|game_id|market_ticker|
timing_label|model_version|capture_window_version)`. Two calls with
identical inputs always produce the identical key — a retry, a concurrent
run, a manual rerun, and scheduler overlap all collapse to "the same
logical observation" without any coordination between callers. The
existing `KalshiResearchObservation.snapshot_id` (a random UUID, Milestone
D) is untouched and still identifies one capture SWEEP; `observation_key`
identifies one logical CHECKPOINT and is what dedup is keyed on — a random
ID is never the dedup mechanism (proven in
`test_research_persistence.py::test_a_random_uuid_snapshot_id_does_not_prevent_dedup`).

`capture_window_version` is baked into the key specifically so a future
redefinition of the timing-bucket windows (`research/timing.py`) can never
silently collide with, or be mistaken for, an observation captured under
the old semantics.

Settlement rows use a separate `settlement_key` (per-market, not
per-snapshot) and a **fact fingerprint** (identity + status + both
settlement outcomes) for dedup, since a settlement's state genuinely
changes over time (PENDING → SETTLED) and each state is a real historical
fact worth preserving, not a duplicate to collapse.

### Concurrency / retry safety

Two independent layers (`research/git_durable_store.py`):

1. **Primary**: every workflow's `concurrency: group: research-data-write`
   serializes writers at the GitHub Actions level.
2. **Secondary**: `commit_and_push_with_retry` — on a rejected push
   (non-fast-forward), hard-resets the local branch to the freshly
   fetched remote tip and re-runs the caller's write function there. Since
   dedup is always recomputed fresh against the just-fetched file content,
   and every write is a pure end-of-file append, this converges without
   ever invoking `git merge` on the data files — no line-level conflict is
   possible, only an extra retry.

`tests/test_research_git_sync.py` proves this against **real local git
repositories** (no network required): two independent clones race to
create the `research-data` branch, the second's push is genuinely
rejected, and the retry loop converges to the union of both writers' rows
with zero duplication and zero data loss
(`test_two_writers_racing_converge_to_union_with_no_data_loss`).

### Why not committed raw Kalshi payloads

Every persisted row is the already-normalized `KalshiResearchObservation`
(ticker, family, threshold, model probability, executable price, fee
figures, provenance) — never the raw Kalshi JSON response. Game/team
identity is referenced by canonical `game_id`, never re-embedded per row
beyond what's needed for self-description (mission section 29).

### Season-scale estimate (mission section 28, measured not assumed)

Deliberately worst-case assumptions: 80 games/week × 150 contracts/game
(moneyline + full spread/total ladders) × all 9 pregame timing buckets ×
14 weeks = **1,512,000 rows**. A measured row (`tests/test_research_scale_benchmark.py`)
is ~2.2KB as compact JSON (full provenance/version metadata embedded, no
raw payload) → **~3.3GB at this ceiling**. The realistic figure is
5–10x smaller: most alt-line rungs stop moving (and in practice a
real capture cadence favors moneyline/central-line buckets), not every
contract survives to every late bucket, and git's packfile compression on
repetitive JSON text shrinks the on-disk repo size further still —
expect roughly 300–700MB/season in practice. Either figure is comfortably
within what git handles as line-oriented text; `test_research_scale_benchmark.py`
also proves append/dedup throughput stays sub-linear-ish (not quadratic)
as the file grows.

## Canonical snapshot identity (mission section 3) — see above.

## Automated capture scheduling (Part B)

**One scanner** (`scripts/research_scan_and_capture.py`), not one workflow
per game — it scans the whole not-started CFB slate every run and decides
which timing buckets are due per already-discovered market
(`research/timing.py::resolve_due_labels`).

### Cadence

Hourly (`0 * * * *`), `.github/workflows/research-capture.yml`. Rationale:
GitHub Actions' documented `schedule` floor is 5 minutes, but cron timing
is best-effort and known to slip during platform load; hourly sits
comfortably above that floor, matches every numeric bucket's half-width
(≥ 15 minutes — see below), and avoids GitHub's per-repo concurrent-run
fairness limits. A tighter cadence near kickoff (e.g. every 15 minutes for
T_90/T_60/T_30) is a documented future refinement requiring a second,
kickoff-aware trigger mechanism — explicitly out of scope for "one
scanner" (mission section 4).

### Timing buckets (mission section 5)

| Label | Target before kickoff | Window (±half-width) |
|---|---|---|
| `EARLY_OPEN` | n/a | due once, first time observed, until game starts |
| `T_7D` | 168h | 144h–192h |
| `T_3D` | 72h | 60h–84h |
| `T_24H` | 24h | 18h–30h |
| `T_6H` | 6h | 4h–8h |
| `T_90` | 90min | 60–120min |
| `T_60` | 60min | 45–75min |
| `T_30` | 30min | 15–45min |
| `CLOSING` | n/a | see "Closing" below — not a fixed offset |

`T_90`/`T_60`/`T_30` windows deliberately overlap (e.g. 60–75min is both
`T_90` and `T_60` territory); if neither has been captured yet (e.g. after
an outage), a single scan captures **both**, each under its own
deterministic key — never suppressed, never merged into one row.

### Exact-once capture (mission section 6)

Enforced entirely by `observation_key` + `research/persistence.py`'s
append-with-dedup: retries, repeated hourly runs, and manual reruns of the
identical checkpoint all produce the identical key and are silently
deduped (no error, no duplicate row) — proven in
`test_research_persistence.py` and `test_research_failure_injection.py`.

### Model-version re-runs (mission section 6)

A changed `model_version` produces a genuinely **new, distinct research
observation** (it's part of the key) — never an in-place correction of the
original. `ResearchCorpusRow.capture_mode` is `PROSPECTIVE` for every row
the live scheduler writes; a hypothetical future backfill tool would stamp
`RETROSPECTIVE_BACKFILL` explicitly, so a report can never conflate
backfilled research with prospective capture (mission section 26). No
backfill tool exists in this milestone — the field exists so one never has
to retrofit this distinction later.

### Missed-window recovery (mission section 7)

`research/timing.py::classify_bucket_state` / `resolve_all_bucket_states`
give every checkpoint an explicit state every scan:
`CAPTURED` / `NOT_YET_DUE` / `MISSED_WINDOW` (window closed, never
captured — never fabricated later) / `GAME_RESCHEDULED` / `WORKFLOW_FAILURE`
/ `MARKET_NOT_AVAILABLE` / `OTHER_EXPLICIT_REASON`
(`schemas/capture_state.py::CaptureState`). These are recorded as an
append-only log (`research/persistence.py::append_capture_state_rows`),
deduped on state (a genuine transition always appends, re-observing the
same state is a no-op) — so "why is T_60 missing" is always answerable
from the corpus itself, never silent.

## Kickoff changes / postponements (Part C)

`research/scan_logic.py::detect_reschedule` flags a genuine kickoff shift
(> 15 minutes, to absorb source clock jitter without false positives).
Already-captured buckets keep their identity (`observation_key` does not
depend on kickoff time at all — only on season/game/ticker/label/model
version), so a reschedule never duplicates or invalidates history;
`resolve_due_labels` is simply re-run against the new kickoff for
whatever hasn't been captured yet. Postponement/cancellation are handled
at settlement time (`GameFinalStatus.POSTPONED`/`CANCELED` →
`MarketSettlementStatus.VOID_POSTPONED`/`VOID_CANCELED`, never inferred as
a loss/win).

### Stale-schedule guard (mission section 9)

`research/scan_logic.py::guard_capture_allowed` — refuses a new pregame
snapshot unless `game_status == "scheduled"` AND the schedule source
timestamp is within 6 hours (`MAX_SCHEDULE_STALENESS_HOURS`, generous
relative to the hourly cadence — survives one missed run). A violation
raises `StaleScheduleGuardError`, counted as a `stale_schedule_failures`
health metric, never silently skipped.

## Closing (Part D)

**Definition**: the last clean, executable, PREGAME quote before the
market stops being tradeable / the game starts. Never a post-kickoff
price, never a stale midpoint, never "whatever the last hourly scan
happened to see" (`research/closing.py`).

**Quality thresholds** (documented, not tuned against real data yet — no
settled season exists):

- `EXACT`: captured within **10 minutes** of kickoff.
- `NEAR_CLOSE`: captured within **60 minutes** (the fallback window,
  mission section 11) — always labeled approximate, always paired with
  its own minutes-to-kickoff gap, never presented as exact.
- `MISSED`: nothing eligible within 60 minutes — no fabricated closing
  value.

## Postgame settlement (Part E)

Source: live CFBD `/games` (`research/settlement.py::extract_game_result`,
defensive candidate-key reads mirroring `ingestion/game_normalization.py`'s
own pattern for the same two-schema-source-disagreement risk documented
there). Settlement reuses the SAME verified operators Milestone D already
parses (`contract_semantics.py`'s confirmed strict `>`) — never a generic
sportsbook rule; an unexpected operator is `UNSETTLEABLE_UNKNOWN_OPERATOR`,
never guessed.

- **Winner**: `actual_winner` = higher-scoring side; contract settles YES
  iff its named team equals `actual_winner`.
- **Spread**: team-relative margin (home margin, or its negation for the
  away side) compared strictly greater-than the threshold.
- **Total**: `home_points + away_points` compared strictly greater-than
  the threshold.
- **Overtime**: no special case at all — settlement always reads the
  FINAL score, exactly like Kalshi's own winner-market rules text already
  says ("resolves based on the official final result" regardless of
  periods). Proven directly in
  `test_research_settlement.py::test_overtime_settles_on_final_score_only_no_special_case`.
- **Postponement/cancellation/no-contest**: `VOID_POSTPONED` /
  `VOID_CANCELED` — `derived_contract_settlement` stays `None`, never
  guessed.
- **Delayed settlement**: settlement is itself an append-only fact log
  (`PENDING_NOT_FINAL` → `SETTLED` is two rows, not a mutation) — both are
  preserved.
- **Official Kalshi settlement**: `flag_mismatch` preserves BOTH the
  derived outcome and any later-observed official one, flagging
  disagreement only when both are present and differ (mission section
  13). No code in this milestone calls a Kalshi settlement endpoint —
  read-only market access only (Part J).

## CLV / market movement (Part F)

Neutral terminology throughout (`research/clv.py`) — never "edge",
mirroring `kalshi/research_ledger.py`'s existing discipline:

- `closing_price_delta = closing_price − entry_snapshot_price`
- `market_move_toward_model = |entry − model_probability| − |close − model_probability|`
  (positive = the market moved toward the model's entry-time view)
- `fee_adjusted_clv = raw_price_movement − (entry_fee + closing_fee)`,
  `None` whenever either fee is unknown (never treated as zero)
- `ModelMarketGapRecord` (mission section 15): preserves
  `model_probability`, `executable_market_probability_at_capture`,
  `gross_gap`, `fee_adjusted_gap`, `closing_market_probability`, and
  settlement-derived `actual_result_hit` per settled observation — the
  core prospective evaluation corpus.

### Gap buckets (mission section 17)

`<2% / 2–5% / 5–8% / 8–12% / 12%+`, magnitude-only
(`research/gap_buckets.py`). Reporting deliberately measures sample count,
settlement hit rate, and average movement PER bucket — nothing ranks
buckets by "bigger is better."

### Correlation awareness (mission section 18)

`research/correlation.py` gives three explicit denominators —
contract-level, game-level (per game × family, so a game's spread ladder
and total ladder are separate clusters), and family-level — plus a
deliberately conservative `effective_sample_size` set equal to the
game-level cluster count (not a fitted intraclass-correlation coefficient,
since no real settled season exists yet to fit one against). Reports
surface all three counts side by side rather than one blended number.

## Health / failure monitoring (Part H)

Every scan run builds one `research/health.py::CaptureHealthReport`:
games/markets scanned, supported markets, captures due/written/skipped,
missed windows, mapping failures, stale-schedule failures, API failures,
persistence failures.

`evaluate_collapse` applies **thresholds, not blanket "any change is an
error"** (mission section 20):

- Zero markets/games scanned → HIGH.
- `supported_markets` < 50% of a trailing baseline → WARNING; < 15% →
  HIGH (a genuinely lighter week — bye weeks — should not trip this if the
  per-market support RATIO holds; only a real pipeline break should).
- Mapping-failure rate ≥ 15% → WARNING, ≥ 40% → HIGH.
- Persistence write count ≠ (captures due − already-present) → HIGH
  (data-integrity failure by definition, not a judgment call).
- Persistence failures → HIGH. Stale-schedule failures → WARNING only.

`should_fail_run` returns non-zero exit on any HIGH diagnostic — the
capture script (and settlement/report scripts by the same pattern) fails
the GitHub Actions run loudly rather than reporting quiet degradation.

## Reports (Part I)

`research/reporting.py::build_weekly_report` /
`build_season_report` → `schemas/report.py`. Weekly: games/contracts
captured, timing-bucket coverage (captured/missed/not-yet-due per label),
family coverage, mapping errors, gap-bucket distribution (with
contract/game-level counts), closing capture quality counts, settled
observations. Season: cumulative totals, family counts, timing-bucket
completeness, gap-bucket distribution, and `model_version_history` — every
model version that has EVER captured a prospective observation this
season, so reports can segment by version (mission section 26).
`report_version` increments on every season-report run; prior versions are
never overwritten (`scripts/research_season_report.py` writes
`{season}-v{N}.json` plus a `{season}-latest.json` pointer).
`tests/test_research_reporting.py::test_weekly_report_never_contains_a_bet_recommendation_field`
mechanically checks no report field name reads as a recommendation.

## Recommendation architecture, hard-disabled (Part J)

`schemas/qualification.py::QualificationStatus` is a **closed, two-member**
enum — `RESEARCH_ONLY` / `QUALIFICATION_DISABLED` — with no `BET`/`PLAY`/
`ACTIONABLE`/tier member reachable from the type at all.
`QualificationRecord`'s own validator rejects any free-text field
containing `bet`/`play`/`stake`/`tier_a`/`tier_b`/`tier_c`/`order`/`execute`
substrings at construction time. `research/qualification.py::default_disabled_record`
is the only constructor application code uses, and it is never called by
any capture/settlement/report path in this milestone — it exists purely so
a later milestone can wire in real logic without a breaking schema change.
`tests/test_qualification_hard_disabled.py` extends the existing
`test_no_recommendation_surface.py` substring scan to the new `research`
and `schemas` packages, and proves the enum/validator behavior directly.

### No execution surface (mission section 25)

No Kalshi trading credentials are read anywhere in this milestone's code
(`config.py` unchanged — no new secret fields). No order-placement client
exists (`data/kalshi_client.py` is read-only, unchanged by this
milestone — `GET /markets`/`GET /events` style calls only). Every workflow
declares `permissions: contents: write` and nothing else; the settlement
and report workflows don't even need that beyond the durable-store push.

## Model versioning / change management (Part K)

`schemas/data_versions.py::DataVersionManifest` — every corpus row embeds
`model_version`, `feature_version`, CFBD/Kalshi capture timestamps,
`mapping_version`, `fee_schedule_version`, `settlement_version`, and
`snapshot_schema_version` (`research_corpus_v1`). No unlabeled schema
drift: a future schema change is a new `snapshot_schema_version`, read
defensively by anything folding historical rows (dedup keys are computed
from raw JSON dicts, not re-validated typed models — see
`research/persistence.py`'s module docstring).

## Full rehearsal (Part M)

`tests/test_research_rehearsal.py::test_full_lifecycle_rehearsal` runs
the entire 12-step lifecycle in-process against REAL library code (the
same functions the live scripts call) — schedule discovery → market
discovery → mapping → model projection → timing bucket selection →
snapshot persistence → duplicate retry → missed-window handling → closing
capture → settlement → CLV calculation → weekly report — using synthetic
history data through the real `GameProjectionCache`/`price_one_market`
pipeline, never a hand-edited database. **PASS.**

`tests/test_research_failure_injection.py` proves recovery for all ten
scenarios the mission lists: missed T_60, duplicated scheduler run,
kickoff moved several hours, Kalshi partial failure, CFBD temporary
failure, persistence retry (via real local-git race, not a mock), an
already-started game, a market that disappears before closing, a
rescheduled game, and settlement delay. **All PASS** — no failures were
discovered that required a design change; the ten scenarios validated the
design as built.

Live-network items (an actual GH Actions run against real CFBD/Kalshi
endpoints, pushing to a real `research-data` branch) remain a genuine
follow-up — this sandboxed dev environment has no network egress to
either API, the same documented constraint every prior milestone (B/C/D)
in this repo notes and resolved via a `workflow_dispatch` run once merged.
See "Remaining blockers" in the final report.
