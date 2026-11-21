## CFB projection scorecard — last 7 days

Generated 2026-11-21T00:15:38Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 42793 across 312 games. Settled: 15198 (void/DNP 124, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 530 (64) | 4.037 | +0.058 | 2.819 | 78% | — |
| game_total | pf-cfb (challenger) | 64 (64) | 12.711 | +3.383 | 8.541 | 84% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 64 (64) | 12.211 | +3.883 | — | — |  |
| margin | pf-cfb (challenger) | 64 (64) | 13.542 | +1.885 | 9.726 | 78% | — |
| margin | script-engine-scoring-baseline (incumbent) | 64 (64) | 13.938 | +1.753 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 121 (64) | 7.567 | +2.025 | 5.289 | 83% | — |
| passing_yards | pf-cfb (challenger) | 121 (64) | 60.776 | +32.332 | 42.938 | 88% | — |
| receiving_yards | pf-cfb (challenger) | 1111 (64) | 16.938 | +3.432 | 11.013 | 82% | — |
| receptions | pf-cfb (challenger) | 1111 (64) | 1.214 | +0.147 | 0.806 | 82% | — |
| rushing_yards | pf-cfb (challenger) | 530 (64) | 26.438 | -0.373 | 18.137 | 79% | — |
| team_points | pf-cfb (challenger) | 128 (64) | 9.243 | +1.692 | 6.481 | 77% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 128 (64) | 9.310 | +1.940 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **INSUFFICIENT** — 64 settled forecasts (< 100)
- margin: **INSUFFICIENT** — 64 settled forecasts (< 100)
- passing_attempts: **ALERT** — bias +2.025 (95% CI +0.297..+3.685) exceeds 5% of the mean
- passing_yards: **ALERT** — bias +32.332 (95% CI +18.357..+45.248) exceeds 5% of the mean
- receiving_yards: **ALERT** — bias +3.432 (95% CI +2.166..+4.719) exceeds 5% of the mean
- receptions: **ALERT** — bias +0.147 (95% CI +0.038..+0.250) exceeds 5% of the mean
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
