# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 194; settled: 129. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 16 | 9 | 6/16 | 10/16 |
| AWAY_CONTROL_STRONG | 12 | 8 | 8/12 | 9/12 |
| HOME_CONTROL_MODERATE | 21 | 16 | 8/21 | 17/21 |
| HOME_CONTROL_STRONG | 20 | 15 | 10/20 | 12/20 |

## CLOSENESS

n = 27; |margin| <= 3: 0.3333; <= 7: 0.6667; <= 8: 0.7037

## Environment

* PACE HIGH: n = 14, direction held 0.7857
* PACE LOW: n = 13, direction held 0.9231
* DEFENSIVE SUPPRESSION: n = 6, realized label 0.5
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
