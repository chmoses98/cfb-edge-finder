# Milestone C — Baseline CFB Projection Engine

**Status: a genuine, leakage-safe, chronologically-backtested baseline
model with real quantitative results below (see "Backtest results" --
filled in from a live run, not fixture data). This is the first
Milestone that produces real football probabilities; it is explicitly
NOT a betting/recommendation engine (see "Scope boundary" at the end).**

**Hardening pass (this revision):** a targeted follow-up mission audited
and addressed three issues the original backtest surfaced: winner-
probability miscalibration (fixed -- leakage-safe Platt scaling, see
section 8.1), in-sample residual estimation (fixed -- a genuine expanding
walk-forward residual pool, see section 8.2), and the FBS-vs-FCS margin
bias (investigated, partially addressed, NOT fully resolved -- see
section 11's FBS-vs-FCS subsection and section 15). All numbers below are
from this hardened code, re-validated against two fresh genuine live
CFBD runs.

## 1. Data audit

Every CFBD endpoint below was investigated via genuine primary-source
documentation (github.com/CFBD/cfbd-python -- CFBD's own domains are
blocked from this dev environment, same constraint documented throughout
Milestones B/B.5). `/games` was additionally live-verified twice in
Milestone B; the others are live-verified for the first time in this
milestone's backtest run (see "Live validation" below).

| Endpoint | Seasons | Key fields | Known before kickoff? | Leakage risk | Used in V1? |
|---|---|---|---|---|---|
| `/games` | All | scores, classification, neutral site, week/season type | Scores: NO (postgame). Schedule metadata: YES | Scores must only be used to predict *earlier* games, never the game itself | YES -- the corpus's target variable and classification/neutral-site features |
| `/stats/game/advanced` | Seasons with advanced-stats coverage (varies) | `plays`, `ppa`, `success_rate`, `explosiveness`, situational splits | NO -- postgame, same as scores | Same as scores: only usable for predicting later games | YES, but only `plays` (pace). `ppa`/`success_rate`/etc. genuinely available but NOT wired into V1 -- see "Scope control" below |
| `/stats/season` | All | Season-aggregate team stats | NO if the season is in progress (aggregate includes future-within-season games) | HIGH if naively used mid-season -- a season total silently includes games after the prediction point | NOT used in V1 (the `/stats/game/advanced` per-game rows are used instead, precisely because they can be filtered to strictly-prior games; a season aggregate cannot be) |
| `/player/returning` | All | Team-level returning production (`percent_passing_ppa`, `passing_usage`, etc.) | YES -- published before the season starts from last season's final data | LOW -- but must not be confused with a same-season aggregate | YES, as the QB-continuity proxy (see section 7) |
| `/talent` | All | Team recruiting-talent composite | YES -- offseason recruiting cycle | LOW | Documented as available and leakage-safe, NOT wired into V1 -- see "Scope control" |
| `/ratings/elo` | All | Team Elo rating | Ambiguous from docs alone whether a given week's value is pre- or post- that week's games -- NOT independently confirmed | HIGH until confirmed -- an Elo value could easily be postgame for its own week | NOT used -- exactly because this ambiguity was found and not resolved with confidence (see "What must improve" below) |
| `/ratings/sp`, `/ratings/srs`, `/ratings/fpi` | Varies | Composite team-strength ratings | Typically settle mid/post-season | Same ambiguity as Elo -- not independently confirmed as leakage-safe at arbitrary points in a season | NOT used |
| `/lines` | Varies | Historical closing betting lines by provider | Closing lines by definition finalize near kickoff | N/A -- **evaluation-only, never a model input** (see section 15/13 note below) | Documented; not fetched by any script in this milestone (see "What must improve") |
| `/roster` | All | Player roster listings | Partially -- rosters are set before the season, but injuries/depth-chart changes happen in-season | Not used this milestone; a future QB-identity signal would need this + depth-chart data this project doesn't have a trustworthy source for | NOT used |
| `/coaches` (not separately audited) | -- | Coaching tenure/history | -- | -- | NOT used -- coaching continuity is a real, documented future signal (see "What must improve"), not built in V1 |

**Design decision from this audit: prefer a small, trustworthy feature
set.** V1 uses exactly three genuinely leakage-safe signal families:
final scores + classification + neutral-site (from `/games`), plays
(from `/stats/game/advanced`, pace only -- not PPA/success rate/etc.),
and returning-production percentages (from `/player/returning`, QB-
continuity proxy only). Talent, Elo/SP+/FPI/SRS, weather, and detailed
play-by-play were all investigated and are documented above as
available-but-not-used, per the mission's explicit "prefer a smaller set
of trustworthy pregame features over a huge uncontrolled feature set"
instruction -- not because they lack value, but because wiring in
several more feature families without validating each one's marginal
contribution would violate the "stop condition" (section 22 of the
mission spec): don't pile complexity onto an unvalidated baseline.

## 2. The leakage contract

Enforced in code, not just documentation -- see
`src/cfb_edge_finder/modeling/leakage.py`.

A prediction "as of" `AsOf(season, week)` may only use `TeamGameLine` rows
whose own `(season, week)` is **strictly less than** the prediction's
`AsOf`. This is checked, not assumed: `ratings.fit_fbs_efficiency_ratings`,
`naive_benchmark.fit_naive_benchmark`, and `score_model.build_residual_pool`
all call `leakage.assert_strictly_before()` on every row they consume and
raise `LeakageError` rather than silently filtering. AsOf is week-granular
(all games within one week of one season are treated as simultaneous --
neither can inform the other's prediction, and refitting per-game instead
of per-week would be far more expensive for no real accuracy gain at this
stage). Postseason phases get synthetic week numbers strictly above the
regular-season ceiling, in bracket order (conference championship < bowl <
CFP first round < ... < CFP national championship) via
`leakage.postseason_week_rank`, reusing Milestone B's own tested
`SeasonType`/`CFPRound` vocabulary rather than inventing a parallel one.

**What is explicitly forbidden, and where each is prevented:**
- Game X's own result/postgame stats predicting game X: impossible by
  construction -- `TeamGameLine.as_of` for game X's own row equals game
  X's own week, so `assert_strictly_before` against an `AsOf` at that
  same week always raises.
- Later games informing earlier predictions: prevented by the walk-forward
  backtest (`backtest.run_walk_forward_backtest`) refitting from scratch
  at every week using only strictly-prior history -- never a random
  train/test split.
- Season-aggregate fields that silently include future-within-season
  games: avoided by construction -- V1 never fetches `/stats/season`
  (see data-audit table above) precisely because that endpoint cannot be
  safely truncated to "before week W" the way per-game rows can.
- Postseason/ranking information unavailable at prediction time: not used
  at all in V1 (no AP-poll or CFP-seeding feature exists in the model).

**A documented, real exception -- not a leak:** `/player/returning`
percentages are computed from the PRIOR season's completed data and
published before the current season starts. Using them for week-1
predictions of the current season is correct, not leakage, precisely
because the source data itself predates the season being predicted.

## 3. Historical corpus

Built by `modeling/corpus.build_team_game_lines` from raw CFBD `/games` +
`/stats/game/advanced` responses -- two `TeamGameLine` rows per completed
game (home perspective + away perspective; see that module's docstring for
why two rows, not one). Explicitly distinguished at the row level:
`team_classification`/`opponent_classification` (fbs/fcs), `is_neutral_site`,
`is_postseason` (+ the specific postseason phase via week rank),
`is_home`. FBS-vs-FCS rows are retained (an FBS team's real schedule
includes them) but are NOT pooled into the FBS-vs-FBS rating fit or
residual pool -- see section 4's "why FCS opponents share one pooled
rating" and section 6's segmentation. No enormous raw API payload is
committed to git: the only committed corpus artifact is a small,
synthetic, clearly-labeled fixture
(`src/cfb_edge_finder/data/fixtures/cfb_backtest_fixture_corpus.json`)
used purely to exercise the CLI/pipeline deterministically without live
access -- the genuine multi-season corpus used for the real backtest
below is fetched fresh on every live run, never persisted to the repo, per
the "don't commit large raw payloads" instruction.

## 4. Team-strength construction

**Method: ridge-regularized simultaneous offense/defense least squares on
points-PER-PLAY (not raw points).** See
`src/cfb_edge_finder/modeling/ratings.py`'s module docstring for the full
derivation; summary:

```
points_per_play(team, game) ≈ mu + offense[team] - defense[opponent] + hfa * home_indicator
```

fit via closed-form ridge regression (`(XᵀX + λI)⁻¹Xᵀy`, numpy) over every
leakage-safe FBS-perspective row. **Why points-per-play, not raw points:**
fitting on raw points would let a fast, mediocre team look "efficient"
purely because it ran more plays than its opponents -- pace and efficiency
would be conflated into one number, which the mission spec explicitly
warns against (section 7). Points-per-play isolates efficiency; pace is
estimated completely separately (section 5) and the two are recombined
multiplicatively at projection time: `expected_points = efficiency *
expected_plays`.

**Why ridge regularization, and why `DEFAULT_RIDGE_LAMBDA = 25.0`
specifically:** ordinary least squares would let a team with 1-2 games of
evidence get a wildly unstable rating. Ridge shrinks every team's
offense/defense rating toward the league average (0.0) in inverse
proportion to how much evidence exists for it -- exactly the "early-season
uncertainty" behavior the mission asks for, applied at the rating-fit
level (a SEPARATE season-to-season carryover shrinkage, described in
section 7, sits on top of this). The value 25.0 is a documented,
provisional, round-number choice -- large enough that 1-2 games sit close
to league-average, small enough that a full season of evidence dominates
it. It has NOT been cross-validated against held-out data; doing so
properly is this document's top "what must improve" item.

**Why FCS opponents are not individually rated:** mission section 4
explicitly warns against blindly mixing FBS-vs-FCS into the main
calibration population. Rather than giving each of the 100+ FCS programs
that occasionally appear on an FBS schedule their own (mostly
single-observation, far too noisy) parameter, every FCS opponent shares
ONE pooled `fcs_offense`/`fcs_defense` parameter pair
(`ratings.FCS_PSEUDO_TEAM_ID`). An FBS team's performance against an FCS
opponent still informs that FBS team's OWN rating (real signal); nothing
FCS-team-specific is claimed. See `tests/test_modeling_ratings_and_priors.py::test_fcs_opponents_share_one_pooled_pseudo_rating_not_individual_ones`.

**Hardening-pass correction to the pooled FCS parameter's shrinkage:**
the live backtest found this pooled parameter was being ridge-penalized
at the SAME strength as an individual FBS team with only a game or two of
evidence (`DEFAULT_RIDGE_LAMBDA`), even though it is fit from every
FBS-vs-FCS game leaguewide (far more pooled evidence than any single
team). `DEFAULT_FCS_RIDGE_LAMBDA` (a separate, smaller constant) corrects
this specific bug. **This did NOT resolve the FBS-vs-FCS margin bias**
(see section 11) -- isolating its effect from a separate, broader
out-of-sample residual-estimation fix showed the FCS segment's bias
RELATIVE TO the FBS-vs-FBS baseline was essentially unchanged, meaning
population heterogeneity within the pooled parameter (not shrinkage
strength) is the likely true driver, still unaddressed. Kept because it
is independently correct statistically, not because it fixed the
headline problem it was motivated by -- see section 15 for the honest
next step.

## 5. Pace

Each team's expected plays for an upcoming game is
`(trailing_pace[home] + trailing_pace[away]) / 2` -- the standard "two
opponents meet in the middle" heuristic. `trailing_pace[team]` is that
team's own average plays/game from strictly-prior weeks, shrunk toward the
league-average pace by the same `games_played / (games_played + k)` form
used everywhere else in this package (`DEFAULT_PACE_SHRINKAGE_K = 4.0`).
Pace is estimated entirely independently of the offense/defense
efficiency fit (see section 4) specifically so it is never double-counted
inside what should be a pure efficiency signal.

## 6. Home-field advantage

`hfa` is a single scalar fit AS PART OF the same ridge regression (section
4), not hardcoded from a generic sportsbook number -- it emerges from the
actual home/away scoring gap in the training data, controlling for team
strength. Neutral-site games get `home_indicator = 0.0` for BOTH teams
(never `+1`/`-1`), so a neutral-site game structurally cannot receive
ordinary home-field advantage -- enforced by `_home_indicator()` and
verified directly by
`tests/test_modeling_ratings_and_priors.py::test_neutral_site_games_excluded_from_home_indicator`
and `tests/test_modeling_score_model.py::test_neutral_site_projection_has_no_home_field_edge`.
**Not investigated in V1** (a real scope-control decision, not an
oversight): whether HFA differs materially by conference, travel distance,
altitude, specific venue, or rivalry. A single league-wide HFA is the
mission's own "at minimum" bar (section 8); splitting it further needs
more evidence of a real, sizeable effect than this milestone's time budget
allowed for -- see "what must improve."

## 7. Early-season priors and QB continuity

**Season-to-season carryover** (`modeling/priors.py`): a team's EFFECTIVE
offense/defense rating going into a projection blends its CURRENT-season
fitted rating with its PRIOR-season ending rating, weighted by
`games_played_this_season / (games_played_this_season + k)`
(`DEFAULT_SEASON_SHRINKAGE_K = 4.0`) -- week 1 (0 games played) is pure
prior-season carryover; by roughly game 4-5 the blend is 50/50; a full
season of evidence dominates. A team with NO prior-season data in the
corpus (new to FBS, e.g. one of Milestone B's four FCS-to-FBS transitional
programs) gets the league-average (0.0) prior, **not a fabricated
below-average "transition penalty"** -- inventing a number for that
penalty's size without real evidence would be exactly the kind of
unverified-but-authoritative-looking output this project refuses to
produce (same principle as `kalshi/executable_price.py`'s fee-rate
discipline). This is a real, documented limitation: such teams'
early-season projections carry inflated uncertainty (see below) but not a
point-estimate penalty this project cannot justify.

**QB continuity** (`modeling/qb_continuity.py`): CFBD has no field that
directly says "the starting QB is the same person as last year." V1 uses
`/player/returning`'s `percent_passing_ppa` as a documented PROXY for
passing-game continuity, classified into
`RETURNING_STARTER`/`MIXED_OR_UNCERTAIN`/`NEW_STARTER`/`UNKNOWN` and
applied ONLY as an uncertainty multiplier on that team's residual draw
(1.00/1.10/1.20/1.20 respectively) -- **never a point-estimate shift**,
per the mission's explicit "conservative V1" instruction. The
architecture (a per-side scale factor on the simulated residual) is ready
to carry a real point-estimate adjustment once a trustworthy, validated
QB-quality signal exists; none does yet, so V1 does not assert one.

## 8. Scoring distribution

**Method: bootstrap simulation from paired historical residuals, moment-
matched into a `GameDistribution` for compatibility with the existing
Milestone A pricer.** See `modeling/score_model.py`'s module docstring for
the full method and its one real, explicitly documented limitation
(residuals are estimated in-sample from the current rating snapshot, not
via a second walk-forward pass -- see "what must improve"). Summary:

1. For every FBS-vs-FBS training game, compute the PAIRED residual
   `(actual_home - expected_home, actual_away - expected_away)`.
2. For a new projection, bootstrap-sample thousands of these paired
   residuals (with replacement), scale each side by that team's QB/early-
   season uncertainty multiplier, add to the target game's own expected
   home/away points, and round to the nearest non-negative integer.
3. Every probability (`P(home wins)`, `P(margin > x)` for arbitrary real
   `x` including exact integers, `P(total > y)`) is then an exact
   empirical frequency over the simulated sample -- discreteness and
   home/away correlation both fall out naturally, with no continuity-
   correction guesswork.
4. The same sample is ALSO moment-matched (mean/sd/correlation) into a
   real `GameDistribution` (`schemas/projection.py`), so
   `projections.distribution.price_market()` -- already built and tested
   in Milestone A -- works unchanged against genuine Milestone C output
   for the first time.

This satisfies the mission's explicit requirement that discreteness and
arbitrary thresholds be representable, without asserting a Normal (or any
other single parametric) shape as ground truth.

### 8.1 Calibration (hardening pass)

The original backtest showed a real winner-probability calibration gap in
the 0.6-0.9 predicted range (observed win rates ran higher than
predicted -- underconfidence). `modeling/calibration.py` adds a second,
much simpler recalibration model on top of the raw simulated probability:
Platt (2-parameter logistic regression on `logit(raw_p)`, via
Newton-Raphson) or isotonic regression (pool-adjacent-violators),
selectable, with a documented minimum-history fallback to identity
(`MIN_CALIBRATION_HISTORY = 200`, `MIN_ISOTONIC_HISTORY = 800`). Fit
**only** on `(raw_probability, actual_outcome)` pairs from strictly-prior
walk-forward weeks -- `backtest.run_walk_forward_backtest` refits the
calibration model at every step from the outcomes already accumulated,
never the holdout being scored (see section 9's leakage discipline,
applied identically here). Both methods are monotonic by construction
(isotonic directly; Platt falls back to identity rather than ever return
an inverted mapping if its fitted slope is non-positive). **Platt is the
adopted default** -- see section 11's live comparison against isotonic
for why.

### 8.2 Expanding residual pool (hardening pass)

The original V1 computed each training game's residual using the SAME
ratings snapshot that game's own row helped fit -- an in-sample estimate
that systematically understates true predictive residual variance. This
is now a genuine EXPANDING WALK-FORWARD residual pool:
`backtest.run_walk_forward_backtest` maintains a running pool that, at
each step, is (a) used to simulate that week's projections, using only
residuals accumulated from strictly-prior weeks, then (b) extended with
that week's own games' residuals -- computed against the ratings that
were fit strictly before those games, i.e. a genuine out-of-sample
prediction error, never a fitted residual on the same rows used to
estimate the ratings that scored it. `score_model.build_expanding_residual_pool`
is the same algorithm as a standalone, single-shot call (used by
`scripts/build_cfb_baseline.py`, which has no backtest loop already
computing this incrementally). See section 11 for the measured effect --
this fix WIDENED both raw calibration underconfidence and margin bias
(see section 11), a real, quantified tradeoff of correcting the
in-sample estimate, not a free improvement.

## 9. Backtest methodology

**Genuine chronological walk-forward, never a random split** -- see
`modeling/backtest.run_walk_forward_backtest`'s module docstring. For
every `(season, week)` with completed games, ratings and the naive
benchmark are refit from scratch using ONLY strictly-prior weeks (leakage-
checked, not assumed), a projection is generated for every game in that
week using those exact ratings, and the walk advances. Both the naive
benchmark and the full model are evaluated on the IDENTICAL held-out
schedule of games, so the comparison in section 11 is genuinely
apples-to-apples.

**Metrics computed per game and aggregated:**
- Winner: log loss, Brier score, calibration bins (predicted probability
  vs. observed win rate vs. sample count, 10 equal-width bins).
- Margin: MAE, RMSE, signed bias (mean(actual - predicted)), 90% interval
  coverage (fraction of actual margins inside the simulated sample's
  [5th, 95th] percentile band -- for the naive benchmark, inside
  `naive_margin ± 1.645 * NAIVE_MARGIN_SD`).
- Total: the same four statistics, on total points.

**Segmentation:** by season, by early-season (week ≤ 3) vs. later season,
by neutral-site vs. not, and by FBS-vs-FBS vs. FBS-vs-FCS -- see section
11 for the actual numbers.

## 10. Live validation

<!-- FILLED IN FROM A GENUINE LIVE RUN -- see the run ID/URL and exact
     numbers below. This dev environment's own network egress to CFBD
     stays blocked (same constraint as Milestones B/B.5); this run used
     the same GitHub Actions workflow_dispatch mechanism established in
     Milestone B (.github/workflows/backtest-cfb-baseline-live.yml). -->

Two live runs via `.github/workflows/backtest-cfb-baseline-live.yml`
(`workflow_dispatch`, seasons `2022 2023 2024 2025`, 6000 Monte Carlo
simulations per game). `CFBD_API_KEY` was masked (`***`) throughout both
runs' logs; never printed.

**Run 1** (`https://github.com/chmoses98/cfb-edge-finder/actions/runs/32619609422`,
2026-08-23T05:08:44Z) surfaced a genuine bug and is reported here for
transparency rather than discarded: CFBD's `/games?division=fbs` query
parameter does **not** fully exclude non-FBS-involving games -- the same
gap Milestone B independently found (a Division II game slipping through
the identical filter). `modeling/corpus.py` had no guard against this, so
FCS-vs-FCS, D-II, D-III, and NAIA games were being built into the corpus
too, and every one of them landed in the walk-forward backtest's "not
FBS-vs-FBS" bucket -- silently inflating the reported "FBS-vs-FCS"
segment to 10,787 games against only 2,935 genuine FBS-vs-FBS games, the
inverse of the real ratio. Fixed in commit `36d268f`
(`corpus._is_fbs_involved`, the exact same "at least one side must be
FBS" policy Milestone B's `ingest_schedule.py` already applies) and
re-run.

**Run 2, the corrected and authoritative run**
(`https://github.com/chmoses98/cfb-edge-finder/actions/runs/32619827949`,
2026-08-23T05:13:57Z, head `36d268f`) -- all results below are from this
run:

- 7,210 `TeamGameLine` rows built across the 4 seasons.
- 11,454 raw CFBD games excluded as having no FBS side on either team
  (the corrected filter working as intended -- CFBD's `/games` response
  includes a very large number of lower-division games even under
  `division=fbs`).
- 3,225 games actually predicted by the walk-forward backtest (week ≤ 3
  of the very first season in the corpus, 2022, has no leakage-safe
  history and is correctly excluded by `min_week_for_first_prediction`).
- Team-name-resolution skips (the same fail-loud-on-ambiguity /
  lenient-for-non-FBS policy as Milestone B): a small number of bare
  `"Miami"` ambiguities and clearly non-FBS/non-FCS program names
  (e.g. "Florida Memorial University", "Keiser University") correctly
  excluded, not silently guessed.

### 10.1 Hardening-pass live re-validation

Two further live runs, same workflow, same seasons/corpus, against the
hardened code (commit `4167e7a`), `CFBD_API_KEY` masked throughout:

- **Run 3** (`https://github.com/chmoses98/cfb-edge-finder/actions/runs/32659507853`,
  `--calibration-method platt`) -- the authoritative source for every
  "calibrated" number in section 11 below.
- **Run 4** (`https://github.com/chmoses98/cfb-edge-finder/actions/runs/32659644617`,
  `--calibration-method isotonic`) -- run purely for the calibration
  method comparison in section 11; not the adopted configuration.

Both runs used the identical genuine CFBD corpus fetch as before (7,210
`TeamGameLine` rows, 3,225 predicted games, 11,454 non-FBS-involved games
correctly excluded) -- the test population was NOT changed to produce
these results, only the model code.

## 11. Backtest results

**All numbers below are from the hardening-pass live runs (32659507853
for calibrated=platt, 32659644617 for calibrated=isotonic), never rounded
differently or recomputed.** The pre-hardening numbers (from the original
live run `32619827949`) are kept alongside every table, labeled
"pre-hardening", so every change is a visible before/after -- nothing is
silently replaced.

### Overall (n=3,225 predicted games)

| | Naive (unchanged) | Model, pre-hardening | Model, raw (hardened) | Model, calibrated (platt, adopted) | Model, calibrated (isotonic, comparison) |
|---|---|---|---|---|---|
| Winner log loss | 0.6141 | 0.5972 | 0.6163 | **0.5924** | 0.6179 |
| Winner Brier | 0.2133 | 0.2054 | 0.2134 | **0.2054** | 0.2057 |
| Margin MAE / RMSE / bias | 15.39 / 19.59 / −0.86 | 14.71 / 18.74 / +0.91 | 14.92 / 19.06 / **+3.27** | (same as raw) | (same as raw) |
| Margin 90% interval coverage | 0.841 | 0.941 | 0.960 | (same as raw) | (same as raw) |
| Total MAE / RMSE / bias | 13.17 / 16.41 / −1.43 | 13.36 / 16.66 / −1.31 | 13.37 / 16.70 / −0.54 | (same as raw) | (same as raw) |
| Total 90% interval coverage | 0.914 | 0.945 | 0.966 | (same as raw) | (same as raw) |

**Read this table honestly, not selectively:**
- **Calibration (platt) works and is the headline win**: overall winner
  log loss improves on BOTH the pre-hardening model (0.5972 → 0.5924) and
  naive (0.6141). Calibration never touches margin/total (see section
  8.1) -- those columns are identical to "raw" by construction.
- **Isotonic is worse than platt and worse than naive** on overall log
  loss (0.6179 > 0.6141) -- this is exactly the genuine out-of-time
  comparison mission section 3 asked for, and it is why platt, not
  isotonic, was adopted: NOT because platt looked better in one bin, but
  because isotonic's extra flexibility overfits the accumulated
  walk-forward history (see the FBS-vs-FBS and Neutral-site segments
  below, where isotonic actively makes log loss WORSE than the raw,
  uncalibrated number). Both are reported here rather than only the
  winner.
- **The expanding residual pool (section 8.2) made the RAW margin bias
  materially worse in absolute terms**: +0.91 → +3.27 overall. This is
  reported plainly, not hidden, per mission section 5's explicit
  instruction ("if the corrected uncertainty model degrades materially,
  report it rather than reverting silently"). The mechanism, confirmed by
  inspection: the original in-sample residuals summed to ~0 by
  construction (an artifact of fitting and scoring on the same rows);
  genuine out-of-sample residuals do not have that property, and reveal a
  real, previously-hidden tendency of the ridge-shrunk ratings to
  under-predict margins on new data. This is a genuine, more honest
  number, not a regression introduced by a bug -- but it is a real cost,
  and calibration does NOT fix it (calibration only touches win
  probability). See section 15 for why this is now flagged as a priority
  item.
- **Interval coverage moved further from the nominal 90% target** (margin
  0.941 → 0.960, total 0.945 → 0.966) -- the wider, more honest
  out-of-sample residual pool is now measurably over-covering. Reported
  as a real, quantified side effect, not claimed as an improvement just
  because "more coverage" sounds good.

Winner calibration bins, overall, raw (hardened) vs. platt-calibrated:

| Bin | Raw predicted | Raw observed | Raw n | Calibrated predicted | Calibrated observed | Calibrated n |
|---|---|---|---|---|---|---|
| [0.0,0.1) | -- | -- | -- | 0.061 | 0.000 | 4 |
| [0.1,0.2) | 0.191 | 0.000 | 1 | 0.160 | 0.156 | 45 |
| [0.2,0.3) | 0.274 | 0.091 | 22 | 0.252 | 0.223 | 103 |
| [0.3,0.4) | 0.366 | 0.267 | 236 | 0.355 | 0.395 | 248 |
| [0.4,0.5) | 0.454 | 0.463 | 886 | 0.450 | 0.501 | 383 |
| [0.5,0.6) | 0.548 | 0.620 | 1168 | 0.557 | 0.504 | 607 |
| [0.6,0.7) | 0.648 | 0.798 | 640 | 0.651 | 0.612 | 786 |
| [0.7,0.8) | 0.741 | 0.959 | 242 | 0.749 | 0.756 | 434 |
| [0.8,0.9) | 0.824 | 0.967 | 30 | 0.848 | 0.820 | 388 |
| [0.9,1.0) | -- | -- | -- | 0.936 | 0.960 | 227 |

**The raw calibration gap got WORSE after the residual-pool fix** (e.g.
[0.7,0.8): predicted 0.741 vs. observed 0.959 -- more underconfident than
the pre-hardening model's 0.743/0.886), for the same reason as the margin
bias above: wider, genuinely out-of-sample uncertainty pulls the raw
simulated win probability toward 0.5 more than the (artificially
narrower) in-sample estimate did. Platt calibration recovers this
cleanly, and then some: every calibrated bin above sits close to its
diagonal, and overall log loss beats the pre-hardening number. This is
the intended, evidenced mechanism -- not a coincidence.

### Segment: FBS-vs-FBS (n=2,935)

| | Pre-hardening | Raw (hardened) | Calibrated (platt) | Calibrated (isotonic) |
|---|---|---|---|---|
| Log loss | 0.6258 | 0.6358 | **0.6218** | 0.6466 |
| Brier | 0.2180 | 0.2224 | **0.2175** | 0.2173 |
| Margin MAE/RMSE/bias | 14.20/18.14/−0.46 | 14.30/18.29/+1.89 | (same) | (same) |
| Margin 90% coverage | 0.947 | 0.959 | (same) | (same) |
| Total MAE/RMSE/bias | 13.16/16.40/−0.62 | 13.16/16.45/+0.31 | (same) | (same) |
| Total 90% coverage | 0.950 | 0.963 | (same) | (same) |

Platt beats the pre-hardening model on this, the CORE_V1-relevant
population. **Isotonic makes log loss WORSE than even the raw,
uncalibrated number here (0.6466 > 0.6358)** -- a genuine overfitting
symptom on real data, reported as direct evidence for the platt-over-
isotonic decision, not asserted from theory alone.

### Segment: FBS-vs-FCS (n=290)

| | Pre-hardening | Raw (hardened) | Calibrated (platt) | Calibrated (isotonic) |
|---|---|---|---|---|
| Log loss | 0.3077 | 0.4194 | 0.2949 | 0.3267 |
| Brier | 0.0781 | 0.1218 | 0.0836 | 0.0886 |
| Margin MAE/RMSE/bias | 19.84/23.98/**+14.84** | 21.28/25.60/**+17.24** | (same) | (same) |
| Margin 90% coverage | 0.883 | 0.966 | (same) | (same) |
| Total MAE/RMSE/bias | 15.37/19.03/−8.29 | 15.51/19.07/−9.10 | (same) | (same) |
| Total 90% coverage | 0.900 | 0.990 | (same) | (same) |

**The margin bias got WORSE (+14.84 → +17.24), not better.**
`DEFAULT_FCS_RIDGE_LAMBDA` (see section 4's hardening note) was a real,
evidence-motivated statistical fix (the pooled FCS parameter WAS being
over-shrunk relative to its true pooled evidence volume) -- but isolating
its effect from the global out-of-sample-residual shift above shows it
did NOT close the FCS-specific gap: comparing the FCS segment's EXCESS
bias over the FBS-vs-FBS baseline (the natural control group, since both
moved by roughly the same amount from the residual-pool fix), pre-
hardening excess was 14.84 − (−0.46) = 15.30; post-hardening excess is
17.24 − 1.89 = 15.35 -- essentially UNCHANGED. **Conclusion, stated
plainly rather than claimed as a fix: the ridge-shrinkage-strength
hypothesis was the wrong primary explanation.** The true driver is very
likely the pooled parameter's population heterogeneity (FCS programs
that get scheduled by FBS teams vary enormously in real strength, and one
shared number cannot capture that spread), which shrinkage strength alone
cannot fix -- see section 15's honest, mission-compliant fallback: this
segment remains research-only and is NOT claimed fixed. The added
`FCS_OPPONENT_UNCERTAINTY_SCALE` uncertainty inflation (section 4) is
visible in the much wider 90% coverage (0.883 → 0.966, now clearly
over-covering) -- correctly widening the band around a still-biased
center, which is honestly not the same as fixing the bias.

Isotonic on this segment: log loss 0.3267, WORSE than platt's 0.2949 and
worse than the pre-hardening 0.3077 -- consistent with isotonic
overfitting on a smaller (n=290), less diagnostic population, further
confirming platt as the safer default.

### Segment: Neutral site (n=242)

| | Pre-hardening | Raw (hardened) | Calibrated (platt) | Calibrated (isotonic) |
|---|---|---|---|---|
| Log loss | 0.6602 | 0.6682 | 0.6724 | 0.7668 |
| Brier | 0.2341 | 0.2381 | 0.2403 | 0.2423 |
| Margin MAE/RMSE/bias | 13.25/17.35/+0.52 | 13.43/17.45/+2.44 | (same) | (same) |
| Margin 90% coverage | 0.946 | 0.950 | (same) | (same) |
| Total MAE/RMSE/bias | 14.59/17.52/−2.27 | 14.51/17.45/−1.45 | (same) | (same) |
| Total 90% coverage | 0.926 | 0.959 | (same) | (same) |

**Honestly reported, not hidden: on this small (n=242) segment, platt
calibration makes log loss slightly WORSE than raw (0.6724 vs 0.6682),
and isotonic makes it much worse (0.7668)** -- a genuine limit of
calibration fit on the OVERALL population and then applied here: neutral-
site games are a distinct, thinner-evidenced subset, and this segment is
too small on its own to expect calibration tuned globally to help every
subgroup uniformly. This does not reverse the adopted platt default
(overall and FBS-vs-FBS results are net positive, and isotonic is worse
here too), but it is a real, quantified example of the "no obvious
instability" bar mission section 3 asks for being only approximately met,
not perfectly.

### Segment: Home/away, non-neutral (n=2,983)

| | Pre-hardening | Raw (hardened) | Calibrated (platt) |
|---|---|---|---|
| Log loss | 0.5921 | 0.6121 | **0.5860** |
| Brier | 0.2031 | 0.2114 | **0.2026** |
| Margin MAE/RMSE/bias | 14.83/18.84/+0.95 | 15.05/19.18/+3.34 | (same) |
| Margin 90% coverage | 0.941 | 0.961 | (same) |
| Total MAE/RMSE/bias | 13.26/16.59/−1.23 | 13.28/16.64/−0.46 | (same) |
| Total 90% coverage | 0.947 | 0.966 | (same) |

Calibration beats both raw and pre-hardening on this (the largest)
segment.

### By season (raw vs. calibrated-platt vs. pre-hardening)

| Season | n | Log loss: pre-hardening | Log loss: raw | Log loss: calibrated |
|---|---|---|---|---|
| 2022 | 791 | 0.6297 | 0.6615 | 0.6626 |
| 2023 | 804 | 0.5871 | 0.6058 | **0.5581** |
| 2024 | 807 | 0.5986 | 0.6109 | **0.5890** |
| 2025 | 823 | 0.5744 | 0.5885 | **0.5620** |

**2022 is a genuine, reported exception**: calibrated log loss (0.6626)
is marginally WORSE than raw (0.6615) for that season specifically. 2022
is the first season in the corpus -- the calibration model accumulating
history AT THAT POINT in the walk-forward has the least accumulated
history of any season (by definition, since seasons before 2022 are not
in this corpus), so its calibration fit is the least mature. This is
consistent with the documented `MIN_CALIBRATION_HISTORY` fallback design
working as intended (thin-history predictions lean more on the
identity fallback and less on a well-fit correction) rather than a bug.

### Early season (week ≤ 3, n=600) vs. later season (week > 3, n=2,625)

| | Pre-hardening: early | Raw (hardened): early | Calibrated: early | Pre-hardening: later | Raw (hardened): later | Calibrated: later |
|---|---|---|---|---|---|---|
| Log loss | 0.5120 | 0.5702 | **0.5118** | 0.6167 | 0.6269 | **0.6109** |
| Brier | 0.1676 | 0.1922 | **0.1733** | 0.2140 | 0.2182 | **0.2128** |
| Margin MAE/RMSE/bias | 17.92/22.42/**+6.10** | 18.75/23.53/**+9.07** | (same as raw) | 13.98/17.79/−0.27 | 14.05/17.88/+1.94 | (same as raw) |
| Margin 90% coverage | 0.902 | 0.935 | (same) | 0.950 | 0.966 | (same) |

**The early-season margin bias got WORSE, not better, after hardening**
(+6.10 → +9.07) -- the same global out-of-sample-residual mechanism
described in the Overall section, compounded early in a season when the
carryover-prior blend (section 7) is itself still leaning heavily on
last season's rating, which the ridge fit's shrinkage-toward-average
tendency affects most. Calibrated winner log loss is essentially
unchanged for early season (0.5120 → 0.5118, a wash) but improves for
later season (0.6167 → 0.6109) -- winner-probability calibration and
margin-bias correction are two DIFFERENT problems, and fixing one
(calibration) provably does not fix the other (margin bias); see section
6 below on why this is not coincidental.

### Is the early-season pattern driven by FBS-vs-FCS composition?

A direct check, since early slates disproportionately include FBS-vs-FCS
"buy games": comparing the FBS-vs-FCS-only margin bias (+17.24 overall,
this milestone's largest single-segment bias) against the early-season
overall bias (+9.07) shows early season is NOT simply an artifact of FCS
composition -- FBS-vs-FBS games ALONE also show a positive margin bias
post-hardening (+1.89, section 11's FBS-vs-FBS table), so the early-
season effect is real and additive on top of, not merely explained away
by, FBS-vs-FCS games appearing more often early. Both a real early-season
prior-shrinkage effect and a real FCS-pooling effect independently
contribute to margin bias; neither fully explains the other away.

## 12. Benchmark comparison

**Honest, mixed-but-net-positive verdict, unchanged in direction by the
hardening pass: the Milestone C model beats the naive benchmark on
winner probability and margin, is roughly a wash on total points,
out-of-sample, on the same genuine 3,225-game walk-forward backtest.**
The naive benchmark is completely unmodified this pass -- every naive
number below is identical to the pre-hardening document. Specifically:

- **Winner probability: model wins, and by MORE than before calibration.**
  Log loss 0.5924 (calibrated) vs. 0.6141 naive (3.5% relative
  improvement, vs. 2.8% pre-hardening), Brier 0.2054 vs. 0.2133 (same as
  pre-hardening). The RAW, uncalibrated hardened model is actually
  slightly WORSE than naive on log loss (0.6163 vs 0.6141) -- calibration
  is not a cosmetic addition here, it is what makes the net comparison
  favorable at all post-hardening. This is reported explicitly rather
  than only citing the calibrated number.
- **Margin: model still wins on MAE/RMSE, but the bias got worse.** MAE
  14.92 vs. 15.39 naive, RMSE 19.06 vs. 19.59, both still lower than
  naive (though less of a margin than pre-hardening's 14.71/18.74).
  Margin BIAS moved from a modest +0.91 pre-hardening to a real +3.27
  post-hardening (section 11) -- the model's margin predictions are
  still more accurate than naive on average error, but are now visibly,
  measurably biased in one direction, a genuine finding the hardening
  pass surfaced rather than introduced (see section 11's mechanism
  explanation).
- **Total points: still essentially a wash, naive still marginally
  ahead.** MAE 13.37 (model, hardened) vs. 13.17 (naive), RMSE 16.70 vs.
  16.41 -- both very close to the pre-hardening numbers (13.36/16.66).
  This finding is UNCHANGED by the hardening pass, confirming it is not
  an artifact of the specific bugs that were fixed. Total 90% coverage is
  now further from nominal for the model (96.6% vs naive's 91.4%) than it
  was pre-hardening (94.5%) -- the model's total uncertainty band is now
  measurably too wide, not just "better calibrated than naive" as the
  pre-hardening document concluded. This is a more honest, less flattering
  restatement of the same underlying finding.

**Conclusion, not forced either direction:** the added modeling
complexity, WITH calibration, is justified for the mission's CORE_V1
winner target (measurably more so than pre-hardening) and for the spread
target's average error (MAE/RMSE), though margin bias is now a real,
visible weakness that was previously hidden by an in-sample estimation
bug. Total-points prediction remains not demonstrated to be worth its
added complexity over naive -- unchanged from the original finding,
now confirmed stable across a methodology correction rather than resting
on a single run.

## 13. Kalshi CORE_V1 readiness

For each Milestone B.5 CORE_V1 family, two DISTINCT questions, never
conflated: **RESEARCH_PRIMITIVE_AVAILABLE** (does a leakage-safe,
backtested football probability primitive exist for this market
family?) and **PRODUCTION_PRICING_READY** (is it validated and mature
enough to price a real Kalshi contract?) -- the second is NO for all
three, unconditionally, regardless of how the first evaluates.

### Game winner
- RESEARCH_PRIMITIVE_AVAILABLE: **YES** -- `SimulatedGameProjection.prob_home_win()`.
- Out-of-time validated: **YES** -- genuine chronological walk-forward,
  3,225 held-out games (section 9, section 11).
- Calibrated enough for research: **YES, materially improved this pass**
  -- calibrated log loss 0.5924 beats both naive (0.6141) and the
  pre-hardening model (0.5972); calibration bins sit close to the
  diagonal across the full range (section 11). Two real, reported
  exceptions remain: the Neutral-site segment (n=242) where calibration
  slightly underperforms raw, and season 2022 (thinnest accumulated
  calibration history) -- both explained, not hidden, in section 11.
- Production-ready: **NO** -- unconditionally, per mission instruction;
  no Kalshi contract/settlement mapping exists in this codebase at all.

### Point spread
- RESEARCH_PRIMITIVE_AVAILABLE: **YES** -- `SimulatedGameProjection.prob_margin_greater_than()`,
  exact at any real threshold including integers.
- Out-of-time validated: **YES** -- margin MAE/RMSE beat naive overall
  and within FBS-vs-FBS (section 11).
- FBS-vs-FCS limitation: **YES, a real and NOT fully resolved one.** A
  +17.24 margin bias (worse than the pre-hardening +14.84) remains in
  this segment; the hardening pass's targeted fix (`DEFAULT_FCS_RIDGE_LAMBDA`)
  was evidence-motivated but, isolated from the global residual-pool
  shift, did NOT close the FCS-relative-to-FBS excess bias (section 11).
  This segment is explicitly flagged as unsupported for pricing use, per
  mission section 4's fallback instruction, not smoothed over as fixed.
- Production-ready: **NO** -- unconditionally.

### Game total
- RESEARCH_PRIMITIVE_AVAILABLE: **YES** -- `SimulatedGameProjection.prob_total_greater_than()`.
- Out-of-time validated: **YES**, same backtest.
- Naive comparison result: **still a wash, confirmed stable across the
  hardening pass** (MAE 13.37 vs. 13.17 naive, RMSE 16.70 vs. 16.41) --
  forecast quality has not improved over the simpler baseline, and
  interval coverage is now further from nominal (96.6% vs naive's
  91.4%) than pre-hardening's 94.5%, a more honest picture than "better
  calibrated" alone conveyed before.
- Production-ready: **NO** -- unconditionally.

**What this does NOT mean:** none of Kalshi's actual push/tie settlement
mechanics (Milestone B.5's PROBABLE-confidence findings on spread/total
boundary handling) are implemented anywhere in this codebase -- this
milestone produces the football probability, not a Kalshi contract
price or settlement outcome. Wiring the two together (and re-confirming
`projections.distribution.price_market()`'s Normal-approximation
continuity correction against this milestone's genuinely simulated,
discrete distribution) is real remaining work, not assumed done.

## 14. Known limitations

- **The margin-bias hardening fix was incomplete for FBS-vs-FCS, and this
  is explicitly not claimed otherwise**: `DEFAULT_FCS_RIDGE_LAMBDA`
  correctly fixed a real over-shrinkage bug, but the FCS segment's
  EXCESS bias over FBS-vs-FBS is essentially unchanged (15.30 →
  15.35, section 11) -- the true driver is very likely FCS population
  heterogeneity within the single pooled parameter, which was not
  addressed this pass. **FBS-vs-FCS margin/spread output remains
  unsupported for pricing use.**
- **The expanding residual pool (section 8.2) revealed, and did not fix,
  a genuine margin-underprediction tendency** that was previously masked
  by an in-sample estimation artifact -- overall margin bias is now +3.27
  (was +0.91), a real, quantified, and currently unaddressed weakness
  (see section 15, now the top-priority item).
- **`DEFAULT_RIDGE_LAMBDA` (25.0) and shrinkage constants (k=4.0 in
  several places) remain provisional, round numbers**, not
  cross-validated against held-out data -- and the newly-revealed margin
  bias is itself evidence this deserves real cross-validation, not just a
  documented caveat.
- **Calibration (platt) does not help uniformly**: it slightly
  underperforms raw on the Neutral-site segment (n=242) and on season
  2022 specifically (thinnest accumulated history) -- see section 11.
  Both are explained, not hidden, but both are real limits of a single
  globally-fit calibration model.
- **Isotonic regression was tested and rejected as the default**, not
  merely assumed inferior: it underperforms both naive and platt overall,
  and actively makes several segments (FBS-vs-FBS, Neutral site)
  materially worse than the raw uncalibrated number -- a genuine
  overfitting finding from real data (section 11).
- **HFA is a single league-wide scalar** -- conference/travel/altitude/
  rivalry variation was not investigated (see section 6).
- **Talent composite, PPA/success-rate/explosiveness, Elo/SP+/FPI/SRS, and
  play-by-play data are all documented as available but NOT wired into
  V1** (see section 1's data audit) -- a deliberate scope-control decision,
  not an oversight.
- **QB continuity is a team-level PROXY** (`percent_passing_ppa`), not a
  direct QB-identity signal, and is uncertainty-only (no point-estimate
  shift) -- see section 7.
- **Historical closing-line data (`/lines`) was documented as available in
  the data audit but not fetched or used this milestone** -- an
  independent external-benchmark comparison against real market lines
  (mission section 15) is real remaining work.
- **Elo/SP+/SRS/FPI's pregame-vs-postgame timing was not confirmed** from
  documentation alone -- these were excluded from V1 specifically because
  that ambiguity was found and not resolved (see section 1), not silently
  assumed safe.
- **Total-points prediction still does not demonstrate an improvement
  over the naive benchmark**, now confirmed stable across the hardening
  pass rather than resting on a single run (section 12) -- reported
  plainly, not minimized.
- **Interval coverage (margin and total) is now further from the nominal
  90% target than pre-hardening** (over-covering, not under-covering) --
  a genuine, quantified side effect of the wider, more honest
  out-of-sample residual pool (section 11).

## 15. Recommendation for next model improvement

The hardening pass changed the priority order from the original document,
based on genuine new evidence, not a preference -- restated here rather
than left stale.

**1. (Highest priority, NEW) Investigate and correct the margin-bias
mechanism the residual-pool fix revealed** (+3.27 overall, section 11):
this is now the single largest, most broadly-distributed quantified
weakness in the model (visible in nearly every segment, not just one).
The evidence points toward ridge-shrinkage systematically under-predicting
margins out-of-sample -- a natural next step is a genuine cross-validated
`ridge_lambda` sweep (evaluated via this same walk-forward backtest,
never in-sample), rather than the current provisional, round-number
constant. This requires no new data source, reuses the existing backtest
harness, and directly targets a bias that affects the CORE_V1 point-
spread family broadly, not just one segment.

**2. Properly resolve the FBS-vs-FCS margin bias with a structural fix,
not just re-tuned shrinkage** (+17.24, still excess-15.35 over FBS-vs-FBS,
section 11): this pass's evidence rules out shrinkage strength as the
primary driver and points to population heterogeneity within the single
pooled FCS parameter. A concrete, evidence-directed next step: a 2-3-tier
FCS strength bucket (rather than one pooled rating) using CFBD's own
FCS-level win/loss record or `/ratings/srs` for FCS teams once its
pregame-timing safety is independently confirmed (a prerequisite this
document already flags as unresolved in section 1). Since FBS-vs-FCS
Kalshi coverage is itself UNVERIFIED (Milestone B.5), this remains lower
priority than item 1.

**3. Investigate calibration's segment-level exceptions** (Neutral site,
season 2022, section 11) -- likely addressable with a
segment-aware or slower-decaying calibration history window, but this is
a genuine refinement, not a blocking issue: the overall and CORE_V1-
relevant (FBS-vs-FBS) numbers are net positive as shipped.

**Explicitly NOT recommended next, despite being tempting:** adding more
raw features (PPA, success rate, talent composite, Elo) before the above
are addressed. The total-points wash-vs-naive finding (section 12,
confirmed stable across this hardening pass) is a signal that the CURRENT
feature set's marginal value has not been fully extracted yet -- piling
on new features without first correcting a known, well-diagnosed margin
bias would repeat exactly the "add complexity to a weak baseline before
understanding why it's weak" mistake the mission's stop condition
(section 22) warns against.

## Scope boundary (mission spec section 16, restated explicitly)

This milestone produces football forecasting output only. It does NOT:
recommend a wager, size a stake, classify an edge tier, call a Kalshi
trading endpoint, or claim profitability anywhere in this codebase --
mechanically checked by `tests/test_no_recommendation_surface.py`, which
now also scans `cfb_edge_finder.modeling`. Nothing in this document should
be read as "this model is ready to bet with."
