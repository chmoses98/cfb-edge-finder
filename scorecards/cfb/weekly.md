## CFB projection scorecard — last 28 days

Generated 2026-11-21T23:36:34Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 44327 across 312 games. Settled: 15702 (void/DNP 130, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1609 (201) | 4.135 | +0.216 | 2.885 | 77% | — |
| game_total | pf-cfb (challenger) | 222 (222) | 12.918 | +0.717 | 8.917 | 82% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 220 (220) | 13.069 | +0.988 | — | — |  |
| margin | pf-cfb (challenger) | 222 (222) | 13.175 | +3.260 | 9.397 | 77% | — |
| margin | script-engine-scoring-baseline (incumbent) | 220 (220) | 13.556 | +3.568 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 356 (197) | 7.446 | +0.664 | 5.205 | 84% | — |
| passing_yards | pf-cfb (challenger) | 356 (197) | 61.171 | +13.443 | 43.613 | 87% | — |
| receiving_yards | pf-cfb (challenger) | 3379 (201) | 17.967 | +1.384 | 11.885 | 82% | — |
| receptions | pf-cfb (challenger) | 3379 (201) | 1.282 | +0.062 | 0.856 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 1609 (201) | 25.813 | +1.142 | 17.666 | 80% | — |
| team_points | pf-cfb (challenger) | 444 (222) | 9.168 | +0.359 | 6.469 | 80% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 440 (220) | 9.337 | +0.492 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +3.260 (95% CI +1.153..+5.383) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **ALERT** — bias +13.443 (95% CI +5.327..+21.848) exceeds 5% of the mean
- receiving_yards: **ALERT** — bias +1.384 (95% CI +0.591..+2.177) exceeds 5% of the mean
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
