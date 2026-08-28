"""Classification of the unresolved Kalshi population.

The headline "~1,400 unresolved" says nothing on its own: an unresolved
FCS-vs-FCS market is a population we deliberately decline, while an
unresolved FBS-vs-FBS market is a lost research opportunity. These tests
hold the line between those two, and hold the reconciliation property
that makes the accounting trustworthy.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from cfb_edge_finder.kalshi.cfb_coverage_reason import KalshiCfbCoverageReason as Reason

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "audit_mapping_coverage", REPO_ROOT / "scripts" / "audit_mapping_coverage.py"
)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)

ALL_CATEGORIES = {
    audit.FBS_VS_FBS_POTENTIAL_LEAK,
    audit.FBS_VS_FCS_UNSUPPORTED,
    audit.FCS_VS_FCS_UNSUPPORTED,
    audit.FUTURES_OR_NON_CORE,
    audit.AMBIGUOUS_TEAM_NAME,
    audit.DETERMINISTIC_ALIAS_MISSING,
    audit.MALFORMED_OR_UNSUPPORTED_MARKET,
    audit.OTHER_EXPLICIT_REASON,
}


def _side(kind, raw="Some Team"):
    return {"raw": raw, "kind": kind}


@pytest.mark.parametrize(
    "reason,home,away,expected",
    [
        # The only shape that can cost us research.
        (Reason.AMBIGUOUS_GAME_MAPPING, "fbs", "fbs", audit.FBS_VS_FBS_POTENTIAL_LEAK),
        # Populations we decline on purpose.
        (Reason.AMBIGUOUS_TEAM_MAPPING, "fbs", "known_fcs", audit.FBS_VS_FCS_UNSUPPORTED),
        (Reason.AMBIGUOUS_TEAM_MAPPING, "known_fcs", "fbs", audit.FBS_VS_FCS_UNSUPPORTED),
        (Reason.AMBIGUOUS_TEAM_MAPPING, "known_fcs", "known_fcs", audit.FCS_VS_FCS_UNSUPPORTED),
        (Reason.AMBIGUOUS_TEAM_MAPPING, "known_fcs", "unknown", audit.FCS_VS_FCS_UNSUPPORTED),
        # Not a single-game market at all.
        (Reason.NON_GAME_FUTURES, "fbs", "fbs", audit.FUTURES_OR_NON_CORE),
        # Genuine ambiguity -- must stay unresolved.
        (Reason.AMBIGUOUS_TEAM_MAPPING, "ambiguous", "fbs", audit.AMBIGUOUS_TEAM_NAME),
        (Reason.AMBIGUOUS_TEAM_MAPPING, "fbs", "ambiguous", audit.AMBIGUOUS_TEAM_NAME),
        # A token in neither list.
        (Reason.AMBIGUOUS_TEAM_MAPPING, "unknown", "fbs", audit.DETERMINISTIC_ALIAS_MISSING),
        # No usable names.
        (Reason.PARSE_UNRESOLVED, "missing", "missing", audit.MALFORMED_OR_UNSUPPORTED_MARKET),
        (Reason.PARSE_UNRESOLVED, "fbs", "missing", audit.MALFORMED_OR_UNSUPPORTED_MARKET),
    ],
)
def test_classification(reason, home, away, expected):
    category, why = audit.classify_unresolved(reason, _side(home), _side(away))
    assert category == expected
    assert why, "every category must carry a stated reason"


def test_every_result_is_a_known_category():
    """No generic bucket: an unhandled shape lands in
    OTHER_EXPLICIT_REASON and says so, rather than being folded into a
    benign category and disappearing from the accounting."""
    kinds = ["fbs", "non_fbs", "known_fcs", "ambiguous", "unknown", "missing", "weird"]
    for reason in list(Reason) + [None]:
        for home in kinds:
            for away in kinds:
                category, why = audit.classify_unresolved(reason, _side(home), _side(away))
                assert category in ALL_CATEGORIES
                assert why


def test_fcs_population_is_checked_before_alias_gaps():
    """An FCS school missing from the registry is EXPECTED -- the registry
    is an FBS registry by design -- so it must never be reported as a
    deterministic alias defect that someone would then 'fix'."""
    category, _ = audit.classify_unresolved(
        Reason.AMBIGUOUS_TEAM_MAPPING, _side("known_fcs"), _side("unknown")
    )
    assert category != audit.DETERMINISTIC_ALIAS_MISSING
    assert category == audit.FCS_VS_FCS_UNSUPPORTED


def test_ambiguity_never_becomes_a_leak_or_an_alias_gap():
    """A bare 'Miami' must never be counted as a lost opportunity or as
    something a deterministic alias would fix -- either would invite a
    guess. It may land in an unsupported-population bucket when the OTHER
    side is a known FCS school, because an FCS team on the card makes the
    fixture unsupported whichever Miami it is."""
    for other in ("fbs", "unknown"):
        category, _ = audit.classify_unresolved(Reason.AMBIGUOUS_TEAM_MAPPING, _side("ambiguous"), _side(other))
        assert category == audit.AMBIGUOUS_TEAM_NAME
    fcs_side, why = audit.classify_unresolved(
        Reason.AMBIGUOUS_TEAM_MAPPING, _side("ambiguous"), _side("known_fcs")
    )
    assert fcs_side == audit.FCS_VS_FCS_UNSUPPORTED
    assert "undetermined" in why, "the label must not silently assert both sides are FCS"
    assert fcs_side not in (audit.FBS_VS_FBS_POTENTIAL_LEAK, audit.DETERMINISTIC_ALIAS_MISSING)


def test_only_two_resolved_fbs_sides_count_as_a_leak():
    """The success metric is this number, so nothing may inflate it."""
    for home, away in [("fbs", "known_fcs"), ("fbs", "unknown"), ("fbs", "ambiguous"),
                       ("fbs", "missing"), ("fbs", "non_fbs"), ("unknown", "unknown")]:
        category, _ = audit.classify_unresolved(Reason.AMBIGUOUS_GAME_MAPPING, _side(home), _side(away))
        assert category != audit.FBS_VS_FBS_POTENTIAL_LEAK, f"{home}/{away} inflated the leak count"


def test_futures_short_circuit_regardless_of_team_kinds():
    for home in ("fbs", "unknown", "missing"):
        category, _ = audit.classify_unresolved(Reason.NON_GAME_FUTURES, _side(home), _side("fbs"))
        assert category == audit.FUTURES_OR_NON_CORE


def test_unresolved_denominator_matches_the_collector():
    """The audit must count the same population the collector's health
    check calls a failure, or the classification would reconcile against
    the wrong total."""
    from cfb_edge_finder.research.scan_logic import is_genuine_mapping_failure

    for reason in Reason:
        expected = is_genuine_mapping_failure(reason) or reason == Reason.NON_GAME_FUTURES
        assert audit.capture_is_unresolved(reason) is bool(expected), reason


def test_audit_makes_no_mapping_or_alias_changes():
    """Diagnosis only -- it must not mutate the registry or aliases."""
    source = (REPO_ROOT / "scripts" / "audit_mapping_coverage.py").read_text(encoding="utf-8")
    for banned in ("ALIASES[", "REGISTRY.append", "REGISTRY +=", "_BY_ID["):
        assert banned not in source
