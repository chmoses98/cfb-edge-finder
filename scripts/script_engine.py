#!/usr/bin/env python3
"""CFB Script Engine V1: build, settle and report. Read-only against every source.

    python scripts/script_engine.py build  --football-dir data/football/2026 --catalog-dir data/live \
        --out-dir data/scripting/live --ledger-dir <ledger checkout>/data/scripting/ledger
    python scripts/script_engine.py settle --football-dir data/football/2026 --ledger-dir <...>
    python scripts/script_engine.py report --ledger-dir <...> --season 2026 --out <dir>

BUILD, per catalog game, in this order -- and the order is the product:
  1. identity only from the catalog (game key, title teams, kickoff)
  2. match the physical game in the football schedule; orient home/away
  3. build the football artifact from football data ALONE, freeze it (hash)
  4. only then read the game's contracts and map them to the frozen scripts
  5. label expressions, write the compact SIFT payload, append the ledger

A game that has kicked off keeps its last pregame publication untouched.
SETTLE scores frozen publications against completed games; REPORT builds the
prospective calibration/performance artifact. Neither can alter a frozen
artifact: they read hashes and write separate files.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import collect_live_context as names_util  # noqa: E402  (name matching only; no network used here)

from cfb_edge_finder.execution.disposition import DispositionConfig, parse_timestamp  # noqa: E402
from cfb_edge_finder.execution.semantics import build_game_teams  # noqa: E402
from cfb_edge_finder.execution.slate import build_packet, load_catalog  # noqa: E402
from cfb_edge_finder.scripting import METHODOLOGY_V2_VERSION, METHODOLOGY_VERSION  # noqa: E402
from cfb_edge_finder.scripting.calibration import CalibrationMismatch, load_calibration  # noqa: E402
from cfb_edge_finder.scripting.claims import build_claims, freeze_claims  # noqa: E402
from cfb_edge_finder.scripting.claims_market import map_claims  # noqa: E402
from cfb_edge_finder.scripting.espn import FBS_CONFERENCE_IDS  # noqa: E402
from cfb_edge_finder.scripting.expressions import annotate  # noqa: E402
from cfb_edge_finder.scripting.football import LeagueFitCache, build_content  # noqa: E402
from cfb_edge_finder.scripting.freeze import canonical_bytes, freeze  # noqa: E402
from cfb_edge_finder.scripting.gamelog import TeamGame, iso_utc, parse_utc, read_jsonl  # noqa: E402
from cfb_edge_finder.scripting.ledger import (  # noqa: E402
    LedgerOrderError,
    append,
    append_v2,
    checkpoint_for,
    load_artifact,
    read_rows,
    row_for,
    row_for_v2,
    store_artifact,
    store_claims_artifact,
)
from cfb_edge_finder.scripting.market_map import map_game  # noqa: E402
from cfb_edge_finder.scripting.packets import (  # noqa: E402
    GameRequest,
    build_football_packet,
    identity_check,
)
from cfb_edge_finder.scripting.publish import sift_payload  # noqa: E402
from cfb_edge_finder.scripting.realized import realized_record, settle_expression  # noqa: E402

INDEX_NAME = "index.json"


def _load_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _read_gz(path: Path) -> Any:
    if not path.exists():
        return None
    with gzip.open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def _write_gz_if_changed(path: Path, payload: Any) -> bool:
    """Deterministic gzip (mtime 0) of canonical JSON; untouched when unchanged."""
    raw = canonical_bytes(payload)
    if _read_gz(path) is not None and canonical_bytes(_read_gz(path)) == raw:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(path, "wb", mtime=0) as fh:
        fh.write(raw)
    return True


def _write_if_changed(path: Path, payload: Any) -> bool:
    data = json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    return True


def _fold(text: str) -> str:
    """'San José State' -> 'San Jose State': accents are spelling, not identity."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def load_football(football_dir: Path) -> tuple[tuple[TeamGame, ...], list[dict[str, Any]], dict[str, Any], dict]:
    rows = tuple(read_jsonl(football_dir / "team_games.jsonl"))
    schedule = _load_json(football_dir / "schedule.json", {"events": []})["events"]
    for event in schedule:
        for c in event["competitors"]:
            c["fbs"] = str(c.get("conference_id")) in FBS_CONFERENCE_IDS
            keys: set[str] = set()
            for name in c.get("names") or []:
                keys |= names_util.name_variants(name) | names_util.name_variants(_fold(name))
            c["name_keys"] = sorted(keys)
    availability = _load_json(football_dir / "availability.json", {})
    manifest = _load_json(football_dir / "manifest.json", {})
    return rows, schedule, availability, manifest


