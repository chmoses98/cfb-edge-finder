"""Parity: the LIVE shadow transformation must be the SAME mathematical
candidate that was historically validated.

If these drift, the prospective test measures a different model from the
one that earned the right to be tested, and 2026 confirms nothing.
"""

from __future__ import annotations

import numpy as np
import pytest

from cfb_edge_finder.research.preseason.candidates import (
    CandidateSpec,
    FittedCandidate,
    apply_candidate,
)
from cfb_edge_finder.research.preseason.evaluation import GamePrediction
from cfb_edge_finder.research.preseason.shadow_prior import TALENT_BETA
from cfb_edge_finder.research.preseason.shadow_transform import (
    APPLIED_AFTER_MARGIN_CORRECTION,
    TOTAL_CHANNEL_UNCHANGED,
    historical_equivalent_shadow_probability,
    transform,
)

RNG = np.random.default_rng(20260828)
# A realistic corrected-margin distribution: CFB margins are wide and
# genuinely hit exactly zero, which is what makes tie handling matter.
MARGINS = np.round(RNG.normal(3.0, 17.0, 8000))

SPEC = CandidateSpec("talent_composite", "talent_composite", "frozen candidate")
FITTED = FittedCandidate(
    spec=SPEC, beta=TALENT_BETA, n_games=2183,
    development_seasons=(2021, 2022, 2023), mean_abs_differential=141.088,
)


def live(home_talent: float, away_talent: float, control_margin: float = 3.0):
    return transform(
        corrected_margin_samples=MARGINS,
        control_margin_corrected=control_margin,
        control_probability_canonical=0.61,
        control_expected_home=28.0,
        control_expected_away=25.0,
        home_talent=home_talent,
        away_talent=away_talent,
    )


def historical(differential: float, control_margin: float = 3.0) -> GamePrediction:
    base = GamePrediction(
        game_id="g", season=2026, week=1, home_win_probability=0.61,
        projected_margin=control_margin, projected_total=53.0,
        actual_home_margin=0, actual_total=0,
    )
    return apply_candidate(base, differential, FITTED, MARGINS)


# ---------------------------------------------- numeric parity


@pytest.mark.parametrize("home,away", [
    (900.0, 800.0), (800.0, 900.0), (1003.7, 563.9), (563.9, 1003.7),
    (700.0, 700.0), (985.2, 624.8),
])
def test_live_margin_matches_the_historical_candidate_exactly(home, away):
    """THE PARITY TEST. Same inputs, same delta, same shifted margin."""
    live_out = live(home, away)
    hist = historical(home - away)
    assert live_out.delta == pytest.approx(TALENT_BETA * (home - away), rel=1e-12)
    assert live_out.shadow_margin == pytest.approx(hist.projected_margin, rel=1e-12)


@pytest.mark.parametrize("home,away", [
    (900.0, 800.0), (800.0, 900.0), (1003.7, 563.9), (700.0, 700.0),
])
def test_live_shadow_probability_matches_the_historical_formula_exactly(home, away):
    live_out = live(home, away)
    hist = historical(home - away)
    assert live_out.shadow_probability == pytest.approx(hist.home_win_probability, rel=1e-12)
    # ...and matches the historical formula computed independently.
    assert live_out.shadow_probability == pytest.approx(
        historical_equivalent_shadow_probability(MARGINS, live_out.delta), rel=1e-12
    )


def test_the_frozen_beta_is_used_not_the_refit_value():
    """0.018993 was preregistered. 0.018898 is a later refit at a higher
    simulation count and must NOT silently replace it."""
    assert TALENT_BETA == 0.018993
    out = live(900.0, 800.0)
    assert out.beta == 0.018993
    assert out.delta == pytest.approx(0.018993 * 100.0)


# ------------------------------------------- transformation order


