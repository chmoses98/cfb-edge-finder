#!/usr/bin/env python3
"""Shadow comparison: V1 scripts vs V2 claims for every game of a built slate.

Reads the outputs of `script_engine.py build` (frozen V1 artifacts, frozen V2
claims artifacts and the SIFT payloads, which carry both market maps) and
reports, per game and in total:

  * V1 scripts and V2 claims side by side
  * winner-direction disagreements (V1 directional scripts vs the V2 CONTROL
    side), classified EXPECTED (a retired or unchanged V1 path that leans the
    other way by definition) or UNEXPECTED (investigate)
  * margin-expression differences (V1 hand-set bands vs the V2 historical range)
  * retired-label consequences, market-map differences and the SIFT headline change

No outcome is read: everything compared here is pregame.

    python scripts/script_engine_v2_shadow.py --live-dir data/scripting/live \
        --json-out <file.json> --md-out <file.md>
"""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter
from pathlib import Path
from typing import Any

DIRECTIONAL_V1 = {"HOME_CONTROL", "AWAY_CONTROL", "FAVORITE_PULLS_AWAY"}
#: V1 archetypes that lean AGAINST the efficiency side by their definition.
COUNTER_DIRECTION_V1 = {"UNDERDOG_HANGS_AROUND", "EXPLOSIVE_UPSET"}
RETIRED_V1 = {
    "FAVORITE_PULLS_AWAY",
    "UNDERDOG_HANGS_AROUND",
    "COMPETITIVE_TOSSUP",
    "COMPETITIVE_SHOOTOUT",
    "COMPETITIVE_GRIND",
    "PACE_DRIVEN_OVER",
    "TURNOVER_DISRUPTION",
}


def _gz(path: Path) -> Any:
    with gzip.open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def _lean(script: dict[str, Any]) -> str | None:
    lean = (script.get("outcome_shape") or {}).get("winner_lean")
    return lean.lower() if lean in ("HOME", "AWAY") else None


