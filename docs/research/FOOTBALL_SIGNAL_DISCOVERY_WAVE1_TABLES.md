# Football Signal Discovery Wave 1 (CFB) — generated tables

Generated from `evaluation_report.json` (code `a3277a0e8`, set 1 `8854fbcdd55c`, set 2 `babd3650f083`, run 2026-10-08T18:54:38+00:00). Do not edit by hand: `python3 scripts/signal_discovery_cfb_tables.py`.

Rows: 8786 eligible games 2014–2025; with spread 8776, total 8680, moneyline 3601 (2021+). Exclusions: {'FEATURE_NO_PRIOR_HISTORY_AWAY': 553, 'FEATURE_NO_PRIOR_HISTORY_BOTH': 982, 'FEATURE_NO_PRIOR_HISTORY_HOME': 48}.

Hypotheses in the FDR families: football 33, market 32.

## 1. Every hypothesis (all seasons; set-2 rows are discovery-contaminated on block A — see §2)

| Id | Status | Football effect | q (BH) | Market effect | q (BH) | Holm p (market) | Name |
|---|---|---|---|---|---|---|---|
| CFB-SIG-001 | MARKET_WATCH | +0.304 | 0.000 | +0.023 | 0.048 | 0.351 | CONTROL (any tier) -> winner / moneyline |
| CFB-SIG-002 | APPROX_EFFICIENT | +0.235 | 0.000 | +0.692 | 0.149 | 1.000 | MODERATE CONTROL -> closing spread (ATS) |
| CFB-SIG-003 | APPROX_EFFICIENT | +0.357 | 0.000 | +0.380 | 0.369 | 1.000 | STRONG CONTROL -> closing spread (ATS) |
| CFB-SIG-004 | MARKET_WATCH | -0.532 | 0.076 | +0.631 | 0.041 | 0.253 | CONTROL x PACE (control-side margin and ATS vs possession environment) |
| CFB-SIG-005 | FOOTBALL_VALIDATED | +0.326 | 0.000 | +0.020 | 0.241 | 1.000 | CONTROL x DEFENSIVE SUPPRESSION (opponent passing offense outclassed) -> winner |
| CFB-SIG-006 | REJECTED | -0.082 | 0.000 | +0.162 | 0.777 | 1.000 | CLOSENESS -> market underdog ATS |
| CFB-SIG-007 | REJECTED | -0.132 | 0.000 | -0.819 | 0.340 | 1.000 | EVEN MATCHUP x LOW PACE -> market underdog ATS |
| CFB-SIG-008 | APPROX_EFFICIENT | +6.955 | 0.000 | +0.271 | 0.695 | 1.000 | PACE claim HIGH -> over |
| CFB-SIG-009 | APPROX_EFFICIENT | +6.290 | 0.000 | +0.089 | 0.877 | 1.000 | PACE claim LOW -> under |
| CFB-SIG-010 | APPROX_EFFICIENT | +4.244 | 0.000 | -0.007 | 0.968 | 1.000 | Possession environment (continuous) -> total residual |
| CFB-SIG-011 | APPROX_EFFICIENT | +9.882 | 0.000 | -1.025 | 0.220 | 1.000 | DEFENSIVE SUPPRESSION claim -> under |
| CFB-SIG-012 | APPROX_EFFICIENT | +8.302 | 0.000 | -0.312 | 0.598 | 1.000 | SCORING ENVIRONMENT ELEVATED -> over |
| CFB-SIG-013 | APPROX_EFFICIENT | +8.925 | 0.000 | -0.651 | 0.220 | 1.000 | SCORING ENVIRONMENT SUPPRESSED -> under |
| CFB-SIG-014 | OVERPRICED | -0.312 | 0.466 | -1.237 | 0.006 | 0.025 | Passing matchup net, beyond sustained efficiency -> margin / ATS |
| CFB-SIG-015 | MARKET_WATCH | +1.814 | 0.000 | +1.396 | 0.000 | 0.001 | Rushing matchup net, beyond sustained efficiency -> margin / ATS |
| CFB-SIG-016 | MARKET_WATCH | +0.324 | 0.000 | +0.673 | 0.048 | 0.351 | RUSH EDGE x CONTROL (same-side rushing net >= 1) -> ATS |
| CFB-SIG-017 | APPROX_EFFICIENT | +0.240 | 0.000 | +0.586 | 0.340 | 1.000 | DISRUPTION x WEAK PROTECTION -> disruption side ATS |
| CFB-SIG-018 | DATA_UNAVAILABLE | — | — | — | — | — | EXPLOSIVE advantage |
| CFB-SIG-019 | APPROX_EFFICIENT | +0.149 | 0.000 | +0.361 | 0.598 | 1.000 | FINISHING edge without efficiency edge -> ATS |
| CFB-SIG-020 | APPROX_EFFICIENT | +12.074 | 0.000 | +0.282 | 0.214 | 1.000 | Sustained-efficiency net (continuous) -> margin / ATS |
| CFB-SIG-021 | REJECTED | -10.434 | 0.000 | -0.043 | 0.877 | 1.000 | Scoring-baseline margin vs closing spread disagreement -> ATS |
| CFB-SIG-022 | REJECTED | -3.483 | 0.000 | +0.185 | 0.436 | 1.000 | Scoring-baseline total vs closing total disagreement -> total residual |
| CFB-SIG-023 | DISCOVERY_ONLY | +0.085 | 0.173 | +5.250 | 0.041 | 0.241 | STRONG CONTROL, market skeptical (control side favoured < 3 or underdog) |
| CFB-SIG-024 | REJECTED | +0.031 | 0.387 | +0.368 | 0.823 | 1.000 | MODERATE CONTROL, market skeptical (control side favoured < 3 or underdog) |
| CFB-SIG-025 | REJECTED | -5.654 | 0.000 | +0.007 | 0.968 | 1.000 | Home offense scoring baseline vs DERIVED implied home team total |
| CFB-SIG-026 | FOOTBALL_VALIDATED | +9.065 | 0.000 | — | — | — | CONTROL -> first-half margin (football only) |
| CFB-SIG-027 | DATA_UNAVAILABLE | — | — | — | — | — | TREND / FORM (improving or declining opponent-adjusted efficiency) |
| CFB-BASE-001 | BASELINE_REFERENCE | -0.000 | — | -0.283 | — | — | BASELINE: home team ATS |
| CFB-BASE-002 | BASELINE_REFERENCE | +0.221 | — | +0.144 | — | — | BASELINE: market favourite ATS |
| CFB-BASE-003 | BASELINE_REFERENCE | +9.827 | — | +0.080 | — | — | BASELINE: RAW yards/play net (unadjusted) -> margin / ATS |
| CFB-DSC-001 | MARKET_WATCH | +7.639 | 0.000 | +0.762 | 0.000 | 0.000 | Rush-defense quality edge -> home ATS residual |
| CFB-DSC-002 | MARKET_WATCH | +10.809 | 0.000 | +0.619 | 0.002 | 0.008 | Production rushing net (unconditional) -> home ATS residual |
| CFB-DSC-003 | MARKET_WATCH | -1.989 | 0.000 | -0.618 | 0.002 | 0.008 | Pass-rate tendency difference -> home ATS residual (negative) |
| CFB-DSC-004 | MARKET_WATCH | +4.278 | 0.000 | -0.823 | 0.041 | 0.235 | Within STRONG CONTROL: control-side scoring-offense advantage -> control-side ATS (negative) |
| CFB-DSC-005 | APPROX_EFFICIENT | +0.341 | 0.000 | +0.915 | 0.149 | 1.000 | STRONG CONTROL with low scoring visibility (control offense ppg-quality edge <= 1.5 SD) -> ATS |
| CFB-DSC-006 | REJECTED | -3.498 | 0.000 | +0.146 | 0.526 | 1.000 | Efficiency net minus scoring net -> home ATS residual |
| CFB-DSC-007 | REJECTED | +1.897 | 0.000 | -0.230 | 0.340 | 1.000 | Combined pass-rate tendency -> total residual (under) |
| CFB-DSC-008 | MARKET_WATCH | +0.812 | 0.038 | +0.754 | 0.097 | 0.734 | Within MODERATE CONTROL: success-rate matchup net -> control-side ATS |

