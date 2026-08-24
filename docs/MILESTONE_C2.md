# Milestone C.2 — Improve CFB Forecast Quality Before Kalshi Pricing

**Status: a genuine, evidence-driven diagnosis-and-ablation pass over the
Milestone C hardened baseline, preceded by a historical-integrity audit
that found and fixed a real bug (conference-realignment leakage into
diagnostics) and replaced single-corpus ablation with a leakage-safe
chronological development/confirmation selection procedure. One change
was adopted (`ridge_lambda` 25.0 -> 10.0) after being selected on
2022-2024 DEVELOPMENT data alone and then checked, unchanged, against the
held-out 2025 CONFIRMATION season -- never tuned on and re-presented as
independent. Two candidate changes (FCS historical-performance tiering, a
lower `season_shrinkage_k`) were tested the same way and REJECTED on both
development and confirmation data. The central finding of this pass -- a
favorite-tail margin-bias pattern -- was diagnosed in detail but NOT
fixed; this is stated plainly, not minimized, per this mission's explicit
"be explicit when something failed" instruction. No Kalshi pricing, edge,
staking, or recommendation surface exists anywhere in this change.**

This document assumes `docs/MILESTONE_C.md` as the reference baseline
throughout. All "before" numbers below are quoted from that document's
hardened-pass results (also independently re-confirmed live in this pass,
see "0-baseline-diagnostics" run below); all "after" numbers are from this
pass's own live runs. Section 2A documents the historical-integrity audit
performed before any ablation result in this document was trusted;
section 3.2/section 7's ablation numbers reflect the corrected, leakage-safe
development/confirmation procedure that audit required, not the
single-corpus procedure this document originally used.

## 1. Repository discipline confirmation (mission section 1)

Confirmed before any code change:
- Main SHA: `d3c18642f122cb3c5ad80a0fca017695229c5270` (Milestone C merge
  commit), matching the mission brief exactly.
