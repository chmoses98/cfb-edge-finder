# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 239; settled: 145. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 17 | 9 | 7/17 | 11/17 |
| AWAY_CONTROL_STRONG | 15 | 9 | 8/15 | 10/15 |
| HOME_CONTROL_MODERATE | 24 | 18 | 9/24 | 19/24 |
| HOME_CONTROL_STRONG | 25 | 19 | 11/25 | 16/25 |

## CLOSENESS

n = 29; |margin| <= 3: 0.3103; <= 7: 0.6207; <= 8: 0.6552

## Environment

* PACE HIGH: n = 17, direction held 0.7647
* PACE LOW: n = 16, direction held 0.875
* DEFENSIVE SUPPRESSION: n = 7, realized label 0.4286
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
