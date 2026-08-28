"""Pregame structural model diagnostics.

Several of these encode false positives found against the genuine corpus
and corrected: tie mass and untradeable quotes are documented correct
behaviour, not defects, and a diagnostic that cries wolf on them would be
ignored on the night it matters.
"""

from __future__ import annotations

import math

import pytest

from cfb_edge_finder.expression.grouping import ContractSnapshot
from cfb_edge_finder.expression.taxonomy import ContractSemantics
from cfb_edge_finder.modeling.live_diagnostics import (
    DiagnosticSeverity,
    check_fee_provenance,
    check_ladder_monotonic,
    check_model_provenance,
    check_probability_valid,
    check_projection_reuse,
    check_unsupported_population_unpriced,
    check_week1_carryover_disclosure,
    check_winner_complementarity,
    run_model_health,
)
from cfb_edge_finder.schemas.common import MarketFamily, Side

GAME = "cfb-2026-wk01-a-at-b"


def snap(
    ticker: str,
    *,
    family: MarketFamily | None = MarketFamily.MONEYLINE,
    team: Side | None = Side.HOME,
    threshold: float | None = None,
    probability: float | None = 0.6,
    yes_price: float | None = 0.5,
    game_id: str = GAME,
    pricing_status: str | None = "model_priced",
    model_version: str | None = "m1",
    fee_status: str | None = "VERIFIED_CURRENT",
) -> ContractSnapshot:
    return ContractSnapshot(
        semantics=ContractSemantics(
            market_ticker=ticker,
            game_id=game_id,
            family=family,
            team=team,
            side=Side.OVER if family is MarketFamily.TOTAL else None,
            threshold=threshold,
            semantic_operator=">",
            parse_status="confirmed_live",
        ),
        timing_label="T_24H",
        captured_at="2026-08-28T13:00:00+00:00",
        model_probability=probability,
        executable_yes_price=yes_price,
        executable_no_price=None if yes_price is None else round(1 - yes_price, 2),
        market_status="active",
        fee_status=fee_status,
        fee_schedule_version="kalshi_fee_schedule_2026_07_07_taker",
        model_version=model_version,
        pricing_status=pricing_status,
        series_ticker="KXNCAAFGAME",
        schema_version="research_corpus_v2",
        capture_mode="PROSPECTIVE",
    )


# ------------------------------------------------- probabilities


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_probability_is_a_blocker(bad):
    findings = check_probability_valid(bad, context="t")
    assert findings[0].severity is DiagnosticSeverity.BLOCKER


@pytest.mark.parametrize("bad", [-0.01, 1.01, 1.7, -5.0])
def test_probability_outside_the_unit_interval_is_a_blocker(bad):
    findings = check_probability_valid(bad, context="t")
    assert findings[0].check_id == "probability_in_unit_interval"
    assert findings[0].severity is DiagnosticSeverity.BLOCKER


@pytest.mark.parametrize("degenerate", [0.0, 1.0])
def test_certainty_is_flagged_high_not_accepted(degenerate):
    """Legal arithmetic, indefensible football."""
    findings = check_probability_valid(degenerate, context="t")
    assert findings[0].check_id == "probability_degenerate"
    assert findings[0].severity is DiagnosticSeverity.HIGH


def test_ordinary_probabilities_produce_nothing():
    assert check_probability_valid(0.62, context="t") == []


def test_missing_probability_is_not_a_finding():
    """An unpriced contract is not a broken one."""
    assert check_probability_valid(None, context="t") == []


# ------------------------------------------------------ ladders


def test_spread_ladder_must_be_non_increasing_in_threshold():
    """Settlement is `team_margin > threshold`, so a HIGHER threshold is
    strictly harder. Winning by more than 14.5 cannot be more likely than
    winning by more than 3.5. Verified against the genuine corpus, where
    every observed ladder is non-increasing in threshold."""
    broken = [
        snap("A", family=MarketFamily.SPREAD, threshold=3.5, probability=0.60),
        snap("B", family=MarketFamily.SPREAD, threshold=14.5, probability=0.80),
    ]
    findings = check_ladder_monotonic(broken, family=MarketFamily.SPREAD)
    assert findings and findings[0].severity is DiagnosticSeverity.BLOCKER
    assert "cannot be more likely" in findings[0].detail


