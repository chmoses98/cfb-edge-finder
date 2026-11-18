# CFB challenger v1 — opponent-adjusted efficiency vs frozen production (research only)

**Verdict: the challenger beats the frozen production model on every game-outcome metric on the untouched
2025 + 2026-to-date holdout (n = 1,413 identical FBS-vs-FBS games). No production path was changed.**

## Production reference (what "production" means here)
* Frozen production = `0.5.0-early-season-talent-prior` exactly as deployed (Wave-2E "P0"): ridge efficiency
  ratings from the four PRIOR seasons only, C.2 margin correction, talent delta, deployed normal pricer
  (P(margin > 0), 0.5 continuity correction). No archived pre-game probabilities exist, so it is the
  committed production-entry-point replay (`data/scripting/validation/rush_projection/p0_games_2014_2025.jsonl.gz`)
  plus 2026: archived rows weeks 1-6 (`base_repair/games_2026.jsonl.gz`) and the same `GameProjectionCache`
  object rebuilt for later weeks. Rebuild reproduces the 279 archived 2026 rows to 1e-13 (margin, total).
* Its largest recoverable weakness: it never ingests the current season, so ratings are stale from week 1 on.

## Challenger
* One consistent source 2014-2026: ESPN postgame summaries (vault) parsed with the repo's own production parser
  (`scripting.espn.team_games_from_summary`); 2026 from the repo ESPN game log (same parser).
* Before every game DATE: weighted ridge, offence and defence separately, on points, points/drive, success rate,
  yards/play, plays and drives, using only team-games with date < game date; prior seasons carried at weight
  `w_prev`; FCS opponents pooled into one entity; home field fitted per metric.
* Game layer: linear ridge from adjusted gaps (+ talent and returning production scaled by k/(k+games played),
  i.e. a preseason prior that decays) to margin and total; team totals = (total ± margin)/2; probit win prob.
* Splits: dev 2016-22 (2020 excluded as target), validation 2023-24 (grid: λ, w_prev, k, α), layer refit on
  2016-24, frozen and committed (`8742f2a9`) BEFORE the holdout was scored once. No line/market column is read.

## Holdout (2025 incl. postseason + 2026 through 2026-11-17), challenger − production, game-clustered 95% CI

| metric | n | production | challenger | diff [95% CI] |
|---|---|---|---|---|
| margin MAE | 1413 | 14.08 | 12.56 | −1.52 [−1.92, −1.14] |
| total MAE | 1413 | 13.62 | 12.81 | −0.81 [−1.08, −0.53] |
| team-points MAE | 1413 | 9.92 | 8.99 | −0.93 [−1.11, −0.75] |
| winner Brier | 1413 | 0.2109 | 0.1861 | −0.0248 [−0.0320, −0.0178] |
| winner log-loss | 1413 | 0.6095 | 0.5513 | −0.0582 [−0.0766, −0.0397] |
| calibration slope | 1413 | 0.776 | 0.963 | +0.187 [+0.082, +0.299] |

By season: 2025 (n 793) margin −1.83 [−2.37, −1.30], Brier −0.033; 2026 (n 620) margin −1.13 [−1.69, −0.58],
total −1.08 [−1.51, −0.66], Brier −0.014 [−0.026, −0.004], slope 0.93 vs 0.78 (CI crosses 0).

Reference (not the frozen model): production stack WITH in-season ingestion ("B1", 2025 only, n 793):
challenger still better — margin −1.37 [−1.85, −0.88], total −0.44 [−0.77, −0.12], Brier −0.025.

## What failed / limits (reported as found)
* **Early season is not won.** Games where teams average < 3 prior games (n 317): margin −0.57 [−1.38, +0.20],
  total −0.10 [−0.62, +0.37], Brier −0.012 [−0.023, +0.001] — not significant, and the challenger is
  over-confident there (slope 0.84 vs production 1.17). The single global σ is too small in weeks 1-3.
* The validation optimum sat at the grid edge (λ = 1, w_prev = 0.1) twice; the surface is flat
  (margin MAE 12.50-12.63 across the grid), so this is not a strong effect. `w_prev2` was left at its default.
* 24 holdout FBS-vs-FBS games have no production forecast (new-FBS 2026 teams unknown to production,
  orientation mismatches) and are excluded from both sides.
* Prior research (`research/projection_foundation`, G model) already reached similar accuracy on 2023-25
  vs the retired engine; this module is an independent, single-source, date-strict replication of that gain
  scored on a holdout that includes 2026, not a new accuracy frontier.
* Player forecasts were not attempted.

## Reproduce
```
python research/cfb_challenger/run_challenger.py data|tune|freeze|holdout   # inputs under /tmp/cfb_work
python -m pytest -q research/cfb_challenger/tests
```
Files: `frozen_params.json`, `results.json`, `challenger/{data,model,production,evaluate}.py`.

## Next lever
Week-dependent uncertainty and a stronger week 0-3 prior (prior-season adjusted ratings as an explicit ridge
centre instead of down-weighted games, plus coaching/QB change flags), since the early-season slice is where
neither model is reliable and where the gain did not reach significance.

## Phase 15 independent validation
Reproduced exactly from vault inputs, leak-tested on real data, talent-timing sensitivity and period models:
see `phase15/RESULTS_P15.md` (stacked branch `claude/sift-phase-15-sports-forecasting-rz966p`).
