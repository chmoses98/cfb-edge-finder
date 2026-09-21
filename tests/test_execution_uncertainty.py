"""Uncertainty, sensitivity and the rule a point estimate cannot buy.

*** THE ASSERTION THIS FILE EXISTS FOR ***
`test_a_point_estimate_can_never_be_called_robust`. Everything else here
supports it. A system that mechanically propagated one projected score across
several hundred ladder rungs and then labelled the survivors ROBUST would be
worse than the one it replaced, because it would carry the authority of a
sensitivity analysis it never ran.
"""

from __future__ import annotations

import pytest

from cfb_edge_finder.execution.evaluator import (
    CANDIDATE_STATUSES,
    EvaluationStatus,
    evaluate_game,
    fair_from_distribution,
)
from cfb_edge_finder.execution.handicap import (
    HandicapValidationError,
    PeriodDistribution,
    parse_handicap,
)
from cfb_edge_finder.execution.sensitivity import (
    Robustness,
    SensitivityBound,
    bet_up_to,
    classify,
    evaluate_side,
)
from cfb_edge_finder.execution.uncertainty import (
    HandicapUncertainty,
    UncertaintyValidationError,
    scenarios,
)
from tests.execution_fakes import GAME_KEY, catalog_dir
from tests.test_execution_disposition import config


def packet(tmp_path, markets=None):
    from cfb_edge_finder.execution.slate import build_slate

    return build_slate(catalog_dir(tmp_path, markets), config(), slate_date=None).packets[0]


def payload(*, uncertainty: dict | None, **overrides):
    block = {
        "home_mean": 27.0,
        "away_mean": 24.0,
        "home_sd": 10.5,
        "away_sd": 10.0,
        "correlation": 0.1,
    }
    if uncertainty is not None:
        block["uncertainty"] = uncertainty
    base = {
        "schema_version": "cfb_handicap_payload/2.0.0",
        "game_key": GAME_KEY,
        "teams": {"home": "Ole Miss", "away": "LSU"},
        "period_distributions": {"full_game": block},
    }
    base.update(overrides)
    return parse_handicap(base)


STATED = {"margin_points": 3.0, "total_points": 5.0, "sd_scale_low": 0.9, "sd_scale_high": 1.15}


# ---------------------------------------------------------- the region


def test_an_absent_region_is_not_a_region_of_width_zero():
    """The distinction the whole taxonomy rests on.

    Both produce one scenario and an identical minimum edge. Only one of them
    is a claim about how wrong the handicap might be."""
    absent = HandicapUncertainty.absent()
    zero = HandicapUncertainty(stated=True)

    assert absent.is_degenerate and zero.is_degenerate
    assert not absent.supports_robustness
    assert not zero.supports_robustness, (
        "a stated region of width zero is a point estimate in the uncertainty schema's clothing "
        "and must not buy a robustness claim either"
    )
    assert len(scenarios(absent)) == 1
    assert len(scenarios(zero)) == 1


def test_a_stated_region_enumerates_its_corners_with_the_base_case_first():
    grid = scenarios(HandicapUncertainty(**STATED, stated=True))
    assert grid[0].is_base, "the base fair value is what every artifact quotes; it must be first"
    # 2 margin x 2 total x 2 sd, plus the base.
    assert len(grid) == 9
    labels = {s.label for s in grid}
    assert "margin-,total-,sd_low" in labels
    assert "margin+,total+,sd_high" in labels
    assert len(labels) == len(grid), "corner labels must be unique or the count is an artefact"


def test_margin_and_total_move_orthogonally():
    """Shifting the margin must not move the total, and vice versa.

    A scheme that moved one team only would change both at once, and the two
    axes would be impossible to read apart in the output."""
    base = PeriodDistribution(27.0, 24.0, 10.5, 10.0, 0.1)
    margin_only = base.perturbed(margin_shift=6.0)
    assert margin_only.home_mean + margin_only.away_mean == pytest.approx(
        base.home_mean + base.away_mean
    )
    assert margin_only.home_mean - margin_only.away_mean == pytest.approx(
        base.home_mean - base.away_mean + 6.0
    )

    total_only = base.perturbed(total_shift=8.0)
    assert total_only.home_mean - total_only.away_mean == pytest.approx(
        base.home_mean - base.away_mean
    )
    assert total_only.home_mean + total_only.away_mean == pytest.approx(
        base.home_mean + base.away_mean + 8.0
    )


