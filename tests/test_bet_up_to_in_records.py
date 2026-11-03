"""RUN CFB validation: no legacy bet-up-to can authorize, size, record or judge an order.

`pit_review` stamps every candidate with `fair_value.conservative_bet_up_to` (highest whole cent at which the
handicap's STATED lower bound clears fee-inclusive break-even + the required edge; `None` with a reason when no
uncertainty is established, including a zero-width range, which is a point estimate). The legacy top-level
`bet_up_to_price` is a base-case point probability with no stated uncertainty: history only, labelled
LEGACY_NON_ACTIONABLE. The decision record and the accounting link must carry the defensible ceiling or None, and an
artifact / record / ledger row on disk that lacks the new field must NOT fall back to the legacy figure.
"""

from __future__ import annotations

import copy
import json

import pytest

from cfb_edge_finder.accounting import decision_attribution as da
from cfb_edge_finder.accounting.recommendation_link import (
    Recommendation,
    recommendations_from_artifact,
    stored_bet_up_to,
)
from cfb_edge_finder.decisions import PRIVATE_MARKER
from cfb_edge_finder.decisions.record import build_decision_record, defensible_bet_up_to, validate_decision_record
from tests.execution_fakes import NOW
from tests.test_decision_records import candidates, minimal_record, prepared, wager

LEGACY = 0.617849
LEGACY_BASIS = "NO_FAIR_VALUE_BLOCK_LEGACY_NON_ACTIONABLE"
STATED = "HANDICAP_STATED_LOWER_BOUND_UNCALIBRATED"


def _artifact(tmp_path):
    out, shard = prepared(tmp_path)
    assert candidates(out, shard, "--no-decision-record") == 0
    return json.loads((out / "candidates" / f"{shard}.candidates.json").read_text())


def _record(artifact):
    return build_decision_record(artifact=artifact, reduction_ledger=None, handicaps={}, packets={}, slate={},
                                 batch_entry=None, source_files={}, created_at=NOW.isoformat())


def _with_fair_value(artifact, **fair_value):
    art = copy.deepcopy(artifact)
    for cand in art["candidates"]:
        cand["bet_up_to_price"] = LEGACY
        cand["fair_value"] = dict(fair_value)
    return art


def _old_format(artifact):
    """An artifact from before `fair_value` existed: only the legacy figure."""
    art = copy.deepcopy(artifact)
    for cand in art["candidates"]:
        cand["bet_up_to_price"] = LEGACY
        cand.pop("fair_value", None)
        cand.pop("pit_review", None)
    art.pop("pit_review", None)
    return art


# ------------------------------------------------------------------ record from a new artifact


def test_no_stated_uncertainty_means_the_record_has_no_bet_up_to_and_says_why(tmp_path):
    art = _with_fair_value(_artifact(tmp_path), conservative_bet_up_to=None, conservative_bet_up_to_basis=None,
                           conservative_bet_up_to_missing_reason="NO_STATED_UNCERTAINTY_REGION")
    record = _record(art)
    assert record["candidates"]
    for cand in record["candidates"]:
        assert cand["bet_up_to"] is None, "an invented ceiling must not be persisted as the recommendation's bet-up-to"
        assert cand["bet_up_to_basis"] == "NO_STATED_UNCERTAINTY_REGION"
        assert cand["bet_up_to_legacy"] == LEGACY, "the legacy figure stays on the record, labelled, for history"
        assert cand["bet_up_to_legacy_status"] == "LEGACY_NON_ACTIONABLE"


def test_a_defensible_ceiling_is_what_the_record_carries(tmp_path):
    art = _with_fair_value(_artifact(tmp_path), conservative_bet_up_to=0.53, conservative_bet_up_to_basis=STATED,
                           conservative_bet_up_to_missing_reason=None)
    for cand in _record(art)["candidates"]:
        assert cand["bet_up_to"] == 0.53 and cand["bet_up_to_legacy"] == LEGACY
        assert cand["bet_up_to_basis"] == STATED


# ------------------------------------------------------------------ OLD artifacts never fall back to legacy


def test_an_artifact_without_the_fair_value_block_has_no_ceiling_not_the_legacy_one(tmp_path):
    old = _old_format(_artifact(tmp_path))
    assert all("fair_value" not in c and c["bet_up_to_price"] == LEGACY for c in old["candidates"])
    for cand in _record(old)["candidates"]:
        assert cand["bet_up_to"] is None and cand["bet_up_to_basis"] == LEGACY_BASIS
        assert cand["bet_up_to_legacy"] == LEGACY
    for rec in recommendations_from_artifact(old):
        assert rec.bet_up_to is None and rec.bet_up_to_basis == LEGACY_BASIS