def test_a_coherent_spread_ladder_passes():
    """Shape taken from a real corpus ladder (oklahoma-at-michigan,
    home): probability falls monotonically as the threshold rises."""
    ok = [
        snap("A", family=MarketFamily.SPREAD, threshold=3.5, probability=0.7314),
        snap("B", family=MarketFamily.SPREAD, threshold=7.5, probability=0.6517),
        snap("C", family=MarketFamily.SPREAD, threshold=14.5, probability=0.4970),
    ]
    assert check_ladder_monotonic(ok, family=MarketFamily.SPREAD) == []


def test_total_ladder_must_be_non_increasing():
    broken = [
        snap("A", family=MarketFamily.TOTAL, team=None, threshold=45.5, probability=0.50),
        snap("B", family=MarketFamily.TOTAL, team=None, threshold=65.5, probability=0.70),
    ]
    assert check_ladder_monotonic(broken, family=MarketFamily.TOTAL)


def test_ladders_of_different_teams_are_not_compared():
    """Home -3.5 and Away -3.5 are different ladders; comparing them
    would invent a violation out of two coherent sets."""
    mixed = [
        snap("A", family=MarketFamily.SPREAD, team=Side.HOME, threshold=3.5, probability=0.40),
        snap("B", family=MarketFamily.SPREAD, team=Side.AWAY, threshold=7.5, probability=0.55),
    ]
    assert check_ladder_monotonic(mixed, family=MarketFamily.SPREAD) == []


def test_ladders_of_different_games_are_not_compared():
    mixed = [
        snap("A", family=MarketFamily.SPREAD, threshold=3.5, probability=0.40),
        snap("B", family=MarketFamily.SPREAD, threshold=7.5, probability=0.55, game_id="other"),
    ]
    assert check_ladder_monotonic(mixed, family=MarketFamily.SPREAD) == []


def test_equal_probabilities_at_different_thresholds_are_allowed():
    flat = [
        snap("A", family=MarketFamily.SPREAD, threshold=3.5, probability=0.50),
        snap("B", family=MarketFamily.SPREAD, threshold=7.5, probability=0.50),
    ]
    assert check_ladder_monotonic(flat, family=MarketFamily.SPREAD) == []


# ------------------------------------------ winner complementarity


def test_tie_mass_shortfall_is_info_not_a_defect():
    """THE FALSE POSITIVE. 33 real games sum to ~0.978; that shortfall is
    documented simulated tie mass, reported and not corrected."""
    sides = [
        snap("H", team=Side.HOME, probability=0.60),
        snap("A", team=Side.AWAY, probability=0.378),
    ]
    findings = check_winner_complementarity(sides)
    assert findings and findings[0].check_id == "winner_tie_mass"
    assert findings[0].severity is DiagnosticSeverity.INFO


def test_probabilities_summing_above_one_are_high():
    """Tie mass can only REMOVE probability. An excess cannot be
    explained by it, so the two sides did not share a distribution."""
    sides = [
        snap("H", team=Side.HOME, probability=0.60),
        snap("A", team=Side.AWAY, probability=0.55),
    ]
    findings = check_winner_complementarity(sides)
    assert findings[0].check_id == "winner_probability_excess"
    assert findings[0].severity is DiagnosticSeverity.HIGH


def test_exactly_complementary_sides_produce_nothing():
    sides = [
        snap("H", team=Side.HOME, probability=0.60),
        snap("A", team=Side.AWAY, probability=0.40),
    ]
    assert check_winner_complementarity(sides) == []


def test_a_lone_moneyline_side_is_not_assessed():
    assert check_winner_complementarity([snap("H", team=Side.HOME)]) == []


# ------------------------------------------------- fee provenance


