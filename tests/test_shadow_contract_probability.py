"""Contract-oriented shadow probability: the family/orientation matrix.

*** THE DEFECT THESE TESTS LOCK OUT ***

The first live prospective capture computed ONE number per game --
`mean(control_margin_samples + delta > 0)`, i.e. P(HOME wins) -- and
wrote it onto every contract on that game. Real captured rows showed the
same value on both sides of a matchup (Boise State at Oregon: control
0.1185 / 0.8699, shadow 0.9315 / 0.9315), so the away-side delta compared
P(home) with P(away).

Reproducing across families showed the blast radius was wider than the
moneyline where it was first seen: SPREAD and TOTAL contracts received
that same winner probability too, because nothing looked at the
contract's proposition at all.

These tests assert the proposition, not just that "something changed".
"""

from __future__ import annotations

import pytest

from cfb_edge_finder.projections.distribution import (
    CONTINUITY_CORRECTION,
    margin_distribution,
    prob_away_covers,
    prob_away_win,
    prob_home_covers,
    prob_home_win,
    prob_over,
    prob_under,
)
from cfb_edge_finder.research.preseason.shadow_contract_pricing import (
    COMPARISON_BASIS,
    PROBABILITY_SEMANTICS_VERSION,
    price_contract_both_arms,
    shadow_game_distribution,
)
from cfb_edge_finder.research.preseason.shadow_prior import TALENT_BETA
from cfb_edge_finder.schemas.common import MarketFamily, Side
from cfb_edge_finder.schemas.projection import GameDistribution

# A frozen, deliberately lopsided game: home clearly better, so every
# orientation has a genuinely different answer and a test cannot pass by
# coincidence on a coin-flip matchup.
CONTROL = GameDistribution(
    home_mean=31.0, away_mean=17.0, home_sd=10.0, away_sd=9.0, correlation=0.1
)
TALENT_DIFFERENTIAL = 200.0
DELTA = TALENT_BETA * TALENT_DIFFERENTIAL  # ~3.8 points toward home


def price(family, side=None, threshold=None, team=None, delta=DELTA, control=CONTROL):
    return price_contract_both_arms(
        control_distribution=control,
        delta=delta,
        family=family,
        side=side,
        threshold=threshold,
        named_team_side=team,
    )


# ------------------------------------------------ the shifted distribution


def test_the_delta_moves_margin_by_exactly_delta_and_leaves_total_alone() -> None:
    shifted = shadow_game_distribution(CONTROL, DELTA)
    control_margin = CONTROL.home_mean - CONTROL.away_mean
    control_total = CONTROL.home_mean + CONTROL.away_mean
    assert (shifted.home_mean - shifted.away_mean) == pytest.approx(control_margin + DELTA)
    assert (shifted.home_mean + shifted.away_mean) == pytest.approx(control_total)


def test_variance_and_correlation_are_untouched() -> None:
    """The frozen candidate adjusts a mean. It makes no claim about
    spread, and silently widening one would be a different model."""
    shifted = shadow_game_distribution(CONTROL, DELTA)
    assert shifted.home_sd == CONTROL.home_sd
    assert shifted.away_sd == CONTROL.away_sd
    assert shifted.correlation == CONTROL.correlation


# ------------------------------------------------ WINNER


def test_winner_home_yes_is_probability_home_wins() -> None:
    result = price(MarketFamily.MONEYLINE, team=Side.HOME)
    assert result.basis == pytest.approx(prob_home_win(CONTROL))
    assert result.shadow == pytest.approx(prob_home_win(shadow_game_distribution(CONTROL, DELTA)))


def test_winner_away_yes_is_probability_away_wins() -> None:
    result = price(MarketFamily.MONEYLINE, team=Side.AWAY)
    assert result.basis == pytest.approx(prob_away_win(CONTROL))
    assert result.shadow == pytest.approx(prob_away_win(shadow_game_distribution(CONTROL, DELTA)))


def test_the_two_winner_sides_are_not_the_same_number() -> None:
    """The exact v1 defect, stated as an assertion."""
    home = price(MarketFamily.MONEYLINE, team=Side.HOME)
    away = price(MarketFamily.MONEYLINE, team=Side.AWAY)
    assert home.shadow != away.shadow
    assert home.basis != away.basis


