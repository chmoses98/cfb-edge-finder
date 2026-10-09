# Football Signal Discovery Lab — Wave 2A (CFB): retrospective replay protocol

Status: **PRE-REGISTERED** (committed before any 2026 outcome of either stream was joined).

Wave 2A asks one question: *if the two frozen Wave-2 CFB rules had existed earlier, what would they have done?* It is
**RETROSPECTIVE RESEARCH**. It does not replace, feed or alter the prospective Wave-2 experiment
(`FOOTBALL_SIGNAL_DISCOVERY_WAVE2_PROTOCOL.md`, candidates SHA-256 `bf19c972…1ce5`). No threshold, feature,
checkpoint, market rule, population rule, read or verdict rule of Wave 2 changes. Replay rows are never written to
the Wave-2 store (`research-signals:wave2/…`) and never counted in a prospective n.

## 1. Frozen rules (used, not restated)

The replay imports `cfb_edge_finder.signal_discovery.wave2` and calls its functions unchanged:
`load_candidates` (hash check), `observation_001` (z, |z| ≥ 1, side), `observation_002` (STRONG CONTROL side),
`checkpoint` (PRIMARY_60_180, LAST in-window capture yielding a center), `ladder_points` / `implied_margin`
(Wave-1 center), `natural_rung` / `contract_for` (own rung → YES at yes_ask, opponent rung → NO at no_ask),
`spread_fee` (July-2026 taker schedule), `entry_row` (PROS-002 `< +3.0` strictly), `settlement_row`,
`closing_context`, `stream_summary`. The PROS-001 feature is `wave2_cycle.feature_for` (production builder at
the 04:00 ET cutoff; `LeakageError` if any history row is not strictly earlier).

## 2. Evidence labels

| Label | Rows |
|---|---|
| `RETROSPECTIVE_DISCOVERY_CORPUS` | 2014–2025 Wave-1 feature tables joined to the CFBD median sportsbook spread. Characterisation only (Wave 1 inspected it). PROS-002 history is additionally labelled `SPORTSBOOK_CLOSE_CHARACTERIZATION`. |
| `RETROSPECTIVE_2026_REPLAY` | Completed 2026 games with kickoff before the Wave-2 activation (2026-10-10T00:00Z), priced on genuinely captured Kalshi spread ladders. Sub-label `PRE_FREEZE` (kickoff < 2026-10-08T22:26:33Z, the Wave-2 freeze commit) or `POST_FREEZE_PRE_ACTIVATION`. |
| `PROSPECTIVE_WAVE2` | Untouched. Shown beside the replay only as its own column; never pooled. |

## 3. 2026 replay inputs (pinned)

| Input | Pin |
|---|---|
| Football log + schedule | `main` @ `8173fd5e` (`data/football/2026/team_games.jsonl` sha256 `e37fe5ca…d4b`, `schedule.json` `6b73c7a1…f606`) |
| Spread ladders, source A | `research-data` @ `292f3aac`, `data/research/observations/2026/*.jsonl`, family `spread` rows that carry a threshold. One attempt = one (run, event) capture; every row of an attempt shares one `captured_at`. |
| Spread ladders, source B | every `kalshi cfb catalog` commit on `main` up to `8173fd5e` with `capture_complete`; one attempt = one game file at one snapshot, stamped with the status file's `captured_at`. |
| Orientation | the frozen winner-market matcher (`orient_markets` + conductor `market_keys`) over the CONTROL study's `quotes_2026.jsonl.gz` plus catalog winner quotes after its last snapshot |
| CONTROL | a production V2 `FINAL_PREGAME` row (script-ledger @ `8be862ef`) when one exists, copied verbatim; otherwise `control_market.football.replay_game` (production `derive_claims` at the production cutoff, `RETROSPECTIVE_REPLAY_AT_PRODUCTION_CUTOFF`), checked against the frozen CONTROL-study rows |

**Source A bids.** Research rows carry both executable asks but no bids. The frozen center uses the YES mid
`(yes_bid + yes_ask)/2`. On Kalshi's single book `yes_bid ≡ 1 − no_ask` and `no_bid ≡ 1 − yes_ask`. That identity is
verified on every catalog market used before it is applied, and every source-A quote carries
`bid_provenance = BOOK_IDENTITY_FROM_OPPOSITE_ASK`. **Entry prices are never derived:** YES is bought at the
captured YES ask and NO at the captured NO ask. A source-A attempt in which any spread rung lacks a threshold is
unusable (`PARTIAL_LADDER_UNPARSED`), never a partial ladder.

**Source B timing.** A snapshot is inside PRIMARY_60_180 only if `[captured_at − elapsed, captured_at + elapsed]`
lies wholly inside [kickoff − 180, kickoff − 60] (elapsed = the build's `elapsed_seconds`). Nothing at or after
kickoff is read.

## 4. Stages (outcome-blind first)

1. **extract** — quotes and orientation evidence only (no score, no settlement).
2. **membership** — features, CONTROL, checkpoint, natural rung, fee, eligibility. Scores are stripped from every
   input before this stage; a test alters every outcome and shows the membership output unchanged.
3. **reveal** — `outcome_for` (ESPN final score) and `settlement_row`.
4. **report** — summaries; replay and prospective side by side, never pooled.

## 5. Replay statuses

Frozen Wave-2 statuses are kept on every row, plus a replay exclusion code from: `BEFORE_CAPTURE_HISTORY`,
`NO_MARKET`, `NO_PRIMARY_WINDOW_CAPTURE`, `NO_EXECUTABLE_QUOTE`, `ORIENTATION_UNRESOLVED`, `IDENTITY_FAILURE`,
`FEATURE_HISTORY_UNAVAILABLE`, `FEE_UNAVAILABLE`, `SETTLEMENT_UNAVAILABLE`, `RULE_NOT_RECONSTRUCTABLE`. Rows below
the frozen threshold are `NOT_ELIGIBLE`, not exclusions. The full denominator (candidates → feature → market →
window → oriented → executable → settled) is reported.

## 6. Metrics and uncertainty

* The frozen `stream_summary`: mean / median / quantile residual with a game-level bootstrap (4,000 draws,
  seed 20261010), positive-residual rate and ATS-like rate with Wilson intervals, natural-rung fee-adjusted ROI with
  a bootstrap interval, outright wins.
* CLV: side-aware, from `closing_context` (last capture 0–60 min pre-kick); a missing close stays missing.
* Robustness (descriptive): one-week, one-team, one-conference removal; top-5-team removal.
* History: the same statistics against the CFBD median spread (untimestamped, assumed −110).

## 7. Interpretation rule (fixed now)

For the 2026 replay only, on settled rows:

| Class | Rule |
|---|---|
| `INSUFFICIENT_REPLAY_DATA` | settled n < 15 |
| `STRONGLY_SUPPORTIVE` | mean residual > 0, its 95 % bootstrap interval excludes 0, and ATS-like ≥ 52.4 % |
| `SUPPORTIVE` | mean residual > 0 and ATS-like ≥ 50 % |
| `UNSUPPORTIVE` | mean residual ≤ 0 and ATS-like < 50 % |
| `MIXED` | anything else |

The prospective status stays `PROSPECTIVE_TRACKING`. Nothing in Wave 2A can change a Wave-2 rule. Any new idea goes
to `FOOTBALL_SIGNAL_WAVE2A_FUTURE_HYPOTHESES.md` and is not tested.
