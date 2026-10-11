# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 64; settled: 27. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 4 | 3 | 1/4 | 3/4 |
| HOME_CONTROL_MODERATE | 3 | 3 | 2/3 | 3/3 |
| HOME_CONTROL_STRONG | 5 | 4 | 1/5 | 2/5 |

## CLOSENESS

n = 6; |margin| <= 3: 0.5; <= 7: 0.6667; <= 8: 0.6667

## Environment

* PACE HIGH: n = 2, direction held 1.0
* PACE LOW: n = 3, direction held 0.6667
* DEFENSIVE SUPPRESSION: n = 2, realized label 0.5
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
