"""Correlation grouping: the tests that stop one game's ladder from
looking like many independent theses."""

from __future__ import annotations

import random

import pytest

from cfb_edge_finder.decision.portfolio import (
    EXPOSURE_LIMITS_ABSENT,
    UNRESOLVED_DIMENSION_LABEL,
    DependenceMagnitude,
    ThesisRelationship,
    build_portfolio_view,
    classify_relationship,
    dependence_magnitude,
    direction_conflicts,
    thesis_group_key,
)
from cfb_edge_finder.expression.exposure import build_exposure
from cfb_edge_finder.expression.taxonomy import ContractSemantics
from cfb_edge_finder.schemas.common import MarketFamily, Side

GAME = "cfb-2026-wk01-akron-at-wake-forest"
OTHER_GAME = "cfb-2026-wk02-akron-at-toledo"


def ml(team: Side, *, game: str = GAME, ticker: str | None = None) -> ContractSemantics:
    return ContractSemantics(
        market_ticker=ticker or f"{game}-ML-{team.value}",
        game_id=game,
        family=MarketFamily.MONEYLINE,
        team=team,
        side=None,
        threshold=None,
        semantic_operator=">",
    )


def spread(team: Side, threshold: float, *, game: str = GAME) -> ContractSemantics:
    return ContractSemantics(
        market_ticker=f"{game}-SPREAD-{team.value}-{threshold}",
        game_id=game,
        family=MarketFamily.SPREAD,
        team=team,
        side=None,
        threshold=threshold,
        semantic_operator=">",
    )


def total(threshold: float, *, game: str = GAME) -> ContractSemantics:
    return ContractSemantics(
        market_ticker=f"{game}-TOTAL-{threshold}",
        game_id=game,
        family=MarketFamily.TOTAL,
        team=None,
        side=Side.OVER,
        threshold=threshold,
        semantic_operator=">",
    )


def unresolved(name: str, *, game: str = GAME) -> ContractSemantics:
    return ContractSemantics(
        market_ticker=name,
        game_id=game,
        family=None,
        team=None,
        side=None,
        threshold=None,
        semantic_operator=None,
        parse_status="UNRESOLVED",
    )


# ------------------------------------------------- relationships


def test_moneyline_pair_is_the_same_event():
    """Home ML and away ML are one thesis with two spellings: YES on one
    settles exactly when NO on the other does."""
    assert classify_relationship(ml(Side.HOME), ml(Side.AWAY)) is (
        ThesisRelationship.EXACT_EQUIVALENT_EVENT
    )


def test_same_team_spread_ladder_is_nested_not_independent():
    assert classify_relationship(spread(Side.HOME, -3.5), spread(Side.HOME, -7.5)) is (
        ThesisRelationship.NESTED_LADDER_SAME_TEAM
    )


def test_moneyline_and_same_team_spread_share_the_margin_thesis():
    """A moneyline is the margin rung at zero. Treating it as its own
    dimension would hide that it moves with every spread on that team."""
    assert classify_relationship(ml(Side.HOME), spread(Side.HOME, -7.5)) is (
        ThesisRelationship.NESTED_LADDER_SAME_TEAM
    )


def test_opposing_teams_on_the_margin_are_offsetting_not_independent():
    assert classify_relationship(spread(Side.HOME, -3.5), spread(Side.AWAY, 10.5)) is (
        ThesisRelationship.OFFSETTING_SAME_DIMENSION
    )


def test_total_ladder_is_nested():
    assert classify_relationship(total(51.5), total(55.5)) is (
        ThesisRelationship.NESTED_LADDER_SAME_TOTAL
    )


def test_margin_and_total_in_one_game_are_related_with_unknown_magnitude():
    """The honest answer. A coefficient here would be invented."""
    relationship = classify_relationship(ml(Side.HOME), total(51.5))
    assert relationship is ThesisRelationship.SAME_GAME_DIFFERENT_DIMENSION
    assert dependence_magnitude(relationship) is (
        DependenceMagnitude.UNDETERMINED_PENDING_EMPIRICAL_MEASUREMENT
    )


