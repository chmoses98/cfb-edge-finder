"""Capture resilience: a CFBD quota outage must not disable
deadline-safe capture, and a known-safe degraded state must not produce a
red run every five minutes.

This is the regression matrix for the 2026-09-03 outage, whose two
separate failures were:

  1. the football-state artifact's SLOW half (model inputs, 14-day bound)
     was held hostage to its FRESH half (kickoff clock, 6h bound), so a
     9.6h-old schedule with no way to refresh it returned state=None and
     killed every capture;
  2. the resulting fail-closed path returned 1 unconditionally, so one
     already-known, already-acknowledged condition generated 288 red runs
     and 288 emails a day.

The tests below map one-to-one onto the mission's test matrix and are
grouped by the section they prove. Nothing here weakens a guard: the
6-hour staleness bound, the pre-kickoff clock guard and the exact-match
identity rules are asserted to still reject, per game, everything they
rejected before.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import requests

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tests"))
sys.path.insert(0, str(_ROOT / "scripts"))

from scan_harness import FBS_TEAMS, SEASON  # noqa: E402

from cfb_edge_finder.data.espn_schedule_client import EspnScheduleClient, ScoreboardFetch  # noqa: E402
from cfb_edge_finder.ids import canonical_game_id  # noqa: E402
from cfb_edge_finder.research import football_state, operational_state, schedule_state  # noqa: E402
from cfb_edge_finder.research.health import Diagnostic, Severity  # noqa: E402
from cfb_edge_finder.research.scan_logic import (  # noqa: E402
    MAX_SCHEDULE_STALENESS_HOURS,
    StaleScheduleGuardError,
    guard_capture_allowed,
)
from cfb_edge_finder.schemas.game import GameRecord  # noqa: E402
from cfb_edge_finder.teams.registry import get_team  # noqa: E402

NOW = datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
HISTORY_SEASONS = [2025]


# ---------------------------------------------------------------- fixtures


def _game(home_id: str, away_id: str, home_name: str, away_name: str, kickoff: datetime, *, status="scheduled"):
    return GameRecord(
        game_id=canonical_game_id(SEASON, "wk01", away_id, home_id, neutral_site=False),
        season=SEASON,
        week_label="wk01",
        season_type="regular",
        home_team_id=home_id,
        away_team_id=away_id,
        home_team_name=home_name,
        away_team_name=away_name,
        kickoff_utc=kickoff,
        status=status,
        discovered_at=NOW,
        last_updated_at=NOW,
    )


def _espn_event(home_name: str, away_name: str, kickoff: datetime, *, status="STATUS_SCHEDULED", event_id="401"):
    """One scoreboard event in the shape live-verified on 2026-09-03."""
    return {
        "id": event_id,
        "date": kickoff.strftime("%Y-%m-%dT%H:%MZ"),
        "name": f"{away_name} at {home_name}",
        "season": {"year": SEASON, "type": 2},
        "competitions": [
            {
                "id": event_id,
                "date": kickoff.strftime("%Y-%m-%dT%H:%MZ"),
                "neutralSite": False,
                "status": {
                    "type": {
                        "id": "1",
                        "name": status,
                        "state": "pre" if status == "STATUS_SCHEDULED" else "post",
                        "completed": status != "STATUS_SCHEDULED",
                        "detail": status,
                    }
                },
                "competitors": [
                    {"homeAway": "home", "team": {"location": home_name, "displayName": home_name}},
                    {"homeAway": "away", "team": {"location": away_name, "displayName": away_name}},
                ],
            }
        ],
    }


class FakeEspn:
    """Returns canned events per bucket; records every bucket asked for.
    Never touches the network, so these tests are hermetic even though the
    real client's hosts were verified live."""

    def __init__(self, events_by_bucket=None, *, fail_all: bool = False, host="site.web.api.espn.com"):
        self.events_by_bucket = events_by_bucket or {}
        self.fail_all = fail_all
        self.host = host
        self.requested: list[str] = []

    def fetch_scoreboard(self, date_param: str) -> ScoreboardFetch:
        self.requested.append(date_param)
        if self.fail_all:
            return ScoreboardFetch(
                host=self.host, url="", date_param=date_param, http_status=403, error="HTTP 403"
            )
        return ScoreboardFetch(
            host=self.host,
            url="",
            date_param=date_param,
            http_status=200,
            events=list(self.events_by_bucket.get(date_param, [])),
        )


def _state_with_ages(schedule_age_h: float, history_age_h: float) -> football_state.FootballState:
    return football_state.FootballState(
        season=SEASON,
        history_seasons=tuple(HISTORY_SEASONS),
        schedule_fetched_at=NOW - timedelta(hours=schedule_age_h),
        teams_fetched_at=NOW - timedelta(hours=history_age_h),
        history_fetched_at=NOW - timedelta(hours=history_age_h),
        schedule_games=[],
        all_division_teams=[],
        history={},
    )


# ================================================================= section B
# Slow model state and fresh schedule state must age independently.


