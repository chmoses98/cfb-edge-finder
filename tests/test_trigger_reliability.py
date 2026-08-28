"""Trigger layer: provenance, checkpoint deadlines, health severity,
heartbeats, and dedup when several triggers fire at once.

The property that matters most: redundant triggering is SAFE. The whole
design deliberately makes the collector run more often than strictly
necessary, so "an extra run costs nothing but time" has to be true rather
than hoped for.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cfb_edge_finder.research import heartbeat as hb
from cfb_edge_finder.research.timing import CLOSING_WINDOW_MINUTES
from cfb_edge_finder.research.trigger import (
    CLOSING_GUARD_LEAD_MINUTES,
    TIGHT_INTERVAL_SECONDS,
    TriggerHealth,
    TriggerType,
    assess_trigger_health,
    checkpoints_for_kickoff,
    classify_trigger,
    guard_should_be_active,
    missed_checkpoints,
    next_checkpoint,
    seconds_until_guard_needed,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 29, 15, 50, tzinfo=UTC)
KICKOFF = datetime(2026, 8, 29, 16, 0, tzinfo=UTC)
ALL_BUT_CLOSING = {"EARLY_OPEN", "T_7D", "T_3D", "T_24H", "T_6H", "T_90", "T_60", "T_30"}


# --- trigger provenance (sections 12, 18) --------------------------------


@pytest.mark.parametrize(
    "event,actor,expected",
    [
        ("schedule", "chmoses98", TriggerType.GITHUB_SCHEDULE),
        ("workflow_dispatch", "github-actions[bot]", TriggerType.EXTERNAL_SCHEDULE),
        ("workflow_dispatch", "chmoses98", TriggerType.MANUAL),
        ("repository_dispatch", "github-actions", TriggerType.EXTERNAL_SCHEDULE),
        ("repository_dispatch", "someone", TriggerType.MANUAL),
        (None, None, TriggerType.UNKNOWN),
        ("push", "chmoses98", TriggerType.UNKNOWN),
    ],
)
def test_trigger_classification(event, actor, expected):
    assert classify_trigger(event, actor) is expected


def test_conductor_and_human_dispatch_are_distinguishable():
    """Both arrive as workflow_dispatch. If they were conflated, a dead
    conductor would look alive every time a human pressed Run."""
    assert classify_trigger("workflow_dispatch", "github-actions[bot]") is TriggerType.EXTERNAL_SCHEDULE
    assert classify_trigger("workflow_dispatch", "chmoses98") is TriggerType.MANUAL


# --- checkpoints (sections 9, 10) ----------------------------------------


def test_closing_checkpoint_closes_exactly_at_kickoff():
    checkpoints = checkpoints_for_kickoff("g1", KICKOFF, ALL_BUT_CLOSING)
    assert [c.label for c in checkpoints] == ["CLOSING"]
    closing = checkpoints[0]
    assert closing.closes_at == KICKOFF
    assert closing.opens_at == KICKOFF - timedelta(minutes=CLOSING_WINDOW_MINUTES)
    assert closing.recoverable is False


def test_numeric_checkpoints_are_recoverable_and_closing_is_not():
    checkpoints = checkpoints_for_kickoff("g1", KICKOFF, set())
    by_label = {c.label: c for c in checkpoints}
    assert by_label["T_30"].recoverable is True
    assert by_label["CLOSING"].recoverable is False


def test_captured_labels_are_not_re_offered():
    checkpoints = checkpoints_for_kickoff("g1", KICKOFF, {"T_90", "CLOSING"})
    assert "T_90" not in {c.label for c in checkpoints}
    assert "CLOSING" not in {c.label for c in checkpoints}


def test_checkpoints_are_ordered_by_deadline():
    checkpoints = checkpoints_for_kickoff("g1", KICKOFF, set())
    assert [c.closes_at for c in checkpoints] == sorted(c.closes_at for c in checkpoints)


def test_next_checkpoint_can_be_restricted_to_unrecoverable():
    checkpoints = checkpoints_for_kickoff("g1", KICKOFF, set())
    early = KICKOFF - timedelta(days=9)
    assert next_checkpoint(checkpoints, early, only_unrecoverable=True).label == "CLOSING"


def test_missed_checkpoint_detection():
    checkpoints = checkpoints_for_kickoff("g1", KICKOFF, ALL_BUT_CLOSING)
    after_kickoff = KICKOFF + timedelta(minutes=5)
    # A run before the window opened does not cover it.
    stale = KICKOFF - timedelta(hours=3)
    assert [c.label for c in missed_checkpoints(checkpoints, stale, after_kickoff)] == ["CLOSING"]
    # A run inside the window does.
    inside = KICKOFF - timedelta(minutes=6)
    assert missed_checkpoints(checkpoints, inside, after_kickoff) == []


# --- health severity (section 9) -----------------------------------------


def _health(last_run, now=NOW, captured=ALL_BUT_CLOSING, interval=TIGHT_INTERVAL_SECONDS):
    return assess_trigger_health(
        now=now,
        last_successful_run=last_run,
        checkpoints=checkpoints_for_kickoff("g1", KICKOFF, captured),
        trigger_interval_seconds=interval,
        max_dispatch_latency_seconds=30.0,
        collector_runtime_seconds=55.0,
    )


def test_healthy_when_running_and_deadline_is_reachable():
    health, _ = _health(NOW - timedelta(minutes=2))
    assert health is TriggerHealth.HEALTHY


def test_high_when_closing_cannot_be_reached_in_time():
    """4 min interval + 30s dispatch + 55s collector needs ~5.4 min. With
    only 3 minutes to kickoff, a fresh invocation cannot land in time."""
    late = KICKOFF - timedelta(minutes=3)
    health, detail = _health(late - timedelta(minutes=1), now=late)
    assert health is TriggerHealth.HIGH
    assert "CLOSING cannot be recovered" in detail


def test_missed_when_closing_window_passed_uncovered():
    after = KICKOFF + timedelta(minutes=1)
    health, detail = _health(KICKOFF - timedelta(hours=2), now=after)
    assert health is TriggerHealth.MISSED
    assert "unrecoverable" in detail


def test_never_run_is_high():
    health, detail = _health(None)
    assert health is TriggerHealth.HIGH
    assert "never" in detail or "no successful" in detail


def test_quiet_collector_with_distant_deadline_is_warn_not_high():
    """Being quiet is not automatically an emergency -- severity is
    relative to the next real deadline, not to a fixed staleness bar."""
    far_kickoff_now = KICKOFF - timedelta(hours=5)
    health, _ = _health(far_kickoff_now - timedelta(minutes=90), now=far_kickoff_now, captured=set())
    assert health in (TriggerHealth.WARN, TriggerHealth.HEALTHY)
    assert health is not TriggerHealth.HIGH


def test_a_recoverable_checkpoint_at_risk_is_warn_not_high():
    """Missing T_30's deadline costs one snapshot; missing CLOSING's
    costs the closing line forever. They must not carry equal severity."""
    captured = ALL_BUT_CLOSING - {"T_30"} | {"CLOSING"}
    at_t30_edge = KICKOFF - timedelta(minutes=15, seconds=30)
    health, _ = _health(at_t30_edge - timedelta(minutes=1), now=at_t30_edge, captured=captured)
    assert health is TriggerHealth.WARN


# --- guard band (sections 5, 11) -----------------------------------------


def test_guard_engages_only_near_a_kickoff():
    assert guard_should_be_active(KICKOFF - timedelta(minutes=10), [KICKOFF])
    assert guard_should_be_active(KICKOFF - timedelta(minutes=24), [KICKOFF])
    assert not guard_should_be_active(KICKOFF - timedelta(minutes=40), [KICKOFF])
    assert not guard_should_be_active(KICKOFF - timedelta(hours=6), [KICKOFF])


def test_guard_does_not_engage_after_kickoff():
    assert not guard_should_be_active(KICKOFF, [KICKOFF])
    assert not guard_should_be_active(KICKOFF + timedelta(minutes=1), [KICKOFF])


def test_guard_lead_covers_the_whole_closing_window_plus_a_cycle():
    """The band must open before CLOSING does, with room for at least one
    full dispatch+collect cycle inside it."""
    cycle_minutes = (TIGHT_INTERVAL_SECONDS + 30.0 + 55.0) / 60.0
    assert CLOSING_GUARD_LEAD_MINUTES >= CLOSING_WINDOW_MINUTES + cycle_minutes


def test_seconds_until_guard_needed():
    now = KICKOFF - timedelta(hours=2)
    assert seconds_until_guard_needed(now, [KICKOFF]) == pytest.approx(
        (120 - CLOSING_GUARD_LEAD_MINUTES) * 60
    )
    assert seconds_until_guard_needed(now, []) is None
    assert seconds_until_guard_needed(KICKOFF + timedelta(hours=1), [KICKOFF]) is None


def test_overlapping_kickoffs_collapse_into_one_band():
    """Cost control: a Saturday's clustered kickoffs must not each buy
    their own guard window."""
    cluster = [KICKOFF, KICKOFF + timedelta(minutes=5), KICKOFF + timedelta(minutes=10)]
    active_at = KICKOFF - timedelta(minutes=20)
    assert guard_should_be_active(active_at, cluster)
    assert not guard_should_be_active(KICKOFF - timedelta(hours=3), cluster)


# --- heartbeat ledger (section 8) ----------------------------------------


def _beat(trigger, finished, succeeded=True):
    return hb.Heartbeat(
        schema_version=hb.HEARTBEAT_SCHEMA_VERSION, run_id="r1", trigger_type=trigger,
        invoked_at=finished.isoformat(), started_at=finished.isoformat(),
        finished_at=finished.isoformat(), succeeded=succeeded,
    )


def test_heartbeat_round_trip(tmp_path):
    hb.append_heartbeat(tmp_path, 2026, _beat("GITHUB_SCHEDULE", NOW))
    rows = hb.load_heartbeats(hb.heartbeat_path(tmp_path, 2026))
    assert len(rows) == 1
    assert rows[0]["trigger_type"] == "GITHUB_SCHEDULE"


def test_last_successful_run_is_per_trigger(tmp_path):
    """The load-bearing case: cron ran a minute ago, the conductor died an
    hour ago. An overall figure alone would look perfectly healthy."""
    hb.append_heartbeat(tmp_path, 2026, _beat("EXTERNAL_SCHEDULE", NOW - timedelta(hours=1)))
    hb.append_heartbeat(tmp_path, 2026, _beat("GITHUB_SCHEDULE", NOW - timedelta(minutes=1)))
    rows = hb.load_heartbeats(hb.heartbeat_path(tmp_path, 2026))
    assert hb.last_successful_run(rows) == NOW - timedelta(minutes=1)
    assert hb.last_successful_run(rows, "EXTERNAL_SCHEDULE") == NOW - timedelta(hours=1)
    assert hb.last_successful_run(rows, "MANUAL") is None


def test_failed_runs_do_not_count_as_success(tmp_path):
    hb.append_heartbeat(tmp_path, 2026, _beat("GITHUB_SCHEDULE", NOW, succeeded=False))
    rows = hb.load_heartbeats(hb.heartbeat_path(tmp_path, 2026))
    assert hb.last_successful_run(rows) is None


def test_heartbeat_write_failure_is_swallowed(tmp_path):
    """Telemetry must never turn an observability problem into a
    data-loss problem by failing a run that collected successfully."""
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory", encoding="utf-8")
    hb.append_heartbeat(blocked, 2026, _beat("GITHUB_SCHEDULE", NOW))  # must not raise


def test_malformed_heartbeat_lines_are_skipped(tmp_path):
    path = hb.heartbeat_path(tmp_path, 2026)
    path.parent.mkdir(parents=True)
    path.write_text('{"broken\n' + _beat("GITHUB_SCHEDULE", NOW).to_json() + "\n", encoding="utf-8")
    assert len(hb.load_heartbeats(path)) == 1


def test_heartbeats_are_trimmed(tmp_path):
    for i in range(30):
        hb.append_heartbeat(tmp_path, 2026, _beat("GITHUB_SCHEDULE", NOW + timedelta(minutes=i)))
    path = hb.heartbeat_path(tmp_path, 2026)
    removed = hb.trim_heartbeats(path, max_rows=10)
    assert removed == 20
    assert len(hb.load_heartbeats(path)) == 10


def test_heartbeat_carries_no_market_prices():
    """Operational telemetry, not research data."""
    fields = set(hb.Heartbeat.__dataclass_fields__)
    for banned in ("price", "probability", "yes_ask", "no_ask", "ticker", "edge"):
        assert not any(banned in f for f in fields), f"heartbeat leaks {banned}"


# --- conductor planning (sections 3, 5, 20, 21) --------------------------


def _conductor(*args):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "collection_conductor.py"), *args],
        capture_output=True, text=True, timeout=600,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
    )


def test_conductor_dry_run_dispatches_nothing():
    result = _conductor("--dry-run", "--season", "2026")
    assert result.returncode == 0
    assert "DRY RUN" in result.stdout
    assert "dispatch #" not in result.stdout


def test_conductor_survives_a_dead_schedule_source():
    """CFBD being down must degrade the trigger layer, not kill it --
    GitHub cron is still the fallback underneath."""
    result = _conductor("--dry-run", "--season", "2026")
    assert result.returncode == 0
    # Positive statement of the failure, not a bare absence of success.
    assert "schedule fetch         : FAIL" in result.stdout
    assert "FETCH_FAILED" in result.stdout
    assert "fallback" in result.stdout.lower()


def test_conductor_refuses_to_dispatch_without_credentials():
    """No token must be a loud, immediate stop -- never a silent loop of
    failing dispatches that resembles a slow scheduler."""
    result = _conductor("--season", "2026", "--no-self-continue")
    assert result.returncode == 1
    assert "cannot dispatch" in result.stdout
    assert "fallback" in result.stdout.lower()


def test_conductor_invokes_the_canonical_collector_only():
    """Section 3: no second capture implementation may exist."""
    source = (REPO_ROOT / "scripts" / "collection_conductor.py").read_text(encoding="utf-8")
    assert 'COLLECTOR_WORKFLOW = "research-capture.yml"' in source
    for banned in ("resolve_due_labels", "price_one_market", "append_observation", "ResearchCorpusRow"):
        assert banned not in source, f"conductor reimplements {banned}"


def test_conductor_has_a_runaway_backstop():
    from scripts.collection_conductor import MAX_DISPATCHES_PER_RUN, MAX_JOB_SECONDS  # type: ignore

    assert MAX_DISPATCHES_PER_RUN <= 200
    assert MAX_JOB_SECONDS < 6 * 3600, "must exit before GitHub's 6h kill, to hand off"


# --- workflow wiring (sections 7, 12, 17) --------------------------------


def _workflow(name: str) -> str:
    return (REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_collector_cadence_and_concurrency_unchanged():
    capture = _workflow("research-capture.yml")
    assert 'cron: "*/10 * * * *"' in capture
    assert "group: research-data-write" in capture
    assert "cancel-in-progress: false" in capture


def test_settlement_cadence_unchanged():
    assert 'cron: "0 */6 * * *"' in _workflow("research-settlement.yml")


def test_conductor_is_not_in_the_writers_concurrency_group():
    """A sleeping conductor inside research-data-write would block every
    collector run behind it -- the fix becoming the outage."""
    conductor = _workflow("research-collection-conductor.yml")
    assert "group: research-collection-conductor" in conductor
    assert "group: research-data-write" not in conductor
    assert "cancel-in-progress: false" in conductor


def test_conductor_cannot_write_repository_contents():
    conductor = _workflow("research-collection-conductor.yml")
    assert "contents: write" not in conductor
    assert "actions: write" in conductor


def test_collector_records_trigger_provenance():
    capture = _workflow("research-capture.yml")
    assert "--trigger-type" in capture
    assert "--trigger-actor" in capture


def test_no_secret_is_echoed_by_the_conductor():
    conductor = _workflow("research-collection-conductor.yml")
    source = (REPO_ROOT / "scripts" / "collection_conductor.py").read_text(encoding="utf-8")
    assert "echo ${{ secrets" not in conductor
    assert "print(token" not in source
    assert 'f"{token' not in source
    assert "token present          : {bool(token)}" in source or "bool(token)" in source


# --- the 2026-08-27T23:01Z incident ---------------------------------------
#
# One manual dispatch produced 25+ conductor runs at ~3/minute, none of
# which invoked the collector. Two root causes; these tests exercise
# BEHAVIOUR, not source text, because a source-string assertion would
# have passed against a conductor that still could not read a credential.

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import capture_kalshi_cfb_snapshot as _milestone_d  # noqa: E402,F401  (import registers it for monkeypatching)

import scripts.collection_conductor as conductor  # noqa: E402

# --- root cause 1: configuration must actually propagate ------------------


def test_cfbd_credential_actually_reaches_the_client(monkeypatch):
    """Proves PROPAGATION, not spelling.

    The conductor called `Settings()` -- the bare dataclass constructor,
    every field defaulting to None -- so it ran with no CFBD credential on
    every invocation and saw zero kickoffs. This captures the key the
    client is actually constructed with, so the same class of bug (any
    settings object that does not read the environment) fails here."""
    seen = {}

    class RecordingClient:
        def __init__(self, api_key=None, **kwargs):
            seen["api_key"] = api_key

        def fetch_games(self, *a, **k):
            return []

    monkeypatch.setenv("CFBD_API_KEY", "test-key-12345")
    monkeypatch.setattr("cfb_edge_finder.data.cfbd_client.CFBDClient", RecordingClient)
    monkeypatch.setattr(
        "capture_kalshi_cfb_snapshot._fetch_candidate_games",
        lambda season, client, now: ([], {}),
    )

    conductor.supported_upcoming_kickoffs(2026, NOW)

    assert seen.get("api_key") == "test-key-12345", (
        "the conductor built its CFBD client without the environment credential"
    )


def test_missing_credential_surfaces_rather_than_silently_returning_zero(monkeypatch):
    """The failure mode that made the incident invisible: no credential
    produced an empty kickoff list that looked exactly like 'no games'."""
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    from cfb_edge_finder.config import Settings

    assert Settings.from_env().cfbd_api_key is None
    assert Settings().cfbd_api_key is None, "bare constructor must remain credential-free"


# --- root cause 2 + the anti-runaway invariant ----------------------------


def _lineage(generation=0, age_hours=0.0):
    return conductor.ChainLineage(
        chain_id="chain-test",
        generation=generation,
        chain_started_at=NOW - timedelta(hours=age_hours),
    )


def _decide(**over):
    kwargs = dict(
        self_continue_enabled=True,
        handoff_reason="job budget reached with work still ahead",
        run_lifetime_seconds=conductor.MIN_LIFETIME_FOR_HANDOFF_SECONDS + 1,
        lineage=_lineage(),
        now=NOW,
        guard_still_needed=True,
    )
    kwargs.update(over)
    return conductor.may_dispatch_successor(**kwargs)


def test_nothing_to_guard_dispatches_no_successor():
    """THE incident condition, asserted directly."""
    allowed, reason = _decide(handoff_reason=None)
    assert allowed is False
    assert "nothing left to guard" in reason


def test_no_upcoming_kickoff_dispatches_no_successor():
    allowed, reason = _decide(guard_still_needed=False)
    assert allowed is False
    assert "no supported kickoff" in reason


def test_rapid_handoff_is_refused():
    """The rate floor: the incident chained every ~20 seconds."""
    allowed, reason = _decide(run_lifetime_seconds=20.0)
    assert allowed is False
    assert "floor" in reason


def test_generation_cap_terminates_a_chain():
    allowed, reason = _decide(lineage=_lineage(generation=conductor.MAX_CHAIN_GENERATIONS))
    assert allowed is False
    assert "generation" in reason


def test_chain_lease_terminates_a_long_lived_chain():
    allowed, reason = _decide(lineage=_lineage(age_hours=13))
    assert allowed is False
    assert "lease" in reason


def test_self_continue_flag_is_respected():
    allowed, _ = _decide(self_continue_enabled=False)
    assert allowed is False


def test_successor_allowed_only_when_every_condition_holds():
    allowed, reason = _decide()
    assert allowed is True
    assert "job budget" in reason


@pytest.mark.parametrize(
    "override",
    [
        {"handoff_reason": None},
        {"guard_still_needed": False},
        {"run_lifetime_seconds": 1.0},
        {"lineage": _lineage(generation=conductor.MAX_CHAIN_GENERATIONS + 5)},
        {"lineage": _lineage(age_hours=99)},
        {"self_continue_enabled": False},
    ],
)
def test_any_single_failing_condition_stops_the_chain(override):
    """Independence: no single guard carries the whole invariant, so one
    logic error cannot recreate the storm."""
    allowed, _ = _decide(**override)
    assert allowed is False


def test_decision_is_deny_by_default_with_no_fallthrough():
    """The incident's shape was a `break` falling THROUGH to an
    unconditional dispatch. Every deny path must return before the single
    allow at the end."""
    source = inspect.getsource(conductor.may_dispatch_successor)
    body = source[source.index('"""', source.index('"""') + 3) :]
    assert body.count("return True") == 1
    assert body.rindex("return True") > body.rindex("return False")


# --- lineage (section 4) --------------------------------------------------


def test_successor_inherits_chain_identity_and_increments_generation():
    parent = _lineage(generation=3)
    child = parent.child()
    assert child.chain_id == parent.chain_id
    assert child.generation == 4
    assert child.chain_started_at == parent.chain_started_at


def test_lineage_is_passed_to_the_successor_as_workflow_inputs():
    inputs = _lineage(generation=2).as_inputs()
    assert inputs["chain_id"] == "chain-test"
    assert inputs["generation"] == "3"
    assert "chain_started_at" in inputs


def test_workflow_declares_the_lineage_inputs():
    conductor_yml = _workflow("research-collection-conductor.yml")
    for field in ("chain_id", "generation", "chain_started_at"):
        assert field in conductor_yml, f"lineage field {field} is not carried by the workflow"


def test_bounded_chain_cannot_exceed_the_generation_cap():
    """Walk a chain forward and prove it terminates."""
    lineage = _lineage()
    generations = 0
    while True:
        allowed, _ = _decide(lineage=lineage)
        if not allowed:
            break
        lineage = lineage.child()
        generations += 1
        assert generations <= conductor.MAX_CHAIN_GENERATIONS + 1, "chain did not terminate"
    assert generations == conductor.MAX_CHAIN_GENERATIONS


def test_worst_case_chain_duration_is_bounded():
    """Generation cap x rate floor gives a hard floor on how long a
    runaway could possibly take, versus ~20s per generation observed."""
    floor = conductor.MIN_LIFETIME_FOR_HANDOFF_SECONDS
    assert conductor.MAX_CHAIN_GENERATIONS * floor >= 4 * 3600


# --- positive schedule telemetry ------------------------------------------
#
# The post-incident run was judged healthy because an error line was
# ABSENT and the kickoff count was zero -- both equally true of the
# broken conductor that fetched nothing. These pin the positive proof
# that makes the two distinguishable.

from cfb_edge_finder.research.trigger import (  # noqa: E402
    ScheduleHealth,
    SchedulePlanningState,
    classify_schedule,
)

HORIZON_END = NOW + timedelta(hours=36)


def _sched(**over):
    kwargs = dict(
        fetch_success=True,
        total_games=3550,
        upcoming_games=3131,
        supported_upcoming_games=102,
        supported_inside_horizon=4,
        horizon_end=HORIZON_END,
        next_upcoming_kickoff=NOW + timedelta(hours=1),
        next_supported_kickoff=NOW + timedelta(hours=2),
        next_supported_kickoff_inside_horizon=NOW + timedelta(hours=2),
        kickoffs_inside_horizon=(NOW + timedelta(hours=2),),
    )
    kwargs.update(over)
    return ScheduleHealth(**kwargs)


def test_fetch_failure_is_its_own_state():
    health = _sched(fetch_success=False, total_games=0, upcoming_games=0,
                     supported_upcoming_games=0, supported_inside_horizon=0,
                     next_upcoming_kickoff=None, next_supported_kickoff=None,
                     next_supported_kickoff_inside_horizon=None, kickoffs_inside_horizon=())
    assert health.state is SchedulePlanningState.FETCH_FAILED
    assert health.state.fetch_succeeded is False
    assert health.state.is_operationally_suspicious


def test_successful_but_empty_schedule_is_suspicious_not_quiet():
    """A season always has games. Zero records from a SUCCESSFUL request
    means the source or query is wrong, and must not read as 'nothing on
    tonight'."""
    health = _sched(total_games=0, upcoming_games=0, supported_upcoming_games=0,
                     supported_inside_horizon=0, kickoffs_inside_horizon=())
    assert health.state is SchedulePlanningState.FETCH_SUCCESS_EMPTY_SCHEDULE
    assert health.state.fetch_succeeded is True
    assert health.state.is_operationally_suspicious