def _espn_shape(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event["id"],
        "date": event["date"],
        "competitions": [
            {
                "neutralSite": event["neutral_site"],
                "competitors": [
                    {"homeAway": c["home_away"], "team": {"displayName": n}}
                    for c in event["competitors"]
                    for n in (c.get("names") or [])[:1]
                ]
                + [
                    {"homeAway": c["home_away"], "team": {"location": n}}
                    for c in event["competitors"]
                    for n in (c.get("names") or [])[1:]
                ],
            }
        ],
    }


def match_by_codes(request: GameRequest, schedule: list[dict[str, Any]]) -> tuple[dict | None, str | None]:
    """Fallback: Kalshi's game key spells both team codes (away first); ESPN
    publishes abbreviations. A split of the key that equals an event's two
    abbreviations, within 36 hours of kickoff, is the same physical game.
    Returns (event, Kalshi home code)."""
    suffix = request.game_key[7:].upper()
    if not request.kickoff_utc or len(suffix) < 4:
        return None, None
    kickoff = parse_utc(request.kickoff_utc)
    for event in schedule:
        try:
            if abs((parse_utc(event["date"]) - kickoff).total_seconds()) > 36 * 3600:
                continue
        except (TypeError, ValueError):
            continue
        abbrs = {str(c.get("abbreviation") or "").upper() for c in event["competitors"]}
        for i in range(2, len(suffix) - 1):
            away, home = suffix[:i], suffix[i:]
            if {away, home} == abbrs and away != home:
                return event, home
    return None, None


def match_event(request: GameRequest, schedule: list[dict[str, Any]]) -> tuple[dict | None, set[str], set[str]]:
    keys = names_util._kalshi_team_keys({"home": request.kalshi_home, "away": request.kalshi_away})
    home_keys = keys["home"] | (names_util.name_variants(_fold(request.kalshi_home or "")))
    away_keys = keys["away"] | (names_util.name_variants(_fold(request.kalshi_away or "")))
    if not home_keys or not away_keys or not request.kickoff_utc:
        return None, home_keys, away_keys
    kickoff = parse_utc(request.kickoff_utc)
    best = None
    for event in schedule:
        try:
            gap = abs((parse_utc(event["date"]) - kickoff).total_seconds())
        except (TypeError, ValueError):
            continue
        if gap > 36 * 3600:
            continue
        names = set()
        for c in event["competitors"]:
            names |= set(c["name_keys"])
        if home_keys & names and away_keys & names:
            if best is None or gap < best[0]:
                best = (gap, event)
    return (best[1] if best else None), home_keys, away_keys


