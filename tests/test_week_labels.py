import pytest

from cfb_edge_finder.ids import validate_week_label
from cfb_edge_finder.ingestion.week_labels import UnclassifiablePostseasonError, derive_week_metadata
from cfb_edge_finder.schemas.common import CFPRound, SeasonType


def test_week_0_is_regular_season():
    meta = derive_week_metadata(season_type_raw="regular", week_raw=0)
    assert meta.week_label == "wk00"
    assert meta.season_type == SeasonType.REGULAR
    assert meta.week_number == 0
    validate_week_label(meta.week_label)


@pytest.mark.parametrize("week", [1, 7, 14, 15])
def test_regular_weeks_produce_deterministic_labels(week):
    meta = derive_week_metadata(season_type_raw="regular", week_raw=week)
    assert meta.week_label == f"wk{week:02d}"
    assert meta.week_number == week
    assert meta.season_type == SeasonType.REGULAR


def test_regular_week_accepts_string_digit():
    meta = derive_week_metadata(season_type_raw="regular", week_raw="3")
    assert meta.week_label == "wk03"


def test_regular_season_missing_week_fails_loud():
    with pytest.raises(ValueError, match="missing a week number"):
        derive_week_metadata(season_type_raw="regular", week_raw=None)


def test_regular_season_malformed_week_fails_loud():
    with pytest.raises(ValueError, match="not a valid integer"):
        derive_week_metadata(season_type_raw="regular", week_raw="not-a-number")


def test_conference_championship_classification():
    meta = derive_week_metadata(season_type_raw="postseason", week_raw=None, postseason_descriptor="SEC Championship")
    assert meta.season_type == SeasonType.CONFERENCE_CHAMPIONSHIP
    assert meta.week_label == "conf-champ-sec"
    assert meta.cfp_round is None


def test_unknown_conference_in_championship_descriptor_fails_loud():
    with pytest.raises(UnclassifiablePostseasonError, match="no known conference"):
        derive_week_metadata(season_type_raw="postseason", week_raw=None, postseason_descriptor="Galactic Championship")


def test_bowl_classification_retains_display_name():
    meta = derive_week_metadata(season_type_raw="postseason", week_raw=None, postseason_descriptor="Duke's Mayo Bowl")
    assert meta.season_type == SeasonType.BOWL
    assert meta.week_label.startswith("bowl-")
    assert meta.bowl_display_name == "Duke's Mayo Bowl"
    validate_week_label(meta.week_label)


@pytest.mark.parametrize(
    ("descriptor", "expected_round"),
    [
        ("CFP First Round", CFPRound.FIRST_ROUND),
        ("CFP Quarterfinal - Orange Bowl", CFPRound.QUARTERFINAL),
        ("CFP Semifinal - Cotton Bowl", CFPRound.SEMIFINAL),
        ("College Football Playoff National Championship", CFPRound.NATIONAL_CHAMPIONSHIP),
    ],
)
def test_cfp_round_classification(descriptor, expected_round):
    meta = derive_week_metadata(season_type_raw="postseason", week_raw=None, postseason_descriptor=descriptor)
    assert meta.season_type == SeasonType.CFP
    assert meta.cfp_round == expected_round
    assert meta.week_label.startswith("cfp-")
    validate_week_label(meta.week_label)


def test_cfp_national_championship_label_has_no_redundant_boilerplate():
    meta = derive_week_metadata(
        season_type_raw="postseason",
        week_raw=None,
        postseason_descriptor="College Football Playoff National Championship",
    )
    assert meta.week_label == "cfp-national-championship"


def test_cfp_quarterfinal_label_carries_only_distinguishing_remainder():
    meta = derive_week_metadata(
        season_type_raw="postseason", week_raw=None, postseason_descriptor="CFP Quarterfinal - Orange Bowl"
    )
    assert meta.week_label == "cfp-quarterfinal-orange-bowl"
    assert "cfp-cfp" not in meta.week_label


def test_postseason_without_descriptor_fails_loud():
    with pytest.raises(UnclassifiablePostseasonError):
        derive_week_metadata(season_type_raw="postseason", week_raw=None, postseason_descriptor=None)


def test_unrecognized_descriptor_fails_loud_rather_than_guessing():
    with pytest.raises(UnclassifiablePostseasonError):
        derive_week_metadata(
            season_type_raw="postseason", week_raw=None, postseason_descriptor="Spring Exhibition Classic"
        )


def test_unrecognized_season_type_fails_loud():
    with pytest.raises(ValueError, match="unrecognized season_type_raw"):
        derive_week_metadata(season_type_raw="preseason", week_raw=1)
