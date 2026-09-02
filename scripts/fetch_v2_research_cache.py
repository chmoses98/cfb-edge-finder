"""Fetch the CFB MODEL V2 historical research cache from CFBD -- one
bounded, budgeted, read-only acquisition run on a GitHub-hosted runner.

*** WHY THIS EXISTS ***
The V2 research program (docs/CFB_MODEL_V2_RESEARCH_REPORT.md) needs far
more than the preseason cache: per-team-game efficiency (PPA, success
rate, explosiveness, havoc, line yards, down splits), box-score stats,
drives, historical closing lines (EVALUATION ONLY), conference
membership by season, recruiting classes, preseason polls, venues, and
prior-season summary ratings. Every one of those is a WHOLE-SEASON pull
(one request per endpoint per season), so a 13-season cache costs a few
hundred metered calls, not thousands.

*** BUDGET DISCIPLINE ***
- /info (unmetered) is read before and after the run so the manifest
  records the exact number of metered calls the run consumed.
- A hard cap (`--max-calls`) aborts the run before the next request once
  reached; the partial cache is still written and pushed.
- The run refuses to start if remaining quota is below `--min-remaining`
  so the 5-minute collector's own headroom is never consumed.

*** TIMING / LEAKAGE CLASSIFICATION IS RECORDED, NOT ASSUMED ***
Every endpoint carries a `timing_semantics` note and a `v2_use` verdict
in the manifest. Per-game box/efficiency endpoints are POSTGAME for their
own game and may only ever feed features of STRICTLY LATER games
(enforced downstream by the dataset builder, never here). /lines is
EVALUATION ONLY. Season-aggregate endpoints (/stats/season/advanced,
/ratings/sp) are postgame for their own season and are used ONLY as
prior-season (S-1) preseason features for season S.

*** 2026 FIREWALL ***
For the current season, only preseason-known endpoints are fetched
(schedule, teams, talent, returning production, recruiting, coaches,
portal, preseason poll). No per-game postgame endpoint is fetched for
2026 -- 2026 outcomes are off limits for V2 fitting and selection.

*** OUTPUT ***
data/research_cache/v2/<season>/<endpoint>.json.gz  (gzipped JSON)
data/research_cache/v2/venues.json.gz
data/research_cache/v2/manifest.json                 (provenance)

The API key is never printed or written.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.config import Settings  # noqa: E402
from cfb_edge_finder.data.cfbd_client import CFBDClient  # noqa: E402

CACHE_VERSION = "v2_research_cache_v1"

# (name, path, params-builder, timing_semantics, v2_use, current_season_ok)
# `current_season_ok` = safe to fetch for the live 2026 season (preseason-known only).
HISTORICAL_ENDPOINTS: list[dict] = [
    {
        "name": "games",
        "path": "/games",
        "params": lambda s: {"year": s, "classification": "fbs"},
        "timing": "scores/lineScores/attendance/excitement/postgame Elo/WP are POSTGAME (targets only); "
        "schedule, venue, neutral, conference, PREGAME Elo are pregame-known",
        "use": "TARGETS + situational pregame features + pregame Elo",
        "current": True,
    },
    {
        "name": "games_teams",
        "path": "/games/teams",
        "params": lambda s: {"year": s, "classification": "fbs"},
        "timing": "POSTGAME box score for its own game; usable only for STRICTLY PRIOR games",
        "use": "rolling team-form features (yards, turnovers, 3rd down, penalties, possession)",
        "current": False,
        "fallback_per_week": True,
    },
    {
        "name": "advanced_regular",
        "path": "/stats/game/advanced",
        "params": lambda s: {"year": s, "seasonType": "regular"},
        "timing": "POSTGAME advanced efficiency for its own game; usable only for STRICTLY PRIOR games",
        "use": "rolling efficiency features (PPA, success rate, explosiveness, havoc, line yards, down splits)",
        "current": False,
    },
    {
        "name": "advanced_postseason",
        "path": "/stats/game/advanced",
        "params": lambda s: {"year": s, "seasonType": "postseason"},
        "timing": "POSTGAME; bowl/playoff games; usable only for STRICTLY PRIOR games and prior-season summaries",
        "use": "as above",
        "current": False,
    },
    {
        "name": "advanced_regular_nogarbage",
        "path": "/stats/game/advanced",
        "params": lambda s: {"year": s, "seasonType": "regular", "excludeGarbageTime": "true"},
        "timing": "POSTGAME; garbage-time-excluded variant",
        "use": "rolling efficiency features (garbage-time excluded variant, to be compared)",
        "current": False,
    },
    {
        "name": "drives_regular",
        "path": "/drives",
        "params": lambda s: {"year": s, "seasonType": "regular", "classification": "fbs"},
        "timing": "POSTGAME per drive; usable only for STRICTLY PRIOR games",
        "use": "finishing drives, field position, drive efficiency, tempo (compacted fields only)",
        "current": False,
        "compact": "drives",
    },
    {
        "name": "drives_postseason",
        "path": "/drives",
        "params": lambda s: {"year": s, "seasonType": "postseason", "classification": "fbs"},
        "timing": "POSTGAME per drive",
        "use": "as above",
        "current": False,
        "compact": "drives",
    },
    {
        "name": "lines_regular",
        "path": "/lines",
        "params": lambda s: {"year": s, "seasonType": "regular"},
        "timing": "opening lines are pregame; CLOSING lines are pregame but market-derived",
        "use": "EVALUATION ONLY benchmark -- never a V2 football-model feature",
        "current": False,
    },
    {
        "name": "lines_postseason",
        "path": "/lines",
        "params": lambda s: {"year": s, "seasonType": "postseason"},
        "timing": "as above",
        "use": "EVALUATION ONLY",
        "current": False,
    },
    {
        "name": "rankings",
        "path": "/rankings",
        "params": lambda s: {"year": s, "seasonType": "regular"},
        "timing": "poll for week W is released BEFORE week W games (week 1 = preseason poll); "
        "downstream builder must verify alignment and lag if unsure",
        "use": "preseason poll consensus as a preseason strength signal (timing verified downstream)",
        "current": True,
    },
    {
        "name": "teams_fbs",
        "path": "/teams/fbs",
        "params": lambda s: {"year": s},
        "timing": "conference membership for season S is fixed before S",
        "use": "season-scoped conference membership, team ids, venue links",
        "current": True,
    },
    {
        "name": "recruiting_teams",
        "path": "/recruiting/teams",
        "params": lambda s: {"year": s},
        "timing": "class of year S signs in the S-1 cycle (Dec/Feb); known before season S",
        "use": "multi-year recruiting strength (preseason feature)",
        "current": True,
    },
    {
        "name": "talent",
        "path": "/talent",
        "params": lambda s: {"year": s},
        "timing": "247 composite entering season S; settled in the S-1 cycle",
        "use": "preseason talent (the 0.5.0 prior input)",
        "current": True,
    },
    {
        "name": "returning_production",
        "path": "/player/returning",
        "params": lambda s: {"year": s},
        "timing": "published pre-season S describing production returning FROM S-1",
        "use": "preseason continuity features",
        "current": True,
    },
    {
        "name": "coaches",
        "path": "/coaches",
        "params": lambda s: {"year": s},
        "timing": "identity/school for S known preseason; per-season W/L inside `seasons` is POSTGAME "
        "(dropped at compaction)",
        "use": "coaching change / tenure (preseason feature)",
        "current": True,
        "compact": "coaches",
    },
    {
        "name": "season_advanced",
        "path": "/stats/season/advanced",
        "params": lambda s: {"year": s},
        "timing": "POSTGAME aggregate for season S -- usable ONLY as a prior-season (S-1) feature for S+1",
        "use": "prior-season efficiency profile (preseason feature for the NEXT season)",
        "current": False,
    },
    {
        "name": "ratings_sp",
        "path": "/ratings/sp",
        "params": lambda s: {"year": s},
        "timing": "END-OF-SEASON SP+ for season S -- POSTGAME for S; usable ONLY as a prior-season feature for S+1",
        "use": "prior-season strength reference (preseason feature for the NEXT season); never in-season",
        "current": False,
    },
    {
        "name": "portal",
        "path": "/player/portal",
        "params": lambda s: {"year": s},
        "timing": "each entry carries transferDate; as-of view requires filtering on transferDate < season start; "
        "ratings/destinations may be RETROACTIVELY REVISED",
        "use": "DESCRIPTIVE availability check only unless timing is proven downstream",
        "current": True,
        "min_season": 2021,
    },
]

DRIVE_FIELDS = (
    "gameId", "id", "offense", "defense", "driveNumber", "scoring", "startPeriod", "startYardline",
    "startYardsToGoal", "endPeriod", "endYardline", "endYardsToGoal", "plays", "yards", "driveResult",
    "isHomeOffense", "startTime", "endTime", "elapsed",
)


def fingerprint(rows) -> str:
    keys: set[str] = set()
    sample = rows[:500] if isinstance(rows, list) else [rows]
    for row in sample:
        if isinstance(row, dict):
            keys.update(row.keys())
    return hashlib.sha256(",".join(sorted(keys)).encode()).hexdigest()[:16]


def compact_drives(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        item = {}
        for f in DRIVE_FIELDS:
            if f in r:
                item[f] = r[f]
            else:
                # snake_case fallback
                alt = "".join("_" + c.lower() if c.isupper() else c for c in f)
                if alt in r:
                    item[f] = r[alt]
        out.append(item)
    return out


def compact_coaches(rows: list[dict], season: int) -> list[dict]:
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        first = row.get("firstName") or row.get("first_name") or ""
        last = row.get("lastName") or row.get("last_name") or ""
        for entry in row.get("seasons") or []:
            if not isinstance(entry, dict) or entry.get("year") != season:
                continue
            out.append({
                "year": season,
                "school": entry.get("school"),
                "coach": f"{first} {last}".strip(),
                "hireDate": row.get("hireDate") or row.get("hire_date"),
            })
    return out


class Budget:
    def __init__(self, max_calls: int):
        self.max_calls = max_calls
        self.calls = 0
        self.log: list[dict] = []

    def charge(self, path: str, params: dict, status: str, rows: int, seconds: float) -> None:
        self.calls += 1
        self.log.append({"path": path, "params": params, "status": status, "rows": rows, "seconds": round(seconds, 2)})

    def exhausted(self) -> bool:
        return self.calls >= self.max_calls


def write_gz(path: Path, payload) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    with gzip.open(path, "wb", compresslevel=9) as fh:
        fh.write(data)
    return path.stat().st_size


def fetch(client: CFBDClient, budget: Budget, path: str, params: dict):
    """One metered call, logged. Returns (rows, error_string)."""
    t0 = time.time()
    try:
        rows = client.fetch_raw(path, params)
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        budget.charge(path, params, f"HTTP {status}", 0, time.time() - t0)
        return None, f"HTTP {status}"
    except requests.RequestException as exc:
        budget.charge(path, params, type(exc).__name__, 0, time.time() - t0)
        return None, type(exc).__name__
    n = len(rows) if isinstance(rows, list) else 1
    budget.charge(path, params, "ok", n, time.time() - t0)
    return rows, None


def quota_snapshot(client: CFBDClient) -> dict | None:
    try:
        info = client.fetch_account_info()
    except Exception as exc:  # noqa: BLE001 -- telemetry only
        return {"error": type(exc).__name__}
    return {k: info.get(k) for k in ("tierName", "monthlyLimit", "remainingCalls", "usedCalls", "resetAt")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+", required=True, help="historical seasons (full fetch)")
    parser.add_argument("--current-season", type=int, default=None, help="live season: preseason-known endpoints only")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=520)
    parser.add_argument("--min-remaining", type=int, default=600)
    parser.add_argument("--skip", nargs="*", default=[], help="endpoint names to skip")
    parser.add_argument("--only", nargs="*", default=None, help="endpoint names to fetch (default all)")
    args = parser.parse_args()

    settings = Settings.from_env()
    key = getattr(settings, "cfbd_api_key", None)
    if not key:
        print("CFBD_API_KEY absent -- refusing to run (no fixture mode for a real acquisition).")
        return 2
    client = CFBDClient(api_key=key, timeout_seconds=120.0)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    quota_before = quota_snapshot(client)
    print(f"quota before: {quota_before}")
    remaining = (quota_before or {}).get("remainingCalls")
    if isinstance(remaining, int) and remaining < args.min_remaining:
        print(f"remaining quota {remaining} < --min-remaining {args.min_remaining}; refusing to run")
        return 3

    budget = Budget(args.max_calls)
    manifest: dict = {
        "cache_version": CACHE_VERSION,
        "fetched_at": datetime.now(UTC).isoformat(),
        "api_key_present": True,
        "source": "https://api.collegefootballdata.com",
        "seasons": sorted(args.seasons),
        "current_season": args.current_season,
        "quota_before": quota_before,
        "endpoints": {},
        "aborted": False,
        "not_fetched": {
            "/roster": "retroactively revised; no as-of snapshot",
            "/ratings/elo by week": "redundant: /games carries homePregameElo/awayPregameElo",
            "/plays": "too expensive per week for the free tier; /stats/game/advanced + /drives cover the needs",
            "weather": "only realised conditions retrievable, not the pregame forecast",
            "injuries": "no structured historical source",
            "2026 per-game postgame endpoints": "FIREWALLED -- 2026 outcomes are off limits for V2",
        },
    }

    def run_endpoint(season: int, spec: dict, current: bool) -> None:
        name = spec["name"]
        if args.only is not None and name not in args.only:
            return
        if name in args.skip:
            return
        if season < spec.get("min_season", 0):
            return
        if current and not spec["current"]:
            return
        if budget.exhausted():
            manifest["aborted"] = True
            return
        params = spec["params"](season)
        rows, err = fetch(client, budget, spec["path"], params)
        note = {
            "path": spec["path"],
            "params": params,
            "timing_semantics": spec["timing"],
            "v2_use": spec["use"],
            "error": err,
            "raw_rows": None,
            "kept_rows": None,
            "schema_fingerprint": None,
            "bytes_gz": None,
        }
        if rows is None and spec.get("fallback_per_week") and not budget.exhausted():
            # Year-only rejected: fall back to per-week pulls (bounded).
            merged: list = []
            weeks_ok = 0
            for st, weeks in (("regular", range(1, 17)), ("postseason", range(1, 2))):
                for wk in weeks:
                    if budget.exhausted():
                        manifest["aborted"] = True
                        break
                    p2 = dict(params)
                    p2.update({"week": wk, "seasonType": st})
                    r2, e2 = fetch(client, budget, spec["path"], p2)
                    if r2:
                        merged.extend(r2)
                        weeks_ok += 1
                    elif e2 and not e2.startswith("HTTP 4"):
                        break
            rows = merged
            err = None if merged else "fallback produced no rows"
            note["fallback_per_week"] = weeks_ok
            note["error"] = err
        if rows is not None:
            note["raw_rows"] = len(rows) if isinstance(rows, list) else 1
            note["schema_fingerprint"] = fingerprint(rows)
            if spec.get("compact") == "drives":
                rows = compact_drives(rows)
            elif spec.get("compact") == "coaches":
                rows = compact_coaches(rows, season)
            note["kept_rows"] = len(rows) if isinstance(rows, list) else 1
            note["bytes_gz"] = write_gz(args.out_dir / str(season) / f"{name}.json.gz", rows)
        manifest["endpoints"].setdefault(str(season), {})[name] = note
        print(f"  {season} {name:28s} {note['error'] or 'ok':10s} rows={note['raw_rows']} gz={note['bytes_gz']}")

    for season in sorted(args.seasons):
        print(f"season {season}")
        for spec in HISTORICAL_ENDPOINTS:
            run_endpoint(season, spec, current=False)
    if args.current_season is not None:
        print(f"current season {args.current_season} (preseason-known endpoints only)")
        for spec in HISTORICAL_ENDPOINTS:
            run_endpoint(args.current_season, spec, current=True)

    if (args.only is None or "venues" in args.only) and "venues" not in args.skip and not budget.exhausted():
        rows, err = fetch(client, budget, "/venues", {})
        note = {
            "path": "/venues",
            "error": err,
            "timing_semantics": "static venue metadata (lat/long/elevation/dome/timezone)",
            "v2_use": "travel distance, altitude, dome, timezone (pregame-known)",
        }
        if rows is not None:
            note["raw_rows"] = len(rows)
            note["schema_fingerprint"] = fingerprint(rows)
            note["bytes_gz"] = write_gz(args.out_dir / "venues.json.gz", rows)
        manifest["endpoints"]["static"] = {"venues": note}
        print(f"  venues {err or 'ok'} rows={note.get('raw_rows')}")

    quota_after = quota_snapshot(client)
    manifest["quota_after"] = quota_after
    manifest["metered_calls_logged"] = budget.calls
    manifest["call_log"] = budget.log
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"\nmetered calls: {budget.calls}; quota after: {quota_after}; aborted={manifest['aborted']}")
    print("NOTE: the API key is never written to the cache or the manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
