# CFB Script Engine V1

```
football data (pre-kickoff only)
  -> opponent-adjusted team profiles            adjust.py, matchup.py
  -> market-blind matchup vector                matchup.py
  -> deterministic findings                     findings.py
  -> 0-4 evidence-gated game scripts            scripts.py
  -> FROZEN football artifact + SHA-256         freeze.py
-------------------------------------------- market data enters only here
  -> script/market compatibility map            market_map.py
  -> script survival, labels, ladders           expressions.py
  -> SIFT payload, prospective ledger           publish.py, ledger.py
  -> realized-script scoring, report            realized.py, report.py
```

This is **not a betting model** and it does not revive the retired one
(`docs/MODEL_RETIREMENT_2026.md`). Nothing in `cfb_edge_finder.scripting`
imports `modeling`, `projections`, `recommendation`, `decision(s)` or `sizing`;
nothing publishes a fair probability, an expected value, a stake or a "+EV"
claim; `NO_MODEL_PRICES` stays true and `model_prices.json` stays empty.

The product answers three questions **separately** and never collapses them:

1. What does the football matchup say? (profile + findings)
2. How can this game plausibly unfold? (scripts)
3. Which available contracts express those possibilities, at today's prices? (market map)

---

## 1. Current-state audit (what existed before this layer)

| Piece | State before | Source |
|---|---|---|
| Market discovery, catalog, completeness | Live, every 30 min | `catalog/`, `data/live` |
| Factual context (12 domains) | Live, not committed | `execution/context*.py`, `scripts/collect_live_context.py` |
| Scoring context | Raw points per game over a **21-day** ESPN scoreboard window, explicitly `opponent_adjusted: False` | `context_sources.scoring_field` |
| Efficiency / situational / pace | CFBD-only, never fetched in production (no `--cfbd`), and per-game not per-season | `context_sources.cfbd_*` |
| Box scores, play-by-play | **Not fetched anywhere** | — |
| Opponent adjustment | Only inside the retired model (`modeling/ratings.py`) | — |
| App export | `event_research.matchup` empty for all 242 events; CFBD values licence-gated off | `scripts/research_export.py` |

So the engine needed its own game log. It is keyless:

## 2. Data sources and tiers

`scripts/collect_football_gamelog.py` (scheduled by `.github/workflows/script-engine.yml`):

| Source | Endpoint | What | Tier |
|---|---|---|---|
| ESPN scoreboard | `site.web.api.espn.com …/scoreboard?groups=80|81&dates=` (CDN fallback) | schedule, kickoff, site, conference ids (division), status | identity / freshness |
| ESPN summary — box score | `site.api.espn.com …/summary?event=` → `boxscore.teams[].statistics`, `boxscore.players[].statistics[defensive|passing]` | plays, yards, rush/pass splits, first downs, 3rd/4th down, turnovers, possession time, penalties, sacks, TFL, passes defended, hurries, primary passer | **CORE** |
| ESPN summary — play log | `drives.previous[].plays[]` | success, early-down success, designed-rush vs dropback splits, sack-adjusted passing, explosive plays, drives, scoring opportunities, tempo, neutral-situation pass rate | **ENHANCED** |
| ESPN core injuries | `sports.core.api.espn.com …/teams/{id}/injuries` | availability listing; a quarterback listed Out/Doubtful/Questionable | availability |
| CFBD (opt-in only) | `/games/teams`, `/stats/game/advanced`, `/drives` | the same CORE counts; success counts and drive outcomes as an ENHANCED alternative | ENHANCED (licence-gated) |

Measured on the first production run (2026-10-06): **390** completed games
involving an FBS team, **0** fetch failures, **0** missing, **100 %** play-log
coverage, 141 teams' availability observed.

A play log is used only when it **reconciles** with the box score (logged
scrimmage plays within ±25 % of box plays, at least four drives); otherwise
the game's ENHANCED counts are dropped with the reason recorded, never
half-used. The parser is tested against real captured payloads
(`tests/fixtures/espn/`).

### Play conventions (ENHANCED)