def test_only_schedule_hard_stale_is_distinguishable_from_history_stale():
    """The 2026-09-03 root cause in one assertion: a 9.6h schedule with an
    18h history is NOT the same condition as a 15-day-old history, and the
    old single verdict could not tell them apart."""
    outage_shape = _state_with_ages(schedule_age_h=9.6, history_age_h=18.0)
    assert outage_shape.freshness(NOW) == football_state.FOOTBALL_STATE_STALE_HARD
    assert outage_shape.hard_stale_components(NOW) == (football_state.COMPONENT_SCHEDULE,)
    assert outage_shape.only_schedule_hard_stale(NOW) is True

    genuinely_dead = _state_with_ages(schedule_age_h=9.6, history_age_h=15 * 24.0)
    assert set(genuinely_dead.hard_stale_components(NOW)) == {
        football_state.COMPONENT_SCHEDULE,
        football_state.COMPONENT_HISTORY,
    }
    assert genuinely_dead.only_schedule_hard_stale(NOW) is False


def test_quota_exhausted_with_fallback_returns_the_usable_slow_half(tmp_path):
    """Test matrix 2 + 13: with CFBD gated and only the schedule half hard
    stale, the model half is reachable -- and its provenance stays CFBD."""
    football_state.save_football_state(tmp_path, _state_with_ages(9.6, 18.0))

    without = football_state.resolve_football_state(
        tmp_path, object(), season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW, allow_cfbd=False
    )
    assert without.state is None, "unchanged behavior for callers with no fallback"

    with_fallback = football_state.resolve_football_state(
        tmp_path,
        object(),
        season=SEASON,
        history_seasons=HISTORY_SEASONS,
        now=NOW,
        allow_cfbd=False,
        schedule_fallback_available=True,
    )
    assert with_fallback.state is not None
    assert with_fallback.source == "cache_schedule_fallback"
    assert with_fallback.freshness == football_state.FOOTBALL_STATE_SCHEDULE_STALE_HARD
    assert with_fallback.cfbd_requests == 0, "test matrix 3: zero metered CFBD calls while gated"
    assert with_fallback.state.source == "cfbd", "test matrix 13: slow-state provenance stays CFBD"


def test_history_hard_stale_still_fails_closed_even_with_a_fallback(tmp_path):
    """The fallback refreshes kickoffs, not model inputs. A dead history
    half must still fail closed -- ESPN can never stand in for it."""
    football_state.save_football_state(tmp_path, _state_with_ages(9.6, 15 * 24.0))
    outcome = football_state.resolve_football_state(
        tmp_path,
        object(),
        season=SEASON,
        history_seasons=HISTORY_SEASONS,
        now=NOW,
        allow_cfbd=False,
        schedule_fallback_available=True,
    )
    assert outcome.state is None
    assert outcome.source == "unavailable"


def test_fresh_cfbd_cache_is_untouched_by_the_fallback_flag(tmp_path):
    """Test matrix 1: the healthy CFBD path must be bit-identical whether
    or not a fallback exists."""
    football_state.save_football_state(tmp_path, _state_with_ages(1.0, 2.0))
    plain = football_state.resolve_football_state(
        tmp_path, object(), season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW, allow_cfbd=False
    )
    with_flag = football_state.resolve_football_state(
        tmp_path,
        object(),
        season=SEASON,
        history_seasons=HISTORY_SEASONS,
        now=NOW,
        allow_cfbd=False,
        schedule_fallback_available=True,
    )
    assert plain.source == with_flag.source == "cache"
    assert plain.freshness == with_flag.freshness == football_state.FOOTBALL_STATE_FRESH


# ================================================================= section C/D
# ESPN provides fresh schedule facts under strict, fail-closed matching.


def test_espn_refresh_matches_a_game_and_resets_its_freshness(tmp_path):
    """Test matrix 2 + 4: the fallback succeeds and a fresh fetch makes
    that game's schedule evidence genuinely fresh again."""
    home_id, home_name = FBS_TEAMS[0]
    away_id, away_name = FBS_TEAMS[1]
    kickoff = NOW + timedelta(hours=20)
    game = _game(home_id, away_id, home_name, away_name, kickoff)
    bucket = kickoff.date().strftime("%Y%m%d")
    espn = FakeEspn({bucket: [_espn_event(home_name, away_name, kickoff)]})

    outcome = schedule_state.refresh_schedule_state(
        tmp_path, [game], season=SEASON, now=NOW, client=espn
    )
    assert outcome.verdict == schedule_state.SCHEDULE_STATE_FRESH
    assert outcome.refreshed_games == 1
    fact = outcome.state.facts[game.game_id]
    assert fact.provider == "espn"
    assert fact.fetched_at == NOW
    assert fact.status == "scheduled"

    applied = schedule_state.apply_schedule_state(
        [game],
        outcome.state,
        cfbd_schedule_fetched_at=NOW - timedelta(hours=9.6),
        now=NOW,
        max_fact_age_hours=MAX_SCHEDULE_STALENESS_HOURS,
    )
    assert applied.fresh_game_ids == frozenset({game.game_id})
    # The whole point: this game now PASSES the unchanged 6h guard.
    guard_capture_allowed(
        game_status=applied.games[0].status,
        schedule_source_timestamp=applied.schedule_source_timestamps[game.game_id],
        now=NOW,
    )


