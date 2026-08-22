"""Structured registry of candidate data sources.

This is a documentation-as-code companion to docs/DATA_SOURCES.md -- it
exists so `production_ready` and `requires_auth` are machine-checkable
rather than only living in prose. It is a registry of what's KNOWN about
each source, not a live client; no network calls happen here. Ingestion
clients are a Milestone B concern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class CostTier(StrEnum):
    FREE = "free"
    FREEMIUM = "freemium"
    PAID = "paid"
    UNKNOWN = "unknown"


class AccessMethod(StrEnum):
    REST_API = "rest_api"
    UNOFFICIAL_API = "unofficial_api"
    SCRAPING = "scraping"
    MANUAL = "manual"


@dataclass(frozen=True)
class DataSourceSpec:
    name: str
    category: str
    access_method: AccessMethod
    requires_auth: bool
    cost_tier: CostTier
    production_ready: bool
    notes: str
    fallback_of: str | None = field(default=None)


# See docs/DATA_SOURCES.md for full detail, caveats, and confidence levels
# per fact. This registry intentionally marks every unverified pricing/limit
# claim in `notes` rather than asserting it as fact.
REGISTRY: tuple[DataSourceSpec, ...] = (
    DataSourceSpec(
        name="collegefootballdata.com (CFBD)",
        category="schedules_scores_pbp_efficiency_rosters_coaches_lines",
        access_method=AccessMethod.REST_API,
        requires_auth=True,
        cost_tier=CostTier.FREEMIUM,
        production_ready=True,
        notes=(
            "Primary source for games, plays, ppa/EPA, SP+, rosters, coaches, "
            "betting lines. Free tier reported ~1,000 calls/mo; paid Patreon "
            "tiers reported up to ~500,000 calls/mo -- UNVERIFIED against the "
            "live pricing page, confirm before committing production volume. "
            "Single-maintainer project: no enterprise SLA."
        ),
    ),
    DataSourceSpec(
        name="ESPN hidden/unofficial API",
        category="schedules_scores_pbp_fpi",
        access_method=AccessMethod.UNOFFICIAL_API,
        requires_auth=False,
        cost_tier=CostTier.FREE,
        production_ready=False,
        notes=(
            "No key needed, but undocumented and can change/break without notice. "
            "Use as cross-check, not sole dependency."
        ),
        fallback_of="collegefootballdata.com (CFBD)",
    ),
    DataSourceSpec(
        name="On3 / 247Sports",
        category="transfers_recruiting_news",
        access_method=AccessMethod.MANUAL,
        requires_auth=True,
        cost_tier=CostTier.PAID,
        production_ready=False,
        notes=(
            "No public commercial API found; automated scraping is a ToS risk. "
            "CFBD's licensed talent/portal endpoints are the defensible route."
        ),
    ),
    DataSourceSpec(
        name="Injury / QB-availability news pipeline",
        category="injuries_availability",
        access_method=AccessMethod.MANUAL,
        requires_auth=False,
        cost_tier=CostTier.UNKNOWN,
        production_ready=False,
        notes=(
            "No structured API exists for this (CFB has no NFL-style mandatory injury report). "
            "This is an editorial/NLP ingestion problem, not a subscribable feed -- budget accordingly."
        ),
    ),
    DataSourceSpec(
        name="NWS/NOAA api.weather.gov",
        category="weather_forecast",
        access_method=AccessMethod.REST_API,
        requires_auth=False,
        cost_tier=CostTier.FREE,
        production_ready=True,
        notes=(
            "Free, no key (descriptive User-Agent required). "
            "Forecast/current-conditions oriented, weak for historical reconstruction."
        ),
    ),
    DataSourceSpec(
        name="Visual Crossing Timeline Weather API",
        category="weather_historical",
        access_method=AccessMethod.REST_API,
        requires_auth=True,
        cost_tier=CostTier.FREEMIUM,
        production_ready=True,
        notes=(
            "Free tier reported ~1,000 records/day, usable commercially. "
            "Better fit than NWS for backtesting past games via venue lat/long."
        ),
        fallback_of="NWS/NOAA api.weather.gov",
    ),
    DataSourceSpec(
        name="Kalshi REST API (/series, /events, /markets)",
        category="market_discovery",
        access_method=AccessMethod.REST_API,
        requires_auth=False,
        cost_tier=CostTier.FREE,
        production_ready=True,
        notes=(
            "Read endpoints reportedly usable unauthenticated; trading requires signed requests "
            "(out of scope). CFB series confirmed via live URLs: KXNCAAFGAME (single-game), "
            "KXNCAAFWINS (season win totals), KXNCAAF (championship). Verify exact base URL and "
            "query params against openapi.yaml before building against it."
        ),
    ),
)
