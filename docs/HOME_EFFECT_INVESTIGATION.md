# CFB early-season home effect — root cause and experimental fix (RESEARCH_ONLY)

Nothing here is active. `scripting/home_effect_experimental.py` has `ENABLED = False`, no production module imports it
(`tests/test_home_effect_experimental.py::test_no_production_module_imports_the_experiment`), and the published
scoring baseline (`matchup.scoring_baseline`) is unchanged.

## Symptom
SIFT Alpha PR #98 found the incumbent's applied home effect (2h, added to every home margin) averaged 11.9 points in
2026 weeks 1–5 vs 4.4 (2024) and 6.1 (2025), and that 2026 home margins were over-projected by ~6 points.

## Root cause: the venue term is not identified early in a season
`adjust.fit_metric` fits `points_per_game` on the **current season only**:
`y = mu + h·x + o_team + d_opp (+ non-FBS offsets)`, team effects shrunk to league average with a 4-game prior, `h`
penalised with a 1-game prior shared by every row.

| Hypothesis | Verdict | Evidence |
|---|---|---|
| Arithmetic / double counting | **No** | `h` is half the home/away gap by construction; margin = +2h is consistent; neutral sites use x = 0 (pinned by `test_scoring_margin_matches_the_baseline_formula`). |
| Early-season instability + weak opponent adjustment (confounding) | **Yes — root cause** | After week 1 each team has one game and the schedule is not random: strong programmes host weaker ones. "Home team was better" and "home teams score more" cannot be separated; the ridge prior decides, and the cheap shared `h` absorbs the strength gap. Relaxing the team prior from 4 to 0.25 games moves 2026 week-3 2h from 12.9 to 7.5 (2025: 11.2 → 6.5). Reproduced synthetically in `test_buy_game_confounding_...` (true 2h = 3, incumbent > 8). |
| Inappropriate weight on recent home performance | No | `h` is one league-wide coefficient with no recency weighting. |
| Missing venue distinctions | Not the driver | The bias is in true home games; neutral sites already get x = 0. Venue detail (altitude, travel) is unmodelled but cannot explain a week-dependent 2–3× swing. |
| Legitimate large signal | No | Out of sample the incumbent over-projects home margins in weeks 1–5 in every development season (+2.1 points mean, 2015–2023), and 2026 most (+6.4). |

2026 was worst because its opening-week home margins were the most lopsided of the three seasons (week-1 fit: 2h = 19.8).

## Fix (experimental, disabled)
Pre-game information from the **previous, finished** season enters the same ridge as prior means:
1. `h` is shrunk toward the previous season's end-of-season `h` (weight `home_prior_games`).
2. Team effects are shrunk toward `carryover ×` their previous end-of-season values instead of zero, so a strong host is
   explained by strength, not venue.

With `carryover = 0, home_prior_games = 1` it reproduces the incumbent exactly (pinned). Shrinking `h` alone
(`carryover = 0`) helps much less — the strength gap then leaks into under-shrunk team effects instead.

## Walk-forward evaluation (identical games, pre-game data only)
`scripts/research_home_effect.py`; output `data/scripting/validation/home_effect_experimental/`.
Grid (16 configs) scored on **2015–2023 only**; best by margin RMSE frozen as `FROZEN_CANDIDATE`
(`home_prior_games = 300, carryover = 0.7`; edge of the pre-registered grid, not extended). Then scored once on 2024–25 and 2026.

| Split | Games | Incumbent RMSE | Candidate RMSE | Paired Δ sq. err (SE) | Home-site bias inc → cand | Winner acc inc → cand |
|---|---|---|---|---|---|---|
| Dev 2015–23 | 6,465 | 17.68 | 16.71 | −33.6 (2.1) | +1.50 → +0.48 | 70.4% → 72.7% |
| Val 2024–25 | 1,577 | 16.89 | 16.00 | −29.6 (3.8) | +1.30 → +0.19 | 69.1% → 71.9% |
| Test 2026 | 610 | 17.75 | 16.13 | −54.8 (8.1) | +4.32 → +1.65 | 65.3% → 68.0% |
| Test 2026 weeks 1–5 | 244 | 19.83 | 16.53 | −120.1 (17.8) | +6.42 → +1.01 | 68.9% → 75.8% |
| Test 2026 weeks 6+ (never inspected before) | 366 | 16.22 | 15.86 | −11.4 (5.3) | +2.92 → +2.07 | 62.8% → 62.8% |

Mean applied 2h in weeks 1–5, incumbent → candidate: 2024 6.1 → 3.5, 2025 9.5 → 5.2, **2026 12.0 → 5.6**.
Totals are essentially unchanged (MAE 13.00 → 13.12 on 2026).

## Limits
- A margin-accuracy improvement, not a betting result. Contract-level evaluation is in SIFT Alpha (`research/cfb_bet_selection`).
- 2026 late-season home bias (+2.1) remains: 2026's in-season `h` itself sits above prior seasons.
- Promotion would require owner review, a methodology version bump and re-freezing the published baseline.