def test_unresolvable_team_fails_closed_for_that_game_only(tmp_path):
    """Test matrix 5: an ESPN team name the registry cannot resolve
    EXACTLY makes that event a non-candidate -- never a fuzzy match."""
    home_id, home_name = FBS_TEAMS[0]
    away_id, away_name = FBS_TEAMS[1]
    kickoff = NOW + timedelta(hours=20)
    game = _game(home_id, away_id, home_name, away_name, kickoff)
    bucket = kickoff.date().strftime("%Y%m%d")
    espn = FakeEspn({bucket: [_espn_event(home_name, "Not A Real School XYZ", kickoff)]})

    outcome = schedule_state.refresh_schedule_state(tmp_path, [game], season=SEASON, now=NOW, client=espn)
    assert outcome.refreshed_games == 0
    assert game.game_id in outcome.rejections
    assert outcome.state.facts.get(game.game_id) is None


def test_ambiguous_and_flipped_events_fail_closed(tmp_path):
    """Test matrix 6: two matching events is ambiguity, and a flipped
    orientation is an explicit refusal -- never a silent correction."""
    home_id, home_name = FBS_TEAMS[0]
    away_id, away_name = FBS_TEAMS[1]
    kickoff = NOW + timedelta(hours=20)
    game = _game(home_id, away_id, home_name, away_name, kickoff)
    bucket = kickoff.date().strftime("%Y%m%d")

    duplicated = FakeEspn(
        {
            bucket: [
                _espn_event(home_name, away_name, kickoff, event_id="1"),
                _espn_event(home_name, away_name, kickoff, event_id="2"),
            ]
        }
    )
    ambiguous = schedule_state.refresh_schedule_state(tmp_path, [game], season=SEASON, now=NOW, client=duplicated)
    assert "ambiguous" in ambiguous.rejections[game.game_id]

    flipped = FakeEspn({bucket: [_espn_event(away_name, home_name, kickoff)]})
    flipped_outcome = schedule_state.refresh_schedule_state(
        tmp_path, [game], season=SEASON, now=NOW, client=flipped
    )
    assert "orientation mismatch" in flipped_outcome.rejections[game.game_id]


def test_malformed_timestamp_and_unknown_status_fail_closed(tmp_path):
    """Test matrix 11 + 12: an unparsable date and an unrecognized status
    name both refuse rather than guess."""
    home_id, home_name = FBS_TEAMS[0]
    away_id, away_name = FBS_TEAMS[1]
    kickoff = NOW + timedelta(hours=20)
    game = _game(home_id, away_id, home_name, away_name, kickoff)
    bucket = kickoff.date().strftime("%Y%m%d")

    bad_date = _espn_event(home_name, away_name, kickoff)
    bad_date["competitions"][0]["date"] = "not-a-timestamp"
    bad_date["date"] = "not-a-timestamp"
    outcome = schedule_state.refresh_schedule_state(
        tmp_path, [game], season=SEASON, now=NOW, client=FakeEspn({bucket: [bad_date]})
    )
    assert game.game_id in outcome.rejections
    assert outcome.state.facts.get(game.game_id) is None

    weird = _espn_event(home_name, away_name, kickoff, status="STATUS_WHO_KNOWS")
    outcome2 = schedule_state.refresh_schedule_state(
        tmp_path, [game], season=SEASON, now=NOW, client=FakeEspn({bucket: [weird]})
    )
    assert "unrecognized ESPN status" in outcome2.rejections[game.game_id]


def test_postponed_and_canceled_are_applied_and_block_capture(tmp_path):
    """Test matrix 12: ESPN's postponed/canceled states are honoured, and
    the unchanged status guard then refuses to capture."""
    home_id, home_name = FBS_TEAMS[0]
    away_id, away_name = FBS_TEAMS[1]
    kickoff = NOW + timedelta(hours=3)
    game = _game(home_id, away_id, home_name, away_name, kickoff)
    bucket = kickoff.date().strftime("%Y%m%d")

    for espn_status, expected in (("STATUS_POSTPONED", "postponed"), ("STATUS_CANCELED", "canceled")):
        outcome = schedule_state.refresh_schedule_state(
            tmp_path,
            [game],
            season=SEASON,
            now=NOW,
            client=FakeEspn({bucket: [_espn_event(home_name, away_name, kickoff, status=espn_status)]}),
        )
        assert outcome.state.facts[game.game_id].status == expected
        applied = schedule_state.apply_schedule_state(
            [game],
            outcome.state,
            cfbd_schedule_fetched_at=NOW - timedelta(hours=1),
            now=NOW,
            max_fact_age_hours=MAX_SCHEDULE_STALENESS_HOURS,
        )
        with pytest.raises(StaleScheduleGuardError):
            guard_capture_allowed(
                game_status=applied.games[0].status,
                schedule_source_timestamp=applied.schedule_source_timestamps[game.game_id],
                now=NOW,
            )