Effects: SIDE = win-rate lift (football) and mean ATS residual in points or (win − no-vig implied) for ML; TOTAL = points vs season mean / total residual (direction-signed); SLOPE = points per feature SD.

## 2. Set 2 on block B only (the decisive sample)

| Id | Status | Market effect | q (BH, within set 2) | n |
|---|---|---|---|---|
| CFB-DSC-001 | MARKET_WATCH | +0.691 | 0.026 | 4299 |
| CFB-DSC-002 | MARKET_WATCH | +0.561 | 0.073 | 4301 |
| CFB-DSC-003 | MARKET_WATCH | -0.502 | 0.082 | 4301 |
| CFB-DSC-004 | REJECTED | -0.305 | 0.723 | 1144 |
| CFB-DSC-005 | APPROX_EFFICIENT | +0.112 | 0.873 | 509 |
| CFB-DSC-006 | REJECTED | +0.435 | 0.118 | 4301 |
| CFB-DSC-007 | REJECTED | +0.118 | 0.723 | 4301 |
| CFB-DSC-008 | REJECTED | +0.284 | 0.723 | 880 |

## 3. Slope hypotheses: market residual detail

| Id | n | β/SD (resid) | SE | p | Block A β (p) | Block B β (p) | seasons expected sign | LOSO range | Follow |z|≥1: n, cover, ROI@−110 |
|---|---|---|---|---|---|---|---|---|---|
| CFB-BASE-003 | 8776 | +0.080 | 0.167 | 0.6323 | +0.157 (0.513) | -0.001 (0.997) | 58.3% | -0.028…+0.169 | 2321, 49.9%, -4.8% |
| CFB-DSC-001 | 8761 | +0.762 | 0.168 | 0.0000 | +0.830 (0.001) | +0.689 (0.003) | 100.0% | +0.701…+0.813 | 2713, 52.8%, 0.7% |
| CFB-DSC-002 | 8776 | +0.619 | 0.171 | 0.0003 | +0.675 (0.006) | +0.558 (0.018) | 91.7% | +0.548…+0.688 | 2581, 52.1%, -0.6% |
| CFB-DSC-003 | 8776 | -0.618 | 0.170 | 0.0003 | -0.689 (0.003) | -0.529 (0.031) | 83.3% | -0.720…-0.539 | 2361, 52.1%, -0.5% |
| CFB-DSC-004 | 2373 | -0.823 | 0.314 | 0.0087 | -1.330 (0.002) | -0.302 (0.498) | 91.7% | -1.049…-0.710 | 713, 53.6%, 2.4% |
| CFB-DSC-006 | 8776 | +0.146 | 0.166 | 0.3778 | -0.146 (0.550) | +0.426 (0.059) | 50.0% | +0.071…+0.239 | 2695, 51.4%, -1.9% |
| CFB-DSC-007 | 8680 | -0.230 | 0.179 | 0.1984 | -0.536 (0.029) | +0.126 (0.633) | 66.7% | -0.323…-0.158 | 2237, 51.1%, -2.5% |
| CFB-DSC-008 | 1834 | +0.754 | 0.354 | 0.0334 | +1.273 (0.015) | +0.273 (0.572) | 83.3% | +0.614…+0.857 | 556, 54.2%, 3.5% |
| CFB-SIG-004 | 4207 | +0.631 | 0.246 | 0.0101 | +0.695 (0.042) | +0.548 (0.118) | 66.7% | +0.513…+0.785 | 1334, 52.6%, 0.5% |
| CFB-SIG-010 | 8680 | -0.007 | 0.179 | 0.9681 | +0.150 (0.557) | -0.177 (0.475) | 58.3% | -0.111…+0.121 | 2764, 49.7%, -5.2% |
| CFB-SIG-014 | 8770 | -1.237 | 0.372 | 0.0009 | -1.043 (0.047) | -1.449 (0.006) | 16.7% | -1.441…-1.054 | 2591, 50.2%, -4.1% |
| CFB-SIG-015 | 8776 | +1.396 | 0.325 | 0.0000 | +1.435 (0.002) | +1.356 (0.003) | 83.3% | +1.306…+1.538 | 2581, 52.1%, -0.6% |
| CFB-SIG-020 | 8776 | +0.282 | 0.168 | 0.0938 | +0.340 (0.163) | +0.221 (0.341) | 58.3% | +0.206…+0.343 | 2389, 51.1%, -2.5% |
| CFB-SIG-021 | 8776 | -0.043 | 0.166 | 0.7973 | -0.427 (0.068) | +0.349 (0.139) | 41.7% | -0.111…+0.033 | 2130, 50.3%, -4.0% |
| CFB-SIG-022 | 8680 | +0.185 | 0.178 | 0.2997 | +0.198 (0.441) | +0.173 (0.485) | 50.0% | +0.084…+0.291 | 2530, 48.5%, -7.4% |
| CFB-SIG-025 | 8680 | +0.007 | 0.126 | 0.9572 | -0.099 (0.586) | +0.102 (0.563) | 50.0% | -0.043…+0.040 | 2271, 49.0%, -6.5% |