def test_a_region_wider_than_the_ceiling_is_refused_rather_than_clamped():
    with pytest.raises(UncertaintyValidationError, match="ceiling"):
        HandicapUncertainty.parse("full_game", {"margin_points": 40.0})


def test_a_correlation_band_that_leaves_the_legal_range_is_refused():
    """Clamping would silently narrow a region the handicapper chose, and a
    narrower region is a STRONGER robustness claim than they made."""
    with pytest.raises(HandicapValidationError, match=r"\[-0.99, 0.99\]"):
        parse_handicap(
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
                        "correlation": 0.8,
                        "uncertainty": {"correlation_delta": 0.5},
                    }
                },
            }
        )


# ------------------------------------------------ the classification rule


def test_a_point_estimate_can_never_be_called_robust():
    """*** THE ONE THAT MATTERS ***

    An untested region gives min == base by construction. The classifier must
    refuse the robust verdict on the BOUND, not on the arithmetic.
    """
    verdict, reason = classify(
        base_net_edge=0.40,
        min_net_edge=0.40,
        min_required_edge=0.02,
        bound=SensitivityBound.NOT_TESTED.value,
    )
    assert verdict == Robustness.SENSITIVE_POSITIVE_EV.value
    assert "stated no uncertainty region" in reason


def test_a_tested_region_that_survives_is_robust_and_one_that_does_not_is_not():
    robust, _ = classify(
        base_net_edge=0.10,
        min_net_edge=0.05,
        min_required_edge=0.02,
        bound=SensitivityBound.EXACT_CORNER_EXTREMUM.value,
    )
    sensitive, _ = classify(
        base_net_edge=0.10,
        min_net_edge=0.01,
        min_required_edge=0.02,
        bound=SensitivityBound.EXACT_CORNER_EXTREMUM.value,
    )
    fragile, why = classify(
        base_net_edge=0.10,
        min_net_edge=-0.03,
        min_required_edge=0.02,
        bound=SensitivityBound.EXACT_CORNER_EXTREMUM.value,
    )
    assert robust == Robustness.ROBUST_POSITIVE_EV.value
    assert sensitive == Robustness.SENSITIVE_POSITIVE_EV.value
    assert fragile == Robustness.NOT_ROBUST.value
    assert "below breakeven" in why or "at or below breakeven" in why


def test_the_bound_is_named_honestly_per_family():
    """A margin BAND is a difference of two tails and is not monotone, so its
    corner minimum is a grid minimum and must say so."""
    monotone = evaluate_side(
        kind="spread",
        entry=0.50,
        fee=0.01,
        fair_by_scenario=[("base", 0.60), ("margin-", 0.55)],
        min_required_edge=0.02,
        tested=True,
    )
    band = evaluate_side(
        kind="margin_band",
        entry=0.50,
        fee=0.01,
        fair_by_scenario=[("base", 0.60), ("margin-", 0.55)],
        min_required_edge=0.02,
        tested=True,
    )
    assert monotone.bound == SensitivityBound.EXACT_CORNER_EXTREMUM.value
    assert band.bound == SensitivityBound.GRID_EXTREMUM.value


def test_the_breakeven_does_not_move_across_the_region():
    """The price is the market's, not the handicap's. Letting it improve in
    the scenarios where the handicap is wrong would flatter the analysis at
    exactly the wrong moment."""
    sensitivity = evaluate_side(
        kind="total",
        entry=0.47,
        fee=0.0132,
        fair_by_scenario=[("base", 0.60), ("total-", 0.51), ("total+", 0.68)],
        min_required_edge=0.02,
        tested=True,
    )
    assert sensitivity.breakeven_probability == pytest.approx(0.4832)
    assert sensitivity.min_net_edge == pytest.approx(0.51 - 0.4832)
    assert sensitivity.max_net_edge == pytest.approx(0.68 - 0.4832)


