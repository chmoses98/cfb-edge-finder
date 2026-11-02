# CFB loss attribution — 2026-10-22 to 2026-11-01

**90 settled, 50 losses, stake $9,013.40, net $-1,848.05 (fee-inclusive; $282.07 fees are inside stake).**

## Completed

- Reconciled 90 contracts; stake/fee/net identities violated: 0; per-order fee recompute mismatches: 0.
- Loss causes: INSUFFICIENT_EVIDENCE=48, VARIANCE=2

## Unresolved (evidence missing — not inferred)

- `CALIBRATED_PREGAME_PROBABILITY` on 44 losing contracts
- `CLOSE_QUOTE` on 17 losing contracts
- `HANDICAP_VS_PRICING_NOT_SEPARABLE_WITHOUT_PREGAME_PROBABILITY` on 25 losing contracts
- `UNDECODED_COMBO_LEGS` on 6 losing contracts

## Family performance (game-cluster bootstrap 95% CI on ROI)

| family | bets | games | stake | net | ROI | 95% CI | excludes 0 |
|---|---|---|---|---|---|---|---|
| GAME_SPREAD | 27 | 26 | 2,871 | -1,706 | -59.4% | [-84.7%, -27.2%] | yes |
| GAME_MONEYLINE | 11 | 11 | 961 | -279 | -29.1% | [-86.0%, +24.0%] | no |
| 1Q_SPREAD | 3 | 3 | 852 | -214 | -25.2% | [-100.0%, +93.2%] | n/a (<10 games) |
| GAME_TOTAL | 6 | 6 | 471 | -154 | -32.8% | [-100.0%, +53.1%] | n/a (<10 games) |
| COMBO_UNDECODED | 8 | 8 | 235 | -120 | -51.0% | [-100.0%, +33.2%] | n/a (<10 games) |
| 1H_SPREAD | 3 | 3 | 195 | -108 | -55.4% | [-100.0%, +93.2%] | n/a (<10 games) |
| 1Q_TOTAL | 3 | 3 | 390 | 62 | +16.0% | [-100.0%, +145.9%] | n/a (<10 games) |
| 1H_TOTAL | 1 | 1 | 94 | 91 | +97.0% | [+97.0%, +97.0%] | n/a (<10 games) |
| GAME_TEAM_TOTAL | 28 | 25 | 2,944 | 581 | +19.8% | [-14.4%, +52.3%] | no |

Families whose CI includes 0 are not distinguishable from break-even/variance on this sample. Families were examined AFTER seeing the losses (worst-of-9 selection), so an interval that excludes 0 is a lead to test prospectively, not a confirmed defect; and a positive family (team totals) is not shown to be profitable by a hot sample whose interval includes 0.

## Exposure at unit=$75 (flags only, no caps)

The unit is a supplied parameter, never inferred; $75 is the owner-set value (1U = $75, PR #249 comment) and must be confirmed by the owner before these flags are read as policy.
- Games over 5U gross: 1; over 5U correlated view: 1
- Days over 15U gross: {'2026-10-24': 49.67, '2026-10-31': 31.72}
- Undecoded combo units: 3.14

