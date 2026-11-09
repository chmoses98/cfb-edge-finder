# CFB market-first audit: 2026-11-10 (evening window)

**EVERY SIDE HAS A DISPOSITION, BUT NOT EVERY SIDE IS ANALYST-EVALUATED: 562 analyst-evaluated, 0 derived historical estimates, 6 shape-only references, 0 insufficient evidence.**

_Research-only. No probability, bet, stake, approval or order is produced by this audit._

## Reconciliation

| games | contracts listed | sides expected (2x) | sides recorded | reconciled |
|---:|---:|---:|---:|:---:|
| 3 | 284 | 568 | 568 | yes |

| status | sides |
|---|---:|
| UNPRICEABLE | 0 |
| NOT_EVALUATED | 0 |
| DERIVED_ESTIMATE | 0 |
| SHAPE_ONLY_REFERENCE | 6 |
| EVALUATED_PASS | 559 |
| EVALUATED_CANDIDATE | 3 |
| INSUFFICIENT_EVIDENCE | 0 |

Sides with any stated probability range: **568** (analyst-evaluated: **562**; generated historical estimates are NOT analyst evaluation). Insufficient evidence: **0**; unpriceable: **0**; genuinely unexamined: **0**.

## Coverage by family

| family | contracts | sides | analyst-evaluated | derived | shape-only | insufficient | not evaluated | unpriceable | % priceable disposed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| game_spread | 86 | 172 | 172 | 0 | 0 | 0 | 0 | 0 | 100.0% |
| first_half_spread | 63 | 126 | 126 | 0 | 0 | 0 | 0 | 0 | 100.0% |
| game_total | 57 | 114 | 114 | 0 | 0 | 0 | 0 | 0 | 100.0% |
| first_half_total | 42 | 84 | 84 | 0 | 0 | 0 | 0 | 0 | 100.0% |
| team_total | 21 | 42 | 42 | 0 | 0 | 0 | 0 | 0 | 100.0% |
| first_half_moneyline | 9 | 18 | 12 | 0 | 6 | 0 | 0 | 0 | 100.0% |
| game_moneyline | 6 | 12 | 12 | 0 | 0 | 0 | 0 | 0 | 100.0% |

## Why sides are unpriceable


## Evidence gaps across priceable sides (a side can carry several)

* `NO_STRUCTURED_INJURY_SOURCE`: 568
* `QB_NOT_CONFIRMED`: 568

## Serious candidates (analyst-stated; listed by kickoff, not ranked)

| game | ticker | side | ask | all-in | low / point / high | Bet Up To | reason |
|---|---|---|---:|---:|---|---:|---|
| 26NOV10WMUAKR | `KXNCAAF1H-26NOV10WMUAKR-AKR` | YES | 0.2 | 0.211203 | 0.22403498204054095 / 0.33512159621629434 / 0.433294546537345 | 0.21 | ANALYST_HANDICAP: moneyline first_half priced from the game handicap; YES range over the stated region 0.224/0.335/0.433 vs fee-inclusive break-even 0.211 at the ask |
| 26NOV10WMUAKR | `KXNCAAF1HSPREAD-26NOV10WMUAKR-WMU5` | NO | 0.38 | 0.396497 | 0.41079057439378575 / 0.5121970766847229 / 0.6167478583540357 | 0.39 | ANALYST_HANDICAP: spread first_half priced from the game handicap; YES range over the stated region 0.411/0.512/0.617 vs fee-inclusive break-even 0.396 at the ask |
| 26NOV10WMUAKR | `KXNCAAFSPREAD-26NOV10WMUAKR-WMU14` | NO | 0.47 | 0.487484 | 0.5 / 0.6319792153669606 / 0.7839708094290536 | 0.48 | ANALYST_HANDICAP: spread full_game priced from the game handicap; YES range over the stated region 0.500/0.632/0.784 vs fee-inclusive break-even 0.487 at the ask |

## Derived leads for analyst review (generated, NOT candidates or recommendations)

None.

## Same-direction expression comparison (arithmetic, picks nothing)

1 game/direction groups contain at least one expression whose lower bound clears break-even. Cheapest-expression family mix (largest lower-bound headroom): {'first_half_spread': 1}. Full rows are in the JSON report.

## Handicap pricing integration

Analyst handicaps accepted: **3** games, pricing **562** sides (analyst evaluation). Rejected: 0. Seeded (draft, not analyst) cross-check: **562** sides across 3 games; of 0 derived estimates with a cross-check, 0 ranges overlap, 0 disagree on whether the lower bound clears the price, median point gap None; 0 carry a corroborated Bet Up To.
* 26NOV10KENTBGSU: Claude (RUN CFB analyst session) at 2026-11-09T21:55:00Z; periods first_half, first_quarter, fourth_quarter, full_game, second_half, second_quarter, third_quarter
* 26NOV10WMUAKR: Claude (RUN CFB analyst session) at 2026-11-09T21:55:00Z; periods first_half, first_quarter, fourth_quarter, full_game, second_half, second_quarter, third_quarter
* 26NOV10OHIOMOH: Claude (RUN CFB analyst session) at 2026-11-09T21:55:00Z; periods first_half, first_quarter, fourth_quarter, full_game, second_half, second_quarter, third_quarter
* seeded handicap cannot price (6 sides): a tie probability needs a discrete score model; a normal distribution cannot express it

## Strongest-opportunity comparison across families (defensibly priced sides; arithmetic only)

| family | defensibly priced | clearing at lower bound | games | median headroom | max headroom | basis |
|---|---:|---:|---:|---:|---:|---|
| first_half_moneyline | 12 | 1 | 1 | 0.0128 | 0.0128 | {'ANALYST': 1} |
| first_half_spread | 124 | 1 | 1 | 0.0143 | 0.0143 | {'ANALYST': 1} |
| first_half_total | 80 | 0 | 0 | None | None | {} |
| game_moneyline | 12 | 0 | 0 | None | None | {} |
| game_spread | 170 | 1 | 1 | 0.0125 | 0.0125 | {'ANALYST': 1} |
| game_total | 111 | 0 | 0 | None | None | {} |
| team_total | 42 | 0 | 0 | None | None | {} |

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

Matchup QB/absence uncertainty levels across teams with a reference class: {('away', 'LOW'): 3, ('home', 'HIGH'): 1, ('home', 'LOW'): 2}

## Why markets were rejected (analyst passes, by reason code)

* `HANDICAP_LOWER_BOUND_BELOW_BREAKEVEN`: 559

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
