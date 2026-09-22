"""A settlement is a LATER and SEPARATE observation than the wager it settles.

The wager ledger is append-only because a wager record is a claim about money
that already moved. That reasoning does not stop applying just because the new
information is welcome, so settlement is a separate row rather than an edit.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from cfb_edge_finder.accounting import report, settlement, store
from cfb_edge_finder.accounting.import_settlements import (
    SettlementRefused,
    build_record,
    import_rows,
    mint_settlement_id,
)

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
)

KEY = "kalshi:v1:3a1f7c5d2e8b49a0c6f1d84b7e2905cfa3b6d17e"
TICKER = "KXNCAAFGAME-26SEP12ALAUGA-ALA"
SEASON = 2026


def wager_row(**overrides):
    row = {
        "wager_id": "routed-abc", "schema_version": "cfb_accounted_wager.v1",
        "source_bet_key": KEY, "import_batch_id": "batch",
        "entry_method": "IMPORTED_RECEIPT", "game_date": "2026-09-12",
        "market_ticker": TICKER, "side": "YES",
        "executed_at": "2026-09-12T20:40:11Z", "contracts": 25.0,
        "execution_price": 0.47, "stake": 11.94, "fees_paid": 0.19,
    }
    row.update(overrides)
    return row


#: Exactly the shape kalshi_router.settlement.WagerSettlement produces.
ROUTER_ROW = {
    "source_bet_key": KEY,
    "market_ticker": TICKER,
    "side": "YES",
    "settlement_status": "SETTLED",
    "settled_at": "2026-09-13T02:00:00Z",
    "result": "WON",
    "gross_return": 25.0,
    "net_profit_loss": 13.06,
    "refusals": [],
}


def seeded(tmp_path, **overrides):
    store.append_wagers(tmp_path, SEASON, [wager_row(**overrides)])
    return tmp_path


# ------------------------------------------------------------- the record

def test_the_exact_exchange_numbers_survive():
    record = build_record(ROUTER_ROW)

    assert record.result == "WON"
    assert record.gross_return == 25.0
    assert record.net_profit_loss == 13.06
    assert record.settled_at == "2026-09-13T02:00:00Z"
    assert record.schema_version == "cfb_wager_settlement.v1"


def test_the_id_comes_from_the_wagers_key_not_from_the_payout():
    """A correction to a result or a payout must land on the same row rather
    than beside it."""
    base = mint_settlement_id(KEY)

    assert mint_settlement_id(KEY) == base
    assert base != mint_settlement_id("kalshi:v1:other")
    assert base.startswith("stl-")


def test_no_model_provenance_is_fabricated():
    """A settlement proves what the exchange paid. It proves nothing about what
    recommended the bet."""
    record = build_record(ROUTER_ROW).to_dict()

    for field in settlement.FORBIDDEN_PROVENANCE_FIELDS:
        assert field not in record


def test_a_pending_row_is_refused_outright():
    """An unsettled wager is recorded by having NO settlement row.

    A PENDING row would have to be superseded when the market settles, in a
    store whose entire guarantee is that rows are not superseded.
    """
    with pytest.raises(SettlementRefused, match="having no settlement row"):
        build_record(dict(ROUTER_ROW, settlement_status="PENDING"))


def test_a_missing_figure_must_carry_its_reason():
    """A null with no explanation is the shape a reader fills in with a zero."""
    with pytest.raises(SettlementRefused, match="no refusal recorded"):
        build_record(dict(ROUTER_ROW, net_profit_loss=None))


def test_a_settled_row_with_a_refused_figure_is_written():
    """"The market settled and the return could not be established" is a
    durable fact with a durable cause, and worth recording."""
    record = build_record(dict(ROUTER_ROW, net_profit_loss=None,
                               refusals=["shared_position_fee"]))

    assert record.gross_return == 25.0
    assert record.net_profit_loss is None
    assert record.refusals == ["shared_position_fee"]


def test_a_refusal_alongside_a_complete_figure_is_itself_refused():
    """A reason for an absence, next to no absence, means one of the two is
    wrong and neither may be assumed."""
    with pytest.raises(SettlementRefused, match="nothing to refuse"):
        build_record(dict(ROUTER_ROW, refusals=["no_settlement_price"]))


def test_a_field_this_ledger_does_not_model_is_refused():
    with pytest.raises(SettlementRefused, match="unknown field"):
        build_record(dict(ROUTER_ROW, model_supported=True))


# -------------------------------------------------------------- the import

def test_a_settlement_for_a_known_wager_is_written(tmp_path):
    result = import_rows(seeded(tmp_path), [ROUTER_ROW], season=SEASON)

    assert (result["written"], result["refused"]) == (1, 0)
    rows = store.read_rows(store.settlement_ledger_path(tmp_path, SEASON))
    assert len(rows) == 1 and rows[0]["result"] == "WON"


def test_a_settlement_for_a_wager_this_ledger_never_saw_is_refused(tmp_path):
    """THE test. A payout attributed to a bet this repository has no record of
    would count in every total while belonging to nothing."""
    result = import_rows(
        seeded(tmp_path), [dict(ROUTER_ROW, source_bet_key="kalshi:v1:ghost")],
        season=SEASON,
    )

    assert (result["written"], result["refused"]) == (0, 1)
    assert "no wager with this source_bet_key" in result["refusals"][0][1]
    assert not store.settlement_ledger_path(tmp_path, SEASON).exists()


def test_running_the_settlement_pass_twice_writes_one_row(tmp_path):
    """A market settles once, so a second observation is a duplicate rather
    than a correction."""
    root = seeded(tmp_path)
    first = import_rows(root, [ROUTER_ROW], season=SEASON)
    second = import_rows(root, [ROUTER_ROW], season=SEASON)

    assert (first["written"], first["already_present"]) == (1, 0)
    assert (second["written"], second["already_present"]) == (0, 1)
    assert len(store.read_rows(store.settlement_ledger_path(tmp_path, SEASON))) == 1


def test_one_refused_settlement_does_not_stop_the_others(tmp_path):
    root = tmp_path
    store.append_wagers(root, SEASON, [
        wager_row(), wager_row(wager_id="routed-b", source_bet_key="kalshi:v1:b"),
    ])
    result = import_rows(root, [
        dict(ROUTER_ROW, source_bet_key="kalshi:v1:ghost"),
        dict(ROUTER_ROW, source_bet_key="kalshi:v1:b"),
    ], season=SEASON)

    assert (result["written"], result["refused"]) == (1, 1)


def test_the_wager_ledger_itself_is_never_touched(tmp_path):
    """Settlement is a separate row. The wager's own bytes do not move."""
    root = seeded(tmp_path)
    before = store.ledger_path(root, SEASON).read_bytes()

    import_rows(root, [ROUTER_ROW], season=SEASON)

    assert store.ledger_path(root, SEASON).read_bytes() == before


