#!/usr/bin/env python3
"""LIVE INFORMATION SIDECAR (research-only, append-only, timestamped).

Captures pregame information that has NO honest historical archive, so
that a timing-safe dataset starts accumulating now:

  * Open-Meteo hourly FORECAST at each upcoming game's kickoff hour, with
    the forecast lead time recorded (free, keyless);
  * ESPN core-API odds for each upcoming event (per-book spread/total/
    moneyline as currently posted);
  * ESPN core-API injury lists for both teams (often empty; the empty
    observation is itself recorded).

Every row carries fetched_at, source URL, a sha256 of the raw payload,
the parsed facts and the game identity (CFBD/ESPN game id). Rows are
appended, never rewritten. This script writes ONLY under --out-dir; the
workflow commits that directory to the isolated `research-sidecar`
orphan branch -- never research-data, never main. It cannot alter
0.5.0 outputs, never touches the collector, and spends zero CFBD calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

UA = {"User-Agent": "cfb-edge-finder live-info sidecar (research-only, read-only)"}
ESPN = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football"


def _get(url: str, params: dict | None = None, retries: int = 1) -> tuple[int, str]:
    """One GET with at most ``retries`` extra attempts on transport failure / 429 / 5xx.

    Never raises: a final failure is returned as (0, message) so it is recorded as
    a row and retried by the next capture instead of aborting the run.
    """
    last: tuple[int, str] = (0, "no attempt")
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=40)
            last = (r.status_code, r.text)
            if r.status_code < 500 and r.status_code != 429:
                return last
        except Exception as exc:  # noqa: BLE001
            last = (0, f"{type(exc).__name__}: {exc}")
        if attempt < retries:
            time.sleep(3.0 * (attempt + 1))
    return last


def _row(
    kind: str, game_id: str, source: str, status: int, raw: str, parsed: dict, now: datetime, extra: dict | None = None
) -> dict:
    return {
        "schema_version": "live_sidecar_v1",
        "kind": kind,
        "game_id": str(game_id),
        "fetched_at": now.isoformat(),
        "source": source,
        "http_status": status,
        "payload_sha256": hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest(),
        "parsed": parsed,
        **(extra or {}),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=Path, required=True, help="csv with game_id,kick,latitude,longitude,season")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--horizon-days", type=int, default=8)
    ap.add_argument("--max-games", type=int, default=120)
    args = ap.parse_args()
    now = datetime.now(UTC)
    g = pd.read_csv(args.games)
    g["kick"] = pd.to_datetime(g.kick, utc=True)
    up = (
        g[(g.kick > now) & (g.kick <= now + timedelta(days=args.horizon_days))].sort_values("kick").head(args.max_games)
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    season = int(up.season.max()) if len(up) else now.year
    out = {k: [] for k in ("weather_forecast", "espn_odds", "espn_injuries")}
    teams_seen: set[str] = set()
    for r in up.itertuples(index=False):
        lead_h = (r.kick - now).total_seconds() / 3600.0
        # --- weather forecast at kickoff hour ---------------------------------
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": float(r.latitude),
            "longitude": float(r.longitude),
            "timezone": "UTC",
            "hourly": "temperature_2m,relative_humidity_2m,precipitation,precipitation_probability,rain,"
                "snowfall,wind_speed_10m,wind_gusts_10m,wind_direction_10m,cloud_cover",
            "forecast_days": 14,
            "wind_speed_unit": "kmh",
        }
        st, txt = _get(url, params)
        parsed: dict = {}
        if st == 200:
            try:
                body = json.loads(txt)
                h = pd.DataFrame(body["hourly"])
                h["time"] = pd.to_datetime(h.time, utc=True)
                kh = r.kick.floor("h")
                win = h[(h.time >= kh) & (h.time <= kh + pd.Timedelta(hours=3))]
                if len(win):
                    parsed = {
                        c: (float(win.iloc[0][c]) if pd.notna(win.iloc[0][c]) else None)
                        for c in win.columns
                        if c != "time"
                    }
                    parsed["wind_gusts_10m_max3h"] = float(win.wind_gusts_10m.max())
                    parsed["precipitation_sum3h"] = float(win.precipitation.sum())
            except Exception as exc:  # noqa: BLE001
                parsed = {"parse_error": f"{type(exc).__name__}: {exc}"}
        out["weather_forecast"].append(
            _row(
                "weather_forecast",
                r.game_id,
                url,
                st,
                txt,
                parsed,
                now,
                {
                    "kickoff_utc": r.kick.isoformat(),
                    "lead_hours": round(lead_h, 2),
                    "venue_id": int(r.venue_id) if pd.notna(r.venue_id) else None,
                },
            )
        )
        time.sleep(0.15)
        # --- ESPN odds ---------------------------------------------------------
        url = f"{ESPN}/events/{r.game_id}/competitions/{r.game_id}/odds"
        st, txt = _get(url)
        parsed = {}
        if st == 200:
            try:
                body = json.loads(txt)
                books = []
                for it in body.get("items", []):
                    books.append(
                        {
                            "provider": (it.get("provider") or {}).get("name"),
                            "details": it.get("details"),
                            "spread": it.get("spread"),
                            "over_under": it.get("overUnder"),
                            "home_ml": ((it.get("homeTeamOdds") or {}).get("moneyLine")),
                            "away_ml": ((it.get("awayTeamOdds") or {}).get("moneyLine")),
                            "home_favorite": (it.get("homeTeamOdds") or {}).get("favorite"),
                        }
                    )
                parsed = {"n_books": len(books), "books": books}
            except Exception as exc:  # noqa: BLE001
                parsed = {"parse_error": f"{type(exc).__name__}: {exc}"}
        out["espn_odds"].append(
            _row(
                "espn_odds",
                r.game_id,
                url,
                st,
                txt,
                parsed,
                now,
                {"kickoff_utc": r.kick.isoformat(), "lead_hours": round(lead_h, 2)},
            )
        )
        time.sleep(0.15)
    # --- ESPN injuries per team (via event competitors) ----------------------
    for r in up.itertuples(index=False):
        st, txt = _get(f"{ESPN}/events/{r.game_id}/competitions/{r.game_id}/competitors")
        if st != 200:
            continue
        try:
            comps = json.loads(txt).get("items", [])
        except Exception:  # noqa: BLE001
            continue
        for c in comps:
            ref = (c.get("team") or {}).get("$ref", "")
            tid = ref.rstrip("/").split("/")[-1].split("?")[0] if ref else None
            if not tid or tid in teams_seen:
                continue
            teams_seen.add(tid)
            url = f"{ESPN}/teams/{tid}/injuries"
            st2, txt2 = _get(url)
            parsed = {}
            if st2 == 200:
                try:
                    body = json.loads(txt2)
                    parsed = {"count": body.get("count"), "items": [i.get("$ref") for i in body.get("items", [])][:50]}
                except Exception as exc:  # noqa: BLE001
                    parsed = {"parse_error": f"{type(exc).__name__}: {exc}"}
            out["espn_injuries"].append(
                _row("espn_injuries", r.game_id, url, st2, txt2, parsed, now, {"espn_team_id": tid})
            )
            time.sleep(0.15)
    for kind, rows in out.items():
        p = args.out_dir / kind / f"{season}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, default=str) + "\n")
        print(f"{kind}: appended {len(rows)} rows -> {p}")
    print(f"upcoming games considered: {len(up)}; fetched_at={now.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
