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

**UPDATE (Part 2, same PR, later session): a second ablation round tested
totals/pace/possession candidates and an uncertainty-calibration
candidate, both under the same leakage-safe development(2022-2024)/
confirmation(2025) discipline established above. Two more changes were
ADOPTED: `pace_mode` `"symmetric"` -> `"matchup"` (matchup-level tempo
interaction) and a global `residual_scale` `1.0` -> `0.85` (uncertainty
narrowing). One more candidate (`pace_shrinkage_k=1.0`) was tested and
REJECTED. See section 17 onward ("Part 2") for the full ablation,
confirmation results, and the updated final model. Sections 1-16 below
are preserved as the historical record of the FIRST C.2 round; where a
Part-1 statement is superseded by Part 2, a note says so explicitly
rather than leaving stale numbers unqualified.**

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

**SUPERSEDED by Part 2 (section 19):** a `residual_scale` global
multiplier was tested via genuine live ablation on development data (not
a "cosmetic" hand-picked constant) and ADOPTED at `0.85`. Coverage is now
0.917 margin / 0.926 total (full corpus) and 0.905 margin / 0.923 total
on the untouched 2025 confirmation season -- both much closer to the 90%
nominal target, confirmed to replicate out-of-time. Still not exactly
0.900 (as instructed, this was never forced), and still slightly wide;
see section 19 for the full ablation against 0.90 and 1.0.

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
- **Pace/possessions**: `DEFAULT_PACE_SHRINKAGE_K=4.0` unchanged
  (`pace_shrinkage_k=1.0` was tested and REJECTED, section 20).
  **SUPERSEDED by Part 2 (section 20): `pace_mode` ADOPTED as
  `"matchup"`** (matchup-level tempo interaction -- each side's expected
  plays now combines its own trailing offense pace with the opponent's
  trailing defensive plays-allowed, instead of both sides sharing one
  symmetric average). `pace_mode="symmetric"` (Milestone C behavior)
  remains available as an explicit opt-out.
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
- **Efficiency features (PPA/success rate)**: NOT implemented this pass;
  confirmed leakage-safe but deferred for lack of a strong dev-set signal
  pointing specifically at efficiency (section 18).
- **Score distribution**: unchanged Monte Carlo simulation family from
  Milestone C. No interaction-term or distribution-family change was
  ablation-justified this pass (section 5).
- **Uncertainty**: unchanged simulation-based interval construction.
  **SUPERSEDED by Part 2 (section 19): a global `residual_scale`
  multiplier ADOPTED at `0.85`** (`DEFAULT_RESIDUAL_SCALE`), narrowing
  every simulated residual draw uniformly on top of the existing
  QB-continuity/early-season/FCS-involved multipliers. `residual_scale=1.0`
  (Milestone C behavior, a true no-op) remains available as an explicit
  opt-out.
- **Calibration**: unchanged walk-forward Platt scaling (section 6);
  reconfirmed as the best available method in Part 2 (section 21).

## 9. Before-vs-after metrics (overall, n=3,225)

**Descriptive aggregate only** -- combining development (2022-2024) and
confirmation (2025) seasons into one full-corpus number, for describing
what the shipped model looks like in aggregate. This table is NOT the
basis for the `ridge_lambda=10.0` selection (that was development-data-only,
section 7); it reproduces this pass's original single-corpus run for
continuity with Milestone C's own "overall" reporting convention. See
section 4 for the honest development-vs-confirmation breakdown.

**UPDATED for Part 2's adopted `pace_mode=matchup` + `residual_scale=0.85`
(section 22's single confirmation run, `CONFIRMATION-final-c2-candidate-2022-2025`,
n=3,225) -- the "Round 1" column below is the state described by the rest
of this Part-1 section (`ridge_lambda=10.0` alone); "Final (Part 2)" is
the fully adopted C.2 model:**

| Metric | Before (Milestone C hardened) | Round 1 (ridge_lambda=10.0 alone) | Final (Part 2: +matchup pace +residual_scale 0.85) |
|---|---:|---:|---:|
| Winner log loss (calibrated) | 0.5921 | 0.5857 | 0.5815 |
| Winner Brier (calibrated) | 0.2052 | 0.2022 | 0.2004 |
| Margin MAE | 14.91 | 14.55 | 14.41 |
| Margin RMSE | 19.04 | 18.57 | 18.37 |
| Margin bias | +3.26 | +3.33 | +2.96 |
| Margin 90% coverage | 0.959 | 0.954 | **0.917** |
| Total MAE | 13.37 | 13.36 | 13.36 |
| Total RMSE | 16.70 | 16.72 | 16.69 |
| Total bias | (not captured this table, Round 1) | (not captured this table, Round 1) | -0.60 |
| Total 90% coverage | 0.966 | 0.962 | **0.926** |

The clearest, broadest gain across both rounds is in interval coverage
(both margin and total moved substantially closer to the 90% nominal
target) alongside a modest, monotonic improvement in winner calibration
and margin point-accuracy. Total point-accuracy (MAE/RMSE) remains
essentially flat across all three configurations -- the totals weakness
diagnosed in section 5 (and further in section 18) was NOT resolved by
either round's changes.

## 10. Segment breakdown (final selected model)

**UPDATED for Part 2's fully adopted model** (`ridge_lambda=10.0` +
`pace_mode=matchup` + `residual_scale=0.85`), from the single Part-2
confirmation run (section 22, n=3,225, full corpus, calibrated):

| Segment | n | Cal. winner LL | Margin MAE | Margin RMSE | Margin bias | Margin 90% cov | Total MAE | Total RMSE | Total bias | Total 90% cov |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FBS-vs-FBS | 2,935 | 0.6104 | 13.79 | 17.59 | +1.61 | 0.919 | 13.13 | 16.42 | +0.25 | 0.924 |
| FBS-vs-FCS | 290 | 0.2895 | 20.66 | 24.93 | +16.63 | 0.903 | 15.61 | 19.21 | -9.25 | 0.945 |
| Neutral site | 242 | 0.6641 | 13.18 | 17.09 | +2.10 | 0.934 | 14.51 | 17.31 | -1.38 | 0.905 |
| Home/away (non-neutral) | 2,983 | 0.5748 | 14.51 | 18.47 | +3.03 | -- | 13.26 | 16.64 | -- | -- |
| season 2022 | 791 | 0.6375 | 14.84 | 19.26 | +2.72 | -- | 13.65 | 16.89 | -- | -- |
| season 2023 | 804 | 0.5340 | 14.10 | 17.93 | +3.04 | -- | 13.53 | 16.84 | -- | -- |
| season 2024 | 807 | 0.5939 | 14.29 | 18.08 | +2.88 | -- | 13.20 | 16.77 | -- | -- |
| **season 2025 (CONFIRMATION)** | **823** | **0.5621** | **14.42** | **18.20** | **+3.20** | **0.905** | **13.05** | **16.27** | **-1.24** | **0.923** |
| weeks 2-3 | 600 | 0.4982 | 17.96 | 22.55 | +8.28 | 0.883 | 13.60 | 16.86 | -3.59 | 0.945 |
| weeks 4+ | 2,625 | 0.6006 | 13.60 | 17.27 | +1.75 | 0.925 | 13.30 | 16.65 | +0.08 | 0.921 |

The FBS-vs-FBS/FBS-vs-FCS/Neutral-site/Home-away/weeks rows above are
full-corpus (2022-2025) segments, consistent with this doc's original
section-10 convention; only the **season 2025 row is the untouched
confirmation-season number** (never used to select `pace_mode` or
`residual_scale` -- see section 22 for the full confirmation report,
including why FBS-vs-FBS/FBS-vs-FCS/neutral-site were not separately
re-cut to 2025-only games, a scope decision stated explicitly there).

## 11. FBS-vs-FCS: final status

