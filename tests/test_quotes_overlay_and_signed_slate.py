"""Fresher quotes.json prices win over the packet's markets.json; the committed signed Nov 10 handicaps are valid."""

from __future__ import annotations

import json
from pathlib import Path

from cfb_edge_finder.research_packet import handicap_pricing as hp
from cfb_edge_finder.research_packet import market_audit as ma
from cfb_edge_finder.research_packet import pricing

REPO = Path(__file__).resolve().parents[1]
KEY = "26NOV10AAABBB"


def _root(tmp_path, quote_fp):
    games = tmp_path / "data" / "research_packets" / "games" / KEY
    games.mkdir(parents=True)
    (games / "markets.json").write_text(json.dumps({"price_provenance": {"game_fingerprint": "packetfp"}}))
    live = tmp_path / "data" / "live" / "execution" / "games" / KEY
    live.mkdir(parents=True)
    quote = {
        "market_ticker": f"KXNCAAFGAME-{KEY}-BBB",
        "yes_ask": 0.40,
        "yes_bid": 0.38,
        "yes_ask_size": 500.0,
        "buy_yes_executable": True,
        "no_ask": 0.63,
        "no_bid": 0.60,
        "no_ask_size": 0.0,
        "buy_no_executable": False,
        "book_state": "two_sided",
        "updated_time": "2026-11-09T19:00:00Z",
    }
    (live / "quotes.json").write_text(json.dumps({"game_fingerprint": quote_fp, "quotes": [quote]}))
    return tmp_path


def _contract():
    stale = {"ask": 0.55, "ask_size": 10.0, "exec": True, "all_in": 0.57}
    return {
        "ticker": f"KXNCAAFGAME-{KEY}-BBB",
        "yes": dict(stale),
        "no": dict(stale),
        "book": "two_sided",
        "upd": "old",
    }


def test_newer_quotes_replace_asks_sizes_and_recompute_fee_inclusive_price(tmp_path):
    root = _root(tmp_path, "quotefp")
    contracts, basis = [_contract()], {}
    ma._overlay_fresher_quotes(root, KEY, contracts, basis)
    yes, no = contracts[0]["yes"], contracts[0]["no"]
    assert yes["ask"] == 0.40 and yes["ask_size"] == 500.0 and yes["exec"] is True
    n = int(75.0 // 0.40)
    assert yes["contracts_1u"] == n
    assert yes["all_in"] == round(0.40 + pricing.order_fee(0.40, n, "KXNCAAFGAME") / n, 6)
    assert no["exec"] is False  # no resting size at the NO ask: unpriceable, not carried over from the stale packet
    assert ma._unpriceable_reason(no) == "NO_LIQUIDITY_AT_ASK"
    assert contracts[0]["upd"] == "2026-11-09T19:00:00Z"
    assert basis[KEY]["source"].startswith("quotes.json") and basis[KEY]["overlaid"] == 1


def test_matching_fingerprint_keeps_packet_prices(tmp_path):
    root = _root(tmp_path, "packetfp")
    contracts, basis = [_contract()], {}
    ma._overlay_fresher_quotes(root, KEY, contracts, basis)
    assert contracts[0]["yes"]["ask"] == 0.55 and basis[KEY]["overlaid"] == 0


def test_committed_signed_nov10_handicaps_are_valid_signed_and_region_stated():
    doc = json.loads((REPO / "docs/reports/CFB_ANALYST_HANDICAPS_2026-11-10.json").read_text())
    dists, meta, rejections = hp.parse_handicaps(doc)
    assert rejections == [] and len(dists) == 3
    for gk, m in meta.items():
        assert m["analyst"] and m["handicapped_at"] and m["evidence"] and m["thesis"] and m["opposing_case"]
        assert len(m["periods"]) == 7 and all(m["region_stated"].values())
        assert not any(h.get("draft") for h in doc["handicaps"] if h["game_key"] == gk)
