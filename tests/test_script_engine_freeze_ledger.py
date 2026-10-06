"""Freezing, the prospective ledger, realized-script classification and the report."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from script_engine_fakes import availability, identity, synthetic_season

from cfb_edge_finder.scripting.football import FootballPacket, build_content
from cfb_edge_finder.scripting.freeze import content_hash, freeze, verify
from cfb_edge_finder.scripting.gamelog import TeamGame
from cfb_edge_finder.scripting.ledger import (
    FINAL_PREGAME,
    PUBLICATION,
    LedgerOrderError,
    append,
    checkpoint_for,
    load_artifact,
    read_rows,
    row_for,
    store_artifact,
)
from cfb_edge_finder.scripting.realized import (
    AMBIGUOUS,
    classify,
    realized_features,
    realized_record,
    score_scripts,
    settle_expression,
)
from cfb_edge_finder.scripting.report import build_report, render_markdown


def _packet(rows, avail=None) -> FootballPacket:
    return FootballPacket(
        identity=identity(),
        game_key="KEY",
        rows=tuple(rows),
        availability=avail or availability(),
        identity_check={"status": "PASS", "orientation_swapped": False},
        freshness={"status": "FRESH"},
    )


@pytest.fixture(scope="module")
def season():
    return synthetic_season()


def test_same_inputs_same_hash_and_the_original_timestamp_is_kept(season):
    first = freeze(build_content(_packet(season)), generated_at="2026-10-22T12:00:00Z")
    again = freeze(build_content(_packet(season)), generated_at="2026-10-23T12:00:00Z", previous_envelope=first)
    assert again is first
    assert again["generated_at"] == "2026-10-22T12:00:00Z"
    assert verify(first)


def test_a_football_change_regenerates_and_records_why(season):
    first = freeze(build_content(_packet(season)), generated_at="2026-10-22T12:00:00Z")
    changed = freeze(
        build_content(_packet(season, avail=availability(qb_uncertain=True))),
        generated_at="2026-10-23T12:00:00Z",
        previous_envelope=first,
    )
    assert changed["artifact_hash"] != first["artifact_hash"]
    assert "AVAILABILITY_CHANGED" in changed["regeneration"]["reasons"]
    assert changed["regeneration"]["history"][-1]["replaced_hash"] == first["artifact_hash"]


def test_a_new_league_game_is_named_as_the_reason(season):
    first = freeze(build_content(_packet(season[:-2])), generated_at="2026-10-22T12:00:00Z")
    second = freeze(build_content(_packet(season)), generated_at="2026-10-23T12:00:00Z", previous_envelope=first)
    assert any(r.startswith("LEAGUE_GAMELOG_CHANGED") for r in second["regeneration"]["reasons"])


def test_the_hash_rejects_keys_outside_the_frozen_set(season):
    content = build_content(_packet(season))
    content["market_price"] = 0.5
    with pytest.raises(ValueError):
        content_hash(content)


def test_ledger_is_append_only_pregame_only_and_content_addressed(tmp_path, season):
    envelope = freeze(build_content(_packet(season)), generated_at="2026-10-23T12:00:00Z")
    kickoff = envelope["content"]["kickoff_utc"]
    assert checkpoint_for(kickoff, "2026-10-23T12:00:00Z") == PUBLICATION
    assert checkpoint_for(kickoff, "2026-10-24T22:30:00Z") == FINAL_PREGAME
    assert checkpoint_for(kickoff, kickoff) is None
    row = row_for(
        game_key="KEY",
        season=2026,
        envelope=envelope,
        market_map=None,
        checkpoint=PUBLICATION,
        recorded_at="2026-10-23T12:00:00Z",
        prices_captured_at=None,
    )
    store_artifact(tmp_path, 2026, envelope)
    assert append(tmp_path, 2026, row) is True
    assert append(tmp_path, 2026, row) is False
    assert len(read_rows(tmp_path, 2026)) == 1
    assert load_artifact(tmp_path, 2026, envelope["artifact_hash"]) == json.loads(json.dumps(envelope))
    with pytest.raises(LedgerOrderError):
        row_for(
            game_key="KEY",
            season=2026,
            envelope=envelope,
            market_map=None,
            checkpoint=PUBLICATION,
            recorded_at=kickoff,
            prices_captured_at=None,
        )


def _final(home_pts: float, away_pts: float, home_success: float, away_success: float) -> tuple[TeamGame, TeamGame]:
    base = synthetic_season()[0]

    def make(team, opp, site, pts, opp_pts, success):
        pbp = dict(base.pbp)
        pbp.update(scrim_plays=60.0, success_plays=round(60 * success), explosive_yards=80.0, scrim_yards=400.0)
        return replace(
            base,
            game_id="FINAL",
            team_id=team,
            opponent_id=opp,
            site=site,
            points_for=pts,
            points_against=opp_pts,
            pbp=pbp,
        )

    return (
        make("F00", "F01", "home", home_pts, away_pts, home_success),
        make("F01", "F00", "away", away_pts, home_pts, away_success),
    )


def test_realized_classification_is_objective_and_allows_ambiguity():
    home, away = _final(31, 17, 0.50, 0.38)
    features = realized_features(home, away)
    assert features["home_margin"] == 14 and features["efficiency_winner"] == "home"
    assert "HOME_CONTROL" in classify(features, {"home_points": 28, "away_points": 20}, "home")["labels"]
    close_home, close_away = _final(24, 21, 0.40, 0.44)
    labels = classify(realized_features(close_home, close_away), {}, "home")["labels"]
    assert "COMPETITIVE_TOSSUP" in labels and "UNDERDOG_HANGS_AROUND" in labels
    odd_home, odd_away = _final(30, 0, 0.30, 0.45)  # won big while losing the efficiency battle
    assert classify(realized_features(odd_home, odd_away), {}, None)["primary"] == AMBIGUOUS


def test_a_script_described_the_game_only_if_every_stated_band_held(season):
    content = build_content(_packet(season))
    scripts = content["game_scripts"]["scripts"]
    home, away = _final(31, 17, 0.50, 0.38)
    scored = {s["archetype"]: s for s in score_scripts(scripts, realized_features(home, away))}
    assert scored["HOME_CONTROL"]["checks"]["home_margin"] is True
    assert scored["HOME_CONTROL"]["checks"]["mechanism"] is True
    if "FAVORITE_PULLS_AWAY" in scored:
        assert scored["FAVORITE_PULLS_AWAY"]["described"] is False  # 14 < 17


def test_expressions_settle_from_their_exact_win_set():
    features = {"home_margin": 14, "total_points": 48, "home_points": 31, "away_points": 17}
    assert settle_expression({"variable": "full_game:home_margin", "at_least": 4, "at_most": None}, features) is True
    assert settle_expression({"variable": "full_game:home_margin", "at_least": 15, "at_most": None}, features) is False
    assert settle_expression({"variable": "full_game:total_points", "at_least": None, "at_most": 48}, features) is True
    assert settle_expression(None, features) is None


def test_report_joins_prospective_rows_to_outcomes_only(tmp_path, season):
    envelope = freeze(build_content(_packet(season)), generated_at="2026-10-23T12:00:00Z")
    row = row_for(
        game_key="KEY",
        season=2026,
        envelope=envelope,
        market_map=None,
        checkpoint=PUBLICATION,
        recorded_at="2026-10-23T12:00:00Z",
        prices_captured_at=None,
    )
    home, away = _final(31, 17, 0.50, 0.38)
    record = realized_record(home, away, row, league_plays=66.0)
    record["kind"] = row["kind"]
    record["expressions"] = []
    report = build_report([row], [record])
    assert report["settled"]["games"] == 1
    assert report["script_accuracy"]["primary_described"] == 1.0
    assert report["calibration_readiness"]["ready"] is False
    assert "Calibration ready" in render_markdown(report)
    # A realized record with no pregame row cannot enter the report.
    orphan = dict(record, game_key="OTHER", artifact_hash="none")
    assert build_report([row], [orphan])["script_accuracy"]["by_data_confidence"].get("UNKNOWN")
