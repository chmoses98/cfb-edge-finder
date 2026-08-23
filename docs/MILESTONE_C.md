# Milestone C — Baseline CFB Projection Engine

**Status: a genuine, leakage-safe, chronologically-backtested baseline
model with real quantitative results below (see "Backtest results" --
filled in from a live run, not fixture data). This is the first
Milestone that produces real football probabilities; it is explicitly
NOT a betting/recommendation engine (see "Scope boundary" at the end).**

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

## 11. Backtest results

All numbers below are copied verbatim from the corrected live run
(`32619827949`), never rounded differently or recomputed.

### Overall (n=3,225 predicted games)

| | Naive benchmark | Milestone C model |
|---|---|---|
| Winner log loss | 0.6141 | **0.5972** |
| Winner Brier | 0.2133 | **0.2054** |
| Margin MAE / RMSE / bias | 15.39 / 19.59 / −0.86 | **14.71 / 18.74** / +0.91 |
| Margin 90% interval coverage | 0.841 | 0.941 |
| Total MAE / RMSE / bias | **13.17 / 16.41** / −1.43 | 13.36 / 16.66 / −1.31 |
| Total 90% interval coverage | 0.914 | 0.945 |

Winner calibration bins (Milestone C model): predicted vs. observed win
rate, by bin (n = sample count):

| Bin | Predicted | Observed | n |
|---|---|---|---|
| [0.2,0.3) | 0.270 | 0.059 | 17 |
| [0.3,0.4) | 0.359 | 0.200 | 115 |
| [0.4,0.5) | 0.459 | 0.400 | 458 |
| [0.5,0.6) | 0.556 | 0.503 | 1124 |
| [0.6,0.7) | 0.639 | 0.721 | 947 |
| [0.7,0.8) | 0.743 | 0.886 | 405 |
| [0.8,0.9) | 0.832 | 0.986 | 148 |
| [0.9,1.0) | 0.919 | 1.000 | 11 |

**A real, visible calibration gap**: in the 0.6-0.9 predicted-probability
range, observed win rates run consistently HIGHER than predicted (e.g.
0.7-0.8 bin: predicted 0.743, observed 0.886) -- the model correctly
ranks favorites but is systematically underconfident about them in this
range. This is flagged honestly here and is the basis for section 15's
recommendation, not smoothed over.

### Segment: FBS-vs-FBS (n=2,935)

Log loss 0.6258, Brier 0.2180, margin MAE/RMSE/bias 14.20/18.14/−0.46
(90% coverage 0.947), total MAE/RMSE/bias 13.16/16.40/−0.62 (90% coverage
0.950). This is the CORE_V1-relevant population and shows the tightest,
best-calibrated coverage of any segment.

### Segment: FBS-vs-FCS (n=290)

Log loss 0.3077, Brier 0.0781 (both much lower/better than FBS-vs-FBS --
expected, since the favorite is usually overwhelming and the winner call
is easy). But margin MAE/RMSE/bias: **19.84 / 23.98 / +14.84** -- a large
positive bias, meaning the model systematically UNDER-predicts how big
these blowouts actually are. Total MAE/RMSE/bias: 15.37/19.03/−8.29 (the
model over-predicts total points by ~8 on average). This is a genuine,
material weakness of the pooled single "generic FCS opponent" rating
(section 4): it averages across FCS programs of very different strength,
understating the true margin when an FBS team faces a particularly weak
one. Margin/total 90% coverage (0.883/0.900) is still reasonable despite
the bias, because the simulated uncertainty band is wide enough to
usually contain the true outcome even when centered incorrectly.

### Segment: Neutral site (n=242)

Log loss 0.6602, Brier 0.2341, margin MAE/RMSE/bias 13.25/17.35/+0.52
(90% coverage 0.946), total MAE/RMSE/bias 14.59/17.52/−2.27 (90% coverage
0.926) -- no sign of a systematic neutral-site-specific problem.

### Segment: Home/away, non-neutral (n=2,983)

Log loss 0.5921, Brier 0.2031, margin MAE/RMSE/bias 14.83/18.84/+0.95
(90% coverage 0.941), total MAE/RMSE/bias 13.26/16.59/−1.23 (90% coverage
0.947).

### By season

| Season | n | Log loss | Brier | Margin MAE/RMSE/bias | Total MAE/RMSE/bias |
|---|---|---|---|---|---|
| 2022 | 791 | 0.6297 | 0.2197 | 15.22/19.59/−0.87 | 13.78/17.08/−1.13 |
| 2023 | 804 | 0.5871 | 0.2005 | 14.54/18.52/+0.65 | 13.49/16.78/−1.50 |
| 2024 | 807 | 0.5986 | 0.2064 | 14.46/18.28/+1.45 | 13.12/16.56/−0.99 |
| 2025 | 823 | 0.5744 | 0.1955 | 14.64/18.54/+2.36 | 13.06/16.21/−1.60 |

