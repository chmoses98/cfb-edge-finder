# CFB MODEL V2 — RESEARCH REPORT

Research branch: `claude/cfb-model-v2-research-krupoc`. Production `0.5.0-early-season-talent-prior` is unchanged and remains the live, frozen benchmark. No 2026 outcome was used to fit or select anything in this report (see §3 and §6 for the firewall).

## V2 VERDICT

_(filled in §20–§22 below; the verdict line is restated at the end of the report)_

---

## 1. Why V1 / 0.5.0 is limited

Code-verified trace of the live path (`research_scan_and_capture.py` → `football_state` cache → `GameProjectionCache` → `fit_fbs_efficiency_ratings` → `build_expanding_residual_pool` → `project_game` → `apply_margin_correction` + talent delta → `to_game_distribution` → `projections.distribution` normal CDF → `ladder_pricing`). What the model actually knows at kickoff of a 2026 game:

| Input | Live status |
|---|---|
| Scores and play counts, 2022–2025 FBS games | used, **pooled with equal weight across the four seasons** (no recency decay) |
| 2026 completed games | **never enter the ratings** — `history_seasons` is fixed to 2022–2025, so the model does not learn during 2026 at all |
| Talent composite (2026) | margin shift only, `0.018993 × Δtalent` |
| Neutral-site flag | correct (HFA forced to 0) |
| Returning production, coaching change, recruiting, conference, rest, travel, weather, QB identity, injuries | not used (`percent_passing_ppa` is `None` live, so every game gets the same 1.20× "unknown QB" inflation) |

Structural findings that motivate a rebuild rather than another patch:

1. **Season carryover is inert.** `games_played` counts all corpus rows (≈50 per team), so the "prior-season blend" weight is ≈0.93 at Week 1 and `prior_season_ratings=None` is passed live. A 2026 Week-1 rating is a four-season equal-weight pooled fit shrunk 7% toward zero. This is the mechanism behind the documented Week-1 margin compression that `a=1.34` in the margin correction and the talent prior both partially compensate for.
2. **No in-season learning in 2026** (above). Weeks 2–15 are priced from the same pooled 2022–2025 fit as Week 1, plus the talent delta.
3. **The live moneyline is the closed-form Normal on the corrected mean**, not the validated empirical simulated win probability (`prob_home_win()` is never called on the Kalshi path). The continuity correction is applied on top of Kalshi's half-point strikes (`P(margin > 4.5)` is priced as `1 − Φ((5.0 − μ)/σ)`), a systematic ≈1-point-of-probability shift against every YES rung near the mean.
4. **Provenance claims `calibration=platt`; no probability calibration runs live.** No config hash is stamped on canonical rows.
5. **Uncertainty is nearly constant**: `EARLY_SEASON_UNCERTAINTY_SCALE` contributes 1.02× instead of the intended 1.30×, and the residual pool is one global bootstrap pool scaled by fixed multipliers.
6. `seed=0` is shared across all games in a week, so simulated residual draws are rank-coupled across games.
7. Totals were never repaired: Week-1 total over-projection (+2.9) and ECE 0.052.

Reproduced control on the model-repair inputs (2021–2025, FBS-vs-FBS, 4,000 sims, live parity settings — `scripts/v2_control_predictions.py`): Week-1 margin MAE 15.96 / bias −6.3 (0.4.0) and 14.12 / −4.0 (0.5.0), Weeks 4+ 13.8–14.4 depending on season. These match the documented numbers.

## 2. Free data inventory

Acquired on a GitHub-hosted runner by `scripts/fetch_v2_research_cache.py` (workflow `preseason-research-fetch.yml`, `mode=v2`), read-only, whole-season pulls. Persisted gzipped on the `research-data` branch under `data/research_cache/v2/` (25.6 MB, 219 files, manifest with per-endpoint row counts, schema fingerprints, timing notes and the full call log).

Cost: **422 metered CFBD calls** in one run (`/info` unmetered before/after: 768 → 358 remaining of 1,000 for September). `/games/teams` rejects year-only requests (HTTP 400) and fell back to per-week pulls (17 calls/season), which is where 204 of the 422 went. **Operational flag: 358 calls remain for the rest of September; the collector's own usage is ≈15–25/day (heartbeat-observed), so the month is tight. No further quota was spent by this research.**

