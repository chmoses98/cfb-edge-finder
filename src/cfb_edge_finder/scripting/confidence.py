"""Script-data confidence and the validation gates. PURE.

*** CONFIDENCE DESCRIBES THE EVIDENCE, NOT THE PICK ***
HIGH / MEDIUM / LOW says how much football evidence stands behind the
matchup picture: coverage, sample, adjustment stability, availability and
identity. It says nothing about how strongly any side or script is
favoured, and it is computed before -- and without -- any market input.

*** THE GATES (all must pass before anything is shown above LOW) ***
  adjusted_inputs     opponent-adjusted yards/play and points exist for both
                      teams, both units
  sample_size         both teams have >= 3 completed games before kickoff
  identity            the football teams were matched to the game's teams
                      by name, home/away orientation established
  freshness           every completed prior game of both teams is in the
                      game log, and the log is recent
  availability        availability was observed or explicitly recorded as
                      not observed, and no quarterback is listed uncertain
  market_blind        the artifact was built from football inputs only
                      (structural: the football builder has no price input)
"""

from __future__ import annotations

from typing import Any

from cfb_edge_finder.scripting.matchup import DIMENSION_EDGE_METRICS
from cfb_edge_finder.scripting.metrics import CORE, METRICS

HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"

MIN_GAMES_GATE = 3
HIGH_MIN_GAMES = 5
HIGH_CORE_COVERAGE = 0.9
MEDIUM_CORE_COVERAGE = 0.8
HIGH_ENHANCED_DIMENSIONS = 3
MAX_PRIOR_WEIGHT_HIGH = 0.5

ENHANCED_DIMENSIONS = ("sustained_efficiency", "rushing", "passing", "explosiveness", "finishing")


def _core_coverage(team: dict[str, Any]) -> float:
    adjustable = [m for m in METRICS if m.tier == CORE and m.adjust]
    have = 0
    for metric in adjustable:
        for unit in ("offense", "defense"):
            row = (team.get("metrics") or {}).get(f"{unit}.{metric.metric_id}") or {}
            have += bool(row.get("adjusted_available"))
    return have / (2 * len(adjustable)) if adjustable else 0.0


def _enhanced_dimensions(vector: dict[str, Any]) -> list[str]:
    out = []
    for dimension in ENHANCED_DIMENSIONS:
        d = (vector.get("dimensions") or {}).get(dimension) or {}
        home = (d.get("home_offense_vs_away_defense") or {}).get("enhanced_components") or 0
        away = (d.get("away_offense_vs_home_defense") or {}).get("enhanced_components") or 0
        if home and away and dimension in DIMENSION_EDGE_METRICS:
            out.append(dimension)
    return out


def _key_prior_weight(team: dict[str, Any]) -> float | None:
    weights = []
    for key in (
        "offense.yards_per_play",
        "defense.yards_per_play",
        "offense.points_per_game",
        "defense.points_per_game",
    ):
        pw = (((team.get("metrics") or {}).get(key) or {}).get("sample") or {}).get("prior_weight")
        if pw is not None:
            weights.append(pw)
    return max(weights) if weights else None


def assess(
    vector: dict[str, Any],
    *,
    names: dict[str, str],
    availability: dict[str, Any],
    identity_check: dict[str, Any],
    freshness: dict[str, Any],
) -> dict[str, Any]:
    teams = vector.get("teams") or {}
    adjustment = vector.get("adjustment") or {}
    sides = ("home", "away")

    coverage = {side: round(_core_coverage(teams.get(side) or {}), 3) for side in sides}
    games = {side: int((teams.get(side) or {}).get("games_observed") or 0) for side in sides}
    prior = {side: _key_prior_weight(teams.get(side) or {}) for side in sides}
    enhanced = _enhanced_dimensions(vector)
    qb_change = {side: bool(((teams.get(side) or {}).get("quarterback") or {}).get("changed")) for side in sides}
    avail_status = {side: (availability.get(side) or {}).get("status", "NOT_OBSERVED") for side in sides}
    qb_uncertain = {side: bool((availability.get(side) or {}).get("qb_uncertain")) for side in sides}

    def adjusted_ok(side: str) -> bool:
        m = (teams.get(side) or {}).get("metrics") or {}
        return all(
            (m.get(k) or {}).get("adjusted_available")
            for k in (
                "offense.yards_per_play",
                "defense.yards_per_play",
                "offense.points_per_game",
                "defense.points_per_game",
            )
        )

    gates = {
        "adjusted_inputs": all(adjusted_ok(s) for s in sides),
        "sample_size": min(games.values()) >= MIN_GAMES_GATE,
        "identity": identity_check.get("status") in ("PASS", "RESOLVED"),
        "freshness": freshness.get("status") == "FRESH",
        "availability": not any(qb_uncertain.values()),
        "market_blind": True,
    }

    reasons: list[str] = []
    for gate, ok in gates.items():
        if not ok:
            reasons.append(f"gate failed: {gate}")
    stable = bool(adjustment.get("stable"))
    if not stable:
        reasons.append("opponent adjustment is not yet stable (schedule graph not connected or median sample < 3)")
    if any(qb_change.values()):
        reasons.append("a quarterback change is visible in the box scores")

    if not all(gates.values()):
        level = LOW
    elif (
        min(coverage.values()) >= HIGH_CORE_COVERAGE
        and len(enhanced) >= HIGH_ENHANCED_DIMENSIONS
        and min(games.values()) >= HIGH_MIN_GAMES
        and stable
        and all(s == "OBSERVED" for s in avail_status.values())
        and not any(qb_change.values())
        and all(p is not None and p <= MAX_PRIOR_WEIGHT_HIGH for p in prior.values())
    ):
        level = HIGH
    elif min(coverage.values()) >= MEDIUM_CORE_COVERAGE and stable and not any(qb_change.values()):
        level = MEDIUM
    else:
        level = LOW
        if min(coverage.values()) < MEDIUM_CORE_COVERAGE:
            reasons.append("core metric coverage below 80% for at least one team")

    known, unknown = [], []
    for side in sides:
        n = names[side]
        known.append(f"{n}: {games[side]} completed game(s) before kickoff, core metric coverage {coverage[side]:.0%}")
        if prior[side] is not None:
            known.append(f"{n}: prior weight on key adjusted metrics up to {prior[side]:.0%}")
        if avail_status[side] == "NOT_OBSERVED":
            unknown.append(f"{n}: injury/availability list was not observed for this publication")
        elif avail_status[side] == "NOT_PUBLISHED":
            unknown.append(
                f"{n}: the source publishes no injury list for this team; absence of a listing is not health"
            )
        if qb_change[side]:
            unknown.append(f"{n}: which quarterback starts (the box scores show a recent change)")
    missing_enhanced = [d for d in ENHANCED_DIMENSIONS if d not in enhanced]
    if missing_enhanced:
        unknown.append("play-by-play-derived evidence unavailable for: " + ", ".join(missing_enhanced))
    unknown.append("weather, travel distance, coaching tendencies and motivation are not part of the V1 evidence")

    return {
        "level": level,
        "describes": "the quality of the football evidence, never how strongly a side or script is favoured",
        "gates": gates,
        "all_gates_pass": all(gates.values()),
        "reasons": reasons,
        "core_coverage": coverage,
        "enhanced_dimensions": enhanced,
        "games_observed": games,
        "key_prior_weight": prior,
        "adjustment_stable": stable,
        "availability_status": avail_status,
        "identity": identity_check,
        "freshness": freshness,
        "known": known,
        "unknown": unknown,
    }
