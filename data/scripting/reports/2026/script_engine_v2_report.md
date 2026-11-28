# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 255; settled: 174. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 21 | 12 | 9/21 | 15/21 |
| AWAY_CONTROL_STRONG | 17 | 11 | 9/17 | 12/17 |
| HOME_CONTROL_MODERATE | 31 | 24 | 13/31 | 24/31 |
| HOME_CONTROL_STRONG | 29 | 21 | 12/29 | 18/29 |

## CLOSENESS

n = 38; |margin| <= 3: 0.2895; <= 7: 0.5789; <= 8: 0.6316

## Environment

* PACE HIGH: n = 19, direction held 0.6842
* PACE LOW: n = 19, direction held 0.8947
* DEFENSIVE SUPPRESSION: n = 8, realized label 0.375
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
