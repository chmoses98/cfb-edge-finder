# CFB Phase 15 — validation of #307, early-season fix attempt, period models, Kalshi ladders (research only)

Stacks on #307 (`claude/sift-predictive-breakthrough-j1m6df`). Nothing in production imports this; no activation.
Holdout = FBS-vs-FBS 2025 (incl. postseason) + 2026 through the last 2026 kickoff in the data (2026-11-15).
CIs are 95% game-clustered bootstrap; Δ = new − baseline (negative = better for a loss). Shared metrics: `p15eval`.

## 1. Independent validation of #307
* **Exact reproduction** from vault inputs (`cfbd_api_cache` v1-292f3aac, `espn_summary_postgame` collected-20261116,
  research-data preseason cache, repo 2026 game log): `tune` re-selects λ=1, w_prev=0.1, k=3, α=1; `freeze` reproduces the
  committed layer to 3e-14; `holdout` reproduces every number in `results.json` to ≤6e-16 (n = 1,413; 24 excluded).
* **Truncation leak test on real data** (`run_p15.py leak`): 10 sampled dates per configuration (first two dates of each
  season, bowls, random mid-season; 2025 + 2026), features rebuilt with every team-game on/after the date deleted:
  185 + 162 games, max |Δ feature| = 1.8e-15, for the #307 path and for the centred + period-rating path. Synthetic
  tests (`tests/test_p15.py`) also prove the extension reduces exactly to #307 when switched off.
* **Talent / returning production timing.** The 2026 values in the vault (fetched 2026-11-16) are identical, all 138
  talent and 136 returning-production rows, to the research-data snapshot fetched **2026-08-28**, before the 2026 FBS
  opener: genuinely preseason for 2026. For 2014-2025 both copies were fetched after the fact, so timing cannot be proven.
  Sensitivity (`run_p15.py talent`): prior-season talent and no returning production, layer refit on ≤ 2024 →
  holdout margin MAE 12.47 vs 12.56 (Δ −0.08 [−0.23, +0.06]), total MAE 12.81 vs 12.81, early (<3 games) 12.55 vs 12.50.
  **The #307 gain does not depend on these inputs** (production is 14.08).
* **2026 ingestion.** 2026 rows come from the repo ESPN log, back-filled 2026-11-03..11-17 (every row observed
  ≥ 3.5 h after kickoff; scores agree with the vault summaries for 751/751 games). Strictness therefore rests on the
  `date < game date` filter (local date = UTC − 6 h; same-day games excluded), which the truncation test covers for
  2026 dates. Minor doc correction: the 2026 holdout ends at the 2026-11-15 games, not 11-17.

## 2. Early-season weakness: fix attempted, not adopted (the #307 model stays the full-game model)
Candidates fixed up front, selected on validation 2023-24 (dev 2016-22 excl. 2020), holdout scored once:
* **Prior-season ratings as ridge centre** (team effects shrunk to ρ × end-of-season S−1 rating, λ_c ∈ {3, 8} × ρ ∈
  {0.5, 0.8}): validation margin MAE 12.53-12.74 vs #307 12.50; early (n = 315) 13.00-13.12 vs 13.02. Rejected.
* Round 2 (validation only, after the grid failed): decayed end-of-prior-season gap features 12.50 / early 13.06;
  production P0 forecast as a decayed feature 12.50 / 13.04. Rejected; never scored on the holdout.
* **Week-dependent σ.** On leave-one-season-out residuals (2016-24) the margin residual SD is flat in games played
  (16.4 / 16.6 / 16.8 for 0 / 1 / 2 prior games vs 15.6-16.5 later): fitted log σ = g0 + g1·3/(3+n) gives g1 = −0.001;
  out-of-season log score const 4.2054, σ(n) 4.2058, σ(n, |μ|) 4.2059. No learnable heteroscedasticity, so the frozen
  σ(n) curve is ≈ constant and the holdout is a tie with #307 (winner log loss Δ −0.00001 [−0.00008, +0.00005]).
* Reading: the holdout early-season over-confidence (slope 0.84, n 317) is not a σ problem; in training seasons the
  early slope varies 0.72-1.49 by season. Recent seasons (2024: 0.72) hint at a transfer-portal-era shift that ≤ 2024
  data cannot yet fit. Left as an honest limit; **#307 remains the baseline and nothing here beat it.**

## 3. Period models (fitted to period outcomes) vs the naive proportional split of the #307 forecast
Derived linescores: `cfb_period_scores_2014_2026.parquet` (11,124 games, 11,121 valid; ESPN header linescores).
Model per segment (Q1-Q4, 1H, 2H regulation): team-period points = ridge on #307 team-points forecast (for/against,
out-of-season predictions in training) + walk-forward opponent-adjusted period-scoring ratings + home; distribution =
empirical PMF of the 1,500 training rows nearest in predicted mean (lumpy: 0/3/7/10/14...), home × away independent with
a fitted tie factor. Shape chosen on validation (empirical beat discretised normal in all six segments; Q1 total log loss
0.372 vs 0.395). Baseline = league share × #307 full-game mean, discretised normal with sd × √share.
Holdout ladder log loss Δ vs naive at Kalshi strikes (quarters: full listed ladder; halves: listed strikes ±10 of
the model mean), Holm over the 25 period comparisons:

| segment | spread | total | winner | tie | verdicts (matrix) |
|---|---|---|---|---|---|
| Q1 | −0.013 [−0.018, −0.009] | −0.059 [−0.069, −0.049] | −0.011 [−0.016, −0.007] | −0.100 | spread RELIABLE, total ABSTAIN (bias +0.02-0.03) |
| Q2 | −0.005 [−0.008, −0.001] (Holm 0.08) | −0.024 [−0.034, −0.015] | 0.000 | −0.042 | total RELIABLE |
| Q3 | −0.010 [−0.013, −0.006] | −0.057 [−0.067, −0.048] | −0.007 (Holm 0.07) | −0.065 | spread RELIABLE, total ABSTAIN (bias +0.03) |
| Q4 | −0.005 [−0.008, −0.002] | −0.032 [−0.041, −0.022] | −0.007 (Holm 0.10) | −0.064 | spread, total RELIABLE |
| 1H | −0.001 [−0.006, +0.003] | −0.019 [−0.027, −0.011] | −0.002 [−0.005, +0.002] | −0.019 | total RELIABLE; team total −0.015 [−0.020, −0.010] USABLE |
| 2H | −0.000 [−0.003, +0.003] | −0.019 [−0.026, −0.013] | +0.001 | −0.005 | USABLE (no spread/winner gain) |

Most of the gain is the shape: against the same means with a normal shape, the empirical PMF alone gives Q1 total
−0.046 of the −0.059. The period-specific ratings carry small weight; **1H/2H spreads and winners are not better than
the naive split** (claimed nothing there). Period CRPS (total) improves in every segment (Q1 −0.55 [−0.65, −0.46]).

## 4. Ladders at Kalshi's strikes, calibration, abstention
Full game, #307 vs production 0.5.0 (holdout, listed strikes ±14 of the mean; production total σ fitted on ≤ 2024):
winner Δ −0.057 [−0.075, −0.039] (slope 1.00 vs 0.84); spread Δ −0.060 [−0.077, −0.045] (slope 0.99 vs 0.46);
total Δ −0.041 [−0.053, −0.031]; team total Δ −0.053 [−0.063, −0.043]; margin CRPS 8.96 vs 10.03.
**Every finalized Kalshi market on holdout games** (146,024 markets, 1,453 games matched by event code / final score):
ESPN linescores reproduce Kalshi's result for 100% of FG, 1H, Q1, Q2 markets and ≥ 99.9% of Q3/Q4. **Kalshi settled
2H markets including overtime in 10/10 OT games although the rules say "excluding overtime"** (4Q excludes OT).
Scored at those exact strikes: FG winner −0.047 [−0.067, −0.027], spread −0.037 [−0.057, −0.019], total −0.046,
team total −0.036 vs production; Q1 total −0.060 [−0.080, −0.042], 1H total −0.015, 1H team total −0.021 vs naive;
1H/2H spread ≈ 0.
Abstain (from reliability bands):
* FG spread where p < 0.10 or > 0.90 (alt lines): observed 0.13 at mean p 0.06 and 0.70 at 0.93; slope 0.75 over
  all listed strikes, 0.90 inside 0.1-0.9.
* FG total and team totals entirely for now: over-probabilities too high by 5-7 pts near the mean (training residual is
  right-skewed, median −0.7; 2025-26 scoring 0.9/game below the model). Still far better than production.
* Q1/Q3 totals (+0.02-0.04 toward the over, same scoring drift), 2H ties, Q2/Q3 ties (slope 0.64), early-season
  winner extremes. Next lever: empirical total-residual shape and a recency-weighted scoring intercept.

## 5. Production integration proposal (not implemented)
* Entry point: the existing research-only shadow path in `scripts/research_scan_and_capture.py` (`_load_v2_artifact`),
  fed a frozen artifact = `frozen_params.json` (#307) + `frozen_p15.json` + `frozen_p15_period_nb.parquet`, rebuilt
  daily from games dated before the slate. Shadow first; control pricing untouched.
* Output schema compatible with `GameProjectionCache` / `CorrectedGameProjection`: `expected_margin` = home − away
  (production sign; positive favours home; sportsbook `spread_home` = −expected_margin), `expected_total`,
  home/away points = (total ± margin)/2, σ_margin, σ_total, ρ, plus per-segment joint PMFs. Kalshi "<team> wins by more
  than X" = P(team − opponent > X) from the joint PMF, both sides; paper card `shadow_projected_margin` carries the margin.
* Only families marked RELIABLE/USABLE in `matrix/CFB.json`, with the abstain bands above. Rollback: keep
  `0.5.0-early-season-talent-prior` as control (`resolve_model_version`); removing the artifact disables the shadow.

## Reproduce / limits
`python periods_data.py OUT.parquet ESPN_DIRS...`; `python run_p15.py feats|tune|prior_variants|freeze|leak|holdout|talent|kalshi`;
`python make_outputs.py MATRIX READINESS`; `pytest research/cfb_challenger/phase15/tests`.
Frozen file sha256 c05b55af… is recorded in `results_p15.json`; re-running `freeze` after the post-freeze code edits
(abbreviation columns, Kalshi matcher, a metrics dtype fix) reproduces it except `code_sha256`. Freeze and holdout ran in
one session; no holdout-dependent choice was made (centre grid lost on validation before scoring). Kalshi period series
exist only in 2026 (≤ 601 games); prices are not used anywhere and no betting edge is claimed.