## 4. Side hypotheses: football, ATS and moneyline

| Id | n | Win | Lift | ATS n | W-L-P | Cover [95%] | Mean resid (p) | ROI@−110 | ML n | Win − implied (p) | ML ROI [95%] |
|---|---|---|---|---|---|---|---|---|---|---|---|
| CFB-BASE-001 | 8125 | 59.1% | -0.000 | 8115 | 3870-4091-154 | 48.6% [47.5%, 49.7%] | -0.28 (0.103) | -7.2% | 3311 | +0.008 (0.305) | -2.9% [-6.9%, 1.2%] |
| CFB-BASE-002 | 8762 | 74.3% | +0.221 | 8762 | 4257-4343-162 | 49.5% [48.4%, 50.6%] | +0.14 (0.390) | -5.5% | 3596 | +0.004 (0.614) | -2.9% [-5.1%, -0.7%] |
| CFB-DSC-005 | 1058 | 85.2% | +0.341 | 1055 | 537-501-17 | 51.7% [48.7%, 54.8%] | +0.92 (0.060) | -1.2% | 416 | +0.031 (0.087) | 1.2% [-4.0%, 6.9%] |
| CFB-SIG-001 | 4216 | 81.5% | +0.304 | 4207 | 2094-2032-81 | 50.8% [49.2%, 52.3%] | +0.52 (0.034) | -3.1% | 1637 | +0.023 (0.015) | 0.7% [-2.3%, 3.7%] |
| CFB-SIG-002 | 1834 | 73.5% | +0.235 | 1834 | 921-869-44 | 51.5% [49.1%, 53.8%] | +0.69 (0.060) | -1.8% | 798 | +0.026 (0.092) | 2.2% [-2.7%, 7.1%] |
| CFB-SIG-003 | 2382 | 87.7% | +0.357 | 2373 | 1173-1163-37 | 50.2% [48.2%, 52.2%] | +0.38 (0.242) | -4.1% | 839 | +0.021 (0.075) | -0.8% [-4.0%, 2.5%] |
| CFB-SIG-005 | 2258 | 84.3% | +0.326 | 2250 | 1088-1116-46 | 49.4% [47.3%, 51.5%] | +0.02 (0.943) | -5.8% | 827 | +0.020 (0.128) | 0.1% [-3.9%, 4.1%] |
| CFB-SIG-006 | 1798 | 39.0% | -0.082 | 1798 | 902-860-36 | 51.2% [48.9%, 53.5%] | +0.16 (0.656) | -2.3% | 850 | +0.007 (0.660) | -1.5% [-9.7%, 6.8%] |
| CFB-SIG-007 | 532 | 33.5% | -0.132 | 532 | 249-273-10 | 47.7% [43.4%, 52.0%] | -0.82 (0.208) | -8.9% | 235 | -0.045 (0.137) | -17.8% [-32.7%, -2.1%] |
| CFB-SIG-016 | 3266 | 83.8% | +0.324 | 3257 | 1636-1565-56 | 51.1% [49.4%, 52.8%] | +0.67 (0.015) | -2.4% | 1240 | +0.029 (0.006) | 1.5% [-1.7%, 4.9%] |
| CFB-SIG-017 | 1053 | 75.0% | +0.240 | 1051 | 513-521-17 | 49.6% [46.6%, 52.7%] | +0.59 (0.212) | -5.3% | 549 | +0.020 (0.258) | 1.2% [-5.0%, 7.7%] |
| CFB-SIG-019 | 998 | 65.7% | +0.149 | 998 | 492-494-12 | 49.9% [46.8%, 53.0%] | +0.36 (0.467) | -4.7% | 399 | +0.004 (0.876) | -2.4% [-10.4%, 5.2%] |
| CFB-SIG-023 | 70 | 55.7% | +0.085 | 70 | 42-27-1 | 60.9% [49.1%, 71.5%] | +5.25 (0.009) | 16.2% | 32 | +0.025 (0.787) | 5.3% [-34.0%, 47.4%] |
| CFB-SIG-024 | 207 | 47.8% | +0.031 | 207 | 106-98-3 | 52.0% [45.1%, 58.7%] | +0.37 (0.720) | -0.8% | 98 | +0.035 (0.489) | 6.0% [-15.7%, 27.1%] |
| CFB-SIG-026 | 4216 | 81.5% | +9.065 | 4207 | 2094-2032-81 | 50.8% [49.2%, 52.3%] | +0.52 (0.034) | -3.1% | 1637 | +0.023 (0.015) | 0.7% [-2.3%, 3.7%] |

