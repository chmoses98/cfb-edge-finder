## CFB projection scorecard — last 7 days

Generated 2026-11-14T23:42:45Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 34643 across 242 games. Settled: 11808 (void/DNP 86, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 277 (34) | 4.537 | +0.175 | 3.157 | 73% | — |
| game_total | pf-cfb (challenger) | 34 (34) | 13.910 | +1.740 | 9.864 | 74% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 34 (34) | 14.962 | +2.274 | — | — |  |
| margin | pf-cfb (challenger) | 34 (34) | 13.908 | +6.281 | 9.817 | 74% | — |
| margin | script-engine-scoring-baseline (incumbent) | 34 (34) | 14.729 | +5.835 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 61 (33) | 8.460 | +2.096 | 5.806 | 80% | — |
| passing_yards | pf-cfb (challenger) | 61 (33) | 68.519 | +20.120 | 47.966 | 82% | — |
| receiving_yards | pf-cfb (challenger) | 568 (34) | 19.058 | +2.041 | 12.548 | 79% | — |
| receptions | pf-cfb (challenger) | 568 (34) | 1.366 | +0.091 | 0.923 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 277 (34) | 27.183 | -0.539 | 18.586 | 76% | — |
| team_points | pf-cfb (challenger) | 68 (34) | 9.974 | +0.870 | 6.983 | 78% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 68 (34) | 10.328 | +1.137 | — | — |  |

**Drift checks**

- carries: **WATCH** — CRPS 113% of historical holdout
- game_total: **INSUFFICIENT** — 34 settled forecasts (< 100)
- margin: **INSUFFICIENT** — 34 settled forecasts (< 100)
- passing_attempts: **INSUFFICIENT** — 61 settled forecasts (< 100)
- passing_yards: **INSUFFICIENT** — 61 settled forecasts (< 100)
- receiving_yards: **ALERT** — bias +2.041 (95% CI +0.067..+3.858) exceeds 5% of the mean
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **INSUFFICIENT** — 68 settled forecasts (< 100)