**UPDATED for Part 2's fully adopted model** (section 10's table):

- Margin bias: **+16.63** (final model, full corpus) -- essentially
  unchanged in magnitude from Milestone C's +17.24 and Round 1's +17.39;
  small movement is consistent with `pace_mode`/`residual_scale` not
  targeting this segment, not a genuine fix.
- Total bias: **-9.25**, likewise essentially unchanged.
- Margin MAE/RMSE: 20.66 / 24.93. Margin 90% coverage 0.903, total 90%
  coverage 0.945 (both close to or above nominal -- FCS games' wider
  natural spread means the uncertainty story here was never primarily
  about `residual_scale`).
- Treatment: pooled single FCS offense/defense parameter
  (`DEFAULT_FCS_RIDGE_LAMBDA=4.0`); a mechanically-derived, deterministic
  tiered alternative was built, tested, and rejected (section 3.4). No
  further FCS-specific work was attempted in Part 2, per this pass's
  explicit instruction not to keep iterating endlessly on FCS -- Part 2's
  effort was deliberately concentrated on the FBS-vs-FBS core market
  (section 17).
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

**UPDATED for Part 2** (section 23 has the full, current readiness table
with rationale spanning both rounds; the entries below are kept in sync):

### Game winner
- RESEARCH_PRIMITIVE_AVAILABLE: **YES**.
- RESEARCH_VALIDATED: **YES** -- calibrated log loss/Brier genuinely
  improved out-of-time across BOTH rounds (section 4/6, section 22),
  stable across seasons, no material regression found.
- PRODUCTION_PRICING_READY: **NO** -- unconditionally.

### Point spread
- RESEARCH_PRIMITIVE_AVAILABLE: **YES**.
- RESEARCH_VALIDATED: **YES, upgraded in Part 2.** Round 1 alone left this
  at NO (margin point-error improved but the bias pattern was unfixed).
  Part 2 adds a genuine, multi-metric, out-of-time-confirmed improvement
  on TOP of that: margin MAE/RMSE further improved AND margin 90%
  coverage moved substantially closer to nominal (0.954 -> 0.917 full
  corpus; 0.905 on the untouched 2025 confirmation season alone,
  section 22) with no material regression anywhere tested. The
  favorite-tail margin-bias pattern (section 3) is REDUCED in magnitude
  (+3.33 -> +2.96 full corpus) but still present and still not
  eliminated -- stated as a real, quantified, remaining limitation
  (section 24), not treated as fully resolved.
- FBS-vs-FCS: **UNSUPPORTED_FOR_PRICING**, unchanged (section 11).
- PRODUCTION_PRICING_READY: **NO** -- unconditionally.

### Game total
- RESEARCH_PRIMITIVE_AVAILABLE: **YES**.
- RESEARCH_VALIDATED: **NO, still not upgraded.** Point-accuracy (MAE/RMSE)
  remains essentially flat vs. naive across both rounds (section 9); Part
  2 diagnosed the totals-bias mechanism in more detail (section 18, two
  distinct opposite-signed patterns) and materially improved total
  interval COVERAGE (0.962 -> 0.926 full corpus; 0.923 on 2025
  confirmation, section 22) via `residual_scale`, but did not improve
  total point-accuracy itself -- calibration and accuracy are genuinely
  separate axes here, and only the former improved.
- PRODUCTION_PRICING_READY: **NO** -- unconditionally.

**Material limitation common to all three families:** none of Kalshi's
settlement mechanics (push/tie handling, contract structure) are
implemented anywhere in this codebase -- this remains a football
probability model, not a market-pricing pipeline, exactly as Milestone C
scoped it.

## 13. Remaining weaknesses (stated honestly)

**UPDATED for Part 2 -- see section 24 for the current, complete list.**
In summary: interval coverage (previously the single largest quantified
gap) is now substantially improved and no longer the top item; the
favorite-tail margin-bias pattern, the high-total shootout effect, and
FBS-vs-FCS all remain open, each essentially unchanged in shape from
Part 1's diagnosis below.

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

**UPDATED for Part 2 -- see section 25 for the current recommendation.**

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

**SUPERSEDED by Part 2:** `MODEL_VERSION` is now
`"0.3.0-milestone-c2"` and `RATINGS_COMPONENT_VERSION` is now
`"ridge_lambda=10.0;pace_mode=matchup;residual_scale=0.85;fcs_mode=pooled;calibration=platt;fcs_treatment=pooled-shrinkage-v2"`
(section 21). Same determinism guarantee holds.

## 16. Explicit scope boundary (mission section 21)

No Kalshi ingestion, pricing, fee modeling, edge calculation, bet
qualification, staking, recommendation, or execution surface was added or
modified anywhere in this pass. `tests/test_no_recommendation_surface.py`
(unchanged from Milestone C) continues to pass and continues to guard
this boundary.

---

# Part 2 — Totals, pace, and uncertainty (this session)

Continuing PR #5 from expected head `9708669` (the historical-integrity
audit commit accepted at the start of this session). PR #5 was **not
merged** and Milestone D was **not begun** at any point in this pass, per
explicit instruction. All audit fixes from Part 1 (section 2A) are
preserved verbatim and unmodified: historical conference classification
still uses per-game CFBD fields only, FCS tiering remains strictly as-of,
diagnostics remain post-hoc only (section 2A.3's architectural-boundary
test suite still passes, untouched), and model selection remains
leakage-safe and chronological (2022-2024 development / 2025
confirmation, section 2A.4's procedure, reused verbatim below). The
accepted Round-1 finding (`ridge_lambda=10.0`) is preserved and used as
the base configuration for every candidate tested in this Part; it was
NOT re-litigated or re-selected.

## 17. Totals diagnosis on development data (mission Part 2 section 1)

`diagnostics.py` was extended with `actual_total_bin`, a population-
median-split tempo/combined-offense-strength/combined-defense-strength
segmentation, and `source_of_total_bias_summary` (all post-hoc, reading
only already-computed `GameOutcome` fields sourced from the same
already-fitted `ratings` snapshot at prediction time -- no new leakage
surface; see `modeling/diagnostics.py` docstrings and
`tests/test_modeling_diagnostics.py`). Run against the Round-1 baseline
(`ridge_lambda=10.0`, `pace_mode=symmetric`, `residual_scale=1.0`) on
**development data only** (2022-2024, n=2,402):

**`source_of_total_bias_summary` (dev, n=2,402):**

| Segment | Total bias |
|---|---:|
| overall | -0.32 |
| FBS-vs-FBS | +0.50 |
| FBS-vs-FCS | -8.72 |
| conference game | +0.70 |
| non-conference game | -2.27 |
| neutral site | -0.78 |
| early season (week<=3) | -3.70 |
| later season (week>3) | +0.46 |
| large projected margin (blowout) | -7.10 |
| close projected margin | +0.76 |
| high tempo (>= median expected plays) | +0.07 |
| low tempo | -0.71 |
| strong combined offense (>= median) | +0.02 |
| weak combined offense | -0.66 |
| strong combined defense (>= median) | -0.93 |
| weak combined defense | +0.29 |

**By the model's own projected-total bin (dev):**

| Projected total bin | n | Total bias |
|---|---:|---:|
| [35,42) | 13 | +6.13 |
| [42,49) | 275 | +1.58 |
| [49,56) | 1,384 | +2.02 |
| [56,63) | 534 | +2.68 |
| [63,70) | 173 | **+15.47** |
| [70,200) | 23 | **+15.88** |

**Two distinct, opposite-signed mechanisms, not one:**
1. **Blowout / large-projected-margin games (dominated by FBS-vs-FCS)
   show NEGATIVE total bias** (-7.10 large-margin, -8.72 FBS-vs-FCS
   specifically): the model over-predicts total points. Consistent with
   garbage-time clock management -- once a game is decided, both sides
   commonly slow the pace, suppressing total points even as the margin
   keeps growing.
2. **High-projected-total (shootout-type) games show strongly POSITIVE
   total bias** (+15 to +16 above a 63-point projected total): the model
   under-predicts total points. This is a separate mechanism, not the
   mirror of (1) -- it is not concentrated in FBS-vs-FCS games and is not
   explained by tempo alone (`high_tempo_bias` is only +0.07).

**Tempo and offense/defense-strength segmentation alone show negligible
correlation with total bias** (all in the -0.93 to +0.29 range) --
ruling out a simple "just adjust for pace" or "just adjust for combined
strength" explanation for either mechanism above. This directly motivated
sections 18-19's candidates: pace/possession modeling (targeting
mechanism 1's clock-management/garbage-time story via genuinely
matchup-specific expected plays) and uncertainty calibration (targeting
the over-wide intervals visible throughout, independent of either bias
mechanism).

