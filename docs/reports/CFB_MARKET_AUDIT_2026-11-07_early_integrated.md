# CFB market-first audit: 2026-11-07 (early window)

**EVERY SIDE HAS A DISPOSITION, BUT NOT EVERY SIDE IS ANALYST-EVALUATED: 0 analyst-evaluated, 2,649 derived historical estimates, 4,302 shape-only references, 910 insufficient evidence.**

_Research-only. No probability, bet, stake, approval or order is produced by this audit._

## Reconciliation

| games | contracts listed | sides expected (2x) | sides recorded | reconciled |
|---:|---:|---:|---:|:---:|
| 42 | 3,992 | 7,984 | 7,984 | yes |

| status | sides |
|---|---:|
| UNPRICEABLE | 123 |
| NOT_EVALUATED | 0 |
| DERIVED_ESTIMATE | 2,649 |
| SHAPE_ONLY_REFERENCE | 4,302 |
| EVALUATED_PASS | 0 |
| EVALUATED_CANDIDATE | 0 |
| INSUFFICIENT_EVIDENCE | 910 |

Sides with any stated probability range: **6,951** (analyst-evaluated: **0**; generated historical estimates are NOT analyst evaluation). Insufficient evidence: **910**; unpriceable: **123**; genuinely unexamined: **0**.

## Coverage by family

| family | contracts | sides | analyst-evaluated | derived | shape-only | insufficient | not evaluated | unpriceable | % priceable disposed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| game_spread | 767 | 1,534 | 0 | 607 | 565 | 320 | 0 | 42 | 100.0% |
| game_total | 566 | 1,132 | 0 | 384 | 462 | 286 | 0 | 0 | 100.0% |
| quarter_total | 484 | 968 | 0 | 0 | 948 | 0 | 0 | 20 | 100.0% |
| quarter_spread | 453 | 906 | 0 | 386 | 511 | 0 | 0 | 9 | 100.0% |
| team_total | 299 | 598 | 0 | 312 | 286 | 0 | 0 | 0 | 100.0% |
| first_half_spread | 274 | 548 | 0 | 254 | 294 | 0 | 0 | 0 | 100.0% |
| second_half_spread | 231 | 462 | 0 | 178 | 261 | 0 | 0 | 23 | 100.0% |
| first_half_total | 180 | 360 | 0 | 0 | 356 | 0 | 0 | 4 | 100.0% |
| second_half_total | 154 | 308 | 0 | 0 | 299 | 0 | 0 | 9 | 100.0% |
| quarter_moneyline | 132 | 264 | 0 | 176 | 88 | 0 | 0 | 0 | 100.0% |
| first_half_moneyline | 126 | 252 | 0 | 116 | 58 | 78 | 0 | 0 | 100.0% |
| game_moneyline | 84 | 168 | 0 | 112 | 4 | 52 | 0 | 0 | 100.0% |
| first_half_team_total | 83 | 166 | 0 | 80 | 80 | 0 | 0 | 6 | 100.0% |
| team_stat_prop | 57 | 114 | 0 | 0 | 0 | 114 | 0 | 0 | 100.0% |
| second_half_moneyline | 33 | 66 | 0 | 44 | 22 | 0 | 0 | 0 | 100.0% |
| touchdown_scorer | 30 | 60 | 0 | 0 | 0 | 50 | 0 | 10 | 100.0% |
| unknown | 12 | 24 | 0 | 0 | 24 | 0 | 0 | 0 | 100.0% |
| winning_margin | 12 | 24 | 0 | 0 | 24 | 0 | 0 | 0 | 100.0% |
| overtime | 10 | 20 | 0 | 0 | 20 | 0 | 0 | 0 | 100.0% |
| game_stat_prop | 5 | 10 | 0 | 0 | 0 | 10 | 0 | 0 | 100.0% |

## Why sides are unpriceable

* `NO_LIQUIDITY_AT_ASK`: 123

## Evidence gaps across priceable sides (a side can carry several)

* `FOOTBALL_HISTORY_ABSENT`: 736
* `NO_FOOTBALL_MODEL_INPUT_FOR_FAMILY`: 218
* `NO_REFERENCE_CLASS`: 978
* `NO_STRUCTURED_INJURY_SOURCE`: 7,861
* `QB_NOT_CONFIRMED`: 7,861

## Serious candidates (analyst-stated; listed by kickoff, not ranked)

None on record. (That is not a finding that none exist: see the status counts above.)

## Derived leads for analyst review (generated, NOT candidates or recommendations)

