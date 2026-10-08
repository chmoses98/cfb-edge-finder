#!/usr/bin/env python3
"""Script Engine V2 candidate: development fit, sealed-holdout prediction, gated reveal. RESEARCH ONLY.

Protocol: docs/ARCHETYPE_V2_HOLDOUT_PROTOCOL.md. Three stages, run in order, each committed:

  1. fit-dev           2021-2025 Wave 1 rows -> frozen parameters + development report
                       (committed WITH the protocol, before any holdout data is opened)
  2. predict-holdout   2014-2020 -> frozen pregame records + V2 claims + a per-game hash manifest.
                       No target game's own result is read.
  3. score-holdout     refuses to run unless the protocol names the frozen-parameter hash and the
                       predictions still match the manifest; then reveals, scores and reports.

    python3 scripts/run_archetype_v2_study.py fit-dev
    python3 scripts/run_archetype_v2_study.py predict-holdout --cfbd 2014=<dir> ... --cfbd 2020=<dir> --work <dir>
    python3 scripts/run_archetype_v2_study.py score-holdout --cfbd 2014=<dir> ... --work <dir>

Changes no production rule; publishes no probability; never reads a price or a line.
"""

from __future__ import annotations

import os

for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import argparse  # noqa: E402
import gzip  # noqa: E402
import hashlib  # noqa: E402
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
from cfb_edge_finder.archetype_research.v2 import (  # noqa: E402
    DEVELOPMENT_SEASONS,
    FROZEN_PARAMETERS,
    HOLDOUT_SEASONS,
    PROTOCOL,
    V2_VERSION,
)
from cfb_edge_finder.archetype_research.v2.compare import compare  # noqa: E402
from cfb_edge_finder.archetype_research.v2.evaluate import criteria, evaluate  # noqa: E402
from cfb_edge_finder.archetype_research.v2.fit import (  # noqa: E402
    attach_claims,
    fit_development,
    load_frozen,
    write_frozen,
)
from cfb_edge_finder.archetype_research.v2.holdout import (  # noqa: E402
    check_gate,
    manifest_of,
    predict_season,
    reveal_and_score,
)
from cfb_edge_finder.scripting import METHODOLOGY_VERSION  # noqa: E402

VALIDATION = ROOT / "data" / "scripting" / "validation"
DEV_ROWS = VALIDATION / "archetype_rows"
DEV_REPORT = VALIDATION / "archetype_v2_development_report.json"
MANIFEST = VALIDATION / "archetype_v2_holdout_manifest.json.gz"
HOLDOUT_REPORT = VALIDATION / "archetype_v2_holdout_report.json"
HOLDOUT_ROWS = VALIDATION / "archetype_v2_holdout_rows"


def _git(*args: str) -> str | None:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip() or None
    except Exception:  # noqa: BLE001
        return None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_rows(path: Path) -> list[dict[str, Any]]:
    out = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if "excluded" not in r:
                out.append(r)
    return out


def _write_gz(path: Path, rows: list[dict[str, Any]]) -> None:
    assert_research_output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")


# ------------------------------------------------------------------ stage 1


def fit_dev(_: argparse.Namespace) -> int:
    files = {s: DEV_ROWS / f"rows_{s}.jsonl.gz" for s in DEVELOPMENT_SEASONS}
    records = [r for s in DEVELOPMENT_SEASONS for r in _read_rows(files[s])]
    attach_claims(records)
    sources = {
        "wave1_rows_sha256": {str(s): _sha(p) for s, p in files.items()},
        "wave1_report": "data/scripting/validation/archetype_historical_report.json",
    }
    params = fit_development(records, sources)
    write_frozen(params, ROOT / FROZEN_PARAMETERS)
    ev = evaluate(records, params)
    report = {
        "schema": "cfb_archetype_v2_development_report/1.0.0",
        "block": "DEVELOPMENT 2021-2025 (in-sample; the only data V2 was designed and fitted on)",
        "v2_version": V2_VERSION,
        "methodology_version": METHODOLOGY_VERSION,
        "frozen_parameters_sha256": params["sha256"],
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_sha_at_run": _git("rev-parse", "HEAD"),
        "evaluation": ev,
        "criteria_in_sample": criteria(ev),
        "v1_vs_v2_in_sample": compare(records, ev, criteria(ev)),
    }
    DEV_REPORT.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"frozen_parameters_sha256": params["sha256"], "development_games": params["development_games"]}))
    return 0


# ------------------------------------------------------------------ stage 2


def _predict_job(name: str, directory: str) -> dict[str, Any]:
    started = time.time()
    out = predict_season(load_cfbd_season(Path(directory)))
    print(f"  {name}: {len(out['records'])} targets predicted in {time.time() - started:.0f}s", file=sys.stderr)
    return out