def test_same_team_in_two_different_games_is_not_same_game_correlation():
    """Akron this week and Akron next week are separate football
    outcomes. Merging them would be the opposite error."""
    assert classify_relationship(ml(Side.HOME), ml(Side.HOME, game=OTHER_GAME)) is (
        ThesisRelationship.INDEPENDENT_DIFFERENT_GAMES
    )


def test_unresolved_semantics_are_never_called_independent_or_equivalent():
    for other in (ml(Side.HOME), total(51.5), unresolved("Y")):
        relationship = classify_relationship(unresolved("X"), other)
        assert relationship is ThesisRelationship.UNRESOLVED_SEMANTICS
        assert dependence_magnitude(relationship) is DependenceMagnitude.UNKNOWN_INCOMPLETE_SEMANTICS


def test_relationship_is_symmetric():
    contracts = [ml(Side.HOME), ml(Side.AWAY), spread(Side.HOME, -3.5), total(51.5), unresolved("X")]
    for a in contracts:
        for b in contracts:
            assert classify_relationship(a, b) is classify_relationship(b, a)


def test_every_relationship_has_a_declared_magnitude():
    for relationship in ThesisRelationship:
        assert isinstance(dependence_magnitude(relationship), DependenceMagnitude)


def test_no_relationship_carries_a_numeric_coefficient():
    """The enum members are labels. If one ever became a number, this
    fails -- which is the point."""
    for magnitude in DependenceMagnitude:
        with pytest.raises((ValueError, TypeError)):
            float(magnitude.value)


# ------------------------------------------------------ grouping


def test_twenty_contracts_from_one_game_are_not_twenty_theses():
    """The headline property. One game's full ladder plus its totals is
    two latent quantities, not twenty edges."""
    contracts = [ml(Side.HOME), ml(Side.AWAY)]
    contracts += [spread(Side.HOME, t) for t in (-14.5, -10.5, -7.5, -3.5, -1.5)]
    contracts += [spread(Side.AWAY, t) for t in (1.5, 3.5, 7.5, 10.5, 14.5)]
    contracts += [total(t) for t in (44.5, 47.5, 51.5, 55.5, 58.5, 61.5, 64.5, 67.5)]
    assert len(contracts) == 20

    view = build_portfolio_view(contracts)
    assert view.contract_count == 20
    assert view.distinct_theses == 2
    assert {g.dimension for g in view.exposure_groups} == {"MARGIN", "TOTAL"}


def test_team_is_not_part_of_the_thesis_key():
    """Home -3.5 and Away +3.5 read the same final margin. Splitting them
    by team would present one number as two theses."""
    assert thesis_group_key(spread(Side.HOME, -3.5)) == thesis_group_key(spread(Side.AWAY, 3.5))
    assert thesis_group_key(ml(Side.HOME)) == thesis_group_key(spread(Side.AWAY, 3.5))


def test_different_games_produce_different_theses():
    view = build_portfolio_view([ml(Side.HOME), ml(Side.HOME, game=OTHER_GAME)])
    assert view.distinct_theses == 2


def test_equivalence_groups_collide_the_two_spellings_of_one_event():
    view = build_portfolio_view([ml(Side.HOME), ml(Side.AWAY)])
    tickers = {g.market_tickers for g in view.equivalence_groups}
    expected = tuple(sorted([ml(Side.HOME).market_ticker, ml(Side.AWAY).market_ticker]))
    assert expected in tickers


def test_a_spread_ladder_produces_no_false_equivalences():
    """Different thresholds are different events. Over-claiming
    equivalence would silently merge distinct exposures."""
    view = build_portfolio_view([spread(Side.HOME, -3.5), spread(Side.HOME, -7.5)])
    assert view.equivalence_groups == []


def test_unresolved_contracts_are_held_together_not_split_into_singletons():
    """Splitting them would ASSERT independence nobody established."""
    view = build_portfolio_view([unresolved("X"), unresolved("Y"), unresolved("Z")])
    assert view.distinct_theses == 1
    assert view.exposure_groups[0].dimension == UNRESOLVED_DIMENSION_LABEL
    assert view.contains_unresolved_semantics
    assert view.unresolved_group_count == 1


