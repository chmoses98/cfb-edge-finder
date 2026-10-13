# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 66; settled: 42. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 6 | 4 | 3/6 | 5/6 |
| HOME_CONTROL_MODERATE | 3 | 3 | 2/3 | 3/3 |
| HOME_CONTROL_STRONG | 7 | 6 | 3/7 | 4/7 |

## CLOSENESS

n = 11; |margin| <= 3: 0.5455; <= 7: 0.7273; <= 8: 0.8182

## Environment

* PACE HIGH: n = 4, direction held 1.0
* PACE LOW: n = 5, direction held 0.8
* DEFENSIVE SUPPRESSION: n = 3, realized label 0.6667
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
