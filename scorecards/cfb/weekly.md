## CFB projection scorecard — last 28 days

Generated 2026-11-15T19:28:49Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 34643 across 242 games. Settled: 14804 (void/DNP 122, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1924 (241) | 4.117 | +0.204 | 2.876 | 78% | — |
| game_total | pf-cfb (challenger) | 241 (241) | 13.533 | +0.063 | 9.425 | 79% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 241 (241) | 13.702 | +0.316 | — | — |  |
| margin | pf-cfb (challenger) | 241 (241) | 13.057 | +2.661 | 9.317 | 77% | — |
| margin | script-engine-scoring-baseline (incumbent) | 241 (241) | 13.320 | +3.148 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 423 (236) | 7.530 | +0.385 | 5.290 | 83% | — |
| passing_yards | pf-cfb (challenger) | 423 (236) | 62.896 | +10.176 | 44.832 | 86% | — |
| receiving_yards | pf-cfb (challenger) | 4091 (241) | 18.126 | +0.933 | 12.071 | 82% | — |
| receptions | pf-cfb (challenger) | 4091 (241) | 1.290 | +0.028 | 0.863 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 1924 (241) | 25.788 | +1.174 | 17.671 | 80% | — |
| team_points | pf-cfb (challenger) | 482 (241) | 9.376 | +0.032 | 6.624 | 80% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 482 (241) | 9.457 | +0.156 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.661 (95% CI +0.673..+4.686) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **ALERT** — bias +10.176 (95% CI +1.793..+18.811) exceeds 5% of the mean
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
