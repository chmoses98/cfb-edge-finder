"""RUN CFB validation: the persisted decision record and the accounting link carry the DEFENSIBLE bet-up-to, never the legacy figure.

`pit_review` stamps every candidate with `fair_value.conservative_bet_up_to` (highest whole cent at which the handicap's STATED
lower bound clears fee-inclusive break-even + the required edge; `None` with a reason when the handicap stated no uncertainty
region) and says it "replaces bet_up_to_price as the price ceiling to quote". The decision record and the recommendation link still
copied the legacy `bet_up_to_price` (a base-case point probability, no stated uncertainty), so a candidate with NO defensible
ceiling was persisted with an invented one and later judged "executed above bet-up-to" against it.
"""

from __future__ import annotations

import copy
import json

from cfb_edge_finder.accounting.recommendation_link import recommendations_from_artifact
from cfb_edge_finder.decisions.record import build_decision_record
from cfb_edge_finder.execution.cli import main
from tests.execution_fakes import NOW
from tests.test_decision_records import candidates, prepared

LEGACY = 0.617849


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


def test_no_stated_uncertainty_means_the_record_has_no_bet_up_to_and_says_why(tmp_path):
    art = _with_fair_value(_artifact(tmp_path), conservative_bet_up_to=None,
                           conservative_bet_up_to_basis=None, conservative_bet_up_to_missing_reason="NO_STATED_UNCERTAINTY_REGION")
    record = _record(art)
    assert record["candidates"]
    for cand in record["candidates"]:
        assert cand["bet_up_to"] is None, "an invented ceiling must not be persisted as the recommendation's bet-up-to"
        assert cand["bet_up_to_basis"] == "NO_STATED_UNCERTAINTY_REGION"
        assert cand["bet_up_to_legacy"] == LEGACY, "the legacy figure stays on the record, labelled, for history"


def test_a_defensible_ceiling_is_what_the_record_carries(tmp_path):
    art = _with_fair_value(_artifact(tmp_path), conservative_bet_up_to=0.53,
                           conservative_bet_up_to_basis="HANDICAP_STATED_LOWER_BOUND_UNCALIBRATED", conservative_bet_up_to_missing_reason=None)
    for cand in _record(art)["candidates"]:
        assert cand["bet_up_to"] == 0.53 and cand["bet_up_to_legacy"] == LEGACY
        assert cand["bet_up_to_basis"] == "HANDICAP_STATED_LOWER_BOUND_UNCALIBRATED"


def test_an_artifact_that_predates_the_fair_value_block_keeps_its_legacy_figure(tmp_path):
    art = _artifact(tmp_path)
    for cand in art["candidates"]:
        cand["bet_up_to_price"] = LEGACY
        cand.pop("fair_value", None)
    for cand in _record(art)["candidates"]:
        assert cand["bet_up_to"] == LEGACY and cand["bet_up_to_basis"] == "LEGACY_BASE_CASE_NO_FAIR_VALUE_BLOCK"


def test_the_accounting_link_reads_the_same_defensible_ceiling(tmp_path):
    none_art = _with_fair_value(_artifact(tmp_path), conservative_bet_up_to=None, conservative_bet_up_to_missing_reason="NO_STATED_UNCERTAINTY_REGION")
    assert all(r.bet_up_to is None for r in recommendations_from_artifact(none_art))
    some_art = _with_fair_value(_artifact(tmp_path / "b"), conservative_bet_up_to=0.53)
    assert all(r.bet_up_to == 0.53 for r in recommendations_from_artifact(some_art))
