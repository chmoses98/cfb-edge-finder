"""End to end: the committed packets for real games, read the way a connector would read them."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cfb_edge_finder.modeling.forbidden_features import forbidden_in

ROOT = Path(__file__).resolve().parents[1]
PACKETS = ROOT / "data/research_packets"
MAX_BYTES = 250_000


def load(rel):
    return json.loads((ROOT / rel).read_text())


@pytest.fixture(scope="module")
def index():
    return load("data/research_packets/index.json")


@pytest.fixture(scope="module")
def rows(index):
    out = []
    for slate in index["slates"]:
        out += load(slate["path"])["games"]
    return out


def test_index_lists_every_slate_and_every_game_has_all_files(index, rows):
    assert index["contains_model_probabilities"] is False
    assert index["game_count"] == len(rows) >= 2
    for r in rows:
        for rel in [
            r["packet"],
            r["files"]["team_profiles"],
            r["files"]["schedules"],
            r["files"]["distributions"],
            *r["files"]["markets"],
        ]:
            p = ROOT / rel
            assert p.exists(), rel
            assert p.stat().st_size <= MAX_BYTES, f"{rel} exceeds the connector-safe budget"
            json.loads(p.read_text())


def test_every_game_gets_the_same_depth_of_analysis_regardless_of_slate_size(rows):
    """A 111-game Saturday gets the same sections as a 2-game Friday night; a game with no football history says so."""
    assert len({tuple(sorted(r["sections"])) for r in rows}) == 1
    full = [r for r in rows if "FOOTBALL_HISTORY_ABSENT" not in r["flag_codes"]]
    absent = [r for r in rows if "FOOTBALL_HISTORY_ABSENT" in r["flag_codes"]]
    assert len(full) > len(absent), "most games must carry the full football picture"
    for r in full:
        assert r["sections"]["matchup_comparison"] == "OK", r["game_key"]
        assert r["sections"]["schedules"] == "OK", r["game_key"]
        assert r["sections"]["distributions"] in ("CONDITIONAL", "THIN_REFERENCE_CLASS"), r["game_key"]
    for r in absent:  # non-FBS vs non-FBS: honestly flagged, results-only schedule, never a silent substitute
        assert r["sections"]["matchup_comparison"] == "UNAVAILABLE" and r["sections"]["distributions"] == "UNAVAILABLE"
        assert r["sections"]["schedules"] in ("RESULTS_ONLY", "UNAVAILABLE")
    for r in rows:
        assert r["sections"]["markets"] == "OK" and r["sections"]["injuries"] == "NO_STRUCTURED_SOURCE", r["game_key"]
        assert "QB_NOT_CONFIRMED" in r["flag_codes"] and "NO_STRUCTURED_INJURY_SOURCE" in r["flag_codes"]


@pytest.mark.parametrize("key", ["26NOV06UNMNEV", "26NOV06TCUARIZ"])
def test_nov_6_test_games_carry_the_full_football_picture(key):
    base = f"data/research_packets/games/{key}"
    packet, profiles, sched, dist = (
        load(f"{base}/{n}.json") for n in ("packet", "team_profiles", "schedules", "distributions")
    )
    # opponent adjustment is genuine: adjusted values differ from raw and carry a standard error and a rank
    rows = packet["football"]["matchup_comparison"]
    assert len(rows) >= 30
    differing = [
        r
        for r in rows
        if r["offense_adjusted"] is not None
        and r["offense_raw"] is not None
        and abs(r["offense_adjusted"] - r["offense_raw"]) > 1e-4
    ]
    assert len(differing) > len(rows) * 0.5
    for side in ("home", "away"):
        metrics = profiles["teams"][side]["metrics"]
        sr = metrics["efficiency"]["success_rate"]["offense"]
        assert sr["adjusted_available"] and sr["standard_error"] is not None and sr["rank"] and sr["games"] >= 3
        assert {
            "passing",
            "rushing",
            "explosiveness",
            "pressure_and_protection",
            "finishing_red_zone",
            "pace_and_tendency",
        } <= set(metrics)
        # exact schedules with opponent quality
        games = sched["teams"][side]["games"]
        assert len(games) >= 5
        assert all("opponent_net_rating_rank" in g and "points_for" in g and "opponent" in g for g in games)
        assert sched["teams"][side]["strength_of_schedule"]["games"] == len(games)
        assert "against_similar_opponents" in sched["teams"][side]
        assert sched["recent_form"][side]["small_sample_warning"] in (True, False)
    # first-half / first-quarter team splits are present, raw and labelled so
    for side in ("home", "away"):
        ps = packet["football"]["period_splits_raw"][side]
        assert ps["adjusted"] is False and ps["games"] >= 3 and "first_half" in ps and "q1" in ps
    # QB: projected, never presented as confirmed
    assert packet["qb"]["status"] == "PROJECTED_ONLY"
    assert packet["qb"]["home"]["projected_starter"] and packet["qb"]["home"]["confirmed_starter"] is None
    # scoring context with limits, and a historical reference class
    assert packet["football"]["baseline"]["accuracy"]["total_points"]["naive_league_total_mae"]
    assert dist["reference_class"]["status"] in ("CONDITIONAL", "THIN_REFERENCE_CLASS")
    assert dist["reference_class"]["first_half"]["games_with_linescores"] > 0
    assert dist["reference_class"]["closest_games"]
    flags = {f["code"] for f in packet["data_quality"]["missing_data_flags"]}
    assert {"QB_NOT_CONFIRMED", "NO_STRUCTURED_INJURY_SOURCE", "EPA_UNAVAILABLE"} <= flags and any(
        f.startswith("PERIOD_") for f in flags
    )
    # owner rule: nothing forbidden is published as a metric
    for side in ("home", "away"):
        for group in profiles["teams"][side]["metrics"].values():
            assert not forbidden_in(list(group))


def test_packets_never_originate_probabilities_or_bets(rows):
    for r in rows[:30]:
        packet = load(r["packet"])
        assert packet["analyst_worksheet"]["your_output_per_game"]["evidence_tier"] == "ANALYST_JUDGMENT"
        assert packet["analyst_worksheet"]["your_output_per_game"]["winner_probability"] == {
            "point": None,
            "low": None,
            "high": None,
        }
        text = json.dumps(load(r["files"]["markets"][0])).lower()
        assert 'contains_model_probabilities":false' in text.replace(" ", "")
        assert "fair_value" not in text and "kelly" not in text


def test_verifier_passes_on_the_committed_packets(rows):
    """The whole committed index, not named games: games leave the index once they kick off."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts/verify_research_packets.py")], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"verified {len(rows)} games" in proc.stdout


def test_bet_up_to_reference_table_is_present_and_monotone():
    table = load("data/research_packets/reference/bet_up_to_table.json")
    assert table["unit_dollars"] == 75.0 and table["tables_by_fee_multiplier"]
    for body in table["tables_by_fee_multiplier"].values():
        by_p = body["bet_up_to_cents_by_probability_lower_bound_pct"]
        vals = [by_p[str(p)] for p in range(1, 100) if by_p[str(p)] is not None]
        assert vals == sorted(vals) and len(vals) > 80