## 5. Total hypotheses

| Id | n | Football lift (pts) | Market n | O/U hit (dir) | Mean resid (p) | ROI@−110 |
|---|---|---|---|---|---|---|
| CFB-SIG-008 | 1391 | +6.96 | 1389 | 47.4% | +0.27 (0.565) | -9.5% |
| CFB-SIG-009 | 1470 | +6.29 | 1426 | 51.6% | +0.09 (0.823) | -1.6% |
| CFB-SIG-011 | 533 | +9.88 | 533 | 47.3% | -1.02 (0.110) | -9.8% |
| CFB-SIG-012 | 1755 | +8.30 | 1731 | 46.0% | -0.31 (0.464) | -12.1% |
| CFB-SIG-013 | 1491 | +8.93 | 1484 | 50.0% | -0.65 (0.105) | -4.6% |

## 6. CONTROL tiers vs spread and moneyline

| Tier | n | W | Win | ATS W-L-P | Cover [95%] | Mean / median resid | p | ROI@−110 | Mean spread (side) | vs opener 2021+ (n, cover, ROI) | ML n | Win − implied | ML ROI [95%] |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ALL_CONTROL | 4216 | 3437 | 81.5% | 2094-2032-81 | 50.8% [49.2%, 52.3%] | +0.52 / +0.0 | 0.034 | -3.1% | -15.5 | 1770, 51.8%, -1.1% | 1637 | +0.023 | 0.7% [-2.3%, 3.7%] |
| ALL_MODERATE | 1834 | 1348 | 73.5% | 921-869-44 | 51.5% [49.1%, 53.8%] | +0.69 / +0.5 | 0.060 | -1.8% | -9.4 | 804, 53.9%, 2.8% | 798 | +0.026 | 2.2% [-2.7%, 7.1%] |
| ALL_STRONG | 2382 | 2089 | 87.7% | 1173-1163-37 | 50.2% [48.2%, 52.2%] | +0.38 / +0.0 | 0.242 | -4.1% | -20.3 | 966, 50.2%, -4.2% | 839 | +0.021 | -0.8% [-4.0%, 2.5%] |
| AWAY_MODERATE | 918 | 618 | 67.3% | 454-441-23 | 50.7% [47.5%, 54.0%] | +0.45 / +0.0 | 0.386 | -3.2% | -6.9 | 402, 52.3%, -0.2% | 399 | +0.005 | 1.3% [-7.1%, 10.3%] |
| AWAY_STRONG | 930 | 773 | 83.1% | 494-420-16 | 54.0% [50.8%, 57.3%] | +1.18 / +1.5 | 0.029 | 3.2% | -15.2 | 376, 52.4%, 0.0% | 362 | +0.015 | -1.1% [-7.1%, 4.8%] |
| HOME_MODERATE | 916 | 730 | 79.7% | 467-428-21 | 52.2% [48.9%, 55.4%] | +0.93 / +0.5 | 0.072 | -0.4% | -11.9 | 402, 55.4%, 5.8% | 399 | +0.046 | 3.1% [-2.5%, 9.1%] |
| HOME_STRONG | 1452 | 1316 | 90.6% | 679-743-21 | 47.7% [45.2%, 50.3%] | -0.13 / -0.5 | 0.744 | -8.8% | -23.5 | 590, 48.7%, -7.0% | 477 | +0.026 | -0.5% [-4.1%, 3.4%] |

