# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 189; settled: 124. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 15 | 8 | 6/15 | 10/15 |
| AWAY_CONTROL_STRONG | 11 | 7 | 7/11 | 8/11 |
| HOME_CONTROL_MODERATE | 20 | 16 | 8/20 | 17/20 |
| HOME_CONTROL_STRONG | 18 | 13 | 9/18 | 11/18 |

## CLOSENESS

n = 27; |margin| <= 3: 0.3333; <= 7: 0.6667; <= 8: 0.7037

## Environment

* PACE HIGH: n = 14, direction held 0.7857
* PACE LOW: n = 11, direction held 0.9091
* DEFENSIVE SUPPRESSION: n = 6, realized label 0.5
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