def test_a_fair_value_block_missing_the_ceiling_key_is_also_not_a_fallback(tmp_path):
    art = copy.deepcopy(_artifact(tmp_path))
    for cand in art["candidates"]:
        cand["bet_up_to_price"] = LEGACY
        cand["fair_value"] = {"qualified_positive_ev": False}
    assert all(c["bet_up_to"] is None for c in _record(art)["candidates"])
    assert all(r.bet_up_to is None for r in recommendations_from_artifact(art))
    assert defensible_bet_up_to({"bet_up_to_price": LEGACY}) == (None, LEGACY_BASIS)


def test_an_old_decision_record_on_disk_does_not_judge_fills_by_its_legacy_ceiling():
    """A record written before the change: `bet_up_to` WAS the legacy figure and there is no `bet_up_to_basis`."""
    old = minimal_record()
    for cand in old["candidates"]:
        cand.pop("bet_up_to_basis", None)
        assert cand["bet_up_to"] == 0.53
    assert validate_decision_record(old) == [], "an old record stays readable"
    # A fill ABOVE the legacy 0.53 would have been named 'executed above bet-up-to'; there is no defensible ceiling now.
    result = da.attribute([wager(execution_price=0.55)], [old])
    entry = result.entries[0]
    assert entry["bet_up_to"] is None and entry["inside_bet_up_to"] is None
    # (any drift flag is the decision-time PRICE tolerance, never a judgment against the legacy ceiling)
    assert "above the bet-up-to" not in result.matches.matches[0].reason
    assert [a["bet_up_to"] for a in entry["alternatives"]] == [None]
    rec = da.candidates_from_records([old])[0].as_recommendation()
    assert rec.bet_up_to is None and rec.bet_up_to_basis == LEGACY_BASIS


def test_an_old_ledger_recommendation_row_is_not_trusted_for_its_ceiling():
    legacy_row = {"recommendation_id": "x", "ticker": "T", "side": "yes", "bet_up_to": LEGACY}
    assert Recommendation.from_dict(legacy_row).bet_up_to is None
    assert stored_bet_up_to(legacy_row) == (None, LEGACY_BASIS)
    marked_legacy = {**legacy_row, "bet_up_to_basis": LEGACY_BASIS}
    assert stored_bet_up_to(marked_legacy)[0] is None
    # a figure without any stated basis is not defensible either
    assert stored_bet_up_to({**legacy_row, "bet_up_to_basis": None})[0] is None
    # a new-format row round-trips
    new_row = Recommendation.from_dict({**legacy_row, "bet_up_to": 0.53, "bet_up_to_basis": STATED})
    assert new_row.bet_up_to == 0.53
    assert Recommendation.from_dict(new_row.as_dict()).bet_up_to == 0.53


# ------------------------------------------------------------------ the accounting link


def test_the_accounting_link_reads_the_same_defensible_ceiling(tmp_path):
    none_art = _with_fair_value(_artifact(tmp_path), conservative_bet_up_to=None,
                                conservative_bet_up_to_missing_reason="NO_STATED_UNCERTAINTY_REGION")
    assert all(r.bet_up_to is None for r in recommendations_from_artifact(none_art))
    some_art = _with_fair_value(_artifact(tmp_path / "b"), conservative_bet_up_to=0.53,
                                conservative_bet_up_to_basis=STATED)
    assert all(r.bet_up_to == 0.53 for r in recommendations_from_artifact(some_art))


# ------------------------------------------------------------------ full path: recommendation -> record -> accounting


def _fill(first, price):
    return wager(market_ticker=first["market_ticker"], side=first["side"], execution_price=price,
                 executed_at=NOW.replace(microsecond=0).isoformat().replace("+00:00", "Z"))


def _review(artifact, *, low, high, scenarios=3):
    """Give every candidate a stated range and re-run the REAL point-in-time review on it (no stub)."""
    from cfb_edge_finder.execution.pit_review import ReviewContext, review_candidate

    ctx = ReviewContext(decision_time=NOW, quote_captured_at=NOW, packets={}, handicaps={})
    art = copy.deepcopy(artifact)
    for cand in art["candidates"]:
        cand["fair_probability_range"] = [low, high]
        cand["scenarios_tested"] = scenarios
        cand["fair_value"] = review_candidate(cand, ctx)["fair_value"]
    return art


