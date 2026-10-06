"""The shadow decision pipeline: gate order, the counted zero, and the
prospective-only rule.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from cfb_edge_finder.decision.artifact import (
    ARTIFACT_SCHEMA_VERSION,
    ArtifactResolution,
    load_artifact,
)
from cfb_edge_finder.decision.shadow import (
    ShadowDecisionState,
    ShadowRunResult,
    evaluate_shadow_candidate,
    run_shadow_pipeline,
)
from cfb_edge_finder.expression.grouping import ContractSnapshot
from cfb_edge_finder.expression.taxonomy import ContractSemantics
from cfb_edge_finder.recommendation.candidate import ResearchCandidate
from cfb_edge_finder.recommendation.eligibility import EligibilityConfig
from cfb_edge_finder.recommendation.evidence import EvidenceState
from cfb_edge_finder.recommendation.thresholds import ApprovalState
from cfb_edge_finder.schemas.common import MarketFamily, Side
from tests.test_decision_artifact import artifact_dict  # noqa: F401

NOW = datetime(2026, 9, 5, 18, 0, tzinfo=UTC)


def snapshot(**overrides) -> ContractSnapshot:
    base = dict(
        semantics=ContractSemantics(
            market_ticker="T1",
            game_id="g1",
            family=MarketFamily.MONEYLINE,
            team=Side.HOME,
            side=None,
            threshold=None,
            semantic_operator=">",
            parse_status="confirmed_live",
        ),
        timing_label="T_24H",
        captured_at=(NOW - timedelta(seconds=30)).isoformat(),
        model_probability=0.62,
        executable_yes_price=0.50,
        executable_no_price=0.52,
        market_status="active",
        fee_status="VERIFIED_CURRENT",
        fee_schedule_version="kalshi_fee_schedule_2026_07_07_taker",
        model_version="m1",
        pricing_status="model_priced",
        series_ticker="KXNCAAFGAME",
        schema_version="research_corpus_v2",
        capture_mode="PROSPECTIVE",
    )
    base.update(overrides)
    return ContractSnapshot(**base)


def candidate(**overrides) -> ResearchCandidate:
    base = dict(
        game_id="g1",
        market_ticker="T1",
        market_family="moneyline",
        timing_label="T_24H",
        team=Side.HOME,
        contract_side=None,
        threshold=None,
        executable_side=Side.YES,
        executable_price=0.50,
        estimated_fee=0.02,
        fee_adjusted_break_even_probability=0.52,
        model_probability=0.62,
        research_probability_surplus=0.10,
        projection_snapshot_id="p1",
        equivalence_group_id="e1",
        dimension_group_id="d1",
        game_group_id="g1",
        model_version="m1",
        captured_at=(NOW - timedelta(seconds=30)).isoformat(),
        market_status="active",
        fee_status="VERIFIED_CURRENT",
        fee_schedule_version="kalshi_fee_schedule_2026_07_07_taker",
        pricing_status="model_priced",
        semantics_resolved=True,
        schema_version="research_corpus_v2",
    )
    base.update(overrides)
    return ResearchCandidate(**base)


PERMISSIVE = EligibilityConfig(max_quote_age_seconds=3600)


def evaluate(**overrides):
    kwargs = dict(
        resolution=load_artifact(None),
        config=PERMISSIVE,
        evidence_state=EvidenceState.VALIDATED,
        available_settled_games=10_000,
        now=NOW,
        capture_mode="PROSPECTIVE",
    )
    cand = overrides.pop("candidate", None) or candidate()
    snap = overrides.pop("snapshot", None) or snapshot()
    kwargs.update(overrides)
    return evaluate_shadow_candidate(cand, snap, **kwargs)


def approved_resolution(tmp_path, **overrides) -> ArtifactResolution:
    payload = artifact_dict(**overrides)
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_artifact(path)


# ----------------------------------------------------- gate ordering


def test_a_retrospective_row_is_rejected_before_anything_else():
    """The prospective check runs FIRST, so a backfilled row is excluded
    even when everything else about it is perfect."""
    decision = evaluate(capture_mode="RETROSPECTIVE_BACKFILL")
    assert decision.state is ShadowDecisionState.NOT_PROSPECTIVE
    assert not decision.is_shadow_qualified


@pytest.mark.parametrize("mode", ["RETROSPECTIVE_BACKFILL", "REPLAY", "prospective", "PROSPECTIVE "])
def test_only_the_exact_prospective_marker_passes(mode):
    """Case and whitespace are not forgiven. A near-miss marker is an
    unknown provenance, not a prospective one."""
    assert evaluate(capture_mode=mode).state is ShadowDecisionState.NOT_PROSPECTIVE


def test_data_quality_is_checked_before_the_artifact():
    decision = evaluate(config=EligibilityConfig(), capture_mode="PROSPECTIVE")
    assert decision.state is ShadowDecisionState.DATA_QUALITY_FAILED
    assert decision.data_quality_failures


def test_absent_artifact_stops_a_clean_candidate():
    decision = evaluate()
    assert decision.state is ShadowDecisionState.NO_THRESHOLD_ARTIFACT
    assert not decision.data_quality_failures


def test_unapproved_artifact_stops_a_clean_candidate(tmp_path):
    resolution = approved_resolution(tmp_path, approval_state=ApprovalState.REVIEWED.value)
    assert evaluate(resolution=resolution).state is ShadowDecisionState.ARTIFACT_NOT_APPROVED


def test_malformed_artifact_stops_a_clean_candidate(tmp_path):
    path = tmp_path / "a.json"
    path.write_text("{", encoding="utf-8")
    assert evaluate(resolution=load_artifact(path)).state is ShadowDecisionState.ARTIFACT_MALFORMED


def test_an_approved_but_incompatible_rule_stops_the_candidate(tmp_path):
    resolution = approved_resolution(
        tmp_path,
        rules=[
            {
                "rule_id": "r1",
                "families": ["spread"],
                "timing_labels": ["T_24H"],
                "model_versions": ["m1"],
                "minimum_settled_games": 10,
                "values": {"k": 0.01},
            }
        ],
    )
    decision = evaluate(resolution=resolution)
    assert decision.state is ShadowDecisionState.ARTIFACT_INCOMPATIBLE
    assert "FAMILY_MISMATCH" in decision.rejection_reasons


def test_unvalidated_evidence_stops_an_otherwise_compatible_candidate(tmp_path):
    decision = evaluate(
        resolution=approved_resolution(tmp_path), evidence_state=EvidenceState.VALIDATION_PENDING
    )
    assert decision.state is ShadowDecisionState.EVIDENCE_NOT_VALIDATED


def test_the_qualified_state_is_genuinely_reachable(tmp_path):
    """If this ever stopped passing, every 'zero qualified' assertion
    elsewhere would be proving that the code is broken, not that the
    locks hold."""
    decision = evaluate(resolution=approved_resolution(tmp_path))
    assert decision.state is ShadowDecisionState.SHADOW_QUALIFIED
    assert decision.is_shadow_qualified


def test_the_sample_minimum_is_enforced_against_real_settled_games(tmp_path):
    decision = evaluate(resolution=approved_resolution(tmp_path), available_settled_games=0)
    assert decision.state is ShadowDecisionState.ARTIFACT_INCOMPATIBLE
    assert "SAMPLE_BELOW_RULE_MINIMUM" in decision.rejection_reasons


# ------------------------------------------------------- provenance


def test_the_decision_carries_its_full_diagnostic_trail():
    decision = evaluate()
    assert decision.market_ticker == "T1"
    assert decision.model_version == "m1"
    assert decision.schema_version == "research_corpus_v2"
    assert decision.capture_mode == "PROSPECTIVE"
    assert decision.threshold_artifact_status == "NO_VALIDATED_THRESHOLD_SET"
    assert decision.model_market_gap == pytest.approx(0.10)


def test_the_gap_is_none_when_either_input_is_missing():
    assert evaluate(candidate=candidate(model_probability=None)).model_market_gap is None
    assert (
        evaluate(candidate=candidate(fee_adjusted_break_even_probability=None)).model_market_gap is None
    )


def test_group_identifiers_are_carried_through():
    decision = evaluate(equivalence_group="eq-1", correlation_group="corr-1")
    assert decision.equivalence_group == "eq-1"
    assert decision.correlation_group == "corr-1"


# ------------------------------------------------- the counted zero


def test_the_qualified_count_is_counted_not_hardcoded():
    result = ShadowRunResult()
    assert result.shadow_qualified_count == 0
    result.decisions.append(evaluate())
    assert result.shadow_qualified_count == 0


def test_the_qualified_count_rises_when_a_candidate_qualifies(tmp_path):
    """Proof that a broken lock would be VISIBLE rather than masked by a
    `return 0`."""
    result = ShadowRunResult()
    result.decisions.append(evaluate(resolution=approved_resolution(tmp_path)))
    assert result.shadow_qualified_count == 1


def test_state_and_rejection_counts_are_histograms_of_the_decisions():
    result = ShadowRunResult()
    result.decisions.extend([evaluate(), evaluate(capture_mode="RETROSPECTIVE_BACKFILL")])
    counts = result.state_counts()
    assert counts["NO_THRESHOLD_ARTIFACT"] == 1
    assert counts["NOT_PROSPECTIVE"] == 1
    assert sum(result.rejection_counts().values()) >= 2


# ---------------------------------------------------- full pipeline


def test_an_empty_corpus_produces_an_empty_run():
    result = run_shadow_pipeline([], resolution=load_artifact(None), now=NOW)
    assert result.decisions == []
    assert result.shadow_qualified_count == 0
    assert result.artifact_status == "NO_VALIDATED_THRESHOLD_SET"


def test_the_pipeline_evaluates_both_executable_sides():
    result = run_shadow_pipeline([snapshot()], resolution=load_artifact(None), now=NOW)
    assert {d.side for d in result.decisions} == {"yes", "no"}


def test_the_pipeline_rejects_a_retrospective_snapshot_wholesale():
    result = run_shadow_pipeline(
        [snapshot(capture_mode="RETROSPECTIVE_BACKFILL")], resolution=load_artifact(None), now=NOW
    )
    assert result.decisions
    assert all(d.state is ShadowDecisionState.NOT_PROSPECTIVE for d in result.decisions)


def test_a_snapshot_with_unknown_capture_mode_is_not_treated_as_prospective():
    """`None` means the row could not say. It must not inherit the
    benefit of the doubt."""
    result = run_shadow_pipeline([snapshot(capture_mode=None)], resolution=load_artifact(None), now=NOW)
    assert all(not d.is_shadow_qualified for d in result.decisions)


def test_the_pipeline_derives_evidence_state_rather_than_trusting_a_caller():
    """A caller passing an enormous settled count still cannot conjure
    VALIDATED, because `assess_readiness` never returns it."""
    result = run_shadow_pipeline(
        [snapshot()], resolution=load_artifact(None), available_settled_games=10**7, now=NOW
    )
    assert all(d.evidence_state != "VALIDATED" for d in result.decisions)


def test_the_pipeline_attaches_correlation_groups():
    result = run_shadow_pipeline([snapshot()], resolution=load_artifact(None), now=NOW)
    assert all(d.correlation_group == "g1|MARGIN" for d in result.decisions)


def test_the_pipeline_is_deterministic():
    a = run_shadow_pipeline([snapshot()], resolution=load_artifact(None), now=NOW)
    b = run_shadow_pipeline([snapshot()], resolution=load_artifact(None), now=NOW)
    assert a.state_counts() == b.state_counts()
    assert [d.market_ticker for d in a.decisions] == [d.market_ticker for d in b.decisions]


def test_the_artifact_schema_version_is_stable():
    """A silent bump would make every existing artifact unparseable."""
    assert ARTIFACT_SCHEMA_VERSION == "shadow_threshold_artifact_v1"
