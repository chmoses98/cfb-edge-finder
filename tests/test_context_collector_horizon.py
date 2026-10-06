"""The horizon has to track the SLATE, and "nothing" has to say which nothing.

These tests exist because of a measured failure, not a hypothetical one. The
first live run of `scripts/collect_live_context.py` (Actions run 35573333193,
2026-09-21) enriched 0 of 234 games. Nothing was broken: the workflow asked for
a two-day horizon on a Monday, the catalog's next kickoff was the Thursday, and
the collector correctly did nothing. The slate then reported

    factual context: 0 / 234 games enriched
    confidence ceiling insufficient   234

which is the same thing it would have reported if every provider had 403'd.
Those two situations call for opposite responses -- widen the window, or go and
look at the source -- and nothing in the output told them apart.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import collect_live_context as collector  # noqa: E402

AS_OF = datetime(2026, 9, 21, 7, 31, tzinfo=UTC)  # the Monday of the real run


def _kick(day: int, hour: int = 20) -> datetime:
    return datetime(2026, 9, day, hour, tzinfo=UTC)


# ------------------------------------------------------- resolving a horizon


def test_a_numeric_horizon_is_days_from_now():
    horizon, label = collector._resolve_horizon("2", AS_OF, [_kick(26)])
    assert horizon == AS_OF + timedelta(days=2)
    assert label == "2-day"


def test_a_fractional_horizon_still_works():
    horizon, label = collector._resolve_horizon("1.5", AS_OF, [_kick(26)])
    assert horizon == AS_OF + timedelta(days=1.5)
    assert label == "1.5-day"


def test_all_reaches_the_catalogs_own_latest_kickoff():
    """`--date all` builds a slate spanning the catalog. The context has to
    span the same thing or the slate is unenriched by construction."""
    kickoffs = [_kick(24), _kick(26), _kick(30), datetime(2026, 10, 4, 7, tzinfo=UTC)]
    horizon, label = collector._resolve_horizon("all", AS_OF, kickoffs)
    assert horizon == datetime(2026, 10, 4, 7, tzinfo=UTC)
    assert "2026-10-04" in label


def test_all_on_an_empty_catalog_does_not_explode():
    horizon, label = collector._resolve_horizon("all", AS_OF, [])
    assert horizon == AS_OF
    assert "empty" in label


def test_all_is_case_insensitive_and_tolerates_whitespace():
    horizon, _ = collector._resolve_horizon("  ALL  ", AS_OF, [_kick(30)])
    assert horizon == _kick(30)


def test_a_nonsense_horizon_is_an_error_not_a_default():
    """Silently falling back to a default would produce an unenriched slate
    and no indication that the flag was ignored."""
    with pytest.raises(ValueError):
        collector._resolve_horizon("soon", AS_OF, [_kick(26)])


# ------------------------------------------- the scoreboard sweep's day count


def test_the_sweep_covers_the_whole_resolved_horizon():
    """The scoreboard walk is one request per DATE, so the swept dates must
    reach the horizon. Reading the raw flag instead of the resolved horizon is
    what made `all` collect a two-day window.

    Asserted as the property rather than the integer, because the integer is
    an index into `range(-lookback, offset + 2)` and an off-by-one there is
    invisible until a game on the last day goes unenriched."""
    horizon = datetime(2026, 10, 4, 7, tzinfo=UTC)  # the real catalog's last kickoff
    offset = collector._horizon_offset_days(AS_OF, horizon)
    swept = {
        (AS_OF + timedelta(days=d)).strftime("%Y%m%d")
        for d in range(-collector.DEFAULT_LOOKBACK_DAYS, offset + 2)
    }
    assert horizon.strftime("%Y%m%d") in swept
    # and the day after, since a late kickoff crosses UTC midnight
    assert (horizon + timedelta(days=1)).strftime("%Y%m%d") in swept


def test_a_same_day_horizon_still_sweeps_today():
    assert collector._horizon_offset_days(AS_OF, AS_OF) == 1


def test_a_past_horizon_floors_at_zero_rather_than_inverting_the_range():
    """range(-21, negative) is empty, and an empty sweep enriches nothing
    while reporting no failure at all."""
    assert collector._horizon_offset_days(AS_OF, AS_OF - timedelta(days=5)) == 0


# ------------------------------------------------------------- the verdict


def test_an_empty_horizon_is_named_as_such():
    assert collector._verdict(0, 0, 0, {}) == "no_games_in_horizon"


def test_every_provider_failing_is_a_different_verdict_from_an_empty_horizon():
    """The whole point. Both produce `0 enriched` downstream."""
    assert collector._verdict(37, 37, 0, {"20260926": "403"}) == "sources_unreachable"


def test_reachable_sources_that_match_nothing_are_not_called_unreachable():
    assert collector._verdict(37, 37, 0, {}) == "no_events_matched"


def test_a_partial_match_is_partial():
    assert collector._verdict(37, 37, 20, {}) == "partial"


def test_everything_matched_is_complete():
    assert collector._verdict(37, 37, 37, {}) == "complete"


# ------------------------------------------------------- the run summary file


def test_the_run_summary_records_what_was_asked_for_and_what_was_reached(tmp_path):
    collector._write_run_summary(
        tmp_path,
        as_of=AS_OF,
        horizon=AS_OF + timedelta(days=2),
        horizon_label="2-day",
        games_in_catalog=234,
        games_in_horizon=0,
        games_written=0,
        events_matched=0,
        scoreboard_dates=0,
        scoreboard_failures={},
        nearest_kickoff=_kick(24, 23),
    )
    written = json.loads((tmp_path / collector.RUN_SUMMARY_NAME).read_text())
    assert written["games_in_catalog"] == 234
    assert written["games_in_horizon"] == 0
    assert written["verdict"] == "no_games_in_horizon"
    assert written["nearest_kickoff"].startswith("2026-09-24")
    assert written["horizon_label"] == "2-day"


def test_the_run_summary_never_records_a_provider_body(tmp_path):
    """Failure REASONS are recorded; a provider's response body is not. A
    scoreboard error can echo a URL with query parameters in it."""
    collector._write_run_summary(
        tmp_path,
        as_of=AS_OF,
        horizon=AS_OF + timedelta(days=2),
        horizon_label="2-day",
        games_in_catalog=10,
        games_in_horizon=10,
        games_written=10,
        events_matched=0,
        scoreboard_dates=23,
        scoreboard_failures={f"2026092{i}": "HTTP 403" for i in range(9)},
        nearest_kickoff=_kick(22),
    )
    written = json.loads((tmp_path / collector.RUN_SUMMARY_NAME).read_text())
    assert written["scoreboard_dates_failed"] == 9
    # deduplicated and capped: nine identical 403s are one reason
    assert written["scoreboard_failure_reasons"] == ["HTTP 403"]
    assert written["verdict"] == "sources_unreachable"


def test_the_summary_is_written_even_when_the_out_dir_does_not_exist(tmp_path):
    """The empty-horizon path returns before the collector creates out_dir."""
    target = tmp_path / "context"
    assert not target.exists()
    collector._write_run_summary(
        target,
        as_of=AS_OF,
        horizon=AS_OF,
        horizon_label="whole-catalog (empty catalog)",
        games_in_catalog=0,
        games_in_horizon=0,
        games_written=0,
        events_matched=0,
        scoreboard_dates=0,
        scoreboard_failures={},
        nearest_kickoff=None,
    )
    assert (target / collector.RUN_SUMMARY_NAME).exists()


# ------------------------------------------------------------ kickoff parsing


def test_an_unparseable_kickoff_is_skipped_not_fatal():
    assert collector._kickoff_of({"kickoff": "not a date"}) is None
    assert collector._kickoff_of({}) is None


def test_a_z_suffixed_kickoff_parses_as_utc():
    assert collector._kickoff_of({"kickoff": "2026-09-26T16:00:00Z"}) == datetime(
        2026, 9, 26, 16, tzinfo=UTC
    )


# ------------------------------------------------- the budget and the breaker
#
# Fail-soft per REQUEST is not fail-soft per RUN. A dead provider costs about
# 79 seconds per URL (25s timeout x 3 attempts plus backoff), and a `--date
# all` slate asks for roughly 900 of them. Without a bound, a full outage
# outlasts the workflow's own 30-minute timeout and the job is cancelled --
# so the slate is lost too, which is worse than the unenriched slate the
# fail-soft design exists to guarantee.


def test_a_fresh_budget_has_not_been_spent():
    assert collector.Budget(600, 25).spent() is False


def test_the_breaker_trips_after_enough_consecutive_failures():
    budget = collector.Budget(0, 3)
    for _ in range(3):
        assert budget.spent() is False
        budget.record(False)
    assert budget.spent() is True
    assert "not answering" in budget.tripped_reason


def test_one_success_resets_the_breaker():
    """A flaky endpoint is not an outage, and a run that gave up on the first
    three-failure streak would abandon a provider that was merely slow."""
    budget = collector.Budget(0, 3)
    budget.record(False)
    budget.record(False)
    budget.record(True)
    budget.record(False)
    budget.record(False)
    assert budget.spent() is False


def test_the_clock_trips_the_budget():
    budget = collector.Budget(0.001, 0)
    time.sleep(0.01)
    assert budget.spent() is True
    assert "budget was spent" in budget.tripped_reason


def test_a_tripped_budget_stays_tripped_and_keeps_its_reason():
    """Re-evaluating must not let a reset counter un-trip a spent run."""
    budget = collector.Budget(0, 2)
    budget.record(False)
    budget.record(False)
    assert budget.spent() is True
    reason = budget.tripped_reason
    budget.record(True)
    assert budget.spent() is True
    assert budget.tripped_reason == reason


def test_zero_disables_each_guard_independently():
    unlimited = collector.Budget(0, 0)
    for _ in range(500):
        unlimited.record(False)
    assert unlimited.spent() is False


def test_a_spent_budget_short_circuits_the_request_itself(monkeypatch):
    """The point is to stop SPENDING. A guard that still issued the request
    and discarded the answer would save nothing."""
    calls = []

    def _explode(*args, **kwargs):
        calls.append(args)
        raise AssertionError("a spent budget must not reach the network")

    monkeypatch.setattr(collector.requests, "get", _explode)
    budget = collector.Budget(0, 1)
    budget.record(False)
    monkeypatch.setattr(collector, "_BUDGET", budget)

    payload, reason = collector._get("https://example.invalid/anything")
    assert payload is None
    assert "not answering" in reason
    assert calls == []


def test_stopping_early_is_its_own_verdict_and_says_why(tmp_path):
    collector._write_run_summary(
        tmp_path,
        as_of=AS_OF,
        horizon=AS_OF + timedelta(days=13),
        horizon_label="whole-catalog (to 2026-10-04)",
        games_in_catalog=234,
        games_in_horizon=234,
        games_written=41,
        events_matched=41,
        scoreboard_dates=36,
        scoreboard_failures={},
        nearest_kickoff=_kick(24, 23),
        stopped_early="the 600s collection budget was spent",
        elapsed_seconds=601.4,
    )
    written = json.loads((tmp_path / collector.RUN_SUMMARY_NAME).read_text())
    assert written["verdict"] == "stopped_early"
    assert "600s" in written["stopped_early"]
    assert written["elapsed_seconds"] == 601.4
    # and it does NOT masquerade as a complete run just because what it did
    # reach matched cleanly
    assert written["games_written"] < written["games_in_horizon"]


# ------------------------------------------------------ sweeping both divisions
#
# `groups=80` is FBS. Kalshi lists FCS too, and the first working run matched
# 124 of 234 games because of it -- the other 110 were games the sweep never
# asked about.


class _Recorder:
    """A stand-in for `_get` that records what was asked and replays answers."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def __call__(self, url, params=None, **kwargs):
        self.asked.append((url, dict(params or {})))
        return self.answers.get(params.get("groups") if params else None, (None, "no stub"))


