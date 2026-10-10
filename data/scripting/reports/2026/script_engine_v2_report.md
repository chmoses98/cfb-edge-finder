# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 62; settled: 21. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 3 | 2 | 0/3 | 2/3 |
| HOME_CONTROL_MODERATE | 3 | 3 | 2/3 | 3/3 |
| HOME_CONTROL_STRONG | 3 | 2 | 0/3 | 1/3 |

## CLOSENESS

n = 5; |margin| <= 3: 0.4; <= 7: 0.6; <= 8: 0.6

## Environment

* PACE HIGH: n = 1, direction held 1.0
* PACE LOW: n = 3, direction held 0.6667
* DEFENSIVE SUPPRESSION: n = 1, realized label 1.0
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
