import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cfb_edge_finder.ingestion.game_normalization import (
    GameNormalizationError,
    away_classification,
    home_classification,
    normalize_cfbd_game,
)
from cfb_edge_finder.ingestion.team_matching import TeamResolutionError
from cfb_edge_finder.ingestion.week_labels import UnclassifiablePostseasonError
from cfb_edge_finder.schemas.common import CFPRound, SeasonType

NOW = datetime(2026, 8, 1, tzinfo=UTC)

LIVE_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/cfb_edge_finder/data/fixtures/cfbd_live_verified_2026_sample.json"
)


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
    # Filtering "no FBS team involved at all" happens at the
    # ingestion-script layer (scripts/ingest_schedule.py's
    # _is_fbs_involved), not inside normalize_cfbd_game.
    game = normalize_cfbd_game(base_raw(awayTeam="Furman", awayClassification="fcs"), observed_at=NOW)
    assert game.home_team_id == "ohio-state"
    assert game.away_team_id == "furman"


# --- FBS-vs-FCS inclusion policy ---


def test_fcs_opponent_gets_a_deterministic_slug_not_dropped():
    game = normalize_cfbd_game(base_raw(awayTeam="Furman", awayClassification="fcs"), observed_at=NOW)
    assert game.away_team_id == "furman"
    game_again = normalize_cfbd_game(base_raw(awayTeam="Furman", awayClassification="fcs"), observed_at=NOW)
    assert game_again.away_team_id == game.away_team_id  # deterministic, not random


def test_unresolved_fbs_team_still_fails_loud_even_with_classification_present():
    # An unrecognized name with classification == "fbs" must NOT be
    # silently slugged -- that's exactly the "unrecognized FBS program"
    # case this project wants surfaced.
    with pytest.raises(GameNormalizationError) as exc_info:
        normalize_cfbd_game(
            base_raw(homeTeam="Some Newly Formed Program", homeClassification="fbs"), observed_at=NOW
        )
    assert isinstance(exc_info.value.cause, TeamResolutionError)


def test_ambiguous_alias_still_fails_loud_regardless_of_classification():
    # Ambiguity (bare "Miami") must never be downgraded to a generated
    # slug just because classification says non-FBS -- it's a genuine
    # identity risk independent of subdivision.
    with pytest.raises(GameNormalizationError) as exc_info:
        normalize_cfbd_game(base_raw(awayTeam="Miami", awayClassification="fcs"), observed_at=NOW)
    assert isinstance(exc_info.value.cause, TeamResolutionError)
    from cfb_edge_finder.teams import AmbiguousTeamAliasError

    assert isinstance(exc_info.value.cause.cause, AmbiguousTeamAliasError)


def test_missing_classification_on_unresolved_team_still_fails_loud():
    # No classification info at all (key absent) must default to the
    # strict/fail-loud path, not the lenient FCS-slugging path.
    raw = base_raw(homeTeam="Some Newly Formed Program")
    del raw["homeClassification"]
    with pytest.raises(GameNormalizationError):
        normalize_cfbd_game(raw, observed_at=NOW)


# --- Defensive schema field-name lookups (genuine CFBD schema verification found disagreement) ---


def test_home_division_key_accepted_as_classification_fallback():
    raw = base_raw()
    del raw["homeClassification"]
    raw["homeDivision"] = "fbs"
    game = normalize_cfbd_game(raw, observed_at=NOW)
    assert game.home_team_id == "ohio-state"


def test_start_time_tbd_lowercase_variant_accepted():
    raw = base_raw()
    del raw["startTimeTBD"]
    raw["startTimeTbd"] = True
    game = normalize_cfbd_game(raw, observed_at=NOW)
    assert game.kickoff_utc is None


# --- Structured playoff field (preferred over notes heuristic) ---


def test_structured_playoff_object_preferred_over_notes_heuristic():
    game = normalize_cfbd_game(
        base_raw(
            week=None,
            seasonType="postseason",
            neutralSite=True,
            homeTeam="Alabama",
            awayTeam="Oregon",
            notes="some irrelevant free text that would fail the heuristic",
            playoff={"competition": "cfp", "round": "quarterfinal", "bowl_name": "Orange Bowl"},
        ),
        observed_at=NOW,
    )
    assert game.season_type == SeasonType.CFP
    assert game.cfp_round == CFPRound.QUARTERFINAL
    assert game.game_id == "cfb-2026-cfp-quarterfinal-orange-bowl-alabama-vs-oregon"