def test_espn_may_never_change_identity(tmp_path):
    """A fallback that could rewrite home/away or neutral site would mint
    a new canonical game_id and orphan every row already captured. Only
    kickoff and status may move."""
    home_id, home_name = FBS_TEAMS[0]
    away_id, away_name = FBS_TEAMS[1]
    kickoff = NOW + timedelta(hours=20)
    game = _game(home_id, away_id, home_name, away_name, kickoff)
    bucket = kickoff.date().strftime("%Y%m%d")
    event = _espn_event(home_name, away_name, kickoff + timedelta(hours=2))
    event["competitions"][0]["neutralSite"] = True

    outcome = schedule_state.refresh_schedule_state(
        tmp_path, [game], season=SEASON, now=NOW, client=FakeEspn({bucket: [event]})
    )
    applied = schedule_state.apply_schedule_state(
        [game],
        outcome.state,
        cfbd_schedule_fetched_at=NOW - timedelta(hours=9.6),
        now=NOW,
        max_fact_age_hours=MAX_SCHEDULE_STALENESS_HOURS,
    )
    refreshed = applied.games[0]
    assert refreshed.game_id == game.game_id
    assert refreshed.home_team_id == game.home_team_id
    assert refreshed.away_team_id == game.away_team_id
    assert refreshed.neutral_site == game.neutral_site
    assert refreshed.kickoff_utc == kickoff + timedelta(hours=2)


# ================================================================= section E
# Reschedule safety.


@pytest.mark.parametrize("shift_hours", [-3.0, 3.0])
def test_reschedule_is_recorded_with_both_kickoffs(tmp_path, shift_hours):
    """Test matrix 7 + 8: a move in either direction re-evaluates future
    windows and preserves the old kickoff alongside the new one."""
    home_id, home_name = FBS_TEAMS[0]
    away_id, away_name = FBS_TEAMS[1]
    original = NOW + timedelta(hours=20)
    moved = original + timedelta(hours=shift_hours)
    game = _game(home_id, away_id, home_name, away_name, original)
    buckets = {
        original.date().strftime("%Y%m%d"): [_espn_event(home_name, away_name, moved)],
        moved.date().strftime("%Y%m%d"): [_espn_event(home_name, away_name, moved)],
    }

    outcome = schedule_state.refresh_schedule_state(
        tmp_path, [game], season=SEASON, now=NOW, client=FakeEspn(buckets)
    )
    assert len(outcome.changes) == 1
    change = outcome.changes[0]
    assert change.previous_kickoff_utc == original
    assert change.new_kickoff_utc == moved
    assert change.detected_at == NOW
    assert outcome.state.facts[game.game_id].kickoff_utc == moved


def test_moved_earlier_past_kickoff_stops_pregame_capture(tmp_path):
    """The dangerous direction, and the reason this whole mechanism
    exists. Under a stale schedule a game moved EARLIER keeps its old,
    later kickoff, so resolve_due_labels happily emits pregame labels for
    a game that already started. With fresh evidence the clock guard sees
    the real kickoff and nothing pregame is due."""
    from cfb_edge_finder.research import timing

    home_id, home_name = FBS_TEAMS[0]
    away_id, away_name = FBS_TEAMS[1]
    stale_kickoff = NOW + timedelta(hours=4)
    real_kickoff = NOW - timedelta(minutes=20)  # already started
    game = _game(home_id, away_id, home_name, away_name, stale_kickoff)

    assert timing.resolve_due_labels(
        kickoff_utc=stale_kickoff, now=NOW, already_captured_labels=set()
    ), "precondition: the stale kickoff would schedule pregame labels"

    buckets = {
        stale_kickoff.date().strftime("%Y%m%d"): [_espn_event(home_name, away_name, real_kickoff)],
        real_kickoff.date().strftime("%Y%m%d"): [_espn_event(home_name, away_name, real_kickoff)],
        (real_kickoff - timedelta(days=1)).date().strftime("%Y%m%d"): [
            _espn_event(home_name, away_name, real_kickoff)
        ],
    }
    outcome = schedule_state.refresh_schedule_state(
        tmp_path, [game], season=SEASON, now=NOW, client=FakeEspn(buckets)
    )
    applied = schedule_state.apply_schedule_state(
        [game],
        outcome.state,
        cfbd_schedule_fetched_at=NOW - timedelta(hours=9.6),
        now=NOW,
        max_fact_age_hours=MAX_SCHEDULE_STALENESS_HOURS,
    )
    assert applied.games[0].kickoff_utc == real_kickoff
    assert (
        timing.resolve_due_labels(kickoff_utc=applied.games[0].kickoff_utc, now=NOW, already_captured_labels=set())
        == []
    ), "test matrix 10: a post-kickoff quote is never captured as pregame"


def test_implausible_kickoff_shift_is_refused(tmp_path):
    """A single event may not teleport a kickoff across the season."""
    home_id, home_name = FBS_TEAMS[0]
    away_id, away_name = FBS_TEAMS[1]
    original = NOW + timedelta(hours=20)
    absurd = original + timedelta(days=30)
    game = _game(home_id, away_id, home_name, away_name, original)
    outcome = schedule_state.refresh_schedule_state(
        tmp_path,
        [game],
        season=SEASON,
        now=NOW,
        client=FakeEspn({original.date().strftime("%Y%m%d"): [_espn_event(home_name, away_name, absurd)]}),
    )
    assert game.game_id in outcome.rejections
    assert outcome.state.facts.get(game.game_id) is None


