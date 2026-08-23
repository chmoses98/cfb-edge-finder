"""The required "beat this or the sophistication wasn't worth it" baseline
(mission spec section 5).

Deliberately NOT opponent-adjusted: each team's expected points is just
its own trailing average points scored/allowed (shrunk toward the league
average by the same games-played-based shrinkage used everywhere else in
this package, so it isn't wildly unstable in week 1), plus one fixed
league-wide home-field-advantage constant. No opponent strength, no pace,
no efficiency decomposition, no priors, no QB signal. If Milestone C's
full model (ratings.py + priors.py + qb_continuity.py + score_model.py)
cannot beat this out-of-sample, that is the headline finding, not a
footnote (see docs/MILESTONE_C.md "Benchmark comparison").
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cfb_edge_finder.modeling.corpus import TeamGameLine
from cfb_edge_finder.modeling.leakage import AsOf, assert_strictly_before

NAIVE_SHRINKAGE_K = 4.0


@dataclass(frozen=True)
class NaiveTeamAverages:
    points_for: float
    points_against: float
    games_played: int


@dataclass(frozen=True)
class NaiveBenchmarkSnapshot:
    as_of: AsOf
    league_avg_points: float
    hfa_points: float
    team_averages: dict[str, NaiveTeamAverages]

    def expected_points_for(self, team_id: str) -> float:
        avg = self.team_averages.get(team_id)
        if avg is None:
            return self.league_avg_points
        weight = avg.games_played / (avg.games_played + NAIVE_SHRINKAGE_K)
        return weight * avg.points_for + (1 - weight) * self.league_avg_points

    def expected_points_against(self, team_id: str) -> float:
        avg = self.team_averages.get(team_id)
        if avg is None:
            return self.league_avg_points
        weight = avg.games_played / (avg.games_played + NAIVE_SHRINKAGE_K)
        return weight * avg.points_against + (1 - weight) * self.league_avg_points


def fit_naive_benchmark(lines: list[TeamGameLine], as_of: AsOf) -> NaiveBenchmarkSnapshot:
    fbs_rows = []
    for line in lines:
        if line.team_classification != "fbs":
            continue
        assert_strictly_before(line.as_of, as_of, context=f"fit_naive_benchmark row for {line.team_id}")
        fbs_rows.append(line)

    if not fbs_rows:
        return NaiveBenchmarkSnapshot(as_of=as_of, league_avg_points=27.0, hfa_points=2.0, team_averages={})

    all_points = [row.team_points for row in fbs_rows]
    league_avg_points = float(np.mean(all_points))

    home_margins = [
        row.team_points - row.opponent_points for row in fbs_rows if row.is_home and not row.is_neutral_site
    ]
    away_margins = [
        row.team_points - row.opponent_points for row in fbs_rows if not row.is_home and not row.is_neutral_site
    ]
    hfa_points = (
        (float(np.mean(home_margins)) - float(np.mean(away_margins))) / 2 if home_margins and away_margins else 2.0
    )

    by_team: dict[str, list[TeamGameLine]] = {}
    for row in fbs_rows:
        by_team.setdefault(row.team_id, []).append(row)

    team_averages = {
        team_id: NaiveTeamAverages(
            points_for=float(np.mean([r.team_points for r in rows])),
            points_against=float(np.mean([r.opponent_points for r in rows])),
            games_played=len(rows),
        )
        for team_id, rows in by_team.items()
    }

    return NaiveBenchmarkSnapshot(
        as_of=as_of, league_avg_points=league_avg_points, hfa_points=hfa_points, team_averages=team_averages
    )


def naive_expected_scores(
    snapshot: NaiveBenchmarkSnapshot, home_id: str, away_id: str, *, is_neutral_site: bool
) -> tuple[float, float]:
    """(expected_home_points, expected_away_points). Blends each team's own
    scoring average with its opponent's average points ALLOWED, since
    "my average points scored" alone ignores who's on defense today --
    but note this blend is NOT opponent-adjustED in the ratings.py sense
    (no iterative solving, no ridge fit): it is two raw trailing averages
    meeting in the middle, on purpose, to keep this baseline genuinely
    naive.
    """
    home_expected = (snapshot.expected_points_for(home_id) + snapshot.expected_points_against(away_id)) / 2
    away_expected = (snapshot.expected_points_for(away_id) + snapshot.expected_points_against(home_id)) / 2
    if not is_neutral_site:
        home_expected += snapshot.hfa_points / 2
        away_expected -= snapshot.hfa_points / 2
    return max(home_expected, 0.0), max(away_expected, 0.0)