def cmd_build(args: argparse.Namespace) -> int:
    now = iso_utc(datetime.now(UTC)) if not args.as_of else iso_utc(parse_utc(args.as_of))
    rows, schedule, availability, manifest = load_football(args.football_dir)
    index, details = load_catalog(args.catalog_dir)
    captured_raw = (index.get("capture") or {}).get("captured_at")
    captured = parse_timestamp(captured_raw)
    frozen_dir = args.out_dir / "frozen"
    frozen_v2_dir = args.out_dir / "frozen_v2"
    sift_dir = args.out_dir / "sift"
    cache = LeagueFitCache()
    summary: dict[str, Any] = {"games": {}, "counts": {}}
    wanted = set(args.game or [])
    ledger_written = 0
    ledger_v2_written = 0
    # V2 claims are published in SHADOW beside V1 (docs/SCRIPT_ENGINE_V2_MIGRATION.md).
    # Nothing V2 does can stop or alter the V1 publication: a calibration that
    # fails verification disables V2 for the run, and a per-game V2 error is
    # recorded and that game publishes V1 alone.
    try:
        calibration: dict[str, Any] | None = load_calibration(ROOT)
        v2_status = {"status": "SHADOW", "methodology_version": METHODOLOGY_V2_VERSION}
    except (CalibrationMismatch, OSError, ValueError) as exc:
        calibration = None
        v2_status = {"status": "DISABLED", "reason": f"control calibration failed verification: {exc}"}

    for entry in sorted(index.get("games") or [], key=lambda g: (str(g.get("kickoff")), str(g.get("game_key")))):
        game_key = str(entry.get("game_key"))
        if wanted and game_key not in wanted:
            continue
        detail = details.get(game_key)
        if detail is None:
            continue
        path = frozen_dir / f"{game_key}.json.gz"
        previous = _read_gz(path)
        kickoff = entry.get("kickoff")
        if kickoff and parse_utc(kickoff) <= parse_utc(now) and previous is not None:
            summary["games"][game_key] = {"status": "KICKED_OFF_FROZEN"}
            continue

        # 1-2. identity only, then the physical game in the football schedule
        teams = build_game_teams(entry.get("title"), [])
        request = GameRequest(game_key, entry.get("title"), teams.home_name, teams.away_name, kickoff)
        event, home_keys, away_keys = match_event(request, schedule)
        check = identity_check(request, event, home_keys, away_keys)
        if check["status"] == "FAIL":
            coded, kalshi_home_code = match_by_codes(request, schedule)
            if coded is not None:
                espn_home = next(c for c in coded["competitors"] if c["home_away"] == "home")
                swapped = str(espn_home.get("abbreviation") or "").upper() != kalshi_home_code
                event = coded
                check = {
                    "status": "RESOLVED",
                    "orientation_swapped": swapped,
                    "espn_event_id": coded["id"],
                    "method": "team_codes",
                    "reason": (
                        "matched by the team codes in the Kalshi game key against ESPN abbreviations "
                        "(names are spelled differently)"
                        + ("; home/away order differs and is translated by team identity" if swapped else "")
                    ),
                }
        if check["status"] == "FAIL":
            record = {"status": "UNAVAILABLE", "reason": f"identity: {check['reason']}"}
            _write_gz_if_changed(sift_dir / f"{game_key}.json.gz", sift_payload(record))
            summary["games"][game_key] = {"status": "IDENTITY_FAIL", "reason": check["reason"]}
            continue

        # 3. the football artifact, frozen before any contract is read
        packet = build_football_packet(request, event, check, schedule, rows, availability, args.season)
        content = build_content(packet, cache)
        envelope = freeze(content, generated_at=now, previous_envelope=previous)

        # 3b. V2 claims (shadow), read from the frozen V1 artifact, frozen before any contract is read.
        # Pregame only: a game that has kicked off gets no V2 artifact, so nothing is backfilled.
        claims_env = None
        v2_game: dict[str, Any] = {"status": "NOT_BUILT", "reason": v2_status.get("reason")}
        if calibration is not None and kickoff and parse_utc(kickoff) > parse_utc(now):
            claims_path = frozen_v2_dir / f"{game_key}.json.gz"
            try:
                claims_env = freeze_claims(
                    build_claims(envelope, calibration), generated_at=now, previous_envelope=_read_gz(claims_path)
                )
            except Exception as exc:  # noqa: BLE001 -- shadow must never break the V1 publication
                v2_game = {"status": "ERROR", "reason": f"{type(exc).__name__}: {exc}"}
            else:
                _write_gz_if_changed(claims_path, claims_env)
        elif calibration is not None:
            v2_game = {"status": "NOT_BUILT", "reason": "kickoff unknown or passed: V2 is pregame only"}

        # 4. only now: the contracts, mapped to the frozen scripts
        config = DispositionConfig(as_of=captured or parse_utc(now), captured_at=captured)
        market_packet = build_packet(entry, detail, config, set(), "America/New_York")
        market_map = map_game(
            envelope,
            market_packet["contracts"],
            orientation_swapped=bool(check.get("orientation_swapped")),
            mapped_at=now,
            excluded_contracts=market_packet["excluded_contracts"],
        )
        annotate(envelope["content"], market_map)
        claims_market = None
        if claims_env is not None:
            try:
                claims_market = map_claims(claims_env, market_map, mapped_at=now)
            except Exception as exc:  # noqa: BLE001 -- shadow must never break the V1 publication
                claims_env = None
                v2_game = {"status": "ERROR", "reason": f"{type(exc).__name__}: {exc}"}
        if claims_env is not None:
            control = claims_env["content"]["claims"]["control"]
            v2_game = {
                "status": claims_env["content"]["status"],
                "claims_artifact_hash": claims_env["artifact_hash"],
                "control": None if control is None else control["tier"],
            }

        record = {
            "game_key": game_key,
            "football": envelope,
            "market_map": market_map,
            "claims": claims_env,
            "claims_market": claims_market,
            "prices_captured_at": captured_raw,
        }
        _write_gz_if_changed(path, envelope)
        _write_gz_if_changed(sift_dir / f"{game_key}.json.gz", sift_payload(record))

        # 5. prospective ledger (append-only, pregame only)
        if args.ledger_dir is not None and kickoff:
            checkpoint = checkpoint_for(content["kickoff_utc"], now)
            if checkpoint is not None:
                store_artifact(args.ledger_dir, args.season, envelope)
                try:
                    row = row_for(
                        game_key=game_key,
                        season=args.season,
                        envelope=envelope,
                        market_map=market_map,
                        checkpoint=checkpoint,
                        recorded_at=now,
                        prices_captured_at=captured_raw,
                    )
                    ledger_written += append(args.ledger_dir, args.season, row)
                    if checkpoint == "FINAL_PREGAME":
                        first = dict(row, kind="PUBLICATION")
                        ledger_written += append(args.ledger_dir, args.season, first)
                except LedgerOrderError:
                    pass
                if claims_env is not None:
                    store_claims_artifact(args.ledger_dir, args.season, claims_env)
                    try:
                        row_v2 = row_for_v2(
                            game_key=game_key,
                            season=args.season,
                            claims_envelope=claims_env,
                            authority_map=claims_market,
                            market_map=market_map,
                            checkpoint=checkpoint,
                            recorded_at=now,
                            prices_captured_at=captured_raw,
                        )
                        ledger_v2_written += append_v2(args.ledger_dir, args.season, row_v2)
                        if checkpoint == "FINAL_PREGAME":
                            ledger_v2_written += append_v2(
                                args.ledger_dir, args.season, dict(row_v2, kind="PUBLICATION")
                            )
                    except LedgerOrderError:
                        pass

        summary["games"][game_key] = {
            "status": content["game_scripts"]["status"],
            "data_confidence": content["data_confidence"],
            "artifact_hash": envelope["artifact_hash"],
            "scripts": [f"{s['role']}:{s['archetype']}" for s in content["game_scripts"]["scripts"]],
            "identity": check["status"],
            "freshness": packet.freshness["status"],
            "survivors": len(market_map.get("survivors") or []),
            "expressions_mapped": market_map["coverage"]["expressions_mapped"],
            "v2": v2_game,
        }

    counts: dict[str, int] = {}
    for info in summary["games"].values():
        counts[info["status"]] = counts.get(info["status"], 0) + 1
    summary["counts"] = dict(sorted(counts.items()))
    summary["methodology_version"] = METHODOLOGY_VERSION
    summary["football_manifest"] = {k: manifest.get(k) for k in ("fetched_at", "games_in_log", "play_log_coverage")}
    summary["catalog_captured_at"] = captured_raw
    summary["ledger_rows_written"] = ledger_written
    summary["v2"] = {**v2_status, "ledger_rows_written": ledger_v2_written}
    _write_if_changed(args.out_dir / INDEX_NAME, {k: v for k, v in summary.items()})
    print(
        f"script engine: {summary['counts']} ; ledger rows written {ledger_written} ; "
        f"V2 {v2_status['status']} rows written {ledger_v2_written}"
    )
    return 0