def test_espn_may_not_resurrect_a_finished_game(tmp_path):
    """A fresher source claiming a completed game is upcoming is a
    contradiction, not a refresh."""
    home_id, home_name = FBS_TEAMS[0]
    away_id, away_name = FBS_TEAMS[1]
    kickoff = NOW - timedelta(hours=6)
    game = _game(home_id, away_id, home_name, away_name, kickoff, status="final")
    outcome = schedule_state.refresh_schedule_state(
        tmp_path,
        [game],
        season=SEASON,
        now=NOW,
        client=FakeEspn({kickoff.date().strftime("%Y%m%d"): [_espn_event(home_name, away_name, kickoff)]}),
    )
    assert "contradiction" in outcome.rejections[game.game_id]


# ================================================================= section F/G
# Provider hierarchy and freshness policy.


def test_total_espn_outage_keeps_old_facts_with_their_own_honest_age(tmp_path):
    """Test matrix 21: both providers unavailable must not invent
    freshness. Prior facts keep their real timestamps and age out on their
    own through the unchanged 6h guard."""
    home_id, home_name = FBS_TEAMS[0]
    away_id, away_name = FBS_TEAMS[1]
    kickoff = NOW + timedelta(hours=20)
    game = _game(home_id, away_id, home_name, away_name, kickoff)
    bucket = kickoff.date().strftime("%Y%m%d")

    first = schedule_state.refresh_schedule_state(
        tmp_path,
        [game],
        season=SEASON,
        now=NOW - timedelta(hours=9),
        client=FakeEspn({bucket: [_espn_event(home_name, away_name, kickoff)]}),
    )
    schedule_state.save_schedule_state(tmp_path, first.state, now=NOW - timedelta(hours=9))

    dead = schedule_state.refresh_schedule_state(
        tmp_path, [game], season=SEASON, now=NOW, client=FakeEspn(fail_all=True)
    )
    assert dead.verdict == schedule_state.SCHEDULE_STATE_UNAVAILABLE
    stale_fact = dead.state.facts[game.game_id]
    assert stale_fact.fetched_at == NOW - timedelta(hours=9), "no timestamp was refreshed without a retrieval"

    applied = schedule_state.apply_schedule_state(
        [game],
        dead.state,
        cfbd_schedule_fetched_at=NOW - timedelta(hours=9.6),
        now=NOW,
        max_fact_age_hours=MAX_SCHEDULE_STALENESS_HOURS,
    )
    assert applied.fresh_game_ids == frozenset(), "a 9h-old fact is not fresh"
    with pytest.raises(StaleScheduleGuardError):
        guard_capture_allowed(
            game_status="scheduled",
            schedule_source_timestamp=applied.schedule_source_timestamps[game.game_id],
            now=NOW,
        )


def test_far_buckets_are_not_refetched_every_run(tmp_path):
    """Test matrix 24: a 5-minute loop must stay idempotent AND cheap.
    Deadline-relevant buckets refresh every run; distant ones do not."""
    home_id, home_name = FBS_TEAMS[0]
    away_id, away_name = FBS_TEAMS[1]
    near_home_id, near_home_name = FBS_TEAMS[2]
    near_away_id, near_away_name = FBS_TEAMS[3]
    near_kick = NOW + timedelta(hours=5)
    far_kick = NOW + timedelta(days=6)
    near = _game(near_home_id, near_away_id, near_home_name, near_away_name, near_kick)
    far = _game(home_id, away_id, home_name, away_name, far_kick)

    near_buckets, far_buckets = schedule_state.required_buckets([near, far], now=NOW)
    assert near_buckets and far_buckets
    assert not set(near_buckets) & set(far_buckets)

    espn = FakeEspn({})
    first = schedule_state.refresh_schedule_state(tmp_path, [near, far], season=SEASON, now=NOW, client=espn)
    schedule_state.save_schedule_state(tmp_path, first.state, now=NOW)
    assert set(espn.requested) == set(near_buckets) | set(far_buckets)

    espn2 = FakeEspn({})
    schedule_state.refresh_schedule_state(
        tmp_path, [near, far], season=SEASON, now=NOW + timedelta(minutes=5), client=espn2
    )
    assert set(espn2.requested) == set(near_buckets), "far buckets skipped inside their refresh interval"


def test_beyond_the_deep_horizon_costs_nothing(tmp_path):
    """A game two months out is not maintained at all -- no bucket, no
    request, no fact."""
    home_id, home_name = FBS_TEAMS[0]
    away_id, away_name = FBS_TEAMS[1]
    game = _game(home_id, away_id, home_name, away_name, NOW + timedelta(days=60))
    near, far = schedule_state.required_buckets([game], now=NOW)
    assert near == [] and far == []
    espn = FakeEspn({})
    schedule_state.refresh_schedule_state(tmp_path, [game], season=SEASON, now=NOW, client=espn)
    assert espn.requested == []


