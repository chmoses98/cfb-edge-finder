## CFB projection scorecard — last 28 days

Generated 2026-11-14T23:42:45Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 34643 across 242 games. Settled: 11808 (void/DNP 86, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1535 (194) | 4.147 | +0.210 | 2.898 | 78% | — |
| game_total | pf-cfb (challenger) | 194 (194) | 13.625 | -1.198 | 9.589 | 77% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 194 (194) | 14.046 | -1.002 | — | — |  |
| margin | pf-cfb (challenger) | 194 (194) | 12.971 | +2.827 | 9.221 | 77% | — |
| margin | script-engine-scoring-baseline (incumbent) | 194 (194) | 13.121 | +3.454 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 336 (189) | 7.589 | +0.113 | 5.328 | 82% | — |
| passing_yards | pf-cfb (challenger) | 336 (189) | 63.375 | +4.229 | 45.312 | 85% | — |
| receiving_yards | pf-cfb (challenger) | 3257 (194) | 18.566 | +0.324 | 12.439 | 81% | — |
| receptions | pf-cfb (challenger) | 3257 (194) | 1.321 | +0.001 | 0.887 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 1535 (194) | 25.606 | +1.142 | 17.557 | 80% | — |
| team_points | pf-cfb (challenger) | 388 (194) | 9.357 | -0.599 | 6.647 | 80% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 388 (194) | 9.418 | -0.503 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.827 (95% CI +0.709..+5.210) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **OK** — within expectations
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
