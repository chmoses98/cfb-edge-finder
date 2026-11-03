"""RUN CFB wiring of the 2026-11-02 postmortem fixes (PR #249, owner comments 6588421337 / 6588474861).

Everything here goes through the real entry point -- ``python -m cfb_edge_finder.execution`` ->
``prepare-live`` -> ``evaluate`` -> ``candidates`` / ``report`` -- or through the exact function that
command calls. The properties under test:

* the review is RESTRICTION-ONLY: same candidates, same order, legacy fields byte-identical;
* stale quote / after-kickoff / missing projection time / missing injuries are NAMED blocks;
* fee-inclusive break-even uses the per-order fee; bet-up-to is None (with a reason) when unsupported;
* 1U = $75 and the flags fire strictly above $375/game and $1,125/day, with no caps.
"""

from __future__ import annotations

import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest

from cfb_edge_finder.decision.exposure_aggregation import STANDARD_UNIT_DOLLARS, Position
from cfb_edge_finder.execution.cli import main
from cfb_edge_finder.execution.evaluator import evaluate_game
from cfb_edge_finder.execution.pit_review import (
    annotate_candidate_artifact,
    cfb_policy,
    context_from_slate,
    exposure_review,
    load_ledger_positions,
    review_candidate,
)
from cfb_edge_finder.execution.report import build_candidate_artifact
from cfb_edge_finder.execution.slate import build_slate
from cfb_edge_finder.expression.fair_value import ReliabilityBin
from tests.execution_fakes import CAPTURED_AT, GAME_KEY, NOW, catalog_dir
from tests.test_execution_candidates import handicap
from tests.test_execution_disposition import config

KICKOFF = "2026-09-19T23:30:00Z"


def run(args) -> int:
    return main(args)


def handicap_with_time(handicapped_at):
    payload = handicap().as_dict()
    payload["handicapped_at"] = handicapped_at
    payload["packet_hash"] = None
    from cfb_edge_finder.execution.handicap import parse_handicap

    return parse_handicap(payload)


def walk(tmp_path: Path, *, as_of: str, extra=()):
    """prepare-live -> evaluate -> candidates through the real CLI. Returns the candidate artifact."""
    catalog = catalog_dir(tmp_path)
    out = tmp_path / "exec"
    assert run(["prepare-live", "--catalog-dir", str(catalog), "--out-dir", str(out),
                "--as-of", NOW.isoformat(), "--date", "all"]) == 0
    manifest = json.loads((out / "shard_manifest.json").read_text())
    shard = manifest["shards"][0]["shard"]
    doc = json.loads((out / "shards" / f"{shard}.json").read_text())
    pk = doc["games"][0]
    hc = tmp_path / "hc.json"
    hc.write_text(json.dumps([{
        "schema_version": "cfb_handicap_payload/2.0.0",
        "game_key": pk["game_key"], "packet_hash": pk["packet_hash"],
        "teams": {"home": "Ole Miss", "away": "LSU"}, "confidence": "test",
        "thesis": "t", "opposing_case": "o",
        "period_distributions": {"full_game": {
            "home_mean": 30.0, "away_mean": 21.0, "home_sd": 10.5, "away_sd": 10.0,
            "correlation": 0.1, "uncertainty": {"margin_points": 3.0, "total_points": 5.0}}},
        "explicit_probabilities": {},
    }]))
    assert run(["evaluate", "--shard", shard, "--out-dir", str(out), "--handicaps", str(hc),
                "--min-edge", "0.02"]) == 0
    code = run(["candidates", "--shard", shard, "--out-dir", str(out), "--min-edge", "0.02",
                "--no-decision-record", "--as-of", as_of, *extra])
    assert code == 0
    art = json.loads((out / "candidates" / f"{shard}.candidates.json").read_text())
    return art, out, shard


# ------------------------------------------------------- the real entry point


