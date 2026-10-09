#!/usr/bin/env python3
"""Football Signal Discovery Lab, Wave 2A (CFB): retrospective replay of the frozen Wave-2 streams. RESEARCH ONLY.

docs/research/FOOTBALL_SIGNAL_DISCOVERY_WAVE2A_PROTOCOL.md. Stages, run in order:

    python scripts/signal_lab_wave2a_cfb.py extract   # captured spread ladders + winner quotes (no outcome)
    python scripts/signal_lab_wave2a_cfb.py replay    # membership (outcome-blind) -> reveal -> report
    python scripts/signal_lab_wave2a_cfb.py history   # 2014-2025 characterisation (Wave-1 corpus)

Every input is read from git at a pinned commit, so a rerun is byte-identical whatever production commits later.
Output goes to data/scripting/validation/signal_discovery_wave2a/ only; the prospective Wave-2 store
(research-signals:wave2/...) is never touched.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import cfb_research_conductor as conductor  # noqa: E402

from cfb_edge_finder.control_market.football import (  # noqa: E402
    outcome_for,
    replay_game,
    replay_targets,
    schedule_meta,
)
from cfb_edge_finder.control_market.quotes import (  # noqa: E402
    Quote,
    orient_markets,
    parse_utc,
    quotes_from_catalog_game,
)
from cfb_edge_finder.scripting.calibration import load_calibration  # noqa: E402
from cfb_edge_finder.scripting.football import LeagueFitCache  # noqa: E402
from cfb_edge_finder.scripting.gamelog import TeamGame, row_order  # noqa: E402
from cfb_edge_finder.signal_discovery import wave2 as W  # noqa: E402
from cfb_edge_finder.signal_discovery import wave2_cycle as C  # noqa: E402
from cfb_edge_finder.signal_discovery.wave2a import history as H  # noqa: E402
from cfb_edge_finder.signal_discovery.wave2a import replay as R  # noqa: E402

OUT = ROOT / "data" / "scripting" / "validation" / "signal_discovery_wave2a"
CAPTURES = OUT / "spread_captures_2026.jsonl.gz"
WINNER_SUPPLEMENT = OUT / "winner_quotes_supplement_2026.jsonl.gz"
EXTRACT_MANIFEST = OUT / "extract_manifest.json"
MEMBERSHIP = OUT / "replay_membership_2026.jsonl.gz"
ROWS = OUT / "replay_rows_2026.jsonl.gz"
REPORT = OUT / "replay_report_2026.json"
HISTORY = OUT / "history_report.json"

#: pinned inputs (protocol §3)
MAIN_SHA = "8173fd5ea2b38a61ad429570fbb5cbcaa22cf7b7"
RESEARCH_DATA_SHA = "292f3aacafe4b892d56ed7c1446225ce85afbc81"
SCRIPT_LEDGER_SHA = "8be862ef848ae15260f04a0f0314c33351d10ea8"
FOOTBALL_LOG = "data/football/2026/team_games.jsonl"
SCHEDULE = "data/football/2026/schedule.json"
FOOTBALL_SHA = {
    FOOTBALL_LOG: "e37fe5ca7aacaf4adab3398912fc0ce11d2f60ec873529210b04c0ca32ed8d4b",
    SCHEDULE: "6b73c7a1fe46f713cea1aa90a06626a1f80be6d2f87c81435643198fc09df606",
}
CONTROL_QUOTES = ROOT / "data" / "scripting" / "validation" / "control_market_2026" / "quotes_2026.jsonl.gz"
CONTROL_ROWS = ROOT / "data" / "scripting" / "validation" / "control_market_2026" / "control_market_rows.jsonl.gz"
CONTROL_QUOTES_LAST = "2026-10-07T23:19:34.451947Z"  # last catalog snapshot inside quotes_2026 (its manifest)
WAVE1 = ROOT / "data" / "scripting" / "validation" / "signal_discovery_wave1"
#: a capture this many hours before (or after) the source's own kickoff hint is kept by `extract`; the replay then
#: applies the exact ESPN-kickoff window
KEEP_HOURS_BEFORE = 6.0
KEEP_HOURS_AFTER = 2.0


# --------------------------------------------------------------------------- git / io


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def git_bytes(ref_path: str) -> bytes:
    return subprocess.run(["git", "show", ref_path], cwd=ROOT, check=True, capture_output=True).stdout


class BlobReader:
    """`git cat-file --batch` reader."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            ["git", "cat-file", "--batch"], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )

    def read(self, sha: str) -> bytes:
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(sha.encode() + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().split()
        data = self.proc.stdout.read(int(header[2]))
        self.proc.stdout.read(1)
        return data

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait()


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=float)