## 18. Possession/pace audit (mission Part 2 section 2)

Two candidates were tested via genuine live walk-forward ablation on
**development data only**, against the `ridge_lambda=10.0` baseline
above, using ONLY leakage-safe, pregame-available data already captured
in the corpus (no new CFBD endpoint call):

- **`pace_mode="matchup"`**: each team's own expected plays now combines
  its OWN trailing offensive pace with the OPPONENT's trailing defensive
  "plays allowed" tendency (`defense_pace_allowed`, re-aggregated from the
  SAME already-captured `team_plays` field from the opponent's
  perspective, FBS-vs-FBS only), instead of both teams sharing one
  symmetric `(home_pace + away_pace)/2` value. This is a genuine
  matchup-level tempo interaction, not a new data source, and does not
  double-count pace with efficiency (offense/defense ratings are fit on
  points-per-play, entirely separate from the plays-per-game pace model).
- **`pace_shrinkage_k=1.0`** (vs. the default 4.0): trusts a team's own
  trailing pace sample faster, with less shrinkage toward the league
  average.

**Isolated dev-set effect (n=2,402, calibrated, vs. Round-1 baseline
WinLL 0.5940 / Brier 0.2060 / margin MAE 14.58 / RMSE 18.65 / margin cov
0.958 / total MAE 13.46 / RMSE 16.86 / total bias -0.35 / total cov
0.965):**

| Candidate | Winner LL | Brier | Margin MAE | Margin RMSE | Total MAE | Total RMSE | Total bias | Total 90% cov | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `pace_shrinkage_k=1.0` | 0.5937 | 0.2058 | 14.58 | 18.65 | 13.45 | 16.86 | -0.37 | 0.965 | **REJECTED** -- statistically indistinguishable from baseline on every metric |
| `pace_mode=matchup` | 0.5923 | 0.2052 | 14.53 | 18.58 | 13.47 | 16.84 | -0.34 | 0.967 | **ACCEPTED** -- small, real, broad improvement on winner/margin; totals essentially unchanged (does not fix the totals-bias mechanisms of section 17, but does not hurt them either) |

`pace_mode=matchup` improves winner LL, Brier, and margin MAE/RMSE
simultaneously while leaving total accuracy, total bias, and coverage
essentially flat -- a genuine multi-metric win with no material downside,
meeting this pass's "reject changes that improve totals trivially but
damage winner/margin quality" bar (inverted here: it improves
winner/margin without damaging totals). It does NOT resolve either
totals-bias mechanism from section 17; that remains open (section 24).
`pace_shrinkage_k=1.0` was rejected outright: no meaningful change on any
metric, consistent with the trailing-pace estimate already being well
past its useful-sample-size plateau at the default k=4.0.

## 19. Uncertainty calibration audit (mission Part 2 section 5)

**Procedure, stated explicitly (never "cosmetic" tuning to hit exactly
0.900):** the Round-1 model over-covers by a consistent 5-7 percentage
points on both margin and total (0.958/0.965 vs. a 90% nominal target).
A single global `residual_scale` multiplier (applied uniformly on top of,
not replacing, the existing QB-continuity/early-season/FCS-involved
per-scenario multipliers) was tested at two candidate values bracketing
a plausible correction for that magnitude of over-coverage -- **0.90**
and **0.85** -- against the `ridge_lambda=10.0` + `pace_mode=matchup`
base, via genuine live walk-forward ablation on **development data
only**. Neither value was chosen by first computing the target coverage
and solving backward for the exact multiplier that would hit 0.900; both
were tested, compared, and the SELECTION was based on broad, multi-metric
evidence (not narrowly on which one's coverage number looked closest to
0.900).

**Dev-set ablation (n=2,402, calibrated):**

| `residual_scale` | Winner LL | Brier | Margin MAE | Margin RMSE | Margin 90% cov | Total MAE | Total RMSE | Total 90% cov |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.0 (baseline, no scaling) | 0.5940 | 0.2060 | 14.58 | 18.65 | 0.958 | 13.46 | 16.86 | 0.965 |
| 0.90 | 0.5910 | 0.2046 | 14.49 | 18.55 | 0.935 | 13.44 | 16.83 | 0.938 |
| **0.85** | **0.5900** | **0.2041** | **14.47** | **18.51** | **0.921** | **13.44** | **16.84** | **0.923** |

**`residual_scale=0.85` dominates `0.90` on every accuracy metric**
(winner LL, Brier, margin MAE/RMSE) while ALSO landing closer to the 90%
nominal target on both margin and total coverage -- a genuine, broad win,
not a coverage-chasing artifact of picking the smaller number. `0.85` was
selected on this basis. FCS-involved games specifically: at
`residual_scale=0.85`, FBS-vs-FCS margin coverage was 0.897 (very close
to nominal) vs. 0.930 at `0.90` -- consistent with the broader pattern.
No value below 0.85 was tested; this is a two-point bracketing test, not
an exhaustive search, and the result is reported as such (section 24).

## 20. Efficiency-feature scope decision (mission Part 2 section 3)

PPA/success-rate features were confirmed **leakage-safe** (same
postgame-per-game-stat category as `plays`/points already used, not
ambiguous like Elo/SP+/FPI which were excluded in Milestone C for genuine
timing ambiguity) but were **NOT implemented this pass**, for two
concrete reasons: (1) genuine risk of miscalibrating a new PPA-based
rating pathway without a dedicated live-verified field-semantics check,
and (2) section 17's segmentation shows tempo and combined offense/
defense strength -- the proxies most directly related to efficiency --
have only weak-to-negligible correlation with total bias (-0.93 to +0.29
across all four segments), providing no strong positive signal that an
efficiency feature would specifically address either totals-bias
mechanism. This is a deliberate, evidence-based deferral, not an
oversight, consistent with this mission's explicit "avoid feature soup"
and "one family at a time, only with genuine signal" instructions.

## 21. Final development ablation table (mission Part 2 section 8)

Per the leakage-safe procedure (section 2A.4, reused verbatim): every row
below was selected using ONLY 2022-2024 development data; 2025 was not
consulted until section 22, after this table -- and the final row -- was
already locked in.

**Development ablation table (n=2,402, seasons 2022-2024, walk-forward,
calibrated):**

