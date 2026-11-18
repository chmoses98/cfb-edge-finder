## CFB projection scorecard — last 28 days

Generated 2026-11-18T23:49:15Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 40477 across 312 games. Settled: 15038 (void/DNP 122, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1931 (242) | 4.122 | +0.186 | 2.877 | 77% | — |
| game_total | pf-cfb (challenger) | 242 (242) | 13.399 | +0.160 | 9.293 | 80% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 242 (242) | 13.514 | +0.461 | — | — |  |
| margin | pf-cfb (challenger) | 242 (242) | 13.000 | +2.766 | 9.278 | 77% | — |
| margin | script-engine-scoring-baseline (incumbent) | 242 (242) | 13.234 | +3.261 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 425 (237) | 7.544 | +0.509 | 5.285 | 83% | — |
| passing_yards | pf-cfb (challenger) | 425 (237) | 62.844 | +10.649 | 44.664 | 86% | — |
| receiving_yards | pf-cfb (challenger) | 4097 (242) | 18.051 | +1.051 | 12.017 | 82% | — |
| receptions | pf-cfb (challenger) | 4097 (242) | 1.288 | +0.037 | 0.861 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 1931 (242) | 25.779 | +1.074 | 17.670 | 80% | — |
| team_points | pf-cfb (challenger) | 484 (242) | 9.267 | +0.080 | 6.559 | 79% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 484 (242) | 9.339 | +0.228 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.766 (95% CI +0.538..+5.001) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **ALERT** — bias +10.649 (95% CI +2.559..+18.570) exceeds 5% of the mean
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