MODERATE − STRONG mean ATS residual: +0.31 pts, bootstrap 95% [-0.64, +1.25].

### 6a. CONTROL tiers by season (ATS: n, cover, mean residual)

| Tier | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ALL_CONTROL | 349, 50%, +0.9 | 360, 52%, +1.6 | 376, 49%, -0.2 | 380, 52%, +0.8 | 345, 50%, +0.0 | 373, 54%, +0.8 | 210, 50%, -0.4 | 338, 49%, -0.6 | 354, 46%, -1.0 | 369, 51%, +1.0 | 391, 53%, +1.2 | 362, 53%, +1.6 |
| ALL_MODERATE | 155, 49%, +0.6 | 175, 55%, +2.3 | 161, 54%, +0.4 | 162, 47%, -1.6 | 140, 49%, +0.5 | 161, 51%, +1.7 | 72, 44%, -1.6 | 151, 54%, +0.9 | 160, 46%, -0.6 | 169, 54%, +2.2 | 175, 55%, +0.7 | 153, 54%, +1.4 |
| ALL_STRONG | 194, 51%, +1.1 | 185, 48%, +1.0 | 215, 45%, -0.7 | 218, 56%, +2.5 | 205, 51%, -0.3 | 212, 56%, +0.1 | 138, 53%, +0.2 | 187, 46%, -1.9 | 194, 47%, -1.3 | 200, 47%, +0.0 | 216, 51%, +1.6 | 209, 52%, +1.8 |
| AWAY_MODERATE | 73, 55%, +2.4 | 91, 60%, +4.6 | 85, 51%, -0.7 | 85, 47%, -3.0 | 64, 48%, -0.1 | 82, 48%, +1.5 | 34, 41%, -1.1 | 77, 47%, +0.3 | 78, 50%, +1.0 | 87, 55%, +1.6 | 92, 48%, -2.3 | 70, 52%, +0.5 |
| AWAY_STRONG | 76, 57%, +4.2 | 65, 59%, +4.3 | 92, 50%, -0.3 | 95, 59%, +2.9 | 76, 52%, +0.3 | 92, 56%, -1.0 | 56, 60%, +3.1 | 78, 50%, +0.1 | 72, 49%, -0.5 | 76, 53%, +0.8 | 77, 55%, +1.1 | 75, 50%, +0.2 |
| HOME_MODERATE | 82, 43%, -1.0 | 84, 50%, -0.2 | 76, 57%, +1.6 | 77, 47%, -0.1 | 76, 50%, +1.1 | 79, 55%, +2.0 | 38, 47%, -2.1 | 74, 61%, +1.5 | 82, 42%, -2.0 | 82, 53%, +2.8 | 83, 63%, +4.0 | 83, 56%, +2.1 |
| HOME_STRONG | 118, 47%, -0.9 | 120, 42%, -0.8 | 123, 41%, -1.1 | 123, 53%, +2.2 | 129, 51%, -0.6 | 120, 57%, +0.9 | 82, 47%, -1.8 | 109, 43%, -3.3 | 122, 46%, -1.8 | 124, 44%, -0.5 | 139, 48%, +1.9 | 134, 53%, +2.7 |