# -------------------------------------------------------------- the report

def test_the_report_joins_settlements_to_wagers(tmp_path):
    root = seeded(tmp_path)
    import_rows(root, [ROUTER_ROW], season=SEASON)

    summary = report.summarize(
        store.read_rows(store.ledger_path(root, SEASON)), SEASON,
        settlements=store.read_rows(store.settlement_ledger_path(root, SEASON)),
    )

    assert (summary.settled, summary.unsettled) == (1, 0)
    assert summary.won == 1
    assert summary.profit_loss_is_complete is True
    assert round(summary.realized_profit_loss, 2) == 13.06


def test_a_wager_with_no_settlement_row_is_unsettled(tmp_path):
    """The absence IS the record, which is why no PENDING row is ever written."""
    root = seeded(tmp_path)

    summary = report.summarize(
        store.read_rows(store.ledger_path(root, SEASON)), SEASON, settlements=[],
    )

    assert (summary.settled, summary.unsettled) == (0, 1)
    assert summary.profit_loss_is_complete is False


def test_a_settled_wager_whose_pl_was_refused_is_settled_but_unestablished(tmp_path):
    """Both facts at once, and the report must not collapse them."""
    root = seeded(tmp_path)
    import_rows(root, [dict(ROUTER_ROW, net_profit_loss=None,
                            refusals=["shared_position_fee"])], season=SEASON)

    summary = report.summarize(
        store.read_rows(store.ledger_path(root, SEASON)), SEASON,
        settlements=store.read_rows(store.settlement_ledger_path(root, SEASON)),
    )

    assert summary.settled == 1
    assert summary.won == 1
    assert summary.profit_loss_unestablished == 1
    assert "UNESTABLISHED for 1 of 1 wagers" in report.render(summary)


