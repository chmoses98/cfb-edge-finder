"""The threshold artifact: schema, strict parsing, and the fail-closed
ladder.

These tests build artifacts inside the tests. The repository ships none,
and that absence is itself asserted below.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from cfb_edge_finder.decision.artifact import (
    ARTIFACT_SCHEMA_VERSION,
    NO_VALIDATED_THRESHOLD_SET,
    THRESHOLD_ARTIFACT_MALFORMED,
    THRESHOLD_ARTIFACT_NOT_APPROVED,
    ArtifactProblem,
    RuleIncompatibility,
    ThresholdRule,
    load_artifact,
    parse_artifact,
)
from cfb_edge_finder.recommendation.thresholds import ApprovalState


def evidence_dict(**overrides) -> dict:
    base = {
        "validation_corpus_identifier": "research-data@deadbeef",
        "validation_window_start": "2026-08-01T00:00:00+00:00",
        "validation_window_end": "2026-12-01T00:00:00+00:00",
        "settled_game_count": 400,
        "settled_observation_count": 9000,
        "clv_observation_count": 8000,
        "calibration_observation_count": 9000,
        "research_methodology_version": "methodology_v1",
        "prospective_only": True,
    }
    base.update(overrides)
    return base


def rule_dict(**overrides) -> dict:
    base = {
        "rule_id": "r1",
        "families": ["moneyline"],
        "timing_labels": ["T_24H"],
        "model_versions": ["m1"],
        "minimum_settled_games": 200,
        "values": {"some_opaque_key": 0.04},
    }
    base.update(overrides)
    return base


def artifact_dict(**overrides) -> dict:
    base = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_version": "v1",
        "created_at": "2026-12-02T00:00:00+00:00",
        "approval_state": ApprovalState.APPROVED_FOR_SHADOW.value,
        "approved_by": "a-human",
        "approved_at": "2026-12-02T01:00:00+00:00",
        "evidence": evidence_dict(),
        "rules": [rule_dict()],
    }
    base.update(overrides)
    return base


# ------------------------------------------- the repository ships none


def test_no_threshold_artifact_is_committed_anywhere():
    """The strongest safety property in this module. If a file matching
    the artifact schema ever appears in the repository, this fails."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    offenders = []
    for path in repo.rglob("*.json"):
        if ".git" in path.parts or "node_modules" in path.parts:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        if isinstance(raw, dict) and raw.get("schema_version") == ARTIFACT_SCHEMA_VERSION:
            offenders.append(str(path.relative_to(repo)))
    assert offenders == [], f"a threshold artifact is committed at: {offenders}"


def test_module_declares_no_threshold_values():
    """No shipped numbers to become defaults."""
    import cfb_edge_finder.decision.artifact as artifact

    numeric = {
        name: value
        for name, value in vars(artifact).items()
        if isinstance(value, (int, float)) and not isinstance(value, bool) and not name.startswith("__")
    }
    assert numeric == {}


# --------------------------------------------- the fail-closed ladder


def test_no_path_is_the_absence_state_not_an_error():
    resolution = load_artifact(None)
    assert resolution.status == NO_VALIDATED_THRESHOLD_SET
    assert resolution.artifact is None
    assert not resolution.usable_for_shadow


def test_missing_file_is_the_absence_state(tmp_path):
    resolution = load_artifact(tmp_path / "nope.json")
    assert resolution.status == NO_VALIDATED_THRESHOLD_SET
    assert resolution.artifact is None


def test_unparseable_json_is_malformed_not_absent(tmp_path):
    """A corrupt artifact must NOT degrade into the same state as no
    artifact -- 'absent' is expected and quiet, 'malformed' means
    something is wrong that a person should see."""
    path = tmp_path / "a.json"
    path.write_text("{not json", encoding="utf-8")
    resolution = load_artifact(path)
    assert resolution.status == THRESHOLD_ARTIFACT_MALFORMED
    assert resolution.artifact is None


def test_unknown_schema_version_is_refused_outright(tmp_path):
    path = tmp_path / "a.json"
    path.write_text(json.dumps(artifact_dict(schema_version="from_the_future_v9")), encoding="utf-8")
    resolution = load_artifact(path)
    assert resolution.status == THRESHOLD_ARTIFACT_MALFORMED
    assert ArtifactProblem.SCHEMA_VERSION_UNKNOWN in resolution.problems