49 DERIVED_ESTIMATE sides have a historical lower bound above fee-inclusive break-even at the captured ask. They are research leads from an uncalibrated reference class, not validated EV.

| family | leads |
|---|---:|
| game_total | 35 |
| team_total | 12 |
| first_half_team_total | 1 |
| quarter_spread | 1 |

## Same-direction expression comparison (arithmetic, picks nothing)

14 game/direction groups contain at least one expression whose lower bound clears break-even. Cheapest-expression family mix (largest lower-bound headroom): {'game_total': 9, 'team_total': 3, 'quarter_spread': 1}. Full rows are in the JSON report.

## Handicap pricing integration

Analyst handicaps accepted: **0** games, pricing **0** sides (analyst evaluation). Rejected: 0. Seeded (draft, not analyst) cross-check: **6,739** sides across 29 games; of 2,649 derived estimates with a cross-check, 2,649 ranges overlap, 49 disagree on whether the lower bound clears the price, median point gap 0.0241; 2,508 carry a corroborated Bet Up To.
* seeded handicap cannot price (168 sides): a tie probability needs a discrete score model; a normal distribution cannot express it
* seeded handicap cannot price (114 sides): team_stat_prop is not a score-distribution contract (no drive-order or stat data in a handicap)
* seeded handicap cannot price (50 sides): touchdown_scorer is not a score-distribution contract (no drive-order or stat data in a handicap)
* seeded handicap cannot price (24 sides): the 1H/game-result joint needs cross-period correlation, which the handicap does not state
* seeded handicap cannot price (20 sides): overtime needs a discrete tie/overtime model; a normal score distribution cannot express it
* seeded handicap cannot price (10 sides): game_stat_prop is not a score-distribution contract (no drive-order or stat data in a handicap)

## Strongest-opportunity comparison across families (defensibly priced sides; arithmetic only)

| family | defensibly priced | clearing at lower bound | games | median headroom | max headroom | basis |
|---|---:|---:|---:|---:|---:|---|
| first_half_moneyline | 116 | 0 | 0 | None | None | {} |
| first_half_spread | 254 | 0 | 0 | None | None | {} |
| first_half_team_total | 80 | 1 | 1 | 0.0025 | 0.0025 | {'DERIVED_UNCORROBORATED': 1} |
| game_moneyline | 112 | 0 | 0 | None | None | {} |
| game_spread | 592 | 0 | 0 | None | None | {} |
| game_total | 384 | 35 | 9 | 0.0479 | 0.1857 | {'DERIVED_UNCORROBORATED': 35} |
| quarter_moneyline | 176 | 0 | 0 | None | None | {} |
| quarter_spread | 386 | 1 | 1 | 0.0016 | 0.0016 | {'DERIVED_UNCORROBORATED': 1} |
| second_half_moneyline | 44 | 0 | 0 | None | None | {} |
| second_half_spread | 177 | 0 | 0 | None | None | {} |
| team_total | 312 | 12 | 3 | 0.0375 | 0.0798 | {'DERIVED_UNCORROBORATED': 12} |

## Derived-estimate method and matchup uncertainty

Skill test (walk-forward from 2025-09-01; no market lines exist in the repository; this measures distribution quality only):

| quantity|period | label | games | Brier gain vs league frequency | 95% CI |
|---|---|---:|---:|---|
| margin|game | SUPPORTED | 1021 | 0.04014 | [0.03125, 0.04949] |
| margin|h1 | SUPPORTED | 1021 | 0.02669 | [0.0182, 0.03442] |
| margin|h2 | SUPPORTED | 1021 | 0.01831 | [0.01199, 0.02484] |
| margin|q1 | SUPPORTED | 1021 | 0.01637 | [0.00995, 0.0231] |
| margin|q2 | SUPPORTED | 1021 | 0.01579 | [0.00971, 0.02166] |
| margin|q3 | SUPPORTED | 1021 | 0.01141 | [0.00624, 0.01687] |
| margin|q4 | SUPPORTED | 1021 | 0.00656 | [0.0025, 0.01037] |
| team|game | SUPPORTED | 1021 | 0.02776 | [0.02156, 0.03359] |
| team|h1 | SUPPORTED | 1021 | 0.01837 | [0.01318, 0.02351] |
| team|h2 | SUPPORTED | 1021 | 0.01316 | [0.00889, 0.01705] |
| team|q1 | SUPPORTED | 1021 | 0.00897 | [0.00463, 0.01279] |
| team|q2 | SUPPORTED | 1021 | 0.00918 | [0.0056, 0.01304] |
| team|q3 | SUPPORTED | 1021 | 0.00759 | [0.00409, 0.011] |
| team|q4 | SUPPORTED | 1021 | 0.0045 | [0.00138, 0.00755] |
| total|game | SUPPORTED | 1021 | 0.00461 | [0.001, 0.00803] |
| total|h1 | SHAPE_ONLY | 1021 | 0.00145 | [-0.0015, 0.00478] |
| total|h2 | SHAPE_ONLY | 1021 | 0.00204 | [-0.0014, 0.00531] |
| total|q1 | SHAPE_ONLY | 1021 | 0.00012 | [-0.00299, 0.00298] |
| total|q2 | SHAPE_ONLY | 1021 | 4e-05 | [-0.00241, 0.00253] |
| total|q3 | SHAPE_ONLY | 1021 | -0.00042 | [-0.00294, 0.00213] |
| total|q4 | SHAPE_ONLY | 1021 | 0.0009 | [-0.00263, 0.00462] |

