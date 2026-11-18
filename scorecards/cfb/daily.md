## CFB projection scorecard — last 7 days

Generated 2026-11-18T23:49:15Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 40477 across 312 games. Settled: 15038 (void/DNP 122, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 552 (67) | 4.186 | +0.082 | 2.922 | 75% | — |
| game_total | pf-cfb (challenger) | 67 (67) | 13.543 | +4.314 | 9.129 | 79% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 66 (66) | 12.868 | +4.465 | — | — |  |
| margin | pf-cfb (challenger) | 67 (67) | 13.399 | +2.741 | 9.613 | 78% | — |
| margin | script-engine-scoring-baseline (incumbent) | 67 (67) | 13.952 | +2.170 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 127 (67) | 7.882 | +2.036 | 5.502 | 82% | — |
| passing_yards | pf-cfb (challenger) | 127 (67) | 64.871 | +34.581 | 45.333 | 86% | — |
| receiving_yards | pf-cfb (challenger) | 1177 (67) | 17.145 | +3.676 | 11.064 | 82% | — |
| receptions | pf-cfb (challenger) | 1166 (67) | 1.228 | +0.156 | 0.810 | 82% | — |
| rushing_yards | pf-cfb (challenger) | 556 (67) | 27.065 | -0.610 | 18.597 | 78% | — |
| team_points | pf-cfb (challenger) | 134 (67) | 9.543 | +2.157 | 6.657 | 78% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 134 (67) | 9.619 | +2.426 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **INSUFFICIENT** — 67 settled forecasts (< 100)
- margin: **INSUFFICIENT** — 67 settled forecasts (< 100)
- passing_attempts: **ALERT** — bias +2.036 (95% CI +0.326..+3.766) exceeds 5% of the mean
- passing_yards: **ALERT** — bias +34.581 (95% CI +19.738..+48.800) exceeds 5% of the mean
- receiving_yards: **ALERT** — bias +3.676 (95% CI +2.433..+4.858) exceeds 5% of the mean
- receptions: **ALERT** — bias +0.156 (95% CI +0.071..+0.246) exceeds 5% of the mean
- rushing_yards: **OK** — within expectations
- team_points: **ALERT** — bias +2.157 (95% CI +0.011..+3.957) exceeds 5% of the mean