def write_gz_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    R.assert_not_prospective_store(str(path.relative_to(ROOT)))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(canonical(r) + "\n" for r in rows).encode("utf-8")
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as fh:
        fh.write(payload)
    return hashlib.sha256(payload).hexdigest()


def read_gz_jsonl(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_json(path: Path, payload: Any) -> None:
    R.assert_not_prospective_store(str(path.relative_to(ROOT)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False, default=float) + "\n")


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- stage 1: extract (quotes only)


def _keep(captured_at: str, kickoff_hint: str | None) -> bool:
    if not kickoff_hint:
        return False
    k, t = parse_utc(kickoff_hint), parse_utc(captured_at)
    return k - timedelta(hours=KEEP_HOURS_BEFORE) <= t <= k + timedelta(hours=KEEP_HOURS_AFTER)


def extract_research() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    paths = [
        p
        for p in git("ls-tree", "-r", "--name-only", RESEARCH_DATA_SHA, "data/research/observations/2026").split()
        if p.endswith(".jsonl")
    ]
    out = []
    for p in sorted(paths):
        for line in git_bytes(f"{RESEARCH_DATA_SHA}:{p}").decode().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            obs = row.get("observation") or {}
            if row.get("capture_mode") != "PROSPECTIVE":
                continue
            if not str(obs.get("kalshi_event_ticker") or "").startswith(f"{W.SPREAD_SERIES}-"):
                continue
            if not _keep(obs["captured_at"], row.get("kickoff_utc_at_capture")):
                continue
            out.append(
                {
                    "source": R.SOURCE_RESEARCH,
                    "run_id": row["run_id"],
                    "captured_at": obs["captured_at"],
                    "kickoff_hint": row.get("kickoff_utc_at_capture"),
                    "kalshi_event_ticker": obs["kalshi_event_ticker"],
                    "kalshi_market_ticker": obs["kalshi_market_ticker"],
                    "threshold": obs.get("threshold"),
                    "executable_yes_price": obs.get("executable_yes_price"),
                    "executable_no_price": obs.get("executable_no_price"),
                    "market_status": obs.get("market_status"),
                    "observation_key": row.get("observation_key"),
                }
            )
    out.sort(key=lambda r: (r["kalshi_event_ticker"], r["captured_at"], r["kalshi_market_ticker"]))
    return out, {"commit": RESEARCH_DATA_SHA, "files": len(paths), "rows": len(out)}


SPREAD_FIELDS = (
    "market_ticker",
    "event_ticker",
    "floor_strike",
    "strike_type",
    "status",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "yes_sub_title",
)


MONTHS = {
    m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)
}


def _near_day(filename: str, day) -> bool:
    """Game files are named by the Kalshi game key (`26OCT10JKSTGRAM.json`); keep games dated within a day."""
    try:
        from datetime import date

        d = date(2000 + int(filename[:2]), MONTHS[filename[2:5]], int(filename[5:7]))
    except (KeyError, ValueError):
        return True
    return abs((d - day).days) <= 1


def _compact(game: dict[str, Any], name: str) -> dict[str, Any]:
    spread = [
        {k: m.get(k) for k in SPREAD_FIELDS} for m in game.get("markets") or [] if m.get("family") == "game_spread"
    ]
    return {
        "game_key": game.get("game_key") or name[:-5],
        "kickoff_hint": game.get("kickoff"),
        "title": game.get("title"),
        "spread_markets": sorted(spread, key=lambda m: m["market_ticker"]),
        "winner_quotes": [q.to_dict() for q in quotes_from_catalog_game(game, "", "")],
    }


