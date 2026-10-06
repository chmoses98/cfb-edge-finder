"""Resumability: what a change invalidates, and what it must not.

*** THE DISTINCTION THIS FILE DEFENDS ***
A handicap survives a price move. It does not survive the starting
quarterback changing. Before materiality existed those two were the same
event -- one hash over the whole packet -- so either every price tick threw
away a football opinion, or a genuine change of game kept one. Both are wrong
and only one of them is visibly wrong.
"""

from __future__ import annotations

import json

from cfb_edge_finder.execution.evaluator import evaluate_game
from cfb_edge_finder.execution.handicap import parse_handicap
from cfb_edge_finder.execution.slate import build_slate, canonical_hash
from cfb_edge_finder.execution.state import ExecutionState, StateStore
from tests.execution_fakes import GAME_KEY, catalog_dir
from tests.test_execution_disposition import config


def packet_of(tmp_path):
    return build_slate(catalog_dir(tmp_path), config(), slate_date=None).packets[0]


def handicap():
    return parse_handicap(
        {
            "schema_version": "cfb_handicap_payload/2.0.0",
            "game_key": GAME_KEY,
            "teams": {"home": "Ole Miss", "away": "LSU"},
            "period_distributions": {
                "full_game": {
                    "home_mean": 27.0,
                    "away_mean": 24.0,
                    "home_sd": 10.5,
                    "away_sd": 10.0,
                    "correlation": 0.1,
                    "uncertainty": {"margin_points": 3.0},
                }
            },
        }
    )


def rehash(packet):
    """Recompute the packet hash the way the builder does, after a mutation."""
    clone = {k: v for k, v in packet.items() if k != "packet_hash"}
    clone["packet_hash"] = canonical_hash(clone)
    return clone


def move_quotes(packet):
    clone = json.loads(json.dumps(packet))
    for record in clone["contracts"]:
        executable = record.get("executable") or {}
        for side in ("yes", "no"):
            block = executable.get(side) or {}
            if block.get("entry") is not None:
                block["entry"] = round(min(0.99, float(block["entry"]) + 0.01), 4)
    clone["market_universe_hash"] = canonical_hash(
        sorted(
            (
                c["ticker"],
                (c["executable"].get("yes") or {}).get("entry"),
                (c["executable"].get("no") or {}).get("entry"),
            )
            for c in clone["contracts"]
        )
    )
    return rehash(clone)


def drop_a_ticker(packet):
    clone = json.loads(json.dumps(packet))
    clone["contracts"] = clone["contracts"][:-1]
    clone["counts"]["eligible"] = len(clone["contracts"])
    return rehash(clone)


def change_availability(packet):
    clone = json.loads(json.dumps(packet))
    context = clone["factual_context"]
    context["domains"]["availability"] = {
        "values": {"home_injuries": [{"athlete": "The starting quarterback"}]},
        "source": "test",
        "observed_at": "2026-09-19T11:00:00+00:00",
        "quality": "fresh",
        "detail": None,
    }
    from cfb_edge_finder.execution.context import (
        GameContext,
        context_fingerprint,
        material_fingerprint,
    )

    rebuilt = GameContext.from_dict(
        {"game_key": clone["game_key"], "domains": context["domains"], "collected_at": "x"}
    )
    context["material_context_hash"] = material_fingerprint(rebuilt)
    context["context_hash"] = context_fingerprint(rebuilt)
    return rehash(clone)


def change_efficiency(packet):
    clone = json.loads(json.dumps(packet))
    context = clone["factual_context"]
    context["domains"]["efficiency"] = {
        "values": {"home": {"offense": {"ppa": 0.41}}},
        "source": "test",
        "observed_at": "2026-09-19T11:00:00+00:00",
        "quality": "fresh",
        "detail": None,
    }
    from cfb_edge_finder.execution.context import (
        GameContext,
        context_fingerprint,
        material_fingerprint,
    )

    rebuilt = GameContext.from_dict(
        {"game_key": clone["game_key"], "domains": context["domains"], "collected_at": "x"}
    )
    context["material_context_hash"] = material_fingerprint(rebuilt)
    context["context_hash"] = context_fingerprint(rebuilt)
    return rehash(clone)


def completed(tmp_path):
    packet = packet_of(tmp_path)
    store = StateStore(tmp_path / "state")
    store.record_handicap(packet, handicap())
    store.record_evaluation(packet, evaluate_game(packet, handicap()))
    return packet, store


# ------------------------------------------------------------ no change


def test_nothing_changed_reuses_everything(tmp_path):
    packet, store = completed(tmp_path)
    state = store.reconcile_with_packet(packet)
    assert state.state == ExecutionState.EVALUATION_COMPLETE.value
    assert state.invalidation_reason is None
    assert state.has_usable_handicap


