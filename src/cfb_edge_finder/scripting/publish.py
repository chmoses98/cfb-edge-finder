"""The compact payload SIFT reads, from a frozen envelope + market map. PURE.

Published under `event_research.extensions.script_engine` by the explorer
export: the contract's `extensions` object is the backward-compatible place
for sport-specific research (other sports' documents stay valid untouched).

Compaction never drops a REQUIRED metric field. Per-metric fields that are
the same for every metric of a team (season, window) are stated once on the
team; registry text (names, descriptions) is stated once in
`metric_registry`; finding evidence is carried by reference (`metric_refs`
into the profile) instead of being copied. Prices are NOT in this payload:
SIFT joins each expression to its live or published quote by ticker, so a
price move never rewrites the football research.
"""

from __future__ import annotations

from typing import Any

from cfb_edge_finder.scripting import METHODOLOGY_VERSION
from cfb_edge_finder.scripting.market_map import COMPAT_CODES
from cfb_edge_finder.scripting.metrics import METRICS

PAYLOAD_VERSION = "cfb_script_engine_payload/1.1.0"

#: Every required per-metric field, as table columns. Repeated strings
#: (sources, observation times, unavailability reasons) are interned into
#: `legend` and referenced by index, so the table stays small without
#: dropping a field.
METRIC_COLUMNS = (
    "raw",
    "adjusted",
    "adjusted_available",
    "adjusted_unavailable_reason",
    "standard_error",
    "rank",
    "universe_size",
    "rank_basis",
    "direction",
    "games",
    "effective_n",
    "prior_weight",
    "source",
    "observed_at",
    "quality",
)
_INTERNED = ("adjusted_unavailable_reason", "source", "observed_at")


def _team(team: dict[str, Any], legend: dict[str, list[str]]) -> dict[str, Any]:
    metrics = team.get("metrics") or {}
    first = next(iter(metrics.values()), {}) if metrics else {}
    table: dict[str, list[Any]] = {}
    for key, m in sorted(metrics.items()):
        flat = {**m, **(m.get("sample") or {})}
        row = []
        for col in METRIC_COLUMNS:
            value = flat.get(col)
            if col in _INTERNED and value is not None:
                bucket = legend.setdefault(col, [])
                if value not in bucket:
                    bucket.append(value)
                value = bucket.index(value)
            row.append(value)
        table[key] = row
    return {
        "team_id": team["team_id"],
        "name": team["name"],
        "division": team["division"],
        "games_observed": team["games_observed"],
        "games_with_play_log": team["games_with_play_log"],
        "record_in_window": team["record_in_window"],
        "quarterback": team["quarterback"],
        "residual_sd": team["residual_sd"],
        "season": first.get("season"),
        "window": first.get("window"),
        "metrics": table,
    }


def _finding(f: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in f.items() if k != "evidence"}


SURVIVAL_COLUMNS = (
    "supported",
    "partial",
    "contradicted",
    "neutral",
    "total_scripts",
    "meaningful_scripts",
    "weighted_score",
    "research_uncalibrated",
)


def _expression(e: dict[str, Any]) -> dict[str, Any]:
    """One mapped contract side. Its quote, family, line and settlement text
    live on the published market row with the same ticker; only what the
    script mapping adds is carried here."""
    out = {
        "ticker": e["ticker"],
        "side": e["side"],
        "wins_when": (e.get("wins_when") or {}).get("description"),
        "thesis": e["thesis"],
        "compat": "".join(COMPAT_CODES[c["status"]] for c in e["compatibility"]),
        "coverage": [c["coverage"] for c in e["compatibility"]],
        "survival": [e["script_survival"][k] for k in SURVIVAL_COLUMNS],
        "labels": e.get("labels") or [],
        "authority": e.get("market_authority"),
    }
    if any(c.get("research") for c in e["compatibility"]):
        # What each uncalibrated scoring band would have said, per script
        # ("-" where the script's status is not RESEARCH_UNCALIBRATED).
        # Research context only: it is never scored and never a label.
        out["research"] = "".join(
            COMPAT_CODES[c["research"]["band_relation"]] if c.get("research") else "-" for c in e["compatibility"]
        )
    if e.get("correlation"):
        out["correlation"] = [
            [c["with"], c["relation"], c.get("both_can_cash"), c.get("both_lose_when")] for c in e["correlation"]
        ]
    return out


RUNG_COLUMNS = (
    "expression_id",
    "is_core",
    "relation_to_core",
    "additional_requirement_points",
    "cashes_when_core_fails",
    "scripts_supported",
    "scripts_lost_vs_core_ranks",
)


