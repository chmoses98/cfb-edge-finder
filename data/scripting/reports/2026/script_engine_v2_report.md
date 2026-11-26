# CFB Script Engine V2 — prospective claim settlement

Methodology `cfb-script-engine/2.0.0`. FINAL_PREGAME games: 242; settled: 163. Frozen claims only; nothing re-derived. Historical counts are frequencies of past games, never chances.

## CONTROL

| Tier | n | Won | Central-50 coverage | Central-80 coverage |
|---|---|---|---|---|
| AWAY_CONTROL_MODERATE | 20 | 12 | 9/20 | 14/20 |
| AWAY_CONTROL_STRONG | 16 | 10 | 8/16 | 11/16 |
| HOME_CONTROL_MODERATE | 28 | 22 | 11/28 | 22/28 |
| HOME_CONTROL_STRONG | 28 | 21 | 12/28 | 18/28 |

## CLOSENESS

n = 34; |margin| <= 3: 0.2941; <= 7: 0.5882; <= 8: 0.6471

## Environment

* PACE HIGH: n = 17, direction held 0.7647
* PACE LOW: n = 18, direction held 0.8889
* DEFENSIVE SUPPRESSION: n = 7, realized label 0.4286
* UNAVAILABLE (explicit): {'pace': 0, 'scoring_environment': 0, 'defensive_suppression': 0}
