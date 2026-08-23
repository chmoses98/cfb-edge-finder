import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ingest_schedule  # noqa: E402


@pytest.fixture(autouse=True)
def _redirect_artifact_dir(tmp_path, monkeypatch):
    # Never let a test run touch the real repo's data/schedules/.
    monkeypatch.setattr(ingest_schedule, "SCHEDULE_ARTIFACT_DIR", tmp_path / "schedules")
    yield


def test_fixture_mode_end_to_end_matches_hand_verified_counts():
    games, summary = ingest_schedule.run_ingestion(2026, "fixture", ingest_schedule.DEFAULT_FIXTURE_PATH)
    # These exact counts were hand-verified against
    # src/cfb_edge_finder/data/fixtures/cfbd_games_2026_sample.json's 15
    # synthetic rows. The FBS-vs-FCS game (Ole Miss vs Furman) is now
    # RETAINED (not filtered) per the FBS-vs-FCS inclusion policy fix --
    # Furman resolves to a generated slug rather than dropping the game.
    # 2 unresolved team aliases remain: bare "Miami" (ambiguous) and an
    # unregistered *FBS* program name (still fails loud, unlike the FCS case).
    assert summary.source_games_fetched == 15
    assert summary.non_fbs_filtered == 0
    assert summary.fbs_games_retained == 13
    assert len(summary.unresolved_team_aliases) == 2
    assert summary.neutral_site_games == 6
    assert summary.postseason_games == 6
    assert summary.validation_failures == []
    assert len(games) == 13


def test_fixture_mode_produces_no_duplicate_game_ids():
    games, _ = ingest_schedule.run_ingestion(2026, "fixture", ingest_schedule.DEFAULT_FIXTURE_PATH)
    game_ids = [g.game_id for g in games]
    assert len(game_ids) == len(set(game_ids))


def test_write_artifact_round_trips():
    games, _ = ingest_schedule.run_ingestion(2026, "fixture", ingest_schedule.DEFAULT_FIXTURE_PATH)
    path = ingest_schedule.write_artifact(2026, games, "fixture")
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["season"] == 2026
    assert data["source_mode"] == "fixture"
    assert data["game_count"] == len(games)
    assert len(data["games"]) == len(games)
    # sorted deterministically by game_id
    assert [g["game_id"] for g in data["games"]] == sorted(g["game_id"] for g in data["games"])


def test_fbs_vs_fcs_game_retained_end_to_end_through_the_script():
    # The exact scenario the mission flagged: an FBS team's game against
    # an FCS opponent (Ole Miss vs Furman in the fixture) must survive the
    # full fetch -> normalize -> filter pipeline, not be silently dropped.
    games, summary = ingest_schedule.run_ingestion(2026, "fixture", ingest_schedule.DEFAULT_FIXTURE_PATH)
    ole_miss_game = next(g for g in games if g.home_team_id == "ole-miss")
    assert ole_miss_game.away_team_id == "furman"
    assert summary.non_fbs_filtered == 0


def test_reschedule_detected_across_two_runs():
    # First run establishes wk01 for vendor id 500100002 (Ohio State vs
    # Texas in the fixture). Simulate that game moving to wk02 on a later
    # pull and verify previous_game_id gets stamped via the artifact from
    # the first run.
    games_1, _ = ingest_schedule.run_ingestion(2026, "fixture", ingest_schedule.DEFAULT_FIXTURE_PATH)
    ingest_schedule.write_artifact(2026, games_1, "fixture")

    rescheduled_fixture = json.loads(ingest_schedule.DEFAULT_FIXTURE_PATH.read_text())
    for raw in rescheduled_fixture:
        if raw["id"] == 500100002:
            raw["week"] = 2  # moved from week 1 to week 2

    rescheduled_path = ingest_schedule.SCHEDULE_ARTIFACT_DIR.parent / "rescheduled_fixture.json"
    rescheduled_path.parent.mkdir(parents=True, exist_ok=True)
    rescheduled_path.write_text(json.dumps(rescheduled_fixture))

    games_2, _ = ingest_schedule.run_ingestion(2026, "fixture", rescheduled_path)
    moved = next(g for g in games_2 if g.source_game_ids.get("cfbd") == "500100002")
    assert moved.week_label == "wk02"
    assert moved.previous_game_id == "cfb-2026-wk01-texas-at-ohio-state"


def test_ordinary_kickoff_update_does_not_change_game_id_or_add_previous_game_id():
    # A kickoff-time-only update (same week) must NOT be treated as a
    # reschedule -- game_id is built without kickoff_utc by design.
    games_1, _ = ingest_schedule.run_ingestion(2026, "fixture", ingest_schedule.DEFAULT_FIXTURE_PATH)
    ingest_schedule.write_artifact(2026, games_1, "fixture")

    updated_fixture = json.loads(ingest_schedule.DEFAULT_FIXTURE_PATH.read_text())
    for raw in updated_fixture:
        if raw["id"] == 500100002:
            raw["startDate"] = "2026-08-29T19:00:00.000Z"  # a few hours earlier, same day/week

    updated_path = ingest_schedule.SCHEDULE_ARTIFACT_DIR.parent / "updated_fixture.json"
    updated_path.parent.mkdir(parents=True, exist_ok=True)
    updated_path.write_text(json.dumps(updated_fixture))

    games_2, _ = ingest_schedule.run_ingestion(2026, "fixture", updated_path)
    same_game = next(g for g in games_2 if g.source_game_ids.get("cfbd") == "500100002")
    assert same_game.game_id == "cfb-2026-wk01-texas-at-ohio-state"
    assert same_game.previous_game_id is None


def _synthetic_large_season(n_games: int) -> list[dict]:
    """A deterministic, large synthetic season -- distinct from the small
    hand-authored fixture above -- for exercising the full ingestion path
    at realistic weekly-universe scale (mission spec section 12/7).
    """
    from cfb_edge_finder.teams import REGISTRY

    team_names = [t.display_name for t in REGISTRY]
    games = []
    game_id = 900000000
    for i in range(n_games):
        home = team_names[(2 * i) % len(team_names)]
        away = team_names[(2 * i + 1) % len(team_names)]
        if home == away:
            away = team_names[(2 * i + 2) % len(team_names)]
        week = 1 + (i % 14)
        games.append(
            {
                "id": game_id + i,
                "season": 2026,
                "week": week,
                "seasonType": "regular",
                "startDate": f"2026-{8 + week // 4:02d}-{(1 + (i % 27)):02d}T19:00:00.000Z",
                "startTimeTBD": False,
                "neutralSite": False,
                "venue": f"Stadium {i}",
                "homeTeam": home,
                "homeClassification": "fbs",
                "awayTeam": away,
                "awayClassification": "fbs",
                "completed": False,
            }
        )
    return games


def test_large_synthetic_season_ingests_without_duplicate_ids_or_crashes(tmp_path):
    large_season = _synthetic_large_season(600)  # ~ a realistic multi-week FBS volume
    fixture_path = tmp_path / "large_season.json"
    fixture_path.write_text(json.dumps(large_season))

    games, summary = ingest_schedule.run_ingestion(2026, "fixture", fixture_path)

    assert summary.source_games_fetched == 600
    assert summary.non_fbs_filtered == 0  # all synthetic games are FBS-vs-FBS by construction
    assert len(games) == summary.fbs_games_retained
    game_ids = [g.game_id for g in games]
    assert len(game_ids) == len(set(game_ids))

    path = ingest_schedule.write_artifact(2026, games, "fixture")
    reloaded = json.loads(path.read_text())
    assert reloaded["game_count"] == len(games)
