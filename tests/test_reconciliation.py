from datetime import UTC, datetime

import pytest

from cfb_edge_finder.ids import canonical_game_id
from cfb_edge_finder.ingestion.reconciliation import (
    IdentityMismatchError,
    cross_check_secondary,
    detect_reschedule,
    find_match,
    merge_same_game_update,
)
from cfb_edge_finder.schemas.common import SeasonType
from cfb_edge_finder.schemas.game import GameRecord
from cfb_edge_finder.schemas.observation import RawGameObservation

NOW = datetime(2026, 8, 1, tzinfo=UTC)
LATER = datetime(2026, 8, 5, tzinfo=UTC)


def make_game(**overrides) -> GameRecord:
    defaults = dict(
        game_id=canonical_game_id(2026, "wk01", "texas", "ohio-state"),
        season=2026,
        week_label="wk01",
        season_type=SeasonType.REGULAR,
        home_team_id="ohio-state",
        away_team_id="texas",
        home_team_name="Ohio State",
        away_team_name="Texas",
        neutral_site=False,
        kickoff_utc=NOW,
        venue="Ohio Stadium",
        source_game_ids={"cfbd": "12345"},
        primary_source="cfbd",
        discovered_at=NOW,
        last_updated_at=NOW,
    )
    defaults.update(overrides)
    return GameRecord(**defaults)


# --- merge_same_game_update ---


def test_merge_same_game_update_takes_incoming_mutable_fields():
    existing = make_game(venue="Old Venue Name", discovered_at=NOW)
    incoming = make_game(venue="Ohio Stadium (Renovated)", kickoff_utc=LATER, discovered_at=LATER)
    merged = merge_same_game_update(existing, incoming)
    assert merged.venue == "Ohio Stadium (Renovated)"
    assert merged.kickoff_utc == LATER
    assert merged.discovered_at == NOW  # earliest discovery preserved


def test_merge_same_game_update_requires_matching_game_id():
    existing = make_game()
    other = make_game(game_id=canonical_game_id(2026, "wk02", "texas", "ohio-state"), week_label="wk02")
    with pytest.raises(ValueError, match="share a game_id"):
        merge_same_game_update(existing, other)


def test_merge_same_game_update_rejects_identity_field_drift():
    existing = make_game()
    # Same game_id string but a home_team_id that disagrees -- should be
    # impossible by construction, and this must be caught, not merged.
    corrupted = existing.model_copy(update={"home_team_id": "michigan"})
    with pytest.raises(IdentityMismatchError):
        merge_same_game_update(existing, corrupted)


def test_merge_preserves_previous_game_id_if_either_side_has_one():
    existing = make_game(previous_game_id="cfb-2026-wk00-texas-at-ohio-state")
    incoming = make_game()
    merged = merge_same_game_update(existing, incoming)
    assert merged.previous_game_id == "cfb-2026-wk00-texas-at-ohio-state"


# --- detect_reschedule ---


def test_detect_reschedule_stamps_previous_game_id_on_week_change():
    old_game_id = canonical_game_id(2026, "wk03", "texas", "ohio-state")
    previous_ids = {"12345": old_game_id}
    incoming = make_game()  # wk01, same vendor id 12345
    result = detect_reschedule(previous_ids, incoming, source="cfbd")
    assert result.previous_game_id == old_game_id
    assert result.game_id != old_game_id


def test_detect_reschedule_no_op_when_vendor_id_unseen_before():
    result = detect_reschedule({}, make_game(), source="cfbd")
    assert result.previous_game_id is None


def test_detect_reschedule_no_op_when_game_id_unchanged():
    same_id = canonical_game_id(2026, "wk01", "texas", "ohio-state")
    previous_ids = {"12345": same_id}
    result = detect_reschedule(previous_ids, make_game(), source="cfbd")
    assert result.previous_game_id is None


def test_detect_reschedule_no_op_when_source_not_present_on_incoming():
    incoming = make_game(source_game_ids={"espn": "999"})
    result = detect_reschedule({"12345": "some-other-id"}, incoming, source="cfbd")
    assert result.previous_game_id is None


# --- find_match / cross_check_secondary ---


def make_observation(**overrides) -> RawGameObservation:
    defaults = dict(
        source="espn",
        source_game_id="e-1",
        observed_at=NOW,
        season=2026,
        raw_week=1,
        raw_season_type="regular",
        raw_home_team="Ohio State",
        raw_away_team="Texas",
        raw_neutral_site=False,
        raw_venue="Ohio Stadium",
        raw_kickoff="2026-08-29T23:00:00Z",
    )
    defaults.update(overrides)
    return RawGameObservation(**defaults)


def test_find_match_by_season_and_unordered_teams():
    game = make_game()
    games_by_id = {game.game_id: game}
    observation = make_observation(raw_home_team="Texas", raw_away_team="Ohio State")  # reversed order
    match = find_match(games_by_id, observation, resolved_home="texas", resolved_away="ohio-state")
    assert match is not None
    assert match.game_id == game.game_id


def test_find_match_returns_none_for_unrelated_teams():
    game = make_game()
    games_by_id = {game.game_id: game}
    observation = make_observation(raw_home_team="Michigan", raw_away_team="Michigan State")
    match = find_match(games_by_id, observation, resolved_home="michigan-state", resolved_away="michigan")
    assert match is None


def test_cross_check_fills_missing_venue_without_overwriting_present_value():
    primary = make_game(venue=None)
    observation = make_observation(raw_venue="Ohio Stadium")
    updated, conflict = cross_check_secondary(primary, observation, source="espn")
    assert updated.venue == "Ohio Stadium"
    assert conflict is None


def test_cross_check_reports_conflict_never_silently_overwrites():
    primary = make_game(venue="Ohio Stadium")
    observation = make_observation(raw_venue="A Different Venue Entirely")
    updated, conflict = cross_check_secondary(primary, observation, source="espn")
    assert updated.venue == "Ohio Stadium"  # untouched
    assert conflict is not None
    assert conflict.conflicts[0].field == "venue"
    assert conflict.resolution is None  # unresolved, not auto-picked


def test_cross_check_reports_neutral_site_disagreement():
    primary = make_game(neutral_site=False)
    observation = make_observation(raw_neutral_site=True)
    updated, conflict = cross_check_secondary(primary, observation, source="espn")
    assert updated.neutral_site is False  # untouched -- neutral_site is identity-bearing, never silently changed
    assert conflict is not None
    assert any(c.field == "neutral_site" for c in conflict.conflicts)


def test_cross_check_no_conflict_when_sources_agree():
    primary = make_game(venue="Ohio Stadium", neutral_site=False)
    observation = make_observation(raw_venue="Ohio Stadium", raw_neutral_site=False)
    updated, conflict = cross_check_secondary(primary, observation, source="espn")
    assert conflict is None
    assert updated == primary
