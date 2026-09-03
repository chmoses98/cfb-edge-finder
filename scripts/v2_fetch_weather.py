#!/usr/bin/env python3
"""Fetch FREE, KEYLESS Open-Meteo weather at kickoff for every V2 game.

Three products, each stored with its own timing class:
  archive   : ERA5/reanalysis OBSERVED conditions (class B proxy -- what
              happened, not what was forecast; 2014-2025)
  hforecast : Historical Forecast API -- archived short-lead forecasts
              (class A-ish; ~2022+)
  prevrun   : Previous Runs API, forecast issued 1 day ahead
              (class A -- a genuine pregame forecast; ~2024+)

One LIGHT request per game (a 2-day window around kickoff), then the
kickoff hour (+3h) is extracted. Open-Meteo weights long hourly ranges as
many call units, so season-long requests throttle; per-game windows are
~1 unit each. Paced to <= ~4,800 requests/hour (limits: 600/min, 5k/hour,
10k/day per IP) with a wall-clock guard that writes whatever was fetched.
Dome games are skipped (the feature builder zeroes them). Most recent
seasons are fetched first so a partial run covers the evaluation folds.
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
    ap.add_argument("--max-calls", type=int, default=9500, help="per run (Open-Meteo free tier: 10k/day)")
    ap.add_argument("--max-minutes", type=float, default=300.0, help="wall-clock guard; partial output is still written")
    ap.add_argument("--sleep", type=float, default=0.75, help="~4,800 requests/hour, under the 5k/hour limit")
    args = ap.parse_args()
    t0 = time.time()
    g = pd.read_csv(args.games)
    g["kick"] = pd.to_datetime(g.kick, utc=True)
    g["kick_hour"] = g.kick.dt.floor("h")
    g = g[~g.dome.fillna(False).astype(bool)]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    calls = 0
    log = []
    for product in args.products:
        base = ENDPOINTS[product]
        sub = g[(g.season >= MIN_SEASON[product]) & (g.season <= 2025)].sort_values(["season", "kick"], ascending=[False, True])
        rows = []
        stopped = None
        for row in sub.itertuples(index=False):
            if calls >= args.max_calls:
                stopped = "budget_exhausted"; break
            if (time.time() - t0) / 60.0 >= args.max_minutes:
                stopped = "time_guard"; break
            kh = row.kick_hour
            start, end = (kh - pd.Timedelta(days=1)).date(), (kh + pd.Timedelta(days=1)).date()
            hourly = HOURLY if product != "prevrun" else ",".join(f"{v}_previous_day1" for v in HOURLY.split(","))
            params = {"latitude": float(row.latitude), "longitude": float(row.longitude), "start_date": str(start),
                      "end_date": str(end), "hourly": hourly, "timezone": "UTC", "wind_speed_unit": "kmh"}
            body, err = None, None
            for attempt in range(3):
                try:
                    r = requests.get(base, params=params, timeout=45)
                    calls += 1
                    if r.status_code == 429:
                        err = "429"; time.sleep(20 * (attempt + 1)); continue
                    r.raise_for_status()
                    body = r.json()
                    break
                except Exception as exc:  # noqa: BLE001
                    err = f"{type(exc).__name__}: {exc}"
                    time.sleep(3)
            if body is None or "hourly" not in body:
                log.append({"product": product, "game_id": str(row.game_id), "status": "fail", "err": err})
                time.sleep(args.sleep)
                continue
            h = pd.DataFrame(body["hourly"])
            h["time"] = pd.to_datetime(h["time"], utc=True)
            h = h.set_index("time")
            win = h.loc[kh: kh + pd.Timedelta(hours=3)]
            if not win.empty:
                rec = {"game_id": str(row.game_id), "season": int(row.season), "product": product, "n_hours": int(len(win))}
                for col in win.columns:
                    key = col.replace("_previous_day1", "")
                    rec[f"{key}_kick"] = float(win.iloc[0][col]) if pd.notna(win.iloc[0][col]) else None
                    rec[f"{key}_mean3h"] = float(win[col].mean()) if win[col].notna().any() else None
                    if key.startswith(("precipitation", "rain", "snowfall")):
                        rec[f"{key}_sum3h"] = float(win[col].sum())
                    if key.startswith("wind_gusts"):
                        rec[f"{key}_max3h"] = float(win[col].max())
                rows.append(rec)
            if calls % 200 == 0:
                print(f"{product}: {calls} calls, {len(rows)} rows, {(time.time() - t0) / 60:.1f} min", flush=True)
            time.sleep(args.sleep)
        out = pd.DataFrame(rows)
        out.to_parquet(args.out_dir / f"weather_{product}.parquet", index=False)
        log.append({"product": product, "status": stopped or "complete", "rows": int(len(out)), "targets": int(len(sub))})
        print(f"{product}: {len(out)} of {len(sub)} game rows ({stopped or 'complete'}), calls so far {calls}", flush=True)
    (args.out_dir / "manifest.json").write_text(json.dumps({
        "source": "https://open-meteo.com (archive-api / historical-forecast-api / previous-runs-api), free, keyless, CC-BY 4.0",
        "products": {p: {"min_season": MIN_SEASON[p], "timing_class": {"archive": "B_observed_proxy", "hforecast": "A_minus_short_lead_forecast", "prevrun": "A_day_ahead_forecast"}[p]} for p in args.products},
        "hourly_vars": HOURLY, "window": "kickoff hour + 3h", "request": "per game, kickoff day +/- 1 day, non-dome only, newest seasons first",
        "calls": calls, "minutes": round((time.time() - t0) / 60, 1), "fetched_at": pd.Timestamp.utcnow().isoformat(), "log": log,
    }, indent=1))
    print("calls", calls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