# --------------------------------------------------------- quotes only


def test_a_quote_move_keeps_the_handicap_and_invalidates_the_evaluation(tmp_path):
    packet, store = completed(tmp_path)
    state = store.reconcile_with_packet(move_quotes(packet))
    assert state.has_usable_handicap, "a price move is not a football event"
    assert state.handicap_status == "complete", "and it does not need review either"
    assert state.state == ExecutionState.STALE_DUE_TO_INPUT_CHANGE.value
    assert state.evaluated_contracts is None
    assert "quotes moved" in state.invalidation_reason


# ------------------------------------------------------ ticker set change


def test_a_ticker_set_change_flags_the_handicap_for_review(tmp_path):
    packet, store = completed(tmp_path)
    state = store.reconcile_with_packet(drop_a_ticker(packet))
    assert state.handicap_status == "complete_needs_review"
    assert state.state == ExecutionState.STALE_DUE_TO_INPUT_CHANGE.value
    assert "eligible contract set changed" in state.invalidation_reason


# --------------------------------------------------- material context


def test_a_material_factual_change_retires_the_handicap_to_history(tmp_path):
    packet, store = completed(tmp_path)
    state = store.reconcile_with_packet(change_availability(packet))
    assert state.state == ExecutionState.HANDICAP_NEEDS_REVIEW.value
    assert state.handicap_status == "complete_needs_review"
    assert "MATERIAL factual input" in state.invalidation_reason
    assert state.superseded_handicaps, (
        "the prior handicap is history, not garbage: a postmortem asking whether the news "
        "arrived after the opinion cannot answer from a payload that was overwritten"
    )
    assert state.superseded_handicaps[-1]["handicap"]["game_key"] == GAME_KEY


def test_a_game_needing_review_is_not_reported_complete(tmp_path):
    packet, store = completed(tmp_path)
    moved = change_availability(packet)
    evaluation = evaluate_game(moved, handicap())
    state = store.record_evaluation(moved, evaluation)
    assert evaluation.complete, "the arithmetic still closes"
    assert not state.is_complete, "and the game is still not finished"
    assert state.state == ExecutionState.HANDICAP_NEEDS_REVIEW.value


def test_a_fresh_handicap_clears_the_review_flag_and_keeps_the_history(tmp_path):
    packet, store = completed(tmp_path)
    moved = change_availability(packet)
    store.reconcile_with_packet(moved)
    state = store.record_handicap(moved, handicap())
    assert state.handicap_status == "complete"
    assert state.state == ExecutionState.HANDICAP_COMPLETE.value
    assert state.superseded_handicaps, "the retired payload stays on disk"


def test_an_immaterial_context_refresh_does_not_force_a_re_handicap(tmp_path):
    """An invalidation that fires on a tenth of a point of yards-per-play is
    one nobody reads."""
    packet, store = completed(tmp_path)
    state = store.reconcile_with_packet(change_efficiency(packet))
    assert state.state != ExecutionState.HANDICAP_NEEDS_REVIEW.value
    assert state.handicap_status == "complete"
    assert not state.superseded_handicaps


# ---------------------------------------------------------- resumption


def test_the_index_counts_the_review_state_separately(tmp_path):
    packet, store = completed(tmp_path)
    moved = change_availability(packet)
    index = store.write_index([moved], tmp_path / "state_index.json")
    assert index["counts"]["handicap_needs_review"] == 1
    assert index["counts"]["complete"] == 0
    assert index["games"][0]["material_context_hash"]


def test_reconciliation_is_recomputed_on_every_load_not_trusted_from_disk(tmp_path):
    """`reconcile_with_packet` READS; it does not write.

    That is deliberate and it is what makes a crashed run safe: the verdict is
    a pure function of the packet on disk, so the next process re-derives it
    rather than inheriting a stale one. Persisting happens when something is
    actually recorded -- a handicap, an evaluation, or the state index.
    """
    packet, store = completed(tmp_path)
    moved = change_availability(packet)

    # A bare reconcile leaves the file alone...
    store.reconcile_with_packet(moved)
    assert StateStore(tmp_path / "state").load(GAME_KEY).handicap_status == "complete"

    # ...and re-derives the same verdict from a cold store every time.
    for _ in range(2):
        state = StateStore(tmp_path / "state").reconcile_with_packet(moved)
        assert state.state == ExecutionState.HANDICAP_NEEDS_REVIEW.value

    # Writing the index is what makes it durable, which is what prepare-live
    # does on every run.
    store.write_index([moved], tmp_path / "state_index.json")
    reloaded = StateStore(tmp_path / "state").load(GAME_KEY)
    assert reloaded.handicap_status == "complete_needs_review"
    assert reloaded.superseded_handicaps