def extract_catalog() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    log = git("log", "--format=%H %s", MAIN_SHA, "--", "data/live/cfb_catalog_status.json").splitlines()
    commits = [line.split(" ", 1)[0] for line in log if line.split(" ", 1)[1].startswith("kalshi cfb catalog")]
    reader = BlobReader()
    snaps, winners, used = [], [], []
    identity = Counter()
    parsed: dict[str, dict[str, Any]] = {}
    try:
        for commit in reversed(commits):
            status = json.loads(git_bytes(f"{commit}:data/live/cfb_catalog_status.json"))
            if not status.get("capture_complete"):
                continue
            captured_at, elapsed = status["captured_at"], float(status.get("elapsed_seconds") or 0.0)
            used.append({"commit": commit, "captured_at": captured_at, "elapsed_seconds": elapsed})
            day = parse_utc(captured_at).date()
            for entry in git("ls-tree", f"{commit}:data/live/games").splitlines():
                meta, name = entry.split("\t", 1)
                if not _near_day(name, day):
                    continue
                blob = meta.split()[2]
                if blob not in parsed:
                    parsed[blob] = _compact(json.loads(reader.read(blob)), name)
                game = parsed[blob]
                if not _keep(captured_at, game["kickoff_hint"]):
                    continue
                identity.update(R.book_identity_holds(game["spread_markets"]))
                snaps.append(
                    {
                        "source": R.SOURCE_CATALOG,
                        "commit": commit,
                        "captured_at": captured_at,
                        "elapsed_seconds": elapsed,
                        "game_key": game["game_key"],
                        "kickoff_hint": game["kickoff_hint"],
                        "title": game["title"],
                        "spread_markets": game["spread_markets"],
                    }
                )
                if captured_at > CONTROL_QUOTES_LAST:
                    winners += [{**q, "captured_at": captured_at, "provenance": commit} for q in game["winner_quotes"]]
    finally:
        reader.close()
    snaps.sort(key=lambda s: (s["game_key"], s["captured_at"]))
    winners.sort(key=lambda q: (q["market_ticker"], q["captured_at"]))
    meta = {
        "main": MAIN_SHA,
        "snapshots": len(used),
        "first": used[0] if used else None,
        "last": used[-1] if used else None,
        "game_snapshots_kept": len(snaps),
        "book_identity_check_on_kept_spread_markets": dict(identity),
    }
    return snaps, winners, meta


def cmd_extract(_: argparse.Namespace) -> int:
    W.load_candidates(ROOT)
    research, rmeta = extract_research()
    snaps, winners, cmeta = extract_catalog()
    if cmeta["book_identity_check_on_kept_spread_markets"].get("violated"):
        raise SystemExit("book identity violated on a catalog spread market: the research-corpus bid rule is unsafe")
    d1 = write_gz_jsonl(CAPTURES, research + snaps)
    d2 = write_gz_jsonl(WINNER_SUPPLEMENT, winners)
    write_json(
        EXTRACT_MANIFEST,
        {
            "schema": "cfb_signal_lab_wave2a_extract/1.0.0",
            "version": R.VERSION,
            "candidates_sha256": W.CANDIDATES_SHA256,
            "not_an_outcome_input": "quotes only: no score, settlement or result is read by this stage",
            "spread_captures": {"path": str(CAPTURES.relative_to(ROOT)), "content_sha256": d1},
            "winner_supplement": {"path": str(WINNER_SUPPLEMENT.relative_to(ROOT)), "content_sha256": d2},
            "research_corpus": rmeta,
            "catalog": cmeta,
            "keep_rule_hours": {"before_kickoff_hint": KEEP_HOURS_BEFORE, "after_kickoff_hint": KEEP_HOURS_AFTER},
        },
    )
    print(f"research rows {len(research)}, catalog game snapshots {len(snaps)}, winner supplement {len(winners)}")
    print(json.dumps(cmeta["book_identity_check_on_kept_spread_markets"]))
    return 0


