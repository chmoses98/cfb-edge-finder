"""The enriched per-game Kalshi menu (research_packet.markets)."""

from __future__ import annotations

import glob
import json
from pathlib import Path

from cfb_edge_finder.research_packet import markets as mk

ROOT = Path(__file__).resolve().parents[1]


def quote(ticker, family, line, yes_ask, yes_bid, *, event="KXNCAAFSPREAD-26NOV06AAABBB", size=1000.0, title="t"):
    return {
        "market_ticker": ticker,
        "event_ticker": event,
        "family": family,
        "title": title,
        "status": "active",
        "floor_strike": line,
        "strike_type": "greater",
        "is_alternate_line": True,
        "book_state": "two_sided",
        "yes_ask": yes_ask,
        "yes_bid": yes_bid,
        "no_ask": round(1 - yes_bid, 2),
        "no_bid": round(1 - yes_ask, 2),
        "yes_ask_size": size,
        "yes_bid_size": 500.0,
        "buy_yes_executable": True,
        "buy_no_executable": True,
        "buy_yes_fee": 0.01,
        "buy_no_fee": 0.01,
        "updated_time": "2026-11-01T00:00:00Z",
    }


def build(quotes, games=()):
    return mk.build_markets(
        game_key="26NOV06AAABBB",
        quote_files=[{"quotes": quotes, "game_fingerprint": "fp1"}],
        event_files={},
        capture={"captured_at": "2026-11-06T00:00:00Z"},
        reference_games=list(games),
    )


def test_side_codes_split_the_game_key_by_the_codes_on_the_tickers():
    codes = mk.side_codes(
        "26NOV06UNMNEV",
        ["KXNCAAFSPREAD-26NOV06UNMNEV-NEV14", "KXNCAAFSPREAD-26NOV06UNMNEV-UNM3", "KXNCAAFTOTAL-26NOV06UNMNEV-33"],
    )
    assert codes == {"away": "UNM", "home": "NEV"}


def test_quantity_orientation_and_period_for_each_family():
    codes = {"away": "AAA", "home": "BBB"}
    assert mk.quantity_for("game_spread", "game", "AAA", "7", 6.5, codes) == {
        "type": "margin_over",
        "period": "game",
        "side": "away",
        "team_code": "AAA",
        "threshold": 6.5,
    }
    assert mk.quantity_for("first_half_total", "h1", "", "24", 23.5, codes)["type"] == "total_over"
    assert mk.quantity_for("quarter_moneyline", "q2", "TIE", "", None, codes) == {
        "type": "win",
        "period": "q2",
        "outcome": "tie",
    }
    assert mk.quantity_for("team_total", "game", "BBB", "20", 19.5, codes)["side"] == "home"
    assert mk.quantity_for("winning_margin", "game", "", "", None, codes)["type"] == "see_title"
    assert mk.period_of("KXNCAAF2QSPREAD", "quarter_spread", None) == "q2"
    assert mk.period_of("KXNCAAF1HTOTAL", "first_half_total", None) == "h1"
    assert mk.period_of("KXNCAAF2HTOTAL", "second_half_total", None) == "h2"
    assert mk.period_of("KXNCAAFTOTAL", "game_total", "full_game") == "full_game"


def test_every_contract_gets_both_buy_sides_with_all_in_costs_and_the_no_ask_is_used():
    m = build([quote("KXNCAAFSPREAD-26NOV06AAABBB-BBB7", "game_spread", 6.5, 0.50, 0.40)])
    c = m["contracts"][0]
    assert c["yes"]["ask"] == 0.50 and c["no"]["ask"] == 0.60  # NO ask = 1 - yes_bid, not 1 - yes_ask (0.50)
    assert c["yes"]["all_in"] > 0.50 and c["no"]["all_in"] > 0.60
    assert c["no"]["ask_size"] == 500.0  # the NO ask size is the YES bid book
    assert m["price_provenance"]["kind"] == "CAPTURED_NOT_LIVE_VERIFIED"
    assert m["contains_model_probabilities"] is False