def test_no_upcoming_games():
    health = _sched(upcoming_games=0, supported_upcoming_games=0, supported_inside_horizon=0,
                     kickoffs_inside_horizon=())
    assert health.state is SchedulePlanningState.FETCH_SUCCESS_NO_UPCOMING_GAMES
    assert not health.state.is_operationally_suspicious


def test_upcoming_games_but_none_supported():
    health = _sched(supported_upcoming_games=0, supported_inside_horizon=0, kickoffs_inside_horizon=())
    assert health.state is SchedulePlanningState.FETCH_SUCCESS_NO_SUPPORTED_GAMES


def test_guardable_game_present():
    assert _sched().state is SchedulePlanningState.FETCH_SUCCESS_GUARDABLE_GAME_PRESENT


def test_states_are_mutually_exclusive_and_ordered_most_severe_first():
    """A failed fetch that also has zero games must report FETCH_FAILED,
    not a benign empty state."""
    assert classify_schedule(
        fetch_success=False, total_games=0, upcoming_games=0,
        supported_upcoming_games=0, supported_inside_horizon=0,
    ) is SchedulePlanningState.FETCH_FAILED


# --- THE incident regression fixture --------------------------------------


def test_incident_pattern_supported_kickoff_outside_horizon():
    """The exact post-incident shape: credentials present, fetch
    succeeded, a real supported kickoff exists ~40.6h out, horizon is
    36h. Previously indistinguishable from 'fetched nothing'."""
    next_supported = NOW + timedelta(hours=40.6)
    health = _sched(
        supported_inside_horizon=0,
        next_supported_kickoff=next_supported,
        next_supported_kickoff_inside_horizon=None,
        kickoffs_inside_horizon=(),
    )
    assert health.state is SchedulePlanningState.FETCH_SUCCESS_SUPPORTED_OUTSIDE_HORIZON

    telemetry = health.as_telemetry()
    # Positive fetch proof, not an absent error message.
    assert telemetry["schedule_fetch_success"] is True
    assert telemetry["total_schedule_games"] == 3550
    assert telemetry["supported_upcoming_games"] == 102
    # The kickoff is reported even though it is beyond the horizon.
    assert telemetry["next_supported_kickoff"] == next_supported.isoformat()
    assert telemetry["next_supported_kickoff_inside_horizon"] is None
    assert telemetry["horizon_end"] == HORIZON_END.isoformat()

    # And the conductor still stops, with zero successors.
    allowed, reason = conductor.may_dispatch_successor(
        self_continue_enabled=True,
        handoff_reason=None,
        run_lifetime_seconds=2.0,
        lineage=_lineage(),
        now=NOW,
        guard_still_needed=False,
    )
    assert allowed is False
    assert "nothing left to guard" in reason


