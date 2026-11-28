# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 304; settled: 181. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 21 | 12 | 9/21 | 15/21 |
| AWAY_CONTROL_STRONG | 19 | 13 | 10/19 | 14/19 |
| HOME_CONTROL_MODERATE | 33 | 26 | 14/33 | 26/33 |
| HOME_CONTROL_STRONG | 30 | 22 | 13/30 | 19/30 |

## CLOSENESS

n = 38; |margin| <= 3: 0.2895; <= 7: 0.5789; <= 8: 0.6316

## Environment

* PACE HIGH: n = 20, direction held 0.7
* PACE LOW: n = 20, direction held 0.9
* DEFENSIVE SUPPRESSION: n = 9, realized label 0.3333
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
