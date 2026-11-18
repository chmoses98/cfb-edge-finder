# OAE challenger forecasts: production integration (inert until approved)

**Flag:** `CFB_OAE_FORECASTS=1` (environment of the projection-foundation publish step). Any other value, or the flag unset,
means OFF. Nothing sets it today, so the workflow on main behaves exactly as before.

**Code:** `src/cfb_edge_finder/oae/`: `ratings.py` (walk-forward ridge), `forecast.py` (layer, sigma, PMFs), `block.py`,
`frozen/` (assets + `PROVENANCE.json`). Hook: `_oae_extend` in `research/projection_foundation/cfb/live/run.py::stage_publish`.
Pricing, trading, execution and settlement code is untouched. There are no new dependencies (numpy + stdlib only) and no new workflow or schedule.

## What flag ON adds
One new top-level key, `oae_challenger`, in `projections/cfb/latest.json` and its timestamped copy on `projection-data`. The block carries
`model_version: cfb-oae-1.0.0`, `status: not_activated`, `activated: false`, provenance hashes, the excluded families and a
per-game entry for FBS-vs-FBS games already in the artifact's `games`:
* `fg`: `expected_margin` (home − away), `spread_home`, `sigma_margin`, `p_home_win`/`p_away_win`, and `spread_ladder` (half-point
  strikes within ±14 of the mean, with `abstain: true` outside p ∈ [0.10, 0.90]).
* `periods`: q1 spread + winner, q2 total, q3 spread, q4 spread + total, h1 total (ladders from the empirical score PMFs).
Existing fields are never modified. The hook refuses to overwrite an existing key, and if the block fails it is logged
and the production artifact is published unchanged. Consumers read fields by name (`cfb/narrative_md.py`, `live/smoke.py`, tests).
No SIFT reader of `projections/cfb/latest.json` exists in sift-alpha.
**Not emitted:** FG total and team totals (PARTIAL), Q1/Q3 totals, 1H spread/winner, 2H and ties.

## Validation evidence (holdout 2025 + 2026 to 2026-11-15, n = 1,413 FBS-vs-FBS games, game-clustered 95% CI)
* Against frozen production 0.5.0 (`research/cfb_challenger/RESULTS.md`, `results.json` on `claude/sift-predictive-breakthrough-j1m6df`):
  margin MAE 12.56 vs 14.08 (−1.52 [−1.92, −1.14]); winner log loss −0.058 [−0.077, −0.040]; slope 0.963 vs 0.776.
* Ladders (`phase15/RESULTS_P15.md`, `results_p15.json` on `claude/sift-phase-15-sports-forecasting-rz966p`): FG spread
  −0.060 [−0.077, −0.045] (slope 0.995); winner −0.057 [−0.075, −0.039]. Periods against the naive split: Q1 spread −0.013, Q1 winner
  −0.011, Q2 total −0.024, Q3 spread −0.010, Q4 spread −0.005, Q4 total −0.032, 1H total −0.019 (all CIs < 0).
* FG total stays out: holdout bias +0.059 (slope 0.974). A Phase-16 experiment found the fix is calibration-only (log loss −0.0017),
  so totals are still not shipped.

## Pregame safety, parity, provenance
* Ratings use seasons S−2 and S−1 plus season-S team-games with local date < game date AND kickoff < forecast time.
  `training_rows` raises `PregameViolation` otherwise. Tests: `tests/test_oae.py` (truncation and poisoned-future rows).
* Parity: `tests/test_oae.py::test_parity_with_research_frozen_forecasts` reproduces the research code's forecasts for 16 2026
  games (FG mean, sigma, win prob, spread ladder, period means and ladders) to max |Δ| 4e-12.
* Frozen assets are verified by sha256 on every load (fail closed). Sources: #307 `8742f2a9`, #308 `7ebddfc6`.
  `research/oae_integration/build_frozen_assets.py` rebuilds them and the parity fixture.

## Rollback
Unset the flag (or set it to anything but `1`). The next publish writes an artifact byte-identical to main, tested in
`research/projection_foundation/tests/test_oae_hook.py`. A stale `oae_challenger` key in `latest.json` is replaced by the next publish.

## Approvals needed before activation
1. Owner approval to set `CFB_OAE_FORECASTS: "1"` in `.github/workflows/projection-foundation.yml` (run job env).
2. Owner sign-off that the block stays `not_activated` (shadow) until a prospective scorecard exists. Any use for
   pricing or trading needs a separate decision.
3. Season roll-over: the frozen inputs cover the 2026 season only. Other seasons fail closed with `skipped: no frozen inputs`.
   2027 needs rebuilt prior rows (2025-26) and a pre-opener talent/returning-production snapshot, which is a new approval.