def test_broken_settings_style_failure_is_now_detectable():
    """The credential bug produced total_games=0 with fetch_success=False.
    That is now a named, warned state rather than an unremarkable zero."""
    broken = _sched(fetch_success=False, total_games=0, upcoming_games=0,
                     supported_upcoming_games=0, supported_inside_horizon=0,
                     next_upcoming_kickoff=None, next_supported_kickoff=None,
                     next_supported_kickoff_inside_horizon=None, kickoffs_inside_horizon=())
    healthy_but_quiet = _sched(supported_inside_horizon=0, kickoffs_inside_horizon=(),
                                next_supported_kickoff_inside_horizon=None)
    assert broken.state is not healthy_but_quiet.state
    assert broken.as_telemetry()["schedule_fetch_success"] is False
    assert healthy_but_quiet.as_telemetry()["schedule_fetch_success"] is True


# --- conductor integration ------------------------------------------------


def test_fetch_schedule_health_reports_counts_not_just_kickoffs(monkeypatch):
    """Behavioural: a real schedule with one in-horizon and one distant
    supported game must produce full counts and both kickoffs."""
    from cfb_edge_finder.schemas.game import GameRecord

    def _game(gid, hours):
        return GameRecord.model_construct(game_id=gid, kickoff_utc=NOW + timedelta(hours=hours))

    games = [_game("in", 5), _game("out", 40), _game("fcs", 6), _game("past", -3)]
    classification = {"in": ("fbs", "fbs"), "out": ("fbs", "fbs"), "fcs": ("fbs", "fcs"), "past": ("fbs", "fbs")}

    monkeypatch.setenv("CFBD_API_KEY", "k")
    monkeypatch.setattr("cfb_edge_finder.data.cfbd_client.CFBDClient", lambda **kw: object())
    monkeypatch.setattr("capture_kalshi_cfb_snapshot._fetch_candidate_games",
                        lambda season, client, now: (games, classification))

    health = conductor.fetch_schedule_health(2026, NOW)
    assert health.fetch_success is True
    assert health.total_games == 4
    assert health.upcoming_games == 3
    assert health.supported_upcoming_games == 2
    assert health.supported_inside_horizon == 1
    assert health.next_supported_kickoff == NOW + timedelta(hours=5)
    assert health.state is SchedulePlanningState.FETCH_SUCCESS_GUARDABLE_GAME_PRESENT


