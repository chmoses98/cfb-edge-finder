"""The kalshi-bet-router contract, exercised against this repository's importer.

ROUTER_ROW is captured verbatim from `kalshi_router.production.to_cfb_import_row`
-- run through the real emitter, not written by hand -- so this is a genuine
cross-repository contract test rather than a guess at what the router sends. If
the router changes its shape, this stops matching.

The MLB ledger learned that lesson expensively on 2026-09-15: the router emitted
execution economics in a shape that importer never read, the import SUCCEEDED,
and the wager was recorded with a stake and a price and no contracts, no fees
and no cash. Nothing failed. A test that runs the real payload through the real
importer and asserts the numbers survive is what catches that.

Note the vocabulary: CFB calls the quantity-weighted fill price
`execution_price`. NFL calls the same number `actual_price`. Renaming one to the
other has to fail here.
"""

from __future__ import annotations

import pytest

from cfb_edge_finder.accounting import store
from cfb_edge_finder.accounting.import_routed_wagers import (
    ImportRefused,
    build_record,
    import_rows,
    mint_wager_id,
)

#: Exactly what the router sends -- no wager_id, no season, no week.
ROUTER_ROW = {
    "source_bet_key": "kalshi:v1:3a1f7c5d2e8b49a0c6f1d84b7e2905cfa3b6d17e",
    "import_batch_id": "kalshi-gap-backfill-2026-09-12-through-production-cutover-v1",
    "entry_method": "IMPORTED_RECEIPT",
    "game_date": "2026-09-12",
    "market_ticker": "KXNCAAFGAME-26SEP12ALAUGA-ALA",
    "side": "YES",
    "executed_at": "2026-09-12T20:40:11Z",
    "contracts": 25.0,
    "execution_price": 0.47,
    "stake": 11.94,
    "fees_paid": 0.19,
    "fees_are_estimated": False,
    "venue": "kalshi",
}

SEASON = 2026


def test_the_exact_exchange_numbers_survive_the_import():
    """The assertion that would have caught the hollow MLB row."""
    record = build_record(ROUTER_ROW, season=SEASON)

    assert record.contracts == 25.0
    assert record.execution_price == 0.47
    assert record.stake == 11.94
    assert record.fees_paid == 0.19
    assert record.fees_are_estimated is False
    assert record.market_ticker == "KXNCAAFGAME-26SEP12ALAUGA-ALA"
    assert record.side == "YES"
    assert record.executed_at == "2026-09-12T20:40:11Z"
    assert record.game_date == "2026-09-12"
    assert record.venue == "kalshi"


def test_the_price_field_is_cfbs_own_name_for_it():
    """`actual_price` is the NFL ledger's word. Sending it here is a contract
    break, not a synonym -- and it must fail loudly rather than land a wager
    with no price."""
    nfl_vocabulary = {k: v for k, v in ROUTER_ROW.items() if k != "execution_price"}
    nfl_vocabulary["actual_price"] = 0.47

    with pytest.raises(ImportRefused, match="unknown field"):
        build_record(nfl_vocabulary, season=SEASON)


def test_the_id_is_derived_only_from_the_venue_key():
    """Identity is not economics.

    Correcting a fee on an already-imported wager must land on the SAME row, and
    the same order noticed by a second backfill is the same wager -- so the id
    may not move when the money or the batch does.
    """
    base = mint_wager_id(ROUTER_ROW["source_bet_key"])

    richer = dict(ROUTER_ROW, stake=999.0, fees_paid=9.99,
                  import_batch_id="some-other-batch")
    assert mint_wager_id(richer["source_bet_key"]) == base
    assert base != mint_wager_id("kalshi:v1:some-other-order")
    assert base.startswith("routed-")


def test_no_model_provenance_is_fabricated():
    """A Kalshi execution proves the owner placed the bet. It proves nothing
    about whether this repository's research-only model called it."""
    record = build_record(ROUTER_ROW, season=SEASON).to_dict()

    for forbidden in ("recommendation_id", "model_evaluation_id",
                      "model_fair_probability", "model_supported",
                      "projection_id", "rating", "edge", "expected_value"):
        assert forbidden not in record
    assert record["entry_method"] == "IMPORTED_RECEIPT"
    assert record["schema_version"] == "cfb_accounted_wager.v1"


def test_a_router_field_this_ledger_does_not_model_is_refused():
    """Not dropped quietly. The sender believes a field it sent was recorded, so
    silently discarding one is how a ledger ends up disagreeing with its source
    while both sides report success."""
    with pytest.raises(ImportRefused, match="unknown field"):
        build_record(dict(ROUTER_ROW, model_supported=True), season=SEASON)


