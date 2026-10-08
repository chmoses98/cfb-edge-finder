"""2026 CONTROL x Kalshi game-winner market-pricing study (research only).

Pre-registered in docs/CONTROL_2026_MARKET_PROTOCOL.md. Three stages, run in order:

    python scripts/run_control_market_study.py extract   # captured quotes -> quotes_2026.jsonl.gz (no outcome)
    python scripts/run_control_market_study.py freeze    # replay + CONTROL + selected quotes -> manifest (no outcome)
    python scripts/run_control_market_study.py reveal    # settlement join -> report, rows, tables

Every input is read from git at a pinned commit (football log and catalog
history on main @ BASE_MAIN_SHA, observations/settlements on research-data @
RESEARCH_DATA_SHA, V2 ledger on script-ledger @ SCRIPT_LEDGER_SHA), so a rerun
is byte-identical regardless of what production has committed since.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import collect_live_context as names_util  # noqa: E402  (production name matching; no network)
from control_market_tables import render_tables  # noqa: E402

from cfb_edge_finder.control_market import (  # noqa: E402
    BASE_MAIN_SHA,
    POPULATIONS,
    PROTOCOL_COMMIT,
    PROTOCOL_SHA256,
    RESEARCH_DATA_SHA,
    SCRIPT_LEDGER_SHA,
    STUDY_DIR,
    STUDY_VERSION,
    TIERS,
    assert_output_path,
    canonical,
    load_protocol,
    verify_protocol,
)
from cfb_edge_finder.control_market.football import (  # noqa: E402
    outcome_for,
    prospective_rows,
    replay_game,
    replay_targets,
    schedule_meta,
)
from cfb_edge_finder.control_market.quotes import (  # noqa: E402
    Quote,
    orient_markets,
    quotes_from_catalog_game,
    quotes_from_observation_row,
)
from cfb_edge_finder.control_market.report import build_report, sensitivities  # noqa: E402
from cfb_edge_finder.control_market.study import freeze_row, reveal_row  # noqa: E402
from cfb_edge_finder.scripting.calibration import load_calibration  # noqa: E402
from cfb_edge_finder.scripting.football import LeagueFitCache  # noqa: E402
from cfb_edge_finder.scripting.gamelog import TeamGame, row_order  # noqa: E402

OUT = ROOT / STUDY_DIR
QUOTES = OUT / "quotes_2026.jsonl.gz"
QUOTES_META = OUT / "quotes_2026_manifest.json"
MANIFEST = OUT / "control_market_manifest.json.gz"
FREEZE_RECORD = OUT / "freeze_record.json"
REPORT = OUT / "control_market_report.json"
ROWS = OUT / "control_market_rows.jsonl.gz"
FOOTBALL_LOG = "data/football/2026/team_games.jsonl"
SCHEDULE = "data/football/2026/schedule.json"
FOOTBALL_SHA = {
    FOOTBALL_LOG: "6e6cf409f25e511c465c4537b7e3a0f1574d60adb456419246083c6c6f31b242",
    SCHEDULE: "daee74eebe1a066ac87922dc17515ecdf4bfdefb366805cc08556a569d95e0b2",
}
#: Quotes on markets whose source kickoff is after this cannot belong to a game in this freeze.
QUOTE_KICKOFF_LIMIT = "2026-10-09T00:00:00Z"


# --------------------------------------------------------------------------- git helpers


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def git_bytes(ref_path: str) -> bytes:
    return subprocess.run(["git", "show", ref_path], cwd=ROOT, check=True, capture_output=True).stdout


class BlobReader:
    """`git cat-file --batch` reader (one process for thousands of blobs)."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            ["git", "cat-file", "--batch"], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )

    def read(self, sha: str) -> bytes:
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(sha.encode() + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().split()
        size = int(header[2])
        data = self.proc.stdout.read(size)
        self.proc.stdout.read(1)
        return data

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait()


def write_gz_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    assert_output_path(path.relative_to(ROOT))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(canonical(r) + "\n" for r in rows).encode("utf-8")
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as fh:
        fh.write(payload)
    return hashlib.sha256(payload).hexdigest()


def read_gz_jsonl(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_json(path: Path, payload: Any) -> None:
    assert_output_path(path.relative_to(ROOT))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- frozen inputs


def load_football() -> tuple[list[TeamGame], list[dict[str, Any]]]:
    blobs = {p: git_bytes(f"{BASE_MAIN_SHA}:{p}") for p in FOOTBALL_SHA}
    for p, data in blobs.items():
        digest = hashlib.sha256(data).hexdigest()
        if digest != FOOTBALL_SHA[p]:
            raise SystemExit(f"frozen input {p} hash {digest} != registered {FOOTBALL_SHA[p]}")
    rows = sorted(
        (TeamGame.from_dict(json.loads(line)) for line in blobs[FOOTBALL_LOG].decode().splitlines() if line.strip()),
        key=row_order,
    )
    events = json.loads(blobs[SCHEDULE])["events"]
    return rows, events


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def market_keys(name: str) -> set[str]:
    """Production Kalshi-side team spellings (registry + affix variants)."""
    return names_util._kalshi_team_keys({"home": name, "away": None})["home"] | names_util.name_variants(_fold(name))


def with_name_keys(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ESPN competitor name keys exactly as `scripts/script_engine.load_football` builds them."""
    out = json.loads(json.dumps(events))
    for event in out:
        for c in event.get("competitors") or []:
            keys: set[str] = set()
            for name in c.get("names") or []:
                keys |= names_util.name_variants(name) | names_util.name_variants(_fold(name))
            c["name_keys"] = sorted(keys)
    return out


# --------------------------------------------------------------------------- stage 1: extract quotes


def extract_observation_quotes() -> tuple[list[Quote], dict[str, Any]]:
    paths = [
        p
        for p in git("ls-tree", "-r", "--name-only", RESEARCH_DATA_SHA, "data/research/observations/2026").split()
        if p.endswith(".jsonl")
    ]
    quotes: list[Quote] = []
    for p in sorted(paths):
        for line in git_bytes(f"{RESEARCH_DATA_SHA}:{p}").decode().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("capture_mode") != "PROSPECTIVE":
                continue
            q = quotes_from_observation_row(row)
            if q is not None:
                quotes.append(q)
    return quotes, {"commit": RESEARCH_DATA_SHA, "files": len(paths)}


def extract_catalog_quotes() -> tuple[list[Quote], dict[str, Any]]:
    log = git("log", "--format=%H %s", BASE_MAIN_SHA, "--", "data/live/cfb_catalog_status.json").splitlines()
    commits = [line.split(" ", 1)[0] for line in log if " kalshi cfb catalog" in f" {line.split(' ', 1)[1]}"]
    reader = BlobReader()
    cache: dict[str, tuple[list[Quote], ...]] = {}
    quotes: list[Quote] = []
    used = []
    try:
        for commit in reversed(commits):
            status = json.loads(git_bytes(f"{commit}:data/live/cfb_catalog_status.json"))
            if not status.get("capture_complete"):
                continue
            captured_at = status["captured_at"]
            used.append({"commit": commit, "captured_at": captured_at})
            tree = git("ls-tree", f"{commit}:data/live/games").splitlines()
            for entry in tree:
                meta, name = entry.split("\t", 1)
                blob = meta.split()[2]
                if blob not in cache:
                    game = json.loads(reader.read(blob))
                    cache[blob] = (quotes_from_catalog_game(game, "", ""),)
                for q in cache[blob][0]:
                    if q.kickoff_hint and q.kickoff_hint >= QUOTE_KICKOFF_LIMIT:
                        continue
                    quotes.append(Quote(**{**q.to_dict(), "captured_at": captured_at, "provenance": commit}))
    finally:
        reader.close()
    return quotes, {"base": BASE_MAIN_SHA, "snapshots": used}


def cmd_extract(_: argparse.Namespace) -> int:
    verify_protocol(load_protocol(ROOT))
    obs, obs_meta = extract_observation_quotes()
    cat, cat_meta = extract_catalog_quotes()
    quotes = sorted(obs + cat, key=lambda q: (q.market_ticker, q.captured_at, q.source))
    digest = write_gz_jsonl(QUOTES, [q.to_dict() for q in quotes])
    write_json(
        QUOTES_META,
        {
            "schema": "cfb_control_market_quotes/1.0.0",
            "study_version": STUDY_VERSION,
            "protocol_sha256": PROTOCOL_SHA256,
            "content_sha256": digest,
            "counts": {
                "RESEARCH_OBSERVATION": len(obs),
                "CATALOG_SNAPSHOT": len(cat),
                "markets": len({q.market_ticker for q in quotes}),
                "events": len({q.event_ticker for q in quotes}),
            },
            "research_observations": obs_meta,
            "catalog": {
                "base": cat_meta["base"],
                "snapshots_used": len(cat_meta["snapshots"]),
                "first": cat_meta["snapshots"][0],
                "last": cat_meta["snapshots"][-1],
            },
            "not_an_outcome_input": "quotes only: no score, settlement or result is read by this stage",
        },
    )
    print(f"quotes: {len(obs)} observation + {len(cat)} catalog -> {QUOTES.relative_to(ROOT)} ({digest[:12]})")
    return 0


def load_quotes() -> list[Quote]:
    meta = json.loads(QUOTES_META.read_text())
    with gzip.open(QUOTES, "rb") as fh:
        payload = fh.read()
    if hashlib.sha256(payload).hexdigest() != meta["content_sha256"]:
        raise SystemExit("quotes extract does not match its manifest hash")
    return [Quote(**json.loads(line)) for line in payload.decode().splitlines() if line.strip()]


# --------------------------------------------------------------------------- stage 2: freeze


def cmd_freeze(_: argparse.Namespace) -> int:
    verify_protocol(load_protocol(ROOT))
    rows, events = load_football()
    calibration = load_calibration(ROOT)
    targets, skipped = replay_targets(rows, schedule_meta(events))
    cache = LeagueFitCache()
    football = [replay_game(rows, t, cache, calibration) for t in targets]
    ledger = [
        json.loads(line)
        for line in git_bytes(f"{SCRIPT_LEDGER_SHA}:data/scripting/ledger/2026/publications_v2.jsonl")
        .decode()
        .splitlines()
        if line.strip()
    ]
    completed_keys: set[str] = set()  # a V2 game must have kicked off before the freeze and be in the log
    log_ids = {r.game_id for r in rows}
    for row in ledger:
        if str(row.get("event_id")) in log_ids:
            completed_keys.add(row.get("game_key"))
    prospective = prospective_rows(ledger, completed_keys)
    quotes = load_quotes()
    orientation = orient_markets(quotes, with_name_keys(events), market_keys)
    by_ticker: dict[str, list[Quote]] = defaultdict(list)
    for q in quotes:
        by_ticker[q.market_ticker].append(q)
    population = [f for f in football if f["control"]] + [p for p in prospective if p["control"]]
    abbrs = {
        str(e["id"]): {str(c.get("abbreviation") or "").upper() for c in e.get("competitors") or []} for e in events
    }
    frozen = [freeze_row(f, by_ticker, orientation, abbrs.get(f["game_id"])) for f in population]
    tiers = defaultdict(int)
    for f in frozen:
        tiers[f"{f['population']}|{f['tier']}"] += 1
    manifest = {
        "schema": "cfb_control_market_manifest/1.0.0",
        "study_version": STUDY_VERSION,
        "protocol_sha256": PROTOCOL_SHA256,
        "protocol_commit": PROTOCOL_COMMIT,
        "inputs": {
            "football_log": {"commit": BASE_MAIN_SHA, **{p: s for p, s in FOOTBALL_SHA.items()}},
            "quotes_sha256": json.loads(QUOTES_META.read_text())["content_sha256"],
            "v2_ledger_commit": SCRIPT_LEDGER_SHA,
            "v2_ledger_rows": len(ledger),
            "v2_ledger_final_pregame_rows": sum(1 for r in ledger if r.get("kind") == "FINAL_PREGAME"),
        },
        "replay": {
            "targets": len(targets),
            "skipped": skipped,
            "control_games": sum(1 for f in football if f["control"]),
            "no_control_claim": sum(1 for f in football if not f["control"]),
            "weeks": sorted({f["week"] for f in football}),
            "kickoff_range": [targets[0].kickoff_utc, targets[-1].kickoff_utc] if targets else None,
        },
        "prospective": {"rows": len(prospective), "control_rows": sum(1 for p in prospective if p["control"])},
        "orientation_status_counts": dict(
            sorted(
                {
                    s: sum(1 for o in orientation.values() if o["status"] == s)
                    for s in {o["status"] for o in orientation.values()}
                }.items()
            )
        ),
        "tier_counts": dict(sorted(tiers.items())),
        "rows": frozen,
        "outcome_read": False,
    }
    payload = canonical(manifest).encode("utf-8")
    assert_output_path(MANIFEST.relative_to(ROOT))
    with open(MANIFEST, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as fh:
        fh.write(payload)
    digest = hashlib.sha256(payload).hexdigest()
    write_json(
        FREEZE_RECORD,
        {
            "manifest": str(MANIFEST.relative_to(ROOT)),
            "manifest_sha256": digest,
            "protocol_sha256": PROTOCOL_SHA256,
            "rows": len(frozen),
            "tier_counts": manifest["tier_counts"],
            "replay": manifest["replay"],
            "prospective": manifest["prospective"],
            "orientation_status_counts": manifest["orientation_status_counts"],
            "outcome_read": False,
        },
    )
    print(json.dumps({"manifest_sha256": digest, "rows": len(frozen), "tiers": manifest["tier_counts"]}, indent=1))
    return 0


# --------------------------------------------------------------------------- stage 3: reveal


def official_settlements() -> dict[str, list[str]]:
    """ticker -> research-data settlement values ("yes"/"no"), used only to cross-check the game-log settlement."""
    out: dict[str, list[str]] = defaultdict(list)
    for folder in ("data/research/settlements/2026", "data/research/attributions/2026"):
        for p in git("ls-tree", "-r", "--name-only", RESEARCH_DATA_SHA, folder).split():
            for line in git_bytes(f"{RESEARCH_DATA_SHA}:{p}").decode().splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                ticker = r.get("kalshi_market_ticker")
                if not ticker or not str(ticker).startswith("KXNCAAFGAME-"):
                    continue
                for key in ("official_kalshi_settlement", "derived_contract_settlement"):
                    v = r.get(key)
                    if isinstance(v, str) and v.lower() in ("yes", "no"):
                        out[ticker].append(v.lower())
    return {t: sorted(set(v)) for t, v in out.items()}


def cmd_reveal(_: argparse.Namespace) -> int:
    verify_protocol(load_protocol(ROOT))
    record = json.loads(FREEZE_RECORD.read_text())
    with gzip.open(MANIFEST, "rb") as fh:
        payload = fh.read()
    if hashlib.sha256(payload).hexdigest() != record["manifest_sha256"]:
        raise SystemExit("freeze manifest does not match the recorded hash; refusing to reveal")
    tracked = git("ls-files", str(MANIFEST.relative_to(ROOT)), str(FREEZE_RECORD.relative_to(ROOT))).split()
    if len(tracked) != 2 or git("status", "--porcelain", "--", str(MANIFEST.relative_to(ROOT))).strip():
        raise SystemExit("freeze manifest must be committed, unmodified, before reveal")
    manifest = json.loads(payload)
    rows, _ = load_football()
    official = official_settlements()
    revealed = []
    for frozen in manifest["rows"]:
        outcome = outcome_for(rows, frozen["game_id"], frozen["control_team_id"])
        revealed.append(reveal_row(frozen, outcome, official))
    code_sha = git("rev-parse", "HEAD").strip()
    for r in revealed:
        r["source_code_sha"] = code_sha
        r["protocol_sha256"] = PROTOCOL_SHA256
    rows_sha = write_gz_jsonl(ROWS, revealed)
    report = {
        "schema": "cfb_control_market_report/1.0.0",
        "study_version": STUDY_VERSION,
        "kind": "MARKET-PRICING RESEARCH (retrospective except PROSPECTIVE_V2_CONTROL rows); not a recommendation",
        "protocol_sha256": PROTOCOL_SHA256,
        "protocol_commit": PROTOCOL_COMMIT,
        "manifest_sha256": record["manifest_sha256"],
        "rows_sha256": rows_sha,
        "source_code_sha": code_sha,
        "inputs": manifest["inputs"],
        "replay": manifest["replay"],
        "prospective": manifest["prospective"],
        "orientation_status_counts": manifest["orientation_status_counts"],
        "settlement_cross_checked_tickers": len(official),
        "populations": list(POPULATIONS),
        "tiers": list(TIERS),
        "results": build_report(revealed, POPULATIONS),
        "sensitivities": sensitivities(revealed),
    }
    write_json(REPORT, report)
    tables = render_tables(report)
    (ROOT / "docs/CONTROL_2026_MARKET_TABLES.md").write_text(tables, encoding="utf-8")
    pooled = report["results"]["POOLED"]
    print(
        json.dumps(
            {
                "overall": pooled["overall_verdict"],
                "verdicts": {t: pooled["groups"][t]["verdict"]["verdict"] for t in TIERS},
            },
            indent=1,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("extract").set_defaults(fn=cmd_extract)
    sub.add_parser("freeze").set_defaults(fn=cmd_freeze)
    sub.add_parser("reveal").set_defaults(fn=cmd_reveal)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
