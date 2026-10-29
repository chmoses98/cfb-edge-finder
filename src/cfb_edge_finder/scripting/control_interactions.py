"""Prospective CONTROL interaction diagnostics over already-frozen V2 claims.

This is a descriptive football report, NOT a signal discovery result, fair-price
model, staking rule or claim of market edge. The caller passes one realized
FINAL_PREGAME record per game, already deduplicated by build_report_v2. All
moderators were present in the pregame V2 claims and are therefore observable
without reading final-game outcomes to select the cohorts.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

MIN_DESCRIPTIVE_SAMPLE = 30
SCHEMA = "cfb_control_interaction_diagnostics/1.0.0"


def _rate(wins: int, decided: int) -> float | None:
    return round(wins / decided, 4) if decided else None


def _labels(row: dict[str, Any], side: str) -> dict[str, str]:
    pace = row.get("pace")
    scoring = row.get("scoring_environment")
    suppression = row.get("defensive_suppression")
    disruption = row.get("disruption") or []

    aligned = any(d.get("side") == side for d in disruption)
    opposing = any(d.get("side") in ("home", "away") and d.get("side") != side for d in disruption)
    if aligned and opposing:
        disruption_state = "BOTH"
    elif aligned:
        disruption_state = "ALIGNED_ONLY"
    elif opposing:
        disruption_state = "OPPOSING_ONLY"
    else:
        disruption_state = "NONE"

    def state(value: dict[str, Any] | None, key: str) -> str:
        if value is None:
            return "NO_CLAIM"
        if value.get("status") == "UNAVAILABLE":
            return "UNAVAILABLE"
        return str(value.get(key) or "UNAVAILABLE")

    return {
        "pace": state(pace, "level"),
        "scoring_environment": state(scoring, "level"),
        "defensive_suppression": (
            "NO_CLAIM"
            if suppression is None
            else "UNAVAILABLE"
            if suppression.get("status") == "UNAVAILABLE"
            else "CLAIM_PRESENT"
        ),
        "disruption_alignment": disruption_state,
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decided = [r for r in rows if isinstance((r.get("control") or {}).get("won"), bool)]
    wins = sum(1 for r in decided if r["control"]["won"])
    margins = [
        r["control"]["control_margin"]
        for r in rows
        if isinstance((r.get("control") or {}).get("control_margin"), (int, float))
        and not isinstance(r["control"]["control_margin"], bool)
    ]
    n = len(rows)
    return {
        "n": n,
        "decided": len(decided),
        "wins": wins,
        "losses": len(decided) - wins,
        "undecided": n - len(decided),
        "win_rate": _rate(wins, len(decided)),
        "mean_control_margin": round(sum(margins) / len(margins), 3) if margins else None,
        "sample": "DESCRIPTIVE" if len(decided) >= MIN_DESCRIPTIVE_SAMPLE else "INSUFFICIENT",
    }


def build_control_interaction_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cross-tab V2 CONTROL tiers against four *pregame* claim dimensions.

    The same games appear in different moderator tables, so table cells must
    not be pooled as independent evidence. There is intentionally no price,
    ROI, confidence interval, probability forecast or promotion status here.
    Missing input is its own explicit cohort, not folded into NO_CLAIM.
    """
    eligible = [
        row
        for row in rows
        if row.get("kind") == "FINAL_PREGAME"
        and isinstance(row.get("control"), dict)
        and row["control"].get("side") in ("home", "away")
        and row["control"].get("strength") in ("MODERATE", "STRONG")
    ]
    # A stale, duplicate final-pregame record must never inflate a sample.
    selected: dict[str, dict[str, Any]] = {}
    for row in eligible:
        key = str(row["game_key"])
        prior = selected.get(key)
        if prior is None or str(row.get("recorded_at") or "") > str(prior.get("recorded_at") or ""):
            selected[key] = row

    tiers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    moderators: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for row in selected.values():
        c = row["control"]
        tier = f"{c['side'].upper()}_CONTROL_{c['strength']}"
        tiers[tier].append(row)
        for dimension, value in _labels(row, c["side"]).items():
            moderators[dimension][value][tier].append(row)

    return {
        "schema": SCHEMA,
        "status": "DESCRIPTIVE_ONLY_NO_VALIDATED_MARKET_EDGE",
        "source": "prospectively frozen V2 FINAL_PREGAME claims joined to completed game outcomes",
        "total_control_games": len(selected),
        "minimum_descriptive_sample": MIN_DESCRIPTIVE_SAMPLE,
        "by_control_tier": {tier: _summarize(rs) for tier, rs in sorted(tiers.items())},
        "moderators": {
            name: {
                state: {tier: _summarize(rs) for tier, rs in sorted(states.items())}
                for state, states in sorted(values.items())
            }
            for name, values in sorted(moderators.items())
        },
        "limitations": [
            "Football-only conditional frequencies; not incremental prediction or a priced betting signal.",
            "The same games appear across moderator dimensions; cells are overlapping, not independent bets.",
            "Small and post-observed cohorts need preregistration, era-separated validation, executable prices and fees.",
            "Missing or uncheckable outcome inputs are counted rather than scored as losses.",
        ],
    }