def test_candidates_command_attaches_the_review_and_blocks_missing_evidence(tmp_path, capsys):
    art, _out, _shard = walk(tmp_path, as_of=(NOW + timedelta(minutes=5)).isoformat())
    assert art["candidates"], "the fixture handicap is deliberately far from the book"
    summary = art["pit_review"]
    assert summary["restriction_only"] is True
    assert summary["candidates"] == len(art["candidates"])
    for cand in art["candidates"]:
        review = cand["pit_review"]
        # The handicap has no handicapped_at and the packet has no availability observation: both are
        # named blocks, never silent passes. The quote (15 min old, 120 min budget) and kickoff are fine.
        assert review["actionable"] is False
        assert "MISSING_PROJECTION_AS_OF" in review["blocking_guards"]
        assert "MISSING_AVAILABILITY" in review["blocking_guards"]
        assert "QUOTE_STALE" not in review["blocking_guards"]
        assert "AFTER_KICKOFF_CUTOFF" not in review["blocking_guards"]
        assert review["notes"] == ["QB_STATUS_NOT_TRACKED"]
        assert cand["fee_inclusive"]["break_even_probability"] > cand["kalshi_executable_price"]
    out_text = capsys.readouterr().out
    assert "POINT-IN-TIME REVIEW" in out_text


def test_stale_quote_and_after_kickoff_are_named_blocks_through_the_cli(tmp_path):
    late = (NOW + timedelta(hours=11, minutes=25)).isoformat()  # 23:25Z: 5 min before kickoff, capture 11h40m old
    art, *_ = walk(tmp_path, as_of=late)
    for cand in art["candidates"]:
        blocked = cand["pit_review"]["blocking_guards"]
        assert "QUOTE_STALE" in blocked
        assert "AFTER_KICKOFF_CUTOFF" in blocked
        expected_age = int((NOW + timedelta(hours=11, minutes=25) - CAPTURED_AT).total_seconds())
        assert cand["pit_review"]["quote_age_seconds"] == expected_age
    assert art["pit_review"]["actionable"] == 0
    assert art["pit_review"]["blocked_by"]["QUOTE_STALE"] == len(art["candidates"])


def test_the_review_is_restriction_only_same_candidates_same_order_legacy_fields_unchanged(tmp_path):
    art, out, shard = walk(tmp_path, as_of=(NOW + timedelta(minutes=5)).isoformat())
    # Rebuild the SAME artifact without the review, from the same evaluations.
    catalog = catalog_dir(tmp_path / "again")
    game = build_slate(catalog, config(), slate_date=None).packets[0]
    before = build_candidate_artifact(
        "early", [evaluate_game(game, handicap(), min_net_edge=0.02)], {GAME_KEY: game},
        {GAME_KEY: handicap()}, min_net_edge=0.02)
    assert [c["market"] for c in before["candidates"]] == [c["market"] for c in art["candidates"]]
    for old, new in zip(before["candidates"], art["candidates"], strict=True):
        for key, value in old.items():
            if key in ("recommendation_id", "related_alternatives"):
                continue  # recommendation_id depends on batch/shard labels; alternatives are not touched
            assert new[key] == value, f"{old['market']}: legacy field {key!r} was altered"
        # additive only
        assert {"pit_review", "fee_inclusive", "fair_value", "exposure_context"} <= set(new) - set(old)


def test_report_command_annotates_final_bets_too(tmp_path):
    art, out, shard = walk(tmp_path, as_of=(NOW + timedelta(minutes=5)).isoformat())
    assert run(["report", "--shard", shard, "--out-dir", str(out), "--min-edge", "0.02",
                "--as-of", (NOW + timedelta(minutes=5)).isoformat()]) == 0
    report = json.loads((out / "reports" / f"{shard}.json").read_text())
    assert report["final_bets"] and all("pit_review" in b for b in report["final_bets"])
    text = (out / "reports" / f"{shard}.txt").read_text()
    assert "POINT-IN-TIME: NOT ACTIONABLE" in text
    assert "conservative bet up to" in text and "bet up to (LEGACY, NON-ACTIONABLE, do not quote)" in text


# ------------------------------------------------------------ pass + unit cases


def packet_with_fresh_availability():
    catalog = catalog_dir(Path(__import__("tempfile").mkdtemp()))
    pk = copy.deepcopy(build_slate(catalog, config(), slate_date=None).packets[0])
    pk["factual_context"]["domains"] = {
        "availability": {"quality": "fresh", "observed_at": (NOW - timedelta(hours=1)).isoformat()}
    }
    return pk


