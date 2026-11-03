# Multi-season key-number calibration — strictly separated (2026-11-02)

Research only. Nothing here is wired into RUN CFB and nothing is promoted. Machine-readable: `multiseason_calibration_2026-11-02.json`. Protocol, split and acceptance rule: `src/cfb_edge_finder/research/multiseason_calibration.py` (committed before any result existed; one amendment, below).

## Protocol

- **TRAIN** season 2024 (647 rated FBS-vs-FBS games): every parameter fitted here only.
- **VALIDATE** season 2025 (653): candidate selection (best margin log score).
- **TEST** 2026 before Oct 17 (184): first look for the selected candidate.
- **FINAL** 2026-10-17..11-01 (163): continuity check only, never the basis of the verdict (PR #249 already reported on this block).
- Means: within-season shrunk team points for/against from strictly earlier dates (no market line, no opponent adjustment, **no havoc**).
- Rule (pre-declared): PROMOTABLE only if A–E all hold on BOTH validate and test: A key-number mass within 1.96 SE; B gap smaller than uncalibrated; C margin log-score gain CI lower bound > 0 and total/team not significantly worse; D event Brier no worse by >0.001; E beats the Normal on margin with CI lower bound > 0.
- **Amendment (recorded before any number was seen):** the first run crashed on an empty FINAL block (`records_2026` ends Oct 4) after computing but before printing; 2026 now reads `team_games.jsonl`. Rule, candidates and dates unchanged.

## Per-block scores (higher log score is better)

| block | system | margin | total | team | event Brier | key-number mass pred (obs) |
|---|---|---|---|---|---|---|
| validate (n=653) | discrete_base | -4.200 | -4.133 | -3.650 | 0.2470 | 25.1% (27.9%) |
| validate (n=653) | discrete_emp | -4.197 | -4.136 | -3.650 | 0.2480 | 28.6% (27.9%) |
| validate (n=653) | discrete_v1 | -4.198 | -4.135 | -3.653 | 0.2476 | 28.6% (27.9%) |
| validate (n=653) | discrete_v2 | -4.190 | -4.133 | -3.652 | 0.2477 | 28.9% (27.9%) |
| validate (n=653) | normal | -4.299 | -4.182 | -3.896 | 0.2481 | 15.5% (27.9%) |
| test (n=184) | discrete_base | -4.215 | -4.192 | -3.674 | 0.2464 | 25.1% (28.8%) |
| test (n=184) | discrete_emp | -4.171 | -4.198 | -3.674 | 0.2472 | 28.6% (28.8%) |
| test (n=184) | discrete_v1 | -4.211 | -4.196 | -3.677 | 0.2468 | 28.6% (28.8%) |
| test (n=184) | discrete_v2 | -4.203 | -4.195 | -3.676 | 0.2469 | 28.9% (28.8%) |
| test (n=184) | normal | -4.373 | -4.211 | -3.949 | 0.2480 | 15.5% (28.8%) |
| final (n=163) | discrete_base | -4.038 | -4.185 | -3.690 | 0.2475 | 25.3% (33.1%) |
| final (n=163) | discrete_emp | -4.023 | -4.184 | -3.689 | 0.2479 | 28.8% (33.1%) |
| final (n=163) | discrete_v1 | -4.027 | -4.186 | -3.692 | 0.2477 | 28.9% (33.1%) |
| final (n=163) | discrete_v2 | -4.019 | -4.186 | -3.691 | 0.2477 | 29.2% (33.1%) |
| final (n=163) | normal | -4.178 | -4.265 | -3.872 | 0.2484 | 15.5% (33.1%) |

## The uncalibrated discrete model vs the repository's Normal (paired, 95% bootstrap CI, per-game log-score gain)

| block | margin | total | team score |
|---|---|---|---|
| validate | +0.099 [+0.071, +0.127] | +0.049 [+0.020, +0.076] | +0.246 [+0.204, +0.287] |
| test | +0.158 [+0.106, +0.209] | +0.018 [-0.047, +0.082] | +0.275 [+0.195, +0.356] |
| final | +0.140 [+0.084, +0.199] | +0.081 [+0.014, +0.147] | +0.181 [+0.089, +0.274] |

## Selected calibrated candidate: `discrete_v2` vs the uncalibrated discrete model

| block | margin | total | team score |
|---|---|---|---|
| validate | +0.010 [+0.001, +0.019] | -0.000 [-0.002, +0.002] | -0.002 [-0.004, -0.001] |
| test | +0.012 [-0.003, +0.030] | -0.003 [-0.007, +0.002] | -0.002 [-0.004, +0.001] |
| final | +0.019 [-0.000, +0.039] | -0.001 [-0.006, +0.004] | -0.001 [-0.004, +0.002] |

| criterion | validate | test | final (continuity) |
|---|---|---|---|
| A_key_mass_within_1.96_se | True | True | True |
| B_gap_smaller_than_uncalibrated | True | True | True |
| C_margin_gain_ci_lower_gt_0_and_others_not_worse | False | False | False |
| D_event_brier_not_worse | True | True | True |
| E_beats_normal_on_margin_ci_lower_gt_0 | True | True | True |

**Verdict: `NOT_PROMOTABLE`** (calibration). Promotion: NOT_PROMOTED either way; an owner decision.

## What this says (and does not)

- **The key-number lift is not worth shipping.** Fitted on a full season the per-key lifts are modest (v2: [[3, 1.4], [7, 1.1], [10, 1.2], [14, 1.2]]) — far from the 2.6 at margin 3 that 208 games of one season produced in PR #249, which supports the suspicion that that fit was one season's lumpiness. The lifts close the aggregate key-number gap (≈25% → ≈29% predicted vs ≈28–29% observed) but add no reliable accuracy: validate margin gain is tiny, test margin gain's CI spans zero, and team-score log score gets slightly worse. Criterion C fails on both blocks.
- **The exact discrete score shape IS an out-of-sample improvement over the Normal**, with no lift: margin and team-score gains have CIs above zero on validate, test and final (three independent blocks); total-points gain is positive on validate/final but its CI spans zero on test. This is a statement about distribution quality on final scores with a rating that uses only team points. It is not a price comparison: **no market prices exist in this repository, so it does not show betting profit or CLV.**
- Fitted base shape: `fg_share=0.1` sits at the **grid lower edge** (0.10), with sigmas 0 — the grid may be too narrow; widening it is a follow-up, not done here to avoid tuning after the fact.
- Recommendation: if the owner wants the discrete shape used, the evidence supports it as the **uncalibrated base**, in shadow, alongside the current prices — not the key-number lift. Not done here.
- Limits: three seasons only (2024, 2025, 2026-to-date); naive ratings (early-season noise); the FINAL block was seen in PR #249 and the author knew the margin-3 gap before designing candidates (design bias disclosed); overtime / TD-value priors remain assumed; opponent-adjusted efficiency, injuries and special teams are **not** part of this test.
