# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 255; settled: 170. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 21 | 12 | 9/21 | 15/21 |
| AWAY_CONTROL_STRONG | 16 | 10 | 8/16 | 11/16 |
| HOME_CONTROL_MODERATE | 30 | 23 | 12/30 | 23/30 |
| HOME_CONTROL_STRONG | 28 | 21 | 12/28 | 18/28 |

## CLOSENESS

n = 37; |margin| <= 3: 0.2973; <= 7: 0.5946; <= 8: 0.6486

## Environment

* PACE HIGH: n = 19, direction held 0.6842
* PACE LOW: n = 18, direction held 0.8889
* DEFENSIVE SUPPRESSION: n = 8, realized label 0.375
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