def test_cli_persisted_record_matches_the_artifact_and_has_no_ceiling_for_point_estimates(tmp_path, monkeypatch):
    root = tmp_path / "private"
    root.mkdir()
    (root / PRIVATE_MARKER).write_text("")
    monkeypatch.setenv("CFB_DECISION_STORE", str(root))
    out, shard = prepared(tmp_path)
    assert candidates(out, shard) == 0
    artifact = json.loads((out / "candidates" / f"{shard}.candidates.json").read_text())
    (written,) = list(root.rglob("*.json"))
    record = json.loads(written.read_text())
    assert validate_decision_record(record) == []
    assert artifact["candidates"]
    for cand, rec_cand in zip(artifact["candidates"], record["candidates"], strict=True):
        # the committed fixtures state zero-width ranges: point estimates, so NO ceiling anywhere
        assert cand["fair_value"]["uncertainty_tier"] == "POINT_ESTIMATE_NO_UNCERTAINTY"
        assert cand["fair_value"]["conservative_bet_up_to"] is None
        assert cand["bet_up_to_price_status"] == "LEGACY_NON_ACTIONABLE"
        assert rec_cand["bet_up_to"] is None and rec_cand["bet_up_to_basis"] == "POINT_ESTIMATE_NO_UNCERTAINTY"
        assert rec_cand["bet_up_to_legacy"] == cand["bet_up_to_price"]
    assert all(r.bet_up_to is None for r in recommendations_from_artifact(artifact))


def test_full_path_recommendation_to_record_to_accounting_uses_only_the_conservative_ceiling(tmp_path):
    art = _review(_artifact(tmp_path), low=0.60, high=0.75)
    record = _record(art)
    assert validate_decision_record(record) == []
    for cand, rec_cand in zip(art["candidates"], record["candidates"], strict=True):
        fair = cand["fair_value"]
        assert fair["uncertainty_tier"] == "STATED_UNCERTAINTY_REGION_UNCALIBRATED"
        assert fair["conservative_bet_up_to"] is not None
        # recommendation -> record
        assert rec_cand["bet_up_to"] == fair["conservative_bet_up_to"]
        assert rec_cand["bet_up_to_basis"] == STATED
        assert rec_cand["bet_up_to_legacy"] == cand["bet_up_to_price"]
    # recommendation -> accounting link
    link = recommendations_from_artifact(art)
    assert [r.bet_up_to for r in link] == [c["fair_value"]["conservative_bet_up_to"] for c in art["candidates"]]
    # record -> decision attribution -> match against a fill
    first = record["candidates"][0]
    ceiling, legacy = first["bet_up_to"], first["bet_up_to_legacy"]
    assert ceiling < legacy, "fixture: the legacy figure is looser than the defensible one"
    between = round((ceiling + legacy) / 2, 4)
    # A price between the defensible and the legacy ceiling is OUTSIDE the ceiling: the legacy figure never excuses it.
    above = da.attribute([_fill(first, between)], [record])
    assert above.entries[0]["bet_up_to"] == ceiling and above.entries[0]["inside_bet_up_to"] is False
    assert "above the bet-up-to" in above.matches.matches[0].reason
    inside = da.attribute([_fill(first, round(ceiling - 0.01, 4))], [record])
    assert inside.entries[0]["inside_bet_up_to"] is True


def test_full_path_with_a_zero_width_range_never_invents_a_ceiling(tmp_path):
    """Artifact -> record -> accounting all carry None for a point estimate, whatever the fill price."""
    art = _review(_artifact(tmp_path), low=0.7, high=0.7, scenarios=7)
    record = _record(art)
    for c in record["candidates"]:
        assert c["bet_up_to"] is None and c["bet_up_to_basis"] == "POINT_ESTIMATE_NO_UNCERTAINTY"
        assert c["bet_up_to_legacy"] is not None
    assert all(r.bet_up_to is None for r in recommendations_from_artifact(art))
    result = da.attribute([_fill(record["candidates"][0], 0.99)], [record])
    assert result.entries[0]["bet_up_to"] is None and result.entries[0]["inside_bet_up_to"] is None
    assert "above the bet-up-to" not in result.matches.matches[0].reason


@pytest.mark.parametrize("key", ["bet_up_to_price", "bet_up_to_price_status"])
def test_every_top_level_legacy_field_on_a_candidate_is_labelled(tmp_path, key):
    art = _artifact(tmp_path)
    assert all(key in c for c in art["candidates"])
    assert all(c["bet_up_to_price_status"] == "LEGACY_NON_ACTIONABLE" for c in art["candidates"])
    assert all(c["fair_value"]["legacy_bet_up_to_status"] == "LEGACY_NON_ACTIONABLE" for c in art["candidates"])