def make_ctx(pk, *, decision_time=NOW + timedelta(minutes=5), handicapped_at=None, **kw):
    hc = handicap_with_time((NOW - timedelta(hours=2)).isoformat() if handicapped_at is None else handicapped_at)
    slate = {"source": {"captured_at": CAPTURED_AT.isoformat()}, "config": {"max_capture_age_minutes": 120.0}}
    return context_from_slate(slate=slate, packets={GAME_KEY: pk}, handicaps={GAME_KEY: hc},
                              decision_time=decision_time, min_net_edge=0.02, **kw), hc


def first_candidate(pk, hc):
    art = build_candidate_artifact("early", [evaluate_game(pk, hc, min_net_edge=0.02)], {GAME_KEY: pk},
                                   {GAME_KEY: hc}, min_net_edge=0.02)
    return art, art["candidates"][0]


def test_every_input_fresh_is_actionable_and_conservative_ceiling_is_below_legacy():
    pk = packet_with_fresh_availability()
    ctx, hc = make_ctx(pk)
    art, cand = first_candidate(pk, hc)
    out = review_candidate(cand, ctx)
    assert out["pit_review"]["actionable"] is True
    fair = out["fair_value"]
    # stated region exists -> a ceiling from the LOWER bound, never above the legacy base-case ceiling
    assert fair["conservative_bet_up_to_basis"] == "HANDICAP_STATED_LOWER_BOUND_UNCALIBRATED"
    assert fair["conservative_bet_up_to"] is not None
    assert fair["conservative_bet_up_to"] <= cand["bet_up_to_price"] + 1e-9
    assert fair["legacy_exceeds_conservative"] is True
    # nothing is calibrated in this repository -> never "qualified"
    assert fair["calibration_status"] == "UNCALIBRATED_NO_RELIABILITY_TABLE"
    assert fair["qualified_positive_ev"] is False


def test_stale_projection_and_stale_availability_and_future_inputs_block():
    pk = packet_with_fresh_availability()
    ctx, hc = make_ctx(pk, handicapped_at=(NOW - timedelta(hours=30)).isoformat())
    _, cand = first_candidate(pk, hc)
    assert "PROJECTION_STALE" in review_candidate(cand, ctx)["pit_review"]["blocking_guards"]
    ctx2, hc2 = make_ctx(pk, handicapped_at=(NOW + timedelta(hours=1)).isoformat())
    assert "PROJECTION_FROM_FUTURE" in review_candidate(cand, ctx2)["pit_review"]["blocking_guards"]
    pk_stale = copy.deepcopy(pk)
    pk_stale["factual_context"]["domains"]["availability"]["observed_at"] = (NOW - timedelta(hours=40)).isoformat()
    ctx3, _ = make_ctx(pk_stale)
    assert "AVAILABILITY_STALE" in review_candidate(cand, ctx3)["pit_review"]["blocking_guards"]


def test_naive_timestamp_is_missing_not_guessed_utc():
    pk = packet_with_fresh_availability()
    ctx, hc = make_ctx(pk, handicapped_at="2026-09-19T10:00:00")  # no tz
    _, cand = first_candidate(pk, hc)
    assert "MISSING_PROJECTION_AS_OF" in review_candidate(cand, ctx)["pit_review"]["blocking_guards"]


def test_no_stated_uncertainty_region_gives_no_ceiling_with_a_reason():
    pk = packet_with_fresh_availability()
    ctx, hc = make_ctx(pk)
    _, cand = first_candidate(pk, hc)
    point_only = dict(cand, scenarios_tested=1, fair_probability_range=[0.68, 0.68])
    fair = review_candidate(point_only, ctx)["fair_value"]
    assert fair["conservative_bet_up_to"] is None
    assert fair["conservative_bet_up_to_missing_reason"] == "POINT_ESTIMATE_NO_UNCERTAINTY"
    assert fair["uncertainty_tier"] == "POINT_ESTIMATE_NO_UNCERTAINTY"
    assert fair["uncertainty_region_stated"] is False
    assert fair["qualified_positive_ev"] is False
    no_prob = dict(cand, scenarios_tested=None, fair_probability=None, fair_probability_range=[None, None])
    assert review_candidate(no_prob, ctx)["fair_value"]["conservative_bet_up_to_missing_reason"] == "NO_PROBABILITY"


