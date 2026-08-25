"""Mission section 3: every KalshiCfbCoverageReason must map to exactly
one CoverageOutcome, and the mapping must be total (checked mechanically,
not by eyeballing the dict)."""

from __future__ import annotations

import pytest

from cfb_edge_finder.kalshi.cfb_coverage_reason import KalshiCfbCoverageReason, to_coverage_outcome
from cfb_edge_finder.schemas.common import CoverageOutcome


def test_every_reason_maps_to_a_coverage_outcome():
    for reason in KalshiCfbCoverageReason:
        outcome = to_coverage_outcome(reason)
        assert isinstance(outcome, CoverageOutcome)


def test_mapped_supported_is_evaluated():
    assert to_coverage_outcome(KalshiCfbCoverageReason.MAPPED_SUPPORTED) == CoverageOutcome.EVALUATED


def test_ambiguous_reasons_are_ticker_unresolved():
    assert to_coverage_outcome(KalshiCfbCoverageReason.AMBIGUOUS_GAME_MAPPING) == CoverageOutcome.TICKER_UNRESOLVED
    assert to_coverage_outcome(KalshiCfbCoverageReason.AMBIGUOUS_TEAM_MAPPING) == CoverageOutcome.TICKER_UNRESOLVED
    assert to_coverage_outcome(KalshiCfbCoverageReason.PARSE_UNRESOLVED) == CoverageOutcome.TICKER_UNRESOLVED


def test_unsupported_reasons_are_unsupported_market():
    assert (
        to_coverage_outcome(KalshiCfbCoverageReason.MAPPED_UNSUPPORTED_FAMILY) == CoverageOutcome.UNSUPPORTED_MARKET
    )
    assert (
        to_coverage_outcome(KalshiCfbCoverageReason.MAPPED_UNSUPPORTED_POPULATION)
        == CoverageOutcome.UNSUPPORTED_MARKET
    )
    assert to_coverage_outcome(KalshiCfbCoverageReason.NON_GAME_FUTURES) == CoverageOutcome.UNSUPPORTED_MARKET


def test_reason_values_carry_no_betting_language():
    forbidden_tokens = {"bet", "play", "wager", "stake", "tier"}
    for reason in KalshiCfbCoverageReason:
        tokens = reason.value.split("_")
        overlap = forbidden_tokens & set(tokens)
        assert not overlap, f"{reason!r} value {reason.value!r} contains forbidden token(s) {overlap}"


def test_to_coverage_outcome_rejects_non_member():
    with pytest.raises(KeyError):
        to_coverage_outcome("not_a_real_reason")  # type: ignore[arg-type]