# -------------------------------------------------------------- the script

def test_the_script_prints_no_payout(tmp_path, capsys):
    import import_routed_settlements as script

    root = seeded(tmp_path)
    payload = tmp_path / "CFB-settlements.json"
    payload.write_text(json.dumps({"settlements": [ROUTER_ROW]}), encoding="utf-8")

    assert script.main([
        "--payload", str(payload), "--base-dir", str(root), "--season", str(SEASON),
    ]) == script.EXIT_OK

    printed = capsys.readouterr().out
    assert "written:         1" in printed
    for sensitive in (TICKER, "25.0", "13.06", KEY):
        assert sensitive not in printed, printed


def test_the_script_fails_when_a_settlement_is_refused(tmp_path):
    import import_routed_settlements as script

    root = seeded(tmp_path)
    payload = tmp_path / "CFB-settlements.json"
    payload.write_text(
        json.dumps({"settlements": [dict(ROUTER_ROW, source_bet_key="kalshi:v1:ghost")]}),
        encoding="utf-8",
    )

    assert script.main([
        "--payload", str(payload), "--base-dir", str(root), "--season", str(SEASON),
    ]) == script.EXIT_REFUSED


# ─────────────────────────────────────────────────────────────────────
# THE SETTLEMENT IDENTITY CONTRACT
#
# On 2026-09-22 a live settle run imported 41 real CFB settlements cleanly --
# 41 NEW, 0 failed, idempotent on a second pass, the ledger validating at
# 59/59/0 -- and the router's auto-merge gate then refused the pull request
# because the rows "do not carry the router's import batch id".
#
# They do not, and they must not. A wager row is identified by the delivery
# that carried it; a SETTLEMENT row is identified by THE WAGER IT SETTLES. The
# fix was in the gate. These tests pin this side of the contract so a later
# "just add the batch id" cannot quietly move the problem here.
# ─────────────────────────────────────────────────────────────────────

def test_the_settlement_schema_models_no_import_batch_id():
    """The wager schema requires one. This one has no such field at all."""
    from dataclasses import fields as dataclass_fields

    from cfb_edge_finder.accounting import wager

    settlement_fields = {f.name for f in dataclass_fields(settlement.WagerSettlement)}
    assert "import_batch_id" not in settlement_fields
    assert "import_batch_id" in {f.name for f in dataclass_fields(wager.AccountedWager)}


def test_a_settlement_row_carrying_an_import_batch_id_is_refused():
    """Load-bearing. This refusal is what makes the absence a CONTRACT rather
    than a gap somebody can fill in upstream without anyone noticing."""
    with pytest.raises(SettlementRefused) as excinfo:
        build_record({**ROUTER_ROW, "import_batch_id": "kalshi-router-v1"})
    assert "import_batch_id" in str(excinfo.value)


def test_the_canonical_identity_of_a_settlement_row_is_exactly_these_fields():
    record = build_record(ROUTER_ROW).to_dict()
    assert set(record) == {
        "settlement_id", "schema_version", "source_bet_key", "market_ticker",
        "side", "settlement_status", "settled_at", "result",
        "gross_return", "net_profit_loss", "refusals", "venue",
    }
    # Required, and each one is refused when absent -- see validate().
    for name in ("settlement_id", "source_bet_key", "market_ticker", "side",
                 "settlement_status", "settled_at"):
        assert record[name]


