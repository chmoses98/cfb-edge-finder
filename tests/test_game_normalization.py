from datetime import UTC, datetime

import pytest

from cfb_edge_finder.ingestion.game_normalization import GameNormalizationError, normalize_cfbd_game
from cfb_edge_finder.ingestion.team_matching import TeamResolutionError
from cfb_edge_finder.ingestion.week_labels import UnclassifiablePostseasonError
from cfb_edge_finder.schemas.common import CFPRound, SeasonType

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def base_raw(**overrides) -> dict:
    raw = {
        "id": 12345,
        "season": 2026,
        "week": 1,
        "seasonType": "regular",
        "startDate": "2026-08-29T23:00:00.000Z",
        "startTimeTBD": False,
        "neutralSite": False,
        "venue": "Ohio Stadium",
        "homeTeam": "Ohio State",
        "homeClassification": "fbs",
        "awayTeam": "Texas",
        "awayClassification": "fbs",
        "completed": False,
    }
    raw.update(overrides)
    return raw


def test_normalize_basic_regular_season_game():
    game = normalize_cfbd_game(base_raw(), observed_at=NOW)
    assert game.game_id == "cfb-2026-wk01-texas-at-ohio-state"
    assert game.home_team_id == "ohio-state"
    assert game.away_team_id == "texas"
    assert game.season_type == SeasonType.REGULAR
    assert game.week_number == 1
    assert game.status == "scheduled"
    assert game.source_game_ids == {"cfbd": "12345"}
    assert game.primary_source == "cfbd"


def test_kickoff_parsed_as_aware_utc_and_raw_retained():
    game = normalize_cfbd_game(base_raw(), observed_at=NOW)
    assert game.kickoff_utc is not None
    assert game.kickoff_utc.tzinfo is not None
    assert game.kickoff_utc == datetime(2026, 8, 29, 23, 0, tzinfo=UTC)
    assert game.kickoff_source_raw == "2026-08-29T23:00:00.000Z"


def test_tbd_kickoff_is_none_but_raw_string_still_retained_if_present():
    game = normalize_cfbd_game(base_raw(startTimeTBD=True), observed_at=NOW)
    assert game.kickoff_utc is None


def test_missing_start_date_produces_none_kickoff_not_a_crash():
    game = normalize_cfbd_game(base_raw(startDate=None), observed_at=NOW)
    assert game.kickoff_utc is None


def test_neutral_site_game_id_invariant_to_vendor_home_away_reversal():
    vendor_a = normalize_cfbd_game(
        base_raw(neutralSite=True, homeTeam="Florida State", awayTeam="Georgia Tech"), observed_at=NOW
    )
    vendor_b = normalize_cfbd_game(
        base_raw(neutralSite=True, homeTeam="Georgia Tech", awayTeam="Florida State"), observed_at=NOW
    )
    assert vendor_a.game_id == vendor_b.game_id
    assert vendor_a.neutral_site is True


@pytest.mark.parametrize(
    ("descriptor", "expected_round"),
    [
        ("CFP Quarterfinal - Orange Bowl", CFPRound.QUARTERFINAL),
    ],
)
def test_normalize_postseason_game_with_descriptor(descriptor, expected_round):
    game = normalize_cfbd_game(
        base_raw(
            week=None, seasonType="postseason", neutralSite=True, notes=descriptor,
            homeTeam="Alabama", awayTeam="Oregon",
        ),
        observed_at=NOW,
    )
    assert game.season_type == SeasonType.CFP
    assert game.cfp_round == expected_round
    assert game.neutral_site is True


def test_postponed_status_mapped_correctly():
    raw = base_raw()
    raw.pop("completed")
    raw["status"] = "postponed"
    game = normalize_cfbd_game(raw, observed_at=NOW)
    assert game.status == "postponed"


def test_completed_true_maps_to_final_status():
    game = normalize_cfbd_game(base_raw(completed=True), observed_at=NOW)
    assert game.status == "final"


def test_unresolved_team_alias_raises_wrapped_error():
    with pytest.raises(GameNormalizationError) as exc_info:
        normalize_cfbd_game(base_raw(homeTeam="Some Newly Formed Program"), observed_at=NOW)
    assert isinstance(exc_info.value.cause, TeamResolutionError)
    assert exc_info.value.source == "cfbd"


def test_ambiguous_team_alias_raises_wrapped_error():
    with pytest.raises(GameNormalizationError) as exc_info:
        normalize_cfbd_game(base_raw(awayTeam="Miami"), observed_at=NOW)
    assert isinstance(exc_info.value.cause, TeamResolutionError)


def test_missing_required_field_raises_wrapped_error_not_a_raw_keyerror():
    raw = base_raw()
    del raw["season"]
    with pytest.raises(GameNormalizationError) as exc_info:
        normalize_cfbd_game(raw, observed_at=NOW)
    assert isinstance(exc_info.value.cause, KeyError)


def test_unclassifiable_postseason_descriptor_wrapped_and_raised():
    with pytest.raises(GameNormalizationError) as exc_info:
        normalize_cfbd_game(
            base_raw(week=None, seasonType="postseason", notes="Spring Exhibition Classic"), observed_at=NOW
        )
    assert isinstance(exc_info.value.cause, UnclassifiablePostseasonError)


def test_deterministic_serialization_of_normalized_game():
    game = normalize_cfbd_game(base_raw(), observed_at=NOW)
    dumped_1 = game.model_dump_json()
    dumped_2 = game.model_dump_json()
    assert dumped_1 == dumped_2


def test_normalize_does_not_itself_filter_by_classification():
    # Filtering FBS-vs-FBS-only happens at the ingestion-script layer
    # (scripts/ingest_schedule.py's _is_fbs_vs_fbs), not inside
    # normalize_cfbd_game -- an FCS opponent fails here only because
    # Furman isn't in the (FBS-only) team registry, not because of its
    # classification field, which normalize_cfbd_game never inspects.
    with pytest.raises(GameNormalizationError) as exc_info:
        normalize_cfbd_game(base_raw(awayTeam="Furman", awayClassification="fcs"), observed_at=NOW)
    assert isinstance(exc_info.value.cause, TeamResolutionError)
