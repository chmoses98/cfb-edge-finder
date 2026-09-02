#!/usr/bin/env python3
"""Fetch FREE, KEYLESS Open-Meteo weather at kickoff for every V2 game.

Three products, each stored with its own timing class:
  archive   : ERA5/reanalysis OBSERVED conditions (class B proxy -- what
              happened, not what was forecast; 2014-2025)
  hforecast : Historical Forecast API -- archived short-lead forecasts
              (class A-ish; ~2022+)
  prevrun   : Previous Runs API, forecast issued 1 day ahead
              (class A -- a genuine pregame forecast; ~2024+)

One request per (venue, season) covering the season's date range, then
the kickoff hour (+/- the game window) is extracted for each game. Paced
to stay well inside Open-Meteo's free limits (600/min, 5k/hour, 10k/day).
Read-only; no API key; nothing touches production."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd
import requests

HOURLY = "temperature_2m,relative_humidity_2m,precipitation,rain,snowfall,wind_speed_10m,wind_gusts_10m,wind_direction_10m,cloud_cover"
ENDPOINTS = {
    "archive": "https://archive-api.open-meteo.com/v1/archive",
    "hforecast": "https://historical-forecast-api.open-meteo.com/v1/forecast",
    "prevrun": "https://previous-runs-api.open-meteo.com/v1/forecast",
}
MIN_SEASON = {"archive": 2014, "hforecast": 2022, "prevrun": 2024}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--products", nargs="+", default=["archive", "hforecast", "prevrun"])
    ap.add_argument("--max-calls", type=int, default=4500)
    ap.add_argument("--sleep", type=float, default=0.25)
    args = ap.parse_args()
    g = pd.read_csv(args.games)
    g["kick"] = pd.to_datetime(g.kick, utc=True)
    g["kick_hour"] = g.kick.dt.floor("h")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    calls = 0
    log = []
    for product in args.products:
        base = ENDPOINTS[product]
        sub = g[g.season >= MIN_SEASON[product]]
        rows = []
        for (vid, season), grp in sub.groupby(["venue_id", "season"]):
            if calls >= args.max_calls:
                log.append({"product": product, "status": "budget_exhausted"})
                break
            lat, lon = float(grp.latitude.iloc[0]), float(grp.longitude.iloc[0])
            start, end = (grp.kick.min() - pd.Timedelta(days=1)).date(), (grp.kick.max() + pd.Timedelta(days=1)).date()
            hourly = HOURLY if product != "prevrun" else ",".join(f"{v}_previous_day1" for v in HOURLY.split(","))
            params = {"latitude": lat, "longitude": lon, "start_date": str(start), "end_date": str(end),
                      "hourly": hourly, "timezone": "UTC", "wind_speed_unit": "kmh"}
            for attempt in range(4):
                try:
                    r = requests.get(base, params=params, timeout=60)
                    calls += 1
                    if r.status_code == 429:
                        time.sleep(15 * (attempt + 1)); continue
                    r.raise_for_status()
                    body = r.json()
                    break
                except Exception as exc:  # noqa: BLE001
                    body = None
                    err = f"{type(exc).__name__}: {exc}"
                    time.sleep(5)
            if body is None or "hourly" not in body:
                log.append({"product": product, "venue_id": int(vid), "season": int(season), "status": "fail", "err": locals().get("err")})
                continue
            h = pd.DataFrame(body["hourly"])
            h["time"] = pd.to_datetime(h["time"], utc=True)
            h = h.set_index("time")
            for gid, kh in zip(grp.game_id, grp.kick_hour):
                win = h.loc[kh: kh + pd.Timedelta(hours=3)]
                if win.empty:
                    continue
                rec = {"game_id": str(gid), "product": product, "n_hours": int(len(win))}
                for col in win.columns:
                    key = col.replace("_previous_day1", "")
                    rec[f"{key}_kick"] = float(win.iloc[0][col]) if pd.notna(win.iloc[0][col]) else None
                    rec[f"{key}_mean3h"] = float(win[col].mean()) if win[col].notna().any() else None
                    if key.startswith(("precipitation", "rain", "snowfall")):
                        rec[f"{key}_sum3h"] = float(win[col].sum())
                    if key.startswith("wind_gusts"):
                        rec[f"{key}_max3h"] = float(win[col].max())
                rows.append(rec)
            time.sleep(args.sleep)
        out = pd.DataFrame(rows)
        out.to_parquet(args.out_dir / f"weather_{product}.parquet", index=False)
        print(f"{product}: {len(out)} game rows, calls so far {calls}", flush=True)
    (args.out_dir / "manifest.json").write_text(json.dumps({
        "source": "https://open-meteo.com (archive-api / historical-forecast-api / previous-runs-api), free, keyless, CC-BY 4.0",
        "products": {p: {"min_season": MIN_SEASON[p], "timing_class": {"archive": "B_observed_proxy", "hforecast": "A_minus_short_lead_forecast", "prevrun": "A_day_ahead_forecast"}[p]} for p in args.products},
        "hourly_vars": HOURLY, "window": "kickoff hour + 3h", "calls": calls, "fetched_at": pd.Timestamp.utcnow().isoformat(), "log": log,
    }, indent=1))
    print("calls", calls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
