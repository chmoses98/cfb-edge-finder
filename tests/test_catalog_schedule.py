"""The Kalshi catalog's clock: when the conductor dispatches it, and how a
missed capture window is reported.

The incident behind this: on 2026-10-17 (a Saturday slate) GitHub created
only six of the catalog's ~48 */30 cron runs, and three of those were
cancelled while still queued behind other writers. The result was seven
capture gaps over the 90-minute staleness tolerance.
"""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cfb_edge_finder.catalog.schedule import (
    CaptureGap,
    capture_gaps,
    keepalive_decision,
    parse_runs,
    run_summary,
)

REPO = Path(__file__).resolve().parents[1]
SATURDAY = datetime(2026, 10, 17, 20, 0, tzinfo=UTC)  # inside the Thu 18:00 - Sun 06:00 UTC window
TUESDAY = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)  # off-peak


def _row(created: datetime, status="completed", conclusion="success", event="schedule", minutes=4, run_id=1):
    return {
        "databaseId": run_id,
        "createdAt": created.isoformat().replace("+00:00", "Z"),
        "updatedAt": (created + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
        "status": status,
        "conclusion": conclusion if status == "completed" else "",
        "event": event,
    }


# --- keepalive ----------------------------------------------------------


def test_dispatches_when_the_slate_window_cadence_has_elapsed():
    runs = parse_runs([_row(SATURDAY - timedelta(minutes=31))])
    decision = keepalive_decision(runs, SATURDAY)
    assert decision.dispatch is True
    assert decision.cadence_minutes == 30


def test_does_not_dispatch_inside_the_cadence():
    runs = parse_runs([_row(SATURDAY - timedelta(minutes=12))])
    decision = keepalive_decision(runs, SATURDAY)
    assert decision.dispatch is False
    assert "inside the 30-min cadence" in decision.reason


def test_off_peak_cadence_is_six_hours():
    assert keepalive_decision(parse_runs([_row(TUESDAY - timedelta(hours=5))]), TUESDAY).dispatch is False
    decision = keepalive_decision(parse_runs([_row(TUESDAY - timedelta(hours=6, minutes=1))]), TUESDAY)
    assert decision.dispatch is True
    assert decision.cadence_minutes == 360


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending", "requested"])
def test_never_stacks_a_run_behind_one_that_is_queued_or_running(status):
    runs = parse_runs([_row(SATURDAY - timedelta(hours=3)), _row(SATURDAY - timedelta(hours=2), status=status)])
    decision = keepalive_decision(runs, SATURDAY)
    assert decision.dispatch is False
    assert status in decision.reason


def test_a_cancelled_or_failed_latest_run_still_counts_toward_the_cadence():
    """Re-dispatch is paced by the newest run of ANY outcome, so a failing
    collector is retried once per cadence, never in a tight loop."""
    runs = parse_runs([_row(SATURDAY - timedelta(minutes=10), conclusion="failure")])
    assert keepalive_decision(runs, SATURDAY).dispatch is False
    runs = parse_runs([_row(SATURDAY - timedelta(minutes=40), conclusion="cancelled")])
    assert keepalive_decision(runs, SATURDAY).dispatch is True


def test_no_history_dispatches():
    assert keepalive_decision([], SATURDAY).dispatch is True


def test_capture_overdue_follows_the_last_SUCCESS_not_the_last_run():
    runs = parse_runs(
        [
            _row(SATURDAY - timedelta(minutes=120)),  # last success, finished 116 min ago
            _row(SATURDAY - timedelta(minutes=20), conclusion="failure"),
        ]
    )
    decision = keepalive_decision(runs, SATURDAY)
    assert decision.capture_overdue is True  # 116 > 90
    assert decision.minutes_since_last_success == pytest.approx(116.0)
    fresh = keepalive_decision(parse_runs([_row(SATURDAY - timedelta(minutes=40))]), SATURDAY)
    assert fresh.capture_overdue is False


def test_entering_the_slate_window_tightens_the_cadence_immediately():
    thursday_1805 = datetime(2026, 10, 15, 18, 5, tzinfo=UTC)
    runs = parse_runs([_row(thursday_1805 - timedelta(hours=2))])
    assert keepalive_decision(runs, thursday_1805).dispatch is True


# --- gap report ---------------------------------------------------------


def test_reports_every_gap_over_the_tolerance_and_the_open_one():
    starts = [0, 25, 230, 250]  # minutes after 12:00; the 25 -> 230 gap is a missed window
    base = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    runs = parse_runs([_row(base + timedelta(minutes=m), run_id=i) for i, m in enumerate(starts)])
    now = base + timedelta(minutes=400)
    gaps = capture_gaps(runs, now, since=base - timedelta(hours=1))
    assert [round(g.minutes) for g in gaps] == [205, 146]
    assert gaps[-1].ongoing is True and gaps[0].ongoing is False
    assert all(g.tolerance_minutes == 90 for g in gaps)
    assert "still open" in gaps[-1].describe()


def test_off_peak_gaps_are_judged_by_the_off_peak_tolerance():
    base = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    runs = parse_runs([_row(base), _row(base + timedelta(hours=10))])
    assert capture_gaps(runs, base + timedelta(hours=11), since=base) == []
    runs = parse_runs([_row(base), _row(base + timedelta(hours=19))])
    gaps = capture_gaps(runs, base + timedelta(hours=19, minutes=10), since=base)
    assert len(gaps) == 1 and gaps[0].tolerance_minutes == 1080


def test_a_gap_that_runs_into_the_slate_window_uses_the_stricter_rule():
    wednesday = datetime(2026, 10, 14, 20, 0, tzinfo=UTC)
    friday = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)  # Thursday-night window, 29 h later
    gaps = capture_gaps(parse_runs([_row(wednesday), _row(friday)]), friday + timedelta(minutes=5), since=wednesday)
    assert gaps and gaps[0].tolerance_minutes == 90