### 6b. CONTROL tiers by closing-spread bucket (side perspective: n, cover)

* ALL_CONTROL: -13.5..-7: 1215, 51.6%; -2.5..0: 122, 52.9%; -20.5..-14: 1028, 51.1%; -6.5..-3: 534, 50.6%; -99..-21: 1142, 48.8%; 0.5..99: 154, 55.6%
* ALL_MODERATE: -13.5..-7: 769, 53.0%; -2.5..0: 95, 51.1%; -20.5..-14: 377, 50.4%; -6.5..-3: 403, 48.9%; -99..-21: 73, 51.4%; 0.5..99: 112, 52.7%
* ALL_STRONG: -13.5..-7: 446, 49.2%; -2.5..0: 27, 59.3%; -20.5..-14: 651, 51.5%; -6.5..-3: 131, 55.7%; -99..-21: 1069, 48.6%; 0.5..99: 42, 63.4%
* AWAY_MODERATE: -13.5..-7: 363, 51.0%; -2.5..0: 69, 54.4%; -20.5..-14: 99, 52.0%; -6.5..-3: 274, 48.9%; -99..-21: 10, 55.6%; 0.5..99: 103, 50.5%
* AWAY_STRONG: -13.5..-7: 280, 50.9%; -2.5..0: 18, 61.1%; -20.5..-14: 278, 55.2%; -6.5..-3: 88, 60.2%; -99..-21: 238, 52.1%; 0.5..99: 24, 69.6%
* HOME_MODERATE: -13.5..-7: 406, 54.8%; -2.5..0: 26, 42.3%; -20.5..-14: 278, 49.8%; -6.5..-3: 129, 48.8%; -99..-21: 63, 50.8%; 0.5..99: 9, 77.8%
* HOME_STRONG: -13.5..-7: 166, 46.3%; -2.5..0: 9, 55.6%; -20.5..-14: 373, 48.8%; -6.5..-3: 43, 46.5%; -99..-21: 831, 47.5%; 0.5..99: 18, 55.6%

## 7. Market disagreement regimes

