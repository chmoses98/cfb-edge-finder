"""Special-teams capture (real ESPN fixture) and the point-in-time matchup evidence layer."""

from __future__ import annotations

import json
from pathlib import Path

from cfb_edge_finder.research.matchup_evidence import MIN_GAMES, evidence_coverage, team_profile_asof
from cfb_edge_finder.scripting.espn import special_teams_from_categories, team_games_from_summary
from cfb_edge_finder.scripting.gamelog import TeamGame

FIX = Path(__file__).parent / "fixtures" / "espn" / "summary_401856706.json"


def rows_from_fixture():
    return team_games_from_summary(json.loads(FIX.read_text()), observed_at="2026-11-03T00:00:00Z")


def test_special_teams_parsed_from_a_real_summary_and_periods_reconcile():
    rows = {r.team_id: r for r in rows_from_fixture()}
    mc = rows["2377"].special_teams
    assert mc["punts"] == 10 and mc["punt_yards"] == 436 and mc["punts_inside_20"] == 2
    assert mc["pr_n"] == 3 and mc["pr_yards"] == 30
    assert mc["fg_att"] == 0 and mc["xp_made"] == 2 and mc["xp_att"] == 2
    # an empty-but-present category is a real zero, not missing
    assert mc["kr_n"] == 0 and mc["kr_yards"] == 0
    other = rows["99"].special_teams
    assert other["kr_n"] == 1 and other["kr_yards"] == 19 and other["xp_made"] == 9
    assert rows["2377"].period_points == (0.0, 0.0, 14.0, 0.0)


def test_absent_categories_are_missing_never_zero_filled():
    assert special_teams_from_categories({}) is None
    only_punt = special_teams_from_categories({"punting": {"totals": ["4", "170", "42.5", "1", "2", "50"]}})
    assert only_punt["punts"] == 4 and "fg_att" not in only_punt and "kr_n" not in only_punt


def test_historical_row_shape_is_unchanged_and_roundtrips():
    r = rows_from_fixture()[0]
    d = r.as_dict()
    assert "special_teams" in d and TeamGame.from_dict(d).special_teams == r.special_teams
    bare = TeamGame.from_dict({**d, "special_teams": None})
    assert "special_teams" not in bare.as_dict()  # additive: a row without it keeps its historical bytes


def _season(n_games: int):
    base = rows_from_fixture()[0]
    import dataclasses

    out = []
    for i in range(n_games):
        out.append(dataclasses.replace(base, game_id=f"g{i}", kickoff_utc=f"2026-09-{i + 1:02d}T16:00:00Z"))
    return out


def test_profile_is_walk_forward_and_flags_thin_history():
    rows = _season(5)
    team = rows[0].team_id
    early = team_profile_asof(rows, team, "2026-09-02T00:00:00Z")  # only game 1 has finished (kickoff+4.5h < cutoff)
    assert early["games"] == 1 and f"FEWER_THAN_{MIN_GAMES}_GAMES" in early["flags"]
    late = team_profile_asof(rows, team, "2026-10-01T00:00:00Z")
    assert late["games"] == 5 and f"FEWER_THAN_{MIN_GAMES}_GAMES" not in late["flags"]
    assert late["special_teams"]["games_with_value"] == 5 and late["special_teams"]["punt_avg"] is not None
    assert any(f.startswith("MISSING_SOURCE_EPA") for f in late["flags"])  # EPA is never silently absent
    # a game that kicks off after the cutoff never leaks in
    assert team_profile_asof(rows, team, "2026-08-31T00:00:00Z")["games"] == 0


def test_coverage_counts_and_never_reads_havoc():
    rows = rows_from_fixture()
    cov = evidence_coverage(
        rows, {"2377": {"status": "OBSERVED", "items": [1], "qb_status": "Q"}, "99": {"status": "FAILED"}}
    )
    assert cov["special_teams"]["rows_with_value"] == 2 and cov["period_scoring"]["rows_with_periods"] == 2
    assert cov["injuries"] == {**cov["injuries"], "teams_in_capture": 2, "teams_observed": 1, "qb_status_known": 1}
    assert cov["havoc"] == "FORBIDDEN_NEVER_READ" and cov["epa"].startswith("MISSING_SOURCE_EPA")
