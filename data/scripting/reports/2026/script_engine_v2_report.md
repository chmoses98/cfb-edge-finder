# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 182; settled: 98. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 10 | 6 | 4/10 | 8/10 |
| AWAY_CONTROL_STRONG | 9 | 5 | 5/9 | 6/9 |
| HOME_CONTROL_MODERATE | 14 | 11 | 5/14 | 13/14 |
| HOME_CONTROL_STRONG | 14 | 11 | 7/14 | 9/14 |

## CLOSENESS

n = 20; |margin| <= 3: 0.35; <= 7: 0.6; <= 8: 0.65

## Environment

* PACE HIGH: n = 9, direction held 0.8889
* PACE LOW: n = 7, direction held 0.8571
* DEFENSIVE SUPPRESSION: n = 5, realized label 0.4
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