def test_a_home_favouring_shift_helps_home_and_hurts_away() -> None:
    home = price(MarketFamily.MONEYLINE, team=Side.HOME)
    away = price(MarketFamily.MONEYLINE, team=Side.AWAY)
    assert home.shadow_minus_basis > 0
    assert away.shadow_minus_basis < 0


def test_tie_mass_is_the_canonical_pricers_own_not_a_new_convention() -> None:
    """Home and away winner probabilities do not sum to 1: the canonical
    pricer applies a continuity correction, leaving mass on the exact
    tie. The shadow inherits that rule rather than inventing one.

    The residual is NOT expected to be equal across the arms -- moving
    the mean moves density out of the band around zero, so a shifted
    distribution genuinely has less tie mass. What must hold is that each
    arm's own three outcomes account for exactly all the probability,
    under the same continuity correction."""
    home = price(MarketFamily.MONEYLINE, team=Side.HOME)
    away = price(MarketFamily.MONEYLINE, team=Side.AWAY)
    shifted = shadow_game_distribution(CONTROL, DELTA)

    for label, dist, p_home, p_away in (
        ("basis", CONTROL, home.basis, away.basis),
        ("shadow", shifted, home.shadow, away.shadow),
    ):
        assert p_home + p_away < 1.0, f"{label} left no mass on the tie"
        margin = margin_distribution(dist)
        tie_mass = margin.cdf(CONTINUITY_CORRECTION) - margin.cdf(-CONTINUITY_CORRECTION)
        assert p_home + p_away + tie_mass == pytest.approx(1.0), label

    # The shift moves density away from the tie band, which is why the
    # residuals differ -- state it so a future reader does not "fix" it.
    assert (home.shadow + away.shadow) > (home.basis + away.basis)


# ------------------------------------------------ SPREAD


@pytest.mark.parametrize("threshold", [3.5, 7.5, 14.5])
def test_spread_home_side_is_home_winning_by_strictly_more_than_threshold(threshold) -> None:
    result = price(MarketFamily.SPREAD, threshold=threshold, team=Side.HOME)
    # price_parsed_contract converts a named-team threshold T to
    # home_line = -T for the home side (see market_pricing's derivation).
    assert result.basis == pytest.approx(prob_home_covers(CONTROL, -threshold))
    assert result.shadow == pytest.approx(
        prob_home_covers(shadow_game_distribution(CONTROL, DELTA), -threshold)
    )


@pytest.mark.parametrize("threshold", [3.5, 7.5, 14.5])
def test_spread_away_side_is_away_winning_by_strictly_more_than_threshold(threshold) -> None:
    result = price(MarketFamily.SPREAD, threshold=threshold, team=Side.AWAY)
    assert result.basis == pytest.approx(prob_away_covers(CONTROL, threshold))
    assert result.shadow == pytest.approx(
        prob_away_covers(shadow_game_distribution(CONTROL, DELTA), threshold)
    )


def test_the_two_spread_sides_are_not_accidentally_identical() -> None:
    home = price(MarketFamily.SPREAD, threshold=7.5, team=Side.HOME)
    away = price(MarketFamily.SPREAD, threshold=7.5, team=Side.AWAY)
    assert home.shadow != away.shadow
    assert home.shadow_minus_basis > 0
    assert away.shadow_minus_basis < 0


def test_a_higher_spread_threshold_is_never_more_likely() -> None:
    """Strict `>` semantics: P(win by more than 14.5) <= P(win by more
    than 7.5). A sign error in the line conversion inverts this."""
    probs = [price(MarketFamily.SPREAD, threshold=t, team=Side.HOME).shadow for t in (3.5, 7.5, 14.5)]
    assert probs == sorted(probs, reverse=True)


def test_a_spread_contract_is_not_given_the_winner_probability() -> None:
    """The v1 behaviour, asserted against."""
    winner = price(MarketFamily.MONEYLINE, team=Side.HOME)
    spread = price(MarketFamily.SPREAD, threshold=7.5, team=Side.HOME)
    assert spread.shadow != winner.shadow