@pytest.mark.parametrize("state", [ApprovalState.DRAFT_RESEARCH, ApprovalState.REVIEWED])
def test_valid_but_unapproved_artifact_is_refused_and_withheld(tmp_path, state):
    """The rules must be UNREACHABLE, not merely unused: the resolution
    does not carry the artifact, so no caller can read a threshold off a
    draft by accident."""
    path = tmp_path / "a.json"
    path.write_text(json.dumps(artifact_dict(approval_state=state.value)), encoding="utf-8")
    resolution = load_artifact(path)
    assert resolution.status == THRESHOLD_ARTIFACT_NOT_APPROVED
    assert resolution.artifact is None
    assert not resolution.usable_for_shadow


def test_an_approved_complete_artifact_does_resolve(tmp_path):
    """The path is real code, not a stub that always refuses. If this
    ever stopped passing, every refusal test above would be proving
    nothing."""
    path = tmp_path / "a.json"
    path.write_text(json.dumps(artifact_dict()), encoding="utf-8")
    resolution = load_artifact(path)
    assert resolution.usable_for_shadow
    assert resolution.artifact is not None
    assert resolution.artifact.is_shadow_eligible
    assert not resolution.artifact.is_live_eligible


def test_live_approval_is_a_strictly_stronger_state(tmp_path):
    path = tmp_path / "a.json"
    path.write_text(
        json.dumps(artifact_dict(approval_state=ApprovalState.APPROVED_FOR_LIVE.value)), encoding="utf-8"
    )
    artifact = load_artifact(path).artifact
    assert artifact.is_live_eligible and artifact.is_shadow_eligible


# ------------------------------------------------- evidence integrity


def test_retrospective_evidence_is_rejected_even_when_approved(tmp_path):
    """An approved artifact built on backfilled data is still refused.
    Approval cannot launder the provenance."""
    path = tmp_path / "a.json"
    path.write_text(
        json.dumps(artifact_dict(evidence=evidence_dict(prospective_only=False))), encoding="utf-8"
    )
    resolution = load_artifact(path)
    assert resolution.status == THRESHOLD_ARTIFACT_MALFORMED
    assert ArtifactProblem.NOT_PROSPECTIVE_ONLY in resolution.problems


@pytest.mark.parametrize(
    "override",
    [
        {"settled_game_count": 0},
        {"settled_observation_count": 0},
        {"validation_corpus_identifier": ""},
        {"research_methodology_version": ""},
        {"validation_window_end": "2026-07-01T00:00:00+00:00"},
    ],
)
def test_incomplete_evidence_is_refused(tmp_path, override):
    path = tmp_path / "a.json"
    path.write_text(json.dumps(artifact_dict(evidence=evidence_dict(**override))), encoding="utf-8")
    assert load_artifact(path).status == THRESHOLD_ARTIFACT_MALFORMED


def test_an_artifact_with_no_rules_is_refused(tmp_path):
    path = tmp_path / "a.json"
    path.write_text(json.dumps(artifact_dict(rules=[])), encoding="utf-8")
    resolution = load_artifact(path)
    assert resolution.status == THRESHOLD_ARTIFACT_MALFORMED
    assert ArtifactProblem.NO_RULES in resolution.problems


@pytest.mark.parametrize("axis", ["families", "timing_labels", "model_versions"])
def test_an_unscoped_rule_is_refused(tmp_path, axis):
    """An empty scope axis would make the rule apply to populations it
    was never validated on."""
    path = tmp_path / "a.json"
    path.write_text(json.dumps(artifact_dict(rules=[rule_dict(**{axis: []})])), encoding="utf-8")
    resolution = load_artifact(path)
    assert resolution.status == THRESHOLD_ARTIFACT_MALFORMED
    assert ArtifactProblem.RULE_SCOPE_EMPTY in resolution.problems


def test_invalid_approval_state_is_refused():
    _, problems = parse_artifact(artifact_dict(approval_state="APPROVED_BY_ME"))
    assert problems == [ArtifactProblem.INVALID_APPROVAL_STATE]


def test_non_dict_input_is_refused():
    artifact, problems = parse_artifact(["not", "a", "dict"])
    assert artifact is None and problems == [ArtifactProblem.INVALID_FIELD_TYPE]


def test_non_numeric_rule_values_are_refused():
    _, problems = parse_artifact(artifact_dict(rules=[rule_dict(values={"k": "high"})]))
    assert ArtifactProblem.INVALID_FIELD_TYPE in problems


# ------------------------------------------------ rule compatibility


def rule(**overrides) -> ThresholdRule:
    base = dict(
        rule_id="r1",
        families=frozenset({"moneyline"}),
        timing_labels=frozenset({"T_24H"}),
        model_versions=frozenset({"m1"}),
        minimum_settled_games=200,
        values={"k": 0.04},
    )
    base.update(overrides)
    return ThresholdRule(**base)


