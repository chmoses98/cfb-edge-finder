#!/usr/bin/env python3
"""Football Signal Discovery Lab, Wave 1 -- CFB runner. RESEARCH ONLY.

Protocol: docs/research/FOOTBALL_SIGNAL_DISCOVERY_WAVE1_PROTOCOL.md

Stages (run in order):

  features   CFBD caches -> market-blind pregame feature table, one row per scheduled game,
             written with a SHA-256 manifest. Reads no line and no target result.
  evaluate   refuses unless the pre-registered hypothesis file hashes to the protocol value
             and the feature table matches its manifest; then joins lines + outcomes and
             evaluates every hypothesis once.

    python3 scripts/run_signal_discovery_cfb.py features --cfbd 2014=<dir> ... --out <dir>
    python3 scripts/run_signal_discovery_cfb.py evaluate --cfbd 2014=<dir> ... --work <dir> --out <dir>
"""

from __future__ import annotations

import os

for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import argparse  # noqa: E402
import gzip  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from concurrent.futures import ProcessPoolExecutor  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfb_edge_finder.archetype_research.replay import (  # noqa: E402
    assert_research_output_path,
    load_cfbd_season,
    schedule_targets,
)
from cfb_edge_finder.scripting.football import LeagueFitCache  # noqa: E402
from cfb_edge_finder.signal_discovery import FEATURE_SCHEMA_VERSION, SIGNAL_DISCOVERY_VERSION  # noqa: E402
from cfb_edge_finder.signal_discovery.features import freeze_table, pregame_features  # noqa: E402


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:  # pragma: no cover
        return None


def _season_features(season: str, directory: str) -> dict[str, Any]:
    data = load_cfbd_season(Path(directory))
    rows = tuple(data["rows"])
    targets, excluded = schedule_targets(data["games"], rows)
    cache = LeagueFitCache()
    out = [pregame_features(rows, t, cache) for t in targets]
    return {"season": int(season), "rows": out, "schedule_exclusions": excluded, "digests": data["digests"]}


