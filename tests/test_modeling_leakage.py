import pytest

from cfb_edge_finder.modeling.leakage import AsOf, LeakageError, assert_strictly_before, postseason_week_rank
from cfb_edge_finder.schemas.common import CFPRound, SeasonType


def test_asof_orders_by_season_then_week():
    assert AsOf(season=2024, week=15) < AsOf(season=2025, week=0)
    assert AsOf(season=2025, week=3) < AsOf(season=2025, week=4)


def test_is_strictly_before():
    assert AsOf(season=2025, week=3).is_strictly_before(AsOf(season=2025, week=4))
    assert not AsOf(season=2025, week=4).is_strictly_before(AsOf(season=2025, week=4))
    assert not AsOf(season=2025, week=5).is_strictly_before(AsOf(season=2025, week=4))


def test_assert_strictly_before_passes_for_prior_row():
    assert_strictly_before(AsOf(season=2025, week=3), AsOf(season=2025, week=4), context="test")


def test_assert_strictly_before_raises_for_same_week():
    with pytest.raises(LeakageError):
        assert_strictly_before(AsOf(season=2025, week=4), AsOf(season=2025, week=4), context="test")


def test_assert_strictly_before_raises_for_future_row():
    with pytest.raises(LeakageError):
        assert_strictly_before(AsOf(season=2025, week=6), AsOf(season=2025, week=4), context="test")


def test_postseason_week_rank_conference_championship_and_bowl_after_regular_season():
    reg = postseason_week_rank
    from cfb_edge_finder.modeling.leakage import REGULAR_SEASON_WEEK_CEILING

    assert reg(SeasonType.CONFERENCE_CHAMPIONSHIP) > REGULAR_SEASON_WEEK_CEILING
    assert reg(SeasonType.BOWL) > reg(SeasonType.CONFERENCE_CHAMPIONSHIP)


def test_postseason_week_rank_cfp_rounds_are_in_bracket_order():
    ranks = [
        postseason_week_rank(SeasonType.CFP, CFPRound.FIRST_ROUND),
        postseason_week_rank(SeasonType.CFP, CFPRound.QUARTERFINAL),
        postseason_week_rank(SeasonType.CFP, CFPRound.SEMIFINAL),
        postseason_week_rank(SeasonType.CFP, CFPRound.NATIONAL_CHAMPIONSHIP),
    ]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == 4


def test_postseason_week_rank_cfp_without_round_fails_loud():
    with pytest.raises(LeakageError):
        postseason_week_rank(SeasonType.CFP, None)


def test_postseason_week_rank_regular_season_type_fails_loud():
    with pytest.raises(LeakageError):
        postseason_week_rank(SeasonType.REGULAR)