def load_extract() -> tuple[list[dict[str, Any]], list[Quote]]:
    manifest = json.loads(EXTRACT_MANIFEST.read_text())
    for key, path in (("spread_captures", CAPTURES), ("winner_supplement", WINNER_SUPPLEMENT)):
        with gzip.open(path, "rb") as fh:
            if hashlib.sha256(fh.read()).hexdigest() != manifest[key]["content_sha256"]:
                raise SystemExit(f"{path.name} does not match its manifest hash")
    caps = read_gz_jsonl(CAPTURES)
    winners = [Quote(**q) for q in read_gz_jsonl(CONTROL_QUOTES)] + [
        Quote(**q) for q in read_gz_jsonl(WINNER_SUPPLEMENT)
    ]
    return caps, winners


# --------------------------------------------------------------------------- football inputs


def load_football() -> tuple[list[TeamGame], list[dict[str, Any]]]:
    blobs = {p: git_bytes(f"{MAIN_SHA}:{p}") for p in FOOTBALL_SHA}
    for p, data in blobs.items():
        digest = hashlib.sha256(data).hexdigest()
        if digest != FOOTBALL_SHA[p]:
            raise SystemExit(f"pinned input {p} hash {digest} != {FOOTBALL_SHA[p]}")
    rows = sorted(
        (TeamGame.from_dict(json.loads(x)) for x in blobs[FOOTBALL_LOG].decode().splitlines() if x.strip()),
        key=row_order,
    )
    return rows, json.loads(blobs[SCHEDULE])["events"]


def v2_final_pregame() -> dict[str, dict[str, Any]]:
    """Production V2 FINAL_PREGAME rows (first per game), by ESPN event id."""
    text = git_bytes(f"{SCRIPT_LEDGER_SHA}:data/scripting/ledger/2026/publications_v2.jsonl").decode()
    out: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("kind") != "FINAL_PREGAME" or r.get("methodology_version") != "cfb-script-engine/2.0.0":
            continue
        eid = str(r.get("event_id"))
        if eid not in out or r["recorded_at"] < out[eid]["recorded_at"]:
            out[eid] = r
    return out


def code_sha() -> str | None:
    try:
        return git("rev-parse", "HEAD").strip()
    except subprocess.CalledProcessError:
        return None


# --------------------------------------------------------------------------- stage 2: membership (outcome-blind)