def test_unresolved_contracts_do_not_merge_into_a_resolved_thesis():
    view = build_portfolio_view([ml(Side.HOME), unresolved("X")])
    assert view.distinct_theses == 2


def test_grouping_is_deterministic_under_input_reordering():
    contracts = [ml(Side.HOME), ml(Side.AWAY), spread(Side.HOME, -3.5), total(51.5), unresolved("X")]
    reference = build_portfolio_view(contracts)
    rng = random.Random(20260828)
    for _ in range(25):
        shuffled = contracts[:]
        rng.shuffle(shuffled)
        assert build_portfolio_view(shuffled) == reference


def test_duplicate_contracts_do_not_inflate_the_count():
    view = build_portfolio_view([ml(Side.HOME), ml(Side.HOME), ml(Side.HOME)])
    assert view.contract_count == 1
    assert view.distinct_theses == 1


def test_empty_input_produces_an_empty_view_not_an_error():
    view = build_portfolio_view([])
    assert view.distinct_theses == 0
    assert view.contract_count == 0
    assert view.limits_status == EXPOSURE_LIMITS_ABSENT


def test_group_lookup_finds_the_containing_group():
    contracts = [ml(Side.HOME), total(51.5)]
    view = build_portfolio_view(contracts)
    assert view.group_for(ml(Side.HOME).market_ticker).dimension == "MARGIN"
    assert view.group_for(total(51.5).market_ticker).dimension == "TOTAL"
    assert view.group_for("does-not-exist") is None


def test_game_groups_are_the_coarsest_level():
    view = build_portfolio_view([ml(Side.HOME), total(51.5), ml(Side.HOME, game=OTHER_GAME)])
    assert len(view.game_groups) == 2


# ------------------------------------- limits are absent, loudly


def test_the_view_carries_no_limits_and_says_so():
    view = build_portfolio_view([ml(Side.HOME)])
    assert view.limits_status == EXPOSURE_LIMITS_ABSENT
    assert not hasattr(view, "max_positions_per_game")
    assert not hasattr(view, "correlation_matrix")


def test_no_module_level_correlation_coefficient_exists():
    import cfb_edge_finder.decision.portfolio as portfolio

    numeric = {
        name: value
        for name, value in vars(portfolio).items()
        if isinstance(value, (int, float)) and not isinstance(value, bool) and not name.startswith("__")
    }
    assert numeric == {}, f"invented numeric constants: {numeric}"


# ---------------------------------------------------- conflicts


def test_direction_conflicts_flags_opposing_margin_exposure():
    exposures = [
        build_exposure(ml(Side.HOME), Side.YES),
        build_exposure(ml(Side.AWAY), Side.YES),
    ]
    conflicts = direction_conflicts(exposures)
    assert len(conflicts) == 1


def test_direction_conflicts_ignores_agreeing_exposure():
    exposures = [
        build_exposure(ml(Side.HOME), Side.YES),
        build_exposure(spread(Side.HOME, -3.5), Side.YES),
    ]
    assert direction_conflicts(exposures) == []


def test_direction_conflicts_ignores_different_games():
    exposures = [
        build_exposure(ml(Side.HOME), Side.YES),
        build_exposure(ml(Side.AWAY, game=OTHER_GAME), Side.YES),
    ]
    assert direction_conflicts(exposures) == []


def test_direction_conflicts_flags_over_against_under():
    exposures = [
        build_exposure(total(51.5), Side.YES),
        build_exposure(total(51.5), Side.NO),
    ]
    assert len(direction_conflicts(exposures)) == 1


def test_direction_conflicts_are_sorted_and_deduplicated():
    exposures = [
        build_exposure(ml(Side.HOME), Side.YES),
        build_exposure(ml(Side.AWAY), Side.YES),
        build_exposure(spread(Side.AWAY, 3.5), Side.YES),
    ]
    conflicts = direction_conflicts(exposures)
    assert conflicts == sorted(set(conflicts))
    for left, right in conflicts:
        assert left < right
