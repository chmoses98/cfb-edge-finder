"""Keyless ESPN lookups the game log does not carry: venue and weather, head coach, QB room, roster injury flags.

Fail-soft in the repository's usual way: a request that fails yields ``{"status": "UNAVAILABLE", "reason": ...}``,
never an empty result that reads as "nothing to report". Empty means the source answered and listed nothing.

Endpoints used (public, no key; reachable from GitHub Actions and from the sandbox this was developed in):

* ``site.api.espn.com/.../summary?event=<id>``  -> ``gameInfo.venue``, ``gameInfo.weather`` (ESPN publishes a
  forecast block for upcoming games), and ESPN's own ``predictor`` (a third-party model: kept separate and labelled).
  The summary also carries ``pickcenter``/``odds``; those are MARKET lines and are deliberately never read here.
* ``site.api.espn.com/.../teams/<id>/roster``   -> head coach, quarterbacks, per-athlete status / injuries.

What does NOT exist keylessly (verified 2026-11-07): a CFB injury report (the league-wide ``/injuries`` endpoint lists
three stale entries from 2020-2022; ``/teams/<id>/injuries`` is empty), a depth chart (``/depthcharts`` is empty), and a
confirmed starting quarterback. The packet says so; web research must fill that gap, with sources.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import requests

BASE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
USER_AGENT = "cfb-edge-finder-research-packet/1.0 (read-only; public ESPN endpoints)"
TIMEOUT = 25.0


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(url: str, params: dict[str, Any] | None = None, retries: int = 2) -> tuple[dict[str, Any] | None, str | None]:
    last = "no attempt"
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        except requests.RequestException as exc:
            last = type(exc).__name__
        else:
            if r.status_code == 200:
                try:
                    return r.json(), None
                except ValueError:
                    return None, "unparsable JSON"
            last = f"HTTP {r.status_code}"
            if r.status_code in (401, 403, 404):
                break
        if attempt < retries:
            time.sleep(1.0 * (attempt + 1))
    return None, last


def parse_event_extras(summary: dict[str, Any], *, observed_at: str) -> dict[str, Any]:
    """Venue, weather and ESPN's predictor from one summary payload. Pure."""
    info = summary.get("gameInfo") or {}
    venue = info.get("venue") or {}
    address = venue.get("address") or {}
    weather = info.get("weather") or {}
    out: dict[str, Any] = {
        "observed_at": observed_at,
        "venue": {
            "name": venue.get("fullName"),
            "city": address.get("city"),
            "state": address.get("state"),
            "grass": venue.get("grass"),
            "indoor": venue.get("indoor"),
        }
        if venue
        else {"status": "UNAVAILABLE", "reason": "summary carried no gameInfo.venue"},
    }
    if weather and any(weather.get(k) is not None for k in ("temperature", "gust", "precipitation")):
        out["weather"] = {
            "status": "FORECAST",
            "source": "espn.summary.gameInfo.weather",
            "temperature_f": weather.get("temperature"),
            "high_f": weather.get("highTemperature"),
            "low_f": weather.get("lowTemperature"),
            "wind_gust_mph": weather.get("gust"),
            "precipitation_probability_pct": weather.get("precipitation"),
            "condition": weather.get("conditionId"),
            "caveat": "an ESPN/AccuWeather forecast for the game's location at publication time, not an observation; indoor venues ignore it",
        }
    else:
        out["weather"] = {
            "status": "UNAVAILABLE",
            "reason": "ESPN published no weather block for this event (typical for domes or games not yet forecast)",
        }
    pred = summary.get("predictor") or {}
    home, away = pred.get("homeTeam") or {}, pred.get("awayTeam") or {}
    try:
        out["espn_predictor"] = {
            "evidence_tier": "EXTERNAL_MODEL_REFERENCE",
            "label": "ESPN's own matchup predictor: a third-party model, not a market and not this repository's baseline",
            "home_team_id": home.get("id"),
            "home_win_pct": float(home["gameProjection"]),
            "away_team_id": away.get("id"),
            "away_win_pct": float(away["gameProjection"]),
        }
    except (KeyError, TypeError, ValueError):
        out["espn_predictor"] = {"status": "UNAVAILABLE", "reason": "the summary carried no predictor (ESPN drops it once a game starts)"}
    return out


def fetch_event_extras(event_id: str) -> dict[str, Any]:
    payload, error = _get(f"{BASE}/summary", {"event": event_id})
    if payload is None:
        return {"status": "UNAVAILABLE", "reason": f"summary request failed: {error}", "attempted_at": _now()}
    return {"status": "OBSERVED", **parse_event_extras(payload, observed_at=_now())}


def parse_roster(payload: dict[str, Any], *, observed_at: str) -> dict[str, Any]:
    """Head coach, quarterback room and any athlete carrying an injury / non-active status. Pure."""
    coaches = payload.get("coach") or []
    coach = None
    if coaches:
        c = coaches[0]
        coach = {
            "name": " ".join(x for x in (c.get("firstName"), c.get("lastName")) if x) or None,
            "experience_years": c.get("experience"),
            "caveat": "ESPN's `experience` is years of coaching experience as it reports them; it does not say whether the coach is new to THIS program",
        }
    qbs: list[dict[str, Any]] = []
    flagged: list[dict[str, Any]] = []
    total = 0
    for group in payload.get("athletes") or []:
        for a in group.get("items") or []:
            total += 1
            pos = (a.get("position") or {}).get("abbreviation")
            status = (a.get("status") or {}).get("name")
            injuries = a.get("injuries") or []
            entry = {
                "name": a.get("displayName"),
                "position": pos,
                "jersey": a.get("jersey"),
                "class": (a.get("experience") or {}).get("displayValue"),
                "status": status,
            }
            if pos == "QB":
                qbs.append(entry)
            if injuries or (status and status.lower() != "active"):
                flagged.append(
                    {
                        **entry,
                        "injuries": [
                            {"status": i.get("status"), "detail": (i.get("details") or {}).get("detail") or i.get("longComment") or i.get("shortComment"), "date": i.get("date")}
                            for i in injuries
                        ],
                        "group": group.get("position"),
                    }
                )
    return {
        "observed_at": observed_at,
        "head_coach": coach,
        "quarterbacks": qbs,
        "roster_athletes": total,
        "flagged_athletes": flagged,
        "flagged_meaning": (
            "athletes the roster feed marks injured or non-active. An EMPTY list means the feed flags nobody, which is "
            "what it does for essentially every CFB team: it is NOT evidence that the team is healthy"
        ),
    }


def fetch_roster(team_id: str) -> dict[str, Any]:
    payload, error = _get(f"{BASE}/teams/{team_id}/roster")
    if payload is None:
        return {"status": "UNAVAILABLE", "reason": f"roster request failed: {error}", "attempted_at": _now()}
    return {"status": "OBSERVED", **parse_roster(payload, observed_at=_now())}
