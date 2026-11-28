## CFB projection scorecard — last 28 days

Generated 2026-11-28T01:43:43Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 51982 across 381 games. Settled: 19196 (void/DNP 152, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1980 (251) | 4.099 | +0.229 | 2.855 | 78% | — |
| game_total | pf-cfb (challenger) | 261 (261) | 12.566 | +0.784 | 8.721 | 84% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 259 (259) | 12.761 | +0.964 | — | — |  |
| margin | pf-cfb (challenger) | 261 (261) | 12.604 | +2.746 | 9.001 | 80% | — |
| margin | script-engine-scoring-baseline (incumbent) | 259 (259) | 12.991 | +2.776 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 443 (246) | 7.009 | +0.757 | 5.025 | 85% | — |
| passing_yards | pf-cfb (challenger) | 443 (246) | 61.417 | +9.772 | 43.840 | 88% | — |
| receiving_yards | pf-cfb (challenger) | 4166 (251) | 18.200 | +0.994 | 12.124 | 82% | — |
| receptions | pf-cfb (challenger) | 4166 (251) | 1.287 | +0.059 | 0.861 | 80% | — |
| rushing_yards | pf-cfb (challenger) | 1980 (251) | 25.703 | +1.174 | 17.563 | 80% | — |
| team_points | pf-cfb (challenger) | 522 (261) | 8.917 | +0.392 | 6.273 | 81% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 518 (259) | 9.068 | +0.482 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.746 (95% CI +0.763..+4.420) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **ALERT** — bias +9.772 (95% CI +1.608..+17.317) exceeds 5% of the mean
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
