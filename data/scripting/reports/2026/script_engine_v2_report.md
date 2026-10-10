# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 58; settled: 9. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| HOME_CONTROL_MODERATE | 1 | 1 | 1/1 | 1/1 |

## CLOSENESS

n = 4; |margin| <= 3: 0.5; <= 7: 0.75; <= 8: 0.75

## Environment

* PACE HIGH: n = 0, direction held None
* PACE LOW: n = 2, direction held 1.0
* DEFENSIVE SUPPRESSION: n = 1, realized label 1.0
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
