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
Labels: `authority: research_only`, `role: shadow`, `bet_authority: false`, `operating_forecast: false`; `settlement_basis` per
family (FG includes OT; Q1-Q4 regulation only, OT excluded; 1H = Q1+Q2) and `period_winner_basis` (3-way, tie not emitted).
Existing fields are never modified. If the block cannot be built (missing/invalid input, hash mismatch, season not
covered, non-finite value, any exception) the production fields publish unchanged and `oae_challenger` is a stub with
`available: false` and `unavailable_reason`. Consumers read fields by name (`cfb/narrative_md.py`, `live/smoke.py`, tests).
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
`research/projection_foundation/tests/test_oae_hook.py`. Unset after set leaves no residue: the next `latest.json` is main's bytes (tested, same archive).

## Two-step approval
1. **Inert merge** (PR #309): code, frozen assets and tests only. The flag is set nowhere; artifacts stay byte-identical.
2. **Activation** = add `CFB_OAE_FORECASTS: "1"` to the `run` job env of `.github/workflows/projection-foundation.yml`. Preconditions:
   owner approval; season still 2026 (other seasons publish `available: false`); the activation PR's own `projection-foundation` run (a
   dry run on real data, flag ON; never exercised in CI so far) logs `publish: ... oae_challenger` with games > 0 and no
   `error`; agreement that the block stays shadow (`bet_authority: false`) until a prospective scorecard exists.
   Pricing/trading use and the 2027 roll-over (rebuilt prior rows + pre-opener talent snapshot) are separate approvals.

## Promotion to the operating CFB game forecast (step 3, separate approval)
Activation (step 2) publishes the block as a labelled **shadow**; SIFT never serves it. Promotion is two keys, and either alone changes
nothing SIFT serves:
1. **Producer key** `CFB_OAE_OPERATING=1` (exact value, with `CFB_OAE_FORECASTS=1`): the block declares `status: activated`,
   `activated: true`, `role: operating`, `operating_forecast: true`; `bet_authority` stays `false`; forecasts are byte-identical to the
   shadow block and the artifact's own `model`/`games` never change (tested in `tests/test_oae.py`).
2. **Consumer key** in sift-alpha `sift_feed/operating_models.json`: `CFB.promoted.model_version = "cfb-oae-1.0.0"`. The feed then serves
   each FBS-vs-FBS game in the block (margin, `spread_home`, win probability, period means); every other game stays unavailable.

**Activation gate (fail closed).** The producer key only *requests* promotion. `oae.block.finalize` is the single place that grants
`OPERATING_AUTHORITY`, and it recomputes the labels from scratch on every publish (forged labels on the input are discarded). It grants
them only when every check passes:
- both keys are exactly `1` and `model_version == cfb-oae-1.0.0`;
- `available: true` with no `unavailable_reason`, so the season is covered by the frozen inputs;
- at least one forecast game;
- every frozen asset is re-hashed from disk (`frozen.verify_all`, uncached) and matches `PROVENANCE.json` and the block's provenance;
- strict JSON with finite numbers;
- every `p_*` lies in [0, 1], and each game has a complete full-game forecast with `p_home_win + p_away_win = 1`,
  `spread_home = -expected_margin`, `sigma_margin > 0`, and period winner probabilities summing to at most 1.

A block that fails any check is published with the non-operating labels and a `promotion_withheld` list of reasons. The
`unavailable_block` stub (missing or invalid input, build exception, frozen-hash mismatch, non-finite output) is **always**
non-operating. A missing, invalid, unsupported-season or hash-failed forecast can therefore never be `activated` or
`operating_forecast`. This is tested in `tests/test_oae.py` and `research/projection_foundation/tests/test_oae_hook.py`, where every
failure path also runs with both keys set.

Incumbent / fallback: CFB production publishes **no** model projections today, so the fallback is "no operating CFB forecast" (the
feed's current `unavailable` status), not a different model. Rollback (immediate on the next publish): unset `CFB_OAE_OPERATING`, or revert
the manifest entry.