def test_no_model_probability_fair_value_edge_or_stake_in_the_menu():
    m = build([quote("KXNCAAFSPREAD-26NOV06AAABBB-BBB7", "game_spread", 6.5, 0.50, 0.40)])
    text = json.dumps(m).lower()
    for banned in ("fair_value", "fair_probability", "kelly", '"stake"', "bet_up_to", '"edge"'):
        assert banned not in text


def test_ladders_are_sorted_by_line_and_state_the_monotone_rule():
    qs = [quote(f"KXNCAAFSPREAD-26NOV06AAABBB-BBB{n}", "game_spread", n - 0.5, 0.5, 0.4) for n in (14, 3, 7)]
    m = build(qs)
    lad = m["ladders"]["KXNCAAFSPREAD-26NOV06AAABBB|BBB"]
    assert lad["lines"] == [2.5, 6.5, 13.5] and "non-increasing" in lad["monotone_rule"]


def test_reference_class_frequency_is_attached_and_labelled_descriptive():
    games = [
        {
            "game_id": str(i),
            "season": 2025,
            "home": "H",
            "away": "A",
            "pred_home": 28,
            "pred_away": 22,
            "pred_total": 50,
            "actual_home": 30 if i % 2 else 20,
            "actual_away": 20,
            "home_periods": None,
            "away_periods": None,
        }
        for i in range(10)
    ]
    m = build(
        [
            quote("KXNCAAFSPREAD-26NOV06AAABBB-BBB7", "game_spread", 6.5, 0.50, 0.40),
            quote("KXNCAAFSPREAD-26NOV06AAABBB-AAA7", "game_spread", 6.5, 0.20, 0.15),
        ],
        games,
    )
    by = {c["ticker"]: c for c in m["contracts"]}
    assert by["KXNCAAFSPREAD-26NOV06AAABBB-BBB7"]["ref_freq"] == {"n": 10, "yes": 0.5}  # home by 10 half the time
    assert by["KXNCAAFSPREAD-26NOV06AAABBB-AAA7"]["ref_freq"] == {"n": 10, "yes": 0.0}  # away never wins by 7+
    assert "NOT a fair probability" in m["pricing_basis"]["contract_fields"]["ref_freq"]


def test_non_active_status_is_kept_and_unlabelled_book_is_not_hidden():
    q = quote("KXNCAAFSPREAD-26NOV06AAABBB-BBB7", "game_spread", 6.5, 0.0, 0.0)
    q["status"], q["book_state"], q["buy_yes_executable"] = "initialized", "empty_book", False
    c = build([q])["contracts"][0]
    assert c["status"] == "initialized" and c["book"] == "empty_book" and c["yes"]["exec"] is False


def test_every_family_in_the_live_catalog_has_a_guide_or_a_documented_gap():
    families = set()
    for p in glob.glob(str(ROOT / "data/live/execution/games/*/quotes.json")):
        families |= {r["family"] for r in json.load(open(p))["quotes"]}
    assert families, "committed execution quotes are required for this regression"
    for f in families:
        guide = mk.FAMILY_GUIDE.get(f)
        assert guide is not None, f"family {f} has no handicapping guide"
        assert guide["coverage"] in (mk.FULL, mk.PARTIAL, mk.GAP) and guide["inputs"]
        if guide["coverage"] != mk.FULL:
            assert guide.get("gaps"), f"{f} is {guide['coverage']} but names no gap"


def test_committed_menu_reconciles_with_the_execution_index():
    index = json.load(open(ROOT / "data/research_packets/index.json"))
    exec_rows = {g["game_key"]: g for g in json.load(open(ROOT / "data/live/cfb_execution_index.json"))["games"]}
    checked = 0
    for g in index["games"][:25]:
        parts = []
        for slate in index["slates"]:
            if slate["slate_date"] == g["slate_date"]:
                row = next(r for r in json.load(open(ROOT / slate["path"]))["games"] if r["game_key"] == g["game_key"])
                parts = row["files"]["markets"]
        total = sum(len(json.load(open(ROOT / p))["contracts"]) for p in parts)
        assert total == exec_rows[g["game_key"]]["market_count"]
        checked += 1
    assert checked
