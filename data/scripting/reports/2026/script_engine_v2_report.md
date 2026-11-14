# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 145; settled: 92. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 10 | 6 | 4/10 | 8/10 |
| AWAY_CONTROL_STRONG | 9 | 5 | 5/9 | 6/9 |
| HOME_CONTROL_MODERATE | 12 | 10 | 5/12 | 11/12 |
| HOME_CONTROL_STRONG | 13 | 10 | 6/13 | 8/13 |

## CLOSENESS

n = 18; |margin| <= 3: 0.3889; <= 7: 0.6111; <= 8: 0.6667

## Environment

* PACE HIGH: n = 6, direction held 0.8333
* PACE LOW: n = 7, direction held 0.8571
* DEFENSIVE SUPPRESSION: n = 5, realized label 0.4
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