def _envelope(*ids):
    return {"events": [{"id": str(i), "name": f"event {i}"} for i in ids]}


def test_both_divisions_are_requested(monkeypatch):
    recorder = _Recorder({80: (_envelope(1), None), 81: (_envelope(2), None)})
    monkeypatch.setattr(collector, "_get", recorder)

    events, error = collector.fetch_scoreboard("20260926")

    assert error is None
    assert {e["id"] for e in events} == {"1", "2"}
    assert sorted(params["groups"] for _url, params in recorder.asked) == [80, 81]


def test_an_event_listed_under_both_groups_appears_once(monkeypatch):
    """A team that moved divisions mid-season could be listed twice, and a
    duplicated event would double-count in a team's game history."""
    monkeypatch.setattr(
        collector, "_get", _Recorder({80: (_envelope(1, 2), None), 81: (_envelope(2, 3), None)})
    )
    events, error = collector.fetch_scoreboard("20260926")
    assert error is None
    assert sorted(e["id"] for e in events) == ["1", "2", "3"]


def test_one_empty_group_is_not_a_failure(monkeypatch):
    """Out of season, or on a date one division does not play, empty is the
    correct answer -- not an outage."""
    monkeypatch.setattr(
        collector, "_get", _Recorder({80: (_envelope(1), None), 81: ({"events": []}, None)})
    )
    events, error = collector.fetch_scoreboard("20260926")
    assert error is None
    assert [e["id"] for e in events] == ["1"]