def test_the_delta_is_applied_after_the_c2_margin_correction():
    """Traced from the historical runner, which built its samples as
    `raw_margin + margin_delta` and then added the talent delta to that.

    The historical run could not distinguish the two orders because C.2
    was a no-op for every evaluated season (its artifact cutoff is
    AsOf(2026, 0)). For 2026 it becomes active, so the order is resolved
    from what the code DID."""
    assert APPLIED_AFTER_MARGIN_CORRECTION is True

    c2_delta = 2.5
    raw = np.array([-3.0, 0.0, 4.0, 10.0])
    corrected = raw + c2_delta
    out = transform(
        corrected_margin_samples=corrected,
        control_margin_corrected=float(np.mean(corrected)),
        control_probability_canonical=0.5,
        control_expected_home=28.0, control_expected_away=25.0,
        home_talent=900.0, away_talent=800.0,
    )
    # Shadow sees raw + c2 + talent, in that order.
    expected = float(np.mean((raw + c2_delta + out.delta) > 0))
    assert out.shadow_probability == pytest.approx(expected)


# --------------------------------------------- channel behaviour


@pytest.mark.parametrize("home,away", [(900.0, 800.0), (600.0, 1000.0), (750.0, 750.0)])
def test_total_is_exactly_unchanged(home, away):
    """The candidate is a margin-only prior. Home +delta/2 and away
    -delta/2 moves the margin by delta and the total by nothing."""
    out = live(home, away)
    assert TOTAL_CHANNEL_UNCHANGED is True
    assert out.shadow_total == pytest.approx(out.control_total, abs=1e-12)
    assert out.total_probabilities_identical
    assert (out.shadow_expected_home - out.shadow_expected_away) == pytest.approx(
        (out.control_expected_home - out.control_expected_away) + out.delta
    )


def test_variance_and_ordering_are_preserved_under_the_shift():
    """A constant shift changes neither spread nor monotonicity in the
    threshold."""
    out = live(900.0, 800.0)
    shifted = MARGINS + out.delta
    assert float(np.std(shifted)) == pytest.approx(float(np.std(MARGINS)), rel=1e-12)
    probs = [out.probability_margin_greater_than(t, MARGINS)[1] for t in (-14, -7, 0, 7, 14)]
    assert probs == sorted(probs, reverse=True)


def test_spread_probabilities_read_the_same_draws_for_both_arms():
    """Computing the arms from different arrays would let sampling noise
    masquerade as a model difference."""
    out = live(900.0, 800.0)
    control, shadow = out.probability_margin_greater_than(-3.5, MARGINS)
    assert control == pytest.approx(float(np.mean(MARGINS > -3.5)))
    assert shadow == pytest.approx(float(np.mean((MARGINS + out.delta) > -3.5)))
    assert shadow > control  # positive talent differential


# ------------------------------- the winner-channel inconsistency


def test_the_canonical_and_basis_control_probabilities_are_both_recorded():
    """The historical control probability split simulated ties while the
    shadow resolved them to AWAY. Live records both bases so the paired
    comparison is like-for-like and production's own number is still
    preserved verbatim."""
    out = live(900.0, 800.0)
    assert out.control_probability_canonical == 0.61
    assert out.control_probability_basis == pytest.approx(float(np.mean(MARGINS > 0)))
    assert out.control_probability_basis != out.control_probability_canonical


def test_the_paired_delta_uses_the_basis_not_the_canonical_probability():
    """So the two arms differ ONLY by the talent delta."""
    out = live(900.0, 800.0)
    assert out.shadow_minus_control_probability == pytest.approx(
        out.shadow_probability - out.control_probability_basis
    )


def test_at_zero_differential_the_shadow_equals_the_basis_control():
    """The cleanest parity statement: no talent difference, no model
    difference."""
    out = live(800.0, 800.0)
    assert out.delta == 0.0
    assert out.shadow_probability == pytest.approx(out.control_probability_basis)
    assert out.shadow_margin == pytest.approx(out.control_margin_corrected)
    assert out.shadow_minus_control_probability == pytest.approx(0.0)


def test_ties_resolve_to_away_matching_settlement():
    zeros = np.zeros(100)
    out = transform(
        corrected_margin_samples=zeros, control_margin_corrected=0.0,
        control_probability_canonical=0.5,
        control_expected_home=25.0, control_expected_away=25.0,
        home_talent=800.0, away_talent=800.0,
    )
    assert out.control_probability_basis == 0.0
    assert out.shadow_probability == 0.0
