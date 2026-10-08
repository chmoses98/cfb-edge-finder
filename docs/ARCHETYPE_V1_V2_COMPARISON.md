# Archetype V1 vs V2 — Sealed Holdout Comparison (2014-2020)

**V1** is the production Script Engine 1.3.0: its published scripts, frozen per game, scored with the Wave 1
analyses. **V2** is the frozen research candidate. Both are read from the same frozen pregame records, the same 4,961
eligible holdout games and the same production findings. Neither was refitted.

* Pre-registered comparison metrics: protocol §10.
* Numbers: `data/scripting/validation/archetype_v2_holdout_report.json` → `v1_vs_v2`, with development in-sample
  values under `development_reference`.
* Base main: `f574e0db3`. Protocol: `09366f012`. Frozen parameters: `683d075d…`. Manifest: `037499cb7`.

## Summary table (holdout)

| Dimension | V1 (production, frozen) | V2 candidate (frozen) | Reading |
|---|---|---|---|
| **Redundant labels** | Every PULLS_AWAY script is co-published with a same-side CONTROL (**1,261 / 1,261**). Realized HANGS_AROUND contains every one-score label. 1.48 scripts per game. | CONTROL tiers are exclusive by construction. One residual redundancy: CLOSENESS_NARROW is exactly a MODERATE control game (1,026 / 1,026). | V2 removes the PULLS/CONTROL duplication. Its own union-closeness duplication should be dropped (keep EVEN). |
| **Coverage** | Any script 85.0%; no script 15.0% | Any claim 87.2%; no claim 12.8%; directional claim 48.4% | Similar coverage |
| **Directional accuracy** | PRIMARY with a winner lean: **82.1%** (1,976 / 2,406) | CONTROL, any tier: **82.1%** (1,973 / 2,402) | **Identical.** V2 restructures the information; it adds none. |
| **Stable lift** | 6 archetypes: HOME/AWAY_CONTROL, PULLS_AWAY, SHOOTOUT, GRIND, PACE | 10 / 10 pre-registered families pass | V2 claims are stable across all 7 seasons. V1's SHOOTOUT/GRIND flipped between eras (below). |
| **Closeness discrimination** | Scripts stating ±8: 45.0% one-score (lift **1.27**, n 1,094) | Union 41.9% (lift 1.18, n 1,969). **EVEN 46.9% (lift 1.32, n 943)** | EVEN is the best, and the cleanest one-score claim |
| **Scoring discrimination**, elevated | SHOOTOUT: mean pct 0.632, matched +0.015 | ELEVATED: 0.642, matched **+0.056** | V2 elevated is more incremental |
| **Scoring discrimination**, suppressed | GRIND: 0.371, matched −0.046 | SUPPRESSED: 0.357, matched −0.011 (not incremental) | V1 GRIND is more incremental than V2 SUPPRESSED on the holdout |
| **Scoring discrimination**, pace | PACE_DRIVEN_OVER: 0.608, matched +0.054 (n 570) | PACE_HIGH: 0.616, matched +0.055 (n 809). PACE_LOW: 0.391, −0.061 | Same signal; V2 covers more games and adds the low side |
| **Scoring discrimination**, defensive suppression | n **24**: 0.312, −0.038 | n **309**: 0.358, −0.034 | V2 surfaces the signal V1 suppressed |
| **Margin-distribution quality** | 7-24 band covers 38.0% (HOME_CONTROL) / 37.3% (AWAY); 17-45 covers 51.0% (PULLS). The bands have no nominal coverage. | Frozen 50% / 80% intervals cover 52.4-53.7% / 76.4-82.2%; mean absolute error from nominal 2.5 pts | V2 margins are calibrated statements; V1's are definitions that miss most outcomes |
| **Abstention** | No script 15.0%. \|m\|-entropy 2.304 (abstained) vs 2.295 (scripted) | No claim 12.8%. Entropy 2.305 vs 2.296 | Neither abstention isolates uncertainty |
| **Interpretability** | One narrative per role (PRIMARY / SECONDARY / DANGER). Overlapping archetypes; evidence-score ranking not monotone. | Independent statements (direction × strength, closeness, environment, pace, suppression, disruption), each with its own tested meaning | V2 is easier to audit claim by claim |

