"""The ESPN parser, against payloads captured from the real 2026-10-03 slate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cfb_edge_finder.scripting.espn import (
    division_of,
    divisions_from_scoreboard,
    team_games_from_summary,
)
from cfb_edge_finder.scripting.gamelog import BOX_KEYS

FIXTURES = Path(__file__).parent / "fixtures" / "espn"


def _divisions() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(FIXTURES.glob("scoreboard_*.json")):
        out.update(divisions_from_scoreboard(json.loads(path.read_text())["events"]))
    return out


def _rows(event_id: str):
    summary = json.loads((FIXTURES / f"summary_{event_id}.json").read_text())
    return {
        r.team: r for r in team_games_from_summary(summary, observed_at="2026-10-04T00:00:00Z", divisions=_divisions())
    }


def test_box_score_counts_match_the_published_box():
    rows = _rows("401856705")  # Vanderbilt at Georgia, 2026-10-03
    uga, van = rows["Georgia"], rows["Vanderbilt"]
    assert (uga.points_for, uga.points_against, uga.site) == (38.0, 14.0, "home")
    assert van.site == "away"
    assert uga.box["total_yards"] == 471 and uga.box["rush_att"] == 34 and uga.box["pass_att"] == 32
    assert uga.box["plays"] == 66
    assert (uga.box["third_conv"], uga.box["third_att"]) == (7, 10)
    assert uga.box["possession_seconds"] == 32 * 60 + 48
    assert uga.box["def_sacks"] == 2 and uga.box["def_tfl"] == 6
    assert set(uga.box) == set(BOX_KEYS)
    assert uga.primary_passer == "Gunner Stockton"
    assert uga.kickoff_utc == "2026-10-03T16:45:00Z"


def test_play_log_reconciles_with_the_box_score():
    for event_id in ("401856705", "401856706", "401858249", "401858250"):
        for row in _rows(event_id).values():
            assert row.pbp is not None, row.notes
            logged = row.pbp["scrim_plays"] + row.pbp["garbage_plays_excluded"]
            assert 0.75 * row.box["plays"] <= logged <= 1.25 * row.box["plays"]
            assert row.pbp["success_plays"] <= row.pbp["scrim_plays"]
            assert row.pbp["rush_plays"] + row.pbp["pass_plays"] == row.pbp["scrim_plays"]


def test_sacks_in_the_play_log_match_the_opponents_box_credit():
    rows = _rows("401858249")  # Miami at Clemson: Miami credited with 6 sacks
    assert rows["Miami"].box["def_sacks"] == 6
    assert rows["Clemson"].pbp["sacks_taken"] == 6


def test_garbage_time_is_excluded_not_dropped():
    lsu = _rows("401856706")["LSU"]  # 63-14 over McNeese
    assert lsu.pbp["garbage_plays_excluded"] > 0
    assert lsu.pbp["scrim_plays"] + lsu.pbp["garbage_plays_excluded"] == lsu.box["plays"]


def test_divisions_come_from_conference_ids():
    rows = _rows("401856706")
    assert rows["LSU"].team_division == "fbs"
    assert rows["McNeese"].team_division == "fcs"
    assert division_of("8") == "fbs" and division_of("29") == "fcs" and division_of(None) == "unknown"


def test_an_inconsistent_play_log_is_dropped_with_a_reason_never_half_used():
    summary = json.loads((FIXTURES / "summary_401856705.json").read_text())
    summary["drives"]["previous"] = summary["drives"]["previous"][:3]
    rows = team_games_from_summary(summary, observed_at="2026-10-04T00:00:00Z")
    assert all(r.pbp is None for r in rows)
    assert all(any("play log dropped" in n for n in r.notes) for r in rows)


def test_an_incomplete_game_yields_no_rows():
    summary = json.loads((FIXTURES / "summary_401856705.json").read_text())
    summary["header"]["competitions"][0]["status"] = {"type": {"completed": False}}
    assert team_games_from_summary(summary, observed_at="x") == []


@pytest.mark.parametrize("event_id", ["401856705", "401858250"])
def test_no_market_field_survives_parsing(event_id):
    for row in _rows(event_id).values():
        blob = json.dumps(row.as_dict()).lower()
        for word in ("odds", "spread", "overunder", "moneyline", "pickcenter"):
            assert word not in blob
