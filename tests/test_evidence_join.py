"""Evidence join: recommendation link, fill ids, entry context and closing-price PROXY (PR #249 item 4).

The invariants: nothing is inferred, the wager rows are never modified, a missing source is a named state,
and a stale closing snapshot is never presented as the close.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cfb_edge_finder.postmortem.evidence_join import (
    NEAR_CLOSE_MAX_LAG,
    Snapshot,
    build_evidence,
    candidate_game_keys,
)

KICK = datetime(2026, 10, 24, 20, 0, tzinfo=UTC)
GAME = "26OCT24AAABBB"
TICK = f"KXNCAAFSPREAD-{GAME}-AAA7"


def q(ticker=TICK, yes_bid=0.40, yes_ask=0.44, book="two_sided"):
    return {"market_ticker": ticker, "yes_bid": yes_bid, "yes_ask": yes_ask, "book_state": book}


class FakeSource:
    """snapshots: {captured_at: {game_key: quotes_doc}}"""

    def __init__(self, data):
        self.data = {Snapshot(f"c{i}", t): games for i, (t, games) in enumerate(sorted(data.items()))}

    def snapshots(self):
        return list(self.data)

    def game_keys(self, snap):
        return set(self.data[snap])

    def quotes(self, snap, game_key):
        return self.data[snap].get(game_key)


def wager(side="YES", price=0.42, at=KICK - timedelta(hours=3), ticker=TICK, **extra):
    return {"source_bet_key": f"k-{side}-{ticker}", "kalshi_ticker": ticker, "selection": side,
            "average_price": price, "placed_at": at.isoformat(), "stake": 50.0,
            "extensions": {"game_date": "2026-10-24"},
            **extra}


def doc(*rows):
    return {"kickoff": KICK.isoformat(), "quotes": list(rows)}


def test_near_close_proxy_and_clv_for_a_yes_position():
    src = FakeSource({KICK - timedelta(minutes=30): {GAME: doc(q(yes_bid=0.46, yes_ask=0.50))}})
    ev = build_evidence([wager(price=0.42)], source=src)
    c = ev["rows"][0]["close_proxy"]
    assert c["state"] == "NEAR_CLOSE_PROXY" and c["seconds_before_kickoff"] == 1800
    assert c["side_mid"] == pytest.approx(0.48)
    assert c["clv_vs_mid"] == pytest.approx(0.06)   # market moved toward the position
    assert c["clv_vs_bid"] == pytest.approx(0.04)   # what it could be sold at


def test_no_side_values_come_from_the_complement_of_the_yes_book():
    src = FakeSource({KICK - timedelta(minutes=10): {GAME: doc(q(yes_bid=0.40, yes_ask=0.44))}})
    c = build_evidence([wager(side="NO", price=0.55)], source=src)["rows"][0]["close_proxy"]
    # NO bid = 1 - yes_ask = 0.56 ; NO ask = 1 - yes_bid = 0.60 ; mid 0.58
    assert c["side_bid"] == pytest.approx(0.56)
    assert c["side_ask"] == pytest.approx(0.60)
    assert c["side_mid"] == pytest.approx(0.58)
    assert c["clv_vs_mid"] == pytest.approx(0.03)


def test_a_stale_snapshot_is_labelled_stale_never_the_close():
    src = FakeSource({KICK - NEAR_CLOSE_MAX_LAG - timedelta(minutes=1): {GAME: doc(q())}})
    c = build_evidence([wager()], source=src)["rows"][0]["close_proxy"]
    assert c["state"] == "STALE_CLOSE_PROXY"
    assert c["seconds_before_kickoff"] > NEAR_CLOSE_MAX_LAG.total_seconds()


def test_snapshots_after_kickoff_are_never_used_as_the_close():
    src = FakeSource({KICK + timedelta(minutes=5): {GAME: doc(q(yes_bid=0.99, yes_ask=1.0))},
                      KICK - timedelta(minutes=20): {GAME: doc(q(yes_bid=0.40, yes_ask=0.44))}})
    c = build_evidence([wager()], source=src)["rows"][0]["close_proxy"]
    assert c["side_mid"] == pytest.approx(0.42)


def test_one_sided_book_gives_no_mid_and_says_why():
    src = FakeSource({KICK - timedelta(minutes=5): {GAME: doc(q(yes_bid=0.40, yes_ask=0.44, book="bid_only"))}})
    c = build_evidence([wager()], source=src)["rows"][0]["close_proxy"]
    assert c["side_mid"] is None and c["clv_vs_mid"] is None
    assert c["clv_vs_mid_missing_reason"] == "BOOK_NOT_TWO_SIDED"
    assert c["clv_vs_bid"] == pytest.approx(0.40 - 0.42)


def test_combo_and_unlisted_market_are_named_missing_not_inferred():
    src = FakeSource({KICK - timedelta(minutes=5): {GAME: doc(q())}})
    combo = wager(ticker="KXMVECROSSCATEGORY-S2026ABC-XYZ")
    other = wager(ticker=f"KXNCAAFTOTAL-{GAME}-55")
    ev = build_evidence([combo, other], source=src)
    assert ev["rows"][0]["close_proxy"]["state"] == "MISSING_MARKET_NOT_IN_CATALOG"
    assert ev["rows"][1]["close_proxy"]["state"] == "MISSING_MARKET_NOT_IN_CATALOG"
    assert ev["clv_vs_mid"]["n"] == 0 and ev["clv_vs_mid"]["mean"] is None
    unknown_game = wager(ticker="KXNCAAFSPREAD-26OCT24ZZZYYY-ZZZ3")
    state = build_evidence([unknown_game], source=src)["rows"][0]["close_proxy"]["state"]
    assert state == "MISSING_GAME_NOT_IN_ANY_SNAPSHOT"


def test_game_key_resolves_across_a_utc_date_rollover():
    assert candidate_game_keys("26NOV04BALLMASS") == ["26NOV04BALLMASS", "26NOV03BALLMASS", "26NOV05BALLMASS"]
    assert candidate_game_keys("26DEC31AAABBB")[2] == "27JAN01AAABBB"
    # spread event dated the 25th, but the game (and its shard) is keyed the 24th
    src = FakeSource({KICK - timedelta(minutes=10): {GAME: doc(q(ticker="KXNCAAFSPREAD-26OCT25AAABBB-AAA7"))}})
    w = wager(ticker="KXNCAAFSPREAD-26OCT25AAABBB-AAA7")
    assert build_evidence([w], source=src)["rows"][0]["close_proxy"]["state"] == "NEAR_CLOSE_PROXY"


def test_entry_context_reports_how_stale_the_last_known_book_was():
    t = KICK - timedelta(hours=3)
    src = FakeSource({t - timedelta(minutes=40): {GAME: doc(q(yes_ask=0.41))}})
    e = build_evidence([wager(price=0.43, at=t)], source=src)["rows"][0]["entry_context"]
    assert e["snapshot_age_seconds_at_order"] == 2400
    assert e["fill_minus_snapshot_ask"] == pytest.approx(0.02)
    none = build_evidence([wager(at=KICK - timedelta(days=9))], source=src)["rows"][0]["entry_context"]
    assert none["state"] == "MISSING_NO_SNAPSHOT_BEFORE_ORDER"


def test_fill_ids_present_only_when_the_ledger_carried_them():
    src = FakeSource({KICK - timedelta(minutes=5): {GAME: doc(q())}})
    ev = build_evidence([wager(), wager(side="NO", kalshi_order_id="o1", kalshi_fill_ids=["f1"])], source=src)
    assert ev["rows"][0]["fill_ids"]["state"] == "MISSING_NOT_IN_ROUTED_LEDGER"
    assert ev["rows"][1]["fill_ids"] == {"state": "PRESENT", "order_id": "o1", "fill_ids": ["f1"]}
    assert ev["coverage"]["fill_ids"] == {"MISSING_NOT_IN_ROUTED_LEDGER": 1, "PRESENT": 1}


def test_no_decision_records_is_unknown_not_executed_not_recommended():
    ev = build_evidence([wager()], source=None)
    r = ev["rows"][0]
    assert r["recommendation"] == {"state": "UNKNOWN_NO_DECISION_RECORDS", "recommendation_id": None}
    assert r["close_proxy"]["state"] == "MISSING_NO_SNAPSHOT_SOURCE"


def _record(ticker=TICK, side="YES", created=KICK - timedelta(hours=5), rid="rec-1", price=0.42):
    return {"record_id": rid, "created_at": created.isoformat(), "slate_date": "2026-10-24", "games": {},
            "candidates": [{"market_ticker": ticker, "side": side, "game_key": GAME, "observed_price": price,
                            "bet_up_to": 0.5, "recommendation_id": rid, "kickoff": KICK.isoformat()}]}


def test_decision_record_links_only_on_exact_market_side_and_precedes_the_order():
    w = wager(price=0.42, at=KICK - timedelta(hours=3))
    ok = build_evidence([w], source=None, decision_records=[_record()])["rows"][0]["recommendation"]
    assert ok["state"] == "matched"
    wrong_side = build_evidence([w], source=None, decision_records=[_record(side="NO")])["rows"][0]["recommendation"]
    assert wrong_side["state"] == "unmatched"
    after_order = build_evidence([w], source=None, decision_records=[_record(created=KICK - timedelta(hours=1))])
    assert after_order["rows"][0]["recommendation"]["state"] == "unmatched"


def test_the_wager_rows_are_never_modified():
    w = [wager(), wager(side="NO", ticker="KXMVECROSSCATEGORY-S1-X")]
    before = copy.deepcopy(w)
    build_evidence(w, source=FakeSource({KICK - timedelta(minutes=5): {GAME: doc(q())}}), decision_records=[_record()])
    assert w == before


def test_summary_script_writes_aggregates_only(tmp_path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    summary = json.loads((root / "docs/reports/evidence_join_2026-10-22_2026-11-01.json").read_text())
    assert "rows" not in summary
    blob = json.dumps(summary)
    assert "KXNCAAF" in blob  # family prefixes only...
    assert "kalshi:v1:" not in blob and "-26OCT" not in blob and "-26NOV" not in blob  # ...never a ticker or bet key
    assert summary["coverage"]["fill_ids"] and summary["coverage"]["recommendation"]
    help_out = subprocess.run([sys.executable, str(root / "scripts/cfb_evidence_join.py"), "--help"],
                              capture_output=True, text=True, check=False)
    assert help_out.returncode == 0 and "AGGREGATE ONLY" in help_out.stdout


def test_clv_is_price_only_and_never_fee_adjusted_or_double_counted():
    """CLV = close value of the side - the FILL PRICE (average_price). The wager's fees and its fee-inclusive
    stake must not move it: the entry fee is accounted once, in stake, not again in a price comparison."""
    src = FakeSource({KICK - timedelta(minutes=30): {GAME: doc(q(yes_bid=0.46, yes_ask=0.50))}})
    plain = build_evidence([wager(price=0.42)], source=src)["rows"][0]["close_proxy"]
    with_fees = build_evidence(
        [wager(price=0.42, fees=2.5, stake=0.42 * 100 + 2.5, contracts=100)], source=src
    )["rows"][0]["close_proxy"]
    assert plain["clv_vs_mid"] == pytest.approx(0.06)
    assert with_fees["clv_vs_mid"] == plain["clv_vs_mid"]
    assert with_fees["clv_vs_bid"] == plain["clv_vs_bid"]
    # The all-in cost per contract (stake / contracts = 0.445) is NOT the comparison price.
    assert with_fees["clv_vs_mid"] != pytest.approx(0.48 - 0.445)


def test_entry_at_or_after_kickoff_is_not_scored_against_the_pregame_close():
    """A live order bought an in-game price. Comparing it with the pre-kickoff close is not CLV (it measured
    -13.7c on the NEAR-close live subset of the 2026 ledger vs about -0.5c for pre-game entries)."""
    src = FakeSource({KICK - timedelta(minutes=30): {GAME: doc(q(yes_bid=0.46, yes_ask=0.50))}})
    live = build_evidence([wager(price=0.62, at=KICK + timedelta(minutes=40))], source=src)["rows"][0]["close_proxy"]
    assert live["entry_phase"] == "LIVE" and live["clv_vs_mid"] is None and live["clv_vs_bid"] is None
    assert live["clv_withheld_reason"] == "ENTRY_AT_OR_AFTER_KICKOFF"
    # kept, labelled, never aggregated
    assert live["live_entry_diagnostic"]["clv_vs_mid"] == pytest.approx(0.48 - 0.62)
    exactly = build_evidence([wager(price=0.62, at=KICK)], source=src)["rows"][0]["close_proxy"]
    assert exactly["entry_phase"] == "LIVE" and exactly["clv_vs_mid"] is None  # kickoff instant is not pre-game
    pre = build_evidence([wager(price=0.42)], source=src)["rows"][0]["close_proxy"]
    assert pre["entry_phase"] == "PRE_GAME" and pre["clv_vs_mid"] == pytest.approx(0.06)


def test_unknown_order_time_cannot_be_proven_pregame_and_live_rows_leave_the_aggregate():
    src = FakeSource({KICK - timedelta(minutes=30): {GAME: doc(q(yes_bid=0.46, yes_ask=0.50))}})
    no_time = {**wager(price=0.42), "placed_at": None}
    c = build_evidence([no_time], source=src)["rows"][0]["close_proxy"]
    assert c["entry_phase"] == "UNKNOWN" and c["clv_vs_mid"] is None
    assert c["clv_withheld_reason"] == "ENTRY_TIME_UNKNOWN"
    rows = [wager(price=0.42), {**wager(price=0.62, at=KICK + timedelta(minutes=40)), "source_bet_key": "k-live"}]
    ev = build_evidence(rows, source=src)
    assert ev["clv_vs_mid"]["n"] == 1 and ev["clv_vs_mid"]["mean"] == pytest.approx(0.06)  # only the pre-game entry
