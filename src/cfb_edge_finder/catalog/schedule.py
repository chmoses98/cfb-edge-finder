"""When the Kalshi catalog should run, and how badly it has missed.

*** WHY THIS EXISTS ***
GitHub does not honour ``kalshi-market-catalog.yml``'s ``*/30`` cron. The
run history for 2026-09-26 .. 2026-10-17 shows 2-8 catalog runs created per
day, slate days included (2026-10-17: scheduled runs created at 00:53,
05:10, 09:31, 13:20, 16:31 and 18:55 UTC, against ~48 expected). The
missing ticks were never created at all, so no concurrency or retry
setting can recover them. A clock has to come from somewhere else.

The repository already has one: the CFB Research Conductor loops every 5
minutes, around the clock, and re-dispatches itself. Each cycle it now
asks :func:`keepalive_decision` whether a catalog run is due and, if so,
dispatches one. The cron stays as a fallback.

The rule is deliberately dumb. Dispatch when no catalog run is queued or
running AND the newest catalog run (any outcome) was created at least one
cadence ago (30 min in the Thu 18:00 - Sun 06:00 UTC slate window, 6 h
otherwise). The cadence and staleness tolerance are the same constants the
RUN CFB freshness verdict uses (freshness.py), so the clock and the
consumer's definition of "stale" cannot drift apart.

:func:`capture_gaps` is the diagnostic. It lists every gap between
successful captures that exceeded the consumer's staleness tolerance, so a
missed capture window is a named line in a run summary rather than
something discovered on a Saturday afternoon.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from cfb_edge_finder.catalog.freshness import STALENESS_TOLERANCE_MULTIPLE, expected_cadence_minutes

CATALOG_WORKFLOW_FILE = "kalshi-market-catalog.yml"
ACTIVE_STATUSES = frozenset({"queued", "in_progress", "waiting", "pending", "requested"})


@dataclass(frozen=True)
class CatalogRun:
    run_id: int | None
    created_at: datetime
    updated_at: datetime | None
    status: str
    conclusion: str | None
    event: str | None

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def succeeded(self) -> bool:
        return self.status == "completed" and self.conclusion == "success"


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_runs(rows: list[dict[str, Any]]) -> list[CatalogRun]:
    """Rows from ``gh run list --json databaseId,createdAt,updatedAt,status,conclusion,event``
    (or the REST API's snake_case equivalents). Rows without a creation
    time are dropped; nothing else is."""
    runs: list[CatalogRun] = []
    for row in rows:
        created = _parse(row.get("createdAt") or row.get("created_at"))
        if created is None:
            continue
        runs.append(
            CatalogRun(
                run_id=row.get("databaseId") or row.get("id"),
                created_at=created,
                updated_at=_parse(row.get("updatedAt") or row.get("updated_at")),
                status=str(row.get("status") or ""),
                conclusion=row.get("conclusion") or None,
                event=row.get("event"),
            )
        )
    return sorted(runs, key=lambda r: r.created_at)


def tolerance_minutes(moment: datetime) -> float:
    return expected_cadence_minutes(moment) * STALENESS_TOLERANCE_MULTIPLE


@dataclass(frozen=True)
class KeepaliveDecision:
    dispatch: bool
    reason: str
    cadence_minutes: int
    minutes_since_last_run: float | None
    minutes_since_last_success: float | None
    capture_overdue: bool
    """True when the newest successful capture is older than the RUN CFB
    staleness tolerance: a consumer reading the catalog right now would get
    CATALOG STALE."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "dispatch": self.dispatch,
            "reason": self.reason,
            "cadence_minutes": self.cadence_minutes,
            "minutes_since_last_run": self.minutes_since_last_run,
            "minutes_since_last_success": self.minutes_since_last_success,
            "capture_overdue": self.capture_overdue,
        }


def _minutes(later: datetime, earlier: datetime | None) -> float | None:
    return None if earlier is None else round((later - earlier).total_seconds() / 60, 1)


def keepalive_decision(runs: list[CatalogRun], now: datetime) -> KeepaliveDecision:
    cadence = expected_cadence_minutes(now)
    last_run = max(runs, key=lambda r: r.created_at, default=None)
    successes = [r for r in runs if r.succeeded and r.updated_at is not None]
    last_success = max((r.updated_at for r in successes), default=None)
    since_run = _minutes(now, last_run.created_at if last_run else None)
    since_success = _minutes(now, last_success)
    overdue = since_success is None or since_success > tolerance_minutes(now)

    def decide(dispatch: bool, reason: str) -> KeepaliveDecision:
        return KeepaliveDecision(dispatch, reason, cadence, since_run, since_success, overdue)

    active = [r for r in runs if r.active]
    if active:
        # Never stack a second run behind one that is queued or running:
        # in the catalog's own concurrency group the newer pending run would
        # simply replace the older one, which gains nothing.
        return decide(False, f"catalog run already {active[-1].status}")
    if last_run is None:
        return decide(True, "no catalog run on record")
    if since_run is not None and since_run >= cadence:
        return decide(True, f"last catalog run created {since_run:.0f} min ago (cadence {cadence} min)")
    return decide(False, f"last catalog run created {since_run:.0f} min ago, inside the {cadence}-min cadence")


@dataclass(frozen=True)
class CaptureGap:
    start: datetime
    end: datetime
    minutes: float
    tolerance_minutes: float
    ongoing: bool

    def describe(self) -> str:
        tail = "still open" if self.ongoing else f"until {self.end:%Y-%m-%d %H:%M}Z"
        return (
            f"no successful capture from {self.start:%Y-%m-%d %H:%M}Z {tail}: "
            f"{self.minutes:.0f} min (tolerance {self.tolerance_minutes:.0f} min)"
        )


def capture_gaps(runs: list[CatalogRun], now: datetime, since: datetime) -> list[CaptureGap]:
    """Every gap between consecutive successful captures (and from the last
    one to ``now``) that exceeded the staleness tolerance in force during
    the gap. Uses the stricter of the tolerances at either end, so a gap
    that runs into the slate window is judged by the slate-window rule."""
    finishes = sorted(r.updated_at for r in runs if r.succeeded and r.updated_at is not None)
    finishes = [t for t in finishes if t >= since - timedelta(days=2)]
    points = [*finishes, now]
    gaps: list[CaptureGap] = []
    for earlier, later in zip(points, points[1:], strict=False):
        if later < since:
            continue
        minutes = (later - earlier).total_seconds() / 60
        tolerance = min(tolerance_minutes(earlier), tolerance_minutes(later))
        if minutes > tolerance:
            gaps.append(CaptureGap(earlier, later, round(minutes, 1), tolerance, ongoing=later is now))
    return gaps


def run_summary(runs: list[CatalogRun], since: datetime) -> dict[str, int]:
    """How the window's runs ended. A ``cancelled`` catalog run in this
    history is one that was replaced while still queued (none of them ever
    started a job), which is what the dedicated concurrency group stops."""
    window = [r for r in runs if r.created_at >= since]
    counts: dict[str, int] = {"created": len(window)}
    for run in window:
        key = run.conclusion or run.status or "unknown"
        counts[key] = counts.get(key, 0) + 1
    counts["scheduled"] = sum(1 for r in window if r.event == "schedule")
    counts["dispatched"] = sum(1 for r in window if r.event == "workflow_dispatch")
    return counts