def test_the_week_is_left_absent_rather_than_invented():
    """Optional is not an invitation. Nothing in the payload establishes a CFB
    week, so the field stays empty rather than being resolved from a date."""
    record = build_record(ROUTER_ROW, season=SEASON)

    assert record.week is None
    assert record.season == SEASON


def test_the_season_is_the_callers_declaration_not_a_guess_from_the_date():
    """A College Football season spans two calendar years, so a January bowl
    game's date says nothing reliable about which season it belongs to. The
    ledger is one file per season, so the caller has to name it, and naming a
    different one puts the row in a different file."""
    january = dict(ROUTER_ROW, game_date="2027-01-08")

    assert build_record(january, season=2026).season == 2026


def test_importing_writes_the_row(tmp_path):
    result = import_rows(tmp_path, [ROUTER_ROW], season=SEASON)

    assert result["written"] == 1
    assert result["already_present"] == 0
    assert result["refused"] == 0

    rows = store.read_rows(store.ledger_path(tmp_path, SEASON))
    assert len(rows) == 1
    assert rows[0]["contracts"] == 25.0
    assert rows[0]["execution_price"] == 0.47
    assert rows[0]["fees_paid"] == 0.19
    assert rows[0]["wager_id"] == mint_wager_id(ROUTER_ROW["source_bet_key"])


def test_a_second_identical_import_writes_nothing(tmp_path):
    """Re-running a backfill has to be boring.

    The no-op is a property of the STORE -- it keys on source_bet_key against
    the whole file -- so this importer does not reimplement dedup and this test
    proves the two actually compose.
    """
    first = import_rows(tmp_path, [ROUTER_ROW], season=SEASON)
    second = import_rows(tmp_path, [ROUTER_ROW], season=SEASON)

    assert (first["written"], first["already_present"]) == (1, 0)
    assert (second["written"], second["already_present"]) == (0, 1)
    assert len(store.read_rows(store.ledger_path(tmp_path, SEASON))) == 1


def test_a_corrected_fee_lands_on_the_same_identity(tmp_path):
    """Not a second wager. The id is the venue's order, so a correction is a
    correction rather than a duplicate bet appearing in the accounting."""
    import_rows(tmp_path, [ROUTER_ROW], season=SEASON)
    corrected = import_rows(tmp_path, [dict(ROUTER_ROW, fees_paid=0.21)], season=SEASON)

    assert corrected["written"] == 0
    assert corrected["already_present"] == 1
    assert len(store.read_rows(store.ledger_path(tmp_path, SEASON))) == 1


def test_an_unimportable_row_is_reported_rather_than_dropped(tmp_path):
    """Refused is a COUNT and a REASON, not silence."""
    result = import_rows(tmp_path, [dict(ROUTER_ROW, side="MAYBE")], season=SEASON)

    assert result["written"] == 0
    assert result["refused"] == 1
    assert "side must be YES or NO" in result["refusals"][0][1]
    assert store.read_rows(store.ledger_path(tmp_path, SEASON)) == []


def test_one_refused_row_does_not_stop_the_others(tmp_path):
    """One unresolvable wager in a batch should cost one wager, not the batch.

    `store.append_wagers` RAISES on the first invalid row, aborting everything
    it was given -- which is correct for a store and wrong for a backfill of
    thirty orders. Screening per row before handing the batch over is what keeps
    one bad row from costing twenty-nine good ones.
    """
    good = dict(ROUTER_ROW, source_bet_key="kalshi:v1:second-order")
    result = import_rows(tmp_path, [dict(ROUTER_ROW, side="MAYBE"), good], season=SEASON)

    assert result["written"] == 1
    assert result["refused"] == 1
    assert len(store.read_rows(store.ledger_path(tmp_path, SEASON))) == 1


def test_a_row_without_a_venue_key_cannot_be_named(tmp_path):
    """No source_bet_key means no identity, and no identity means dedup cannot
    work -- so the row would be written again on every re-run."""
    result = import_rows(tmp_path, [dict(ROUTER_ROW, source_bet_key="")], season=SEASON)

    assert result["refused"] == 1
    assert "source_bet_key is required" in result["refusals"][0][1]


def test_the_row_lands_in_the_named_seasons_ledger(tmp_path):
    import_rows(tmp_path, [ROUTER_ROW], season=SEASON)

    assert store.ledger_path(tmp_path, SEASON).exists()
    assert not store.ledger_path(tmp_path, 2025).exists()
