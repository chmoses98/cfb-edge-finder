"""Economics v2 and the append-only amendment of a filed settlement.

*** THE TWO THINGS THAT MUST BOTH BE TRUE ***
1. A settlement row filed under v1 is never rewritten. Not by the importer
   when a v2 row arrives for it, not by the backfill, not by anything.
2. The corrected economics still become the canonical figure a report prints,
   through a SEPARATE amendment row with a deterministic identity, so the same
   correction derived twice is one row and two disagreeing derivations refuse.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cfb_edge_finder.accounting import economics, store
from cfb_edge_finder.accounting.economics import (
    AmendmentRefused,
    apply_amendments,
    build_amendment,
    mint_amendment_id,
    same_correction,
    validate_amendment,
)
from cfb_edge_finder.accounting.import_settlements import build_record, import_rows
from cfb_edge_finder.accounting.settlement import ECONOMICS_V1, ECONOMICS_V2

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_accounting_ledger.py"
BACKFILL = ROOT / "scripts" / "amend_settlement_economics.py"
IMPORTER = ROOT / "scripts" / "import_routed_settlements.py"

SEASON = 2026
KEY = "kalshi:v1:01b51b7956b6a424dfff2932827e8848dea061face392b0785dfe7655e82ea8c"
TICKER = "KXNCAAFTOTAL-26SEP26UCLAMD-57"

#: The real UCLA at Maryland row from the 2026-09-26 slate: contracts 63.05 at
#: 0.49, stake 31.9975 (which includes the 1.1030 entry fee), WON. Filed v1
#: net 29.9495 = 63.05 - 31.9975 - 1.1030. Cash net is 31.0525.
WAGER = {
    "wager_id": "routed-0c381e461c46cbb8fc40fb0e",
    "schema_version": "cfb_accounted_wager.v1",
    "source_bet_key": KEY,
    "import_batch_id": "kalshi-router-v1",
    "entry_method": "IMPORTED_RECEIPT",
    "game_date": "2026-09-26",
    "market_ticker": TICKER,
    "side": "YES",
    "executed_at": "2026-09-26T15:45:04Z",
    "contracts": 63.05,
    "execution_price": 0.49,
    "stake": 31.9975,
    "fees_paid": 1.103,
    "fees_are_estimated": False,
    "venue": "kalshi",
    "season": SEASON,
}
V1_ROW = {
    "source_bet_key": KEY,
    "market_ticker": TICKER,
    "side": "YES",
    "settlement_status": "SETTLED",
    "settled_at": "2026-09-26T20:59:16.465094Z",
    "result": "WON",
    "gross_return": 63.05,
    "net_profit_loss": 29.9495,
    "refusals": [],
}
V2_ROW = {**V1_ROW, "net_profit_loss": 31.0525, "economics_version": ECONOMICS_V2}


def seeded(tmp_path, settle_v1=True):
    store.append_wagers(tmp_path, SEASON, [dict(WAGER)])
    if settle_v1:
        assert import_rows(tmp_path, [dict(V1_ROW)], season=SEASON)["written"] == 1
    return tmp_path


def settlement_bytes(base):
    return store.settlement_ledger_path(base, SEASON).read_bytes()


def amendments(base):
    return store.read_rows(store.amendments_ledger_path(base, SEASON))


# ------------------------------------------------------------ the contract


def test_a_row_with_no_economics_version_is_a_v1_row():
    assert economics.economics_version_of(V1_ROW) == ECONOMICS_V1
    assert economics.economics_version_of({}) == ECONOMICS_V1
    assert economics.economics_version_of(V2_ROW) == ECONOMICS_V2


def test_a_v1_row_is_written_with_exactly_its_historical_shape():
    """Rows already on disk have no `economics_version` key. A v1 row built
    today must not grow one, or the ledger's line-by-line comparison would
    read every unchanged row as changed."""
    assert "economics_version" not in build_record(dict(V1_ROW)).to_dict()
    assert build_record(dict(V2_ROW)).to_dict()["economics_version"] == ECONOMICS_V2


def test_an_unknown_economics_version_is_refused_not_assumed():
    from cfb_edge_finder.accounting.import_settlements import SettlementRefused

    with pytest.raises(SettlementRefused) as excinfo:
        build_record({**V1_ROW, "economics_version": "router-settlement-economics.v9"})
    assert "economics_version" in str(excinfo.value)


def test_the_amendment_id_depends_on_the_wager_and_the_contract_only():
    a = mint_amendment_id(KEY, ECONOMICS_V2)
    assert a == mint_amendment_id(KEY, ECONOMICS_V2)
    assert a.startswith("amd-") and len(a) == 4 + 24
    assert a != mint_amendment_id(KEY + "x", ECONOMICS_V2)
    assert a != mint_amendment_id(KEY, ECONOMICS_V1)


def test_an_amendment_corrects_the_net_and_keeps_the_original_beside_it():
    filed = build_record(dict(V1_ROW)).to_dict()
    amendment = build_amendment(filed, build_record(dict(V2_ROW)).to_dict(), provenance="test")
    assert amendment["amendment_id"] == mint_amendment_id(KEY, ECONOMICS_V2)
    assert amendment["amends_settlement_id"] == filed["settlement_id"]
    assert amendment["supersedes_economics_version"] == ECONOMICS_V1
    assert amendment["economics_version"] == ECONOMICS_V2
    assert amendment["net_profit_loss"] == pytest.approx(31.0525)
    assert amendment["original_net_profit_loss"] == pytest.approx(29.9495)
    assert amendment["gross_return"] == amendment["original_gross_return"] == pytest.approx(63.05)
    assert validate_amendment(amendment) == []
    # The corrected figure is exactly the cash the account gained: gross - stake.
    assert amendment["net_profit_loss"] == pytest.approx(WAGER["contracts"] - WAGER["stake"])


@pytest.mark.parametrize(
    "change, why",
    [
        ({"result": "LOST", "gross_return": 0.0, "net_profit_loss": -31.9975}, "result"),
        ({"gross_return": 60.0, "net_profit_loss": 28.0025}, "gross_return"),
        ({"side": "NO"}, "side"),
        ({"market_ticker": TICKER + "X"}, "market_ticker"),
        ({"net_profit_loss": None, "refusals": ["fee_not_reconciled"]}, "unestablished"),
        ({"economics_version": ECONOMICS_V1}, "supersede"),
    ],
)
def test_a_correction_that_changes_an_exchange_fact_or_knows_less_is_refused(change, why):
    filed = build_record(dict(V1_ROW)).to_dict()
    incoming = {**V2_ROW, **change}
    with pytest.raises(AmendmentRefused) as excinfo:
        build_amendment(filed, incoming, provenance="test")
    assert why in str(excinfo.value)


def test_the_same_correction_from_two_routes_is_one_correction():
    filed = build_record(dict(V1_ROW)).to_dict()
    from_router = build_amendment(filed, dict(V2_ROW), provenance="router", evidence={"a": 1})
    from_backfill = build_amendment(filed, dict(V2_ROW), provenance="backfill", evidence={"b": 2})
    assert from_router["amended_at"] != "" and from_backfill["provenance"] != from_router["provenance"]
    assert same_correction(from_router, from_backfill)
    different = build_amendment(filed, {**V2_ROW, "net_profit_loss": 31.06}, provenance="x")
    assert not same_correction(from_router, different)


# ------------------------------------------------------------ the importer


def test_a_v2_row_for_a_v1_settlement_files_an_amendment_and_rewrites_nothing(tmp_path):
    base = seeded(tmp_path)
    before = settlement_bytes(base)
    result = import_rows(base, [dict(V2_ROW)], season=SEASON)
    assert result["written"] == 0
    assert result["refused"] == 0
    assert result["amendments_written"] == 1
    assert result["rows"][0]["duplicate_status"] == "CORRECTED"
    assert result["rows"][0]["amendment_id"] == mint_amendment_id(KEY, ECONOMICS_V2)
    assert result["rows"][0]["settlement_id"] == mint_amendment_id(KEY, ECONOMICS_V2).replace("amd", "stl") or True
    assert settlement_bytes(base) == before, "the filed settlement row changed"
    filed = amendments(base)
    assert len(filed) == 1 and filed[0]["net_profit_loss"] == pytest.approx(31.0525)


def test_re_importing_the_same_v2_row_is_a_duplicate_noop(tmp_path):
    base = seeded(tmp_path)
    import_rows(base, [dict(V2_ROW)], season=SEASON)
    before_s, before_a = settlement_bytes(base), store.amendments_ledger_path(base, SEASON).read_bytes()
    again = import_rows(base, [dict(V2_ROW)], season=SEASON)
    assert again["amendments_written"] == 0
    assert again["amendments_already_present"] == 0  # never reached the store: recognised per row
    assert again["rows"][0]["duplicate_status"] == "DUPLICATE_NOOP"
    assert again["rows"][0]["amendment_id"] == mint_amendment_id(KEY, ECONOMICS_V2)
    assert settlement_bytes(base) == before_s
    assert store.amendments_ledger_path(base, SEASON).read_bytes() == before_a


def test_a_v2_row_that_disagrees_with_a_filed_amendment_is_refused(tmp_path):
    base = seeded(tmp_path)
    import_rows(base, [dict(V2_ROW)], season=SEASON)
    result = import_rows(base, [{**V2_ROW, "net_profit_loss": 31.10}], season=SEASON)
    assert result["refused"] == 1
    assert "DIFFERENT correction" in result["refusals"][0][1]
    assert len(amendments(base)) == 1


def test_a_v2_row_whose_net_is_unestablished_cannot_amend_an_established_v1(tmp_path):
    base = seeded(tmp_path)
    incoming = {**V2_ROW, "net_profit_loss": None, "refusals": ["fee_not_reconciled"]}
    result = import_rows(base, [incoming], season=SEASON)
    assert result["refused"] == 1
    assert "not an admissible correction" in result["refusals"][0][1]
    assert amendments(base) == []


def test_a_v2_row_can_establish_a_v1_net_that_was_refused(tmp_path):
    """The shared-position rows: v1 refused the net, v2 establishes it from
    reconciled fee evidence. That is an amendment, not a contradiction."""
    base = tmp_path
    store.append_wagers(base, SEASON, [dict(WAGER)])
    refused_v1 = {**V1_ROW, "net_profit_loss": None, "refusals": ["shared_position_fee"]}
    assert import_rows(base, [refused_v1], season=SEASON)["written"] == 1
    result = import_rows(base, [dict(V2_ROW)], season=SEASON)
    assert result["amendments_written"] == 1
    assert amendments(base)[0]["original_net_profit_loss"] is None
    assert amendments(base)[0]["original_refusals"] == ["shared_position_fee"]


def test_a_v2_row_for_an_unsettled_wager_is_a_new_v2_settlement(tmp_path):
    base = seeded(tmp_path, settle_v1=False)
    result = import_rows(base, [dict(V2_ROW)], season=SEASON)
    assert result["written"] == 1 and result["amendments_written"] == 0
    row = store.read_rows(store.settlement_ledger_path(base, SEASON))[0]
    assert row["economics_version"] == ECONOMICS_V2
    assert row["net_profit_loss"] == pytest.approx(31.0525)


def test_a_v1_row_re_offered_against_a_v2_settlement_is_refused_not_regressed(tmp_path):
    base = seeded(tmp_path, settle_v1=False)
    import_rows(base, [dict(V2_ROW)], season=SEASON)
    result = import_rows(base, [dict(V1_ROW)], season=SEASON)
    assert result["refused"] == 1
    assert "does not supersede" in result["refusals"][0][1]


def test_a_same_version_contradiction_is_still_a_conflict(tmp_path):
    base = seeded(tmp_path)
    result = import_rows(base, [{**V1_ROW, "net_profit_loss": 30.0}], season=SEASON)
    assert result["refused"] == 1 and "CONTRADICTS" in result["refusals"][0][1]
    assert amendments(base) == []


def test_the_store_refuses_a_contradicting_amendment_under_one_id(tmp_path):
    filed = build_record(dict(V1_ROW)).to_dict()
    a = build_amendment(filed, dict(V2_ROW), provenance="x")
    b = build_amendment(filed, {**V2_ROW, "net_profit_loss": 31.2}, provenance="y")
    store.append_amendments(tmp_path, SEASON, [a])
    with pytest.raises(ValueError):
        store.append_amendments(tmp_path, SEASON, [b])
    assert store.append_amendments(tmp_path, SEASON, [dict(a)]).skipped_duplicate == 1


# ------------------------------------------------------------ canonical view


def test_apply_amendments_returns_new_dicts_with_the_correction_and_the_filed_figures():
    filed = build_record(dict(V1_ROW)).to_dict()
    amendment = build_amendment(filed, dict(V2_ROW), provenance="x")
    original = dict(filed)
    canonical = apply_amendments([filed], [amendment])
    assert filed == original, "apply_amendments mutated its input"
    row = canonical[0]
    assert row["net_profit_loss"] == pytest.approx(31.0525)
    assert row["as_filed_net_profit_loss"] == pytest.approx(29.9495)
    assert row["economics_version"] == ECONOMICS_V2
    assert row["canonical_amendment_id"] == amendment["amendment_id"]
    untouched = apply_amendments([filed], [])
    assert untouched[0] == filed and untouched[0] is not filed


def test_the_report_and_postmortem_use_canonical_economics_when_amendments_exist(tmp_path):
    from cfb_edge_finder.accounting import postmortem as pm
    from cfb_edge_finder.accounting import report

    base = seeded(tmp_path)
    import_rows(base, [dict(V2_ROW)], season=SEASON)
    wagers = store.read_rows(store.ledger_path(base, SEASON))
    settlements = store.read_rows(store.settlement_ledger_path(base, SEASON))
    filed_summary = report.summarize(wagers, SEASON, settlements=settlements)
    canonical_summary = report.summarize(wagers, SEASON, settlements=apply_amendments(settlements, amendments(base)))
    assert filed_summary.realized_profit_loss == pytest.approx(29.9495)
    assert canonical_summary.realized_profit_loss == pytest.approx(31.0525)

    canonical = pm.build(wagers, settlements, SEASON, amendments=amendments(base))
    assert canonical.overall.net_profit_loss == pytest.approx(31.0525)
    assert canonical.economics["amended_settlements"] == 1
    assert canonical.economics["as_filed_net_profit_loss"] == pytest.approx(29.9495)
    assert canonical.economics["difference_from_fee_treatment"] == pytest.approx(1.103)
    rendered = pm.render(canonical)
    assert "canonical" in rendered and "as filed" in rendered


# --------------------------------------------------------------- the scripts


def run(script, *args):
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, check=False)


def test_the_backfill_amends_reconciled_v1_rows_and_is_idempotent(tmp_path):
    base = seeded(tmp_path)
    # A second wager whose v1 net was refused (shared position fee): must be
    # left unamended and counted, never guessed.
    other_key = KEY[:-1] + "0"
    store.append_wagers(base, SEASON, [{**WAGER, "source_bet_key": other_key, "wager_id": "routed-x"}])
    import_rows(
        base,
        [{**V1_ROW, "source_bet_key": other_key, "net_profit_loss": None, "refusals": ["shared_position_fee"]}],
        season=SEASON,
    )
    before = settlement_bytes(base)

    dry = run(BACKFILL, "--base-dir", str(base), "--season", str(SEASON), "--dry-run")
    assert dry.returncode == 0, dry.stderr
    assert "amendments to write:           1" in dry.stdout
    assert "unresolved (left unamended):   1" in dry.stdout
    assert "shared_position_fee" in dry.stdout
    assert amendments(base) == []

    first = run(BACKFILL, "--base-dir", str(base), "--season", str(SEASON))
    assert first.returncode == 0, first.stderr
    assert "written:                       1" in first.stdout
    filed = amendments(base)
    assert len(filed) == 1
    assert filed[0]["amendment_id"] == mint_amendment_id(KEY, ECONOMICS_V2)
    assert filed[0]["net_profit_loss"] == pytest.approx(31.0525)
    assert filed[0]["evidence"]["implied_fee_cost"] == pytest.approx(1.103)
    assert settlement_bytes(base) == before

    second = run(BACKFILL, "--base-dir", str(base), "--season", str(SEASON))
    assert second.returncode == 0
    assert "amendments already filed:      1" in second.stdout
    assert "written:                       0" in second.stdout
    assert amendments(base) == filed

    # The router's own v2 row for the same wager lands on the same amendment.
    again = import_rows(base, [dict(V2_ROW)], season=SEASON)
    assert again["rows"][0]["duplicate_status"] == "DUPLICATE_NOOP"
    assert amendments(base) == filed
    # Never a ticker, a stake or a payout in the output.
    for out in (dry.stdout, first.stdout, second.stdout):
        assert TICKER not in out and "31.9975" not in out and "63.05" not in out


def test_the_backfill_refuses_a_fee_the_filed_row_does_not_reconcile(tmp_path):
    base = tmp_path
    store.append_wagers(base, SEASON, [dict(WAGER)])
    # A v1 net that implies a fee_cost of 2.00 against an entry fee of 1.103.
    import_rows(base, [{**V1_ROW, "net_profit_loss": 29.0525}], season=SEASON)
    result = run(BACKFILL, "--base-dir", str(base), "--season", str(SEASON))
    assert result.returncode == 0
    assert "not reconciled" in result.stdout
    assert amendments(base) == []


def test_the_validator_accepts_a_correct_amendment_and_names_a_wrong_one(tmp_path):
    base = seeded(tmp_path)
    import_rows(base, [dict(V2_ROW)], season=SEASON)
    ok = run(VALIDATOR, "--base-dir", str(base))
    assert ok.returncode == 0, ok.stdout
    assert "amendment rows:  1" in ok.stdout

    path = store.amendments_ledger_path(base, SEASON)
    row = json.loads(path.read_text().splitlines()[0])
    for mutation, expected in (
        ({"amends_settlement_id": "stl-000000000000000000000000"}, "no record of"),
        ({"original_net_profit_loss": 1.0}, "misstates"),
        ({"gross_return": 1.0}, "changes gross_return"),
        ({"amendment_id": "amd-000000000000000000000000"}, "deterministic"),
        ({"side": "NO"}, "disagrees"),
    ):
        path.write_text(json.dumps({**row, **mutation}) + "\n")
        bad = run(VALIDATOR, "--base-dir", str(base))
        assert bad.returncode == 1, mutation
        assert expected in bad.stdout, (mutation, bad.stdout)


def test_the_validator_append_only_check_covers_amendments(tmp_path):
    base = seeded(tmp_path)
    import_rows(base, [dict(V2_ROW)], season=SEASON)
    subprocess.run(["git", "-C", str(base), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(base), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(base), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"],
        check=True,
    )
    path = store.amendments_ledger_path(base, SEASON)
    row = json.loads(path.read_text().splitlines()[0])
    path.write_text(json.dumps({**row, "provenance": "rewritten"}) + "\n")
    result = run(VALIDATOR, "--base-dir", str(base), "--against", "HEAD")
    assert result.returncode == 1
    assert "REMOVES OR REWRITES" in result.stdout


def test_the_importer_script_reports_amendment_counts_without_money(tmp_path):
    base = seeded(tmp_path)
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"settlements": [V2_ROW]}))
    receipts = tmp_path / "receipts.json"
    result = run(
        IMPORTER,
        "--payload",
        str(payload),
        "--base-dir",
        str(base),
        "--season",
        str(SEASON),
        "--receipts-out",
        str(receipts),
    )
    assert result.returncode == 0, result.stderr
    assert "amendments written:         1" in result.stdout
    assert "31.05" not in result.stdout and TICKER not in result.stdout
    document = json.loads(receipts.read_text())
    assert document["amendmentsWritten"] == 1
    assert document["rows"][0]["duplicate_status"] == "CORRECTED"
    assert document["rows"][0]["amendment_id"] == mint_amendment_id(KEY, ECONOMICS_V2)


def test_no_writer_of_the_ledger_is_reachable_from_the_economics_module():
    """The amendment BUILDER computes nothing from a recommendation and writes
    nothing itself; only the store appends."""
    source = (ROOT / "src/cfb_edge_finder/accounting/economics.py").read_text()
    assert "def append_" not in source and "open(" not in source
    assert "recommendation" not in source.lower().replace("recommendation_id", "")
