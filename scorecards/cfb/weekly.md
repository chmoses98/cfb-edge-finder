## CFB projection scorecard — last 28 days

Generated 2026-11-20T00:49:55Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 41743 across 312 games. Settled: 15150 (void/DNP 122, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1912 (240) | 4.145 | +0.167 | 2.893 | 77% | — |
| game_total | pf-cfb (challenger) | 240 (240) | 13.470 | +0.129 | 9.338 | 80% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 240 (240) | 13.612 | +0.461 | — | — |  |
| margin | pf-cfb (challenger) | 240 (240) | 12.999 | +2.625 | 9.292 | 78% | — |
| margin | script-engine-scoring-baseline (incumbent) | 240 (240) | 13.239 | +3.143 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 421 (235) | 7.542 | +0.614 | 5.288 | 83% | — |
| passing_yards | pf-cfb (challenger) | 421 (235) | 62.875 | +10.969 | 44.660 | 86% | — |
| receiving_yards | pf-cfb (challenger) | 4049 (240) | 18.035 | +1.135 | 11.992 | 82% | — |
| receptions | pf-cfb (challenger) | 4049 (240) | 1.286 | +0.048 | 0.859 | 80% | — |
| rushing_yards | pf-cfb (challenger) | 1912 (240) | 25.954 | +0.886 | 17.797 | 80% | — |
| team_points | pf-cfb (challenger) | 480 (240) | 9.291 | +0.065 | 6.578 | 80% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 480 (240) | 9.355 | +0.228 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.625 (95% CI +0.507..+4.599) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **ALERT** — bias +10.969 (95% CI +3.122..+18.575) exceeds 5% of the mean
- receiving_yards: **ALERT** — bias +1.135 (95% CI +0.417..+1.956) exceeds 5% of the mean
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