def test_settlement_identity_is_deterministic_across_reruns(tmp_path):
    """Same input, same id, same bytes -- twice, in separate ledgers. This is
    what makes a re-import a no-op instead of a second row."""
    first = build_record(ROUTER_ROW).to_dict()
    second = build_record(dict(ROUTER_ROW)).to_dict()
    assert first == second
    assert first["settlement_id"] == mint_settlement_id(KEY)

    one, two = tmp_path / "one", tmp_path / "two"
    for base in (one, two):
        store.append_wagers(base, SEASON, [wager_row()])
        import_rows(base, [dict(ROUTER_ROW)], season=SEASON)
    assert (one / "settlements" / f"{SEASON}.jsonl").read_text() == (
        two / "settlements" / f"{SEASON}.jsonl").read_text()


def test_the_real_pr_53_row_shape_imports(tmp_path):
    """Copied verbatim from the diff of cfb-edge-finder#53, including the row
    whose net figure is absent with a refusal recorded -- the shape that was
    already on disk and merging when the gate turned it away."""
    rows = [
        {
            "source_bet_key": "kalshi:v1:03a8210bb09c533faadd20f5229a471408888089adf7f48c4bc412026468eb45",
            "market_ticker": "KXNCAAF1QTOTAL-26SEP19UGAARK-11", "side": "YES",
            "settlement_status": "SETTLED", "settled_at": "2026-09-19T16:45:34.509166Z",
            "result": "LOST", "gross_return": 0.0, "net_profit_loss": -51.5253,
            "refusals": [], "venue": "kalshi",
        },
        {
            "source_bet_key": "kalshi:v1:41b8a143560f919459f5bde7bf2e608e1f0a70b6b64909c68042cdf33ee815ef",
            "market_ticker": "KXNCAAFSPREAD-26SEP19PREWCU-WCU34", "side": "NO",
            "settlement_status": "SETTLED", "settled_at": "2026-09-20T00:34:34.521071Z",
            "result": "LOST", "gross_return": 0.0, "net_profit_loss": None,
            "refusals": ["shared_position_fee"], "venue": "kalshi",
        },
    ]
    for row in rows:
        store.append_wagers(tmp_path, SEASON, [wager_row(
            wager_id=f"w-{row['source_bet_key'][-6:]}",
            source_bet_key=row["source_bet_key"],
            market_ticker=row["market_ticker"], side=row["side"])])

    result = import_rows(tmp_path, rows, season=SEASON)
    assert (result["written"], result["refused"]) == (2, 0)

    written = [json.loads(line) for line in
               (tmp_path / "settlements" / f"{SEASON}.jsonl").read_text().splitlines()]
    assert [r["settlement_id"] for r in written] == [
        "stl-23dbe2a8dea558d7bc7ab8e1", "stl-9a9ca35b56a3a72ab142ee58"]
    assert all("import_batch_id" not in r for r in written)

    # and a second identical pass writes nothing
    again = import_rows(tmp_path, [dict(r) for r in rows], season=SEASON)
    assert (again["written"], again["already_present"]) == (0, 2)


# ─────────────────────────────────────────────────────────────────────
# A RESTATED PAYOUT IS A PERSON'S DECISION
# ─────────────────────────────────────────────────────────────────────

def test_a_second_settlement_contradicting_the_recorded_one_is_refused(tmp_path):
    """Dropping it quietly would leave the ledger holding one figure while the
    router believes another, with nothing anywhere saying they disagreed."""
    base = seeded(tmp_path)
    assert import_rows(base, [dict(ROUTER_ROW)], season=SEASON)["written"] == 1

    restated = {**ROUTER_ROW, "net_profit_loss": 99.99}
    result = import_rows(base, [restated], season=SEASON)
    assert (result["written"], result["refused"]) == (0, 1)
    reason = result["refusals"][0][1]
    assert "CONTRADICTS" in reason
    assert "net_profit_loss" in reason
    # names only -- this reason is printed into a public Actions log
    assert "99.99" not in reason
    assert TICKER not in reason