# ------------------------------------------------ TOTAL


@pytest.mark.parametrize("line", [41.5, 48.5, 55.5])
def test_total_over_is_the_game_total_strictly_exceeding_the_line(line) -> None:
    result = price(MarketFamily.TOTAL, side=Side.OVER, threshold=line)
    assert result.basis == pytest.approx(prob_over(CONTROL, line))


@pytest.mark.parametrize("line", [41.5, 48.5, 55.5])
def test_total_under_is_priced_as_under_not_as_a_winner(line) -> None:
    result = price(MarketFamily.TOTAL, side=Side.UNDER, threshold=line)
    assert result.basis == pytest.approx(prob_under(CONTROL, line))


def test_the_talent_shift_leaves_totals_exactly_unchanged() -> None:
    """A RESULT, not an oversight. The frozen candidate moves margin and
    preserves total, so it makes no prediction about totals at all. If
    this ever stops holding, the candidate's total channel changed."""
    for line in (41.5, 48.5, 55.5):
        for side in (Side.OVER, Side.UNDER):
            result = price(MarketFamily.TOTAL, side=side, threshold=line)
            assert result.shadow == pytest.approx(result.basis)
            assert result.shadow_minus_basis == pytest.approx(0.0)


def test_a_total_contract_is_not_given_the_winner_probability() -> None:
    winner = price(MarketFamily.MONEYLINE, team=Side.HOME)
    total = price(MarketFamily.TOTAL, side=Side.OVER, threshold=48.5)
    assert total.shadow != winner.shadow


# ------------------------------------------------ THE PARITY REGRESSION


ALL_ORIENTATIONS = [
    ("winner home", MarketFamily.MONEYLINE, None, None, Side.HOME),
    ("winner away", MarketFamily.MONEYLINE, None, None, Side.AWAY),
    ("spread home 3.5", MarketFamily.SPREAD, None, 3.5, Side.HOME),
    ("spread away 3.5", MarketFamily.SPREAD, None, 3.5, Side.AWAY),
    ("spread home 14.5", MarketFamily.SPREAD, None, 14.5, Side.HOME),
    ("spread away 14.5", MarketFamily.SPREAD, None, 14.5, Side.AWAY),
    ("total over 48.5", MarketFamily.TOTAL, Side.OVER, 48.5, None),
    ("total under 48.5", MarketFamily.TOTAL, Side.UNDER, 48.5, None),
]


@pytest.mark.parametrize(
    "label,family,side,threshold,team", ALL_ORIENTATIONS, ids=[c[0] for c in ALL_ORIENTATIONS]
)
def test_zero_talent_differential_makes_the_arms_identical(
    label, family, side, threshold, team
) -> None:
    """THE regression test for this repair.

    With no talent differential the shadow IS the control. If any
    orientation disagrees, something other than the talent shift is
    differing between the arms -- an orientation flip, a tie-rule
    difference, or a market input read differently on one side."""
    result = price(family, side=side, threshold=threshold, team=team, delta=0.0)
    assert result.basis == result.shadow, f"{label}: arms differ with zero talent differential"
    assert result.shadow_minus_basis == 0.0


@pytest.mark.parametrize(
    "label,family,side,threshold,team", ALL_ORIENTATIONS, ids=[c[0] for c in ALL_ORIENTATIONS]
)
def test_every_orientation_prices_both_arms_or_neither(
    label, family, side, threshold, team
) -> None:
    """One armed pricing would produce a delta against a missing
    counterfactual."""
    result = price(family, side=side, threshold=threshold, team=team)
    assert (result.basis is None) == (result.shadow is None)
    assert result.basis is not None


def test_semantics_metadata_is_attached() -> None:
    result = price(MarketFamily.MONEYLINE, team=Side.HOME)
    assert result.semantics_version == PROBABILITY_SEMANTICS_VERSION
    assert result.comparison_basis == COMPARISON_BASIS


def test_an_unresolved_side_prices_neither_arm() -> None:
    """A winner contract whose named team never resolved to a side must
    not silently fall back to 'home'."""
    result = price(MarketFamily.MONEYLINE, team=None)
    assert result.basis is None and result.shadow is None
