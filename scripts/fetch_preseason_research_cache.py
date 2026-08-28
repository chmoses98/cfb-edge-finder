#!/usr/bin/env python3
"""Fetch the leakage-safe historical inputs for preseason-prior research.

Runs on a GitHub-hosted runner where CFBD egress and CFBD_API_KEY exist.
Read-only against CFBD. Writes a COMPACT cache plus a provenance manifest.

*** WHY COMPACT, NOT RAW ***

Only the fields the audited features actually need are kept. Committing
whole raw payloads would bloat the repository, and -- worse -- would
tempt a later reader to use a field that never passed the as-of audit.
The manifest records a schema fingerprint of the FULL payload, so a
shape change upstream is still detectable even though the bulk is
discarded.

*** THE SECRET IS NEVER PRINTED ***

The key is read from the environment by `Settings.from_env()` and handed
to the client. Nothing here logs it, echoes it, or writes it to the
cache, and the manifest records only whether a key was present.

*** WHAT IT DELIBERATELY DOES NOT FETCH ***

Endpoints the source audit rejected: /roster (retroactively revised),
/ratings/* (pre/post-week timing unconfirmed), transfer portal (no
historical snapshots), weather, injuries. Fetching them "just in case"
would put unusable data one convenient step from being used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.config import Settings  # noqa: E402
from cfb_edge_finder.data.cfbd_client import CFBDClient  # noqa: E402

CACHE_VERSION = "preseason_research_cache_v1"

GAME_FIELDS = (
    "id", "season", "week", "seasonType", "startDate", "neutralSite", "conferenceGame",
    "homeTeam", "awayTeam", "homePoints", "awayPoints", "homeClassification",
    "awayClassification", "completed",
)
"""Only what the control and the evaluation need. Scores are the target
variable; classification and neutral site are pregame metadata."""

RETURNING_FIELDS = (
    "season", "team", "conference", "totalPPA", "percentPPA", "percentPassingPPA",
    "percentReceivingPPA", "percentRushingPPA", "usage", "passingUsage",
    "receivingUsage", "rushingUsage",
)
"""The full returning-production split set. The control uses only the
passing share; the broader splits are Candidate A."""

TALENT_FIELDS = ("year", "school", "talent")

COACH_FIELDS = ("firstName", "lastName", "school", "year")
"""Identity and season ONLY. The per-season win/loss and ranking fields
inside /coaches' nested `seasons` are POSTGAME for their own season and
are deliberately dropped so they cannot leak."""


def fingerprint(rows: list[dict]) -> str:
    """Schema fingerprint over the union of keys in the FULL payload.

    Computed before compaction, so an upstream shape change is detected
    even though the extra fields are then discarded."""
    keys: set[str] = set()
    for row in rows[:500]:
        if isinstance(row, dict):
            keys.update(row.keys())
    return hashlib.sha256(",".join(sorted(keys)).encode()).hexdigest()[:16]


def compact(rows: list[dict], fields: tuple[str, ...]) -> list[dict]:
    """Keep only audited fields. Accepts camelCase or snake_case keys, as
    CFBD has served both across versions."""
    def alt(name: str) -> str:
        out = [name[0].lower()]
        for ch in name[1:]:
            out.append("_" + ch.lower() if ch.isupper() else ch)
        return "".join(out)

    kept = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = {}
        for field in fields:
            if field in row:
                item[field] = row[field]
            elif alt(field) in row:
                item[field] = row[alt(field)]
            else:
                item[field] = None
        kept.append(item)
    return kept


def flatten_coaches(rows: list[dict], season: int) -> list[dict]:
    """One row per (coach, school) for THIS season only.

    /coaches nests a `seasons` list; entries for other seasons are
    dropped, and every outcome statistic inside them is dropped with
    them. Reading a later season's record would reveal whether the hire
    worked out."""
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        first = row.get("firstName") or row.get("first_name") or ""
        last = row.get("lastName") or row.get("last_name") or ""
        for entry in row.get("seasons") or []:
            if not isinstance(entry, dict):
                continue
            year = entry.get("year")
            if year != season:
                continue
            out.append({
                "year": year,
                "school": entry.get("school"),
                "coach": f"{first} {last}".strip(),
            })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", type=int, nargs="+", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--include-advanced", action="store_true",
        help="Also fetch /stats/game/advanced (pace). One extra call per season.",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    key = getattr(settings, "cfbd_api_key", None)
    client = CFBDClient(api_key=key)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict = {
        "cache_version": CACHE_VERSION,
        "fetched_at": datetime.now(UTC).isoformat(),
        "api_key_present": bool(key),
        "source": "https://api.collegefootballdata.com",
        "seasons": sorted(args.seasons),
        "endpoints": {},
        "not_fetched": {
            "/roster": "retroactively revised; no as-of snapshot",
            "/ratings/*": "pre/post-week timing unconfirmed",
            "transfer_portal": "no historical snapshots exist",
            "weather": "only realised conditions retrievable, not pregame forecast",
            "injuries": "no structured historical source",
        },
    }

    for season in sorted(args.seasons):
        season_payload: dict = {"season": season}
        endpoint_notes: dict = {}

        def record(
            name: str, raw: list[dict], kept: list[dict], timing: str, usable: bool,
            *, notes: dict = endpoint_notes,
        ) -> None:
            # `notes` is bound as a default argument on purpose: a closure
            # over the loop variable would make every season's record()
            # write into the LAST season's dict.
            notes[name] = {
                "raw_rows": len(raw),
                "kept_rows": len(kept),
                "schema_fingerprint": fingerprint(raw),
                "timing_semantics": timing,
                "verdict": "USABLE" if usable else "UNUSABLE",
            }

        games = client.fetch_games(season=season, season_type="regular", division="fbs")
        kept_games = compact(games, GAME_FIELDS)
        season_payload["games"] = kept_games
        record(
            "/games", games, kept_games,
            "scores POSTGAME (target variable only); schedule metadata pregame",
            True,
        )

        returning = client.fetch_returning_production(season=season)
        kept_returning = compact(returning, RETURNING_FIELDS)
        season_payload["returning_production"] = kept_returning
        record(
            "/player/returning", returning, kept_returning,
            f"published pre-season {season}, describing production returning FROM {season - 1}",
            True,
        )

        talent = client.fetch_talent(season=season)
        kept_talent = compact(talent, TALENT_FIELDS)
        season_payload["talent"] = kept_talent
        record(
            "/talent", talent, kept_talent,
            f"recruiting composite entering {season}, settled in the {season - 1} signing cycle",
            True,
        )

        coaches = client.fetch_coaches(season=season)
        kept_coaches = flatten_coaches(coaches, season)
        season_payload["coaches"] = kept_coaches
        record(
            "/coaches", coaches, kept_coaches,
            f"identity/school for {season} only; per-season outcome stats DROPPED as postgame",
            True,
        )

        if args.include_advanced:
            advanced = client.fetch_advanced_team_game_stats(season=season)
            kept_advanced = [
                {
                    "gameId": r.get("gameId") or r.get("game_id"),
                    "season": r.get("season"),
                    "week": r.get("week"),
                    "team": r.get("team"),
                    "opponent": r.get("opponent"),
                    "plays": ((r.get("offense") or {}).get("plays")),
                }
                for r in advanced if isinstance(r, dict)
            ]
            season_payload["advanced"] = kept_advanced
            record(
                "/stats/game/advanced", advanced, kept_advanced,
                "POSTGAME; pace only, usable for strictly-prior games",
                True,
            )

        manifest["endpoints"][str(season)] = endpoint_notes
        path = args.out_dir / f"{season}.json"
        path.write_text(json.dumps(season_payload, separators=(",", ":"), sort_keys=True) + "\n")
        print(
            f"  season {season}: games={len(kept_games)} returning={len(kept_returning)} "
            f"talent={len(kept_talent)} coaches={len(kept_coaches)} -> {path.name} "
            f"({path.stat().st_size // 1024} KiB)"
        )

    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    print(f"\n  wrote manifest with {len(manifest['endpoints'])} season(s)")
    print("  NOTE: the API key is never written to the cache or the manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