def test_structured_playoff_championship_maps_to_national_championship():
    game = normalize_cfbd_game(
        base_raw(
            week=None, seasonType="postseason", neutralSite=True, homeTeam="Alabama", awayTeam="Oregon",
            playoff={"competition": "cfp", "round": "championship"},
        ),
        observed_at=NOW,
    )
    assert game.cfp_round == CFPRound.NATIONAL_CHAMPIONSHIP


def test_playoff_object_absent_falls_back_to_notes_heuristic():
    game = normalize_cfbd_game(
        base_raw(
            week=None, seasonType="postseason", neutralSite=True, homeTeam="Alabama", awayTeam="Oregon",
            notes="CFP Quarterfinal - Orange Bowl",
        ),
        observed_at=NOW,
    )
    assert game.cfp_round == CFPRound.QUARTERFINAL


def test_playoff_object_with_unrecognized_round_fails_loud():
    with pytest.raises(GameNormalizationError):
        normalize_cfbd_game(
            base_raw(
                week=None, seasonType="postseason", neutralSite=True, homeTeam="Alabama", awayTeam="Oregon",
                playoff={"competition": "cfp", "round": "some_future_round_format"},
            ),
            observed_at=NOW,
        )


# --- Genuine live-verified fixture (records copied verbatim from a real,
# authenticated CFBD /games?year=2026 response -- see
# cfbd_live_verified_2026_sample.PROVENANCE.md) run through the actual
# production normalization path, not a separate throwaway parser.


def _load_live_fixture() -> list[dict]:
    return json.loads(LIVE_FIXTURE_PATH.read_text())


def test_live_fixture_file_has_the_four_expected_records():
    raw_games = _load_live_fixture()
    assert {g["id"] for g in raw_games} == {401864494, 401866409, 401856766, 401907702}


def test_live_fbs_vs_fbs_game_normalizes_once_alias_is_known():
    raw_games = _load_live_fixture()
    raw = next(g for g in raw_games if g["id"] == 401864494)
    # "San José State" (accented) is the exact genuine string CFBD reports;
    # it must resolve now that the registry alias has been added.
    assert raw["awayTeam"] == "San José State"
    game = normalize_cfbd_game(raw, observed_at=NOW)
    assert game.home_team_id == "usc"
    assert game.away_team_id == "san-jose-state"
    assert game.status == "scheduled"


def test_live_fbs_vs_fcs_game_retained_not_dropped():
    raw_games = _load_live_fixture()
    raw = next(g for g in raw_games if g["id"] == 401866409)
    assert away_classification(raw) == "fcs"
    game = normalize_cfbd_game(raw, observed_at=NOW)
    assert game.home_team_id == "buffalo"
    assert game.away_team_id == "ualbany"


def test_live_neutral_site_fbs_vs_fbs_game_normalizes_correctly():
    raw_games = _load_live_fixture()
    raw = next(g for g in raw_games if g["id"] == 401856766)
    game = normalize_cfbd_game(raw, observed_at=NOW)
    assert game.neutral_site is True
    assert {game.home_team_id, game.away_team_id} == {"tcu", "north-carolina"}


def test_live_division_ii_game_is_not_fbs_involved_and_would_be_filtered():
    # This record is a genuine negative case: neither side is FBS
    # (home is Division II, away has no classification at all), so the
    # schedule-ingestion layer's _is_fbs_involved() filter must exclude
    # it entirely -- normalize_cfbd_game() itself doesn't filter (see
    # test_normalize_does_not_itself_filter_by_classification above), but
    # the classification helpers it's built from must report this
    # correctly so that filter can do its job.
    raw_games = _load_live_fixture()
    raw = next(g for g in raw_games if g["id"] == 401907702)
    assert home_classification(raw) != "fbs"
    assert away_classification(raw) != "fbs"