def cmd_settle(args: argparse.Namespace) -> int:
    rows, _, _, _ = load_football(args.football_dir)
    by_game: dict[str, dict[str, TeamGame]] = {}
    for r in rows:
        by_game.setdefault(r.game_id, {})[r.site if r.site != "neutral" else r.team_id] = r
    ledger_rows = read_rows(args.ledger_dir, args.season)
    out_path = args.ledger_dir / str(args.season) / "realized.jsonl"
    done = (
        {json.loads(line)["artifact_hash"] + json.loads(line)["kind"] for line in out_path.read_text().splitlines()}
        if out_path.exists()
        else set()
    )
    written = 0
    for row in ledger_rows:
        key = row["artifact_hash"] + row["kind"]
        if key in done:
            continue
        pair = by_game.get(row["event_id"])
        if not pair:
            continue
        home_id, away_id = row["teams"]["home"]["team_id"], row["teams"]["away"]["team_id"]
        all_rows = [r for r in rows if r.game_id == row["event_id"]]
        home = next((r for r in all_rows if r.team_id == home_id), None)
        away = next((r for r in all_rows if r.team_id == away_id), None)
        if home is None or away is None:
            continue
        artifact = load_artifact(args.ledger_dir, args.season, row["artifact_hash"])
        league_plays = None
        if artifact is not None:
            league_plays = (
                artifact["content"]["matchup_profile"]["adjustment"]["league_baselines"].get("plays_per_game") or {}
            ).get("mu")
        record = realized_record(home, away, row, league_plays)
        record["kind"] = row["kind"]
        record["expressions"] = [
            {
                "id": e["id"],
                "labels": e["labels"],
                "compat": e["compat"],
                "entry": e["entry"],
                "cost_per_contract": e["cost_per_contract"],
                "won": settle_expression(e.get("wins_when"), record["features"]),
            }
            for e in row["expressions"]
        ]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        written += 1
    print(f"settled {written} publication(s)")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from cfb_edge_finder.scripting.report import build_report, render_markdown

    path = args.ledger_dir / str(args.season) / "realized.jsonl"
    realized = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    report = build_report(read_rows(args.ledger_dir, args.season), realized)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "script_engine_report.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    (args.out / "script_engine_report.md").write_text(render_markdown(report))
    print(f"report: {report['publications']['games']} game(s), {report['settled']['games']} settled")
    return 0