def compare_game(game_key: str, v1: dict[str, Any], v2: dict[str, Any], sift: dict[str, Any]) -> dict[str, Any]:
    content, claims = v1["content"], v2["content"]
    scripts = content["game_scripts"]["scripts"]
    codes = {f["code"] for f in content["matchup_findings"]}
    c = claims["claims"]
    control = c["control"]
    control_side = control["side"] if control else None

    disagreements = []
    for s in scripts:
        lean = _lean(s)
        if lean is None:
            continue
        if s["archetype"] in DIRECTIONAL_V1:
            if lean != control_side:
                disagreements.append(
                    {"script": s["archetype"], "v1_lean": lean, "v2_control": control_side, "class": "UNEXPECTED"}
                )
        elif s["archetype"] in COUNTER_DIRECTION_V1:
            if control_side is not None and lean != control_side:
                disagreements.append(
                    {
                        "script": s["archetype"],
                        "v1_lean": lean,
                        "v2_control": control_side,
                        "class": "EXPECTED_COUNTER_PATH",
                    }
                )
        elif s["archetype"] == "TURNOVER_DISRUPTION":
            if control_side is not None and lean != control_side:
                disagreements.append(
                    {
                        "script": s["archetype"],
                        "v1_lean": lean,
                        "v2_control": control_side,
                        "class": "EXPECTED_DISRUPTION_HAS_NO_WINNER_IN_V2",
                    }
                )
    v1_control = [s for s in scripts if s["archetype"] in ("HOME_CONTROL", "AWAY_CONTROL")]
    structural = []
    if control and not v1_control:
        structural.append("V2 CONTROL without a published V1 CONTROL script (V1 ranking/contradiction dropped it)")
    if v1_control and not control:
        structural.append("V1 CONTROL script without V2 CONTROL (UNEXPECTED)")
    if "BOTH_DEFENSES_CONTROL" in codes and not any(s["archetype"] == "DEFENSIVE_SUPPRESSION" for s in scripts):
        structural.append("DEFENSIVE_SUPPRESSION surfaces in V2; V1 did not publish it (GRIND exclusivity / ranking)")
    if c["closeness"] and not any(s["archetype"] == "COMPETITIVE_TOSSUP" for s in scripts):
        structural.append("CLOSENESS in V2 without a V1 TOSSUP script")
    if "EVEN_MATCHUP" in codes and not c["closeness"]:
        structural.append("EVEN_MATCHUP present but unresolved: no V2 CLOSENESS (as in V1)")
    for d in c["disruption"]:
        if not any(s["archetype"] == "TURNOVER_DISRUPTION" and s["lead_side"] == d["side"] for s in scripts):
            structural.append(
                f"DISRUPTION_EDGE {d['side']} surfaces without V1 TURNOVER_DISRUPTION (no volatility gate)"
            )

    margin = None
    if control:
        v1_bands = {
            s["archetype"]: s["outcome_shape"]["bands"].get("home_margin")
            for s in scripts
            if s["archetype"] in DIRECTIONAL_V1
        }
        rng = control["historical_range"] or {}
        margin = {
            "v1_home_margin_bands": v1_bands,
            "v2_tier": control["tier"],
            "v2_central_50_control_margin": rng.get("central_50"),
            "v2_central_80_control_margin": rng.get("central_80"),
            "v2_n": rng.get("n"),
        }

    v1_map = (sift or {}).get("script_market_map") or {}
    v1_active = Counter()
    for e in v1_map.get("expressions") or []:
        if e.get("authority") == "ACTIVE":
            v1_active["ACTIVE"] += 1
            if any(code in ("S", "P", "C") for code in e.get("compat") or ""):
                v1_active["ACTIVE_classified_S_P_C"] += 1
    v2_ma = ((sift or {}).get("claims_v2") or {}).get("market_authority") or {}
    return {
        "game_key": game_key,
        "teams": {k: v["name"] for k, v in content["teams"].items()},
        "kickoff_utc": content["kickoff_utc"],
        "data_quality": content["data_confidence"],
        "v1_status": content["game_scripts"]["status"],
        "v1_scripts": [
            f"{s['role']}:{s['archetype']}" + (f"({s['lead_side']})" if s["lead_side"] else "") for s in scripts
        ],
        "v2_status": claims["status"],
        "v2_claims": {
            "control": control["tier"] if control else None,
            "closeness": bool(c["closeness"]),
            "pace": c["pace"]["level"] if c["pace"] else None,
            "scoring_environment": c["scoring_environment"]["level"] if c["scoring_environment"] else None,
            "defensive_suppression": bool(c["defensive_suppression"]),
            "disruption": [f"{d['side']}:{d['strength']}" for d in c["disruption"]],
            "explosive_upset": [s["lead_side"] for s in c["explosive_upset"]["scripts"]],
        },
        "winner_direction": disagreements,
        "structural": structural,
        "margin": margin,
        "retired_v1_scripts": [s["archetype"] for s in scripts if s["archetype"] in RETIRED_V1],
        "market": {
            "v1": dict(v1_active),
            "v2_counts": v2_ma.get("counts"),
            "v2_moneyline_directions": len(v2_ma.get("moneyline") or []),
        },
        "sift": {"v1_headline": content["sift_read"]["headline"], "v2_headline": claims["story"]["headline"]},
    }