Widening policy (uncalibrated, widening only): `{'calibrated': False, 'applies_to': "each team's projected points, in both directions; the range is the union over the four corners", 'base_multiplier': 1.0, 'qb_unconfirmed_add': 0.25, 'qb_unstable_add': 0.5, 'qb_changed_add': 0.5, 'absences_add': 0.5, 'cap': 2.5, 'note': 'widening only: a larger multiplier can lower a lower bound and never raises one. No source measures how much a QB change or an absence moves a game, so these are stated assumptions, not estimates.'}`

Matchup QB/absence uncertainty levels across teams with a reference class: {('away', 'ELEVATED'): 2, ('away', 'HIGH'): 2, ('away', 'LOW'): 25, ('home', 'ELEVATED'): 2, ('home', 'HIGH'): 2, ('home', 'LOW'): 25}

## Why markets were rejected (analyst passes, by reason code)

No analyst passes are on record.

## Missing evidence named (by the generator or an analyst)

* comparable past games for this matchup's baseline: 736
* team receiving/rushing touchdown counts by player or position: 114
* drive-order / first-touchdown data: 50
* defensive/special-teams touchdown rates: 10

## Alternate expressions of the same thesis

No thesis-tagged comparisons on record; none is claimed.

## Historical after-fee ROI and CLV by family (context only)

| family | wagers | games | outlay $ | net P&L $ | ROI after fees | 95% CI (game-clustered) | CLV vs mid (n) |
|---|---:|---:|---:|---:|---:|---|---|
| combo_or_other | 34 | 33 | 1,329.49 | -274.98 | -20.7% | [-0.6527, 0.3291] | n/a |
| first_half_spread | 11 | 10 | 593.47 | -164.92 | -27.8% | [-0.772, 0.2804] | 0.005 (3) |
| first_half_total | 5 | 4 | 287.94 | 65.98 | 22.9% | too few games | -0.015 (1) |
| game_moneyline | 33 | 32 | 2,789.68 | 69.37 | 2.5% | [-0.3198, 0.3384] | -0.013182 (11) |
| game_spread | 114 | 105 | 7,908.92 | -1,437.54 | -18.2% | [-0.3872, 0.0533] | -0.008058 (27) |
| game_total | 46 | 45 | 2,466.37 | 101.19 | 4.1% | [-0.2727, 0.3688] | 0.015 (6) |
| quarter_spread | 12 | 12 | 1,623.94 | -419.28 | -25.8% | [-0.7582, 0.5714] | -0.0399 (3) |
| quarter_total | 9 | 9 | 611.72 | 156.54 | 25.6% | [-0.4801, 0.9552] | -0.00117 (3) |
| team_total | 66 | 62 | 5,574.96 | 857.69 | 15.4% | [-0.0899, 0.3852] | -0.052747 (28) |

* ROI is realised after-fee P&L on settled accounted wagers; cells are small and were examined after the fact.
* No model probability was recorded with these wagers, so handicapping error cannot be separated from pricing.
* CLV below is vs the closing MID proxy from the evidence join. Executable (ask-side) CLV is NOT available in the repository's evidence join; it is reported as unavailable rather than approximated.
* Nothing here excludes, favours or forces any family.
* Executable CLV: UNAVAILABLE

## Defects

None.

_NOT_EVALUATED means no analyst evaluation is on record; it is not a pass._
_reference_class numbers are descriptive historical frequencies, never a probability or a status._
_Nothing is ranked by attractiveness; candidates are listed by kickoff then ticker. No family is favoured or excluded and no diversification is forced._
