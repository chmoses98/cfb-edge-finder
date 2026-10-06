#!/usr/bin/env python3
"""Retrospective validation of the scoring baseline (docs/SCRIPT_ENGINE.md section 15.1).

    python3 scripts/validate_scoring_baseline.py \\
        --cfbd 2024=<dir> --cfbd 2025=<dir> \\
        --espn-log data/football/2026/team_games.jsonl \\
        --espn-schedule data/football/2026/schedule.json \\
        --as-of 2026-10-06T00:00:00Z \\
        --out data/scripting/validation --report docs/SCORING_BASELINE_VALIDATION.md

Each `--cfbd` directory holds the CFBD season cache files games.json.gz,
games_teams.json.gz, advanced_regular_nogarbage.json.gz and
drives_regular.json.gz (from the `research-data` branch,
data/research_cache/v2/<season>/). The target is actual scores; no market
or sportsbook number is read. Splits and corrections are the pre-registered
ones; there are no knobs to tune them.
"""

from __future__ import annotations

import os

# One BLAS thread per process: the league fits are many small solves, and
# competing BLAS thread pools make them an order of magnitude slower.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import argparse  # noqa: E402
import gzip  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from concurrent.futures import ProcessPoolExecutor  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfb_edge_finder.scripting import METHODOLOGY_VERSION  # noqa: E402
from cfb_edge_finder.scripting.baseline_eval import (  # noqa: E402
    CORRECTIONS,
    GATE,
    apply_correction,
    band_offset_diagnostics,
    block_stats,
    defense_diagnostics,
    evaluate,
    fit_correction,
    gate_checks,
    residual_profile,
    segment_stats,
    targets_from_rows,
)
from cfb_edge_finder.scripting.cfbd import team_games_from_cfbd  # noqa: E402
from cfb_edge_finder.scripting.gamelog import read_jsonl, rows_fingerprint  # noqa: E402

#: Pre-registered (docs/SCRIPT_ENGINE.md section 15.1). Not configurable.
SPLITS = {
    "A": {"train": ["2024"], "test": "2025"},
    "B": {"train": ["2024", "2025"], "test": "2026"},
}


def _load_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def load_cfbd(directory: Path) -> tuple[list, dict[str, dict[str, Any]], dict[str, str]]:
    games = _load_gz(directory / "games.json.gz")
    rows = team_games_from_cfbd(
        games,
        _load_gz(directory / "games_teams.json.gz"),
        _load_gz(directory / "advanced_regular_nogarbage.json.gz"),
        _load_gz(directory / "drives_regular.json.gz"),
        observed_at="historical-cfbd-cache",
    )
    meta = {
        str(g["id"]): {
            "home_id": str(g["homeId"]),
            "away_id": str(g["awayId"]),
            "neutral_site": bool(g.get("neutralSite")),
            "conference_game": g.get("conferenceGame"),
        }
        for g in games
        if g.get("id") is not None
    }
    digests = {
        f: hashlib.sha256((directory / f).read_bytes()).hexdigest()[:16]
        for f in (
            "games.json.gz",
            "games_teams.json.gz",
            "advanced_regular_nogarbage.json.gz",
            "drives_regular.json.gz",
        )
    }
    return rows, meta, digests


def load_espn(log: Path, schedule: Path, as_of: str) -> tuple[list, dict[str, dict[str, Any]], dict[str, str]]:
    rows = [r for r in read_jsonl(log) if r.kickoff_utc < as_of]
    meta = {}
    for event in json.loads(schedule.read_text())["events"]:
        sides = {c["home_away"]: c for c in event.get("competitors") or []}
        if set(sides) != {"home", "away"}:
            continue
        meta[str(event["id"])] = {
            "home_id": str(sides["home"]["team_id"]),
            "away_id": str(sides["away"]["team_id"]),
            "neutral_site": bool(event.get("neutral_site")),
            "conference_game": event.get("conference_game"),
        }
    digests = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:16] for p in (log, schedule)}
    return rows, meta, digests