def attempts_for(
    caps: list[dict[str, Any]], game_keys: list[str], kickoff: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    attempts, quotes = [], []
    for key in game_keys:
        research = [
            c
            for c in caps
            if c["source"] == R.SOURCE_RESEARCH and c["kalshi_event_ticker"] == f"{W.SPREAD_SERIES}-{key}"
        ]
        a, q = R.research_attempts(research, game_key=key, kickoff=kickoff)
        attempts += a
        quotes += q
        for s in caps:
            if s["source"] != R.SOURCE_CATALOG or s["game_key"] != key:
                continue
            a1, q1 = R.catalog_attempt(
                s["spread_markets"],
                game_key=key,
                kickoff=kickoff,
                captured_at=s["captured_at"],
                elapsed_seconds=s["elapsed_seconds"],
                commit=s["commit"],
            )
            if a1 is not None:
                attempts.append(a1)
                quotes += q1
    return attempts, quotes


def membership(
    rows: list[TeamGame],
    events: list[dict[str, Any]],
    caps: list[dict[str, Any]],
    winners: list[Quote],
    v2: dict[str, dict[str, Any]],
    sha: str | None,
    only_game_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Every replay row up to and including the entry. No target game's score is read."""
    # Scores of the TARGET game never reach membership: the builders see only rows that finished before the
    # production cutoff (`history_for` raises LeakageError otherwise); prior games' results are pregame facts.
    blind = list(rows)
    schedule = conductor.with_name_keys(json.loads(json.dumps(events)))
    for e in schedule:
        for c in e.get("competitors") or []:
            c.pop("score", None)
    meta = schedule_meta(schedule)
    targets, skipped = replay_targets(blind, meta, kickoff_before=W.ACTIVATION_UTC)
    orientation = orient_markets(winners, schedule, conductor.market_keys)
    calibration = load_calibration(ROOT)
    cache = LeagueFitCache()
    first_capture = min(c["captured_at"] for c in caps)
    conf = {
        str(e["id"]): {str(c["team_id"]): c.get("conference_id") for c in e.get("competitors") or []} for e in schedule
    }
    obs_rows, entries, controls = [], [], []
    for t in targets:
        if only_game_ids is not None and t.game_id not in only_game_ids:
            continue
        ident = C.identity({"content": {"event_id": t.game_id}}, schedule)
        game_keys = R.kalshi_keys_for(orientation, t.game_id)
        game = {
            "game_key": game_keys[0] if game_keys else f"ESPN{t.game_id}",
            "kickoff_utc": t.kickoff_utc,
            "game_id": t.game_id,
            "teams": ident["teams"] if ident else None,
        }
        attempts, quotes = attempts_for(caps, game_keys, t.kickoff_utc)
        codes = W.team_codes(orientation, t.game_id)
        # PROS-001
        if ident is None:
            o1 = W.observation_001(game=game, feature=None, reason="NO_ESPN_IDENTITY", now=t.kickoff_utc, code_sha=sha)
        else:
            feat = C.feature_for(ident, t.kickoff_utc, 2026, tuple(blind), schedule, cache)
            o1 = W.observation_001(game=game, feature=feat, reason=None, now=t.kickoff_utc, code_sha=sha)
        # PROS-002: production FINAL_PREGAME row if one exists, else the replay at the production cutoff
        fp = v2.get(t.game_id)
        rec = replay_game(blind, t, cache, calibration)
        rc = rec.get("control") or {}
        if fp is not None:
            control = (fp.get("claims") or {}).get("control") or {}
            ledger_row = fp
            provenance = "PROSPECTIVE_V2_LEDGER_FINAL_PREGAME"
        else:
            control = {"tier": rc.get("tier"), "side": rc.get("side"), "strength": rc.get("strength")}
            ledger_row = {
                "claims": {"control": control},
                "recorded_at": None,
                "claims_artifact_hash": rec["football_record_hash"],
                "methodology_version": rec["methodology_version"],
            }
            provenance = rec["football_provenance"]
        controls.append(
            {
                "game_id": t.game_id,
                "kickoff_utc": t.kickoff_utc,
                "week": t.week,
                "replay_tier": rc.get("tier"),
                "ledger_tier": (((fp or {}).get("claims") or {}).get("control") or {}).get("tier") if fp else None,
                "used": control.get("tier"),
                "provenance": provenance,
            }
        )
        rows_g = [R.observation_row(o1, week=t.week, first_capture=first_capture)]
        if control.get("tier") in W.STRONG_TIERS and ident is not None:
            o2 = W.observation_002(game=game, ledger_row=ledger_row, now=t.kickoff_utc, code_sha=sha)
            o2["control_provenance"] = provenance
            rows_g.append(R.observation_row(o2, week=t.week, first_capture=first_capture))
        for obs in rows_g:
            obs["side_conference"] = conf.get(t.game_id, {}).get((obs.get("side_team") or {}).get("team_id"))
            obs_rows.append(obs)
            if obs["status"] != W.PENDING:
                continue
            ent = R.replay_entry(
                obs, attempts=attempts, quotes=quotes, codes=codes, first_capture=first_capture, code_sha=sha
            )
            ent["week"] = t.week
            ent["side_conference"] = obs["side_conference"]
            ent["kalshi_game_keys"] = game_keys
            if ent["status"] == W.ELIGIBLE:
                R.assert_fee_known(ent["contract"])
            entries.append(ent)
    return {
        "targets": len(targets),
        "skipped": skipped,
        "observations": obs_rows,
        "entries": entries,
        "controls": controls,
        "first_capture": first_capture,
        "orientation_counts": dict(Counter(o["status"] for o in orientation.values())),
        "orientation": orientation,
    }


def membership_digest(m: dict[str, Any]) -> str:
    """Hash of the outcome-blind decisions (status, reason, side, z, centre, rung, ask, fee) of every row."""
    keep = ("row_id", "status", "reason", "side", "replay_exclusion", "market_implied_margin", "ticker", "ask", "fee")
    rows = [
        {k: r.get(k) for k in keep} | {"z": (r.get("signal") or {}).get("z")} for r in m["observations"] + m["entries"]
    ]
    return hashlib.sha256(canonical(sorted(rows, key=lambda r: r["row_id"])).encode()).hexdigest()


# --------------------------------------------------------------------------- stage 3/4: reveal and report


def denominators(m: dict[str, Any], settled: list[dict[str, Any]], stream: str) -> dict[str, Any]:
    obs = [o for o in m["observations"] if o["signal_id"] == stream]
    ent = [e for e in m["entries"] if e["signal_id"] == stream]
    st = [s for s in settled if s["signal_id"] == stream]
    priced = [e for e in ent if e.get("market_implied_margin") is not None]
    return {
        "candidate_games": len(obs),
        "feature_or_control_available": sum(1 for o in obs if o["replay_exclusion"] != R.FEATURE_HISTORY_UNAVAILABLE),
        "rule_selected": len(ent),
        "kalshi_event_matched": sum(1 for e in ent if e.get("kalshi_game_keys")),
        "window_capture_reached": sum(1 for e in ent if e.get("window_attempts_by_source")),
        "oriented": sum(1 for e in ent if (e.get("health") or {}).get("orientation") == "RESOLVED"),
        "priced_center": len(priced),
        "eligible_entries": sum(1 for e in ent if e["status"] == W.ELIGIBLE),
        "executable_contract": sum(1 for e in ent if (e.get("contract") or {}).get("status") == W.ELIGIBLE),
        "settled": sum(1 for s in st if s["status"] == W.SETTLED),
        "observation_exclusions": dict(Counter(str(o["replay_exclusion"]) for o in obs)),
        "entry_exclusions": dict(Counter(str(e["replay_exclusion"]) for e in ent)),
    }


def cmd_replay(_: argparse.Namespace) -> int:
    W.load_candidates(ROOT)
    caps, winners = load_extract()
    rows, events = load_football()
    v2 = v2_final_pregame()
    sha = code_sha()
    m = membership(rows, events, caps, winners, v2, sha)
    digest = membership_digest(m)
    write_gz_jsonl(MEMBERSHIP, m["observations"] + m["entries"])
    # ---- reveal (first and only read of scores)
    settled = []
    for e in m["entries"]:
        if e["status"] != W.ELIGIBLE:
            continue
        attempts, quotes = attempts_for(caps, e["kalshi_game_keys"], e["kickoff_utc"])
        codes = W.team_codes(m["orientation"], e["game_id"])
        closing = (
            W.closing_context(
                attempts=attempts,
                quotes=quotes,
                codes=codes["codes"],
                team_id=e["side_team"]["team_id"],
                kickoff=e["kickoff_utc"],
            )
            if codes["status"] == "RESOLVED"
            else None
        )
        s = R.reveal(e, outcome_for(rows, e["game_id"], e["side_team"]["team_id"]), closing, code_sha=sha)
        s["week"] = e["week"]
        s["side_team_id"] = e["side_team"]["team_id"]
        s["side_team_name"] = e["side_team"].get("name")
        s["side_conference"] = e.get("side_conference")
        teams = e.get("teams") or {}
        s["matchup"] = f"{(teams.get('away') or {}).get('name')} @ {(teams.get('home') or {}).get('name')}"
        if s["status"] == W.SETTLED:
            s["market_role"] = R.market_role(s["market_implied_margin"])
        settled.append(s)
    write_gz_jsonl(ROWS, m["observations"] + m["entries"] + settled)
    report = build_report(m, settled, digest, sha)
    write_json(REPORT, report)
    for sid in W.STREAMS:
        s = report["streams"][sid]
        print(sid, json.dumps(s["denominators"]), s["replay_verdict"])
    return 0


def frozen_control_check(m: dict[str, Any]) -> dict[str, Any]:
    """The replayed CONTROL tier against the frozen CONTROL-study rows (same rule, earlier log vintage)."""
    frozen = {r["game_id"]: r["tier"] for r in read_gz_jsonl(CONTROL_ROWS)}
    replay = {c["game_id"]: c["replay_tier"] for c in m["controls"]}
    both = [g for g in frozen if g in replay]
    disagree = [{"game_id": g, "frozen": frozen[g], "replay": replay[g]} for g in both if frozen[g] != replay[g]]
    missing = [g for g in frozen if g not in replay]
    return {"frozen_rows": len(frozen), "compared": len(both), "disagreements": disagree, "missing_in_replay": missing}


def build_report(m: dict[str, Any], settled: list[dict[str, Any]], digest: str, sha: str | None) -> dict[str, Any]:
    streams = {}
    for sid in W.STREAMS:
        st = [s for s in settled if s["signal_id"] == sid and s["status"] == W.SETTLED]
        summary = W.stream_summary(sid, st)
        # the frozen read ladder (EARLY_READ, ...) counts PROSPECTIVE rows only; it says nothing about a replay
        summary.pop("verdict", None)
        rows = [
            {
                "game_id": s["game_id"],
                "game_key": s["game_key"],
                "kickoff_utc": s["kickoff_utc"],
                "week": s["week"],
                "freeze_phase": s.get("freeze_phase"),
                "side": s["side"],
                "side_team": s.get("side_team_name"),
                "matchup": s.get("matchup"),
                "side_team_id": s["side_team_id"],
                "market_implied_margin": s["market_implied_margin"],
                "actual_margin": s["actual_margin"],
                "residual": s["residual"],
                "ats_like": s["ats_like"],
                "outright_win": s["outright_win"],
                "economics": s["economics"],
                "clv": s["clv"],
                "capture_source": s.get("capture_source"),
                "entry_captured_at": s.get("entry_captured_at"),
                "entry_minutes_before": s.get("entry_minutes_before"),
                "market_role": s.get("market_role"),
                "side_conference": s.get("side_conference"),
            }
            for s in sorted(st, key=lambda s: (s["kickoff_utc"], s["game_id"]))
        ]
        obs = {o["game_id"]: o for o in m["observations"] if o["signal_id"] == sid}
        for r in rows:
            o = obs.get(r["game_id"]) or {}
            r["z"] = (o.get("signal") or {}).get("z")
            r["feature_value"] = (o.get("signal") or {}).get("value")
            r["control_tier"] = (o.get("signal") or {}).get("tier")
            r["control_provenance"] = o.get("control_provenance")
        streams[sid] = {
            "evidence_label": R.EVIDENCE_2026,
            "denominators": denominators(m, settled, sid),
            "summary": summary,
            "robustness": R.robustness(rows),
            "clv": R.clv_summary(st) if st else {"display": W.NO_SETTLED_SAMPLE},
            "replay_verdict": R.classify(summary),
            "prospective_status": W.PROSPECTIVE_TRACKING,
            "prospective_n": W.NO_SETTLED_SAMPLE,
            "games": rows,
            "unsettled": [
                {k: s.get(k) for k in ("game_id", "game_key", "status", "reason", "replay_exclusion")}
                for s in settled
                if s["signal_id"] == sid and s["status"] != W.SETTLED
            ],
        }
    pros001_obs = [
        o
        for o in m["observations"]
        if o["signal_id"] == W.PROS_001 and (o.get("signal") or {}).get("value") is not None
    ]
    values = [o["signal"]["value"] for o in pros001_obs]
    return {
        "schema": "cfb_signal_lab_wave2a_report/1.0.0",
        "version": R.VERSION,
        "evidence_label": R.EVIDENCE_2026,
        "kind": "RETROSPECTIVE replay of the frozen Wave-2 rules. Not prospective; never pooled with Wave 2.",
        "code_sha": sha,
        "inputs": {
            "main": MAIN_SHA,
            "research_data": RESEARCH_DATA_SHA,
            "script_ledger": SCRIPT_LEDGER_SHA,
            "football_sha256": FOOTBALL_SHA,
            "extract_manifest_sha256": file_sha(EXTRACT_MANIFEST),
            "candidates_sha256": W.CANDIDATES_SHA256,
            "wave2_protocol_sha256": file_sha(ROOT / "docs/research/FOOTBALL_SIGNAL_DISCOVERY_WAVE2_PROTOCOL.md"),
            "wave2a_protocol_sha256": file_sha(ROOT / "docs/research/FOOTBALL_SIGNAL_DISCOVERY_WAVE2A_PROTOCOL.md"),
        },
        "seed": W.SEED,
        "n_boot": W.N_BOOT,
        "population": {
            "rule": "completed 2026 games in the pinned log with kickoff < Wave-2 activation",
            "activation_utc": W.ACTIVATION_UTC,
            "freeze_utc": R.FREEZE_UTC,
            "targets": m["targets"],
            "skipped": m["skipped"],
            "first_capture_utc": m["first_capture"],
        },
        "membership_digest": digest,
        "orientation_counts": m["orientation_counts"],
        "control_check": frozen_control_check(m),
        "control_provenance": dict(Counter(c["provenance"] for c in m["controls"] if c["used"] in W.STRONG_TIERS)),
        "pros001_feature_distribution_2026": {
            "n": len(values),
            "mean": float(np.mean(values)) if values else None,
            "sd": float(np.std(values, ddof=1)) if len(values) > 1 else None,
            "share_abs_z_ge_1": (
                sum(1 for v in values if abs((v - W.DSC001_MEAN) / W.DSC001_SD) >= W.Z_THRESHOLD) / len(values)
                if values
                else None
            ),
            "wave1_mean": W.DSC001_MEAN,
            "wave1_sd": W.DSC001_SD,
        },
        "streams": streams,
    }


# --------------------------------------------------------------------------- history


def cmd_history(_: argparse.Namespace) -> int:
    W.load_candidates(ROOT)
    feats = []
    for p in sorted((WAVE1 / "features").glob("features_*.jsonl.gz")):
        feats += read_gz_jsonl(p)
    evaluation = read_gz_jsonl(WAVE1 / "evaluation_rows.jsonl.gz")
    joined = H.joined_rows(feats, evaluation)
    r1, r2 = H.pros001_rows(joined), H.pros002_rows(joined)
    wave1 = json.loads((WAVE1 / "evaluation_report.json").read_text())
    follow = wave1["results"]["CFB-DSC-001"]["market"]["follow_rule_abs_z_ge_1"]
    c1 = H.characterise(r1)
    reproduction = {
        "wave1_follow_rule": {k: follow[k] for k in ("n", "covers", "losses", "pushes", "mean_resid", "median_resid")},
        "wave2a": {
            "n": c1["overall"]["n"],
            "covers": c1["overall"]["ats_like"]["wins"],
            "losses": c1["overall"]["ats_like"]["losses"],
            "pushes": c1["overall"]["ats_like"]["pushes"],
            "mean_resid": c1["overall"]["residual"]["mean"],
            "median_resid": c1["overall"]["residual"]["median"],
        },
    }
    reproduction["exact"] = all(
        abs(reproduction["wave1_follow_rule"][k] - reproduction["wave2a"][k]) < 1e-9 for k in reproduction["wave2a"]
    )
    eligible2 = [r for r in r2 if r["eligible"]]
    write_json(
        HISTORY,
        {
            "schema": "cfb_signal_lab_wave2a_history/1.0.0",
            "version": R.VERSION,
            "evidence_label": R.EVIDENCE_HISTORY,
            "market_basis": H.SPORTSBOOK_CLOSE,
            "kind": "CHARACTERISATION ONLY: Wave 1 inspected these seasons; not independent validation.",
            "inputs": {
                "features_manifest_sha256": file_sha(WAVE1 / "features" / "manifest.json")
                if (WAVE1 / "features" / "manifest.json").exists()
                else None,
                "evaluation_rows_sha256": file_sha(WAVE1 / "evaluation_rows.jsonl.gz"),
            },
            "seed": W.SEED,
            "corpus_eligible_games": len(joined),
            W.PROS_001: {"reproduction_of_wave1_follow_rule": reproduction, **c1},
            W.PROS_002: {
                "strong_control_games": len(r2),
                "strong_control_with_line": sum(1 for r in r2 if r["implied"] is not None),
                "eligible_implied_below_3": len(eligible2),
                "all_strong": H.describe(r2),
                **H.characterise(eligible2),
            },
        },
    )
    print("PROS-001 history reproduction exact:", reproduction["exact"], json.dumps(reproduction))
    print("PROS-002 history eligible:", len(eligible2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("extract").set_defaults(fn=cmd_extract)
    sub.add_parser("replay").set_defaults(fn=cmd_replay)
    sub.add_parser("history").set_defaults(fn=cmd_history)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
