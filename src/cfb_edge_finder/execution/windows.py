"""Kickoff windows.

College Saturdays are organised around Eastern-time kickoff slots, so the
windows are defined in the venue-facing timezone and not in UTC -- a UTC
banding would split the noon Eastern slate across two windows in November
and again at the start of daylight saving.

The bands are deliberately coarse and fixed. They exist to make the slate
processable in chunks, not to say anything about the games in them.
"""

from __future__ import annotations

from datetime import datetime

WINDOW_ORDER = ("early", "afternoon", "evening", "late", "unscheduled")

DEFAULT_TIMEZONE = "America/New_York"


def local_hour(moment: datetime | None, tz_name: str = DEFAULT_TIMEZONE) -> int | None:
    if moment is None:
        return None
    from zoneinfo import ZoneInfo

    try:
        zone = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 - tzdata unavailable in a slim container
        return moment.hour
    return moment.astimezone(zone).hour


def kickoff_window(moment: datetime | None, tz_name: str = DEFAULT_TIMEZONE) -> str:
    """early < 15:00 ET <= afternoon < 19:00 ET <= evening < 22:00 ET <= late.

    Kickoffs after midnight local (a Hawaii or late-Pacific game) belong
    with the night they are part of, so the 00:00-05:59 band is `late`
    rather than the next morning's `early`."""
    hour = local_hour(moment, tz_name)
    if hour is None:
        return "unscheduled"
    if 0 <= hour < 6:
        return "late"
    if hour < 15:
        return "early"
    if hour < 19:
        return "afternoon"
    if hour < 22:
        return "evening"
    return "late"