| Endpoint | Seasons | Rows | Timing class | V2 use |
|---|---|---|---|---|
| `/games` | 2014–2026 | 11,262 | scores POSTGAME (targets); schedule/venue/neutral/conference/**pregame Elo** pregame | targets, situational, external Elo benchmark |
| `/stats/game/advanced` (regular, postseason, garbage-time-excluded) | 2014–2025 | 50,464 | POSTGAME for its own game | rolling efficiency state (strictly prior games only) |
| `/games/teams` (box score) | 2014–2025 | 10,369 | POSTGAME | rolling box state (turnovers, havoc, 3rd down, penalties) |
| `/drives` | 2014–2025 | 258,557 | POSTGAME | finishing drives (points per opportunity), field position, tempo |
| `/lines` | 2014–2025 | 12,761 | pregame but market-derived | **EVALUATION ONLY** |
| `/rankings` | 2014–2026 | 185 weeks | week-1 poll = preseason poll (verified: 2024 wk1 top-5 = Georgia, Ohio State, Oregon, Texas, Alabama; wk2 differs) | preseason poll points |
| `/teams/fbs` | 2014–2026 | 1,704 | pregame | season-scoped conference, venue location/elevation/timezone |
| `/recruiting/teams` | 2014–2026 | 2,790 | signed in the prior cycle | 4-class recruiting average |
| `/talent` | 2015–2026 | 2,413 | settled in prior cycle (2014 absent) | talent composite |
| `/player/returning` | 2014–2026 | 1,691 | preseason S, describes S−1 | continuity features |
| `/coaches` | 2014–2026 | 1,839 | identity only (W/L dropped) | coaching change, tenure |
| `/stats/season/advanced` | 2014–2025 | 1,566 | POSTGAME for S; **S−1 only** | not used by the winner (state already covers it) |
| `/ratings/sp` | 2014–2025 | 1,578 | END-OF-SEASON S; **S−1 only** | tested, no incremental value (§8) |
| `/player/portal` | 2021–2026 | 18,892 | `transferDate` is an event date; ratings/destinations may be revised | **descriptive only, not a feature** (timing unproven) |
| `/venues` | static | 852 | static | travel distance, altitude, dome |

Classified UNAVAILABLE or unsafe and not used: roster/QB identity (retroactively revised), injuries (no structured source), pregame weather forecasts (only realised conditions exist), `/plays` (too expensive for the free tier and not needed), CFBD weekly `/ratings/elo` (redundant with the `/games` pregame Elo). The AP/Coaches poll and the `/games` pregame Elo are the two externally-computed pregame signals; both are timing-safe by construction but Elo is treated as an external benchmark/optional input rather than a core V2 dependency because its live availability for upcoming games is not guaranteed (only 245 of 888 2026 schedule rows carried it at fetch time).

## 3. Final historical dataset

`scripts/v2_build_dataset.py` → `data/research_cache/v2_work/dataset.parquet` (+ `.meta.json` with `dataset_version`, state config, cache `fetched_at`, feature-column hash). Build time ≈50 s from the cache; no CFBD calls.

* One row per FBS-involved game, 2015–2025 completed (10,393 rows incl. 888 masked 2026 schedule rows; 8,325 FBS-vs-FBS completed rows are the evaluation population), postseason included with the production `postseason_week_rank` ordering.
* Team keys are CFBD school names (consistent across every endpoint); `home_slug`/`away_slug` map to the production registry ids (bare "Miami" → `miami-fl` explicitly; the production resolver refuses it and V1 therefore cannot rate Miami (FL) games — 72 of them over 2019–2025).
* **Targets**: home/away points, margin, total, home_won. **2026 targets are NaN by construction** — the builder never reads a 2026 score.
* **Team state** (231 pregame feature columns): for every distinct (season, week) as-of, one weighted ridge solve with a multi-column right-hand side gives opponent-adjusted offense/defense strengths for 26 metrics (scoring, PPA, success rate, explosiveness, pass/rush, standard/passing downs, line yards, plays, drives points-per-drive, points-per-opportunity, turnover rate, three-and-outs, starting field position, seconds per play, third-down rate, penalties, havoc, sacks, takeaways) from games **strictly before** the as-of, with season-decay weights and a pooled FCS pseudo-team. Week-1 rows have zero current-season games by assertion.
* **Preseason** (dated S−1 or earlier): talent, recruiting classes S…S−3, returning production splits, coaching change/tenure, prior-season and two-seasons-ago single-season strengths and record, end-of-S−1 SP+, preseason AP/Coaches poll points, FBS-newcomer flag.
* **Situational**: neutral, conference game, week, postseason, rest days, travel distance (team home venue → game venue), venue elevation/dome, kickoff hour, FCS opponent.
* **Market (evaluation only, `mkt_*`)**: consensus closing spread (home-margin convention), total, vig-removed moneyline probability, opening spread; never a feature.

Sanity checks recorded in the build log: efficiency differentials correlate 0.55–0.58 with margin, points-per-opportunity 0.52, CFBD Elo difference 0.60, market spread 0.66.

## 4. Feature families

| Family | Columns (per side, home and away) | Source |
|---|---|---|
| Structural strength | opponent-adjusted margin strength, scoring offense/defense, league HFA (season-decayed walk-forward ridge) | `/games` |
| Efficiency state | PPA, success rate, explosiveness, pass/rush PPA and success, standard/passing-down success, line yards, plays, garbage-time-excluded PPA/SR | `/stats/game/advanced` |
| Drive state | points per drive, points per opportunity, turnover rate, three-and-out rate, average starting yards-to-goal, seconds per play | `/drives` |
| Box state | third-down rate, penalty yards, havoc (TFL+PD+INT), sacks, takeaways | `/games/teams` |
| Evidence | games this season, decay-weighted games, `early_w = exp(−min_games/4)` | derived |
| Preseason | talent, 4-class recruiting average and current class, returning PPA/passing/rushing/receiving/usage, coach change and tenure, S−1 and S−2 single-season margin strength, S−1 points for/against and win %, S−1 SP+ (overall/off/def), preseason AP and Coaches points, FBS newcomer | preseason endpoints |
| Early-season interactions | preseason differential × `early_w` for talent, recruiting, prior strength, SP+, poll, returning PPA, returning passing, coach change (plus k=2 and k=8 decay variants) | derived |
| Situational | week, postseason, conference game, rest difference and short-week flags, travel distance (difference and away), venue elevation and dome, kickoff hour | `/games`, `/teams/fbs`, `/venues` |
| Long-memory strength (`_L`) | margin, scoring and PPA strengths with a slow (0.85) season decay | derived |
| External | CFBD pregame Elo difference | `/games` |

Matchup transforms follow the state's sign convention: for metric m, home expectation = off_h − def_a, away expectation = off_a − def_h; `diff_m` (home − away) drives margin, `sum_m` drives total. Named feature sets (hashed) are in `research/v2/features.py`: `struct` (3), `struct+pre` (38), `eff` (32), `eff+pre` (66), `eff+pre+sit` (77), `full` (150), `full+elo` (151), `elo_only` (2), total-oriented `tot_struct`, `tot_eff`, `tot_eff+pre`, `tot_full`, plus the ablation and long-memory sets.

## 5. Model families tested

* Baselines: zero model (training means), **closing market consensus** (evaluation only), CFBD-Elo-only ridge, structural-only ridge.
* Ridge regression (standardised, alpha chosen on the inner validation season from {1, 10, 100, 1000}) on every feature set, for margin, total, and separate home/away points.
* LightGBM regression (L2 and L1 objectives; early-stopped on the inner validation season, refit on all training seasons at the chosen iteration; a small-tree variant).
* Ridge + LightGBM residual stack.
* Winner classifiers: logistic regression and LightGBM classifier on `full`.
* Training-row recency weights (0.85, 0.70 per season), FBS-vs-FCS rows in training, chronological affine recalibration, and NNLS chronological ensembles.
* Team-state hyper-parameters as dataset variants: season decay 0.20–0.70, ridge strength 3–12, long-memory features.

## 6. Chronological validation design

Rolling origin, declared before any candidate ran: test seasons **2017, 2018, 2019, 2021, 2022, 2023, 2024, 2025**; every fold trains on all completed FBS-vs-FBS games with season < Y (2015 onward), selects hyper-parameters on season Y−1 only, refits on all seasons < Y, and predicts every FBS-vs-FBS game of Y. 2020 is never a test season (COVID scheduling) but remains training evidence. 2015–2016 are training-only. The control comparison (§19) uses the 3,675 games of 2021–2025 the model-repair mission also evaluated. Every fold is reported separately (§7, §16); Week 1, Weeks 1–3, Weeks 4+, postseason, neutral and conference/non-conference segments are reported for every candidate in `registry.jsonl`.

Uncertainty: paired per-game (or per-contract, game-clustered) differences with a 2,000-resample bootstrap over whole games; "improves/degrades" means the 95% interval excludes zero.

_(sections 7–22 follow)_