def test_fetch_schedule_health_returns_failure_as_data(monkeypatch):
    """A dead schedule source must degrade the trigger layer, not raise."""
    monkeypatch.setattr("cfb_edge_finder.data.cfbd_client.CFBDClient", lambda **kw: object())
    monkeypatch.setattr("capture_kalshi_cfb_snapshot._fetch_candidate_games",
                        lambda season, client, now: (_ for _ in ()).throw(RuntimeError("cfbd down")))
    health = conductor.fetch_schedule_health(2026, NOW)
    assert health.fetch_success is False
    assert health.state is SchedulePlanningState.FETCH_FAILED
    assert "cfbd down" in health.detail


def test_render_states_fetch_result_explicitly():
    text = _sched().render()
    assert "schedule fetch         : PASS" in text
    assert "games fetched          : 3550" in text
    assert "next supported kickoff" in text


# --- heartbeat + readiness integration ------------------------------------


def test_heartbeat_carries_positive_schedule_telemetry(tmp_path):
    beat = hb.Heartbeat(
        schema_version=hb.HEARTBEAT_SCHEMA_VERSION, run_id="r", trigger_type="GITHUB_SCHEDULE",
        invoked_at=NOW.isoformat(), started_at=NOW.isoformat(), finished_at=NOW.isoformat(),
        succeeded=True, schedule_fetch_success=True,
        schedule_state=SchedulePlanningState.FETCH_SUCCESS_SUPPORTED_OUTSIDE_HORIZON.value,
        total_schedule_games=3550, supported_upcoming_games=102,
        next_supported_kickoff="2026-08-29T16:00:00+00:00",
    )
    hb.append_heartbeat(tmp_path, 2026, beat)
    row = hb.load_heartbeats(hb.heartbeat_path(tmp_path, 2026))[0]
    assert row["schedule_fetch_success"] is True
    assert row["total_schedule_games"] == 3550
    assert row["next_supported_kickoff"] == "2026-08-29T16:00:00+00:00"
    assert row["schedule_state"] == "FETCH_SUCCESS_SUPPORTED_OUTSIDE_HORIZON"