| Term | Definition |
|---|---|
| scrimmage play | down 1–4, type rush / pass / sack / interception / fumble; never penalty, kick, punt, timeout, two-point try |
| success | ≥ 50 % of distance on 1st down, ≥ 70 % on 2nd, 100 % on 3rd/4th; TD = success; turnover or sack = failure |
| explosive | designed run of 12+ yards, completed pass of 16+ yards |
| garbage time | margin at the snap > 38 (Q2), > 28 (Q3), > 22 (Q4): excluded from every rate, counted separately |
| neutral situation | quarters 1–3, margin within 14 |
| scoring opportunity | a drive with a snap at or inside the opponent's 40 |
| drive points | TD = 7, FG = 3 (PATs and two-point tries not attributed; defensive/return scores are not the offense's) |

## 3. The metric registry (`metrics.py`)

Every metric is defined once, from the **offense's** point of view, as
numerator/denominator counts. The defensive metric is the same quantity
**allowed** (opponents' offense rows). Season values are Σnum / Σden, never
a mean of per-game rates.

| Dimension | CORE | ENHANCED |
|---|---|---|
| scoring | points per game | — |
| sustained efficiency | yards/play, first downs/play, third-down rate (*secondary*) | success rate, early-down success rate |
| rushing | yards/rush (NCAA box: sacks count as rushes) | rush success rate, yards/designed rush |
| passing | yards/attempt, interception rate (*regression-prone*) | net yards/dropback (sack-adjusted), pass success rate |
| explosiveness | — | explosive-play rate, explosive rush rate, explosive pass rate |
| disruption | sack rate allowed, havoc proxy (TFL + PD per play, **turnovers excluded**), turnovers/game (*regression-prone*) | — |
| finishing | — | points per scoring opportunity, points per drive |
| pace | plays/game, possession seconds/play, pass rate (*not adjusted*) | neutral pass rate (*not adjusted*) |
| volatility | residual game-to-game spread (from the fit) | explosive-yard share (*not adjusted*) |

Turnovers are published but **never carry a script on their own**:
repeatable disruption (sacks, TFL, passes defended) is a separate metric
from raw turnover margin.

Every metric published to SIFT carries: raw, adjusted (or `null` with the
reason), standard error, rank, universe size, rank basis, direction, games,
effective sample, prior weight, source, observation time, season, window
and a quality flag (`OK`, `THIN_SAMPLE`, `PRIOR_HEAVY`, `UNAVAILABLE`,
`NOT_ADJUSTED`). **Raw is never substituted for adjusted.**

## 4. Opponent adjustment — the exact math (`adjust.py`)

One fit per metric, per data cutoff. For every team-game row *r* (team *t*'s
offense against opponent *d*) with per-game value *y_r = num_r / den_r*:

```
y_r = μ + h·x_r + o_t + g_o·[t ∉ FBS] + d_d + g_d·[d ∉ FBS] + ε_r
```

* `μ` league baseline (unpenalised); `x_r` = +1 home, −1 away, 0 neutral, so
  `h` is half the home/away gap
* `o_t` offensive effect (positive = produces more), `d_d` defensive effect
  (positive = **allows** more)
* `g_o`, `g_d` shared non-FBS offsets, so an FCS opponent is not treated as an
  average FBS team

Weighted ridge least squares, `w_r = den_r / mean(den)` clipped to
[0.25, 2.5] (per-game counts weight 1). Penalty `λ` per team effect, stated
**in games**: λ = 4 for FBS teams (8 for non-FBS, toward their offset), 0.5 on
the group offsets, 1 on `h`. With *n* games against average opposition an
estimate keeps ≈ n / (n + λ) of its raw deviation: a team two games in carries
a 67 % prior weight and cannot look certain.

```
A     = XᵀWX + Λ
β     = A⁻¹ XᵀWy
s²    = Σ w·(y − Xβ)² / max(N − tr(A⁻¹XᵀWX), 1)
Var β = s² · A⁻¹                      (posterior-style uncertainty)
```

Published per team and unit:

* `adjusted = μ + o_t (+ g_o)` — this offense against an average defense,
  neutral site (likewise defense)
* `se = √(cᵀ Var β c)` for that contrast
* `games`, `effective_n = Σw`, `prior_weight = λ / (λ + effective_n)`
* rank within FBS teams (ties broken by team id); non-FBS teams are unranked

**Stability**: the fit reports the number of connected components of the
FBS-vs-FBS schedule graph and the median FBS sample; it is *stable* only with
one component and a median ≥ 3 games.

**Leakage**: history comes only through `gamelog.before(rows, cutoff)`,
which keeps a game only if `kickoff + 4h30m < cutoff`. The cutoff for a game
is **04:00 US/Eastern on its local date** (`gamelog.data_cutoff`): every game
on a slate day shares one history, and a game in progress can never feed a
later kickoff. `tests/test_script_engine_adjustment.py` proves a fit at
cutoff C on the full season equals the fit on rows truncated at C, and that
appending future games never changes a past estimate.

Validated on known truth (synthetic season, `tests/script_engine_fakes.py`):
correlation of adjusted with true offensive effects 0.92. On the real 2025
CFBD season the method ranks plausibly (Tennessee, USC, Indiana top
opponent-adjusted scoring offenses at 2025-10-11) with ~45 % prior weight at
five games.

## 5. The matchup vector (`matchup.py`)

For one metric and one direction (home offense vs away defense):

```
offense_z   = s · (adjusted_offense − mean) / sd        (FBS distribution of adjusted values)
defense_z   = s · (adjusted_defense_allowed − mean) / sd
edge        = offense_z + defense_z                       s = +1 if more is better for an offense
uncertainty = √((se_off/sd_off)² + (se_def/sd_def)²)
```

A dimension's edge is the mean of its primary metrics' edges (secondary,
regression-prone and descriptive metrics are shown, never averaged in).
`net_home_advantage = home direction − away direction`. Pace:
`possession_environment = (z home O plays + z away D plays allowed + z away O
plays + z home D plays allowed) / 2`. Volatility: residual spread percentiles,
explosive-yard share, raw turnover rates.

**Scoring baseline** — the only scoring number the engine computes:
`home = adj_off(home) + adj_def_allowed(away) − μ + h·x`, likewise away.
Labelled *descriptive, uncalibrated, NOT a projection*; used only to draw the
scoring bands a script's shape is stated in.

## 6. Findings (`findings.py`)

Each finding is a code, the side it favours, the value, the threshold, the
uncertainty, and the metric keys (`home.offense.success_rate`, …) that
produced it; its sentence is generated from those numbers. Two gates for
every matchup finding: `|value| ≥ threshold` **and** `|value| ≥ uncertainty`
(noise cannot become a finding).

| Family | Codes (S = HOME/AWAY) | Threshold (moderate / strong) |
|---|---|---|
| net advantage | `S_SUSTAINED_EFFICIENCY_ADVANTAGE`, `S_FINISHING_ADVANTAGE`, `S_EXPLOSIVE_ADVANTAGE`, `S_SCORING_ADVANTAGE`, `S_EARLY_DOWN_ADVANTAGE` | 1.0 / 2.0 |
| unit matchup | `S_RUSH_ADVANTAGE`, `S_PASS_ADVANTAGE`, `S_PASS_EXPLOSIVE_ADVANTAGE`, `S_RUSH_EXPLOSIVE_ADVANTAGE`, `S_DISRUPTION_ADVANTAGE`, `S_DEFENSIVE_CONTROL` | 1.0 / 2.0 |
| environment | `HIGH/LOW_POSSESSION_ENVIRONMENT` (1.0/2.0), `HIGH/LOW_SCORING_ENVIRONMENT` (1.5/3.0), `BOTH_OFFENSES_EFFICIENT`, `BOTH_DEFENSES_CONTROL` (0.75), `NARROW_EFFICIENCY_GAP`, `EVEN_MATCHUP` | as stated |
| dependence | `S_SCORING_DEPENDENT_ON_EXPLOSIVES`, `S_DEFENSE_TURNOVER_RELIANT`, `S_OFFENSE_TURNOVER_PRONE`, `S_HIGH_VARIANCE_OFFENSE`, `HIGH_VARIANCE_MATCHUP` | z ≥ 1.0; percentile ≥ 0.8; ≥ 2 indicators |
| data | `S_THIN_SAMPLE`, `S_NON_FBS`, `S_QB_CHANGE_RECENT`, `S_QB_AVAILABILITY_UNCERTAIN` | — |

## 7. Scripts (`scripts.py`)

Archetypes are labels with explicit evidence requirements, never hardcoded
conclusions. A script exists only if its **required** findings exist.

| Archetype | Requires | Margin band (definition) |
|---|---|---|
| `HOME_CONTROL` / `AWAY_CONTROL` | S sustained-efficiency advantage | S by 7–24 |
| `FAVORITE_PULLS_AWAY` | **strong** S efficiency edge + a second, non-scoring S advantage (finishing, disruption, explosiveness, rushing or passing; a scoring advantage only supports) | S by 17–45 |
| `UNDERDOG_HANGS_AROUND` | S efficiency edge + a **non-pace** counter (other side's disruption or defensive control, S explosive dependence or turnovers, other side's explosives, both defenses control, narrow gap); low possessions only supports | −7 to +8 on S |
| `EXPLOSIVE_UPSET` | S efficiency edge + the other side's explosive advantage | other side by 1–14 |
| `TURNOVER_DISRUPTION` | S disruption advantage + a volatility finding (never turnovers alone) | S by 1–21 |
| `COMPETITIVE_SHOOTOUT` | high scoring environment or both offenses efficient; no strong edge | ±8 **only with** `EVEN_MATCHUP` or `NARROW_EFFICIENCY_GAP`, otherwise no margin; total baseline +4 to +28 |
| `COMPETITIVE_GRIND` | low scoring / low possessions / both defenses control; no strong edge | ±8 **only with** `EVEN_MATCHUP` or `NARROW_EFFICIENCY_GAP`, otherwise no margin; total baseline −28 to −4 |
| `COMPETITIVE_TOSSUP` | `EVEN_MATCHUP` that the data resolve (see below), and no shootout/grind applies | ±8 |
| `PACE_DRIVEN_OVER` | high possession environment | total baseline +3 to +28 |
| `DEFENSIVE_SUPPRESSION` | both defenses control | total baseline −30 to −6 |

Team-points bands are drawn around the scoring baseline (e.g. control: leader
baseline −3…+14, trailer −14…+2).

**A scoring or pace environment is not a margin.** High or low scoring and
possession volume say nothing about closeness, so a shootout or grind states
its one-score band — and a chain step claiming "within one score" — only
when an independent closeness finding (`EVEN_MATCHUP`,
`NARROW_EFFICIENCY_GAP`) exists, and that step cites it. Without one the
script keeps its scoring environment and states no margin, so it is NEUTRAL
for every moneyline and spread.

**Band authority.** Margin bands are `ARCHETYPE_DEFINITION`. Every total and
team-points band is `UNCALIBRATED_DESCRIPTIVE`: its centre is the descriptive
scoring baseline and its offsets are hand-set. Each script publishes
`outcome_shape.band_authority`; market mapping may not rest on an
uncalibrated band (section 15).

**Ranking (no probabilities in V1).** `evidence = Σ weight(required +
supporting) − Σ weight(contradicting)`, STRONG = 2, MODERATE = 1.
PRIMARY = best evidence; DANGER = best remaining script that **breaks** the
primary (different winner, opposite total environment, or a margin band
below the primary's — a bigger version of the same thesis is an alternate,
never a danger), which may be net-contradicted down to −1 but must have its
required findings; SECONDARY / ALTERNATE = next best; at most four; mutually
redundant archetypes excluded. `probability` is present and **always null**.

**Causal chains** are built step by step from present findings and each step
cites them (`tests/test_script_engine_scripts.py` asserts every step's
findings exist and that no market word appears in any generated text).

## 8. Data confidence and validation gates (`confidence.py`)

Gates — all must pass for anything above LOW: opponent-adjusted inputs exist
(yards/play and points, both units, both teams); ≥ 3 games each; identity
PASS or RESOLVED; game log FRESH (every finished prior game of either team
involving an FBS team is in the log); no quarterback listed uncertain;
market-blind (structural).

* **HIGH**: all gates, core coverage ≥ 90 %, ≥ 3 ENHANCED dimensions,
  ≥ 5 games each, stable adjustment, availability **observed** for both teams,
  no QB change, key prior weight ≤ 50 %
* **MEDIUM**: all gates, core coverage ≥ 80 %, stable, no QB change
* **LOW**: otherwise, with every reason listed

Confidence describes evidence, never how strongly a side is favoured: the
scripts themselves do not change with it.

## 9. Freezing and market blindness (`freeze.py`, `football.py`)

The frozen content is canonical JSON (sorted keys, compact, UTF-8) of
identity, input fingerprints, matchup profile, findings, scripts,
confidence and the SIFT read; `artifact_hash = SHA-256`. An unchanged
football picture keeps its hash **and** its original `generated_at`. A change
records why: `LEAGUE_GAMELOG_CHANGED (+n games)`, `TEAM_GAMELOG_CHANGED`,
`AVAILABILITY_CHANGED`, `IDENTITY_CHANGED`, `FRESHNESS_CHANGED`,
`METHODOLOGY_CHANGED`, `KICKOFF_CHANGED`. A market price is never a possible
reason because it is never an input.

**Proof** (`tests/test_script_engine_market_blindness.py`):

1. *Structural*: the import closure of every football module
   (`scripting.FOOTBALL_MODULES`) reaches no catalog, Kalshi, execution,
   accounting, decision, recommendation, modeling, projections, sizing,
   research or market-side scripting module; every scripting module is
   classified football or market; `FootballPacket` and `GameRequest` have no
   field a price could travel in; the catalog adapter keeps identity only.
2. *Behavioural*: the same football packet run against five radically
   different Kalshi price worlds (as listed, home 97¢, away 97¢, all 50¢,
   inverted) yields **byte-identical** frozen artifacts and identical
   compatibility maps (non-vacuous: ≥ 15 eligible contracts, ≥ 20 mapped
   expressions in every world).
3. `map_game` refuses an envelope whose content no longer matches its hash
   and refuses to run before the artifact's `generated_at`.

## 10. Market mapping (`market_map.py`, `expressions.py`)

Contract terms come from the normalized semantics the evaluator already uses
(`execution.semantics` via `slate.build_packet`), turned into the exact set of
integer outcomes each side pays on by `card_review.win_set` — no ticker
parsing. Kalshi team slots are translated to the football artifact's
home/away **by team identity** when the orders differ.

For a script band [lo, hi] on the same variable: **SUPPORTED** (every outcome
pays), **PARTIAL** (some do; coverage = share of the band), **CONTRADICTED**
(none), **NEUTRAL** (the script states no band), **UNMAPPABLE** (not a single
interval on a full-game margin/total/team-points variable: first-half,
props, tie/overtime, a two-piece NO — with the reason),
**RESEARCH_UNCALIBRATED** (the script's band on this variable has no market
authority — in V1 every total and team-points band; what it would have said
is kept under `research`, and it scores 0). Every eligible
contract is evaluated on both sides:
`expressions = 2 × eligible = mapped + unmappable`.

**Survival**: supported / partial / contradicted / neutral counts out of the
published scripts, plus a weighted sort score (PRIMARY 1.0, SECONDARY 0.7,
ALTERNATE 0.5, DANGER 0.35; SUPPORTED +1, PARTIAL +0.5×coverage,
CONTRADICTED −1, RESEARCH_UNCALIBRATED 0) used **only to sort**; the raw
per-script map is published. `meaningful_scripts` excludes neutral,
unmappable and research-uncalibrated entries, so a total or team-total
contract has none and can earn no positive label.

**Labels** (never a price verdict): `MULTI_SCRIPT`, `BEST_EXPRESSION`,
`SCRIPT_ALIGNED`, `AGGRESSIVE`, `SCRIPT_DEPENDENT`, `NARROW_SCRIPT`,
`CONTRADICTED`, `LOW_DATA_CONFIDENCE`, `MARKET_DISAGREEMENT`,
`SCORING_BAND_UNCALIBRATED` (every mapped total and team-total contract, and
its only script label until the section 15 gate passes).
`HIGH_PROBABILITY_EXPRESSION`, "+EV", fair probability and bet-up-to are
**not in the vocabulary**: every expression carries
`pricing = {status: RESEARCH_ONLY, source: null, fair_probability: null,
expected_value: null}`.

**Price enters only to compare rungs of one thesis**: the best expression of a
thesis is the one with the most script support; between rungs with identical
support, the cheaper entry (its extra requirement is inside every supporting
script). Each thesis publishes its ladder: relation to the core expression
(`card_review.relate`: nested tail extension, correlated path, …), the extra
points required, which scripts each rung loses. `MARKET_DISAGREEMENT` is a flag
beside the artifact, raised when the football PRIMARY backs a side whose
moneyline asks ≤ 40¢, or when the PRIMARY's total band excludes the full-game
total line the market prices nearest 50¢ (within 15¢); nothing in the
artifact moves.

## 11. Publication

* `data/football/<season>/` — the game log, schedule, availability, manifest
* `data/scripting/live/frozen/<game_key>.json.gz` — the frozen envelope
* `data/scripting/live/sift/<game_key>.json.gz` — the compact SIFT payload
  (metric tables with interned legends, compatibility strings, ladders;
  **no prices**: SIFT joins quotes by ticker)
* `event_research.extensions.script_engine` — embedded by
  `research_export.py` (the contract's open `extensions` slot: no contract or
  MANIFEST change, other sports unaffected), trimmed with a disclosed note to
  the 150 KB event budget; `opponent_adjustment` and `matchup_metrics`
  capabilities report `RESEARCH`
* `script-ledger` branch (main only) — the prospective ledger

## 12. Prospective validation (`ledger.py`, `realized.py`, `report.py`)

Append-only rows: `PUBLICATION` (first publication of an artifact hash) and
`FINAL_PREGAME` (the last build within 3 hours of kickoff; the workflow runs
every 2 hours Thursday–Saturday). A row recorded at or after kickoff is
refused. Each row carries the frozen scripts (role, archetype, bands), the
finding codes, every mapped expression's compatibility, labels, survival,
win set and entry price; the full envelope is stored once, content-addressed.

After the game, `realized.py` classifies the game objectively from the same
box-score/play-log rows (control, pulls away, hangs around, shootout, grind,
toss-up, explosive upset, disruption, or `AMBIGUOUS`), checks each frozen
script (every stated band contains the realized value **and** its mechanism
held), checks each matchup finding's direction, and settles every expression
from its exact win set. `report.py` builds: PRIMARY described, PRIMARY or
SECONDARY, any script; by archetype and confidence tier; finding accuracy by
code; and research-unit performance (one contract at the recorded cost) for
MULTI_SCRIPT, BEST_EXPRESSION, single-script, AGGRESSIVE and CONTRADICTED
expressions, by tier. Nothing is backfilled: a game with no pregame row does
not exist for the report.

## 13. Known limitations

* ESPN is an undocumented public API; a shape change is caught by the
  reconciliation check and the fixture tests, not prevented.
* Early season (weeks 0–3) is LOW by design: thin samples, an unconnected
  schedule graph.
* Margin bands are archetype definitions, not estimates; total and
  team-points bands sit on an uncalibrated descriptive baseline.
* First-half, quarter, overtime and player markets are UNMAPPABLE in V1.
* Availability is a listing, not a depth chart; ESPN's CFB injury lists are
  sparse, and a quarterback change is inferred only from box scores.
* No weather, travel distance, coaching tendency, motivation or roster
  continuity enters the evidence.
* The home effect is shared by all teams; FCS teams share an offset.
* Four catalog games (spelling differences with no code match) fail identity
  and publish no read, with the reason.

## 14. What must happen before script probabilities are published

1. Accumulate prospective, frozen `FINAL_PREGAME` rows and settle them —
   at least 30 settled games per published archetype (`report.MIN_SAMPLE`),
   ideally two seasons, covering all confidence tiers.
2. Freeze a calibration method in advance (e.g. reliability by archetype and
   evidence score, isotonic per role) and validate it out of sample on a
   later block of weeks than it was fitted on.
3. Show the calibrated frequencies beat a naive baseline (archetype base
   rates) out of sample, by tier.
4. Only then populate `probability`, versioned, with the calibration artifact
   named — and still never call a contract "+EV" without an identified,
   validated pricing source.

## 15. Scoring-band authority and the promotion gate

Every numeric band a script states carries its provenance in
`outcome_shape.band_authority` (and the game-level `game_scripts.band_policy`):

| Authority | Bands | May a market classification rest on it? |
|---|---|---|
| `ARCHETYPE_DEFINITION` | `home_margin` — control 7–24, pulls away 17–45, one score ±8, hangs around −7…+8, upset 1–14, disruption 1–21 | Yes. The numbers *are* the archetype. |
| `UNCALIBRATED_DESCRIPTIVE` | `total_points`, `home_points`, `away_points` — every band whose centre is `matchup.scoring_baseline` | **No.** Research context only. |
| `CALIBRATED` | reserved; no V1 band carries it | Only after this gate passes. |

**Market consequence.** A total or team-total contract classified against an
uncalibrated band gets the compatibility status `RESEARCH_UNCALIBRATED`, with
what the band would have said kept under `research.band_relation`. That
status scores 0 in script survival and is excluded from `meaningful_scripts`,
so a scoring contract can never be SUPPORTED, CONTRADICTED, MULTI_SCRIPT,
SCRIPT_ALIGNED or a BEST_EXPRESSION; it carries the single label
`SCORING_BAND_UNCALIBRATED` and `market_authority: RESEARCH_UNCALIBRATED`.
A band whose authority is missing is treated as uncalibrated (fail closed).
The qualitative scoring conclusions — `total_environment` ELEVATED /
SUPPRESSED, `home_scoring` / `away_scoring` ABOVE / BELOW_BASELINE, and the
findings behind them — are untouched and remain football research.
A MARKET_DISAGREEMENT on totals is still reported, marked
`evidence_of_value: false`: it is an observation about an uncalibrated band.

**The market never repairs the baseline.** Band authority is decided on the
football side before any price is read; the football artifact stays
byte-identical across price worlds (`test_script_engine_market_blindness.py`).

### 15.1 Pre-registered retrospective validation (written before any error was computed)

*Target:* actual final scores (home points, away points, total). Never a
Kalshi total, never a sportsbook line.

*Prediction under test:* `matchup.scoring_baseline` exactly as production
computes it, rebuilt for each target game by `build_content` from only the
games that finished before that game's data cutoff (04:00 America/New_York on
its local date; `gamelog.before`). One league fit per distinct history.

*Blocks:* 2024 regular season (CFBD cache, `research-data` branch), 2025
regular season (same source), 2026 completed games before 2026-10-06 (the
production ESPN game log). Every completed game with both teams in the log;
games where the baseline is undefined (a team with no prior game) are counted
and excluded.

*Reported:* N, bias (predicted − actual), MAE, RMSE, median / p80 / p90
absolute error for the total; bias and MAE for home and away points;
segmented by prior-games bucket, reconstructed data confidence, predicted
total bucket, unit-strength matchups (terciles of the adjusted points
offense / defense among FBS teams at the cutoff), conference game, and
FBS-vs-FCS involvement. Reference: the naive league total (2 × fitted `mu`
plus the home effect) from the same fit.

*Defense double-counting test:* per team-game, regress
`actual − mu − h·x` on `o = adj_off − mu` and `d = adj_def_allowed − mu`.
A coefficient on `d` below 1 means defensive strength is over-credited in
the additive baseline, above 1 under-credited.

*Candidate corrections (fixed in advance; nothing else is tried):*
1. `linear_total`: `total = a + b · baseline_total` (OLS).
2. `components`: per team-game `points = mu + h·x + c + b_o · o + b_d · d` (OLS).

*Splits (fixed in advance):*
* **Split A** — fit on 2024, evaluate on 2025.
* **Split B** — fit on 2024 + 2025, evaluate on 2026-to-date.

No correction is fitted on, tuned on or selected by a test block, and none
is put into production by this validation: a passing correction only becomes
a candidate for the gate below.

### 15.2 Promotion gate: UNCALIBRATED_DESCRIPTIVE → CALIBRATED

All of the following, for one frozen, versioned correction method:

1. **Pre-registration.** The method, its training block and its test blocks
   are committed before the test blocks are scored.
2. **Two untouched out-of-sample blocks pass**, each with N ≥ 300 completed
   FBS-involved games (Split A and Split B above, or later blocks):
   * total bias within ±1.5 points, with its 95% interval containing 0;
     home and away points bias each within ±1.0;
   * total MAE at least 3% better than both the raw baseline and the naive
     league total;
   * no segment from 15.1 with N ≥ 100 shows a bias beyond ±3 points whose
     95% interval excludes 0.
3. **Bands come from the calibrated error distribution**, not from hand-set
   offsets: each scoring band is a stated central interval of the
   out-of-sample residual distribution, and its empirical coverage in each
   test block is within ±5 percentage points of nominal, overall and in each
   predicted-total bucket with N ≥ 100.
4. **Prospective confirmation.** The first ≥ 150 prospectively frozen games
   (ledger `FINAL_PREGAME` rows, settled on actual scores) meet the bias and
   coverage criteria of points 2 and 3.
5. **One change, recorded.** `SCORING_BAND_AUTHORITY` moves to `CALIBRATED`
   in the same change that bumps `METHODOLOGY_VERSION`, names the calibration
   artifact and adds a test pinning the evaluation report. The markets never
   enter: no step of the gate reads a price.

Until every point holds, scoring contracts stay `RESEARCH_UNCALIBRATED`.

### 15.3 Results (run 2026-10-06; full tables in `docs/SCORING_BASELINE_VALIDATION.md`)

Per-game records and the summary are in `data/scripting/validation/`;
the 2026 inputs used are snapshotted in `data/scripting/validation/inputs/`;
2024 and 2025 come from the CFBD cache on the `research-data` branch
(file digests in the report). Reproduce with `scripts/validate_scoring_baseline.py`.

* **No overall upward bias.** Total bias (predicted − actual): 2024 −0.97
  (N 784), 2025 −0.75 (N 793), 2026 −0.42 (N 244); every 95% interval
  contains 0. MAE 12.2–12.9, RMSE 15.2–16.4, p90 absolute error ≈ 24–27.
* **Little information about totals.** corr(baseline, actual total) is
  0.24–0.27; the baseline's MAE beats the naive league total (2 × `mu`) by
  only 2.7% (2024–25) and 4.6% (2026).
* **Over-dispersed.** Low predictions come in low and high ones high:
  predicted < 45 runs −5 points; 55–65 runs +3 to +3.5; 65–75 runs +4.5 to
  +4.8 (small N). The pre-registered `linear_total` slope fitted on 2024 is
  0.61, i.e. the baseline's spread should be shrunk by ~40%.
* **Strong defenses are not double-counted.** `b_defense` = 1.04 ± 0.07
  (2024), 1.12 ± 0.07 (2025), 1.01 ± 0.13 (2026): the additive baseline
  credits defensive strength about right (if anything slightly under).
  Both-defenses-strong games are unbiased (−1.95, +1.21, +1.19, all CIs
  containing 0).
* **The scripts' total bands are what is mis-placed.** Where the PRIMARY
  was a shootout, actual totals landed *below* the baseline (−3.1, −1.4,
  −3.7) while the band is centred +16 above it; where it was a grind, totals
  landed *above* the baseline (+2.4, +2.5; 2026 −2.5) while the band is
  centred −16 below. Only 26–31% of shootout totals fell inside the shootout
  band. The scoring-environment findings are computed from the same
  adjusted inputs the baseline already contains, so offsetting the band by
  them counts the environment twice. Georgia–Alabama (baseline 69, band
  73–97) is this pattern, not a baseline that ran high.
* **2026 home effect is inflated early.** Applied home effect 6.0 points in
  2026 (2.4 and 3.3 in 2024–25): home points run +3.0, away −3.4, and the
  predicted home margin (+15.1) is well above the actual (+8.7). Early in the
  season the home term is confounded with FBS-hosts-FCS games. Margin bands
  are archetype definitions and do not use it; it is a known limitation
  of the descriptive baseline, recorded here, not changed.
* **Early season runs low.** Games where a team had 1–2 prior games run
  −4.5 to −5.7 (2024–25); LOW-confidence games −2.5 / −5.1.
* **No correction passes.** Split A (2024 → 2025): `linear_total` MAE
  12.77 vs 12.90 raw (−1.0%, needs −3%) and it over-shoots
  both-defenses-strong games (+3.65); `components` 12.92. Split B
  (2024+2025 → 2026): `linear_total` 12.16 = raw; `components` 12.20; and
  N = 244 < 300. **No scoring band is promoted.** Even a calibrated
  baseline would need an 80% band about 40 points wide (p10 / p90 of
  actual − baseline ≈ −19 / +22), so scoring markets stay
  `RESEARCH_UNCALIBRATED`.

## 16. The margin-authority contract

Every script that states a `home_margin` band publishes
`outcome_shape.margin_authority_evidence`: the findings that give it
permission to apply its archetype's margin definition to this game. Scripts
without a margin band publish an empty list.

**Rules** (`scripts.margin_authority_violations`):
- the list is non-empty whenever a margin band is stated;
- every code is a finding present for the game;
- none is a **forbidden sole authority**: `HIGH_/LOW_SCORING_ENVIRONMENT`, `HIGH_/LOW_POSSESSION_ENVIRONMENT`, `HOME_/AWAY_SCORING_ADVANTAGE`;
- any causal step that claims "one score" cites a non-forbidden code.

Forbidden findings stay valid football findings and may support a margin
script; they never authorise one.

**Closeness must be resolved, not inferred from a missing edge**
(`scripts.closeness_grants_margin`). The closeness findings publish the
efficiency gap and its uncertainty:
- `EVEN_MATCHUP` authorises a close margin only when |gap| + uncertainty ≤ 2.0, the strong-edge threshold. The data must rule out a decisive edge either way.
- `NARROW_EFFICIENCY_GAP` authorises one only when its edge clears its uncertainty, as the finding claims.

Otherwise the finding is still published, as information, but authorises
nothing. A shootout or grind then states no margin, a tossup (which is nothing
but a one-score claim) is not generated, and an unresolved narrow gap cannot
qualify hangs-around. This is a gate on evidence quality, not a probability.
`LOW_DATA_CONFIDENCE` labelling is unchanged.

**Enforcement**:
- `build_scripts` raises `MarginAuthorityError` rather than publish a violating script.
- `market_map.classify_against` independently refuses to classify against a margin band whose evidence is empty or contains a forbidden code. Such a contract is classified `RESEARCH_UNCALIBRATED`, never supported or contradicted.
- `tests/test_script_engine_scripts.py` sweeps every builder over every combination of up to three findings and asserts the contract, and asserts that scoring- and pace-only inputs never produce a margin band.

### 16.1 Archetype authority audit (methodology 1.3.0)

| Archetype | Created by | Margin authority evidence | Margin definition | Scoring/pace may rank or support? | Scoring/pace alone can activate the margin? |
|---|---|---|---|---|---|
| `HOME_CONTROL` / `AWAY_CONTROL` | S sustained-efficiency advantage | that advantage | S by 7–24 | yes (S scoring advantage, low possessions support) | **no** |
| `FAVORITE_PULLS_AWAY` | strong S efficiency advantage + finishing / disruption / explosive / rush / pass advantage | both | S by 17–45 | yes (S scoring advantage supports; low possessions contradicts) | **no** |
| `UNDERDOG_HANGS_AROUND` | S efficiency advantage + a non-pace counter (a narrow gap only when resolved) | advantage + counter | trailing side within −7…+8 | yes (low possessions supports; high possessions contradicts) | **no** |
| `EXPLOSIVE_UPSET` | S efficiency advantage + other side's explosive advantage | both | other side by 1–14 | no | **no** |
| `TURNOVER_DISRUPTION` | D disruption advantage + a volatility finding | both | D by 1–21 | no | **no** |
| `COMPETITIVE_SHOOTOUT` | high scoring environment or both offenses efficient; no strong edge | resolved `EVEN_MATCHUP` / `NARROW_EFFICIENCY_GAP` | ±8 only with that evidence | yes (scoring/pace create the script and rank it) | **no** |
| `COMPETITIVE_GRIND` | low scoring / both defenses control / low possessions; no strong edge | resolved `EVEN_MATCHUP` / `NARROW_EFFICIENCY_GAP` | ±8 only with that evidence | yes | **no** |
| `COMPETITIVE_TOSSUP` | resolved `EVEN_MATCHUP` | `EVEN_MATCHUP` | ±8 | yes (low possessions supports) | **no** |
| `PACE_DRIVEN_OVER`, `DEFENSIVE_SUPPRESSION` | possession / defensive environment | — | no margin band | — | — |