def _thesis(t: dict[str, Any], rank_of: dict[str, int]) -> dict[str, Any]:
    return {
        "thesis": t["thesis"],
        "core_expression": t["core_expression"],
        "rungs": [
            [
                r["expression_id"],
                r["is_core"],
                r["relation_to_core"],
                r["additional_requirement_points"],
                r["cashes_when_core_fails"],
                r["scripts_supported"],
                sorted(rank_of.get(sid, 0) for sid in r["scripts_lost_vs_core"]),
            ]
            for r in t["ladder"]
        ],
    }


def sift_payload(record: dict[str, Any]) -> dict[str, Any]:
    """`record` = {"football": envelope, "market_map": annotated map | None, ...}."""
    envelope = record.get("football")
    if envelope is None:
        return {
            "version": PAYLOAD_VERSION,
            "status": record.get("status") or "UNAVAILABLE",
            "reason": record.get("reason"),
            "methodology_version": METHODOLOGY_VERSION,
        }
    content = envelope["content"]
    mm = record.get("market_map") or {}
    profile = content["matchup_profile"]
    legend: dict[str, list[str]] = {}
    teams = {side: _team(profile["teams"][side], legend) for side in ("home", "away")}
    has_scripts = bool(content["game_scripts"]["scripts"])
    rank_of = {sc["script_id"]: sc["rank"] for sc in content["game_scripts"]["scripts"]}
    expressions = [e for e in mm.get("expressions") or [] if e.get("unmappable_reason") is None and has_scripts]
    unmappable: dict[str, int] = {}
    for e in mm.get("expressions") or []:
        if e.get("unmappable_reason") is not None:
            key = f"{e['family']}"
            unmappable[key] = unmappable.get(key, 0) + 1
    return {
        "version": PAYLOAD_VERSION,
        "status": content["game_scripts"]["status"],
        "script_generation": {
            "generated_at": envelope["generated_at"],
            "football_data_cutoff": content["football_data_cutoff"],
            "market_blind": content["market_blind"],
            "artifact_hash": envelope["artifact_hash"],
            "data_confidence": content["data_confidence"],
            "methodology_version": content["methodology_version"],
            "adjustment_version": content["adjustment_version"],
            "regeneration_reasons": envelope["regeneration"]["reasons"],
            "mapped_at": mm.get("mapped_at"),
            "prices_captured_at": record.get("prices_captured_at"),
            "event_id_football": content["event_id"],
            "identity": content["confidence"]["identity"],
        },
        "sift_read": content["sift_read"],
        "data_confidence": {
            k: content["confidence"][k]
            for k in (
                "level",
                "describes",
                "gates",
                "all_gates_pass",
                "reasons",
                "known",
                "unknown",
                "core_coverage",
                "enhanced_dimensions",
                "games_observed",
                "adjustment_stable",
                "availability_status",
                "freshness",
            )
        },
        "matchup_profile": {
            "metric_columns": list(METRIC_COLUMNS),
            "legend": legend,
            "teams": teams,
            "dimensions": profile["dimensions"],
            "scoring_baseline": profile["scoring_baseline"],
            "adjustment": {
                k: profile["adjustment"][k]
                for k in (
                    "cutoff_exclusive",
                    "config",
                    "stable",
                    "fbs_schedule_components",
                    "median_fbs_games",
                    "games_in_window",
                )
            },
        },
        "matchup_findings": [_finding(f) for f in content["matchup_findings"]],
        "game_scripts": content["game_scripts"]["scripts"],
        "band_policy": content["game_scripts"].get("band_policy"),
        "script_market_map": {
            "scripts": mm.get("scripts") or [],
            "compat_codes": {v: k for k, v in COMPAT_CODES.items()},
            "survival_columns": list(SURVIVAL_COLUMNS),
            "correlation_columns": ["with", "relation", "both_can_cash", "both_lose_when"],
            "expressions": [_expression(e) for e in expressions],
            "unmappable_by_family": dict(sorted(unmappable.items())),
            "coverage": mm.get("coverage"),
            "research_only": True,
            "pricing_note": (
                "No validated CFB pricing source exists. Script survival describes compatibility with frozen "
                "football scripts; it is not a probability and not an expected value."
            ),
        },
        "script_survivors": [s["expression_id"] for s in mm.get("survivors") or []],
        "rung_columns": list(RUNG_COLUMNS),
        "theses": [_thesis(t, rank_of) for t in mm.get("theses") or []],
        "market_disagreement": mm.get("market_disagreement"),
        "metric_registry": {m.metric_id: m.describe() for m in METRICS},
    }
