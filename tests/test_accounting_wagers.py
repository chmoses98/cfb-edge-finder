"""What the CFB wager ledger accepts, refuses, and never duplicates."""

from __future__ import annotations

import json

import pytest

from cfb_edge_finder.accounting import (
    ENTRY_METHOD_IMPORTED_RECEIPT,
    FORBIDDEN_PROVENANCE_FIELDS,
    SCHEMA_VERSION,
    AccountedWager,
    validate,
)
from cfb_edge_finder.accounting import store


def make(**overrides) -> dict:
    row = AccountedWager(
        wager_id="cfb-2026-0001",
        schema_version=SCHEMA_VERSION,
        source_bet_key="kalshi:order:9f2c",
        import_batch_id="kalshi-gap-backfill-2026-09-12-through-production-cutover-v1",
        entry_method=ENTRY_METHOD_IMPORTED_RECEIPT,
        game_date="2026-09-13",
        market_ticker="KXNCAAFGAME-26SEP13ALAGA-ALA",
        side="YES",
        executed_at="2026-09-13T18:04:11Z",
        contracts=40.0,
        execution_price=0.61,
        stake=24.40,
        season=2026,
        week=3,
    ).to_dict()
    row.update(overrides)
    return row


def test_a_reconstructed_wager_is_acceptable():
    assert validate(make()) == []


@pytest.mark.parametrize("field", FORBIDDEN_PROVENANCE_FIELDS)
def test_every_model_provenance_field_is_refused(field):
    """Not "ignored", not "dropped" -- refused loudly.

    A ledger that quietly discards a provenance field still accepts the write,
    so the caller that supplied it never learns it was wrong and keeps supplying
    it. The next reader sees a clean row and a caller who believes the model
    backed this bet.
    """
    problems = validate(make(**{field: "anything"}))
    assert any(field in problem for problem in problems), problems


def test_a_model_backed_claim_cannot_sneak_in_as_a_false_value():
    # `model_supported=False` looks harmless and is still a model claim: it
    # asserts the model had an opinion. The wager predates any such opinion.
    assert validate(make(model_supported=False)) != []


def test_entry_method_must_be_imported_receipt():
    assert validate(make(entry_method="MODEL")) != []
    assert validate(make(entry_method=None)) != []


def test_side_must_be_a_real_side():
    assert validate(make(side="MAYBE")) != []


def test_negative_economics_are_refused():
    assert validate(make(stake=-1.0)) != []
    assert validate(make(contracts=-1.0)) != []


def test_a_missing_game_date_is_refused():
    """Blank rather than guessed.

    The mission is explicit that gameDate may not be inferred from a market's
    close time. An empty string is what a caller that could not establish the
    date passes, so it must not be writable.
    """
    assert validate(make(game_date="")) != []
    assert validate(make(game_date=None)) != []


def test_profit_may_not_be_stated_from_estimated_fees():
    estimated = make(fees_paid=1.20, fees_are_estimated=True, net_profit_loss=14.40)
    assert validate(estimated) != []
    # The same row without the derived figure is fine: recording an estimated
    # fee is honest; reporting P&L computed from it as realised is not.
    ok = make(fees_paid=1.20, fees_are_estimated=True)
    assert validate(ok) == []


def test_settlement_fields_start_absent_rather_than_zero():
    row = make()
    for field in ("settlement_status", "result", "gross_return", "net_profit_loss"):
        assert row[field] is None, f"{field} should be absent until established"


def test_validation_reports_every_problem_not_just_the_first():
    problems = validate(make(side="MAYBE", entry_method="MODEL", stake=-1.0))
    assert len(problems) >= 3, problems


def test_rerunning_the_same_import_writes_nothing(tmp_path):
    rows = [make(), make(wager_id="cfb-2026-0002", source_bet_key="kalshi:order:7a11")]

    first = store.append_wagers(tmp_path, 2026, rows)
    assert (first.written, first.skipped_duplicate) == (2, 0)

    second = store.append_wagers(tmp_path, 2026, rows)
    assert (second.written, second.skipped_duplicate) == (0, 2)

    assert len(store.read_rows(store.ledger_path(tmp_path, 2026))) == 2


def test_a_duplicate_inside_one_batch_is_written_once(tmp_path):
    result = store.append_wagers(tmp_path, 2026, [make(), make()])
    assert (result.written, result.skipped_duplicate) == (1, 1)


def test_the_same_order_re_reconstructed_with_different_wording_still_dedupes(tmp_path):
    """Dedup keys on the VENUE's order identity, not on our description of it.

    A second backfill run that renders notes differently, or assigns a new
    wager_id, is still the same order. If dedup keyed on the whole row, a
    cosmetic difference would mint a second wager and double the owner's
    recorded exposure.
    """
    store.append_wagers(tmp_path, 2026, [make()])
    again = store.append_wagers(
        tmp_path, 2026, [make(wager_id="cfb-2026-9999", notes="re-derived")]
    )
    assert again.written == 0


def test_the_store_refuses_an_invalid_row_rather_than_writing_it(tmp_path):
    with pytest.raises(ValueError):
        store.append_wagers(tmp_path, 2026, [make(model_supported=True)])
    assert not store.ledger_path(tmp_path, 2026).exists()


def test_writes_append_and_never_rewrite_an_existing_line(tmp_path):
    store.append_wagers(tmp_path, 2026, [make()])
    path = store.ledger_path(tmp_path, 2026)
    before = path.read_text(encoding="utf-8")

    store.append_wagers(
        tmp_path, 2026, [make(wager_id="cfb-2026-0002", source_bet_key="kalshi:order:7a11")]
    )
    after = path.read_text(encoding="utf-8")

    assert after.startswith(before), "an existing ledger line was rewritten"


def test_a_corrupt_ledger_line_is_surfaced_not_silently_skipped(tmp_path):
    store.append_wagers(tmp_path, 2026, [make()])
    path = store.ledger_path(tmp_path, 2026)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")

    # Reading must fail loudly. If it skipped the bad line, the next import
    # would see that wager as absent and write it again.
    with pytest.raises(ValueError):
        store.read_rows(path)


def test_rows_are_written_as_one_json_object_per_line(tmp_path):
    store.append_wagers(tmp_path, 2026, [make()])
    lines = store.ledger_path(tmp_path, 2026).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["market_ticker"].startswith("KXNCAAF")


def test_the_ledger_never_lands_on_main():
    assert store.DATA_BRANCH != "main"