def test_schedule_state_round_trips_and_rejects_tampering(tmp_path):
    """Test matrix 25/26 shape: the artifact is content-hashed like every
    other durable artifact, and a corrupted payload degrades to 'no fresh
    facts' rather than to bad facts."""
    home_id, home_name = FBS_TEAMS[0]
    away_id, away_name = FBS_TEAMS[1]
    kickoff = NOW + timedelta(hours=20)
    game = _game(home_id, away_id, home_name, away_name, kickoff)
    outcome = schedule_state.refresh_schedule_state(
        tmp_path,
        [game],
        season=SEASON,
        now=NOW,
        client=FakeEspn({kickoff.date().strftime("%Y%m%d"): [_espn_event(home_name, away_name, kickoff)]}),
    )
    schedule_state.save_schedule_state(tmp_path, outcome.state, now=NOW)

    reloaded = schedule_state.load_schedule_state(tmp_path, SEASON)
    assert reloaded.facts[game.game_id].kickoff_utc == kickoff

    payload = tmp_path / "data" / "research" / schedule_state.SCHEDULE_STATE_SUBDIR / f"{SEASON}.json"
    tampered = json.loads(payload.read_text())
    tampered["facts"][game.game_id]["kickoff_utc"] = (kickoff + timedelta(days=1)).isoformat()
    payload.write_text(json.dumps(tampered, sort_keys=True, separators=(",", ":")))
    assert schedule_state.load_schedule_state(tmp_path, SEASON).facts == {}


# ================================================================= section H/I
# Exit semantics and alert suppression.


def _classify(**kwargs):
    base = dict(
        diagnostics=[],
        fail_closed=False,
        blocker=None,
        deadline_risk=False,
        prior_state={},
        now=NOW,
    )
    base.update(kwargs)
    return operational_state.classify_run(**base)


def test_high_severity_always_fails_loudly():
    """Test matrix 18 + 19: mapping collapse and persistence failure keep
    exiting 1, and no amount of prior alerting suppresses them."""
    for code in ("mapping_failure_rate_high", "persistence_failures", "closing_capture_shortfall"):
        result = _classify(
            diagnostics=[Diagnostic(Severity.HIGH, code, "detail")],
            blocker="CFBD_QUOTA_EXHAUSTED",
            prior_state={
                "last_alerted_state": operational_state.INTEGRITY_FAILURE,
                "last_alerted_blocker": "CFBD_QUOTA_EXHAUSTED",
                "last_alerted_at": NOW.isoformat(),
            },
        )
        assert result.operational_state == operational_state.INTEGRITY_FAILURE
        assert result.should_fail_run is True
        assert code in result.high_severity_codes


def test_deadline_at_risk_always_fails_loudly():
    """Test matrix 20 + 22: a due CLOSING with no trustworthy schedule is
    unrecoverable data loss and is never suppressed, even when the
    identical state alerted seconds ago."""
    result = _classify(
        fail_closed=True,
        blocker="CFBD_QUOTA_EXHAUSTED",
        deadline_risk=True,
        prior_state={
            "last_alerted_state": operational_state.DEADLINE_AT_RISK,
            "last_alerted_blocker": "CFBD_QUOTA_EXHAUSTED",
            "last_alerted_at": NOW.isoformat(),
        },
    )
    assert result.operational_state == operational_state.DEADLINE_AT_RISK
    assert result.should_fail_run is True
    assert result.suppressed_because is None


def test_repeated_known_quota_state_stops_being_red():
    """Test matrix 15 + 16 + 17 -- the headline behavior change. The first
    run in a degraded state is red; identical repeats are green; the state
    itself is recorded durably either way."""
    first = _classify(blocker="CFBD_QUOTA_EXHAUSTED", prior_state={})
    assert first.operational_state == operational_state.DEGRADED_SAFE
    assert first.should_fail_run is True, "entering degradation is noteworthy exactly once"

    recorded = operational_state.record_state(first, {}, now=NOW)
    assert recorded["last_alerted_state"] == operational_state.DEGRADED_SAFE

    for minutes in (5, 10, 15, 300):
        later = NOW + timedelta(minutes=minutes)
        repeat = operational_state.classify_run(
            diagnostics=[],
            fail_closed=False,
            blocker="CFBD_QUOTA_EXHAUSTED",
            deadline_risk=False,
            prior_state=recorded,
            now=later,
        )
        assert repeat.operational_state == operational_state.DEGRADED_SAFE
        assert repeat.should_fail_run is False, f"still red after {minutes} minutes"
        assert repeat.suppressed_because is not None
        # Suppression must not erase the alert bookkeeping it depends on.
        recorded = operational_state.record_state(repeat, recorded, now=later)
        assert recorded["last_alerted_at"] == NOW.isoformat()


def test_a_materially_changed_blocker_re_alerts():
    """Test matrix 23: the fallback dying is a NEW condition even though
    the state name is unchanged."""
    recorded = operational_state.record_state(
        _classify(blocker="CFBD_QUOTA_EXHAUSTED"), {}, now=NOW
    )
    worse = operational_state.classify_run(
        diagnostics=[],
        fail_closed=False,
        blocker="CFBD_QUOTA_EXHAUSTED+SCHEDULE_FALLBACK_UNAVAILABLE",
        deadline_risk=False,
        prior_state=recorded,
        now=NOW + timedelta(minutes=5),
    )
    assert worse.should_fail_run is True
    assert worse.suppressed_because is None


def test_long_lived_degradation_re_alerts_daily_not_never():
    """Suppressed must not mean forgotten: a state that persists past the
    re-alert window reports again."""
    recorded = operational_state.record_state(_classify(blocker="CFBD_QUOTA_EXHAUSTED"), {}, now=NOW)
    much_later = NOW + timedelta(hours=operational_state.REALERT_AFTER_HOURS + 0.1)
    again = operational_state.classify_run(
        diagnostics=[],
        fail_closed=False,
        blocker="CFBD_QUOTA_EXHAUSTED",
        deadline_risk=False,
        prior_state=recorded,
        now=much_later,
    )
    assert again.should_fail_run is True