def test_heartbeat_absent_schedule_field_is_not_a_failure():
    """A legacy heartbeat must read as 'not recorded', never as 'fetch
    failed' -- the same legacy-vs-defect distinction the corpus schema
    already makes."""
    beat = hb.Heartbeat(
        schema_version=hb.HEARTBEAT_SCHEMA_VERSION, run_id="r", trigger_type="MANUAL",
        invoked_at=NOW.isoformat(), started_at=NOW.isoformat(), finished_at=NOW.isoformat(),
        succeeded=True,
    )
    assert beat.schedule_fetch_success is None


def test_heartbeat_still_carries_no_market_data():
    fields = set(hb.Heartbeat.__dataclass_fields__)
    for banned in ("price", "probability", "yes_ask", "no_ask", "ticker", "edge"):
        assert not any(banned in f for f in fields)


# --- external scheduler provenance ----------------------------------------
#
# GitHub's own scheduler delivered ~1.7% of the collector's */10 slots over
# a measured 573-minute window, so an INDEPENDENT cron service becomes the
# primary clock. A dispatch it makes with a fine-grained PAT carries the
# token OWNER as the actor, which is indistinguishable from a human
# pressing Run -- so the caller declares what it is.

from cfb_edge_finder.research.trigger import DECLARABLE_TRIGGER_SOURCES  # noqa: E402


