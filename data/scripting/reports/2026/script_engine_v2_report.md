# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 182; settled: 112. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 12 | 7 | 5/12 | 9/12 |
| AWAY_CONTROL_STRONG | 9 | 5 | 5/9 | 6/9 |
| HOME_CONTROL_MODERATE | 16 | 13 | 6/16 | 15/16 |
| HOME_CONTROL_STRONG | 18 | 13 | 9/18 | 11/18 |

## CLOSENESS

n = 24; |margin| <= 3: 0.3333; <= 7: 0.625; <= 8: 0.6667

## Environment

* PACE HIGH: n = 12, direction held 0.8333
* PACE LOW: n = 8, direction held 0.875
* DEFENSIVE SUPPRESSION: n = 6, realized label 0.5
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
