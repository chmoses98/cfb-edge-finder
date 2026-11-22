# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 239; settled: 156. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 18 | 10 | 8/18 | 12/18 |
| AWAY_CONTROL_STRONG | 16 | 10 | 8/16 | 11/16 |
| HOME_CONTROL_MODERATE | 27 | 21 | 10/27 | 21/27 |
| HOME_CONTROL_STRONG | 27 | 21 | 12/27 | 18/27 |

## CLOSENESS

n = 32; |margin| <= 3: 0.3125; <= 7: 0.5938; <= 8: 0.625

## Environment

* PACE HIGH: n = 17, direction held 0.7647
* PACE LOW: n = 17, direction held 0.8824
* DEFENSIVE SUPPRESSION: n = 7, realized label 0.4286
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
