"""The required edge is an operator preference and must never read as a finding.

`"min_net_edge": 0.02` in a machine-readable artifact reads like a calibrated
bar. It never was one -- this repository has never validated the edge at which
a CFB Kalshi contract is profitable -- and a reader, human or model, who takes
that number for evidence is being misled by the file rather than by anything
anybody claimed.

So the default is neutral, and wherever the number is written it travels with
its own provenance.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.execution.evaluator import (  # noqa: E402
    DEFAULT_MIN_NET_EDGE,
    LEGACY_UNVALIDATED_MIN_NET_EDGE,
    NEGLIGIBLE_EDGE,
)
from cfb_edge_finder.execution.report import min_net_edge_provenance  # noqa: E402


def test_the_default_required_edge_is_neutral():
    """Zero means the repository imposes no bar it cannot justify."""
    assert DEFAULT_MIN_NET_EDGE == 0.0


def test_the_old_default_is_retained_but_labelled_unvalidated():
    """Kept so an operator can ask for the old behaviour BY NAME and see, in
    the artifact, that they chose it rather than inherited it."""
    assert LEGACY_UNVALIDATED_MIN_NET_EDGE == 0.02
    assert "UNVALIDATED" in "LEGACY_UNVALIDATED_MIN_NET_EDGE"


def test_neutral_is_not_the_same_as_no_filter():
    """Zero does not mean 'bet everything'. A contract still has to be
    distinguishable from noise, and still has to survive every corner of the
    uncertainty region to be robust. Those were always the real filters."""
    assert NEGLIGIBLE_EDGE > 0
    assert DEFAULT_MIN_NET_EDGE < NEGLIGIBLE_EDGE or DEFAULT_MIN_NET_EDGE == 0.0


def test_the_default_is_reported_as_the_repositorys_not_the_operators():
    p = min_net_edge_provenance(DEFAULT_MIN_NET_EDGE)
    assert p["source"] == "repository_default"


def test_an_operator_supplied_bar_is_attributed_to_the_operator():
    p = min_net_edge_provenance(0.035)
    assert p["source"] == "operator"


def test_asking_for_the_old_default_is_recorded_as_an_operator_choice():
    """The point of keeping it: choosing 0.02 is now visible as a choice."""
    assert min_net_edge_provenance(LEGACY_UNVALIDATED_MIN_NET_EDGE)["source"] == "operator"


def test_no_bar_is_ever_reported_as_validated():
    for value in (0.0, 0.001, 0.02, 0.05, 0.5):
        p = min_net_edge_provenance(value)
        assert p["is_validated_threshold"] is False, value


def test_the_note_separates_robustness_from_calibration():
    """The specific confusion worth pre-empting: robustness measures
    sensitivity to the handicap's STATED uncertainty. It says nothing about
    whether the fair probability is calibrated."""
    note = min_net_edge_provenance(0.0)["note"].lower()
    assert "calibrated" in note
    assert "robustness" in note
    assert "never established" in note


def test_the_provenance_makes_no_profitability_claim():
    note = min_net_edge_provenance(0.02)["note"].lower()
    for banned in ("profitable at", "expected profit", "proven", "validated bar", "will win"):
        assert banned not in note, banned
