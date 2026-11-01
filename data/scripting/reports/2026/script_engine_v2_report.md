# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 128; settled: 85. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 10 | 6 | 4/10 | 8/10 |
| AWAY_CONTROL_STRONG | 8 | 4 | 4/8 | 5/8 |
| HOME_CONTROL_MODERATE | 9 | 8 | 4/9 | 8/9 |
| HOME_CONTROL_STRONG | 11 | 9 | 5/11 | 7/11 |

## CLOSENESS

n = 17; |margin| <= 3: 0.4118; <= 7: 0.6471; <= 8: 0.7059

## Environment

* PACE HIGH: n = 6, direction held 0.8333
* PACE LOW: n = 7, direction held 0.8571
* DEFENSIVE SUPPRESSION: n = 5, realized label 0.4
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