def test_one_failing_group_still_yields_the_other(monkeypatch):
    """Losing FCS must not cost FBS. Partial context beats none."""
    monkeypatch.setattr(
        collector, "_get", _Recorder({80: (_envelope(1), None), 81: (None, "HTTP 500")})
    )
    events, error = collector.fetch_scoreboard("20260926")
    assert error is None
    assert [e["id"] for e in events] == ["1"]


def test_the_date_fails_only_when_no_group_answers(monkeypatch):
    monkeypatch.setattr(
        collector, "_get", _Recorder({80: (None, "HTTP 403"), 81: (None, "HTTP 403")})
    )
    events, error = collector.fetch_scoreboard("20260926")
    assert events == []
    assert error == "HTTP 403"


def test_an_event_without_an_id_is_kept_rather_than_dropped(monkeypatch):
    """An id is not guaranteed, and a game with no id is still a game whose
    score the history builder can read."""
    monkeypatch.setattr(
        collector,
        "_get",
        _Recorder({80: ({"events": [{"name": "no id here"}]}, None), 81: ({"events": []}, None)}),
    )
    events, error = collector.fetch_scoreboard("20260926")
    assert error is None
    assert len(events) == 1


def test_the_group_list_names_both_divisions():
    assert collector.SCOREBOARD_GROUPS == (80, 81)