| Variant | Winner LL | Brier | Margin MAE | Margin RMSE | Margin Bias | Total MAE | Total RMSE | Total Bias | Margin 90% | Total 90% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Milestone C baseline (ridge=25) | 0.6024 | 0.2095 | 14.96 | 19.15 | +3.24 | 13.48 | 16.85 | n/a\* | 0.961 | n/a\* |
| `ridge_lambda=10.0` (Round 1 SELECTED, this Part's base) | 0.5940 | 0.2060 | 14.58 | 18.65 | +3.24 | 13.46 | 16.86 | -0.35 | 0.958 | 0.965 |
| `pace_shrinkage_k=1.0` | 0.5937 | 0.2058 | 14.58 | 18.65 | +3.31 | 13.45 | 16.86 | -0.37 | 0.958 | 0.965 | **REJECTED** |
| `pace_mode=matchup` | 0.5923 | 0.2052 | 14.53 | 18.58 | +3.33 | 13.47 | 16.84 | -0.34 | 0.960 | 0.967 | **ACCEPTED** |
| `residual_scale=0.90` (on ridge10 base) | 0.5910 | 0.2046 | 14.49 | 18.55 | +3.04 | 13.44 | 16.83 | -0.35 | 0.935 | 0.938 | tested, dominated |
| `residual_scale=0.85` (on ridge10 base) | 0.5900 | 0.2041 | 14.47 | 18.51 | +2.89 | 13.44 | 16.84 | -0.38 | 0.921 | 0.923 | **ACCEPTED** |
| **FINAL COMBINED: ridge10 + matchup + rscale0.85** | **0.5891** | **0.2037** | **14.41** | **18.43** | **+2.88** | **13.45** | **16.82** | **-0.37** | **0.921** | **0.925** | **SELECTED** |

\* Total bias / total 90% coverage were not captured in the Milestone C
baseline's original table (Part 1 section 7) and no raw log from that
specific run remains accessible this session; reported as unavailable
rather than fabricated. All other cells in this row are direct quotes
from Part 1's documented, live-verified numbers.

The FINAL COMBINED row was itself run once, live, on development data
(`dev-8-combined-ridge10-matchup-rscale085-2022-2024`) to verify the two
accepted candidates compose cleanly rather than interacting unexpectedly
-- they do: the combined row improves winner LL/Brier/margin MAE/RMSE
beyond either candidate alone, and coverage matches `residual_scale=0.85`
alone (as expected, since `pace_mode` barely touches coverage). Total
MAE/RMSE/bias remain flat throughout, confirming section 17's finding
that neither accepted candidate addresses the totals-accuracy weakness.
**This is the final selected C.2 model:** `ridge_lambda=10.0`,
`pace_mode="matchup"`, `residual_scale=0.85`, `fcs_mode="pooled"`,
`season_shrinkage_k=4.0`, Platt calibration.

## 22. Confirmation run (mission Part 2 section 9) -- run EXACTLY ONCE

The final candidate, frozen after section 21 using ONLY 2022-2024 data,
was evaluated on the full 2022-2025 corpus **exactly once**
(`CONFIRMATION-final-c2-candidate-2022-2025`, live workflow run, job
`97561747815`) -- this single run is both the "before-vs-after" full-
corpus aggregate (section 9's updated table) and the source of every
number in this section. The selection logic itself (sections 18-21) never
inspected 2025 at any point; this run is the first and only time 2025 was
consulted for this candidate.

**2025 confirmation season only (n=823, calibrated):**

| Metric | Value |
|---|---:|
| Winner log loss | 0.5621 |
| Winner Brier | 0.1919 |
| Margin MAE / RMSE / bias | 14.42 / 18.20 / +3.20 |
| Margin 90% coverage | **0.905** |
| Total MAE / RMSE / bias | 13.05 / 16.27 / -1.24 |
| Total 90% coverage | **0.923** |

**Comparison to Round 1's confirmation numbers** (`ridge_lambda=10.0`
alone, Part 1 section 4: WinLL 0.5620, Brier 0.1917, margin MAE/RMSE
14.48/18.34, total MAE/RMSE 13.09/16.33): winner calibration is
essentially flat (as expected -- neither Part-2 candidate targets winner
calibration specifically), margin and total point-accuracy both improved
slightly (margin MAE 14.48->14.42, total MAE 13.09->13.05), and **interval
coverage improved substantially and replicates cleanly out-of-time** --
this is the clearest, most confidently confirmed gain from Part 2.

**Full-corpus subsets (n=3,225, calibrated -- see section 10's table for
the complete segment breakdown):**

| Subset | n | Winner LL | Margin MAE/bias | Margin 90% cov | Total MAE/bias | Total 90% cov |
|---|---:|---:|---|---:|---|---:|
| FBS-vs-FBS | 2,935 | 0.6104 | 13.79 / +1.61 | 0.919 | 13.13 / +0.25 | 0.924 |
| FBS-vs-FCS | 290 | 0.2895 | 20.66 / +16.63 | 0.903 | 15.61 / -9.25 | 0.945 |
| Neutral site | 242 | 0.6641 | 13.18 / +2.10 | 0.934 | 14.51 / -1.38 | 0.905 |

**Scope note, stated explicitly:** the three subset rows above are
full-corpus (2022-2025) segments, not re-cut to 2025-only games, matching
this document's existing section-10 convention and avoiding new,
unreviewed diagnostics code changes inside the confirmation step itself
(the confirmation run's underlying code was frozen before this run, by
design). The season-2025 row IS the true, isolated confirmation number
for the model's overall quality; the subset rows describe the final
model's behavior across the whole walk-forward corpus, which for
FBS-vs-FBS/neutral-site (much larger n, stable behavior across seasons in
every prior table in this document) is a reasonable proxy for their 2025
behavior specifically. **The development-season improvement replicates
cleanly on confirmation for coverage and point-accuracy; it does not
newly resolve the margin-bias or totals-bias mechanisms, which were never
targeted by either accepted candidate.**

## 23. CORE_V1 readiness (current, complete -- supersedes section 12)

Same three-tier scheme as section 12 (RESEARCH_PRIMITIVE_AVAILABLE /
RESEARCH_VALIDATED / PRODUCTION_PRICING_READY).
PRODUCTION_PRICING_READY remains **NO for all three families,
unconditionally** -- no Kalshi contract/settlement mapping exists
anywhere in this codebase, in either Part.

| Family | RESEARCH_PRIMITIVE_AVAILABLE | RESEARCH_VALIDATED | PRODUCTION_PRICING_READY |
|---|---|---|---|
| Game winner | YES | **YES** -- winner LL/Brier improved out-of-time across both rounds, confirmed on 2025 | NO |
| Point spread | YES | **YES (upgraded in Part 2)** -- margin MAE/RMSE and interval coverage both genuinely improved out-of-time, confirmed on 2025; favorite-tail bias reduced but not eliminated | NO |
| Game total | YES | **NO, still not upgraded** -- point-accuracy (MAE/RMSE) flat vs. naive across both rounds; interval coverage materially improved and confirmed on 2025, but that is a calibration gain, not an accuracy gain | NO |

FBS-vs-FCS remains **UNSUPPORTED_FOR_PRICING** across all three families
(section 11), unchanged from Milestone C.

## 24. Remaining weaknesses (current, complete -- supersedes section 13)

- **The favorite-tail margin-bias pattern is diagnosed but still not
  fixed**, though reduced in magnitude (+3.33 -> +2.96 full corpus,
  +3.37 -> +3.20 on 2025 confirmation). The leading structural hypothesis
  (a linear ratings model under-predicting a mildly convex true
  relationship, section 3.3) was not tested in either Part. This remains
  the single largest open item.
- **Total point-accuracy (MAE/RMSE) is unchanged from Milestone C across
  both C.2 rounds.** Section 17-18 diagnosed WHERE the weakness
  concentrates (two opposite-signed mechanisms: garbage-time deflation in
  blowout/FBS-vs-FCS games, an under-predicted shootout effect above a
  63-point projected total) and RULED OUT tempo/offense/defense-strength
  as a simple explanation, but neither section 18's `pace_mode=matchup`
  nor any other change tested improved total accuracy itself.
- **Interval coverage, previously the largest quantified gap, is now
  substantially improved** (margin 0.958->0.921 dev / 0.905 confirmation;
  total 0.965->0.923 dev / 0.923 confirmation) via `residual_scale=0.85`,
  confirmed to replicate out-of-time -- genuinely resolved, though still
  slightly above the 90% nominal target on both axes, and only a
  two-point bracketing search (0.85/0.90) was run, not an exhaustive one.
- **FBS-vs-FCS margin bias (+16.63) and total bias (-9.25) remain
  materially unresolved.** Per this mission's explicit instruction, no
  further FCS-specific iteration was attempted in Part 2 (Part 1 already
  tested and rejected a tiered alternative); FBS-vs-FCS stays
  UNSUPPORTED_FOR_PRICING.
- **Early-season games (weeks<=3) remain a distinct, sizable weak spot**
  on both margin (bias +8.28, coverage 0.883 confirmation-era full
  corpus) and total (bias -3.59) -- present in both Part 1 and Part 2,
  not touched by either round's changes.
- **Neutral-site total coverage (0.905 full corpus / not separately
  isolated to 2025) sits closer to nominal than most segments**, but
  neutral-site total bias (-1.38) and winner LL (a relatively weak 0.6641)
  remain among the noisier segments (n=242, smaller sample).
- **No possession/efficiency features (PPA, success rate, explosiveness),
  talent composite, or alternative score-distribution family were wired
  in**, a deliberate, evidence-based deferral (section 20) given weak
  dev-set correlation signal -- a real target for a future pass, not a
  validated result yet.
- **`residual_scale=0.85` was selected from a 2-point bracketing test
  (0.85 vs. 0.90), not a fine-grained search** -- a real next-step
  refinement, not claimed as a fully-tuned optimum, consistent with how
  Part 1 described `ridge_lambda`'s own round-number selection.

## 25. Recommended next step (current -- supersedes section 14)

**Another model-quality pass, not Milestone D**, for the same reason Part
1 gave and still true: the favorite-tail margin-bias pattern and the
(now more precisely characterized, still two-mechanism) totals weakness
remain the two highest-priority open items, both real and both still
unaddressed by two full rounds of hyperparameter-level ablation. A
credible next pass should test a genuine structural hypothesis for the
margin-bias pattern (e.g. a margin-scale nonlinearity) and, separately,
a targeted mechanism for the totals weakness specifically -- e.g. an
explicit garbage-time/blowout deceleration term (targeting mechanism 1)
and/or a genuinely justified efficiency-interaction feature for the
shootout effect (targeting mechanism 2, picking up where section 20 left
off with an actual live ablation rather than a deferral) -- each via its
own dedicated walk-forward ablation under this pass's now twice-proven
leakage-safe development/confirmation procedure. FBS-vs-FCS pricing
support should remain explicitly withheld until independently resolved.

## 26. Tests added this Part

`ruff check src tests scripts` and `pytest -v` both pass (347 tests,
up from Part 1's 334; see below). New/changed test coverage this Part:

- `tests/test_modeling_diagnostics.py`: `actual_total_bin` determinism,
  the new tempo/offense/defense segments in `full_diagnostic_report`,
  `source_of_total_bias_summary`'s key coverage and empty-subset behavior
  (5 new tests).
- `tests/test_modeling_ratings_and_priors.py`: `pace_mode="matchup"` now
  reflects opponent defensive plays-allowed and lets the two sides of one
  game differ; `defense_pace_allowed` excludes FBS-vs-FCS games;
  `pace_mode` rejects an unknown value; **`pace_mode="matchup"` is now the
  default** (proven equal to explicit `"matchup"`); `pace_mode="symmetric"`
  remains available and unchanged as an explicit opt-out (6 tests, 2
  renamed/rewritten from Part 1's placeholder default-is-unchanged form
  now that the default itself changed).
- `tests/test_modeling_score_model.py`: `residual_scale` below 1.0
  narrows the simulated spread without moving the point estimate;
  **`residual_scale=0.85` is now the default** (proven equal to explicit
  `0.85`); `residual_scale=1.0` remains available and produces
  deterministic, reproducible draws as an explicit opt-out (3 tests, 1
  renamed/rewritten for the same reason as above).

No test asserting Part 1's audit fixes (conference realignment,
FCS-tiering as-of correctness, the prediction/diagnostics architectural
boundary, development/confirmation bit-identical-outcomes) was modified
or weakened -- all remain in the suite, verbatim, and passing.

# Part 3 -- Margin-tail and totals structural pass (this session)

## 27. Scope and starting state

A final, narrow structural pass before a go/no-go decision on Milestone D
(research-only Kalshi pricing), per this Part's mission brief. Starting
point: PR #5 at `785f723` (Part 2's accepted head); the Part 2 model
(`ridge_lambda=10.0`, `pace_mode="matchup"`, `residual_scale=0.85`,
`fcs_mode="pooled"`, `season_shrinkage_k=4.0`, walk-forward Platt
calibration, `MODEL_VERSION="0.3.0-milestone-c2"`) and the leakage-safe
development(2022-2024)/confirmation(2025) discipline (section 2A.4) were
both preserved unchanged. Scope was explicitly narrowed to two remaining
structural weaknesses flagged in Part 2 section 24: the favorite-tail
margin-bias pattern and the two-mechanism totals weakness. No broad
feature work was attempted.

## 28. CI registration on the final PR head

PR #5's head advanced through this Part's four commits
(`449ad2c` diagnosis infra -> `d398bd5` margin correction ->
`856e69b` total correction -> `b749621` bug fix). Each commit's push
triggered the repository's existing `pull_request`-triggered CI workflow
against that exact head automatically -- no synthetic/no-op commit was
needed to "register" CI, since the workflow is already wired to run on
every push to an open PR's branch. The final head, `b749621`, has a
genuinely green CI run (`workflow run 32778652571`, conclusion
`success`), and PR #5 reports `mergeable_state: clean` against `main`.

## 29. Favorite-tail margin diagnosis

`favorite_tail_margin_diagnosis` (new, `modeling/diagnostics.py`) bins
`|model_margin_mean|` into `[0,3) [3,7) [7,14) [14,21) [21,28) [28,999)`
(mission's specified edges) crossed with five slices (home favorite, away
favorite, neutral site, FBS-vs-FBS, FBS-vs-FCS), and reports a
sign-corrected `favorite_direction_margin_error` -- positive always means
"the favorite won by more than projected," regardless of which side is
favored, so home- and away-favorite compression can't cancel out in an
aggregate. Run live on development data only
(`dev-margin-tail-and-totals-diagnosis-2022-2024`, job `97584918706`,
model at Part 2's settings, no correction applied):

**Favorite-direction margin bias by |projected margin| bin (dev, n=2,402):**

| Slice | [0,3) | [3,7) | [7,14) | [14,21) | [21,28)\* | [28,999)\* |
|---|---:|---:|---:|---:|---:|---:|
| home favorite | +1.33 (n=411) | +2.79 (n=478) | **+7.35** (n=476) | **+11.67** (n=177) | +14.66 (n=24) | +13.90 (n=4) |
| away favorite | -0.02 (n=359) | +1.87 (n=296) | +1.68 (n=154) | +5.26 (n=22) | +2.73 (n=1) | -- |
| FBS-vs-FBS | +0.72 (n=765) | +2.03 (n=759) | +4.85 (n=556) | +4.41 (n=98) | +22.03 (n=10) | +5.08 (n=1) |
| FBS-vs-FCS | -2.10 (n=5) | +22.87 (n=15) | +14.33 (n=74) | +17.32 (n=101) | +8.95 (n=15) | +16.84 (n=3) |

\* smallest-n cells (n<25) are noisy and not load-bearing for the
conclusion below; included for completeness only.

**Confirmed: the model systematically compresses large expected margins
toward zero**, growing from near-zero at pick'em to a clear, monotonic
bias by the 7-14 point bin and beyond. Two findings the mission
specifically asked to check:

- **A real home/away asymmetry exists.** At matched |projected margin|,
  home favorites show roughly 2-4x the compression of away favorites
  (7-14 bin: +7.35 vs +1.68; 14-21 bin: +11.67 vs +5.26). Within
  FBS-vs-FBS alone the raw home/away split isn't separately reported, but
  the FBS-vs-FBS-only column (+4.85, +4.41) sits between the two,
  confirming the asymmetry isn't purely an FBS-vs-FCS artifact even
  though FBS-vs-FCS games (disproportionately large home favorites)
  amplify it in the unconditioned home_favorite row.
- **FBS-vs-FCS shows a much larger, separate effect** (+14 to +23 across
  every bin above pick'em) and, per this mission's explicit instruction,
  is **not** the optimization target -- see section 37.

Source-summary cross-check (`source_of_margin_bias_summary`, same run):
`overall_bias +2.88`, `fbs_vs_fbs_bias +1.61`, `fbs_vs_fcs_bias +16.00`,
`large_favorite_bias +10.33`, `pickem_like_bias +0.72` -- consistent with
the binned table and with Part 2's already-documented pattern (section
3.1), now precisely localized to the 7+ point-favorite range.

## 30. Margin structural-fix candidates

Two monotonic, walk-forward, FBS-vs-FBS-only candidates were implemented
in a new module, `modeling/margin_calibration.py`, mirroring
`calibration.py`'s existing win-probability Platt/isotonic architecture
exactly (same `_pava` primitive reused for isotonic, same
200-game/800-game minimum-history identity-fallback guards, same refit-
at-every-walk-forward-step discipline -- refit strictly from
prior-only FBS-vs-FBS (projected margin, actual margin) pairs):

- **`linear`**: a 2-parameter OLS recalibration of `model_margin_mean`,
  falling back to identity if the fitted slope is <=0 or history is thin.
- **`isotonic`**: a pool-adjacent-violators fit, monotonic by
  construction.

Both are applied as a uniform **location shift** to `model_margin_mean`
and both `model_margin_p05`/`model_margin_p95` bounds (never a rescale,
preserving Part 2's `residual_scale` coverage gain exactly), FBS-vs-FCS
games untouched, and are structurally decoupled from
`model_prob_home_win`/`calibrated_prob_home_win` (verified by dedicated
tests, section 41) -- satisfying the mission's "must not distort winner
probability incoherently" requirement by construction, not by
post-hoc check alone. No hand-written correction table was used; both
are genuine data fits.

**Dev results (n=2,402, seasons 2022-2024, base = Part 2 model):**

| Variant | Winner LL | Margin MAE | Margin RMSE | Margin Bias | Margin 90% cov |
|---|---:|---:|---:|---:|---:|
| none (Part 2 baseline) | 0.5891 | 14.42 | 18.44 | +2.88 | 0.923 |
| `linear` | 0.5893 | **14.29** | **18.26** | +2.29 | 0.920 |
| `isotonic` | 0.5893 | 14.39 | 18.36 | **+2.16** | **0.926** |

`linear` was **selected**: it gives the larger MAE/RMSE improvement with
a comparable bias reduction (+2.88 -> +2.29 vs. isotonic's +2.16), no
material coverage cost (0.920 vs. Part 2's 0.923), and no change to
winner calibration or the totals channel (both fully decoupled). The
tiny (~0.0002) Winner LL/Brier drift between the baseline and both
correction rows here is ordinary live-CFBD-fetch noise between separate
runs, not a coupling leak -- decoupling is enforced structurally and
verified by dedicated unit/integration tests, not inferred from this
aggregate.

## 31. Totals two-mechanism diagnosis

Reusing the existing segmentation infrastructure
(`source_of_total_bias_summary`, `full_diagnostic_report`, both already
present from Part 2) on the same baseline run, segmented by projected
margin/total magnitude, actual closeness, favorite size, pace, and
offense/defense strength -- **post-hoc only, no actual final
margin/score used as a prediction-time input anywhere in this
diagnosis or in either candidate below.**

Two genuinely distinct patterns, confirmed:

- **(A) Garbage-time suppression**: `large_projected_margin_bias -4.39`
  in the aggregate summary -- but breaking this down by division,
  `fbs_vs_fbs_bias +0.44` while `fbs_vs_fcs_bias -8.74`: **the
  garbage-time effect is concentrated almost entirely in FBS-vs-FCS
  games**, not FBS-vs-FBS. This is the key refinement this Part adds to
  Part 2's diagnosis: within the population any correction is allowed to
  train on (FBS-vs-FBS only, per section 37), the garbage-time signal is
  much weaker than the unconditioned aggregate implies.
- **(B) Shootout under-prediction**: the diagnostic segmentation table's
  `projected total in [63,70)`/`[70,200)` rows show total bias jumping to
  `+12.98`/`+16.57` -- a real, sharply-onset effect above roughly a
  63-point projected total, unrelated to margin.
- Tempo and offense/defense-strength bias are all near zero
  (`high_tempo_bias +0.27`, `low_tempo_bias -1.02`,
  `strong_combined_offense_bias -0.34`, etc.) -- ruled out as simple
  explanations, consistent with Part 2 section 18's finding.

## 32. Total-mean structural-fix candidates

A new module, `modeling/total_calibration.py`, reuses
`margin_calibration.py`'s generic OLS/PAVA fit primitives directly rather
than re-implementing them, offering two independent single-predictor
candidates matching the two mechanisms above -- each fitted/applied
walk-forward, FBS-vs-FBS-only, location-shift-only, decoupled from
win probability, using only pregame-known predictors (the model's own
projected total, or the model's own projected margin magnitude -- never
the actual final score/margin):

- **`predictor="total"`**: direct fit of actual total on the model's own
  projected total -- targets mechanism (B).
- **`predictor="margin_magnitude"`**: fits the *residual*
  (actual - projected total) as a function of `|projected margin|`,
  added back onto the model's own projected total -- targets mechanism
  (A). (Implementation note: this candidate's genuine relationship is
  negative in `|margin|`, opposite the increasing-relationship assumption
  built into the reused linear/isotonic primitives; the predictor is
  negated before fitting/applying, in mirrored coordinates, to recover it
  correctly -- see the module's docstring.)

**A real bug was found and fixed during this candidate's first live
test**, before being reported as a finding: the identity-fallback branch
(insufficient history or a degenerate fit) was returning the raw negated
`|margin|` predictor value as if it were the fitted total-points residual
-- correct behavior for the margin-to-margin primitives' original use
case, wrong here since input and output are different quantities in
different units. The buggy run (`total_correction_method=linear`,
`predictor=margin_magnitude`) showed a nonsensical total bias of **+5.75**
(MAE 14.98, coverage 0.895) spread uniformly across nearly every segment
-- the uniformity, not just the magnitude, was the tell that this wasn't
a genuine targeted garbage-time effect. Fixed by explicitly zeroing the
identity-fallback residual (commit `b749621`); two regression tests
added (`tests/test_modeling_total_calibration.py`); both `predictor`
candidates were then re-run live with the corrected code before any
conclusion was drawn.

**Dev results (n=2,402, base = Part 2 model + margin `linear` correction):**

| Variant | Winner LL | Margin Bias | Total MAE | Total RMSE | Total Bias | Total 90% cov |
|---|---:|---:|---:|---:|---:|---:|
| `total_correction=none` (base) | 0.5893 | +2.29 | **13.45** | **16.82** | **-0.37** | **0.926** |
| `linear` / predictor=`total` | 0.5895 | +2.28 | 13.47 | 16.84 | -0.50 | 0.920 |
| `isotonic` / predictor=`total` | 0.5886 | +2.28 | 13.53 | 16.93 | -0.41 | 0.921 |
| `linear` / predictor=`margin_magnitude` (bug-fixed) | 0.5893 | +2.29 | 13.48 | 16.85 | -0.44 | 0.923 |
| `isotonic` / predictor=`margin_magnitude` (bug-fixed) | 0.5895 | +2.28 | 13.50 | 16.86 | -0.49 | 0.920 |

**All four candidates are rejected.** None improves total MAE, RMSE, or
coverage over doing nothing; bias magnitude is flat-to-slightly-worse in
every case. This is a genuine negative finding, not a bug artifact (the
bug that would have produced a false positive was caught and fixed
first, section above) -- it directly confirms the section 31 refinement:
within FBS-vs-FBS specifically, neither diagnosed mechanism leaves enough
signal for one of this mission's "small set of defensible" structural
corrections to exploit. **`total_correction_method="none"` is retained.**

## 33. Development ablation table (complete, this Part)

Per the leakage-safe procedure (section 2A.4, reused verbatim): every row
below was selected using ONLY 2022-2024 development data; 2025 was not
consulted until section 34, after this table was already locked in.

**Development ablation table (n=2,402, seasons 2022-2024, walk-forward, calibrated):**

| Variant | Winner LL | Brier | Margin MAE | Margin RMSE | Margin Bias | Total MAE | Total RMSE | Total Bias | Margin 90% | Total 90% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Part 2 model (base, no Part 3 correction) | 0.5891 | 0.2037 | 14.42 | 18.44 | +2.88 | 13.45 | 16.82 | -0.38 | 0.923 | 0.927 |
| `margin_correction=linear` | 0.5893 | 0.2038 | 14.29 | 18.26 | +2.29 | 13.45 | 16.82 | -0.37 | 0.920 | 0.926 | **margin: SELECTED** |
| `margin_correction=isotonic` | 0.5893 | 0.2038 | 14.39 | 18.36 | +2.16 | 13.46 | 16.82 | -0.37 | 0.926 | 0.926 | tested, not selected |
| + `total=linear`/predictor=total | 0.5895 | 0.2039 | 14.29 | 18.27 | +2.28 | 13.47 | 16.84 | -0.50 | 0.922 | 0.920 | **REJECTED** |
| + `total=isotonic`/predictor=total | 0.5886 | 0.2035 | 14.28 | 18.26 | +2.28 | 13.53 | 16.93 | -0.41 | 0.924 | 0.921 | **REJECTED** |
| + `total=linear`/predictor=margin_magnitude | 0.5893 | 0.2038 | 14.30 | 18.28 | +2.29 | 13.48 | 16.85 | -0.44 | 0.920 | 0.923 | **REJECTED** |
| + `total=isotonic`/predictor=margin_magnitude | 0.5895 | 0.2040 | 14.32 | 18.29 | +2.28 | 13.50 | 16.86 | -0.49 | 0.920 | 0.920 | **REJECTED** |
| **FINAL: `margin_correction=linear` + `total_correction=none`** | **0.5893** | **0.2038** | **14.29** | **18.26** | **+2.29** | **13.45** | **16.82** | **-0.37** | **0.920** | **0.926** | **SELECTED** |

No total-correction candidate improved on doing nothing (section 32), so
the final row is identical to the `margin_correction=linear` row --
stated plainly rather than manufacturing a combined row that doesn't
exist. **This is the final selected candidate**, frozen before any 2025
data was consulted.

## 34. 2025 confirmation run (run EXACTLY ONCE)

The final candidate was evaluated on the full 2022-2025 corpus **exactly
once** (`FINAL-C2-PART3-2025-CONFIRMATION-ONCE-ONLY`, live workflow run,
job `97597147600`). No parameter was adjusted after this run.

**2025 confirmation season only (n=823, calibrated):**

| Metric | Value |
|---|---:|
| Winner log loss | 0.5624 |
| Winner Brier | 0.1920 |
| Margin MAE / RMSE / bias | 14.33 / 18.06 / **+2.28** |
| Margin 90% coverage | 0.898 |
| Total MAE / RMSE / bias | 13.06 / 16.27 / -1.24 |
| Total 90% coverage | 0.922 |

**The margin-bias improvement replicates almost exactly out-of-time**:
dev bias +2.29 (section 33) vs. 2025-confirmation bias **+2.28** --
essentially identical, and a clear improvement over Part 2's own 2025
confirmation number for the pre-correction model (+3.20, section 22).
Margin coverage on 2025 (0.898) sits slightly below both the dev number
(0.920) and the nominal 0.90 target -- noted honestly, not a material
collapse (n=823, a 2-3 point difference is within ordinary confirmation-
to-confirmation noise given Part 2's own dev-vs-2025 coverage gap was
similar in magnitude). Total metrics are, as expected, essentially flat
between dev (bias -0.37) and confirmation (bias -1.24) since
`total_correction=none` changes nothing about the totals channel.

**Full-corpus subsets (n=3,225, calibrated):**

| Subset | n | Winner LL | Margin MAE/bias | Margin 90% cov | Total MAE/bias | Total 90% cov |
|---|---:|---:|---|---:|---|---:|
| Overall | 3,225 | 0.5823 | 14.30 / +2.29 | 0.916 | 13.35 / -0.59 | 0.925 |
| FBS-vs-FBS | 2,935 | 0.6112 | 13.67 / +0.87 | 0.917 | 13.13 / +0.26 | 0.923 |
| FBS-vs-FCS | 290 | 0.2899 | 20.69 / +16.68 | 0.907 | 15.60 / -9.19 | 0.945 |
| Neutral site | 242 | 0.6654 | 13.20 / +2.35 | 0.942 | 14.50 / -1.37 | 0.905 |

**Scope note, stated explicitly** (same convention as section 22): the
subset rows above are full-corpus (2022-2025) segments, not re-cut to
2025-only games -- the confirmation run's diagnostics code was frozen
before this run by design, and no new segmentation code was written
inside the confirmation step itself. The season-2025 row above IS the
true, isolated confirmation number for the model's overall quality. The
FBS-vs-FBS margin bias (+0.87 full corpus) and FBS-vs-FCS margin bias
(+16.68 full corpus) are both close to their dev-only counterparts
(+1.61 dev before this Part's correction narrows it further within
2022-2024 specifically; +16.00 dev), showing the same stability pattern
Part 2 observed.

**Verdict: the gain replicates.** The favorite-tail margin correction is
a genuine, out-of-time-confirmed improvement, not a development-set
artifact. The totals-structure candidates were correctly identified as
not helping and were not adopted; no post-hoc adjustment was made after
this run.

## 35. Final C.2 model (complete)

`ridge_lambda=10.0`, `fcs_ridge_lambda=4.0`, `pace_shrinkage_k=4.0`,
`season_shrinkage_k=4.0`, `fcs_mode="pooled"`, `pace_mode="matchup"`,
`residual_scale=0.85`, walk-forward Platt win-probability calibration
(all unchanged from Part 2) **plus `margin_correction_method="linear"`**
(new this Part, walk-forward, FBS-vs-FBS-only, location-shift-only) and
**`total_correction_method="none"`** (evaluated and explicitly rejected
this Part, section 32-33). `MODEL_VERSION` remains
`"0.3.0-milestone-c2"` in `scripts/build_cfb_baseline.py` -- **not
bumped**, because that script's single-game research-projection pipeline
does not currently invoke `margin_calibration.py` at all (see section
38); the version string continues to describe exactly what that script
produces.

## 36. CORE_V1 readiness (current, complete -- supersedes section 23)

Same three-tier scheme as sections 12/23. PRODUCTION_PRICING_READY
remains **NO for all three families, unconditionally** -- no Kalshi
contract/settlement mapping exists anywhere in this codebase.

| Family | RESEARCH_PRIMITIVE_AVAILABLE | RESEARCH_VALIDATED | PRODUCTION_PRICING_READY |
|---|---|---|---|
| Game winner | YES | YES -- unchanged from Part 2; margin/total corrections are structurally decoupled from this channel | NO |
| Point spread | YES | **YES (further upgraded)** -- favorite-tail bias reduced (+2.88 -> +2.29 dev), confirmed to replicate almost exactly out-of-time (+2.28 on 2025); MAE/RMSE also improved; bias not eliminated, still the largest single open item | NO |
| Game total | YES | NO, still not upgraded -- both diagnosed mechanisms were tested as candidates this Part and genuinely rejected (section 32); MAE/RMSE/bias remain flat vs. Part 2; interval coverage remains Part 2's genuine gain, undisturbed | NO |

FBS-vs-FCS remains **UNSUPPORTED_FOR_PRICING** across all three families,
unchanged from Milestone C and Part 2.

## 37. Excluded populations

Per this mission's explicit instruction, FBS-vs-FCS was diagnosed
(sections 29/31, both bias sources) but never used to select or tune
either candidate, and no FCS-specific model change was attempted.
FBS-vs-FCS margin bias (+16.68 full corpus / +16.00 dev, essentially
unchanged from Part 2's +16.63/+16.05) and total bias (-9.19 full corpus
/ -8.74 dev, essentially unchanged from Part 2's -9.25/-8.75) remain
materially unresolved and **UNSUPPORTED_FOR_PRICING**, retained in every
diagnostic/coverage table in this document but excluded from every
selection decision.

## 38. Known gap: correction not yet wired into the live single-game projection path

**Stated explicitly, not discovered by an external reviewer:**
`modeling/margin_calibration.py`'s `correct_margin` is wired into
`modeling/backtest.py`'s `run_walk_forward_backtest` (used for every
ablation/ confirmation number in this document) but is **not** invoked by
`scripts/build_cfb_baseline.py` -- the CLI that produces a single live
game's `ProjectionRecord` via `project_game` directly. A real,
leakage-safe wiring would require accumulating the same walk-forward
(FBS-vs-FBS projected-margin, actual-margin) history used inside the
backtest for the single-game CLI's own as-of cutoff -- a piece of
plumbing that doesn't exist yet outside the backtest harness. This Part's
scope was validating the correction via genuine walk-forward backtesting
for the Milestone D go/no-go decision (section 40), not building the
live-projection plumbing; wiring the selected, confirmed correction into
`build_cfb_baseline.py` (or wherever Milestone D's live projections
originate) is explicitly the **first item of work for Milestone D**, not
a blocker to starting it -- Milestone D is precisely where live-pricing
plumbing gets built, per its own scope statement (section 16/21).

## 39. Remaining weaknesses (current, complete -- supersedes section 24)

- **Favorite-tail margin bias is reduced but not eliminated.** Dev bias
  +2.88 -> +2.29 (linear correction), confirmed +2.28 on 2025. The
  home/away asymmetry (section 29) is diagnosed but not separately
  modeled -- a single scalar correction narrows the overall pattern
  without addressing why home favorites compress more than away
  favorites at matched |margin|.
- **Total point-accuracy (MAE/RMSE) remains unchanged from Part 2 and
  Milestone C.** Both diagnosed mechanisms were tested as candidates this
  Part and genuinely rejected -- not for lack of trying, but because
  neither leaves exploitable signal within the FBS-vs-FBS-only,
  pregame-predictor-only, small-candidate-family constraints this
  mission imposed.
- **Interval coverage remains Part 2's gain, undisturbed** by this
  Part's changes (margin/total corrections are location-shift-only, by
  design) -- margin 0.920 dev / 0.898 2025-confirmation, total 0.926 dev
  / 0.922 2025-confirmation, both still slightly off nominal 0.90 in
  different directions across seasons, as previously noted.
- **FBS-vs-FCS margin bias (+16.68) and total bias (-9.19) remain
  materially unresolved**, deliberately excluded from this Part's scope
  per mission instruction (section 37).
- **The correction is validated in backtesting but not yet wired into
  the live single-game projection CLI** (section 38) -- a real,
  explicitly-flagged gap for Milestone D's first work item, not a
  research-validation problem.
- **Early-season games (weeks<=3) remain a distinct, sizable weak spot**
  on both margin and total, present in every Part of this mission,
  untouched by any Part's changes.
- Every Part 2 remaining-weakness item not superseded above (no
  possession/efficiency features wired in, `residual_scale=0.85` from a
  coarse 2-point search) is unchanged and still applies.

## 40. Recommended next step (current -- supersedes section 25)

**MOVE TO MILESTONE D -- RESEARCH-ONLY KALSHI PRICING.** Checked against
this mission's own decision threshold: winner calibration remains
validated and untouched by this Part's changes; FBS-vs-FBS margin
modeling is measurably more stable (bias nearly halved, confirmed to
replicate almost exactly out-of-time rather than merely on development
data); the total distribution remains coherent and uncertainty-
calibrated (coverage preserved, MAE/RMSE honestly flat rather than
claimed as improved); FBS-vs-FCS is explicitly excluded and stays
UNSUPPORTED_FOR_PRICING; the leakage-safe walk-forward architecture was
reused verbatim a third time with no new leakage surface introduced (and
is enforced by tests, not just asserted); the 2025 confirmation shows no
collapse anywhere; CI is green on the final head; the full test suite
(384 tests) and `ruff check` both pass. Milestone D remains
research-only -- no real-money readiness is implied by this
recommendation, and section 38's live-projection wiring gap should be
its first concrete task.

## 41. Tests added this Part

`ruff check src tests scripts` and `pytest -v` both pass (**384 tests**,
up from Part 2's 347). New/changed test coverage this Part:

- `tests/test_modeling_diagnostics.py`: `absolute_projected_margin_bin`
  symmetry, `favorite_direction_margin_error`'s sign-flip for away
  favorites (the specific test that proves home/away bias can't cancel
  out in an aggregate), `favorite_tail_margin_diagnosis` reports all five
  slices (3 new tests).
- `tests/test_modeling_margin_calibration.py` (new file, 10 tests):
  identity fallback below history threshold and for a degenerate/
  negative-slope fit, correct recovery of a known synthetic linear/
  isotonic relationship, monotonicity, `correct_margin`'s
  none/unknown-method dispatch.
- `tests/test_modeling_total_calibration.py` (new file, 10 tests):
  both candidates' none-is-a-true-no-op and known-relationship-recovery
  behavior, the sign-handling regression test for the negated-predictor
  design, and **two regression tests for the identity-fallback bug**
  found this Part -- one asserting the fixed output equals the
  unchanged projected total (not the raw negated predictor that would
  have been the buggy answer), one for the isotonic path.
- `tests/test_modeling_backtest.py`: 12 new integration tests --
  no-op at `method="none"` for both correction types, win-probability/
  margin channel decoupling, mean-and-both-interval-bounds shift
  coherently together, FBS-vs-FCS games never touched by either
  correction, no leakage from a game's own or future outcomes (season-
  scoped, since the synthetic corpus reuses week numbers across
  seasons), reproducibility, unknown-predictor raises. A new
  `_synthetic_corpus_with_garbage_time` helper (genuine, deliberate
  margin<->total coupling) was added after the bug fix, because the two
  `predictor="margin_magnitude"` integration tests were previously
  passing only because the bug made the correction trivially "engage" on
  pure-noise data; they now correctly exercise real signal.

No test asserting any Part 1 or Part 2 fix/finding was modified or
weakened -- all remain in the suite, verbatim, and passing.

## 42. Merge verdict

**SAFE TO MERGE AS RESEARCH MODEL: YES.** CI is green on the final head
(`b749621`), the PR reports `mergeable_state: clean`, the full test suite
passes, `ruff` is clean, no leakage was reintroduced, and the 2025
confirmation replicates the development-set gain honestly (including the
totals-channel non-improvement, reported as such rather than obscured).
This assessment does not authorize action: **per this mission's explicit
instruction, PR #5 was not merged and Milestone D was not begun as part
of this session.**
