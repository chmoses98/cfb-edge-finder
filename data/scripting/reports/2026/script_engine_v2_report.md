# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 182; settled: 108. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 11 | 7 | 5/11 | 9/11 |
| AWAY_CONTROL_STRONG | 9 | 5 | 5/9 | 6/9 |
| HOME_CONTROL_MODERATE | 16 | 13 | 6/16 | 15/16 |
| HOME_CONTROL_STRONG | 17 | 13 | 9/17 | 11/17 |

## CLOSENESS

n = 23; |margin| <= 3: 0.3043; <= 7: 0.6087; <= 8: 0.6522

## Environment

* PACE HIGH: n = 11, direction held 0.8182
* PACE LOW: n = 8, direction held 0.875
* DEFENSIVE SUPPRESSION: n = 5, realized label 0.4
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