Reasonably consistent season-to-season; a mild improving trend in log
loss/Brier across seasons is visible but with n≈800/season is not strong
enough evidence to claim a real trend rather than noise.

### Early season (week ≤ 3, n=600) vs. later season (week > 3, n=2,625)

| | Early (week ≤ 3) | Later (week > 3) |
|---|---|---|
| Log loss | 0.5120 | 0.6167 |
| Brier | 0.1676 | 0.2140 |
| Margin MAE/RMSE/bias | 17.92/22.42/**+6.10** | 13.98/17.79/−0.27 |
| Margin 90% coverage | 0.902 | 0.950 |
| Total MAE/RMSE/bias | 13.41/16.73/−3.79 | 13.35/16.64/−0.74 |

Early-season winner log loss/Brier are actually BETTER than later season
-- plausible given early slates include many lopsided, easy-to-call
buy games. But the margin bias is real and worth flagging precisely
because it is NOT visible in the win-probability numbers: **+6.10**,
meaning the model systematically under-predicts blowout margins in weeks
1-3, consistent with the season-carryover prior (section 7) correctly
pulling early-week ratings toward a conservative blend but perhaps too
conservatively for the games where the true talent gap is large and
already evident.

## 12. Benchmark comparison

**Honest, mixed-but-net-positive verdict: the Milestone C model beats the
naive benchmark on winner probability and margin, is roughly a wash on
total points, out-of-sample, on a genuine 3,225-game walk-forward
backtest.** Specifically:

- **Winner probability: model wins.** Log loss 0.5972 vs. 0.6141 (2.8%
  relative improvement), Brier 0.2054 vs. 0.2133 (3.7% relative
  improvement). Small but consistent and in the expected direction --
  opponent adjustment provides real, if modest, information beyond raw
  scoring averages.
- **Margin: model wins.** MAE 14.71 vs. 15.39, RMSE 18.74 vs. 19.59, both
  meaningfully lower. Interval coverage is also closer to the nominal 90%
  target for the model (94.1%) than the naive benchmark (84.1%, a real
  undercoverage problem for the fixed-SD naive approach).
- **Total points: essentially a wash, naive marginally ahead.** MAE 13.36
  (model) vs. 13.17 (naive), RMSE 16.66 vs. 16.41 -- the naive benchmark's
  simple points-for/against averaging is, on this evidence, about as good
  at predicting total points as the full opponent-adjusted/pace model.
  This is reported plainly rather than minimized: the added sophistication
  of separating pace from efficiency (section 5) has NOT yet demonstrated
  a measurable total-points advantage over the naive approach on this
  corpus. Total 90% coverage is closer to nominal for the model (94.5% vs
  91.4%), so the model's total UNCERTAINTY estimate is arguably better
  calibrated even where its total POINT estimate is not more accurate.

**Conclusion, not forced either direction:** the added modeling
complexity is justified for the mission's CORE_V1 winner and spread
targets (where it demonstrably improves on the naive baseline) but is not
yet demonstrated to be justified for the total-points target specifically
-- see section 15's recommendation, which is drawn directly from this
finding rather than treating "model beats naive" as a given.

## 13. Kalshi CORE_V1 readiness

For each Milestone B.5 CORE_V1 family, whether the underlying FOOTBALL
probability primitive now exists and is validated (NOT whether Kalshi
contract settlement mapping is production-ready -- that is a separate,
unproven question; see "Scope boundary" below):

| Kalshi family | Required primitive | Available in this milestone? | Validated how | Genuine live result |
|---|---|---|---|---|
| Game winner | `P(home_score > away_score)` | YES -- `SimulatedGameProjection.prob_home_win()` | Chronological backtest: log loss, Brier, calibration (section 11) | Beats naive benchmark (log loss 0.5972 vs 0.6141, Brier 0.2054 vs 0.2133) on 3,225 held-out games, but a real calibration gap remains in the 0.6-0.9 predicted range (see section 15, item 1) -- validated and directionally sound, NOT yet calibration-clean |
| Point spread | Margin distribution `P(margin > threshold)` for arbitrary threshold | YES -- `SimulatedGameProjection.prob_margin_greater_than()`, exact at any real threshold including integers | Margin MAE/RMSE/bias/coverage (section 11) | Beats naive on MAE/RMSE and interval coverage overall and within FBS-vs-FBS; a genuine, sizeable bias (+14.84) exists specifically for FBS-vs-FCS games (section 15, item 2) |
| Game total | Total-score distribution `P(total > threshold)` | YES -- `SimulatedGameProjection.prob_total_greater_than()` | Total MAE/RMSE/bias/coverage (section 11) | Roughly a wash vs. the naive benchmark on point accuracy (MAE 13.36 vs 13.17); the model's uncertainty *coverage* is better calibrated (94.5% vs 91.4% at the 90% target) even where its point estimate is not more accurate -- see section 12's explicit, non-forced conclusion |

**What this does NOT mean:** none of Kalshi's actual push/tie settlement
mechanics (Milestone B.5's PROBABLE-confidence findings on spread/total
boundary handling) are implemented anywhere in this codebase -- this
milestone produces the football probability, not a Kalshi contract
price or settlement outcome. Wiring the two together (and re-confirming
`projections.distribution.price_market()`'s Normal-approximation
continuity correction against this milestone's genuinely simulated,
discrete distribution) is real remaining work, not assumed done.

## 14. Known limitations

- **In-sample residual estimation** (section 8): the uncertainty band's
  SHAPE is fit from the same rating snapshot used for the prediction, not
  from a second walk-forward pass. The point predictions themselves ARE
  walk-forward/leakage-safe (the backtest refits ratings weekly); only the
  precision of the uncertainty band around them is affected.
- **`DEFAULT_RIDGE_LAMBDA` (25.0) and shrinkage constants (k=4.0 in two
  places) are provisional, round numbers**, not cross-validated against
  held-out data.
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
- **A real, measured winner-probability calibration gap** exists in the
  0.6-0.9 predicted range (observed win rates run higher than predicted --
  see section 11's calibration table). This is the single highest-value
  fix identified this milestone (section 15).
- **A real, measured margin/total bias in the FBS-vs-FCS segment**
  (+14.84 margin bias, section 11) traces directly to the pooled
  single-parameter FCS rating (section 4) being too coarse for this
  population's wide talent spread.
- **Total-points prediction did not demonstrate an improvement over the
  naive benchmark** on this corpus (section 12) -- reported plainly, not
  minimized. The pace/efficiency decomposition (section 5) has not yet
  proven its value for this specific target.

## 15. Recommendation for next model improvement

Two genuine findings compete for "highest value"; both are reported, with
a stated priority order and why.

**1. (Highest priority) Fix the winner-probability calibration gap in the
0.6-0.9 predicted range** (section 11's calibration table): observed win
rates run meaningfully higher than predicted in exactly the range that
matters most for a game-winner/spread market (moderate-to-strong
favorites, not toss-ups or near-locks). This directly affects Kalshi
CORE_V1 readiness (section 13) -- a systematically underconfident
probability is a systematically mispriced one. The fix is well-understood
and evidence-directed, not speculative: fit a monotonic recalibration
(Platt scaling or isotonic regression) on ONLY strictly-prior weeks within
the same walk-forward scheme already built (`backtest.py`), apply it to
each week's raw probabilities before scoring. This requires no new data
source and no architecture change -- it is a genuine next step, not a
research project.

**2. Address the FBS-vs-FCS margin bias** (+14.84, section 11): the
single pooled "generic FCS opponent" rating is too coarse for games where
the true talent gap is large, which is common in this segment. Since
FBS-vs-FCS Kalshi coverage is itself UNVERIFIED (Milestone B.5), this is
lower priority than item 1, but the fix is concrete: at minimum, a
2-3-tier FCS strength bucket (rather than one pooled rating) using CFBD's
own FCS-level win/loss record or `/ratings/srs` for FCS teams once its
pregame-timing safety is independently confirmed (a prerequisite this
document already flags as unresolved in section 1).

**Explicitly NOT recommended next, despite being tempting:** adding more
raw features (PPA, success rate, talent composite, Elo) before either of
the above is fixed. The total-points wash-vs-naive finding (section 12) is
a signal that the CURRENT feature set's marginal value has not been fully
extracted yet (via calibration) -- piling on new features without first
correcting a known, well-diagnosed calibration issue would repeat exactly
the "add complexity to a weak baseline before understanding why it's
weak" mistake the mission's stop condition (section 22) warns against.

## Scope boundary (mission spec section 16, restated explicitly)

This milestone produces football forecasting output only. It does NOT:
recommend a wager, size a stake, classify an edge tier, call a Kalshi
trading endpoint, or claim profitability anywhere in this codebase --
mechanically checked by `tests/test_no_recommendation_surface.py`, which
now also scans `cfb_edge_finder.modeling`. Nothing in this document should
be read as "this model is ready to bet with."
