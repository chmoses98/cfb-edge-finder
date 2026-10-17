# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 98; settled: 52. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 8 | 5 | 4/8 | 7/8 |
| AWAY_CONTROL_STRONG | 1 | 0 | 0/1 | 0/1 |
| HOME_CONTROL_MODERATE | 3 | 3 | 2/3 | 3/3 |
| HOME_CONTROL_STRONG | 8 | 7 | 3/8 | 5/8 |

## CLOSENESS

n = 12; |margin| <= 3: 0.5833; <= 7: 0.75; <= 8: 0.8333

## Environment

* PACE HIGH: n = 5, direction held 1.0
* PACE LOW: n = 5, direction held 0.8
* DEFENSIVE SUPPRESSION: n = 4, realized label 0.5
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
