"""Week 1 ops health: every state reachable, precedence honoured, and
nothing green by default.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from cfb_edge_finder.decision.collection_protection import (
    ProtectionAssessment,
    ProtectionState,
)
from cfb_edge_finder.decision.ops_health import (
    HealthCheck,
    OpsHealthReport,
    OpsState,
    check_closing_coverage,
    check_collection_protection,
    check_corpus_integrity,
    check_natural_data,
    check_safety_locks,
)

NOW = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)


def report(*checks: HealthCheck) -> OpsHealthReport:
    return OpsHealthReport(generated_at=NOW, checks=list(checks))


def check(state: OpsState, check_id: str = "c") -> HealthCheck:
    return HealthCheck(check_id, state, "detail")


# --------------------------------------------------- precedence


def test_no_checks_is_blocked_not_healthy():
    """An empty report is ignorance, not health. Defaulting to HEALTHY
    here would make a crashed collector look fine."""
    assert report().overall_state is OpsState.BLOCKED


@pytest.mark.parametrize(
    "states,expected",
    [
        ([OpsState.HEALTHY], OpsState.HEALTHY),
        ([OpsState.HEALTHY, OpsState.PENDING_NATURAL_DATA], OpsState.PENDING_NATURAL_DATA),
        ([OpsState.HEALTHY, OpsState.WARN], OpsState.WARN),
        ([OpsState.PENDING_NATURAL_DATA, OpsState.WARN], OpsState.WARN),
        ([OpsState.WARN, OpsState.BLOCKED], OpsState.BLOCKED),
        ([OpsState.BLOCKED, OpsState.PENDING_NATURAL_DATA, OpsState.HEALTHY], OpsState.BLOCKED),
    ],
)
def test_precedence_is_blocked_then_warn_then_pending_then_healthy(states, expected):
    assert report(*(check(s, f"c{i}") for i, s in enumerate(states))).overall_state is expected


def test_pending_natural_data_is_never_masked_by_healthy_checks():
    """Reporting HEALTHY over an empty settlement set would be a false
    claim that the research can proceed."""
    combined = report(
        check(OpsState.HEALTHY, "a"),
        check(OpsState.HEALTHY, "b"),
        check(OpsState.PENDING_NATURAL_DATA, "c"),
    )
    assert combined.overall_state is OpsState.PENDING_NATURAL_DATA


def test_every_state_is_reachable_from_the_real_check_functions():
    """A state nobody can produce is decoration."""

    def protection(state):
        return check_collection_protection(
            ProtectionAssessment(state=state, detail="d")
        ).state

    produced = {
        protection(ProtectionState.QUIET_PERIOD),
        protection(ProtectionState.CHECKPOINT_APPROACHING),
        protection(ProtectionState.CLOSING_AT_RISK),
        check_natural_data(settled_games=0, minimum_for_research=None).state,
    }
    assert produced == set(OpsState)


def test_every_protection_state_maps_to_an_ops_state():
    """No protection state may fall through unmapped -- a KeyError here
    at runtime would take the whole health command down."""
    for state in ProtectionState:
        assert check_collection_protection(ProtectionAssessment(state=state, detail="d")).state in OpsState


# NOTE: the old collection-freshness and external-scheduler checks were
# removed in favour of the deadline-aware model. They compared the
# observed trigger interval against an assumed fixed cadence, which the
# repository cannot observe and which reported an intentional
# quiet-period configuration as BLOCKED. Their replacement is tested in
# tests/test_collection_protection.py.


# ------------------------------------------------ corpus integrity


@pytest.mark.parametrize(
    "kwargs",
    [
        {"duplicate_rows": 1},
        {"malformed_rows": 1},
        {"non_prospective_rows": 1},
    ],
)
def test_any_integrity_breach_is_blocked_with_no_degraded_mode(kwargs):
    base = dict(duplicate_rows=0, malformed_rows=0, non_prospective_rows=0, total_rows=100)
    base.update(kwargs)
    result = check_corpus_integrity(**base)
    assert result.state is OpsState.BLOCKED
    assert "Do NOT backfill" in result.remedy


def test_an_empty_corpus_is_blocked_not_healthy():
    assert check_corpus_integrity(
        duplicate_rows=0, malformed_rows=0, non_prospective_rows=0, total_rows=0
    ).state is OpsState.BLOCKED


def test_a_clean_corpus_is_healthy():
    assert check_corpus_integrity(
        duplicate_rows=0, malformed_rows=0, non_prospective_rows=0, total_rows=1909
    ).state is OpsState.HEALTHY


def test_all_integrity_problems_are_reported_together():
    detail = check_corpus_integrity(
        duplicate_rows=2, malformed_rows=3, non_prospective_rows=4, total_rows=100
    ).detail
    assert "2 duplicate" in detail and "3 malformed" in detail and "4 rows not marked" in detail


# ------------------------------------------------ closing coverage


def test_nothing_due_is_healthy_not_a_missed_capture():
    assert check_closing_coverage(closing_due=0, closing_captured=0).state is OpsState.HEALTHY


def test_all_closings_missed_is_blocked_and_says_it_is_unrecoverable():
    result = check_closing_coverage(closing_due=5, closing_captured=0)
    assert result.state is OpsState.BLOCKED
    assert "never backfilled" in result.detail


def test_a_partial_miss_warns_and_says_the_loss_is_permanent():
    result = check_closing_coverage(closing_due=5, closing_captured=3)
    assert result.state is OpsState.WARN
    assert "permanently lost" in result.detail


def test_full_coverage_is_healthy():
    assert check_closing_coverage(closing_due=5, closing_captured=5).state is OpsState.HEALTHY


# --------------------------------------------------- safety locks


def test_all_locks_holding_is_healthy():
    assert check_safety_locks(
        qualification_disabled=True,
        threshold_artifact_absent=True,
        validated_state_unreachable=True,
        sizing_disconnected=True,
    ).state is OpsState.HEALTHY


@pytest.mark.parametrize(
    "opened",
    [
        "qualification_disabled",
        "threshold_artifact_absent",
        "validated_state_unreachable",
        "sizing_disconnected",
    ],
)
def test_any_open_lock_is_blocked_with_no_degraded_mode(opened):
    kwargs = dict(
        qualification_disabled=True,
        threshold_artifact_absent=True,
        validated_state_unreachable=True,
        sizing_disconnected=True,
    )
    kwargs[opened] = False
    result = check_safety_locks(**kwargs)
    assert result.state is OpsState.BLOCKED
    assert "Stop." in result.remedy


# --------------------------------------------------- natural data


def test_zero_settled_games_states_the_blocking_phrase_verbatim():
    result = check_natural_data(settled_games=0, minimum_for_research=None)
    assert result.state is OpsState.PENDING_NATURAL_DATA
    assert "EMPIRICAL THRESHOLD RESEARCH BLOCKED ON NATURAL SAMPLE SIZE." in result.detail
    assert result.remedy == ""


def test_no_established_minimum_cannot_claim_sufficiency():
    """Some data is not evidence that there is enough data. Without a
    stated minimum, sufficiency is unknowable rather than satisfied."""
    result = check_natural_data(settled_games=500, minimum_for_research=None)
    assert result.state is OpsState.PENDING_NATURAL_DATA
    assert "no validated minimum" in result.detail


def test_below_a_stated_minimum_is_still_blocked():
    assert check_natural_data(settled_games=199, minimum_for_research=200).state is (
        OpsState.PENDING_NATURAL_DATA
    )


def test_meeting_a_stated_minimum_does_not_approve_anything():
    result = check_natural_data(settled_games=200, minimum_for_research=200)
    assert result.state is OpsState.HEALTHY
    assert "separate human decision" in result.detail


# -------------------------------------------------------- output


def test_the_payload_is_json_serialisable_and_sorted():
    payload = report(check(OpsState.WARN, "z"), check(OpsState.HEALTHY, "a")).to_payload()
    assert json.loads(json.dumps(payload))["overall_state"] == "WARN"
    assert [c["check_id"] for c in payload["checks"]] == ["a", "z"]


def test_state_counts_include_every_state_even_at_zero():
    counts = report(check(OpsState.HEALTHY)).state_counts()
    assert set(counts) == {s.value for s in OpsState}
    assert counts["HEALTHY"] == 1 and counts["BLOCKED"] == 0


def test_rendering_puts_the_worst_checks_first():
    text = report(
        check(OpsState.HEALTHY, "healthy_one"),
        check(OpsState.BLOCKED, "blocked_one"),
        check(OpsState.WARN, "warn_one"),
    ).render()
    assert text.index("blocked_one") < text.index("warn_one") < text.index("healthy_one")
    assert "OVERALL: BLOCKED" in text


def test_rendering_is_deterministic():
    built = report(check(OpsState.WARN, "b"), check(OpsState.HEALTHY, "a"))
    assert built.render() == built.render()


def test_blocked_checks_are_directly_enumerable():
    built = report(check(OpsState.BLOCKED, "x"), check(OpsState.HEALTHY, "y"))
    assert [c.check_id for c in built.blocked_checks] == ["x"]