# ------------------------------------------------- why a game matched nothing
#
# 27 of 234 games matched no ESPN event on the first full run. `_match_event`
# answers yes or no, which is all the pricing needs and nothing a person can
# act on: a name spelled differently from ESPN's is an alias fix, a game ESPN
# never published is a provider gap, and no amount of alias work closes the
# second. Counting them together hides both.


def _packet(home, away, kickoff="2026-09-26T20:00:00Z", key="G1"):
    return {
        "game_key": key,
        "kickoff": kickoff,
        "game_metadata": {"teams": {"home": home, "away": away}},
    }


def _espn(home, away, date="2026-09-26T20:00:00Z"):
    return {
        "id": "e1",
        "date": date,
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": home, "shortDisplayName": home}},
                    {"homeAway": "away", "team": {"displayName": away, "shortDisplayName": away}},
                ]
            }
        ],
    }


def test_a_title_with_no_teams_is_named_as_such():
    reason, _ = collector.classify_match_failure(_packet(None, None), [_espn("A", "B")])
    assert reason == collector.UNMATCHED_NO_TEAMS


def test_no_espn_event_at_all_is_a_provider_gap_not_a_naming_problem():
    reason, detail = collector.classify_match_failure(
        _packet("Towson", "Morgan St."), [_espn("Alabama", "Auburn")]
    )
    assert reason == collector.UNMATCHED_NO_EVENT_IN_WINDOW
    assert "Morgan St. at Towson" in detail


def test_one_side_matching_points_at_an_alias_for_the_other():
    """The actionable case: ESPN knows the game, we spell one team its way.

    The example is an ABBREVIATION, deliberately. "Morgan St." against ESPN's
    "Morgan State" used to land here and no longer does -- `name_variants`
    rewrites that affix. What remains is the class of mismatch no punctuation
    rule can close: "App State" is Appalachian State because of a fact about
    the school, not a fact about the string."""
    reason, detail = collector.classify_match_failure(
        _packet("NC St.", "Appalachian St."), [_espn("NC State", "App State")]
    )
    assert reason == collector.UNMATCHED_ONE_TEAM
    assert "matched home only" in detail


def test_neither_side_matching_is_distinguished_from_one_side():
    reason, _ = collector.classify_match_failure(
        _packet("Ole Miss", "LSU"),
        [{"id": "e", "date": "2026-09-26T20:00:00Z",
          "competitions": [{"competitors": [
              {"homeAway": "home", "team": {"displayName": "Ole Miss"}},
              {"homeAway": "away", "team": {"displayName": "Mississippi"}}]}]}],
    )
    # home matched, away did not -> one-team, not neither
    assert reason == collector.UNMATCHED_ONE_TEAM


def test_a_right_game_on_the_wrong_date_is_its_own_reason():
    """A postponed or rescheduled game is neither an alias bug nor a gap."""
    reason, detail = collector.classify_match_failure(
        _packet("Towson", "Morgan St.", kickoff="2026-09-26T20:00:00Z"),
        [_espn("Towson", "Morgan St.", date="2026-10-10T20:00:00Z")],
    )
    assert reason == collector.UNMATCHED_OUTSIDE_WINDOW
    assert "2026-10-10" in detail and "2026-09-26" in detail


def test_the_utc_boundary_is_not_reported_as_a_date_mismatch():
    """A late kickoff crosses UTC midnight; 36 hours is deliberate slack."""
    reason, _ = collector.classify_match_failure(
        _packet("Hawaii", "Fresno St.", kickoff="2026-09-27T05:00:00Z"),
        [_espn("Hawaii", "Fresno St.", date="2026-09-26T05:00:00Z")],
    )
    assert reason != collector.UNMATCHED_OUTSIDE_WINDOW


def test_the_classifier_never_contradicts_the_matcher(monkeypatch):
    """If _match_event found it, the classifier must not be asked -- and if it
    is, it must not claim the game was unmatchable."""
    packet, events = _packet("Towson", "Morgan St."), [_espn("Towson", "Morgan St.")]
    assert collector._match_event(packet, events) is not None