def cmd_features(args: argparse.Namespace) -> int:
    out = Path(args.out)
    assert_research_output_path(out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = dict(s.split("=", 1) for s in args.cfbd)
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(_season_features, jobs.keys(), jobs.values()))
    manifest: dict[str, Any] = {
        "version": SIGNAL_DISCOVERY_VERSION,
        "schema": FEATURE_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "code_sha": _git_sha(),
        "seasons": {},
        "reads": "CFBD games (whitelisted meta), games_teams, advanced_*_nogarbage, drives_*; "
        "NO lines, NO target result",
    }
    for res in sorted(results, key=lambda r: r["season"]):
        path = out / f"features_{res['season']}.jsonl.gz"
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            for row in sorted(res["rows"], key=lambda r: (r["kickoff_utc"], r["game_id"])):
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        eligible = sum(1 for r in res["rows"] if r["eligibility"] is None)
        manifest["seasons"][str(res["season"])] = {
            "file": path.name,
            "rows": len(res["rows"]),
            "eligible": eligible,
            "table_sha256": freeze_table(res["rows"]),
            "schedule_exclusions": len(res["schedule_exclusions"]),
            "source_digests": res["digests"],
        }
    manifest["seconds"] = round(time.time() - t0, 1)
    (out / "features_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(json.dumps({k: v for k, v in manifest.items() if k != "seasons"}, indent=2))
    for s, m in manifest["seasons"].items():
        print(s, m["rows"], m["eligible"], m["table_sha256"][:12])
    return 0


VALIDATION_DIR = ROOT / "data" / "scripting" / "validation" / "signal_discovery_wave1"
SET1_FILE = VALIDATION_DIR / "hypotheses_set1.json"
SET2_FILE = VALIDATION_DIR / "hypotheses_set2.json"
PROTOCOL_DOC = ROOT / "docs" / "research" / "FOOTBALL_SIGNAL_DISCOVERY_WAVE1_PROTOCOL.md"


def _canon_sha(payload: Any) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def cmd_freeze_set1(args: argparse.Namespace) -> int:
    from cfb_edge_finder.signal_discovery.hypotheses import SET1, STATUS_RULE, WALK_FORWARD

    payload = {
        "set": 1,
        "version": SIGNAL_DISCOVERY_VERSION,
        "hypotheses": SET1,
        "walk_forward": WALK_FORWARD,
        "status_rule": STATUS_RULE,
    }
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    SET1_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print("set1 sha256", _canon_sha(payload))
    return 0


def cmd_freeze_set2(args: argparse.Namespace) -> int:
    from cfb_edge_finder.signal_discovery.hypotheses import SET2

    payload = {"set": 2, "version": SIGNAL_DISCOVERY_VERSION, "hypotheses": SET2, "decisive_block": "B"}
    SET2_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print("set2 sha256", _canon_sha(payload))
    return 0


def _check_frozen(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    sha = _canon_sha(payload)
    if not PROTOCOL_DOC.exists():
        raise SystemExit("protocol document missing; refusing")
    text = PROTOCOL_DOC.read_text()
    if "Status: **PRE-REGISTERED**" not in text:
        raise SystemExit("protocol is not marked PRE-REGISTERED; refusing")
    if sha not in text:
        raise SystemExit(f"{path.name} sha {sha} is not named in the protocol; refusing")
    return payload


def _load_features(work: Path, seasons: list[int]) -> list[dict]:
    manifest = json.loads((work / "features_manifest.json").read_text())
    from cfb_edge_finder.signal_discovery.features import freeze_table

    rows = []
    for s in seasons:
        meta = manifest["seasons"][str(s)]
        with gzip.open(work / meta["file"], "rt", encoding="utf-8") as fh:
            season_rows = [json.loads(line) for line in fh]
        if freeze_table(season_rows) != meta["table_sha256"]:
            raise SystemExit(f"feature table {s} does not match its manifest; refusing")
        rows += season_rows
    return rows


def _join(work: Path, cfbd: dict[str, str], seasons: list[int]):
    from cfb_edge_finder.signal_discovery.evaluate import merge
    from cfb_edge_finder.signal_discovery.market_lines import load_lines
    from cfb_edge_finder.signal_discovery.outcomes import load_outcomes

    feats = _load_features(work, seasons)
    markets, outcomes = {}, {}
    for s in seasons:
        d = Path(cfbd[str(s)])
        markets.update(load_lines(d))
        outcomes.update(load_outcomes(d, load_cfbd_season(d)["rows"]))
    return merge(feats, markets, outcomes)


def cmd_screen(args: argparse.Namespace) -> int:
    """Stage A exploration on BLOCK A seasons only."""
    from cfb_edge_finder.signal_discovery.hypotheses import STATUS_RULE
    from cfb_edge_finder.signal_discovery.screen import screen

    _check_frozen(SET1_FILE)  # set 1 must be frozen before anything is explored
    block_a = STATUS_RULE["blocks"]["A"]
    cfbd = dict(s.split("=", 1) for s in args.cfbd)
    if any(int(s) not in block_a for s in cfbd):
        raise SystemExit("the Stage A screen may only open block A seasons")
    rows, excl = _join(Path(args.work), cfbd, block_a)
    report = screen(rows)
    report["exclusions"] = dict(excl)
    report["seasons"] = block_a
    report["code_sha"] = _git_sha()
    out = Path(args.out)
    assert_research_output_path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "stage_a_screen.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=float))
    print(json.dumps(report["top"], indent=1, default=float)[:6000])
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from cfb_edge_finder.signal_discovery import deep_dive
    from cfb_edge_finder.signal_discovery.evaluate import evaluate_all, walk_forward

    set1 = _check_frozen(SET1_FILE)
    specs = list(set1["hypotheses"])
    rule = set1["status_rule"]
    set2 = None
    if SET2_FILE.exists():
        set2 = _check_frozen(SET2_FILE)
    cfbd = dict(s.split("=", 1) for s in args.cfbd)
    seasons = sorted(int(s) for s in cfbd)
    rows, excl = _join(Path(args.work), cfbd, seasons)
    t0 = time.time()
    report: dict[str, Any] = {
        "version": SIGNAL_DISCOVERY_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "code_sha": _git_sha(),
        "set1_sha256": _canon_sha(set1),
        "set2_sha256": _canon_sha(set2) if set2 else None,
        "seasons": seasons,
        "rows": len(rows),
        "rows_with_spread": sum(1 for r in rows if r["m.spread_home"] is not None),
        "rows_with_total": sum(1 for r in rows if r["m.total"] is not None),
        "rows_with_ml": sum(1 for r in rows if r["m.ml_home_novig"] is not None),
        "exclusions": dict(excl),
        "seed": 20261008,
    }
    all_specs = specs + ([dict(h, set=2) for h in set2["hypotheses"]] if set2 else [])
    ev = evaluate_all(rows, all_specs, rule)
    # set-2 candidates: the decisive evaluation is block B only (block A = their discovery sample)
    if set2:
        block_b = [r for r in rows if r["season"] in rule["blocks"]["B"]]
        evb = evaluate_all(block_b, [dict(h, set=2) for h in set2["hypotheses"]], rule)
        report["set2_block_b"] = {
            "summary": evb["summary"],
            "results": {k: _strip(v) for k, v in evb["results"].items()},
        }
    report["summary"] = ev["summary"]
    report["hypotheses_tested_football"] = ev["hypotheses_tested_football"]
    report["hypotheses_tested_market"] = ev["hypotheses_tested_market"]
    report["results"] = {k: _strip(v) for k, v in ev["results"].items()}
    report["walk_forward"] = {name: walk_forward(rows, name, spec) for name, spec in set1["walk_forward"].items()}
    report["deep_dive"] = deep_dive.run(rows, ev["results"])
    report["seconds"] = round(time.time() - t0, 1)
    out = Path(args.out)
    assert_research_output_path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluation_report.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=float))
    with gzip.open(out / "evaluation_rows.jsonl.gz", "wt", encoding="utf-8") as fh:
        keep = (
            "game_id",
            "season",
            "week",
            "home",
            "away",
            "neutral_site",
            "control_side",
            "control_strength",
            "closeness",
            "pace_claim",
            "scoring_env_claim",
            "defensive_suppression_claim",
            "disruption_sides",
            "net.sustained_efficiency",
            "net.passing",
            "net.rushing",
            "possession_environment",
            "baseline.home_margin",
            "baseline.total_points",
            "m.spread_home",
            "m.total",
            "m.ml_home_novig",
            "o.home_points",
            "o.away_points",
            "home_ats_resid",
            "total_resid",
        )
        for r in sorted(rows, key=lambda r: (r["season"], r["game_id"])):
            fh.write(json.dumps({k: r.get(k) for k in keep}, sort_keys=True) + "\n")
    for s in ev["summary"]:
        print(
            f"{s['id']:<14} {s['status']:<20} f={_fmt(s['football_effect'])} qf={_fmt(s['football_q'])} "
            f"m={_fmt(s['market_effect'])} qm={_fmt(s['market_q'])}  {s['name'][:60]}"
        )
    return 0


def _fmt(v: Any) -> str:
    return "  —  " if v is None else f"{v:+.3f}"


def _strip(res: dict) -> dict:
    return {k: v for k, v in res.items() if k != "rows"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("features")
    f.add_argument("--cfbd", action="append", required=True, help="SEASON=DIR")
    f.add_argument("--out", required=True)
    f.add_argument("--workers", type=int, default=4)
    sub.add_parser("freeze-set1")
    sub.add_parser("freeze-set2")
    for name in ("screen", "evaluate"):
        p = sub.add_parser(name)
        p.add_argument("--cfbd", action="append", required=True, help="SEASON=DIR")
        p.add_argument("--work", required=True, help="directory holding the feature tables + manifest")
        p.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.cmd == "features":
        return cmd_features(args)
    if args.cmd == "freeze-set1":
        return cmd_freeze_set1(args)
    if args.cmd == "freeze-set2":
        return cmd_freeze_set2(args)
    if args.cmd == "screen":
        return cmd_screen(args)
    if args.cmd == "evaluate":
        return cmd_evaluate(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