def test_external_scheduler_dispatch_is_labelled_external_not_manual():
    """The gap this closes: without a declaration, an external cron using
    a PAT owned by the repo owner reads as MANUAL, so a DEAD external
    scheduler would look alive every time a human dispatched once."""
    assert classify_trigger("workflow_dispatch", "chmoses98") is TriggerType.MANUAL
    assert (
        classify_trigger("workflow_dispatch", "chmoses98", "EXTERNAL_SCHEDULE")
        is TriggerType.EXTERNAL_SCHEDULE
    )


def test_a_caller_cannot_claim_to_be_githubs_own_scheduler():
    """Cron provenance is the one thing only GitHub can establish. If a
    caller could assert it, the staleness signal that exists to catch a
    dead scheduler would become unfalsifiable."""
    assert "GITHUB_SCHEDULE" not in DECLARABLE_TRIGGER_SOURCES
    assert classify_trigger("workflow_dispatch", "chmoses98", "GITHUB_SCHEDULE") is TriggerType.MANUAL


@pytest.mark.parametrize("declared", ["", None, "nonsense", "UNKNOWN", "   "])
def test_unrecognised_declaration_falls_back_to_inference(declared):
    """The parameter can only refine the answer, never corrupt it."""
    assert classify_trigger("workflow_dispatch", "chmoses98", declared) is TriggerType.MANUAL
    assert classify_trigger("schedule", "chmoses98", declared) is TriggerType.GITHUB_SCHEDULE