| Football | Market regime | n | Win | ATS cover [95%] | Mean resid (p) | ROI@−110 | ML n | Implied | ML win | ML ROI |
|---|---|---|---|---|---|---|---|---|---|---|
| CONTROL_STRONG | MARKET_MODERATE(fav 3-9.5) | 290 | 72.1% | 54.0% [48.2%, 59.7%] | +1.79 (0.059) | 3.1% | 129 | 67.9% | 70.5% | 1.0% |
| CONTROL_STRONG | MARKET_SKEPTICAL(fav<3 or dog) | 70 | 55.7% | 60.9% [49.1%, 71.5%] | +5.25 (0.009) | 16.2% | 32 | 44.4% | 46.9% | 5.3% |
| CONTROL_STRONG | MARKET_STRONG(fav>=10) | 2013 | 91.0% | 49.3% [47.1%, 51.5%] | +0.01 (0.983) | -5.9% | 678 | 86.9% | 88.9% | -1.4% |
| CONTROL_MODERATE | MARKET_MODERATE(fav 3-9.5) | 772 | 67.0% | 52.1% [48.6%, 55.7%] | +0.53 (0.360) | -0.5% | 350 | 67.5% | 69.7% | 0.5% |
| CONTROL_MODERATE | MARKET_SKEPTICAL(fav<3 or dog) | 207 | 47.8% | 52.0% [45.1%, 58.7%] | +0.37 (0.720) | -0.8% | 98 | 47.5% | 51.0% | 6.0% |
| CONTROL_MODERATE | MARKET_STRONG(fav>=10) | 855 | 85.6% | 50.7% [47.3%, 54.1%] | +0.91 (0.088) | -3.2% | 350 | 80.5% | 83.1% | 2.9% |

Football scoring baseline and closing spread disagree on the favourite in 1431 games: the football side won 37.5% (market favourite 62.5%); football side ATS cover 49.7% (mean resid -0.12, p 0.768). 2021+ (n 648): football side implied 37.6%, won 38.6%.

CONTROL side win rate by moneyline no-vig implied bucket (2021+):

* 0.0-0.2: n 2, implied 16.9%, won 100.0%
* 0.2-0.4: n 43, implied 29.8%, won 51.2%
* 0.4-0.6: n 148, implied 52.5%, won 52.7%
* 0.6-0.8: n 666, implied 72.2%, won 73.7%
* 0.8-0.9: n 507, implied 85.2%, won 88.2%
* 0.9-1.0: n 271, implied 93.0%, won 93.7%

## 8. Walk-forward (pre-registered models)

* **WF-ATS**: {"n": 6526, "oos_corr": 0.0153, "oos_corr_ci_boot": [-0.010596009113177174, 0.04168109223024512], "oos_mse_model": 240.2117, "oos_mse_zero": 239.5608}
  * picks: n 412, cover 46.7%, ROI@−110 -10.9%
  * folds: 2017: r +0.011; 2018: r +0.012; 2019: r -0.015; 2020: r +0.027; 2021: r +0.047; 2022: r -0.010; 2023: r +0.081; 2024: r +0.029; 2025: r +0.004
* **WF-ML**: {"brier_market": 0.1852, "brier_market_plus_football": 0.1852, "delta_logloss": 0.0004, "delta_logloss_ci_boot": [-0.001958884677297172, 0.002704999240334101], "logloss_market": 0.547, "logloss_market_plus_football": 0.5474, "n": 2920}
  * folds: 2022: Δlogloss -0.0031; 2023: Δlogloss +0.0053; 2024: Δlogloss +0.0018; 2025: Δlogloss -0.0025
* **WF-TOTAL**: {"n": 6522, "oos_corr": -0.0141, "oos_corr_ci_boot": [-0.03887310110764965, 0.011469598717273927], "oos_mse_model": 261.6609, "oos_mse_zero": 261.2158}
  * picks: n 157, cover 38.5%, ROI@−110 -26.6%
  * folds: 2017: r +0.016; 2018: r -0.038; 2019: r -0.049; 2020: r -0.088; 2021: r -0.044; 2022: r -0.056; 2023: r +0.010; 2024: r +0.117; 2025: r +0.051

## 9. Stage A screen (block A 2014–2019 only; exploration, not evidence)

410 associations (273 market); market q<0.05: 15; q<0.10: 21.

