"""Decision-side evidence: digest-only references and the exact-unique-match link (vendored ledger)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from cfb_edge_finder.decisions import evidence_link as de
from tests.execution_fakes import NOW
from tests.test_run_cfb_owner_rules_e2e import walk

LED = de.ledger_module()
KEY = "kalshi:v1:" + "cd" * 32
T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
REC1, REC2 = "rec_PRIVATE_ID_ONE", "rec_PRIVATE_ID_TWO"


def cand(rid, market="KXNCAAFGAME-26NOV07ARKAUB-ARK", side="YES", at=T0, as_of="2026-11-03T11:00:00+00:00"):
    return {
        "market_ticker": market,
        "side": side,
        "created_at_ts": at.timestamp(),
        "recommendation_ref": de.ref(de.DOMAIN_RECOMMENDATION, rid),
        "model_run_ref": None,
        "model_as_of": as_of,
    }


def execution(first_fill_offset=600.0):
    return {
        "schema_version": LED.SCHEMA_VERSION,
        "kind": LED.KIND_EXECUTION,
        "producer": LED.PRODUCER_ROUTER,
        "source_bet_key": KEY,
        "recorded_at": "2026-11-03T12:30:00+00:00",
        "order_id_digest": "a" * 32,
        "fill_id_digests": ["b" * 32],
        "fill_count": 1,
        "first_fill_ts": T0.timestamp() + first_fill_offset,
        "last_fill_ts": T0.timestamp() + first_fill_offset,
        "execution_action": "buy",
        "all_taker": True,
        "flags": [],
    }


def item(ex=None, market="KXNCAAFGAME-26NOV07ARKAUB-ARK", side="YES"):
    return {"execution": ex or execution(), "market_ticker": market, "side": side}


def link(cands, **kw):
    return de.link_executions([item(**kw)], cands, recorded_at="2026-11-03T13:00:00+00:00")[0]


def test_refs_are_domain_separated_opaque_digests():
    a, b = de.ref(de.DOMAIN_RECOMMENDATION, "x"), de.ref(de.DOMAIN_MODEL_RUN, "x")
    assert len(a) == 32 and a != b and a == de.ref(de.DOMAIN_RECOMMENDATION, "x")
    assert LED.DIGEST_RE.match(a)


def test_exact_unique_match_is_linked_and_validates():
    row = link([cand(REC1)])
    assert row["status"] == LED.LINK_LINKED and row["recommendation_ref"] == de.ref(de.DOMAIN_RECOMMENDATION, REC1)
    assert row["model_as_of"] == "2026-11-03T11:00:00+00:00"
    assert LED.validate_row(row) == []


def test_no_match_is_missing_never_guessed():
    assert link([cand(REC1, side="NO")])["status"] == LED.LINK_MISSING  # wrong side
    assert link([cand(REC1, market="KXNCAAFGAME-26NOV07ARKAUB-AUB")])["status"] == LED.LINK_MISSING  # wrong market
    assert link([])["status"] == LED.LINK_MISSING


def test_recommendation_created_after_the_fill_cannot_link():
    late = cand(REC1, at=datetime.fromtimestamp(T0.timestamp() + 1200, UTC))  # after the fill at +600s
    assert link([late])["status"] == LED.LINK_MISSING


def test_window_is_explicit_and_enforced():
    old = cand(REC1, at=datetime.fromtimestamp(T0.timestamp() - de.LINK_WINDOW_SECONDS - 1, UTC))
    assert link([old])["status"] == LED.LINK_MISSING
    with pytest.raises(ValueError):
        de.link_executions([item()], [cand(REC1)], recorded_at="2026-11-03T13:00:00+00:00", window_seconds=0)


def test_two_distinct_recommendations_are_ambiguous_not_nearest():
    row = link([cand(REC1), cand(REC2, at=datetime.fromtimestamp(T0.timestamp() + 300, UTC))])
    assert row["status"] == LED.LINK_AMBIGUOUS and "recommendation_ref" not in row


def test_the_same_recommendation_repeated_is_still_a_unique_link():
    row = link([cand(REC1), cand(REC1, at=datetime.fromtimestamp(T0.timestamp() + 60, UTC))])
    assert row["status"] == LED.LINK_LINKED


def test_decision_candidates_carry_no_raw_id_and_count_gaps():
    art = {
        "candidates": [
            {"recommendation_id": REC1, "market": "KXNCAAFGAME-26NOV07ARKAUB-ARK", "side": "yes"},
            {"recommendation_id": None, "market": "KXNCAAFGAME-26NOV07ARKAUB-ARK", "side": "YES"},
            {"recommendation_id": REC2, "market": None, "side": "YES"},
            {"recommendation_id": "rec3", "market": "KXNCAAFGAME-26NOV08MIAUGA-MIA", "side": "YES"},  # no model-as-of
        ]
    }
    rows, gaps = de.decision_candidates(
        art, decision_time=T0, model_as_of_by_game={"26NOV07ARKAUB": "2026-11-03T11:00:00+00:00"}
    )
    assert len(rows) == 1 and rows[0]["side"] == "YES"
    assert gaps == {"missing_recommendation_id": 1, "missing_market_or_side": 1, "missing_model_as_of": 1}
    blob = json.dumps(rows)
    assert "PRIVATE" not in blob and REC1 not in blob


def test_write_is_append_only_and_idempotent(tmp_path):
    p = tmp_path / "d.jsonl"
    rows = [cand(REC1), cand(REC2, market="KXNCAAFGAME-26NOV07ARKAUB-AUB")]
    assert de.write_decision_candidates(p, rows) == 2
    before = p.read_bytes()
    assert de.write_decision_candidates(p, rows) == 0 and p.read_bytes() == before
    assert de.read_decision_candidates(p) == [json.loads(x) for x in before.decode().splitlines()]


def test_real_run_cfb_candidates_command_writes_digest_only_decision_evidence(tmp_path):
    art = walk(tmp_path, home_mean=21.0, away_mean=31.0, handicapped_at=NOW.isoformat())
    files = list((tmp_path / "exec" / "candidates").glob("*.decision_candidates.jsonl"))
    assert art["candidates"] and len(files) == 1
    rows = de.read_decision_candidates(files[0])
    assert len(rows) == len(art["candidates"])  # every candidate has a recommendation id and a stated model-as-of
    # every row is exactly-linkable evidence: digests, a market, a side, a model-as-of
    assert rows and all(
        LED.DIGEST_RE.match(r["recommendation_ref"]) and r["model_as_of"] and r["side"] in ("YES", "NO") for r in rows
    )
    raw_ids = {str(c.get("recommendation_id")) for c in art["candidates"] if c.get("recommendation_id")}
    blob = files[0].read_text()
    assert not any(rid in blob for rid in raw_ids)
    # and the candidate artifact itself is unchanged in shape: restriction-only review fields are still present
    assert art["pit_review"]["candidates"] == len(art["candidates"])
