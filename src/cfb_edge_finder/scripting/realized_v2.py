"""V2 claims settlement: what happened to each frozen FINAL_PREGAME claim. PURE.

Reads V2 ledger FINAL_PREGAME rows exactly as written (and, for the CONTROL range and the environment
references, the frozen artifacts those rows name by hash) and joins the final game. A claim is NEVER rebuilt:
the row's compact claims and the stored claims artifact are the only pregame inputs.

Per claim, only semantics that already exist in this repository are used:

* CONTROL       -- did the CONTROL side win; its realized margin; did the margin land inside the frozen
                  historical central-50 / central-80 range carried by the claim (coverage of a historical
                  empirical range, not a forecast check).
* CLOSENESS     -- absolute margin and the <=3 / <=7 / <=8 (one-score, `realized.ONE_SCORE`) cuts.
* PACE / SCORING / DEFENSIVE SUPPRESSION -- the realized environment descriptors of the pre-registered
                  historical study (restated from `archetype_research.taxonomy.environment_labels`: PACE_DRIVEN_OVER,
                  DEFENSIVE_SUPPRESSION) and the production finding-direction rule for possession volume
                  (`realized.score_findings`: total plays vs twice the league plays-per-game norm), against the
                  frozen V1 artifact's own league norms and scoring baseline.
* DISRUPTION    -- the study's mechanism (sacks + TFL made vs allowed) and whether the side won.

Anything whose inputs are missing is recorded as {"status": "UNAVAILABLE", "reason": ...}, never guessed.
Rows are append-only and keyed by (game_key, claims_artifact_hash, kind): settling twice writes nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from cfb_edge_finder.scripting.gamelog import TeamGame
from cfb_edge_finder.scripting.realized import ONE_SCORE, realized_features

REALIZED_V2_SCHEMA = "cfb_script_realized_v2/1.0.0"
METHODOLOGY = "cfb-script-engine/2.0.0"


def environment_labels(features: dict[str, Any], pre: dict[str, Any]) -> list[str]:
    """PACE_DRIVEN_OVER / DEFENSIVE_SUPPRESSION realized descriptors, the rule of the pre-registered historical study
    (`archetype_research.taxonomy.environment_labels`, docs/ARCHETYPE_HISTORICAL_PROTOCOL.md section 4), restated
    here because production may not import the research package; tests pin the two to agree."""
    out = []
    norms, base_total = pre["league_norms"], pre["baseline"]["total_points"]
    total = features["total_points"]
    plays_mu = norms.get("plays_per_game")
    if plays_mu and base_total is not None and features["total_plays"] > 2 * plays_mu and total > base_total:
        out.append("PACE_DRIVEN_OVER")
    basis = features["efficiency_basis"]
    mu = norms.get(basis)
    h, a = features["home"].get(basis), features["away"].get(basis)
    if mu is not None and h is not None and a is not None and base_total is not None:
        if h < mu and a < mu and total < base_total:
            out.append("DEFENSIVE_SUPPRESSION")
    return out


def unavailable(reason: str) -> dict[str, Any]:
    return {"status": "UNAVAILABLE", "reason": reason}


def key_of(row: dict[str, Any]) -> tuple[str, str, str]:
    return row["game_key"], row["claims_artifact_hash"], row["kind"]


def _in(band: list[float] | None, value: float) -> bool | None:
    return None if not band else band[0] <= value <= band[1]


def settle_control(compact: dict[str, Any] | None, claims_artifact: dict[str, Any] | None, f: dict[str, Any]):
    if not compact:
        return None
    side = compact["side"]
    margin = f["home_margin"] if side == "home" else -f["home_margin"]
    rng = ((((claims_artifact or {}).get("content") or {}).get("claims") or {}).get("control") or {}).get(
        "historical_range"
    )
    out: dict[str, Any] = {
        "side": side,
        "strength": compact["strength"],
        "tier": compact["tier"],
        "control_margin": margin,
        "won": margin > 0 if margin != 0 else None,
    }
    if rng is None:
        out["range"] = unavailable("claims artifact or its historical range not found in the ledger")
    else:
        out["range"] = {
            "median": rng["median"],
            "central_50": rng["central_50"],
            "central_80": rng["central_80"],
            "in_central_50": _in(rng["central_50"], margin),
            "in_central_80": _in(rng["central_80"], margin),
            "calibration_sha256": rng.get("calibration_sha256"),
        }
    return out


def settle_closeness(present: bool, f: dict[str, Any]):
    if not present:
        return None
    m = abs(f["home_margin"])
    return {"abs_margin": m, "within_3": m <= 3, "within_7": m <= 7, "within_one_score_8": m <= ONE_SCORE}


def _norms(v1_artifact: dict[str, Any] | None) -> dict[str, Any] | None:
    if v1_artifact is None:
        return None
    profile = v1_artifact["content"]["matchup_profile"]
    baselines = profile["adjustment"]["league_baselines"]
    return {
        "league_norms": {k: (v or {}).get("mu") for k, v in baselines.items()},
        "baseline": {"total_points": (profile.get("scoring_baseline") or {}).get("total_points")},
    }


def settle_environment(compact: dict[str, Any], v1_artifact: dict[str, Any] | None, f: dict[str, Any]):
    pre = _norms(v1_artifact)
    pace, scoring, defense = (
        compact.get("pace"),
        compact.get("scoring_environment"),
        compact.get("defensive_suppression"),
    )
    if pre is None:
        missing = unavailable("frozen V1 football artifact not found in the ledger (league norms, scoring baseline)")
        return (
            None if pace is None else {"level": pace, **missing},
            None if scoring is None else {"level": scoring["level"], **missing},
            None if not defense else missing,
        )
    labels = environment_labels(f, pre)
    plays_mu = pre["league_norms"].get("plays_per_game")
    base_total = pre["baseline"]["total_points"]
    pace_out = None
    if pace is not None:
        if not plays_mu:
            pace_out = {"level": pace, **unavailable("league plays-per-game norm missing")}
        else:
            high = f["total_plays"] > 2 * plays_mu
            pace_out = {
                "level": pace,
                "realized_total_plays": f["total_plays"],
                "league_plays_per_game": round(plays_mu, 3),
                "direction_held": high if pace == "HIGH" else not high,
                "pace_driven_over": "PACE_DRIVEN_OVER" in labels,
                "semantics": "realized.score_findings possession rule; taxonomy.environment_labels",
            }
    scoring_out = None
    if scoring is not None:
        if base_total is None:
            scoring_out = {"level": scoring["level"], **unavailable("frozen scoring baseline missing")}
        else:
            total = f["total_points"]
            scoring_out = {
                "level": scoring["level"],
                "realized_total_points": total,
                "baseline_total_points": base_total,
                "realized_vs_baseline": "ABOVE" if total > base_total else "BELOW" if total < base_total else "EQUAL",
                "pace_driven_over": "PACE_DRIVEN_OVER" in labels,
                "defensive_suppression_label": "DEFENSIVE_SUPPRESSION" in labels,
                "semantics": "descriptive: realized total vs the frozen opponent-adjusted scoring baseline",
            }
    defense_out = None
    if defense:
        basis = f["efficiency_basis"]
        if pre["league_norms"].get(basis) is None or base_total is None:
            defense_out = unavailable(f"league norm for {basis} or scoring baseline missing")
        else:
            defense_out = {
                "realized_label": "DEFENSIVE_SUPPRESSION" in labels,
                "efficiency_basis": basis,
                "semantics": "taxonomy.environment_labels DEFENSIVE_SUPPRESSION",
            }
    return pace_out, scoring_out, defense_out


def settle_disruption(items: list[dict[str, Any]], home: TeamGame, away: TeamGame, f: dict[str, Any]):
    out = []
    made = {
        "home": (home.box.get("def_sacks") or 0) + (home.box.get("def_tfl") or 0),
        "away": (away.box.get("def_sacks") or 0) + (away.box.get("def_tfl") or 0),
    }
    have = home.box.get("def_sacks") is not None and away.box.get("def_sacks") is not None
    for d in items:
        side = d["side"]
        other = "away" if side == "home" else "home"
        margin = f["home_margin"] if side == "home" else -f["home_margin"]
        out.append(
            {
                "side": side,
                "aligned_with_control": d.get("aligned_with_control"),
                "side_won": margin > 0 if margin else None,
                "mechanism_held": (made[side] > made[other]) if have else None,
                "mechanism": "sacks + tackles for loss made vs the opponent's",
            }
        )
    return out


def realized_row(
    row: dict[str, Any],
    home: TeamGame,
    away: TeamGame,
    claims_artifact: dict[str, Any] | None,
    v1_artifact: dict[str, Any] | None,
) -> dict[str, Any]:
    """One settled V2 FINAL_PREGAME row. `home`/`away` are the ESPN home and away team rows of the game."""
    f = realized_features(home, away)
    compact = row.get("claims") or {}
    pace, scoring, defense = settle_environment(compact, v1_artifact, f)
    return {
        "schema_version": REALIZED_V2_SCHEMA,
        "methodology_version": row["methodology_version"],
        "kind": row["kind"],
        "game_key": row["game_key"],
        "event_id": row["event_id"],
        "kickoff_utc": row["kickoff_utc"],
        "recorded_at": row["recorded_at"],
        "claims_artifact_hash": row["claims_artifact_hash"],
        "football_artifact_hash": row.get("football_artifact_hash"),
        "status": row.get("status"),
        "result": {
            "home_points": f["home_points"],
            "away_points": f["away_points"],
            "home_margin": f["home_margin"],
            "total_points": f["total_points"],
            "total_plays": f["total_plays"],
        },
        "control": settle_control(compact.get("control"), claims_artifact, f),
        "closeness": settle_closeness(bool(compact.get("closeness")), f),
        "pace": pace,
        "scoring_environment": scoring,
        "defensive_suppression": defense,
        "disruption": settle_disruption(compact.get("disruption") or [], home, away, f),
        "explosive_upset": None
        if not compact.get("explosive_upset")
        else unavailable("V1 rule, untested historically (explosive-play counts unavailable)"),
    }


def settle_rows(
    ledger_rows: Iterable[dict[str, Any]],
    done: set[tuple[str, str, str]],
    team_rows: list[TeamGame],
    load_claims: Any,
    load_v1: Any,
) -> list[dict[str, Any]]:
    """New realized rows for FINAL_PREGAME V2 rows whose game is complete in the log and not yet settled."""
    by_game: dict[str, dict[str, TeamGame]] = {}
    for r in team_rows:
        by_game.setdefault(r.game_id, {})[r.team_id] = r
    out = []
    for row in ledger_rows:
        if row.get("kind") != "FINAL_PREGAME" or row.get("methodology_version") != METHODOLOGY:
            continue
        if key_of(row) in done:
            continue
        pair = by_game.get(str(row["event_id"])) or {}
        home = pair.get(str(row["teams"]["home"]["team_id"]))
        away = pair.get(str(row["teams"]["away"]["team_id"]))
        if home is None or away is None or home.points_for is None or away.points_for is None:
            continue
        out.append(
            realized_row(
                row, home, away, load_claims(row["claims_artifact_hash"]), load_v1(row.get("football_artifact_hash"))
            )
        )
        done.add(key_of(row))
    return out


def _rate(hits: int, n: int) -> float | None:
    return round(hits / n, 4) if n else None


def build_report_v2(ledger_rows: list[dict[str, Any]], realized: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate over the LAST settled FINAL_PREGAME row of each game."""
    last: dict[str, dict[str, Any]] = {}
    for r in realized:
        if r["kind"] != "FINAL_PREGAME":
            continue
        cur = last.get(r["game_key"])
        if cur is None or r["recorded_at"] > cur["recorded_at"]:
            last[r["game_key"]] = r
    rows = list(last.values())
    control: dict[str, dict[str, Any]] = {}
    for r in rows:
        c = r["control"]
        if not c:
            continue
        b = control.setdefault(c["tier"], {"n": 0, "wins": 0, "range_n": 0, "in_central_50": 0, "in_central_80": 0})
        b["n"] += 1
        b["wins"] += 1 if c["won"] else 0
        rng = c["range"]
        if rng.get("status") != "UNAVAILABLE":
            b["range_n"] += 1
            b["in_central_50"] += 1 if rng["in_central_50"] else 0
            b["in_central_80"] += 1 if rng["in_central_80"] else 0
    for b in control.values():
        b["win_rate"] = _rate(b["wins"], b["n"])
        b["central_50_coverage"] = _rate(b["in_central_50"], b["range_n"])
        b["central_80_coverage"] = _rate(b["in_central_80"], b["range_n"])
    close = [r["closeness"] for r in rows if r["closeness"]]
    pace = [r["pace"] for r in rows if r["pace"] and r["pace"].get("status") != "UNAVAILABLE"]
    defense = [
        r["defensive_suppression"]
        for r in rows
        if r["defensive_suppression"] and r["defensive_suppression"].get("status") != "UNAVAILABLE"
    ]
    final = {(r["game_key"]) for r in ledger_rows if r.get("kind") == "FINAL_PREGAME"}
    return {
        "schema": "cfb_script_engine_v2_report/1.0.0",
        "methodology_version": METHODOLOGY,
        "final_pregame_games": len(final),
        "settled_games": len(rows),
        "unavailable_is_explicit": True,
        "control": dict(sorted(control.items())),
        "closeness": {
            "n": len(close),
            "within_3": _rate(sum(c["within_3"] for c in close), len(close)),
            "within_7": _rate(sum(c["within_7"] for c in close), len(close)),
            "within_one_score_8": _rate(sum(c["within_one_score_8"] for c in close), len(close)),
        },
        "pace": {
            level: {
                "n": len(ps := [p for p in pace if p["level"] == level]),
                "direction_held": _rate(sum(p["direction_held"] for p in ps), len(ps)),
            }
            for level in ("HIGH", "LOW")
        },
        "defensive_suppression": {
            "n": len(defense),
            "realized_label": _rate(sum(d["realized_label"] for d in defense), len(defense)),
        },
        "unavailable_counts": {
            "pace": sum(1 for r in rows if r["pace"] and r["pace"].get("status") == "UNAVAILABLE"),
            "scoring_environment": sum(
                1 for r in rows if r["scoring_environment"] and r["scoring_environment"].get("status") == "UNAVAILABLE"
            ),
            "defensive_suppression": sum(
                1
                for r in rows
                if r["defensive_suppression"] and r["defensive_suppression"].get("status") == "UNAVAILABLE"
            ),
        },
    }


