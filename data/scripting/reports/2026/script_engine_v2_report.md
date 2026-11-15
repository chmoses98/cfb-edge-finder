# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 182; settled: 119. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 15 | 8 | 6/15 | 10/15 |
| AWAY_CONTROL_STRONG | 10 | 6 | 6/10 | 7/10 |
| HOME_CONTROL_MODERATE | 18 | 14 | 6/18 | 15/18 |
| HOME_CONTROL_STRONG | 18 | 13 | 9/18 | 11/18 |

## CLOSENESS

n = 25; |margin| <= 3: 0.36; <= 7: 0.64; <= 8: 0.68

## Environment

* PACE HIGH: n = 14, direction held 0.7857
* PACE LOW: n = 9, direction held 0.8889
* DEFENSIVE SUPPRESSION: n = 6, realized label 0.5
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