def compatible_kwargs(**overrides) -> dict:
    base = dict(
        family="moneyline",
        timing_label="T_24H",
        model_version="m1",
        side="yes",
        executable_price=0.5,
        model_market_gap=0.06,
        available_settled_games=500,
    )
    base.update(overrides)
    return base


def test_a_fully_matching_candidate_has_no_incompatibilities():
    assert rule().incompatibilities(**compatible_kwargs()) == []


@pytest.mark.parametrize(
    "override,expected",
    [
        ({"model_version": "m2"}, RuleIncompatibility.MODEL_VERSION_MISMATCH),
        ({"model_version": None}, RuleIncompatibility.MODEL_VERSION_MISMATCH),
        ({"family": "spread"}, RuleIncompatibility.FAMILY_MISMATCH),
        ({"family": None}, RuleIncompatibility.FAMILY_MISMATCH),
        ({"timing_label": "T_30"}, RuleIncompatibility.TIMING_MISMATCH),
        ({"timing_label": None}, RuleIncompatibility.TIMING_MISMATCH),
        ({"available_settled_games": 199}, RuleIncompatibility.SAMPLE_BELOW_RULE_MINIMUM),
    ],
)
def test_each_axis_mismatch_is_reported(override, expected):
    assert expected in rule().incompatibilities(**compatible_kwargs(**override))


def test_none_on_an_axis_is_a_mismatch_never_a_wildcard():
    """A candidate that cannot say which model priced it must not inherit
    evidence gathered under a different one."""
    problems = rule().incompatibilities(
        **compatible_kwargs(model_version=None, family=None, timing_label=None)
    )
    assert {
        RuleIncompatibility.MODEL_VERSION_MISMATCH,
        RuleIncompatibility.FAMILY_MISMATCH,
        RuleIncompatibility.TIMING_MISMATCH,
    } <= set(problems)


def test_all_incompatibilities_are_reported_not_just_the_first():
    problems = rule().incompatibilities(
        **compatible_kwargs(model_version="m2", family="spread", available_settled_games=0)
    )
    assert len(problems) == 3


def test_price_bounds_are_inclusive_at_the_edges():
    bounded = rule(price_min=0.40, price_max=0.60)
    assert bounded.incompatibilities(**compatible_kwargs(executable_price=0.40)) == []
    assert bounded.incompatibilities(**compatible_kwargs(executable_price=0.60)) == []
    assert RuleIncompatibility.PRICE_OUT_OF_RANGE in bounded.incompatibilities(
        **compatible_kwargs(executable_price=0.3999)
    )


def test_a_missing_price_fails_a_price_bounded_rule():
    bounded = rule(price_min=0.40)
    assert RuleIncompatibility.PRICE_OUT_OF_RANGE in bounded.incompatibilities(
        **compatible_kwargs(executable_price=None)
    )


def test_gap_bounds_behave_the_same_way():
    bounded = rule(gap_min=0.05, gap_max=0.20)
    assert bounded.incompatibilities(**compatible_kwargs(model_market_gap=0.05)) == []
    assert RuleIncompatibility.GAP_OUT_OF_REGION in bounded.incompatibilities(
        **compatible_kwargs(model_market_gap=0.04)
    )
    assert RuleIncompatibility.GAP_OUT_OF_REGION in bounded.incompatibilities(
        **compatible_kwargs(model_market_gap=None)
    )


def test_side_scope_is_optional_but_enforced_when_present():
    assert rule().incompatibilities(**compatible_kwargs(side="no")) == []
    scoped = rule(sides=frozenset({"yes"}))
    assert RuleIncompatibility.SIDE_MISMATCH in scoped.incompatibilities(**compatible_kwargs(side="no"))
    assert RuleIncompatibility.SIDE_MISMATCH in scoped.incompatibilities(**compatible_kwargs(side=None))


def test_sample_minimum_boundary_is_exact():
    assert rule(minimum_settled_games=200).incompatibilities(
        **compatible_kwargs(available_settled_games=200)
    ) == []
    assert RuleIncompatibility.SAMPLE_BELOW_RULE_MINIMUM in rule(
        minimum_settled_games=200
    ).incompatibilities(**compatible_kwargs(available_settled_games=199))


def test_parsed_artifact_round_trips_its_declared_evidence(tmp_path):
    path = tmp_path / "a.json"
    path.write_text(json.dumps(artifact_dict()), encoding="utf-8")
    artifact = load_artifact(path).artifact
    assert artifact.evidence.settled_game_count == 400
    assert artifact.evidence.validation_window_start == datetime(2026, 8, 1, tzinfo=UTC)
    assert artifact.rules[0].values == {"some_opaque_key": 0.04}
