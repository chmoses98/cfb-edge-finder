#!/usr/bin/env python3
"""READ-ONLY source-reachability + quota probe for the V2 data-enrichment
mission. Runs on a GitHub-hosted runner (the dev container's egress
proxy blocks most third-party hosts).

Spends ZERO metered CFBD calls: /info is unmetered (verified by
scripts/validate_cfbd_quota_live.py). Every other request is to free,
keyless public endpoints and only reports status/shape. Never prints
the API key."""
from __future__ import annotations

import json
import os
import sys

import requests

UA = {"User-Agent": "cfb-edge-finder research probe (read-only)"}


def show(label: str, r: requests.Response | None, err: str | None = None, chars: int = 400) -> None:
    print(f"\n=== {label}")
    if r is None:
        print(f"  ERROR {err}")
        return
    print(f"  HTTP {r.status_code}  bytes={len(r.content)}  ctype={r.headers.get('content-type','')[:40]}")
    body = r.text[:chars].replace("\n", " ")
    print(f"  body: {body}")


def get(label: str, url: str, **kw):
    try:
        r = requests.get(url, headers=UA, timeout=40, **kw)
        show(label, r)
        return r
    except Exception as exc:  # noqa: BLE001
        show(label, None, f"{type(exc).__name__}: {exc}")
        return None


def main() -> int:
    key = os.environ.get("CFBD_API_KEY")
    if key:
        r = requests.get("https://api.collegefootballdata.com/info",
                         headers={"Authorization": f"Bearer {key}", **UA}, timeout=30)
        print("=== CFBD /info (unmetered)")
        print("  ", json.dumps({k: v for k, v in (r.json() or {}).items()
                               if k in ("monthlyLimit", "remainingCalls", "usedCalls", "resetAt", "tierName")}))
    else:
        print("CFBD_API_KEY absent; skipping quota read")

    # Open-Meteo (free, keyless): observed archive, historical forecast archive, previous-runs (lead time)
    get("open-meteo ARCHIVE (observed reanalysis)",
        "https://archive-api.open-meteo.com/v1/archive",
        params={"latitude": 40.0017, "longitude": -83.0197, "start_date": "2024-09-07", "end_date": "2024-09-07",
                "hourly": "temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation,relative_humidity_2m",
                "timezone": "UTC"})
    get("open-meteo HISTORICAL FORECAST (archived forecasts, ~2022+)",
        "https://historical-forecast-api.open-meteo.com/v1/forecast",
        params={"latitude": 40.0017, "longitude": -83.0197, "start_date": "2024-09-07", "end_date": "2024-09-07",
                "hourly": "temperature_2m,wind_speed_10m,precipitation", "timezone": "UTC"})
    get("open-meteo PREVIOUS RUNS (fixed lead time, ~2024+)",
        "https://previous-runs-api.open-meteo.com/v1/forecast",
        params={"latitude": 40.0017, "longitude": -83.0197, "start_date": "2024-09-07", "end_date": "2024-09-07",
                "hourly": "temperature_2m_previous_day1,wind_speed_10m_previous_day1,precipitation_previous_day1",
                "timezone": "UTC"})
    get("open-meteo FORECAST (live, for prospective capture)",
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": 40.0017, "longitude": -83.0197, "hourly": "temperature_2m,wind_speed_10m,precipitation",
                "forecast_days": 7, "timezone": "UTC"})

    # ESPN hidden APIs (keyless): depth charts, injuries, scoreboard (odds/weather), team roster
    get("ESPN core depthcharts (2025 Ohio State id=194)",
        "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/seasons/2025/teams/194/depthcharts")
    get("ESPN core depthcharts (2026)",
        "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/seasons/2026/teams/194/depthcharts")
    get("ESPN site injuries (league-wide)",
        "https://site.api.espn.com/apis/site/v2/sports/football/college-football/injuries")
    get("ESPN site team (roster/injuries links)",
        "https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams/194", params={"enable": "roster,injuries"})
    get("ESPN scoreboard week 1 2026 (odds, weather fields?)",
        "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
        params={"week": 1, "seasontype": 2, "groups": 80, "limit": 300})
    get("ESPN core event odds sample (2024 game 401628378)",
        "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401628378/competitions/401628378/odds")
    get("ESPN core event weather/venue (2024 game 401628378)",
        "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events/401628378")

    # sportsdataverse release assets (free, MIT-style data releases)
    for tag, fn in (("cfbfastR_cfb_pbp", "play_by_play_2024.rds"), ("cfbfastR_cfb_player_stats", "player_stats_2024.rds"),
                    ("cfbfastR_cfb_rosters", "rosters_2024.rds"), ("cfbfastR_cfb_schedules", "schedules_2024.rds"),
                    ("cfbfastR_cfb_team_box", "team_box_2024.rds")):
        url = f"https://github.com/sportsdataverse/sportsdataverse-data/releases/download/{tag}/{fn}"
        try:
            r = requests.head(url, headers=UA, timeout=30, allow_redirects=True)
            print(f"\n=== release asset {tag}/{fn}: HTTP {r.status_code} len={r.headers.get('content-length')}")
        except Exception as exc:  # noqa: BLE001
            print(f"\n=== release asset {tag}/{fn}: ERROR {exc}")

    # Other candidate free sources: reachability + robots
    for label, url in (("footballscoop robots", "https://www.footballscoop.com/robots.txt"),
                       ("collegefootballpoll coaching changes", "https://www.collegefootballpoll.com/coaching-changes/"),
                       ("wikipedia 2024 FBS coaching changes", "https://en.wikipedia.org/wiki/2024_NCAA_Division_I_FBS_football_season#Coaching_changes"),
                       ("cfbd docs redoc", "https://api.collegefootballdata.com/api/docs/?url=/api-docs.json"),
                       ("NWS API points (keyless)", "https://api.weather.gov/points/40.0017,-83.0197")):
        get(label, url, chars=250)
    print("\nSTATUS: read-only probe complete; no metered call spent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