def test_blocked_with_nothing_due_is_degraded_waiting_not_an_incident():
    """Test matrix 21: both providers down, no checkpoint due, explicit
    state -- green after the entry alert, and never silent about it."""
    first = _classify(fail_closed=True, blocker="CFBD_QUOTA_EXHAUSTED+SCHEDULE_HORIZON_UNKNOWN")
    assert first.operational_state == operational_state.DEGRADED_WAITING
    assert first.should_fail_run is True
    recorded = operational_state.record_state(first, {}, now=NOW)
    repeat = operational_state.classify_run(
        diagnostics=[],
        fail_closed=True,
        blocker="CFBD_QUOTA_EXHAUSTED+SCHEDULE_HORIZON_UNKNOWN",
        deadline_risk=False,
        prior_state=recorded,
        now=NOW + timedelta(minutes=5),
    )
    assert repeat.should_fail_run is False
    assert repeat.operational_state == operational_state.DEGRADED_WAITING


def test_a_transient_integrity_failure_does_not_re_alert_the_degraded_state():
    """Regression, live 2026-09-05T17:10Z -> 17:15Z.

    A transient `api_failures` diagnostic classified one run
    INTEGRITY_FAILURE and stamped that name into `last_alerted_state`. The
    next run -- the same long-running DEGRADED_SAFE(quota) it had been for
    hours -- then read as a state ENTRY and went red. Every blip cost two
    emails instead of one.

    A terminal run must leave the degraded bookkeeping untouched."""
    degraded = _classify(blocker="CFBD_QUOTA_EXHAUSTED", prior_state={})
    recorded = operational_state.record_state(degraded, {}, now=NOW)
    assert recorded["last_alerted_state"] == operational_state.DEGRADED_SAFE

    blip_at = NOW + timedelta(minutes=5)
    blip = operational_state.classify_run(
        diagnostics=[Diagnostic(Severity.HIGH, "api_failures", "1 data-source call(s) failed")],
        fail_closed=False,
        blocker="CFBD_QUOTA_EXHAUSTED",
        deadline_risk=False,
        prior_state=recorded,
        now=blip_at,
    )
    assert blip.should_fail_run is True, "a HIGH diagnostic is still red, unconditionally"
    recorded = operational_state.record_state(blip, recorded, now=blip_at)
    assert recorded["operational_state"] == operational_state.INTEGRITY_FAILURE
    assert recorded["last_alerted_state"] == operational_state.DEGRADED_SAFE, (
        "a terminal run must not clobber the degraded alert bookkeeping"
    )

    after = operational_state.classify_run(
        diagnostics=[],
        fail_closed=False,
        blocker="CFBD_QUOTA_EXHAUSTED",
        deadline_risk=False,
        prior_state=recorded,
        now=NOW + timedelta(minutes=10),
    )
    assert after.operational_state == operational_state.DEGRADED_SAFE
    assert after.should_fail_run is False, "returning to an already-alerted degraded state is not news"
    assert after.suppressed_because is not None


def test_a_blocker_that_changes_across_a_blip_still_re_alerts():
    """The flap fix must not create a blind spot: if the degraded
    condition genuinely worsened while a terminal run was in the way, that
    is new information and must still be red."""
    recorded = operational_state.record_state(_classify(blocker="CFBD_QUOTA_EXHAUSTED"), {}, now=NOW)
    blip_at = NOW + timedelta(minutes=5)
    blip = operational_state.classify_run(
        diagnostics=[Diagnostic(Severity.HIGH, "persistence_failures", "detail")],
        fail_closed=False,
        blocker="CFBD_QUOTA_EXHAUSTED",
        deadline_risk=False,
        prior_state=recorded,
        now=blip_at,
    )
    recorded = operational_state.record_state(blip, recorded, now=blip_at)
    worse = operational_state.classify_run(
        diagnostics=[],
        fail_closed=False,
        blocker="CFBD_QUOTA_EXHAUSTED+SCHEDULE_FALLBACK_UNAVAILABLE",
        deadline_risk=False,
        prior_state=recorded,
        now=NOW + timedelta(minutes=10),
    )
    assert worse.should_fail_run is True
    assert worse.suppressed_because is None


def test_known_degraded_state_stays_quiet_for_a_full_week_of_runs():
    """QUIET HIBERNATION end-to-end: a permanent, acknowledged blocker
    driving a run every 5 minutes must produce ONE alert on entry and then
    stay green until the bounded weekly re-report."""
    now = NOW
    first = _classify(blocker="CFBD_QUOTA_EXHAUSTED", prior_state={})
    assert first.should_fail_run is True
    recorded = operational_state.record_state(first, {}, now=now)

    reds = 0
    ticks = 0
    # Six days of a 5-minute cadence, stopping short of the re-alert window.
    while (now - NOW) < timedelta(hours=operational_state.REALERT_AFTER_HOURS - 1):
        now += timedelta(minutes=5)
        ticks += 1
        run = operational_state.classify_run(
            diagnostics=[],
            fail_closed=False,
            blocker="CFBD_QUOTA_EXHAUSTED",
            deadline_risk=False,
            prior_state=recorded,
            now=now,
        )
        reds += int(run.should_fail_run)
        recorded = operational_state.record_state(run, recorded, now=now)

    assert ticks > 1900, "the simulation must actually cover a week of 5-minute runs"
    assert reds == 0, f"{reds} of {ticks} known-degraded runs were still red"

    due = operational_state.classify_run(
        diagnostics=[],
        fail_closed=False,
        blocker="CFBD_QUOTA_EXHAUSTED",
        deadline_risk=False,
        prior_state=recorded,
        now=NOW + timedelta(hours=operational_state.REALERT_AFTER_HOURS + 0.1),
    )
    assert due.should_fail_run is True, "suppressed must not mean forgotten"


