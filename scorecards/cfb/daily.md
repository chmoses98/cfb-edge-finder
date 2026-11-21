## CFB projection scorecard — last 7 days

Generated 2026-11-21T23:36:34Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 44327 across 312 games. Settled: 15702 (void/DNP 130, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 219 (28) | 4.252 | +0.191 | 2.954 | 74% | — |
| game_total | pf-cfb (challenger) | 52 (52) | 12.664 | +0.868 | 8.399 | 87% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 49 (49) | 12.233 | +1.118 | — | — |  |
| margin | pf-cfb (challenger) | 52 (52) | 12.567 | -0.826 | 9.001 | 79% | — |
| margin | script-engine-scoring-baseline (incumbent) | 49 (49) | 13.110 | -0.829 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 52 (31) | 6.702 | +2.410 | 4.771 | 83% | — |
| passing_yards | pf-cfb (challenger) | 54 (31) | 56.590 | +40.128 | 41.254 | 91% | — |
| receiving_yards | pf-cfb (challenger) | 480 (31) | 17.235 | +4.463 | 11.137 | 82% | — |
| receptions | pf-cfb (challenger) | 400 (31) | 1.254 | +0.211 | 0.828 | 83% | — |
| rushing_yards | pf-cfb (challenger) | 243 (31) | 28.058 | -0.644 | 19.171 | 77% | — |
| team_points | pf-cfb (challenger) | 104 (52) | 8.614 | +0.434 | 6.117 | 81% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 96 (49) | 9.044 | +0.333 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **INSUFFICIENT** — 52 settled forecasts (< 100)
- margin: **INSUFFICIENT** — 52 settled forecasts (< 100)
- passing_attempts: **INSUFFICIENT** — 52 settled forecasts (< 100)
- passing_yards: **INSUFFICIENT** — 54 settled forecasts (< 100)
- receiving_yards: **ALERT** — bias +4.463 (95% CI +2.819..+6.391) exceeds 5% of the mean
- receptions: **ALERT** — bias +0.211 (95% CI +0.039..+0.352) exceeds 5% of the mean
- rushing_yards: **WATCH** — CRPS 110% of historical holdout
- team_points: **OK** — within expectations
