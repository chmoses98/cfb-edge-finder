# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 127; settled: 67. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 9 | 6 | 4/9 | 8/9 |
| AWAY_CONTROL_STRONG | 2 | 0 | 0/2 | 0/2 |
| HOME_CONTROL_MODERATE | 4 | 4 | 3/4 | 4/4 |
| HOME_CONTROL_STRONG | 10 | 8 | 4/10 | 6/10 |

## CLOSENESS

n = 15; |margin| <= 3: 0.4667; <= 7: 0.6667; <= 8: 0.7333

## Environment

* PACE HIGH: n = 6, direction held 0.8333
* PACE LOW: n = 7, direction held 0.8571
* DEFENSIVE SUPPRESSION: n = 5, realized label 0.4
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