def test_bet_up_to_errs_toward_paying_less():
    """Kalshi's trade fee rises with price, so a ceiling computed at the
    QUOTED fee is slightly above the true one -- and the artifact says so."""
    ceiling = bet_up_to(fair_probability=0.60, fee=0.013, min_required_edge=0.02)
    assert ceiling == pytest.approx(0.567)


# ------------------------------------------------- through the evaluator


def test_an_uncertain_handicap_produces_robust_and_sensitive_rows(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, payload(uncertainty=STATED), min_net_edge=0.02)
    statuses = evaluation.status_counts
    assert evaluation.unaccounted == 0
    assert statuses.get(EvaluationStatus.ROBUST_POSITIVE_EV.value, 0) + statuses.get(
        EvaluationStatus.SENSITIVE_POSITIVE_EV.value, 0
    ) + statuses.get(EvaluationStatus.NOT_ROBUST.value, 0) > 0
    for row in evaluation.rows:
        if row["status"] in CANDIDATE_STATUSES:
            assert row["sensitivity"]["scenarios_tested"] > 1
            assert row["sensitivity"]["net_edge_low"] <= row["sensitivity"]["base_net_edge"]


def test_the_same_handicap_without_a_region_produces_no_robust_row_at_all(tmp_path):
    game = packet(tmp_path)
    tested = evaluate_game(game, payload(uncertainty=STATED), min_net_edge=0.02)
    untested = evaluate_game(game, payload(uncertainty=None), min_net_edge=0.02)

    assert tested.status_counts.get(EvaluationStatus.ROBUST_POSITIVE_EV.value, 0) > 0
    assert untested.status_counts.get(EvaluationStatus.ROBUST_POSITIVE_EV.value, 0) == 0
    # ...and the contracts did not vanish; they moved to the honest bucket.
    assert untested.unaccounted == 0
    assert untested.eligible == tested.eligible


def test_every_recommendation_carries_its_sensitivity_evidence(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, payload(uncertainty=STATED), min_net_edge=0.02)
    for row in evaluation.rows:
        if row["status"] not in CANDIDATE_STATUSES:
            continue
        sensitivity = row["sensitivity"]
        for key in (
            "base_fair_probability",
            "fee_adjusted_breakeven",
            "base_net_edge",
            "fair_probability_low",
            "fair_probability_high",
            "net_edge_low",
            "net_edge_high",
            "sensitivity_bound",
            "robustness",
            "robustness_reason",
            "worst_scenario",
        ):
            assert key in sensitivity, f"{row['ticker']} has no {key}"
        assert row["bet_up_to_price"] is not None


def test_an_explicit_probability_without_a_range_cannot_be_robust(tmp_path):
    game = packet(tmp_path)
    tie = next(
        r["ticker"]
        for r in game["contracts"]
        if (r.get("semantics") or {}).get("requires") != "period_distribution"
    )
    handicap = payload(
        uncertainty=STATED,
        explicit_probabilities={tie: 0.99},
    )
    evaluation = evaluate_game(game, handicap, min_net_edge=0.02)
    row = next(r for r in evaluation.rows if r["ticker"] == tie)
    assert row["sensitivity"]["sensitivity_bound"] == SensitivityBound.NOT_TESTED.value
    assert row["status"] != EvaluationStatus.ROBUST_POSITIVE_EV.value


def test_an_explicit_probability_with_a_range_is_tested_against_it(tmp_path):
    game = packet(tmp_path)
    tie = next(
        r["ticker"]
        for r in game["contracts"]
        if (r.get("semantics") or {}).get("requires") != "period_distribution"
    )
    handicap = payload(
        uncertainty=STATED,
        explicit_probabilities={tie: 0.60},
        explicit_probability_ranges={tie: [0.40, 0.70]},
    )
    evaluation = evaluate_game(game, handicap, min_net_edge=0.02)
    row = next(r for r in evaluation.rows if r["ticker"] == tie)
    assert row["sensitivity"]["sensitivity_bound"] == SensitivityBound.STATED_RANGE.value
    assert row["sensitivity"]["scenarios_tested"] == 3


