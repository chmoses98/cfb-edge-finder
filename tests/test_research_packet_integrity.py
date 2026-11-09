"""Historical integrity: time cutoffs, game identity, orientation, opponent adjustment and reference-class leakage."""

from __future__ import annotations

import gzip
import json
import re
import unicodedata
from pathlib import Path

import pytest

from cfb_edge_finder.research_packet import history as hist
from cfb_edge_finder.research_packet import markets as mk
from cfb_edge_finder.scripting.football import LeagueFitCache
from cfb_edge_finder.scripting.gamelog import before, data_cutoff, read_jsonl
from cfb_edge_finder.scripting.matchup import GameIdentity, matchup_vector

ROOT = Path(__file__).resolve().parents[1]
ALIASES = {
    "liu": "long island university",
    "university at albany": "ualbany",
    "penn": "pennsylvania",
    "tennessee-martin": "ut martin",
    "louisiana-monroe": "ul monroe",
    "umass": "massachusetts",
}


def load(rel):
    return json.loads((ROOT / rel).read_text())


def _norm(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\bst\b\.?", "state", text)
    return set(re.sub(r"[^a-z ]", "", ALIASES.get(text, text)).split())


@pytest.fixture(scope="module")
def packet_rows():
    index = load("data/research_packets/index.json")
    rows = []
    for slate in index["slates"]:
        rows += load(slate["path"])["games"]
    return rows


@pytest.fixture(scope="module")
def frozen_games():
    """Frozen historical extract (scripts/build_integrity_fixture.py): minimum-sample assertions run against this, never
    against however many games happen to be on the current slate."""
    games = load("tests/fixtures/research_packet_integrity_frozen.json")["games"]
    assert len(games) >= 100  # the fixture itself must not shrink
    return games


# ---- historical reference rows ------------------------------------------------------------------------
def test_every_history_linescore_reconciles_to_its_own_final_score_with_the_same_orientation():
    games = hist.load_games(ROOT / "data/research_packets/reference/history_games.jsonl.gz")
    assert len(games) > 1500
    for g in games:
        if g["home_periods"]:
            assert sum(g["home_periods"]) == g["actual_home"], g["game_id"]
            assert sum(g["away_periods"]) == g["actual_away"], g["game_id"]
    manifest = load("data/research_packets/reference/history_manifest.json")
    assert manifest["linescores_dropped_unreconciled"] == 0 and manifest["carries_market_data"] is False
    assert manifest["linescores_reoriented_to_match_final_score"] >= 2  # the two 2024 neutral-site bowls


def test_history_records_were_built_before_kickoff():
    for season in (2024, 2025, 2026):
        with gzip.open(ROOT / f"data/scripting/validation/records_{season}.jsonl.gz", "rt") as fh:
            for line in fh:
                r = json.loads(line)
                assert r["cutoff_check"] is True and r["cutoff"] < r["kickoff_utc"]


def test_2026_history_baselines_reproduce_from_pre_cutoff_rows_only():
    """Rebuild stored 2026 baselines with today's code from ONLY the games before each cutoff: identical numbers
    prove the baseline methodology is unchanged and the stored predictions did not see their own game."""
    rows = tuple(read_jsonl(ROOT / "data/football/2026/team_games.jsonl"))
    sched = {e["id"]: e for e in load("data/football/2026/schedule.json")["events"]}
    with gzip.open(ROOT / "data/scripting/validation/records_2026.jsonl.gz", "rt") as fh:
        records = [json.loads(line) for line in fh]
    cache, checked = LeagueFitCache(), 0
    for r in records[::7]:
        ev = sched.get(str(r["game_id"]))
        if r.get("pred_total") is None or ev is None:
            continue
        by = {c["home_away"]: c for c in ev["competitors"]}
        ident = GameIdentity(
            event_id=str(r["game_id"]),
            home_id=by["home"]["team_id"],
            away_id=by["away"]["team_id"],
            home_name=r["home"],
            away_name=r["away"],
            kickoff_utc=r["kickoff_utc"],
            neutral_site=bool(ev.get("neutral_site")),
            season=2026,
        )
        cutoff = data_cutoff(r["kickoff_utc"])
        history = list(before(rows, cutoff))
        assert all(x.kickoff_utc < cutoff and x.game_id != str(r["game_id"]) for x in history)
        sb = matchup_vector(cache.get(rows, cutoff), history, ident)["scoring_baseline"]
        if sb.get("home_points") is None:
            continue
        assert abs(sb["home_points"] - r["pred_home"]) < 0.05 and abs(sb["away_points"] - r["pred_away"]) < 0.05
        checked += 1
    assert checked >= 20


# ---- reference class leakage ---------------------------------------------------------------------------
def test_reference_class_excludes_games_at_or_after_the_cutoff_and_the_game_itself():
    games = [
        {"game_id": "1", "kickoff_utc": "2026-10-01T00:00:00Z"},
        {"game_id": "2", "kickoff_utc": "2026-11-06T09:00:00Z"},  # exactly at the cutoff: not history
        {"game_id": "3", "kickoff_utc": "2026-11-08T00:00:00Z"},  # after: would be leakage
        {"game_id": "4", "kickoff_utc": "2026-10-20T00:00:00Z"},  # the game itself
    ]
    kept = hist.eligible_reference(games, "2026-11-06T09:00:00Z", "4")
    assert [g["game_id"] for g in kept] == ["1"]
    assert len(hist.eligible_reference(games, None, None)) == 4


def test_packets_never_contain_games_from_after_their_data_cutoff(packet_rows):
    for r in packet_rows[:40]:
        packet = load(r["packet"])
        cutoff = packet["inputs"].get("football_data_cutoff")
        if not cutoff:
            continue
        assert (packet["inputs"]["history_through_kickoff_utc"] or "") < cutoff
        sched = load(r["files"]["schedules"])
        own = packet["teams"]["identity"]["espn_event_id"]
        for side, team in sched["teams"].items():
            for g in team["games"]:
                assert g["kickoff_utc"] < cutoff, (r["game_key"], side)
                assert g["game_id"] != own


# ---- identity and orientation --------------------------------------------------------------------------
def _check_moneyline_attribution(game_key, teams, swapped_markets, swapped_packet, contracts):
    assert swapped_markets == bool(swapped_packet)
    checked = 0
    for c in contracts:
        assert c.get("side") in ("home", "away"), game_key
        assert _norm(c["yes_means"]) & _norm(teams[c["side"]]), (game_key, c["yes_means"], teams[c["side"]])
        checked += 1
    return checked


def test_every_moneyline_contract_is_attributed_to_the_football_team_it_names(packet_rows):
    """Live slate: every moneyline contract present must name the football team on its side (no minimum count)."""
    for r in packet_rows:
        packet = load(r["packet"])
        markets = load(r["files"]["markets"][0])
        _check_moneyline_attribution(
            r["game_key"],
            {s: packet["teams"][s]["name"] for s in ("home", "away")},
            markets["side_teams"]["orientation_swapped_vs_kalshi_key"],
            packet["teams"]["identity"].get("orientation_swapped"),
            [c for c in markets["contracts"] if c["family"] == "game_moneyline"],
        )


def test_moneyline_attribution_holds_across_a_frozen_historical_sample(frozen_games):
    checked = sum(
        _check_moneyline_attribution(
            g["game_key"],
            g["teams"],
            g["orientation_swapped_markets"],
            g["orientation_swapped_packet"],
            g["moneyline_contracts"],
        )
        for g in frozen_games
    )
    assert checked > 100


def _q(ticker, line, ya, yb):
    return {
        "market_ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0],
        "family": "game_spread",
        "title": "t",
        "status": "active",
        "floor_strike": line,
        "strike_type": "greater",
        "is_alternate_line": False,
        "book_state": "two_sided",
        "yes_ask": ya,
        "yes_bid": yb,
        "no_ask": round(1 - yb, 2),
        "no_bid": round(1 - ya, 2),
        "yes_ask_size": 10.0,
        "yes_bid_size": 10.0,
        "buy_yes_executable": True,
        "buy_no_executable": True,
        "buy_yes_fee": 0.01,
        "buy_no_fee": 0.01,
        "updated_time": "t",
    }


def test_swapped_orientation_keeps_every_contract_side_on_the_football_games_home_away():
    games = [
        {
            "game_id": str(i),
            "season": 2025,
            "home": "H",
            "away": "A",
            "pred_home": 30,
            "pred_away": 20,
            "pred_total": 50,
            "actual_home": 30,
            "actual_away": 20,
            "home_periods": None,
            "away_periods": None,
        }
        for i in range(10)
    ]
    quotes = [
        _q("KXNCAAFSPREAD-26NOV07AAABBB-AAA3", 2.5, 0.5, 0.4),
        _q("KXNCAAFSPREAD-26NOV07AAABBB-BBB3", 2.5, 0.5, 0.4),
    ]
    args = {
        "game_key": "26NOV07AAABBB",
        "quote_files": [{"quotes": quotes, "game_fingerprint": "f"}],
        "event_files": {},
        "capture": {},
        "reference_games": games,
    }
    straight = {c["ticker"]: c for c in mk.build_markets(**args)["contracts"]}
    swapped = {
        c["ticker"]: c
        for c in mk.build_markets(**args, orientation_swapped=True, team_names={"home": "AAA U", "away": "BBB U"})[
            "contracts"
        ]
    }
    # Kalshi key AAABBB = AAA away, BBB home. Football game says the reverse: AAA is HOME.
    assert (
        straight["KXNCAAFSPREAD-26NOV07AAABBB-AAA3"]["side"] == "away"
        and swapped["KXNCAAFSPREAD-26NOV07AAABBB-AAA3"]["side"] == "home"
    )
    assert straight["KXNCAAFSPREAD-26NOV07AAABBB-AAA3"]["ref_freq"]["yes"] == 0.0  # away never wins by 3+
    assert swapped["KXNCAAFSPREAD-26NOV07AAABBB-AAA3"]["ref_freq"]["yes"] == 1.0  # home wins by 10 every time
    assert mk.build_markets(**args, orientation_swapped=True)["side_teams"]["orientation_swapped_vs_kalshi_key"] is True


# ---- opponent adjustment -------------------------------------------------------------------------------
def _assert_adjustment_tracks_defenses_faced(seen, min_teams):
    assert len(seen) >= min_teams
    xs, ys = zip(*seen.values(), strict=True)
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    corr = cov / (sum((x - mx) ** 2 for x in xs) ** 0.5 * sum((y - my) ** 2 for y in ys) ** 0.5)
    assert corr < -0.2, corr


def test_adjusted_scoring_moves_with_the_defenses_a_team_actually_faced(frozen_games):
    """Across teams with 6+ games, (adjusted - raw) points per game falls as the average rank of the defenses faced
    worsens: a team that lit up weak defenses is credited less than its raw number. Shrinkage toward the prior is a
    confound, so the bar is a clearly negative correlation, not a tight fit. Runs on the frozen historical sample so
    the minimum team count does not depend on the current slate size."""
    seen = {}
    for g in frozen_games:
        for side in ("home", "away"):
            ppg = g["ppg_offense"][side]
            faced = g["avg_opponent_defense_rank_points"][side]
            if ppg and ppg["games"] >= 6 and ppg["adjusted"] is not None and faced:
                seen[g["team_ids"][side]] = (ppg["adjusted"] - ppg["raw"], faced)
    _assert_adjustment_tracks_defenses_faced(seen, 60)