def test_a_contradicting_settlement_does_not_stop_the_others(tmp_path):
    other_key = "kalshi:v1:0000000000000000000000000000000000000000"
    base = seeded(tmp_path)
    store.append_wagers(base, SEASON, [wager_row(
        wager_id="routed-xyz", source_bet_key=other_key)])
    import_rows(base, [dict(ROUTER_ROW)], season=SEASON)

    result = import_rows(base, [
        {**ROUTER_ROW, "result": "LOST", "gross_return": 0.0, "net_profit_loss": -11.94},
        {**ROUTER_ROW, "source_bet_key": other_key},
    ], season=SEASON)
    assert (result["written"], result["refused"]) == (1, 1)


def test_the_store_itself_refuses_a_contradicting_settlement(tmp_path):
    """The backstop, for any caller that is not the importer."""
    base = seeded(tmp_path)
    first = build_record(ROUTER_ROW).to_dict()
    store.append_settlements(base, SEASON, [first])
    with pytest.raises(ValueError, match="CONTRADICTS"):
        store.append_settlements(
            base, SEASON, [{**first, "gross_return": 0.0, "result": "LOST"}])


def test_an_identical_repeat_is_still_a_silent_noop(tmp_path):
    """The refusal above must not have broken idempotency."""
    base = seeded(tmp_path)
    record = build_record(ROUTER_ROW).to_dict()
    store.append_settlements(base, SEASON, [record])
    result = store.append_settlements(base, SEASON, [dict(record)])
    assert (result.written, result.skipped_duplicate) == (0, 1)


def test_two_contradicting_settlements_inside_one_batch_are_refused(tmp_path):
    base = seeded(tmp_path)
    result = import_rows(base, [
        dict(ROUTER_ROW),
        {**ROUTER_ROW, "net_profit_loss": 1.0},
    ], season=SEASON)
    # The first is written; the second reaches the store, which refuses to
    # append a contradiction of a row this very batch is writing.
    assert result["written"] == 1
    assert result["already_present"] + result["refused"] == 1


# ─────────────────────────────────────────────────────────────────────
# THE WRONG LEDGER IS AS BAD AS NO LEDGER
# ─────────────────────────────────────────────────────────────────────

def test_a_settlement_for_a_wager_in_a_different_season_is_refused(tmp_path):
    """The wager is real and this repository holds it -- in 2026. Filing its
    settlement against 2027 would make it an orphan there and leave the 2026
    wager unsettled forever, so the season is never inferred and never
    guessed."""
    base = seeded(tmp_path)
    result = import_rows(base, [dict(ROUTER_ROW)], season=2027)
    assert (result["written"], result["refused"]) == (0, 1)
    assert "no wager with this source_bet_key" in result["refusals"][0][1]
    assert not (base / "settlements" / "2027.jsonl").exists()


def test_a_settlement_with_a_source_key_no_wager_carries_is_refused(tmp_path):
    """Not a typo'd field -- a real-looking key that belongs to no wager."""
    base = seeded(tmp_path)
    result = import_rows(
        base,
        [{**ROUTER_ROW, "source_bet_key": "kalshi:v1:" + "f" * 40}],
        season=SEASON,
    )
    assert (result["written"], result["refused"]) == (0, 1)
    assert not (base / "settlements" / f"{SEASON}.jsonl").exists()


def test_an_orphan_is_refused_before_anything_is_written(tmp_path):
    """Refused BEFORE the write, not cleaned up after one."""
    base = seeded(tmp_path)
    ledger = base / "settlements" / f"{SEASON}.jsonl"
    result = import_rows(base, [
        {**ROUTER_ROW, "source_bet_key": "kalshi:v1:" + "a" * 40},
        {**ROUTER_ROW, "source_bet_key": "kalshi:v1:" + "b" * 40},
    ], season=SEASON)
    assert (result["written"], result["refused"]) == (0, 2)
    assert not ledger.exists()
