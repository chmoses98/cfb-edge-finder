"""Trigger layer: provenance, checkpoint deadlines, health severity,
heartbeats, and dedup when several triggers fire at once.

The property that matters most: redundant triggering is SAFE. The whole
design deliberately makes the collector run more often than strictly
necessary, so "an extra run costs nothing but time" has to be true rather
than hoped for.
"""

from __future__ import annotations

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
    assert "SCHEDULE LOOKUP FAILED" in result.stdout
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


# --- the 2026-08-27T23:01Z live-dispatch failures -------------------------
#
# The first dispatch of the merged conductor exposed two defects. One
# manual dispatch at 23:01:00Z produced twelve runs by 23:04:59Z, each
# finishing in ~20s and immediately starting the next. These pin both
# root causes so neither can return.


def test_conductor_reads_credentials_from_the_environment():
    """Root cause 1: the conductor called `Settings()` -- the bare
    dataclass constructor, whose fields all default to None -- instead of
    `Settings.from_env()`. It therefore ran with no CFBD credential on
    EVERY invocation, saw zero kickoffs, and concluded it had nothing to
    guard. A conductor that can never see a kickoff can never guard one."""
    source = (REPO_ROOT / "scripts" / "collection_conductor.py").read_text(encoding="utf-8")
    assert "Settings.from_env()" in source
    assert "settings = Settings()" not in source


def test_settings_bare_constructor_really_is_empty():
    """Guards the assumption above rather than trusting it."""
    from cfb_edge_finder.config import Settings

    assert Settings().cfbd_api_key is None


def test_no_successor_when_there_is_nothing_to_guard():
    """Root cause 2: the loop broke out on 'no upcoming supported kickoff'
    and then fell through to an UNCONDITIONAL self-dispatch, so a
    conductor with nothing to do started another one immediately."""
    source = (REPO_ROOT / "scripts" / "collection_conductor.py").read_text(encoding="utf-8")
    assert "handoff_reason" in source
    assert "elif handoff_reason is None:" in source
    assert "no successor: nothing left to guard" in source


def test_short_lived_run_may_not_start_a_successor():
    """The structural backstop. Independent of why a run ended: a
    conductor that lived only seconds cannot chain. Had this existed, the
    runaway could not have formed even with the reason logic wrong."""
    from scripts.collection_conductor import MIN_LIFETIME_FOR_HANDOFF_SECONDS  # type: ignore

    assert MIN_LIFETIME_FOR_HANDOFF_SECONDS >= 300, "floor too low to stop a fast chain"
    source = (REPO_ROOT / "scripts" / "collection_conductor.py").read_text(encoding="utf-8")
    assert "lifetime < MIN_LIFETIME_FOR_HANDOFF_SECONDS" in source


def test_handoff_requires_both_a_reason_and_the_lifetime_floor():
    """Two independent conditions, so one being wrong cannot alone
    recreate an unbounded chain."""
    source = (REPO_ROOT / "scripts" / "collection_conductor.py").read_text(encoding="utf-8")
    handoff = source[source.index("lifetime = (datetime.now(UTC) - started)") :]
    assert handoff.index("elif handoff_reason is None:") < handoff.index("elif lifetime <")
    assert handoff.index("elif lifetime <") < handoff.index("successor = dispatch_workflow(")