def test_untradeable_quote_without_a_fee_is_info():
    """THE OTHER FALSE POSITIVE. Two real contracts quoted at exactly
    $1.00 carry no fee because they cannot be bought at a profit."""
    findings = check_fee_provenance([snap("A", yes_price=1.0, fee_status="unverified")])
    assert findings[0].check_id == "fee_absent_untradeable_quote"
    assert findings[0].severity is DiagnosticSeverity.INFO


def test_priced_tradeable_contract_without_a_verified_fee_is_high():
    findings = check_fee_provenance([snap("A", yes_price=0.5, fee_status="unverified")])
    assert findings[0].check_id == "fee_provenance_unverified"
    assert findings[0].severity is DiagnosticSeverity.HIGH


def test_unpriced_contract_owes_no_fee():
    findings = check_fee_provenance(
        [snap("A", probability=None, pricing_status="not_priced", fee_status="unverified")]
    )
    assert findings == []


def test_verified_fee_produces_nothing():
    assert check_fee_provenance([snap("A")]) == []


# ------------------------------------------------- population etc


def test_pricing_an_unsupported_population_is_a_blocker():
    findings = check_unsupported_population_unpriced(
        [snap("A", pricing_status="not_priced", probability=0.6)]
    )
    assert findings[0].severity is DiagnosticSeverity.BLOCKER


def test_unpriced_unsupported_contract_is_fine():
    assert check_unsupported_population_unpriced(
        [snap("A", pricing_status="not_priced", probability=None)]
    ) == []


def test_priced_contract_without_a_model_version_is_high():
    """A missing version silently removes the contract from every future
    threshold rule, because None is a mismatch not a wildcard."""
    findings = check_model_provenance([snap("A", model_version=None)])
    assert findings[0].severity is DiagnosticSeverity.HIGH


def test_two_model_versions_in_one_game_is_a_blocker():
    mixed = [snap("A", model_version="m1"), snap("B", model_version="m2")]
    findings = check_projection_reuse(mixed)
    assert findings[0].severity is DiagnosticSeverity.BLOCKER
    assert "2 model versions" in findings[0].detail


def test_one_model_version_per_game_passes():
    assert check_projection_reuse([snap("A"), snap("B")]) == []


def test_zero_carryover_is_disclosed_as_info_not_alarm():
    """Week 1's defining property. Important to see, wrong to alarm."""
    findings = check_week1_carryover_disclosure({GAME: 0.0})
    assert findings[0].severity is DiagnosticSeverity.INFO
    assert "entirely prior-season" in findings[0].detail
    assert check_week1_carryover_disclosure({GAME: 0.5}) == []


# ----------------------------------------------------- aggregate


def test_a_clean_snapshot_set_is_healthy():
    report = run_model_health([snap("H", team=Side.HOME, probability=0.6),
                               snap("A", team=Side.AWAY, probability=0.4)])
    assert report.is_healthy
    assert report.counts()["BLOCKER"] == 0


def test_info_findings_do_not_make_a_report_unhealthy():
    report = run_model_health([snap("H", team=Side.HOME, probability=0.60),
                               snap("A", team=Side.AWAY, probability=0.378)])
    assert report.counts()["INFO"] >= 1
    assert report.is_healthy


def test_a_blocker_makes_a_report_unhealthy():
    report = run_model_health([snap("A", probability=math.nan)])
    assert not report.is_healthy
    assert report.blockers


def test_the_module_has_no_edge_or_disagreement_check():
    """Large model-market disagreement is the research subject, not an
    anomaly. Flagging it would teach the reader to dismiss exactly the
    observations worth studying."""
    import pathlib

    src = pathlib.Path("src/cfb_edge_finder/modeling/live_diagnostics.py").read_text()
    for banned in ("check_disagreement", "check_edge", "gap_too_large", "suspicious_edge"):
        assert banned not in src


def test_diagnostics_never_mutate_a_snapshot():
    """A diagnostic that repaired what it measured would destroy the
    signal it exists to provide."""
    before = snap("A", probability=0.6)
    run_model_health([before])
    assert before.model_probability == 0.6
    assert before.fee_status == "VERIFIED_CURRENT"