def test_failed_and_cancelled_runs_are_not_captures():
    base = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    runs = parse_runs(
        [
            _row(base),
            _row(base + timedelta(minutes=30), conclusion="cancelled"),
            _row(base + timedelta(minutes=60), conclusion="failure"),
            _row(base + timedelta(minutes=150)),
        ]
    )
    gaps = capture_gaps(runs, base + timedelta(minutes=160), since=base)
    assert len(gaps) == 1 and isinstance(gaps[0], CaptureGap) and round(gaps[0].minutes) == 150


def test_run_summary_counts_cancellations_and_dispatches():
    runs = parse_runs(
        [
            _row(SATURDAY - timedelta(hours=3)),
            _row(SATURDAY - timedelta(hours=2), conclusion="cancelled"),
            _row(SATURDAY - timedelta(hours=1), event="workflow_dispatch"),
        ]
    )
    counts = run_summary(runs, SATURDAY - timedelta(hours=24))
    assert counts["created"] == 3 and counts["cancelled"] == 1 and counts["success"] == 2
    assert counts["dispatched"] == 1 and counts["scheduled"] == 2


def test_parse_runs_accepts_rest_api_rows_and_drops_rows_without_a_time():
    runs = parse_runs(
        [
            {"id": 9, "created_at": "2026-10-17T19:13:48Z", "updated_at": "2026-10-17T19:17:55Z",
             "status": "completed", "conclusion": "success", "event": "workflow_dispatch"},
            {"id": 10, "status": "queued"},
        ]
    )
    assert len(runs) == 1 and runs[0].run_id == 9 and runs[0].succeeded


# --- CLI ----------------------------------------------------------------


def _cli():
    spec = importlib.util.spec_from_file_location("catalog_schedule_cli", REPO / "scripts" / "catalog_schedule.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_keepalive_dispatches_only_with_the_flag(monkeypatch, capsys):
    cli = _cli()
    monkeypatch.setattr(cli, "fetch_runs", lambda limit: [_row(SATURDAY - timedelta(minutes=45))])
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    assert cli.main(["keepalive", "--now", SATURDAY.isoformat()]) == 0
    assert calls == []
    assert cli.main(["keepalive", "--dispatch", "--now", SATURDAY.isoformat()]) == 0
    assert calls == [["gh", "workflow", "run", "kalshi-market-catalog.yml", "--ref", "main"]]
    last = [line for line in capsys.readouterr().out.splitlines() if line.startswith("{")][-1]
    assert json.loads(last)["dispatched"] is True


def test_keepalive_exits_1_when_history_is_unreadable(monkeypatch):
    cli = _cli()

    def boom(limit):
        raise OSError("gh missing")

    monkeypatch.setattr(cli, "fetch_runs", boom)
    assert cli.main(["keepalive", "--dispatch"]) == 1


def test_report_never_fails_and_writes_the_step_summary(monkeypatch, tmp_path, capsys):
    cli = _cli()
    base = SATURDAY - timedelta(hours=6)
    monkeypatch.setattr(cli, "fetch_runs", lambda limit: [_row(base), _row(base + timedelta(hours=4))])
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cli.main(["report", "--now", SATURDAY.isoformat()]) == 0
    out = capsys.readouterr().out
    assert "::warning title=Missed Kalshi capture window::" in out
    assert "capture windows missed" in summary.read_text()

    def boom(limit):
        raise OSError("no gh")

    monkeypatch.setattr(cli, "fetch_runs", boom)
    assert cli.main(["report"]) == 0


# --- wiring -------------------------------------------------------------

WORKFLOWS = REPO / ".github" / "workflows"


def test_the_conductor_loop_runs_the_keepalive_on_main_only_and_never_fails_on_it():
    text = (WORKFLOWS / "cfb-research-conductor.yml").read_text(encoding="utf-8")
    loop = text[text.index("while :; do") : text.index("done", text.index("while :; do"))]
    call = re.search(
        r'if \[ "\$GITHUB_REF_NAME" = "main" \]; then\s+GH_REPO="\$GITHUB_REPOSITORY" python '
        r"scripts/catalog_schedule.py keepalive --dispatch \\\s+\|\| echo",
        loop,
    )
    assert call, "the conductor's 5-minute loop no longer dispatches the catalog"
    permissions = text[text.index("permissions:") : text.index("concurrency:")]
    assert "actions: write" in permissions


def test_the_catalog_reports_its_capture_gaps_on_every_run():
    text = (WORKFLOWS / "kalshi-market-catalog.yml").read_text(encoding="utf-8")
    step = text[text.index("- name: Report missed capture windows") :]
    assert "if: always()" in step
    assert "python3 scripts/catalog_schedule.py report" in step
    assert re.search(r"^permissions:\n  contents: write\n  actions: read$", text, re.MULTILINE)
    # the cron stays as the fallback clock
    assert 'cron: "*/30 * * * 5,6"' in text