def render_markdown_v2(report: dict[str, Any]) -> str:
    lines = [
        "# CFB Script Engine V2 — prospective claim settlement",
        "",
        f"Methodology `{report['methodology_version']}`. FINAL_PREGAME games: {report['final_pregame_games']}; "
        f"settled: {report['settled_games']}. Frozen claims only; nothing re-derived. Historical counts are "
        "frequencies of past games, never chances.",
        "",
        "## CONTROL",
        "",
        "| Tier | n | Won | Central-50 coverage | Central-80 coverage |",
        "|---|---|---|---|---|",
    ]
    for tier, b in report["control"].items():
        lines.append(
            f"| {tier} | {b['n']} | {b['wins']} | {b['in_central_50']}/{b['range_n']} "
            f"| {b['in_central_80']}/{b['range_n']} |"
        )
    if not report["control"]:
        lines.append("| — | 0 | — | — | — |")
    c = report["closeness"]
    lines += [
        "",
        "## CLOSENESS",
        "",
        f"n = {c['n']}; |margin| <= 3: {c['within_3']}; <= 7: {c['within_7']}; <= 8: {c['within_one_score_8']}",
        "",
        "## Environment",
        "",
    ]
    for level, p in report["pace"].items():
        lines.append(f"* PACE {level}: n = {p['n']}, direction held {p['direction_held']}")
    d = report["defensive_suppression"]
    lines.append(f"* DEFENSIVE SUPPRESSION: n = {d['n']}, realized label {d['realized_label']}")
    lines.append(f"* UNAVAILABLE (explicit): {report['unavailable_counts']}")
    return "\n".join(lines) + "\n"