def cmd_settle_v2(args: argparse.Namespace) -> int:
    """Settle V2 FINAL_PREGAME rows (frozen claims, never regenerated) against the game log. Append-only."""
    from cfb_edge_finder.scripting.ledger import load_artifact, read_rows_v2
    from cfb_edge_finder.scripting.realized_v2 import key_of, settle_rows

    rows, _, _, _ = load_football(args.football_dir)
    out_path = args.ledger_dir / str(args.season) / "realized_v2.jsonl"
    existing = out_path.read_text().splitlines() if out_path.exists() else []
    done = {key_of(json.loads(line)) for line in existing if line.strip()}

    def load_claims(digest: str) -> dict | None:
        path = args.ledger_dir / str(args.season) / "artifacts_v2" / f"{digest}.json.gz"
        return _read_gz(path) if path.exists() else None

    def load_v1(digest: str | None) -> dict | None:
        return load_artifact(args.ledger_dir, args.season, digest) if digest else None

    new = settle_rows(read_rows_v2(args.ledger_dir, args.season), done, rows, load_claims, load_v1)
    if new:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a", encoding="utf-8") as fh:
            for record in new:
                fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"V2: settled {len(new)} FINAL_PREGAME row(s)")
    return 0


def cmd_report_v2(args: argparse.Namespace) -> int:
    from cfb_edge_finder.scripting.ledger import read_rows_v2
    from cfb_edge_finder.scripting.realized_v2 import build_report_v2, render_markdown_v2

    path = args.ledger_dir / str(args.season) / "realized_v2.jsonl"
    realized = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    report = build_report_v2(read_rows_v2(args.ledger_dir, args.season), realized)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "script_engine_v2_report.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    (args.out / "script_engine_v2_report.md").write_text(render_markdown_v2(report))
    print(f"V2 report: {report['final_pregame_games']} FINAL_PREGAME game(s), {report['settled_games']} settled")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--football-dir", type=Path, required=True)
    b.add_argument("--catalog-dir", type=Path, required=True)
    b.add_argument("--out-dir", type=Path, required=True)
    b.add_argument("--ledger-dir", type=Path, default=None)
    b.add_argument("--season", type=int, default=2026)
    b.add_argument("--as-of", type=str, default=None)
    b.add_argument("--game", action="append")
    s = sub.add_parser("settle")
    s.add_argument("--football-dir", type=Path, required=True)
    s.add_argument("--ledger-dir", type=Path, required=True)
    s.add_argument("--season", type=int, default=2026)
    r = sub.add_parser("report")
    r.add_argument("--ledger-dir", type=Path, required=True)
    r.add_argument("--season", type=int, default=2026)
    r.add_argument("--out", type=Path, required=True)
    s2 = sub.add_parser("settle-v2")
    s2.add_argument("--football-dir", type=Path, required=True)
    s2.add_argument("--ledger-dir", type=Path, required=True)
    s2.add_argument("--season", type=int, default=2026)
    r2 = sub.add_parser("report-v2")
    r2.add_argument("--ledger-dir", type=Path, required=True)
    r2.add_argument("--season", type=int, default=2026)
    r2.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    commands = {
        "build": cmd_build,
        "settle": cmd_settle,
        "report": cmd_report,
        "settle-v2": cmd_settle_v2,
        "report-v2": cmd_report_v2,
    }
    return commands[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