def test_a_range_with_no_stated_probability_is_refused():
    """A range is the uncertainty AROUND a number, not a substitute for one.
    Taking the midpoint would invent a probability nobody supplied."""
    with pytest.raises(HandicapValidationError, match="not a substitute"):
        parse_handicap(
            {
                "schema_version": "cfb_handicap_payload/2.0.0",
                "game_key": GAME_KEY,
                "teams": {"home": "Ole Miss", "away": "LSU"},
                "period_distributions": {},
                "explicit_probability_ranges": {"SOME-TICKER": [0.3, 0.5]},
            }
        )


def test_a_schema_1_payload_still_prices_everything_and_claims_nothing(tmp_path):
    """Backward compatibility that costs the claim, not the coverage."""
    game = packet(tmp_path)
    legacy = parse_handicap(
        {
            "schema_version": "cfb_handicap_payload/1.0.0",
            "game_key": GAME_KEY,
            "teams": {"home": "Ole Miss", "away": "LSU"},
            "period_distributions": {
                "full_game": {
                    "home_mean": 27.0,
                    "away_mean": 24.0,
                    "home_sd": 10.5,
                    "away_sd": 10.0,
                    "correlation": 0.1,
                }
            },
        }
    )
    assert legacy.is_legacy_point_estimate
    evaluation = evaluate_game(game, legacy, min_net_edge=0.02)
    assert evaluation.unaccounted == 0
    assert evaluation.status_counts.get(EvaluationStatus.ROBUST_POSITIVE_EV.value, 0) == 0


# ----------------------------------------------------- adversarial moves


@pytest.mark.parametrize(
    "shift,label",
    [(1.0, "one point"), (3.0, "a field goal"), (7.0, "a touchdown")],
)
def test_being_wrong_about_the_margin_moves_the_fair_value_measurably(tmp_path, shift, label):
    """Part of the adversarial set: show the system does not keep treating a
    sharp-looking probability as robust when the handicap moves under it."""
    game = packet(tmp_path)
    spread = next(
        r for r in game["contracts"] if (r.get("semantics") or {}).get("kind") == "spread"
    )
    base = PeriodDistribution(27.0, 24.0, 10.5, 10.0, 0.1)
    moved = base.perturbed(margin_shift=-shift)
    before = fair_from_distribution(spread["semantics"], base).value
    after = fair_from_distribution(spread["semantics"], moved).value
    assert before is not None and after is not None
    assert abs(before - after) > 0.0, f"{label} of margin error changed nothing"


def test_a_too_narrow_sd_assumption_shows_up_as_a_wider_edge_range(tmp_path):
    """A handicapper who believes the game is a coin flip with a 4-point
    standard deviation is making a much stronger claim than one who says 12,
    and the reported edge range is where that shows."""
    game = packet(tmp_path)
    narrow = parse_handicap(
        {
            "schema_version": "cfb_handicap_payload/2.0.0",
            "game_key": GAME_KEY,
            "teams": {"home": "Ole Miss", "away": "LSU"},
            "period_distributions": {
                "full_game": {
                    "home_mean": 27.0,
                    "away_mean": 24.0,
                    "home_sd": 4.0,
                    "away_sd": 4.0,
                    "correlation": 0.1,
                    "uncertainty": {"sd_scale_low": 0.9, "sd_scale_high": 2.5},
                }
            },
        }
    )
    evaluation = evaluate_game(game, narrow, min_net_edge=0.02)
    ranges = [
        row["sensitivity"]["net_edge_high"] - row["sensitivity"]["net_edge_low"]
        for row in evaluation.rows
        if row.get("sensitivity")
    ]
    assert ranges and max(ranges) > 0.05, (
        "a standard deviation that could be 2.5x wider must produce a visibly wide edge range"
    )
