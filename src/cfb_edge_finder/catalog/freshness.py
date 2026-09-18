"""Is the catalog OPERATIONALLY FRESH?

*** THE TRAP THIS MODULE EXISTS TO AVOID ***
The catalog deliberately does not commit when the market surface has not
changed. So the committed `captured_at` is the timestamp of the last
CHANGE, not of the last OBSERVATION, and an old one does **not** mean the
collector has stopped.

    committed captured_at 6 hours old
      + a successful production run 12 minutes ago whose fingerprint
        matched what is already published
      = the published content was re-observed 12 minutes ago and found
        unchanged.  FRESH.

    committed captured_at 12 minutes old
      + no successful production run since
      = the collector has stopped and we are looking at its last gasp.
        STALE, no matter how recent the timestamp looks.

Freshness is therefore a property of the LAST SUCCESSFUL PRODUCTION RUN,
corroborated by the artifact -- never of the artifact alone. Reading the
timestamp by itself is exactly the mistake that lets a dead collector look
healthy, which is the failure mode this whole repository was rebuilt to
eliminate.

*** WHY NOT JUST COMMIT A TIMESTAMP EVERY RUN ***
Because that trades a real problem for a worse one: a heartbeat commit
every 30 minutes forever is precisely the storage churn the catalog's
change detection exists to prevent, and it makes `git log` useless for
seeing when the market actually moved. The run history already carries the
liveness signal; it just has to be read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# Matched to the workflow's actual cadence (kalshi-market-catalog.yml):
#   Thu 18:00 -> Sun 06:00 UTC   every 30 min
#   otherwise                    every 6 hours
SLATE_WINDOW_CADENCE_MINUTES = 30
OFF_PEAK_CADENCE_MINUTES = 360

# A run is allowed to be late by this multiple of its cadence before the
# catalog is called stale. GitHub's scheduler is not punctual -- this
# repository measured 1.7% on-time delivery on a previous mission -- and a
# 30-minute schedule that fires at 47 minutes is normal, not an outage.
# 3x is wide enough to absorb that and a queued or slow run, and narrow
# enough that a genuinely dead collector is caught within ~90 minutes
# during a slate.
STALENESS_TOLERANCE_MULTIPLE = 3.0


class CatalogFreshness(StrEnum):
    FRESH = "CATALOG FRESH"
    STALE = "CATALOG STALE"


@dataclass(frozen=True)
class FreshnessVerdict:
    freshness: CatalogFreshness
    reason: str
    in_slate_window: bool
    expected_cadence_minutes: int
    tolerance_minutes: float
    last_successful_run_at: datetime | None = None
    minutes_since_last_success: float | None = None
    published_captured_at: datetime | None = None
    minutes_since_published_capture: float | None = None
    fingerprint_matched: bool | None = None
    """True when the last successful run observed the market surface and
    found it identical to what is published -- i.e. the older committed
    content was FRESHLY RE-OBSERVED, not merely old."""

    @property
    def is_fresh(self) -> bool:
        return self.freshness is CatalogFreshness.FRESH

    def explain(self) -> str:
        lines = [f"{self.freshness.value}: {self.reason}"]
        window = "slate window (Thu 18:00-Sun 06:00 UTC)" if self.in_slate_window else "off-peak"
        lines.append(
            f"  window={window} expected every {self.expected_cadence_minutes}min, "
            f"tolerated up to {self.tolerance_minutes:.0f}min"
        )
        if self.minutes_since_last_success is not None:
            lines.append(f"  last successful production run: {self.minutes_since_last_success:.0f}min ago")
        else:
            lines.append("  last successful production run: NONE FOUND")
        if self.minutes_since_published_capture is not None:
            lines.append(
                f"  published captured_at: {self.minutes_since_published_capture:.0f}min ago "
                f"(a change timestamp, not an observation timestamp)"
            )
        if self.fingerprint_matched is True:
            lines.append("  the last run re-observed the published content and found it unchanged")
        return "\n".join(lines)


def in_slate_window(moment: datetime) -> bool:
    """Thursday 18:00 UTC through Sunday 06:00 UTC, matching the workflow.

    Python weekday(): Mon=0 ... Thu=3, Fri=4, Sat=5, Sun=6."""
    day, hour = moment.weekday(), moment.hour
    if day == 3:
        return hour >= 18
    if day in (4, 5):
        return True
    if day == 6:
        return hour < 6
    return False


def expected_cadence_minutes(moment: datetime) -> int:
    return SLATE_WINDOW_CADENCE_MINUTES if in_slate_window(moment) else OFF_PEAK_CADENCE_MINUTES


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def assess_freshness(
    last_successful_run_at: Any,
    published_status: dict[str, Any] | None = None,
    last_run_fingerprint: str | None = None,
    now: datetime | None = None,
) -> FreshnessVerdict:
    """Decide FRESH or STALE.

    `last_successful_run_at` is the completion time of the most recent
    SUCCESSFUL `Kalshi CFB Market Catalog` run on main -- the liveness
    signal. `published_status` is data/live/cfb_catalog_status.json.
    `last_run_fingerprint`, when supplied, is the content fingerprint that
    run reported; matching the published one proves the older committed
    content was re-observed rather than merely left sitting.

    A stale, missing or failed run is STALE regardless of how recent the
    published timestamp looks."""
    moment = now or datetime.now(UTC)
    cadence = expected_cadence_minutes(moment)
    tolerance = cadence * STALENESS_TOLERANCE_MULTIPLE
    window = in_slate_window(moment)

    run_at = _parse(last_successful_run_at)
    status = published_status or {}
    captured_at = _parse(status.get("captured_at"))
    published_fp = status.get("content_fingerprint")

    since_capture = (moment - captured_at).total_seconds() / 60.0 if captured_at else None
    matched = None
    if last_run_fingerprint and published_fp:
        matched = last_run_fingerprint == published_fp

    common = {
        "in_slate_window": window,
        "expected_cadence_minutes": cadence,
        "tolerance_minutes": tolerance,
        "last_successful_run_at": run_at,
        "published_captured_at": captured_at,
        "minutes_since_published_capture": since_capture,
        "fingerprint_matched": matched,
    }

    if run_at is None:
        return FreshnessVerdict(
            freshness=CatalogFreshness.STALE,
            reason=(
                "no successful production run found. A recent published timestamp does NOT "
                "substitute -- without a successful run the collector cannot be shown to be alive."
            ),
            minutes_since_last_success=None,
            **common,
        )

    since_run = (moment - run_at).total_seconds() / 60.0
    common_with_run = {**common, "minutes_since_last_success": since_run}

    if since_run > tolerance:
        return FreshnessVerdict(
            freshness=CatalogFreshness.STALE,
            reason=(
                f"last successful production run was {since_run:.0f}min ago, beyond the "
                f"{tolerance:.0f}min tolerance for a {cadence}min cadence."
            ),
            **common_with_run,
        )

    if matched is True:
        return FreshnessVerdict(
            freshness=CatalogFreshness.FRESH,
            reason=(
                f"a successful production run {since_run:.0f}min ago re-observed the market surface "
                f"and found it identical to what is published. The committed content is "
                f"{since_capture:.0f}min old but was confirmed current."
                if since_capture is not None
                else f"a successful production run {since_run:.0f}min ago confirmed the published content."
            ),
            **common_with_run,
        )

    return FreshnessVerdict(
        freshness=CatalogFreshness.FRESH,
        reason=(
            f"a successful production run completed {since_run:.0f}min ago, "
            f"within the {tolerance:.0f}min tolerance."
        ),
        **common_with_run,
    )