## V1 archetypes on the holdout (LABEL_MATCH, Wave 1 definitions)

| V1 archetype | Generated | PPV | Lift (bootstrap 95%) | Development lift (Wave 1) |
|---|---|---|---|---|
| HOME_CONTROL | 1,310 | 32.7% | 1.49 (1.39–1.58) | 1.43 |
| AWAY_CONTROL | 1,046 | 31.4% | 1.97 (1.83–2.12) | 1.96 |
| FAVORITE_PULLS_AWAY | 1,261 | 58.2% | 1.68 (1.61–1.75) | 1.66 |
| UNDERDOG_HANGS_AROUND | 1,046 | 36.1% | **1.02** (0.95–1.09) | 0.98 |
| COMPETITIVE_SHOOTOUT | 558 | 14.5% | **1.66** (1.38–1.96) | 0.97 |
| COMPETITIVE_GRIND | 963 | 14.4% | **1.33** (1.16–1.51) | 1.14 |
| COMPETITIVE_TOSSUP | 392 | 17.1% | 1.08 (0.87–1.30) | 0.96 |
| PACE_DRIVEN_OVER | 570 | 46.7% | 1.81 (1.67–1.96) | 1.55 |
| DEFENSIVE_SUPPRESSION | 24 | 29.2% | 1.58 (0.65–2.61) | 1.18 |
| TURNOVER_DISRUPTION | 186 | 11.3% | 2.05 (1.24–2.86) | 1.84 |
| EXPLOSIVE_UPSET | 0 | — | untested (no explosive data) | untested |

* **An important V1 finding.** Wave 1 found no lift for the realized COMPETITIVE_SHOOTOUT / GRIND labels in
  2021-2025. In 2014-2020 they **do** show lift: 1.66 and 1.33. A combined "close and high-scoring" narrative is
  **era-unstable**: it works in one block and not the other. The separate dimensions V2 tests (closeness via EVEN, and
  environment) are stable in both blocks. This supports separating them; it does not show the combined label is
  useless.
* **What did not replicate as useful:**
  * HANGS_AROUND (1.02)
  * TOSSUP (1.08)
* **Directional V1 archetypes replicated almost exactly:** HOME_CONTROL, AWAY_CONTROL, PULLS_AWAY.

## V1 CONTROL vs V2 CONTROL

| Question | Answer (holdout) |
|---|---|
| Does merging PULLS_AWAY into strength-tiered CONTROL reduce redundancy? | **Yes.** V1 published PULLS_AWAY only alongside a same-side CONTROL (1,261 / 1,261). V2 has one directional claim per game, with a strength tier. |
| Does it preserve information? | **Yes.** Directional accuracy is identical (82.1% both). STRONG vs MODERATE separates outcomes cleanly: home 91.0% vs 79.5%, median +24 vs +13; away 84.6% vs 67.9%, median +17 vs +7. Every V1 PULLS_AWAY game is a STRONG-edge game, so V2 STRONG contains all of V1's PULLS information. |
| Does it improve margin description? | **Yes.** V2's frozen central-80 intervals cover 76-82% of holdout outcomes and central-50 52-54%, against 35-42% for V1's 7-24 band and 29-54% for 17-45. |
| Does it generalize? | **Yes.** All four tiers pass C1-C2 in 7/7 seasons. The ordering holds in 7/7 seasons in both directions. The intervals are calibrated in all four tiers. |

## Bottom line

V2 does not find new football information. Its directional accuracy equals V1's because it reads the same efficiency
finding. What V2 changes is the **structure**:

* It removes the PULLS/CONTROL duplication.
* It replaces mis-shaped hand-set bands with calibrated empirical intervals.
* It separates closeness from scoring environment. This exposes a stable EVEN_MATCHUP closeness signal, and a stable
  DEFENSIVE_SUPPRESSION signal that V1's exclusivity hid.
* It makes the dimensions claims that can be tested one at a time.

Keep in mind:

* The suppressed scoring environment and the union closeness claim add little beyond simpler references.
* V1's GRIND is more incremental than V2's SUPPRESSED on this holdout.