| Scope | Feature | Target | n | β/SD | z | q (whole screen) | seasons same sign |
|---|---|---|---|---|---|---|---|
| SIDE | defdiff.rush_success_rate | home_ats_resid | 4462 | +0.827 | +3.45 | 0.002 | 6/6 |
| WITHIN_STRONG_CONTROL | offdiff.third_down_rate | control_side_ats_resid | 1229 | -1.384 | -3.09 | 0.007 | 5/6 |
| SIDE | netq.rush_success_rate | home_ats_resid | 4462 | +0.747 | +3.07 | 0.007 | 6/6 |
| WITHIN_STRONG_CONTROL | offdiff.points_per_game | control_side_ats_resid | 1229 | -1.309 | -3.03 | 0.008 | 6/6 |
| SIDE | defdiff.plays_per_game | home_ats_resid | 4475 | +0.721 | +2.97 | 0.010 | 5/6 |
| WITHIN_STRONG_CONTROL | market.spread_home(reference) | control_side_ats_resid | 1229 | +1.304 | +2.93 | 0.011 | 6/6 |
| SIDE | pass_rate_diff_raw | home_ats_resid | 4475 | -0.720 | -2.93 | 0.011 | 5/6 |
| WITHIN_STRONG_CONTROL | offdiff.points_per_opportunity | control_side_ats_resid | 1205 | -1.296 | -2.85 | 0.014 | 6/6 |
| SIDE | net.rushing | home_ats_resid | 4475 | +0.669 | +2.75 | 0.019 | 6/6 |
| WITHIN_STRONG_CONTROL | offdiff.points_per_drive | control_side_ats_resid | 1227 | -1.181 | -2.71 | 0.021 | 6/6 |
| SIDE | defdiff.points_per_game | home_ats_resid | 4475 | +0.603 | +2.55 | 0.033 | 6/6 |
| WITHIN_MODERATE_CONTROL | offdiff.success_rate | control_side_ats_resid | 954 | +1.239 | +2.51 | 0.036 | 4/6 |
| SIDE | offdiff.seconds_per_play | home_ats_resid | 4474 | -0.605 | -2.48 | 0.039 | 5/6 |
| WITHIN_STRONG_CONTROL | netq.third_down_rate | control_side_ats_resid | 1229 | -1.109 | -2.47 | 0.040 | 5/6 |
| WITHIN_MODERATE_CONTROL | netq.success_rate | control_side_ats_resid | 954 | +1.224 | +2.44 | 0.043 | 6/6 |
| SIDE | defdiff.yards_per_rush | home_ats_resid | 4475 | +0.535 | +2.27 | 0.066 | 6/6 |
| TOTAL | defsum.seconds_per_play | total_resid | 4378 | -0.567 | -2.24 | 0.072 | 4/6 |
| WITHIN_STRONG_CONTROL | offdiff.first_down_rate | control_side_ats_resid | 1229 | -0.986 | -2.22 | 0.076 | 5/6 |
| WITHIN_MODERATE_CONTROL | netq.interception_rate | control_side_ats_resid | 954 | +1.059 | +2.20 | 0.079 | 5/6 |
| SIDE | offdiff.turnovers_per_game | home_ats_resid | 4475 | +0.521 | +2.18 | 0.082 | 5/6 |
| TOTAL | pass_rate_sum_raw | total_resid | 4379 | -0.558 | -2.18 | 0.082 | 6/6 |
| WITHIN_STRONG_CONTROL | baseline.home_margin | control_side_ats_resid | 1229 | -0.886 | -2.08 | 0.105 | 4/6 |
| WITHIN_STRONG_CONTROL | offdiff.pass_success_rate | control_side_ats_resid | 1225 | -0.916 | -2.07 | 0.105 | 6/6 |
| WITHIN_STRONG_CONTROL | offdiff.interception_rate | control_side_ats_resid | 1229 | -0.902 | -2.04 | 0.113 | 5/6 |
| SIDE | netq.yards_per_rush | home_ats_resid | 4475 | +0.495 | +2.03 | 0.114 | 6/6 |

## 10. 2026 CONTROL vs Kalshi spread ladder (descriptive; last catalog snapshot before kickoff)

| Tier | Game | Minutes before kickoff | Implied control margin (ladder 50%) | Actual control margin | Margin − implied |
|---|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | Iowa@Michigan | 100 | -5.2 | +1 | +6.2 |
| HOME_CONTROL_MODERATE | Wyoming@North Dakota State | 87 | +17.0 | +28 | +11.0 |
| AWAY_CONTROL_MODERATE | Texas Tech@Colorado | 118 | +13.6 | +22 | +8.4 |
| HOME_CONTROL_MODERATE | Washington@USC | 118 | +7.3 | +4 | -3.3 |
| HOME_CONTROL_MODERATE | Utah State@Boise State | 118 | +18.7 | +19 | +0.3 |
| STRONG (all) | 58 games | ≤ 1440 | — | — | 29 above / 29 below, mean +0.9 |

Not priced: {'BEFORE_CATALOG_HISTORY': 13, 'NO_PREKICK_SNAPSHOT': 12} (catalog history starts 2026-09-18).
