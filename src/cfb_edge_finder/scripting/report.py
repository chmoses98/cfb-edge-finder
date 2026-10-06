"""The prospective script-engine report: did the frozen scripts describe the games? PURE.

Built ONLY from ledger rows (recorded before kickoff) joined to realized
records (computed after the game). Nothing is backfilled: a game with no
pregame ledger row does not exist for this report.

Two separate scorecards:

SCRIPT ACCURACY (from the FINAL_PREGAME row when present, else PUBLICATION)
  primary_described, primary_or_secondary_described, any_script_described,
  by PRIMARY archetype and by data-confidence tier, plus finding-direction
  accuracy by finding code.

EXPRESSION PERFORMANCE (research units: one contract at the recorded entry)
  For each label group -- MULTI_SCRIPT, single-script (SCRIPT_DEPENDENT),
  AGGRESSIVE, CONTRADICTED, BEST_EXPRESSION -- and by confidence tier:
  count, win rate, and profit per contract at the recorded cost. This is
  bookkeeping, not a claim of edge: no stake is implied and the sample says
  how little it can support.

*** WHAT THIS REPORT IS FOR ***
Script probabilities may only be published once enough PROSPECTIVE games
exist to calibrate them; `calibration_readiness` states how far away that is.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

#: Games needed per archetype before a frequency is reported as anything
#: other than "insufficient sample".
MIN_SAMPLE = 30

GROUPS = (
    ("MULTI_SCRIPT", lambda labels: "MULTI_SCRIPT" in labels),
    ("BEST_EXPRESSION", lambda labels: "BEST_EXPRESSION" in labels),
    ("SINGLE_SCRIPT", lambda labels: "SCRIPT_DEPENDENT" in labels),
    ("AGGRESSIVE", lambda labels: "AGGRESSIVE" in labels),
    ("CONTRADICTED", lambda labels: "CONTRADICTED" in labels),
)


def _rate(hits: int, n: int) -> float | None:
    return round(hits / n, 4) if n else None


def _pick_rows(realized: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """One realized record per game: FINAL_PREGAME preferred, else PUBLICATION."""
    chosen: dict[str, dict[str, Any]] = {}
    for rec in realized:
        current = chosen.get(rec["game_key"])
        if current is None or (rec["kind"] == "FINAL_PREGAME" and current["kind"] != "FINAL_PREGAME"):
            chosen[rec["game_key"]] = rec
    return chosen


def build_report(ledger_rows: list[dict[str, Any]], realized: list[dict[str, Any]]) -> dict[str, Any]:
    by_hash = {(r["artifact_hash"], r["kind"]): r for r in ledger_rows}
    games = _pick_rows(realized)

    accuracy: dict[str, Any] = {"games": len(games)}
    for key in ("primary_described", "primary_or_secondary_described", "any_script_described"):
        accuracy[key] = _rate(sum(1 for g in games.values() if g[key]), len(games))

    by_arch: dict[str, list[bool]] = defaultdict(list)
    by_tier: dict[str, list[bool]] = defaultdict(list)
    realized_arch: dict[str, int] = defaultdict(int)
    finding_acc: dict[str, list[bool]] = defaultdict(list)
    for game in games.values():
        row = by_hash.get((game["artifact_hash"], game["kind"])) or {}
        primary = next((s for s in game["scripts"] if s["role"] == "PRIMARY"), None)
        if primary is not None:
            by_arch[primary["archetype"]].append(game["primary_described"])
        by_tier[row.get("data_confidence") or "UNKNOWN"].append(game["primary_described"])
        realized_arch[game["realized_archetype"]["primary"]] += 1
        for f in game["findings"]:
            if f["correct"] is not None:
                finding_acc[f["code"]].append(f["correct"])

    def table(groups: dict[str, list[bool]]) -> dict[str, Any]:
        return {
            k: {
                "games": len(v),
                "described": _rate(sum(v), len(v)),
                "sample": "ok" if len(v) >= MIN_SAMPLE else "insufficient",
            }
            for k, v in sorted(groups.items())
        }

    perf: dict[str, Any] = {}
    for name, test in GROUPS:
        for tier in ("ALL", "HIGH", "MEDIUM", "LOW"):
            n = wins = 0
            profit = 0.0
            for game in games.values():
                row = by_hash.get((game["artifact_hash"], game["kind"])) or {}
                if tier != "ALL" and row.get("data_confidence") != tier:
                    continue
                for e in game.get("expressions") or []:
                    if e.get("won") is None or e.get("cost_per_contract") is None or not test(e["labels"]):
                        continue
                    n += 1
                    wins += bool(e["won"])
                    profit += (1.0 if e["won"] else 0.0) - float(e["cost_per_contract"])
            perf.setdefault(name, {})[tier] = {
                "expressions": n,
                "win_rate": _rate(wins, n),
                "profit_per_contract": round(profit / n, 4) if n else None,
                "sample": "ok" if n >= MIN_SAMPLE else "insufficient",
            }

    publications = {r["game_key"] for r in ledger_rows}
    smallest = min((len(v) for v in by_arch.values()), default=0)
    return {
        "schema_version": "cfb_script_engine_report/1.0.0",
        "publications": {"rows": len(ledger_rows), "games": len(publications)},
        "settled": {"games": len(games)},
        "script_accuracy": {
            **accuracy,
            "by_primary_archetype": table(by_arch),
            "by_data_confidence": table(by_tier),
            "realized_archetypes": dict(sorted(realized_arch.items())),
        },
        "finding_direction_accuracy": table(finding_acc),
        "expression_performance": perf,
        "calibration_readiness": {
            "min_games_per_archetype_required": MIN_SAMPLE,
            "smallest_archetype_sample": smallest,
            "ready": bool(by_arch) and smallest >= MIN_SAMPLE,
            "note": (
                "Script probabilities stay null until every published archetype has at least the required number "
                "of prospectively frozen, settled games, and a calibration method is validated out of sample."
            ),
        },
        "not_a_claim_of_edge": (
            "Expression performance is research-unit bookkeeping at recorded entry prices. It is not evidence of "
            "an edge until the sample supports it, and no stake is implied."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    acc = report["script_accuracy"]
    lines = [
        "# CFB Script Engine — prospective report",
        "",
        f"Publications: {report['publications']['games']} game(s), {report['publications']['rows']} ledger row(s). "
        f"Settled: {report['settled']['games']}.",
        "",
        "## Script accuracy",
        "",
        f"- PRIMARY described the game: {acc.get('primary_described')}",
        f"- PRIMARY or SECONDARY: {acc.get('primary_or_secondary_described')}",
        f"- Any published script: {acc.get('any_script_described')}",
        "",
        "| PRIMARY archetype | games | described | sample |",
        "|---|---|---|---|",
    ]
    for arch, row in acc["by_primary_archetype"].items():
        lines.append(f"| {arch} | {row['games']} | {row['described']} | {row['sample']} |")
    lines += [
        "",
        "## Expression performance (research units)",
        "",
        "| group | tier | n | win rate | P/L per contract |",
    ]
    lines.append("|---|---|---|---|---|")
    for group, tiers in report["expression_performance"].items():
        for tier, row in tiers.items():
            lines.append(
                f"| {group} | {tier} | {row['expressions']} | {row['win_rate']} | {row['profit_per_contract']} |"
            )
    ready = report["calibration_readiness"]
    lines += [
        "",
        f"Calibration ready: **{ready['ready']}** (smallest archetype sample {ready['smallest_archetype_sample']} "
        f"of {ready['min_games_per_archetype_required']} required).",
        "",
        report["not_a_claim_of_edge"],
        "",
    ]
    return "\n".join(lines)