def test_healthy_runs_never_alert_and_never_suppress():
    healthy = _classify()
    assert healthy.operational_state == operational_state.HEALTHY
    assert healthy.should_fail_run is False
    assert healthy.alerting is False


def test_deadline_risk_only_counts_games_without_fresh_evidence():
    """The risk set is exactly 'inside the window AND untrusted'. A game
    with fresh evidence is not at risk however close its kickoff is, and a
    game with an unknown kickoff cannot lose a checkpoint at all."""
    kickoffs = {
        "trusted_soon": NOW + timedelta(hours=1),
        "untrusted_soon": NOW + timedelta(hours=1),
        "untrusted_far": NOW + timedelta(hours=48),
        "untrusted_past": NOW - timedelta(hours=1),
        "unknown_kickoff": None,
    }
    at_risk = operational_state.deadline_risk_games(
        kickoffs_by_game_id=kickoffs, trusted_game_ids={"trusted_soon"}, now=NOW
    )
    assert at_risk == ["untrusted_soon"]


# ================================================================= section L
# Client-level behavior verified against the shapes probed live.


def test_client_prefers_the_first_host_that_answers(monkeypatch):
    """Live CI probing found site.api 403 and site.web.api 200 on the same
    day. The client must move on from a blocked host without retrying it
    into the ground, and must not retry a 403 at all."""

    class Resp:
        def __init__(self, status, payload=None):
            self.status_code = status
            self._payload = payload

        def json(self):
            if self._payload is None:
                raise ValueError("no json")
            return self._payload

    calls: list[str] = []

    class Session:
        def get(self, url, params=None, timeout=None, headers=None):
            calls.append(url)
            if "site.web.api" in url:
                return Resp(403)
            return Resp(200, {"content": {"sbData": {"events": [{"id": "1"}]}}})

    client = EspnScheduleClient(session=Session())
    fetch = client.fetch_scoreboard("20260903")
    assert fetch.ok
    assert fetch.host == EspnScheduleClient.CDN
    assert sum(1 for c in calls if "site.web.api" in c) == 1, "a 403 is policy, not a transient to retry"


def test_client_returns_failures_as_data_never_raises(monkeypatch):
    class Session:
        def get(self, *a, **k):
            raise requests.ConnectionError("boom")

    fetch = EspnScheduleClient(session=Session()).fetch_scoreboard("20260903")
    assert fetch.ok is False
    assert "ConnectionError" in (fetch.error or "")


# ================================================================= identity
# The exact-match rule that decides which ESPN events denote which game.


def test_fbs_teams_must_resolve_through_the_registry():
    """A program the registry curates must resolve by exact alias. A slug
    match for a known FBS team would mean the registry and the feed
    disagree about an FBS program -- a signal, never a shortcut."""
    home_id, home_name = FBS_TEAMS[0]
    assert schedule_state._side_matches(home_name, home_id) is True
    assert schedule_state._side_matches("Alabbama", home_id) is False
    assert schedule_state._side_matches(None, home_id) is False


def test_non_fbs_opponents_match_by_the_same_slug_cfbd_itself_assigns():
    """The registry curates FBS programs only, so an FCS opponent never
    appears in it. ingestion/team_matching gives that opponent
    slugify_team(name); applying the identical function to ESPN's own name
    is the same exact match from the other side -- and without it the live
    fallback covered only 46% of FBS games (run 33791551291)."""
    assert get_team("arkansas-pine-bluff") is None, "precondition: not an FBS program"
    assert schedule_state._side_matches("Arkansas-Pine Bluff", "arkansas-pine-bluff") is True
    assert schedule_state._side_matches("Some Other School", "arkansas-pine-bluff") is False


def test_fbs_vs_fcs_game_now_matches_end_to_end(tmp_path):
    """The concrete shape that was failing live: an FBS home team against
    an unregistered FCS opponent."""
    home_id, home_name = FBS_TEAMS[0]
    kickoff = NOW + timedelta(hours=20)
    game = _game(home_id, "arkansas-pine-bluff", home_name, "Arkansas-Pine Bluff", kickoff)
    outcome = schedule_state.refresh_schedule_state(
        tmp_path,
        [game],
        season=SEASON,
        now=NOW,
        client=FakeEspn(
            {kickoff.date().strftime("%Y%m%d"): [_espn_event(home_name, "Arkansas-Pine Bluff", kickoff)]}
        ),
    )
    assert outcome.rejections == {}
    assert outcome.state.facts[game.game_id].kickoff_utc == kickoff
