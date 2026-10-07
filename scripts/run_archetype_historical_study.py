#!/usr/bin/env python3
"""Historical archetype validation (docs/ARCHETYPE_HISTORICAL_PROTOCOL.md). RESEARCH ONLY.

    python3 scripts/run_archetype_historical_study.py \\
        --cfbd 2021=<dir> --cfbd 2022=<dir> ... --cfbd 2025=<dir> \\
        --espn-pregame-only data/football/2026/team_games.jsonl data/football/2026/schedule.json \\
        --out data/scripting/validation --tables docs/ARCHETYPE_HISTORICAL_TABLES.md

Each `--cfbd` directory holds the CFBD season cache files named in
`archetype_research.replay.CFBD_FILES` (from the `research-data` branch,
data/research_cache/v2/<season>/). For every completed game the production
Script Engine is replayed from the games that finished before that game's
data cutoff, the pregame record is frozen and hashed, and only then is the
game's own result revealed and scored.

`--espn-pregame-only` replays the 2026 production ESPN log for generation
rates ONLY: no 2026 result is revealed or scored (2026 is the prospective
season).

Writes nothing under the live publication or ledger paths, publishes no
probability, and changes no production rule.
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
from collections import Counter  # noqa: E402
from concurrent.futures import ProcessPoolExecutor  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfb_edge_finder.archetype_research import (  # noqa: E402
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    PROTOCOL,
    PROTOCOL_COMMIT,
    RESEARCH_VERSION,
)
from cfb_edge_finder.archetype_research import analysis as A  # noqa: E402
from cfb_edge_finder.archetype_research.replay import (  # noqa: E402
    Target,
    assert_research_output_path,
    file_digest,
    load_cfbd_season,
)
from cfb_edge_finder.archetype_research.study import run_season, run_targets  # noqa: E402
from cfb_edge_finder.scripting import ADJUSTMENT_VERSION, METHODOLOGY_VERSION  # noqa: E402
from cfb_edge_finder.scripting.gamelog import read_jsonl, rows_fingerprint  # noqa: E402

SEASON_ROLES = {
    2021: "RETROSPECTIVE_VALIDATION",
    2022: "RETROSPECTIVE_VALIDATION",
    2023: "RETROSPECTIVE_VALIDATION",
    2024: "DEVELOPMENT",
    2025: "DEVELOPMENT",
    2026: "DISCOVERY_THEN_PROSPECTIVE (pregame generation rates only here)",
}


def _season_job(season: str, directory: str) -> dict[str, Any]:
    started = time.time()
    result = run_season(load_cfbd_season(Path(directory)))
    print(f"  {season}: {len(result['records'])} targets in {time.time() - started:.0f}s", file=sys.stderr)
    return result


def _espn_job(log: str, schedule: str) -> dict[str, Any]:
    rows = read_jsonl(Path(log))
    targets = []
    by_game = {}
    for r in rows:
        by_game.setdefault(r.game_id, {})[r.team_id] = r
    for event in json.loads(Path(schedule).read_text())["events"]:
        sides = {c["home_away"]: c for c in event.get("competitors") or []}
        if set(sides) != {"home", "away"} or not event.get("completed"):
            continue
        h, a = str(sides["home"]["team_id"]), str(sides["away"]["team_id"])
        game = by_game.get(str(event["id"])) or {}
        if h not in game or a not in game:
            continue
        targets.append(
            Target(
                game_id=str(event["id"]),
                season=game[h].season,
                week=game[h].week,
                season_type=None,
                kickoff_utc=game[h].kickoff_utc,
                home_id=h,
                away_id=a,
                home_name=game[h].team,
                away_name=game[a].team,
                neutral_site=bool(event.get("neutral_site")),
                conference_game=event.get("conference_game"),
                home_division=game[h].team_division,
                away_division=game[a].team_division,
            )
        )
    targets.sort(key=lambda t: (t.kickoff_utc, t.game_id))
    records = run_targets(rows, targets, reveal_outcomes=False)
    return {
        "records": records,
        "digests": {Path(p).name: file_digest(Path(p)) for p in (log, schedule)},
        "rows_fingerprint": rows_fingerprint(rows),
    }


def generation_profile(records: list[dict]) -> dict[str, Any]:
    """Pregame-only: what the engine generated (no outcome is read)."""
    elig = [r["pregame"] for r in records if r["pregame"]["eligibility"] is None]
    n = len(elig)
    findings = Counter(f["code"] for p in elig for f in p["findings"])
    explosive = sum(any("EXPLOSIVE" in f["code"] for f in p["findings"]) for p in elig)
    return {
        "eligible_games": n,
        "status": dict(Counter(p["status"] for p in elig)),
        "primary": dict(Counter(p["scripts"][0]["archetype"] for p in elig if p["scripts"]).most_common()),
        "any_role": dict(Counter(s["archetype"] for p in elig for s in p["scripts"]).most_common()),
        "confidence": dict(Counter(p["confidence"] for p in elig)),
        "games_with_any_explosive_finding": explosive,
        "explosive_upset_candidates": sum(
            any(c["archetype"] == "EXPLOSIVE_UPSET" for c in p["candidates"]) for p in elig
        ),
        "finding_rates": {k: round(v / n, 4) for k, v in sorted(findings.items())} if n else {},
    }


def analyse(records: list[dict]) -> dict[str, Any]:
    return {
        "taxonomy_quality": A.taxonomy_quality(records),
        "game_shapes": A.game_shape_summary(records),
        "identification": A.identification(records),
        "overall_scorecard": A.overall_scorecard(records),
        "confusion": A.confusion(records),
        "no_script": A.no_script(records),
        "ablation": A.ablation(records),
        "margins": A.margins(records),
        "scoring_environments": A.scoring_environments(records),
        "ambiguous_anatomy": A.ambiguous_anatomy(records),
    }


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cfbd", action="append", default=[], help="SEASON=DIR")
    ap.add_argument("--espn-pregame-only", nargs=2, metavar=("LOG", "SCHEDULE"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tables", type=Path)
    ap.add_argument("--starting-main-sha", default="d2befdcde97f3bc667589675644987ce963a1b8b")
    args = ap.parse_args()
    assert_research_output_path(args.out)

    jobs = {spec.split("=", 1)[0]: spec.split("=", 1)[1] for spec in args.cfbd}
    with ProcessPoolExecutor(max_workers=max(1, len(jobs) + 1)) as pool:
        futures = {name: pool.submit(_season_job, name, src) for name, src in jobs.items()}
        espn = pool.submit(_espn_job, *args.espn_pregame_only) if args.espn_pregame_only else None
        seasons = {name: futures[name].result() for name in sorted(futures)}
        espn_result = espn.result() if espn else None

    records = [r for name in sorted(seasons) for r in seasons[name]["records"]]
    report: dict[str, Any] = {
        "schema": "cfb_archetype_historical_report/1.0.0",
        "research_version": RESEARCH_VERSION,
        "methodology_version": METHODOLOGY_VERSION,
        "adjustment_version": ADJUSTMENT_VERSION,
        "protocol": PROTOCOL,
        "protocol_commit": PROTOCOL_COMMIT,
        "starting_main_sha": args.starting_main_sha,
        "code_sha_at_run": _git_sha(),
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "study_window": sorted(int(s) for s in seasons),
        "season_roles": {str(k): v for k, v in SEASON_ROLES.items()},
        "bootstrap": {"resamples": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED},
        "target": "actual final scores and box-score/play-log realized features; no market number is read",
        "production_changed": False,
        "probabilities_published": False,
        "coverage": {name: {**s["coverage"], "file_sha256": s["digests"]} for name, s in seasons.items()},
        "pooled": analyse(records),
        "blocks": {
            "RETROSPECTIVE_VALIDATION_2021_2023": analyse([r for r in records if r["pregame"]["season"] <= 2023]),
            "DEVELOPMENT_2024_2025": analyse([r for r in records if r["pregame"]["season"] >= 2024]),
        },
        "rolling_origin": A.rolling_origin(records),
        "exploratory_clusters": A.exploratory_clusters(records),
    }
    if espn_result:
        cfbd_2025_early = [
            r
            for r in seasons.get("2025", {"records": []})["records"]
            if (r["pregame"]["week"] or 99) <= 6 and r["pregame"]["season_type"] == "regular"
        ]
        report["source_sensitivity_2026_espn_pregame_only"] = {
            "note": "Pregame generation rates only. No 2026 outcome is revealed or scored.",
            "inputs": {"files_sha256": espn_result["digests"], "rows_fingerprint": espn_result["rows_fingerprint"]},
            "espn_2026": generation_profile(espn_result["records"]),
            "cfbd_2025_weeks_1_6_for_comparison": generation_profile(cfbd_2025_early),
        }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "archetype_historical_report.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    rows_dir = args.out / "archetype_rows"
    rows_dir.mkdir(parents=True, exist_ok=True)
    for name, s in seasons.items():
        with gzip.open(rows_dir / f"rows_{name}.jsonl.gz", "wt", encoding="utf-8") as fh:
            for r in s["records"]:
                fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
            for e in s["schedule_exclusions"]:
                fh.write(json.dumps({"excluded": e}, sort_keys=True, separators=(",", ":")) + "\n")
    if args.tables:
        from archetype_historical_tables import render  # noqa: PLC0415

        args.tables.write_text(render(report))
    print(json.dumps({k: v["eligible_with_prior_history"] for k, v in report["coverage"].items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