def test_non_executable_price_blocks():
    pk = packet_with_fresh_availability()
    ctx, hc = make_ctx(pk)
    _, cand = first_candidate(pk, hc)
    out = review_candidate(dict(cand, kalshi_executable_price=None), ctx)
    assert "NON_EXECUTABLE_PRICE" in out["pit_review"]["blocking_guards"]
    assert out["fee_inclusive"]["break_even_probability"] is None  # unknown fee is never free


def test_calibrated_path_uses_reliability_bins_and_can_qualify():
    pk = packet_with_fresh_availability()
    bins = [ReliabilityBin(0.0, 1.0, 400, 280)]  # 70% realised hit rate over 400 settled outcomes
    ctx, hc = make_ctx(pk, reliability_bins=bins)
    _, cand = first_candidate(pk, hc)
    fair = review_candidate(cand, ctx)["fair_value"]
    assert fair["calibration_status"] == "CALIBRATED"
    assert fair["conservative_bet_up_to_basis"] == "CALIBRATED_LOWER_BOUND"
    assert fair["conservative_bet_up_to"] is not None
    assert fair["qualified_positive_ev"] is True
    sparse = [ReliabilityBin(0.0, 1.0, 5, 4)]
    ctx2, _ = make_ctx(pk, reliability_bins=sparse)
    assert review_candidate(cand, ctx2)["fair_value"]["calibration_status"] == "UNCALIBRATED_SPARSE_BIN"


def test_fee_inclusive_break_even_uses_the_per_order_fee():
    pk = packet_with_fresh_availability()
    ctx, hc = make_ctx(pk)
    _, cand = first_candidate(pk, hc)
    fee = review_candidate(cand, ctx)["fee_inclusive"]
    assert fee["reference_order_dollars"] == 75.0
    # break-even = price + order_fee/contracts (rounded up once per order, so >= the unrounded per-contract fee)
    assert fee["break_even_probability"] == pytest.approx(
        cand["kalshi_executable_price"] + fee["order_fee_dollars"] / fee["reference_contracts"], abs=1e-6)
    assert fee["break_even_probability"] >= cand["fee_adjusted_breakeven"] - 1e-6


# ------------------------------------------------------------------- exposure


def _pos(side_ticker, dollars, day="2026-11-03"):
    return Position(side_ticker, "YES", dollars, day)


def test_unit_is_seventy_five_dollars_and_flags_are_strictly_above_375_and_1125():
    assert STANDARD_UNIT_DOLLARS == 75.0
    pk = packet_with_fresh_availability()
    ctx, _ = make_ctx(pk, existing_positions=[
        _pos("KXNCAAFSPREAD-26NOV03BALLMASS-MASS3", 375.0)])
    rep = exposure_review(ctx, [])
    assert rep["flag_thresholds_dollars"] == {"per_game": 375.0, "per_day": 1125.0}
    assert rep["flags_already_held"] == []  # exactly $375 is NOT above the line
    ctx2, _ = make_ctx(pk, existing_positions=[_pos("KXNCAAFSPREAD-26NOV03BALLMASS-MASS3", 375.01)])
    assert any(f.endswith("GAME_GROSS_OVER_5U") for f in exposure_review(ctx2, [])["flags_already_held"])


def test_daily_flag_aggregates_games_and_nothing_is_capped_or_removed():
    pk = packet_with_fresh_availability()
    positions = [
        _pos("KXNCAAFSPREAD-26NOV03BALLMASS-MASS3", 300.0),
        _pos("KXNCAAFGAME-26NOV03BUFFMOH-BUFF", 300.0),
        _pos("KXNCAAFTOTAL-26NOV03CMUEMU-55", 300.0),
        _pos("KXNCAAFTOTAL-26NOV03XXXYYY-55", 226.0),
    ]
    ctx, hc = make_ctx(pk, existing_positions=positions)
    rep = exposure_review(ctx, [])
    assert any("DAY_GROSS_OVER_15U" in f for f in rep["flags_already_held"])  # 1126 > 1125
    # annotating an artifact keeps every candidate: flags, never caps
    art, _ = first_candidate(pk, hc)
    n = len(art["candidates"])
    annotate_candidate_artifact(art, ctx)
    assert len(art["candidates"]) == n and art["pit_review"]["exposure"]["policy"] == "FLAGS_ONLY_NO_CAPS"