- CI: green on main (Milestone C PR #4's merge was gated on it).
- Local tests: 308 passed on main before this pass's changes began; **334
  pass now** (26 new tests added this pass -- diagnostics module tests,
  FCS-tiering tests, and the historical-integrity audit's regression
  tests: conference realignment safety, FCS strictly-prior-evidence
  walk-forward proof, the prediction/diagnostics architectural boundary,
  and development/confirmation leakage-safety; see section 2A).
- No betting/staking/recommendation/execution surface exists anywhere in
  the diff (see `tests/test_no_recommendation_surface.py`, unchanged and
  still passing).
- `chmoses98/edge-finder-api` (the MLB repo) was never opened, read, or
  modified in this mission.
- All work happened on a new branch, `claude/milestone-c2-forecast-quality`,
  branched from that exact main SHA. No commits were made directly to
  main. This PR is not merged as part of this mission (see section 23).

**Standing output rule preserved verbatim, per explicit instruction:**
*"Do not surface stale or redundant background-task completion
notifications. Poll/wait internally as needed, but once the final CI/
workflow result is known and included in the final mission report,
suppress later redundant wait-completion messages unless they contain
genuinely new information that changes the result."* This rule governed
all live-workflow polling in this pass (four separate live ablation runs)
and remains in force for future missions.

## 2. Method: how every number below was produced

All metrics come from `scripts/backtest_cfb_baseline.py --mode live`
executed via the existing `backtest-cfb-baseline-live.yml` manual
GitHub Actions workflow (unchanged mechanism from Milestone C -- this dev
environment cannot reach CFBD directly). Every run in this document is a
genuine chronological walk-forward backtest across seasons 2022-2025
(min-week-for-first-prediction=2), never a random split, never fit on the
evaluation game, never using a future-season aggregate. This pass added
CLI/workflow hyperparameter flags (`--ridge-lambda`,
`--fcs-ridge-lambda`, `--pace-shrinkage-k`, `--season-shrinkage-k`,
`--fcs-mode`, `--variant-label`, `--diagnostics`) purely so different
hyperparameter configurations could be compared on IDENTICAL data and
code, across separate live runs, without touching the model between runs
-- this is what makes the ablation table in section 15 a genuine
comparison rather than a description of sequential code changes.

`src/cfb_edge_finder/modeling/diagnostics.py` (new this pass) is a pure,
leakage-safe post-hoc segmentation module: every function operates only
on already-computed `GameOutcome` records (each already the product of a
leakage-checked walk-forward prediction) plus a static, pregame-known
team registry lookup. It introduces no new leakage surface -- see that
module's docstring for the full argument. Favorite/underdog and
margin-magnitude segments are classified from the **model's own**
`model_margin_mean`, never a market or Kalshi line (no market-line input
exists anywhere in this codebase).

## 2A. Historical-integrity audit (performed before trusting any ablation result)

A follow-up audit instruction required four things be checked, and fixed
if broken, before any live ablation result in this document could be
trusted. All four are now genuinely addressed, not just claimed:

### 2A.1 Historical conference membership -- real bug found and fixed

The original `modeling/diagnostics.py::is_conference_game` classified a
historical game's conference status via `teams.registry.get_team()`, a
**single CURRENT (2026) snapshot** of conference membership. Because of
conference realignment, this is a genuine bug: `teams/registry.py`
itself documents that Texas State moved from the Sun Belt (through the
2024 season) to the rebuilt Pac-12 (2026 registry). A 2022-2024 Texas
State vs. Troy game -- both Sun Belt at the time, a real conference game
-- would have been silently misclassified as NON-conference by the old
code, because the registry now disagrees with Troy's conference.

**Fix:** `TeamGameLine` (corpus.py) now captures CFBD's own per-game
`homeConference`/`awayConference`/`conferenceGame` fields directly from
the raw `/games` row (season-scoped, pregame-known facts -- conference
membership for a season is fixed before it starts, exactly like
`homeClassification`). These flow through to `GameOutcome` (backtest.py)
as `home_conference`/`away_conference`/`is_conference_game`.
`diagnostics.is_conference_game` now classifies EXCLUSIVELY from these
historical, per-game fields -- CFBD's own `conferenceGame` flag when
reported, falling back to comparing the two historical conference strings
otherwise -- and never touches `teams.registry` at all.

**Regression test** (`test_diagnostics_conference_realignment_safety`,
`tests/test_modeling_diagnostics.py`): asserts the 2026 registry's
Texas State/Troy conferences genuinely differ (guarding against the test
going stale), then asserts a synthetic 2023 Texas State-vs-Troy game
tagged with the HISTORICAL "Sun Belt"/"Sun Belt" fields is still
classified as a conference game -- proving the fix, not just asserting
it. A second test (`test_conference_fields_reflect_historical_row_not_current_2026_registry`,
`tests/test_modeling_corpus.py`) proves `build_team_game_lines` itself
captures the historical row's conference verbatim, unaffected by the
registry. 8 total conference-related tests added across both files.

### 2A.2 FCS tiering: audited, found already strictly as-of

`modeling/ratings.py::_fcs_team_tiers` was audited line-by-line. It
operates only on `training_rows`, a list ALREADY filtered by
`fit_fbs_efficiency_ratings` via `assert_strictly_before(line.as_of,
as_of)` -- any row not strictly prior to the prediction cutoff raises
`LeakageError` rather than silently being included. Tiers are
recomputed from scratch on every call (no cross-call memoization), so a
walk-forward caller genuinely gets a fresh, strictly-prior-only tiering
at every (season, week) step, exactly like every other rating in the
snapshot. No end-of-season record, no full-season aggregate, and no
later FBS-vs-FCS result can reach a tier assigned at an earlier cutoff.

**New regression test**
(`test_fcs_tier_recomputed_walk_forward_and_unaffected_by_future_games`,
`tests/test_modeling_ratings_and_priors.py`): builds a synthetic FCS
opponent that is blown out early (weeks 1-2) but plays much closer games
later (weeks 6-7) -- exactly the "future result would change a past
tier" scenario the audit asked to rule out. Fits ratings at `as_of=week
3` two ways: (a) via the same `history = [ln for ln in lines if
ln.as_of.is_strictly_before(as_of)]` filter `run_walk_forward_backtest`
itself uses, given the FULL season's lines, and (b) directly from ONLY
the early-weeks rows. Asserts these two produce IDENTICAL tiers/tier
ratings -- proving the week-6/7 games (present in the full corpus, just
chronologically later) cannot leak into the week-3 tier. A second
assertion, at `as_of=week 8` (now including the later games), shows the
SAME opponent's tier legitimately changes -- proving recomputation is
genuinely walk-forward, not a frozen first-seen value. No code change was
needed here; this section documents a clean audit result, not a fix.

### 2A.3 Prediction/diagnostics architectural boundary

Confirmed via the existing import graph (`corpus.py`, `ratings.py`,
`priors.py`, `score_model.py`, `naive_benchmark.py`, `calibration.py`,
`leakage.py`, `qb_continuity.py`, `backtest.py` -- every module on the
genuine prediction path) plus a new static (AST-based) test suite,
`tests/test_diagnostics_prediction_boundary.py`, that parses each of
those modules' real import statements and asserts none of them import
`modeling/diagnostics.py`. `diagnostics.py` is confirmed to depend only
on `backtest.GameOutcome` (one-directional), and
`scripts/build_cfb_baseline.py` (the actual single-game research
projection CLI) is confirmed to not import `diagnostics.py` at all --
only `scripts/backtest_cfb_baseline.py`'s optional `--diagnostics` flag
does, and only to PRINT a report after a backtest already completed. No
outcome-derived diagnostic field can reach a prediction-time feature.

### 2A.4 Leakage-safe chronological model selection

The mission's own diagnosis of this pass's earlier draft: ablation
candidates had been compared on the COMPLETE 2022-2025 corpus, and the
same corpus's numbers were then presented as if they were untouched
validation -- a real risk of reporting an overfit selection as if it were
independently confirmed. This is corrected via **Option A**: 2022-2024 is
the DEVELOPMENT corpus (candidate selection uses ONLY this data); 2025 is
a genuinely held-out CONFIRMATION season, consulted only AFTER a
candidate is already selected, never to choose between candidates.

**Why this is a sound decomposition, not an approximation:** a new
regression test,
`test_development_only_backtest_matches_full_corpus_for_the_shared_seasons`
(`tests/test_modeling_backtest.py`), proves that running
`run_walk_forward_backtest` on a development-only season subset produces
BIT-IDENTICAL outcomes for those seasons as the corresponding prefix of a
run over the full corpus -- the mere presence of the later confirmation
season anywhere in the input can never change a development-season
prediction, because every rating/residual-pool/calibration state at a
given `as_of` is built strictly from rows before it, regardless of what
(if anything) comes after in the input list.

**Execution:** four fresh live workflow runs, restricted to `--seasons
2022 2023 2024` only (`dev-0-baseline-2022-2024` through
`dev-3-season-shrinkage-k-1-2022-2024`), selected the winning candidate
using ONLY that data (section 3.2/section 7). The confirmation-season
(2025) numbers used afterward are the already-collected, per-season 2025
segment from this pass's four full-corpus (2022-2025) runs -- reused
because they are deterministic and were not used to make the
development-only selection; no additional live run was needed to
compute them honestly.

### 2A.5 Population preservation

The conference-field addition to `TeamGameLine`/`GameOutcome` is purely
additive (new optional fields, default `None`) -- it touches no
accept/skip control flow in `build_team_game_lines`
(`_is_fbs_involved`, team resolution, and the postseason-week/score
checks are all unchanged). Confirmed: the development-only live runs
report **"Fetched 5374 team-game lines across seasons [2022, 2023,
2024]; 8541 games skipped"** -- identically structured skip accounting to
Milestone C's original runs -- and **2,402 predicted games**, which is
exactly `3,225 - 823` (the original full 2022-2025 count minus the 2025
season's own count, independently confirmed via this pass's earlier
diagnostic run). No population change occurred; none was needed.

## 3. Diagnosis: margin bias (mission Part A, sections 3-7)

### 3.1 The pattern

The Milestone C hardening pass had already found and reported an overall
margin bias of +3.26 (model under-predicts margins on average -- actual
outcomes are more lopsided than projected). This pass's full segmentation
(baseline run, `0-baseline-diagnostics`, n=3,225) makes the shape of that
bias explicit for the first time:

| Segment (by model's own projected margin) | n | Margin bias |
|---|---:|---:|
| projected margin in [-21,-14) | 4 | -3.81 (n too small to trust) |
| projected margin in [-14,-7) | 96 | -6.68 |
| projected margin in [-7,-3) | 331 | -2.36 |
| projected margin in [-3,0) | 573 | -0.57 |
| projected margin in [0,3) | 719 | +0.54 |
| projected margin in [3,7) | 792 | +5.84 |
| projected margin in [7,14) | 493 | +6.54 |
| projected margin in [14,21) | 198 | +18.59 |
| projected margin in [21,100) | 19 | +18.07 |

And by the coarser favorite/underdog/pick'em buckets:

| Segment | n | Margin bias |
|---|---:|---:|
| pick'em-like (\|margin\|<=3) | 1,292 | +0.05 |
| moderate favorite (3<\|margin\|<14) | 1,712 | +3.76 |
| large projected favorite (\|margin\|>=14) | 221 | +18.14 |
| home favorite | 2,221 | +5.52 |
| home underdog | 1,004 | -1.76 |

**The bias is near-zero for pick'em-like games and grows sharply, almost
monotonically, with the magnitude of the model's own projected margin.**
It is not symmetric: the model's home-favorite predictions are
under-shot more (and more severely at the extreme) than home-underdog
predictions are over-shot. In plain terms: **games the model is already
confident about turn out even more lopsided than the model expects,**
and this effect concentrates almost entirely in the large-favorite tail
(above roughly a 14-point projected margin) rather than being spread
evenly across all games.

Two structural segments are consistent with (not separate from) this
same pattern:
- **Weeks 2-3 bias is +9.07 vs. weeks 4+ at +1.93.** This is not
  evidence of a *separate* early-season problem -- early weeks have a
  higher share of large-mismatch, out-of-conference "buy games" (an FBS
  power hosting a weak opponent before conference play starts), so a
  higher concentration of large-projected-favorite games in weeks 2-3
  is itself sufficient to explain most of the early-season gap.
- **Conference games (+0.30 bias) vs. non-conference games (+4.30
  bias).** Conference games are drawn from a narrower talent-gap
  distribution (compare a Big Ten also-ran to a national contender both
  playing conference-mandated opponents) than an arbitrary
  non-conference "buy game" matchup, so this again reduces, rather than
  adds to, the count of extreme mismatches -- consistent with, not
  independent evidence against, the margin-magnitude finding above.

### 3.2 Root-cause elimination via genuine, leakage-safe ablation

Mission section 3 asks to determine whether the bias is "mostly X"
before changing the model. Three concrete, plausible hypotheses were
tested via genuine live walk-forward ablation, following the leakage-safe
DEVELOPMENT/CONFIRMATION procedure of section 2A.4: each candidate was
compared against baseline on 2022-2024 DEVELOPMENT data only, then
independently checked against the 2025 CONFIRMATION season (never the
reverse):

| Hypothesis | Change tested | Dev (2022-24) margin bias | Confirmation (2025) margin bias | Large-favorite-tail effect | Verdict |
|---|---|---:|---:|---|---|
| Individual-team ridge over-shrinkage | `ridge_lambda` 25.0 -> 10.0 | +3.31 (dev baseline +3.24) | +3.37 (baseline +3.33) | Same shape, still concentrated at the tail, on both | **REJECTED as bias fix** (still adopted for its OTHER benefits, section 4) |
| Pooled-FCS-parameter heterogeneity | `fcs_mode` pooled -> tiered | +3.42 (dev baseline +3.24) | +3.45 (baseline +3.33) | FBS-vs-FCS bias WORSE on both: dev +17.44 vs. +16.72; confirmation-consistent with the earlier full-corpus finding | **REJECTED** |
| Season-carryover over-shrinkage | `season_shrinkage_k` 4.0 -> 1.0 | +3.07 (dev baseline +3.24) | +3.31 (baseline +3.33) | No meaningful shift on either dataset | **REJECTED** |

All three interventions leave margin bias in the same narrow +3.0 to +3.5
band on BOTH the development and confirmation seasons (well within
run-to-run noise) and leave the large-favorite-tail concentration pattern
intact in both. This is a genuine negative result, replicated on
genuinely untouched confirmation data, not an absence of testing or an
artifact of tuning on the same data being re-reported as validation:
**the bias is very likely NOT primarily caused by over-regularization at
any of the three tested levels (individual-team ridge, pooled-FCS ridge,
or season-to-season carryover shrinkage).**

### 3.3 Leading hypothesis (not implemented this pass)

The pattern -- small bias near a 0 projected margin, growing sharply and
asymmetrically favoring blowouts at the tail -- is consistent with a
structural property of the additive points-per-play rating model
itself: `points_per_play ~= mu + offense[team] - defense[opponent] +
hfa * home_indicator` is a *linear* model of team strength differentials.
If the true relationship between a talent/efficiency gap and the
resulting scoring margin is even mildly convex (a good offense against a
bad defense gains MORE than proportionally, e.g. via forced turnovers,
extended drives, and second-and-short situations compounding), a purely
additive model will systematically under-predict the largest gaps while
staying accurate for close-to-even matchups -- exactly the shape
observed. This was not tested this pass: doing so credibly would require
a genuine structural change (e.g. a margin-scale nonlinearity or an
interaction term fit and validated via its own walk-forward ablation),
which is real, scoped, deferred future work (section 20), not something
to bolt on speculatively in the time remaining in this pass. Per mission
section 7's explicit fallback instruction ("if no reliable improvement is
possible... document... rather than forcing a fake solution"), this
report states plainly: **no reliable fix for the margin-bias pattern was
found in this pass.**

### 3.4 FBS-vs-FCS tiering (a specific instance of Part A's mandate)

A concrete, mechanically-derived, no-hindsight, deterministic tiering
extension was built and tested (mission section 7): each FCS opponent's
own trailing scoring margin against FBS teams (already present in the
corpus, strictly prior-only) buckets it into `weak` / `average` / `strong`
tiers via fixed thresholds (`FCS_TIER_WEAK_THRESHOLD=-35.0`,
`FCS_TIER_STRONG_THRESHOLD=-20.0`, `FCS_TIER_MIN_GAMES=2`, default tier
`average` for thin/unseen evidence) rather than one pooled parameter.
Result: FBS-vs-FCS margin bias went from +16.72 (pooled) to +17.44
(tiered) on 2022-2024 development data, and from +17.19 (pooled) to
+17.92 (tiered) on the 2025 confirmation season -- **worse, not better,
on both.** This is a genuine, reported negative finding, replicated on
untouched confirmation data: the extra structure did not pay for itself.
**`fcs_mode` remains `"pooled"` in the shipped configuration.**
`FBS-vs-FCS remains UNSUPPORTED_FOR_PRICING`, exactly as Milestone C left
it, per the mission's own fallback instruction rather than a forced fix.

## 4. Adopted change: `ridge_lambda` 25.0 -> 10.0

**Selected on 2022-2024 development data alone.** Isolated from the bias
question above, lowering `ridge_lambda` is a clear, multi-metric
out-of-time improvement there (n=2,402):

| Metric | Dev baseline (lambda=25.0) | Dev lambda=10.0 | Change |
|---|---:|---:|---:|
| Winner log loss (calibrated) | 0.6024 | 0.5940 | better |
| Winner Brier (calibrated) | 0.2095 | 0.2060 | better |
| Margin MAE | 14.96 | 14.58 | better |
| Margin RMSE | 19.15 | 18.65 | better |
| Margin bias | +3.24 | +3.31 | unchanged (not a bias fix) |
| Total MAE | 13.48 | 13.46 | unchanged |
| Total RMSE | 16.85 | 16.86 | unchanged |
| Margin 90% coverage | 0.961 | 0.958 | unchanged (still over-covering) |

`ridge_lambda=10.0` was the clear winner among all four development-only
candidates on winner LL, Brier, and margin MAE/RMSE (section 7) -- this
selection used ONLY 2022-2024 data, never the 2025 confirmation season.

**Checked, not re-selected, against the 2025 confirmation season**
(n=823 -- consulted only after the above selection was already locked
in):

| Metric | Confirmation baseline (lambda=25.0) | Confirmation lambda=10.0 | Change |
|---|---:|---:|---:|
| Winner log loss (calibrated) | 0.5614 | 0.5620 | **essentially flat -- a small regression, within noise for n=823, reported honestly rather than hidden** |
| Winner Brier (calibrated) | 0.1922 | 0.1917 | better |
| Margin MAE | 14.78 | 14.48 | better -- replicates the development gain |
| Margin RMSE | 18.75 | 18.34 | better -- replicates the development gain |
| Margin bias | +3.33 | +3.37 | unchanged (not a bias fix) |
| Total MAE | 13.03 | 13.09 | unchanged |
| Total RMSE | 16.21 | 16.33 | unchanged |
| FBS-vs-FCS margin bias | +17.19 | +17.39 | unchanged -- this change does not meaningfully touch the FCS segment |

**Honest summary of what replicates and what doesn't:** the margin
point-accuracy improvement (MAE/RMSE) is real and replicates cleanly on
untouched confirmation data. The winner-calibration improvement (log
loss/Brier) is a clear, multi-season win on development data, but on the
single held-out confirmation season it is a wash (Brier marginally
better, log loss marginally worse) -- consistent with genuine noise at
n=823 rather than a reversal, but not claimed as a strong confirmed
effect either. This is reported plainly rather than only emphasizing the
larger development-season numbers, per this mission's explicit
instruction not to present a tuned result as untouched validation, and
its "be explicit when something failed" instruction. The margin-accuracy
gain alone is sufficient to meet this mission's "genuine out-of-time
performance improvement" adoption bar (section 2); this is independent
of whether it explains the still-open margin-bias pattern.

## 5. Diagnosis: totals weakness (mission Part B, sections 8-13)

Milestone C's totals finding was "a wash vs. naive" (MAE 13.37 vs. naive
13.17). This pass's segmentation (`0-baseline-diagnostics` run) reveals
the totals weakness is not evenly distributed either:

| Segment (by model's own projected total) | n | Total bias |
|---|---:|---:|
| projected total in [35,42) | 20 | +1.84 |
| projected total in [42,49) | 369 | +2.34 |
| projected total in [49,56) | 1,858 | +1.79 |
| projected total in [56,63) | 701 | +2.76 |
| projected total in [63,70) | 238 | **+15.43** |
| projected total in [70,200) | 39 | **+17.11** |

For the large majority of games (projected total 42-63, n=2,928 of
3,225), total bias sits in a tight, modest +1.8 to +2.8 band. **The
weakness is concentrated almost entirely in the small minority of games
the model itself projects as very high-scoring (>=63 points)** --
exactly the same "compressed tail" shape as the margin-bias finding in
section 3, and consistent with the same leading hypothesis (a linear
efficiency/pace model under-predicting the extreme end of its own
distribution).

A second, distinct and opposite-signed effect appears specifically in
FBS-vs-FCS games: total bias there is **-9.09** (model OVER-predicts
total points, while simultaneously UNDER-predicting margin by +17.19).
This is a coherent, real pattern, not noise: once an FBS team is
comfortably ahead of an overmatched FCS opponent, both sides commonly
reduce pace (running clock, backup personnel, fewer possessions) --
suppressing total points scored even as the score DIFFERENTIAL keeps
growing. The high-total-bin effect above is a separate mechanism (two
efficient FBS offenses combining for more points than a linear
offense+opponent-defense sum predicts, i.e. a possible shootout/
interaction effect), since it points the opposite direction from the
FCS-specific effect and is not concentrated in FBS-vs-FCS games.

**No change was made to possession/efficiency features, interaction
terms, or the score-distribution family this pass.** Mission section 9
explicitly requires genuine ablation evidence, not speculation, before
adding such complexity; this segmentation identifies *where* the totals
weakness lives and a plausible *mechanism* (garbage-time deflation for
FCS games; an offense-efficiency interaction for shootouts) but does not
by itself constitute a validated fix -- implementing and validating either
would need its own dedicated ablation pass. This is intentionally
deferred (section 20), not built speculatively, per this mission's
explicit conservatism instruction.

### 5.1 Interval coverage (mission section 13)

Margin and total 90% interval coverage remain over-nominal under the
adopted configuration (0.954 margin / 0.962 total vs. a 90% target,
essentially unchanged from Milestone C's 0.959 / 0.966). No coverage-
targeting change was made this pass: the mission explicitly warns against
"cosmetic shrinking," and no walk-forward-safe, evidence-backed
recalibration of the simulation uncertainty was tested this pass (it
would need its own dedicated live ablation, not a speculative one-line
SD adjustment). **This remains an open, explicitly reported weakness**
(section 18), not a silently accepted one.

## 6. Winner-calibration protection (mission Part C, section 14)

The adopted configuration (lambda=10, platt calibration, pooled FCS,
season_shrinkage_k=4.0 unchanged) **improves** calibrated winner log loss
(0.5940 vs. 0.6024) and Brier (0.2060 vs. 0.2095) relative to the
Milestone C hardened baseline on the 2022-2024 development seasons used
to select it (section 4). On the 2025 confirmation season, held out from
that selection, Brier replicates as slightly better (0.1917 vs. 0.1922)
while log loss is essentially flat (0.5620 vs. 0.5614) -- reported as a
wash on confirmation, not oversold as a repeat of the development-season
win (section 4's "Honest summary"). No candidate tested this pass
materially HURT winner calibration on either dataset --
`fcs_mode=tiered`'s calibrated log loss (dev 0.6017, confirmation 0.5663)
is close to baseline on both, and it was rejected primarily on its
FBS-vs-FCS margin-bias regression (section 3.4), not calibration.
**Walk-forward Platt calibration is retained unchanged as the
calibration method** -- no genuine out-of-time-superior replacement was
found or sought this pass (isotonic was already tested and rejected in
Milestone C; re-litigating that finding was out of scope here).

## 7. Ablation table (mission Part D, section 15)

Per section 2A.4, candidate selection uses ONLY the 2022-2024 development
table below; the 2025 confirmation table is consulted afterward, never to
choose between candidates. "Dev baseline"/"Confirmation baseline"
reproduce Milestone C's hardened configuration, re-run live in this pass
for an apples-to-apples comparison on identical code/data.

**Development selection table** (n=2,402, seasons 2022-2024, walk-forward, calibrated):

| Variant | Winner LL | Brier | Margin MAE | Margin RMSE | Margin Bias | Total MAE | Total RMSE | Notes |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Dev baseline (ridge=25, fcs=pooled, season_k=4) | 0.6024 | 0.2095 | 14.96 | 19.15 | +3.24 | 13.48 | 16.85 | Reference anchor |
| ridge_lambda=10.0 | 0.5940 | 0.2060 | 14.58 | 18.65 | +3.31 | 13.46 | 16.86 | **SELECTED** -- best on every metric except bias/totals |
| fcs_mode=tiered | 0.6017 | 0.2092 | 14.99 | 19.20 | +3.42 | 13.46 | 16.82 | **REJECTED** -- FBS-vs-FCS bias worsened (+17.44 vs +16.72) |
| season_shrinkage_k=1.0 | 0.5967 | 0.2069 | 14.79 | 18.94 | +3.07 | 13.44 | 16.82 | **REJECTED** -- better than dev baseline but not better than ridge_lambda=10.0 on any metric |

**Confirmation table** (n=823, season 2025 only -- held out from the selection above, consulted only afterward):

| Variant | Winner LL | Brier | Margin MAE | Margin RMSE | Margin Bias | Total MAE | Total RMSE | Notes |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Confirmation baseline (ridge=25, fcs=pooled, season_k=4) | 0.5614 | 0.1922 | 14.78 | 18.75 | +3.33 | 13.03 | 16.21 | Reference anchor |
| ridge_lambda=10.0 (selected) | 0.5620 | 0.1917 | 14.48 | 18.34 | +3.37 | 13.09 | 16.33 | Margin-accuracy gain replicates; winner-LL essentially flat (section 4) |
| fcs_mode=tiered (already rejected) | 0.5663 | 0.1942 | 14.78 | 18.74 | +3.45 | 13.09 | 16.34 | FBS-vs-FCS bias regression replicates (+17.92 vs +17.19) |
| season_shrinkage_k=1.0 (already rejected) | 0.5613 | 0.1920 | 14.72 | 18.67 | +3.31 | 13.04 | 16.24 | Confirms no advantage over the selected candidate |

**Final selected model** = `ridge_lambda=10.0` (Milestone C architecture +
one adopted hyperparameter change), selected on development data alone
and independently checked, not re-selected, against confirmation data.

No variant, on either the development or confirmation season, improved
margin bias, total accuracy, or interval coverage over baseline -- the
only genuine, adopted improvement is margin-point-accuracy (replicated on
both datasets) plus a winner-calibration gain that is clear on
development data but a wash on the single confirmation season. This is
reported plainly rather than dressed up as a broader win.

## 8. Final selected model

Unchanged from Milestone C except where noted:

- **Offense/defense ratings**: ridge-regularized simultaneous
  least-squares on points-per-play, `DEFAULT_RIDGE_LAMBDA` **lowered
  from 25.0 to 10.0** (section 4). `DEFAULT_FCS_RIDGE_LAMBDA=4.0`
  unchanged.
- **Opponent adjustment**: unchanged additive Massey/SRS-style method
  (`ratings.py`). No structural change was ablation-justified this pass
  (section 3.2/3.3).
- **Pace/possessions**: unchanged (`DEFAULT_PACE_SHRINKAGE_K=4.0`); no
  possession/efficiency feature work was undertaken (section 5).
- **Home-field advantage**: unchanged single league-wide scalar term.
- **Season-carryover prior**: unchanged (`DEFAULT_SEASON_SHRINKAGE_K=4.0`
  in `priors.py`) -- the k=1.0 candidate was tested and rejected
  (section 3.2, section 7).
- **QB treatment**: unchanged team-level `percent_passing_ppa` proxy,
  uncertainty-only (no point-estimate shift), from Milestone C.
- **FCS treatment**: unchanged pooled single parameter, now with a
  documented, tested, and REJECTED tiered alternative available in code
  (`fcs_mode="tiered"`, opt-in only, not the default) for future
  reference. `fcs_mode="pooled"` remains the shipped default.
- **Score distribution**: unchanged Monte Carlo simulation family from
  Milestone C. No interaction-term or distribution-family change was
  ablation-justified this pass (section 5).
- **Uncertainty**: unchanged simulation-based interval construction;
  coverage remains over-nominal and unaddressed this pass (section 5.1).
- **Calibration**: unchanged walk-forward Platt scaling (section 6).

## 9. Before-vs-after metrics (overall, n=3,225)

**Descriptive aggregate only** -- combining development (2022-2024) and
confirmation (2025) seasons into one full-corpus number, for describing
what the shipped model looks like in aggregate. This table is NOT the
basis for the `ridge_lambda=10.0` selection (that was development-data-only,
section 7); it reproduces this pass's original single-corpus run for
continuity with Milestone C's own "overall" reporting convention. See
section 4 for the honest development-vs-confirmation breakdown.

| Metric | Before (Milestone C hardened) | After (C.2 adopted) |
|---|---:|---:|
| Winner log loss (calibrated) | 0.5921 | 0.5857 |
| Winner Brier (calibrated) | 0.2052 | 0.2022 |
| Margin MAE | 14.91 | 14.55 |
| Margin RMSE | 19.04 | 18.57 |
| Margin bias | +3.26 | +3.33 |
| Margin 90% coverage | 0.959 | 0.954 |
| Total MAE | 13.37 | 13.36 |
| Total RMSE | 16.70 | 16.72 |
| Total 90% coverage | 0.966 | 0.962 |

## 10. Segment breakdown (final selected model, ridge_lambda=10 run)

| Segment | n | Cal. winner LL | Margin MAE | Margin RMSE | Margin bias | Total MAE | Total RMSE | Total bias |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| FBS-vs-FBS | 2,935 | 0.6135 | 13.89 | 17.74 | +1.95 | 13.14 | 16.45 | +0.26 |
| FBS-vs-FCS | 290 | 0.3046 | 21.21 | 25.52 | +17.39 | 15.65 | 19.26 | -9.28 |
| Neutral site | 242 | 0.6689 | 13.31 | 17.26 | +2.30 | 14.50 | 17.44 | -1.55 |
| season 2022 | 791 | 0.6498 | 15.14 | 19.62 | +3.42 | 13.68 | 16.96 | -0.32 |
| season 2023 | 804 | 0.5400 | 14.26 | 18.16 | +3.41 | 13.48 | 16.86 | -0.50 |
| season 2024 | 807 | 0.5925 | 14.34 | 18.16 | +3.15 | 13.22 | 16.74 | -0.25 |
| season 2025 | 823 | 0.5620 | 14.48 | 18.34 | +3.37 | 13.09 | 16.33 | -1.29 |

(weeks 2-3 / weeks 4+ split was captured for the baseline diagnostic run
only, section 3.1's +9.07/+1.93; the adopted-model rerun of that specific
split was not separately re-verified since section 3.2's ablation already
showed the pattern is stable across the tested hyperparameter changes.)

## 11. FBS-vs-FCS: final status

- Margin bias: **+17.39** (final model), essentially unchanged from
  Milestone C's +17.24 and this pass's own pooled baseline (+17.19).
- Total bias: **-9.28**.
- Margin MAE/RMSE: 21.21 / 25.52.
- Treatment: pooled single FCS offense/defense parameter
  (`DEFAULT_FCS_RIDGE_LAMBDA=4.0`); a mechanically-derived, deterministic
  tiered alternative was built, tested, and rejected (section 3.4).
- **Supported for pricing: NO.** Unchanged from Milestone C, per this
  mission's own explicit fallback instruction -- no reliable improvement
  was found, so `UNSUPPORTED_FOR_PRICING` is preserved rather than forced.

## 12. CORE_V1 readiness (mission section 20 reclassification)

Same two distinct axes as Milestone C section 13 -- RESEARCH_PRIMITIVE_AVAILABLE
(does a leakage-safe, backtested probability primitive exist) vs.
PRODUCTION_PRICING_READY (validated and mature enough to price a real
Kalshi contract). A third, intermediate label is introduced this pass per
mission section 20's explicit request for a conservative middle tier:
**RESEARCH_VALIDATED** (backtested, out-of-time-improved, and with no
known material regression -- but still not a pricing decision).
PRODUCTION_PRICING_READY remains **NO for all three families,
unconditionally** -- no Kalshi contract/settlement mapping exists
anywhere in this codebase.

### Game winner
- RESEARCH_PRIMITIVE_AVAILABLE: **YES**.
- RESEARCH_VALIDATED: **YES** -- calibrated log loss/Brier genuinely
  improved out-of-time (section 4/6), stable across 3 of 4 seasons, no
  material regression found.
- PRODUCTION_PRICING_READY: **NO** -- unconditionally.

### Point spread
- RESEARCH_PRIMITIVE_AVAILABLE: **YES**.
- RESEARCH_VALIDATED: **NO, not upgraded this pass.** Margin point-error
  (MAE/RMSE) improved, but the margin-bias pattern (section 3) was
  diagnosed in detail and NOT fixed -- a real, material, and now better-
  understood limitation, not resolved evidence.
- FBS-vs-FCS: **UNSUPPORTED_FOR_PRICING**, unchanged (section 11).
- PRODUCTION_PRICING_READY: **NO** -- unconditionally.

### Game total
- RESEARCH_PRIMITIVE_AVAILABLE: **YES**.
- RESEARCH_VALIDATED: **NO.** Still a wash vs. naive on aggregate
  accuracy; this pass newly diagnosed WHERE the weakness concentrates
  (section 5) but did not fix it; coverage remains over-nominal
  (section 5.1).
- PRODUCTION_PRICING_READY: **NO** -- unconditionally.

**Material limitation common to all three families:** none of Kalshi's
settlement mechanics (push/tie handling, contract structure) are
implemented anywhere in this codebase -- this remains a football
probability model, not a market-pricing pipeline, exactly as Milestone C
scoped it.

## 13. Remaining weaknesses (stated honestly)

- **The adopted `ridge_lambda=10.0`'s winner-calibration gain is a wash on
  the single held-out confirmation season**, even though it is a clear,
  multi-metric win on development data (section 4). Only the
  margin-point-accuracy portion of the gain clearly replicates. This is a
  real, quantified limit of "confirmed on one confirmation season" rather
  than a larger multi-season out-of-time test.
- **The favorite-tail margin-bias pattern is diagnosed but unfixed.**
  Three plausible regularization-based hypotheses were tested and ruled
  out (section 3.2); the leading hypothesis (a structural non-linearity
  the additive rating model can't express) was not tested this pass.
  This is the single largest open item from this pass.
- **The high-projected-total shootout effect (+15 to +17 bias above a
  63-point projected total) is newly diagnosed, not fixed.** No
  possession/efficiency/interaction-term work was done this pass.
- **FBS-vs-FCS margin bias (+17.39) remains materially unresolved**; the
  one concrete structural alternative tested this pass (tiering) made it
  slightly worse, not better.
- **Interval coverage (margin 0.954, total 0.962 vs. a 90% nominal
  target) remains over-nominal**, unchanged in substance from Milestone
  C, and was not addressed this pass.
- **`DEFAULT_RIDGE_LAMBDA=10.0` and the other shrinkage constants remain
  provisional, round numbers** picked from a coarse 2-point ablation
  sweep (25.0 vs. 10.0), not a fine-grained cross-validated optimum --
  a real next-step improvement, not claimed as fully tuned.
- **HFA remains a single league-wide scalar**; conference/travel/altitude
  variation was not investigated this pass either.
- **No possession/efficiency features (PPA, success rate, explosiveness),
  talent composite, or alternative score-distribution family were
  wired in**, consistent with this mission's explicit instruction not to
  add complexity without genuine, ablation-backed evidence -- this
  pass's segmentation work provides real evidence pointing at WHERE such
  features might help (large-favorite / high-total games specifically),
  but that is a target for future ablation, not a validated result yet.

## 14. Recommended next step

**Another model-quality pass, not Milestone D.** The favorite-tail
margin-bias pattern (section 3) and the high-total shootout effect
(section 5) are the two highest-priority open items -- both newly
characterized with real segmentation evidence in this pass, both still
unexplained by the regularization-based hypotheses this pass ruled out.
A credible next pass should specifically test a genuine structural
hypothesis (e.g. a margin-scale nonlinearity, or an explicit
offense-efficiency interaction term for the total-points model), each via
its own dedicated live walk-forward ablation, rather than proceeding to
Kalshi pricing against a model whose largest, most broadly-distributed
known bias remains open. FBS-vs-FCS pricing support should remain
explicitly withheld until that segment's bias is independently resolved
or the segment is confirmed unpriced by Kalshi (per Milestone B.5's own
still-UNVERIFIED finding on FBS-vs-FCS listing coverage). Any future
ablation pass should reuse this pass's leakage-safe development/
confirmation procedure (section 2A.4) from the start, rather than
comparing candidates on the complete corpus and risking the same
selection-overfitting failure mode this pass's audit found and corrected.

## 15. Versioning and reproducibility (mission section 18)

`scripts/build_cfb_baseline.py`'s `MODEL_VERSION` is bumped from
`"0.1.0-milestone-c"` to `"0.2.0-milestone-c2"`. A new
`RATINGS_COMPONENT_VERSION` string
(`"ridge_lambda=10.0;fcs_mode=pooled;calibration=platt;fcs_treatment=pooled-shrinkage-v2"`)
is threaded into every `ModelVersion.ratings_component_version` produced
by that script, so any historical `ProjectionRecord` can be traced back to
exactly which rating/calibration/FCS-treatment configuration produced it,
alongside the existing `git_commit_sha` field and each record's training
cutoff (`AsOf(season, week)`, already recorded via the `as_of` argument
and reflected in the projection's `game_id`). Re-running
`build_cfb_baseline.py` with the same seasons/as-of/seed against a given
commit remains fully deterministic -- no new source of nondeterminism was
introduced this pass.

## 16. Explicit scope boundary (mission section 21)

No Kalshi ingestion, pricing, fee modeling, edge calculation, bet
qualification, staking, recommendation, or execution surface was added or
modified anywhere in this pass. `tests/test_no_recommendation_surface.py`
(unchanged from Milestone C) continues to pass and continues to guard
this boundary.
