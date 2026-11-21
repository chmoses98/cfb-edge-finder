## CFB projection scorecard — last 28 days

Generated 2026-11-21T19:50:03Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 43782 across 312 games. Settled: 15542 (void/DNP 130, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1741 (217) | 4.146 | +0.244 | 2.886 | 77% | — |
| game_total | pf-cfb (challenger) | 217 (217) | 13.250 | +0.905 | 9.146 | 81% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 217 (217) | 13.393 | +1.248 | — | — |  |
| margin | pf-cfb (challenger) | 217 (217) | 13.402 | +3.067 | 9.540 | 74% | — |
| margin | script-engine-scoring-baseline (incumbent) | 217 (217) | 13.749 | +3.497 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 383 (213) | 7.564 | +0.488 | 5.308 | 82% | — |
| passing_yards | pf-cfb (challenger) | 383 (213) | 62.653 | +12.755 | 44.681 | 86% | — |
| receiving_yards | pf-cfb (challenger) | 3662 (217) | 17.935 | +1.254 | 11.904 | 82% | — |
| receptions | pf-cfb (challenger) | 3662 (217) | 1.287 | +0.045 | 0.861 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 1741 (217) | 25.634 | +1.577 | 17.507 | 80% | — |
| team_points | pf-cfb (challenger) | 434 (217) | 9.358 | +0.453 | 6.600 | 78% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 434 (217) | 9.508 | +0.622 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +3.067 (95% CI +0.921..+5.376) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **ALERT** — bias +12.755 (95% CI +4.377..+21.520) exceeds 5% of the mean
- receiving_yards: **ALERT** — bias +1.254 (95% CI +0.376..+2.031) exceeds 5% of the mean
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
