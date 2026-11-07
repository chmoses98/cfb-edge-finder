#!/usr/bin/env python3
"""Build the historical-comparables reference used by every research packet. Keyless, read-only.

Joins the committed scoring-baseline validation records (pre-kickoff baseline vs actual score, 2024-2026,
``data/scripting/validation/records_<season>.jsonl.gz``) to ESPN's own per-period linescores (the public scoreboard
carries ``competitors[].linescores`` for completed games) so first-quarter and first-half outcomes can be conditioned on
the same pre-kickoff baseline.

Output: ``data/research_packets/reference/history_games.jsonl.gz`` -- one row per completed game that has a
baseline. Rows carry NO market line, price or odds: this is football history only.

Nothing is imputed. A game whose linescores are missing or do not sum to the final score keeps its full-game row and
carries null periods; it is excluded from period distributions, never estimated into them.

    python scripts/build_historical_reference.py --out-dir data/research_packets/reference
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
ET = ZoneInfo("America/New_York")
SCHEMA = "cfb_history_games/1.0.0"


def read_records(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _periods(competitor: dict[str, Any], final: Any) -> list[float] | None:
    raw = competitor.get("linescores")
    if not isinstance(raw, list) or len(raw) < 4:
        return None
    try:
        values = [float(x["value"]) for x in raw]
        total = float(final)
    except (KeyError, TypeError, ValueError):
        return None
    return values if abs(sum(values) - total) < 1e-6 else None


def fetch_date(session: requests.Session, date_et: str, group: int, retries: int = 3) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for attempt in range(retries):
        try:
            r = session.get(
                SCOREBOARD, params={"dates": date_et.replace("-", ""), "groups": group, "limit": 400}, timeout=45
            )
            r.raise_for_status()
            events = r.json().get("events") or []
            break
        except Exception:  # noqa: BLE001 -- a failed date is reported, never zero
            if attempt == retries - 1:
                return {"__error__": {"date": date_et, "group": group}}
            time.sleep(2 * (attempt + 1))
    for ev in events:
        comp = (ev.get("competitions") or [{}])[0]
        by_side = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        if "home" not in by_side or "away" not in by_side:
            continue
        out[str(ev.get("id"))] = {
            "home_periods": _periods(by_side["home"], by_side["home"].get("score")),
            "away_periods": _periods(by_side["away"], by_side["away"].get("score")),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data/research_packets/reference")
    ap.add_argument("--seasons", type=int, nargs="+", default=[2024, 2025, 2026])
    args = ap.parse_args(argv)
    val = ROOT / "data/scripting/validation"

    rows: list[dict[str, Any]] = []
    for season in args.seasons:
        for r in read_records(val / f"records_{season}.jsonl.gz"):
            if r.get("pred_total") is None or r.get("actual_home") is None:
                continue
            rows.append(r)
    print(f"{len(rows)} baseline games")

    dates = sorted(
        {
            datetime.fromisoformat(str(r["kickoff_utc"]).replace("Z", "+00:00")).astimezone(ET).strftime("%Y-%m-%d")
            for r in rows
        }
    )
    # A game keyed by ET date may sit on the next UTC/ET calendar day in ESPN's scoreboard; fetch each date +-0.
    session = requests.Session()
    linescores: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for i, d in enumerate(dates):
        for group in (80, 81):
            got = fetch_date(session, d, group)
            if "__error__" in got:
                errors.append(got["__error__"])
                continue
            linescores.update(got)
        if i % 20 == 0:
            print(f"  {i}/{len(dates)} dates, {len(linescores)} games with scoreboard rows")

    out_rows: list[dict[str, Any]] = []
    with_periods = 0
    reoriented = dropped_unreconciled = 0
    for r in sorted(rows, key=lambda x: (x["kickoff_utc"], x["game_id"])):
        ls = linescores.get(str(r["game_id"])) or {}
        hp, ap_ = ls.get("home_periods"), ls.get("away_periods")
        has = bool(hp and ap_ and len(hp) == len(ap_))
        if has:
            # ORIENTATION GUARD. ESPN's scoreboard and the baseline records can disagree about which side is "home" in a
            # neutral-site game (two 2024 bowls did). Linescores are attached only when they reconcile to the record's
            # own
            # final score; a swapped pair is re-oriented by score, anything else is dropped, never guessed.
            straight = sum(hp) == r["actual_home"] and sum(ap_) == r["actual_away"]
            swapped = sum(hp) == r["actual_away"] and sum(ap_) == r["actual_home"]
            if not straight and swapped:
                hp, ap_ = ap_, hp
                reoriented += 1
            elif not straight:
                has = False
                dropped_unreconciled += 1
        with_periods += has
        out_rows.append(
            {
                "game_id": str(r["game_id"]),
                "season": r["season"],
                "week": r.get("week"),
                "kickoff_utc": r["kickoff_utc"],
                "home": r["home"],
                "away": r["away"],
                "neutral": bool(r.get("neutral_site")),
                "conference_game": r.get("conference_game"),
                "fcs_involved": r.get("fcs_involved"),
                "confidence": r.get("confidence_published"),
                "prior_games": r.get("prior_games"),
                "pred_home": r["pred_home"],
                "pred_away": r["pred_away"],
                "pred_total": r["pred_total"],
                "pred_uncertainty_points": r.get("uncertainty_points"),
                "actual_home": r["actual_home"],
                "actual_away": r["actual_away"],
                "home_periods": [int(x) for x in hp] if has else None,
                "away_periods": [int(x) for x in ap_] if has else None,
            }
        )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    path = args.out_dir / "history_games.jsonl.gz"
    with gzip.GzipFile(path, "wb", mtime=0) as fh:
        for row in out_rows:
            fh.write((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode())
    manifest = {
        "schema_version": SCHEMA,
        "source": "data/scripting/validation/records_<season>.jsonl.gz (baseline vs actual) + ESPN scoreboard "
            "linescores",
        "games": len(out_rows),
        "games_with_period_linescores": with_periods,
        "seasons": args.seasons,
        "linescores_reoriented_to_match_final_score": reoriented,
        "linescores_dropped_unreconciled": dropped_unreconciled,
        "scoreboard_fetch_failures": errors,
        "carries_market_data": False,
    }
    (args.out_dir / "history_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(json.dumps(manifest, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