def compare(live: Path) -> dict[str, Any]:
    games = []
    skipped: Counter = Counter()
    for v2_path in sorted((live / "frozen_v2").glob("*.json.gz")):
        key = v2_path.name[: -len(".json.gz")]
        v1_path = live / "frozen" / v2_path.name
        if not v1_path.exists():
            skipped["no_v1_artifact"] += 1
            continue
        sift_path = live / "sift" / v2_path.name
        sift = _gz(sift_path) if sift_path.exists() else {}
        games.append(compare_game(key, _gz(v1_path), _gz(v2_path), sift))
    disagreements = Counter(d["class"] for g in games for d in g["winner_direction"])
    structural = Counter(s.split(" (")[0] if "UNEXPECTED" not in s else s for g in games for s in g["structural"])
    summary = {
        "games": len(games),
        "skipped": dict(skipped),
        "v1_status": dict(Counter(g["v1_status"] for g in games)),
        "v2_status": dict(Counter(g["v2_status"] for g in games)),
        "winner_direction": dict(disagreements),
        "unexpected_winner_direction": disagreements.get("UNEXPECTED", 0),
        "structural": dict(structural),
        "unexpected_structural": sum(1 for g in games for s in g["structural"] if "UNEXPECTED" in s),
        "retired_v1_scripts": dict(Counter(a for g in games for a in g["retired_v1_scripts"])),
        "v2_claims": {
            "control": dict(Counter(g["v2_claims"]["control"] for g in games if g["v2_claims"]["control"])),
            "closeness": sum(g["v2_claims"]["closeness"] for g in games),
            "pace": dict(Counter(g["v2_claims"]["pace"] for g in games if g["v2_claims"]["pace"])),
            "scoring_environment": dict(
                Counter(g["v2_claims"]["scoring_environment"] for g in games if g["v2_claims"]["scoring_environment"])
            ),
            "defensive_suppression": sum(g["v2_claims"]["defensive_suppression"] for g in games),
            "disruption": sum(len(g["v2_claims"]["disruption"]) for g in games),
            "explosive_upset": sum(len(g["v2_claims"]["explosive_upset"]) for g in games),
        },
        "market": {
            "v1_active_expressions": sum(g["market"]["v1"].get("ACTIVE", 0) for g in games),
            "v1_active_classified_S_P_C": sum(g["market"]["v1"].get("ACTIVE_classified_S_P_C", 0) for g in games),
            "v2_direct_moneyline": sum(g["market"]["v2_moneyline_directions"] for g in games),
            "v2_by_authority": dict(sum((Counter(g["market"]["v2_counts"] or {}) for g in games), Counter())),
        },
    }
    return {"summary": summary, "games": games}


def markdown(report: dict[str, Any], title: str) -> str:
    s = report["summary"]
    lines = [
        f"# {title}",
        "",
        "Pregame only: no outcome is read.",
        "",
        "## Summary",
        "",
        "```json",
        json.dumps(s, indent=1, sort_keys=True),
        "```",
        "",
        "## Games",
        "",
        "| Game | V1 scripts | V2 claims | Direction | V2 story |",
        "|---|---|---|---|---|",
    ]
    for g in report["games"]:
        v = g["v2_claims"]
        parts = [
            p
            for p in (
                v["control"],
                "CLOSENESS" if v["closeness"] else None,
                f"PACE_{v['pace']}" if v["pace"] else None,
                v["scoring_environment"],
                "DEF_SUPP" if v["defensive_suppression"] else None,
                *(f"DISRUPTION:{d}" for d in v["disruption"]),
                *(f"EXPLOSIVE_UPSET:{d}" for d in v["explosive_upset"]),
            )
            if p
        ]
        direction = "; ".join(f"{d['script']} {d['class']}" for d in g["winner_direction"]) or "agree"
        lines.append(
            f"| {g['game_key']} | {'<br>'.join(g['v1_scripts']) or '—'} | {', '.join(parts) or '—'} | "
            f"{direction} | {g['sift']['v2_headline']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-dir", type=Path, required=True)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--title", default="Script Engine V2 shadow comparison")
    args = parser.parse_args(argv)
    report = compare(args.live_dir)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(markdown(report, args.title), encoding="utf-8")
    print(json.dumps(report["summary"], indent=1, sort_keys=True))
    return 1 if report["summary"]["unexpected_winner_direction"] or report["summary"]["unexpected_structural"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