def run_block(name: str, rows: list, meta: dict, digests: dict) -> dict[str, Any]:
    started = time.time()
    targets, skipped = targets_from_rows(rows, meta)
    records = evaluate(rows, targets)
    leaks = sum(1 for r in records if not r["cutoff_check"])
    if leaks:
        raise SystemExit(f"{name}: {leaks} records saw a game at or after their own kickoff")
    print(f"  {name}: {len(records)} games in {time.time() - started:.0f}s", file=sys.stderr)
    return {
        "records": records,
        "inputs": {"rows": len(rows), "rows_fingerprint": rows_fingerprint(rows), "files": digests},
        "targets_skipped": skipped,
    }


def _block_job(name: str, kind: str, source: Any) -> dict[str, Any]:
    if kind == "cfbd":
        return run_block(name, *load_cfbd(Path(source)))
    log, schedule, as_of = source
    return run_block(name, *load_espn(Path(log), Path(schedule), as_of))


def summarize(blocks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"blocks": {}, "splits": {}}
    for name, block in blocks.items():
        recs = block["records"]
        out["blocks"][name] = {
            **block_stats(recs),
            "inputs": block["inputs"],
            "targets_skipped": block["targets_skipped"],
            "segments": segment_stats(recs),
            "defense": defense_diagnostics(recs),
            "primary_total_band_hit_rate": _band_hits(recs),
            "band_offsets": band_offset_diagnostics(recs),
            "residuals": residual_profile(recs),
        }
    for split, spec in SPLITS.items():
        if spec["test"] not in blocks or any(t not in blocks for t in spec["train"]):
            continue
        train = [r for t in spec["train"] for r in blocks[t]["records"]]
        test = blocks[spec["test"]]["records"]
        methods = {}
        for method in CORRECTIONS:
            model = fit_correction(method, train)
            corrected = apply_correction(model, test)
            methods[method] = {
                "model": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in model.items()},
                "test": block_stats(corrected),
                "gate": gate_checks(corrected, test),
            }
        out["splits"][split] = {
            "train": spec["train"],
            "test": spec["test"],
            "raw_baseline_on_test": gate_checks([r for r in test if r.get("pred_total") is not None], test),
            "corrections": methods,
        }
    return out


def _band_hits(records: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, list[bool]] = {}
    for r in records:
        if r.get("actual_in_primary_total_band") is None:
            continue
        by.setdefault(r["primary"], []).append(r["actual_in_primary_total_band"])
    return {k: {"n": len(v), "inside": sum(v), "rate": round(sum(v) / len(v), 3)} for k, v in sorted(by.items())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cfbd", action="append", default=[], help="SEASON=DIR")
    ap.add_argument("--espn-log", type=Path)
    ap.add_argument("--espn-schedule", type=Path)
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    jobs = {spec.split("=", 1)[0]: ("cfbd", spec.split("=", 1)[1]) for spec in args.cfbd}
    if args.espn_log and args.espn_schedule:
        jobs["2026"] = ("espn", (str(args.espn_log), str(args.espn_schedule), args.as_of))
    with ProcessPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(_block_job, name, kind, src) for name, (kind, src) in jobs.items()}
        blocks = {name: futures[name].result() for name in sorted(futures)}

    summary = summarize(blocks)
    summary.update(
        {
            "schema": "cfb_scoring_baseline_validation/1.0.0",
            "methodology_version": METHODOLOGY_VERSION,
            "as_of": args.as_of,
            "protocol": "docs/SCRIPT_ENGINE.md section 15.1 (pre-registered)",
            "target": "actual final scores; no market total, sportsbook line or price is read",
            "gate_thresholds": GATE,
            "pre_registered_splits": SPLITS,
        }
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "scoring_baseline_report.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    for name, block in blocks.items():
        with gzip.open(args.out / f"records_{name}.jsonl.gz", "wt", encoding="utf-8") as fh:
            for r in block["records"]:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
    if args.report:
        from validate_scoring_baseline_report import render  # noqa: PLC0415

        args.report.write_text(render(summary))
    print(json.dumps({k: v["total"] for k, v in summary["blocks"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