def test_declaration_is_case_and_whitespace_tolerant():
    """A hand-typed scheduler config should not silently mislabel itself."""
    for raw in ("external_schedule", " EXTERNAL_SCHEDULE ", "External_Schedule"):
        assert classify_trigger("workflow_dispatch", "chmoses98", raw) is TriggerType.EXTERNAL_SCHEDULE


def test_conductor_dispatch_still_classifies_without_a_declaration():
    """The conductor predates this and declares nothing; actor inference
    must keep working for it."""
    assert classify_trigger("workflow_dispatch", "github-actions[bot]") is TriggerType.EXTERNAL_SCHEDULE


def test_declaration_never_reaches_capture_logic():
    """Provenance is operational metadata. It must not be able to change
    what is captured -- only how the run is labelled."""
    source = (REPO_ROOT / "scripts" / "research_scan_and_capture.py").read_text(encoding="utf-8")
    idx = source.index("args.trigger_source")
    # Every use is confined to trigger classification and the heartbeat detail.
    uses = [source[i : i + 120] for i in range(len(source)) if source.startswith("args.trigger_source", i)]
    assert uses, "trigger_source is not wired through at all"
    for use in uses:
        assert ("classify_trigger" in source[max(0, idx - 200) : idx + 200]) or ("declared=" in use)


def test_collector_workflow_accepts_and_forwards_trigger_source():
    capture = _workflow("research-capture.yml")
    assert "trigger_source:" in capture
    assert "--trigger-source" in capture
    # The external caller must not be able to turn off persistence.
    assert "no_push:" in capture


def test_external_and_github_schedule_are_tracked_separately(tmp_path):
    """Section 9: a stopped external scheduler must be visible even when
    GitHub cron happens to have fired recently, and vice versa."""
    hb.append_heartbeat(tmp_path, 2026, _beat("EXTERNAL_SCHEDULE", NOW - timedelta(hours=3)))
    hb.append_heartbeat(tmp_path, 2026, _beat("GITHUB_SCHEDULE", NOW - timedelta(minutes=2)))
    rows = hb.load_heartbeats(hb.heartbeat_path(tmp_path, 2026))
    assert hb.last_successful_run(rows, "EXTERNAL_SCHEDULE") == NOW - timedelta(hours=3)
    assert hb.last_successful_run(rows, "GITHUB_SCHEDULE") == NOW - timedelta(minutes=2)
    assert hb.last_successful_run(rows) == NOW - timedelta(minutes=2)