def predict_holdout(args: argparse.Namespace) -> int:
    jobs = dict(spec.split("=", 1) for spec in args.cfbd)
    if sorted(int(s) for s in jobs) != list(HOLDOUT_SEASONS):
        raise SystemExit(f"predict-holdout needs exactly the holdout seasons {HOLDOUT_SEASONS}")
    check_gate(ROOT / PROTOCOL, ROOT / FROZEN_PARAMETERS)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {s: pool.submit(_predict_job, s, d) for s, d in jobs.items()}
        seasons = {s: futures[s].result() for s in sorted(futures)}
    manifest: dict[str, Any] = {
        "schema": "cfb_archetype_v2_holdout_manifest/1.0.0",
        "v2_version": V2_VERSION,
        "frozen_parameters_sha256": load_frozen(ROOT / FROZEN_PARAMETERS)["sha256"],
        "protocol_commit": _git("log", "-1", "--format=%H", "--", PROTOCOL),
        "code_sha_at_run": _git("rev-parse", "HEAD"),
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "Pregame predictions only. No holdout game's own result was read to produce this manifest.",
        "seasons": {},
    }
    for s, out in seasons.items():
        _write_gz(work / f"predictions_{s}.jsonl.gz", out["records"])
        m = manifest_of(out["records"])
        manifest["seasons"][s] = {
            "manifest_sha256": m["sha256"],
            "games": m["games"],
            "coverage": out["coverage"],
            "file_sha256": out["digests"],
            "schedule_exclusions": out["schedule_exclusions"],
        }
    assert_research_output_path(MANIFEST)
    with gzip.open(MANIFEST, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    print(json.dumps({s: v["manifest_sha256"][:16] for s, v in manifest["seasons"].items()}))
    return 0


# ------------------------------------------------------------------ stage 3


def _score_job(name: str, directory: str, work: str, manifest_sha: str) -> dict[str, Any]:
    data = load_cfbd_season(Path(directory))
    targets, _ = schedule_targets(data["games"], data["rows"])
    predictions = _read_rows(Path(work) / f"predictions_{name}.jsonl.gz")
    revealed, frozen = reveal_and_score(
        predictions,
        {"sha256": manifest_sha},
        data["rows"],
        {t.game_id: t for t in targets},
        protocol_path=ROOT / PROTOCOL,
        frozen_path=ROOT / FROZEN_PARAMETERS,
    )
    return {"records": revealed}


def score_holdout(args: argparse.Namespace) -> int:
    with gzip.open(MANIFEST, "rt", encoding="utf-8") as fh:
        manifest = json.load(fh)
    frozen = check_gate(ROOT / PROTOCOL, ROOT / FROZEN_PARAMETERS)
    if manifest["frozen_parameters_sha256"] != frozen["sha256"]:
        raise SystemExit("manifest was produced under different frozen parameters")
    jobs = dict(spec.split("=", 1) for spec in args.cfbd)
    with ProcessPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {
            s: pool.submit(_score_job, s, d, args.work, manifest["seasons"][s]["manifest_sha256"])
            for s, d in jobs.items()
        }
        seasons = {s: futures[s].result() for s in sorted(futures)}
    records = [r for s in sorted(seasons) for r in seasons[s]["records"]]
    ev = evaluate(records, frozen)
    crit = criteria(ev)
    dev = json.loads(DEV_REPORT.read_text())
    report = {
        "schema": "cfb_archetype_v2_holdout_report/1.0.0",
        "block": "SEALED HISTORICAL HOLDOUT 2014-2020 (scored once, after the protocol and frozen parameters)",
        "v2_version": V2_VERSION,
        "methodology_version": METHODOLOGY_VERSION,
        "protocol": PROTOCOL,
        "protocol_commit": _git("log", "-1", "--format=%H", "--", PROTOCOL),
        "frozen_parameters_sha256": frozen["sha256"],
        "frozen_parameters_commit": _git("log", "-1", "--format=%H", "--", FROZEN_PARAMETERS),
        "manifest_commit": _git("log", "-1", "--format=%H", "--", str(MANIFEST.relative_to(ROOT))),
        "manifest_season_sha256": {s: v["manifest_sha256"] for s, v in manifest["seasons"].items()},
        "code_sha_at_run": _git("rev-parse", "HEAD"),
        "base_main_sha": args.base_main_sha,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "holdout_seasons": list(HOLDOUT_SEASONS),
        "development_seasons": list(DEVELOPMENT_SEASONS),
        "coverage": {s: {**v["coverage"], "file_sha256": v["file_sha256"]} for s, v in manifest["seasons"].items()},
        "frozen_control_intervals": {t: v["intervals"] for t, v in frozen["control"].items()},
        "evaluation": ev,
        "criteria": crit,
        "v1_vs_v2": compare(records, ev, crit),
        "development_reference": {
            "evaluation": dev["evaluation"],
            "criteria_in_sample": dev["criteria_in_sample"],
            "v1_vs_v2_in_sample": dev["v1_vs_v2_in_sample"],
        },
        "production_changed": False,
        "probabilities_published": False,
    }
    HOLDOUT_REPORT.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    for s, out in seasons.items():
        _write_gz(HOLDOUT_ROWS / f"rows_{s}.jsonl.gz", out["records"])
    if args.tables:
        from archetype_v2_tables import render  # noqa: PLC0415

        Path(args.tables).write_text(render(report))
    print(json.dumps(crit["summary"], indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("fit-dev")
    p = sub.add_parser("predict-holdout")
    p.add_argument("--cfbd", action="append", required=True, help="SEASON=DIR")
    p.add_argument("--work", required=True)
    s = sub.add_parser("score-holdout")
    s.add_argument("--cfbd", action="append", required=True, help="SEASON=DIR")
    s.add_argument("--work", required=True)
    s.add_argument("--tables")
    s.add_argument("--base-main-sha", default="f574e0db3be383697f1f0cd128e2aaffd5e85aba")
    args = ap.parse_args()
    return {"fit-dev": fit_dev, "predict-holdout": predict_holdout, "score-holdout": score_holdout}[args.stage](args)


if __name__ == "__main__":
    raise SystemExit(main())