def test_proposal_without_stakes_is_not_assessed_and_with_stakes_reports_new_flags():
    pk = packet_with_fresh_availability()
    ctx, hc = make_ctx(pk)
    art, cand = first_candidate(pk, hc)
    assert exposure_review(ctx, art["candidates"])["proposal_assessed"] is False
    # One $450 bet is clamped to the 5U per-bet ceiling ($375), which is not over the >$375 game flag by itself.
    ctx2, _ = make_ctx(pk, proposed_stakes={cand["market"]: 450.0})
    rep = exposure_review(ctx2, art["candidates"])
    assert rep["proposal_assessed"] is True
    assert not rep["flags_introduced_by_proposal"]
    # Two bets on one game, each within the per-bet ceiling, together exceed $375 -> a FLAG (never a cap).
    others = [c for c in art["candidates"] if c["market"] != cand["market"]]
    if others:
        ctx3, _ = make_ctx(pk, proposed_stakes={cand["market"]: 300.0, others[0]["market"]: 300.0})
        rep3 = exposure_review(ctx3, art["candidates"])
        assert rep3["flags_introduced_by_proposal"], "$600 across one game must raise the >$375 game flag"


def test_ledger_loader_reads_the_committed_export_shape(tmp_path):
    f = tmp_path / "w.json"
    f.write_text(json.dumps({"items": [
        {"kalshi_ticker": "KXNCAAFSPREAD-26NOV03BALLMASS-MASS3", "selection": "NO", "stake": 100.0,
         "placed_at": "2026-11-02T18:00:00Z"},
        {"kalshi_ticker": None, "selection": "YES", "stake": 5.0},
        {"kalshi_ticker": "X", "selection": "MAYBE", "stake": 5.0},
    ]}))
    pos = load_ledger_positions(f)
    assert len(pos) == 1 and pos[0].side == "NO" and pos[0].stake_dollars == 100.0
    assert load_ledger_positions(tmp_path / "missing.json") == []


def test_cfb_policy_reuses_existing_budgets_and_does_not_require_qb():
    pol = cfb_policy()
    assert pol.require_qb_confirmation is False
    assert pol.max_quote_age == timedelta(minutes=120.0)  # disposition.DEFAULT_MAX_CAPTURE_AGE_MINUTES
    assert pol.max_availability_age == timedelta(hours=18)  # context.FRESHNESS_BUDGET_SECONDS['availability']


def test_zero_width_range_is_a_point_estimate_even_when_many_scenarios_ran():
    """low == high is the point estimate twice, not a validated interval: flagged, labelled, and no ceiling."""
    pk = packet_with_fresh_availability()
    ctx, hc = make_ctx(pk)
    _, cand = first_candidate(pk, hc)
    flat = dict(cand, scenarios_tested=7, fair_probability_range=[0.61, 0.61])
    fair = review_candidate(flat, ctx)["fair_value"]
    assert fair["zero_width_range"] is True
    assert fair["uncertainty_tier"] == "POINT_ESTIMATE_NO_UNCERTAINTY"
    assert "POINT ESTIMATE" in fair["uncertainty_tier_note"]
    assert fair["conservative_bet_up_to"] is None and fair["conservative_bet_up_to_basis"] is None
    assert fair["conservative_bet_up_to_missing_reason"] == "POINT_ESTIMATE_NO_UNCERTAINTY"
    assert fair["within_conservative_bet_up_to"] is None
    assert fair["qualified_positive_ev"] is False
    assert fair["legacy_bet_up_to_status"] == "LEGACY_NON_ACTIONABLE"


def test_a_real_region_is_tiered_as_an_uncalibrated_stated_region():
    pk = packet_with_fresh_availability()
    ctx, hc = make_ctx(pk)
    _, cand = first_candidate(pk, hc)
    fair = review_candidate(cand, ctx)["fair_value"]
    assert fair["zero_width_range"] is False
    assert fair["uncertainty_tier"] == "STATED_UNCERTAINTY_REGION_UNCALIBRATED"
    assert fair["uncertainty_tier_note"] is None
