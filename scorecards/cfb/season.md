## CFB projection scorecard — season to date

Generated 2026-11-17T19:52:04Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 38747 across 312 games. Settled: 14870 (void/DNP 122, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1932 (242) | 4.119 | +0.201 | 2.876 | 78% | — |
| game_total | pf-cfb (challenger) | 242 (242) | 13.514 | +0.100 | 9.409 | 79% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 242 (242) | 13.680 | +0.349 | — | — |  |
| margin | pf-cfb (challenger) | 242 (242) | 13.019 | +2.634 | 9.296 | 77% | — |
| margin | script-engine-scoring-baseline (incumbent) | 242 (242) | 13.286 | +3.114 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 425 (237) | 7.537 | +0.425 | 5.291 | 82% | — |
| passing_yards | pf-cfb (challenger) | 425 (237) | 63.084 | +10.613 | 44.912 | 86% | — |
| receiving_yards | pf-cfb (challenger) | 4110 (242) | 18.118 | +0.989 | 12.056 | 82% | — |
| receptions | pf-cfb (challenger) | 4110 (242) | 1.290 | +0.033 | 0.862 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 1932 (242) | 25.801 | +1.124 | 17.690 | 80% | — |
| team_points | pf-cfb (challenger) | 484 (242) | 9.356 | +0.050 | 6.611 | 80% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 484 (242) | 9.436 | +0.172 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.634 (95% CI +0.569..+4.637) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **ALERT** — bias +10.613 (95% CI +2.581..+18.882) exceeds 5% of the mean
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
