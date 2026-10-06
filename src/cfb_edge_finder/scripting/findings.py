"""Deterministic matchup findings. PURE.

A finding is a structured, reproducible statement about the matchup vector:
a code, the side it favours, the measured value, the threshold it cleared,
and the metric keys (`home.offense.success_rate`, ...) that produced it.
Its `statement` is generated from those same numbers, so the prose can never
say something the evidence does not.

*** TWO GATES FOR EVERY MATCHUP FINDING ***
  1. |value| >= threshold          the difference is football-meaningful
  2. |value| >= uncertainty        the difference exceeds its own noise
A two-game team with a large but noisy edge fails gate 2, which is the
point: early-season noise does not get to become a script.

*** WHAT NEVER CREATES A FINDING ***
No price, line, spread, total, implied probability or consensus. The inputs
are the matchup vector (football only), the availability observation and
the identity. `tests/test_script_engine_market_blindness.py` enforces it.

Thresholds are in matchup-edge units (see `matchup.py`): an edge of 1.0 is,
for example, two units each half an SD on the favourable side of average.
"""

from __future__ import annotations

from typing import Any

MODERATE = "MODERATE"
STRONG = "STRONG"

#: (moderate, strong) thresholds.
NET_THRESHOLD = (1.0, 2.0)
UNIT_THRESHOLD = (1.0, 2.0)
ENVIRONMENT_THRESHOLD = (1.0, 2.0)
SCORING_ENV_THRESHOLD = (1.5, 3.0)
BOTH_UNITS_THRESHOLD = 0.75
DEPENDENCE_Z = 1.0
HIGH_VARIANCE_PERCENTILE = 0.8
THIN_SAMPLE_GAMES = 3
#: Below these net gaps (efficiency, scoring) a matchup is EVEN: neither
#: composite clears the moderate threshold for either side.
EVEN_NET = NET_THRESHOLD[0]
EVEN_SCORING_NET = NET_THRESHOLD[0]

CATEGORY_MATCHUP = "MATCHUP"
CATEGORY_ENVIRONMENT = "ENVIRONMENT"
CATEGORY_DEPENDENCE = "DEPENDENCE"
CATEGORY_DATA = "DATA"

SIDES = ("home", "away")
OTHER = {"home": "away", "away": "home"}
DIRECTION_KEY = {"home": "home_offense_vs_away_defense", "away": "away_offense_vs_home_defense"}

#: Plain-language labels for each dimension, used in generated statements.
DIMENSION_LABEL = {
    "sustained_efficiency": "sustained-efficiency",
    "rushing": "rushing",
    "passing": "passing",
    "explosiveness": "explosive-play",
    "disruption": "pass-protection / disruption",
    "finishing": "finishing",
    "scoring": "scoring",
}


def _strength(value: float, thresholds: tuple[float, float]) -> str | None:
    magnitude = abs(value)
    if magnitude >= thresholds[1]:
        return STRONG
    if magnitude >= thresholds[0]:
        return MODERATE
    return None


def _clear(value: float, uncertainty: float | None) -> bool:
    return uncertainty is None or abs(value) >= uncertainty


def _refs(direction: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for comp in direction.get("components") or []:
        out.extend([comp["offense_ref"], comp["defense_ref"]])
    return out


def _metric(vector: dict[str, Any], ref: str) -> dict[str, Any] | None:
    side, unit, metric_id = ref.split(".", 2)
    return ((vector.get("teams") or {}).get(side) or {}).get("metrics", {}).get(f"{unit}.{metric_id}")


def _evidence(vector: dict[str, Any], refs: list[str]) -> list[dict[str, Any]]:
    out = []
    for ref in dict.fromkeys(refs):
        m = _metric(vector, ref)
        if m is None:
            continue
        out.append(
            {
                "ref": ref,
                "name": m["name"],
                "raw": m["raw"],
                "adjusted": m["adjusted"],
                "rank": m["rank"],
                "universe_size": m["universe_size"],
                "direction": m["direction"],
                "games": m["sample"]["games"],
                "quality": m["quality"],
            }
        )
    return out


def _fmt_rank(m: dict[str, Any] | None) -> str:
    if not m or m.get("rank") is None:
        return "unranked"
    return f"#{m['rank']} of {m['universe_size']}"


def _lead_metric_phrase(vector: dict[str, Any], direction: dict[str, Any], names: dict[str, str]) -> str:
    """'success rate: offense #12 of 136 vs defense #98 of 136' for the first component."""
    comps = direction.get("components") or []
    if not comps:
        return ""
    comp = comps[0]
    off, dfn = _metric(vector, comp["offense_ref"]), _metric(vector, comp["defense_ref"])
    if off is None or dfn is None:
        return ""
    off_side = comp["offense_ref"].split(".")[0]
    def_side = comp["defense_ref"].split(".")[0]
    return (
        f" ({off['name'].lower()}, opponent-adjusted: {names[off_side]} offense {_fmt_rank(off)}, "
        f"{names[def_side]} defense {_fmt_rank(dfn)})"
    )


def _finding(
    code: str,
    side: str,
    dimension: str,
    category: str,
    strength: str,
    value: float | None,
    threshold: float | None,
    uncertainty: float | None,
    refs: list[str],
    statement: str,
    vector: dict[str, Any],
) -> dict[str, Any]:
    return {
        "code": code,
        "side": side,
        "dimension": dimension,
        "category": category,
        "strength": strength,
        "value": None if value is None else round(value, 3),
        "threshold": threshold,
        "uncertainty": None if uncertainty is None else round(uncertainty, 3),
        "metric_refs": list(dict.fromkeys(refs)),
        "evidence": _evidence(vector, refs),
        "statement": statement,
    }


def derive_findings(
    vector: dict[str, Any],
    names: dict[str, str],
    availability: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Every finding the matchup vector supports, in a deterministic order."""
    dims = vector.get("dimensions") or {}
    out: list[dict[str, Any]] = []

    # --- net advantages: who wins this dimension of the matchup
    for dimension, code_stub in (
        ("sustained_efficiency", "SUSTAINED_EFFICIENCY_ADVANTAGE"),
        ("finishing", "FINISHING_ADVANTAGE"),
        ("explosiveness", "EXPLOSIVE_ADVANTAGE"),
        ("scoring", "SCORING_ADVANTAGE"),
    ):
        d = dims.get(dimension) or {}
        net = d.get("net_home_advantage")
        if net is None:
            continue
        strength = _strength(net, NET_THRESHOLD)
        home_dir, away_dir = d["home_offense_vs_away_defense"], d["away_offense_vs_home_defense"]
        unc = max(home_dir.get("uncertainty") or 0.0, away_dir.get("uncertainty") or 0.0)
        if strength is None or not _clear(net, unc):
            continue
        side = "home" if net > 0 else "away"
        lead = home_dir if side == "home" else away_dir
        out.append(
            _finding(
                f"{side.upper()}_{code_stub}",
                side,
                dimension,
                CATEGORY_MATCHUP,
                strength,
                net,
                NET_THRESHOLD[0],
                unc,
                _refs(home_dir) + _refs(away_dir),
                (
                    f"{names[side]} owns the stronger opponent-adjusted {DIMENSION_LABEL[dimension]} matchup: "
                    f"{names['home']} offense vs {names['away']} defense {home_dir['edge']:+.2f}, "
                    f"{names['away']} offense vs {names['home']} defense {away_dir['edge']:+.2f}"
                    f"{_lead_metric_phrase(vector, lead, names)}."
                ),
                vector,
            )
        )

    # --- how decisive is the efficiency gap?
    sus = dims.get("sustained_efficiency") or {}
    sus_net = sus.get("net_home_advantage")
    score_net = (dims.get("scoring") or {}).get("net_home_advantage")
    if sus_net is not None:
        refs = _refs(sus.get("home_offense_vs_away_defense") or {}) + _refs(
            sus.get("away_offense_vs_home_defense") or {}
        )
        # The efficiency gap's uncertainty, as the net-advantage findings use it.
        # Published on the closeness findings so a script can tell measured
        # parity from an edge the data cannot see.
        sus_unc = (
            max(
                (sus.get("home_offense_vs_away_defense") or {}).get("uncertainty") or 0.0,
                (sus.get("away_offense_vs_home_defense") or {}).get("uncertainty") or 0.0,
            )
            or None
        )
        if NET_THRESHOLD[0] <= abs(sus_net) < NET_THRESHOLD[1]:
            leader = names["home"] if sus_net > 0 else names["away"]
            out.append(
                _finding(
                    "NARROW_EFFICIENCY_GAP",
                    "game",
                    "sustained_efficiency",
                    CATEGORY_ENVIRONMENT,
                    MODERATE,
                    abs(sus_net),
                    NET_THRESHOLD[1],
                    sus_unc,
                    refs,
                    (
                        f"{leader}'s sustained-efficiency edge is real but not decisive ({abs(sus_net):.2f}, below the "
                        f"{NET_THRESHOLD[1]:.1f} strong threshold): enough for the trailing side to stay in range."
                    ),
                    vector,
                )
            )
        if abs(sus_net) < EVEN_NET and score_net is not None and abs(score_net) < EVEN_SCORING_NET:
            out.append(
                _finding(
                    "EVEN_MATCHUP",
                    "game",
                    "sustained_efficiency",
                    CATEGORY_ENVIRONMENT,
                    MODERATE,
                    abs(sus_net),
                    EVEN_NET,
                    sus_unc,
                    refs,
                    (
                        f"Neither team owns the opponent-adjusted efficiency or scoring matchup "
                        f"(efficiency gap {sus_net:+.2f}, scoring gap {score_net:+.2f})."
                    ),
                    vector,
                )
            )

    # --- early downs: a per-metric net, enhanced tier only
    sustained = dims.get("sustained_efficiency") or {}
    early = {
        side: next(
            (
                c
                for c in (sustained.get(DIRECTION_KEY[side]) or {}).get("components") or []
                if c["metric_id"] == "early_down_success_rate"
            ),
            None,
        )
        for side in SIDES
    }
    if early["home"] and early["away"]:
        net = early["home"]["edge"] - early["away"]["edge"]
        unc = max(early["home"]["uncertainty"], early["away"]["uncertainty"])
        strength = _strength(net, NET_THRESHOLD)
        if strength and _clear(net, unc):
            side = "home" if net > 0 else "away"
            refs = [early[s][k] for s in SIDES for k in ("offense_ref", "defense_ref")]
            out.append(
                _finding(
                    f"{side.upper()}_EARLY_DOWN_ADVANTAGE",
                    side,
                    "sustained_efficiency",
                    CATEGORY_MATCHUP,
                    strength,
                    net,
                    NET_THRESHOLD[0],
                    unc,
                    refs,
                    (
                        f"{names[side]} holds the early-down edge: its offense stays ahead of the chains on 1st and "
                        f"2nd down against this defense ({early[side]['edge']:+.2f}) more than "
                        f"{names[OTHER[side]]} does ({early[OTHER[side]]['edge']:+.2f})."
                    ),
                    vector,
                )
            )

    # --- unit matchups: one offense against one defense
    for side in SIDES:
        for dimension, code_stub in (("rushing", "RUSH_ADVANTAGE"), ("passing", "PASS_ADVANTAGE")):
            direction = (dims.get(dimension) or {}).get(DIRECTION_KEY[side]) or {}
            edge = direction.get("edge")
            if edge is None or edge <= 0:
                continue
            strength = _strength(edge, UNIT_THRESHOLD)
            if strength and _clear(edge, direction.get("uncertainty")):
                out.append(
                    _finding(
                        f"{side.upper()}_{code_stub}",
                        side,
                        dimension,
                        CATEGORY_MATCHUP,
                        strength,
                        edge,
                        UNIT_THRESHOLD[0],
                        direction.get("uncertainty"),
                        _refs(direction),
                        (
                            f"{names[side]}'s {DIMENSION_LABEL[dimension]} offense meets a {names[OTHER[side]]} "
                            f"defense it out-rates on the matchup scale ({edge:+.2f})"
                            f"{_lead_metric_phrase(vector, direction, names)}."
                        ),
                        vector,
                    )
                )
        explosive = (dims.get("explosiveness") or {}).get(DIRECTION_KEY[side]) or {}
        for comp in explosive.get("components") or []:
            if comp["metric_id"] not in ("explosive_pass_rate", "explosive_rush_rate") or comp["edge"] <= 0:
                continue
            strength = _strength(comp["edge"], UNIT_THRESHOLD)
            if strength and _clear(comp["edge"], comp["uncertainty"]):
                kind = "PASS" if comp["metric_id"] == "explosive_pass_rate" else "RUSH"
                out.append(
                    _finding(
                        f"{side.upper()}_{kind}_EXPLOSIVE_ADVANTAGE",
                        side,
                        "explosiveness",
                        CATEGORY_MATCHUP,
                        strength,
                        comp["edge"],
                        UNIT_THRESHOLD[0],
                        comp["uncertainty"],
                        [comp["offense_ref"], comp["defense_ref"]],
                        (
                            f"{names[side]}'s explosive {kind.lower()}ing offense meets a {names[OTHER[side]]} defense "
                            f"that allows explosive {kind.lower()} plays ({comp['edge']:+.2f})."
                        ),
                        vector,
                    )
                )
        # A DEFENSE that controls the opposing offense: the other side's direction is strongly negative.
        for dimension, code_stub in (
            ("disruption", "DISRUPTION_ADVANTAGE"),
            ("sustained_efficiency", "DEFENSIVE_CONTROL"),
        ):
            direction = (dims.get(dimension) or {}).get(DIRECTION_KEY[OTHER[side]]) or {}
            edge = direction.get("edge")
            if edge is None or edge >= 0:
                continue
            strength = _strength(edge, UNIT_THRESHOLD)
            if strength and _clear(edge, direction.get("uncertainty")):
                what = (
                    "generates sacks and negative plays against an offense that allows them"
                    if dimension == "disruption"
                    else "out-rates the opposing offense on a down-to-down basis"
                )
                out.append(
                    _finding(
                        f"{side.upper()}_{code_stub}",
                        side,
                        dimension,
                        CATEGORY_MATCHUP,
                        strength,
                        -edge,
                        UNIT_THRESHOLD[0],
                        direction.get("uncertainty"),
                        _refs(direction),
                        f"{names[side]}'s defense {what} ({edge:+.2f} for {names[OTHER[side]]}'s offense).",
                        vector,
                    )
                )

    # --- environment
    pace = dims.get("pace") or {}
    env = pace.get("possession_environment")
    if env is not None:
        strength = _strength(env, ENVIRONMENT_THRESHOLD)
        if strength:
            code = "HIGH_POSSESSION_ENVIRONMENT" if env > 0 else "LOW_POSSESSION_ENVIRONMENT"
            out.append(
                _finding(
                    code,
                    "game",
                    "pace",
                    CATEGORY_ENVIRONMENT,
                    strength,
                    env,
                    ENVIRONMENT_THRESHOLD[0],
                    None,
                    pace.get("refs") or [],
                    (
                        f"Both offenses and both defenses point to {'more' if env > 0 else 'fewer'} snaps than an "
                        f"average FBS game (possession environment {env:+.2f})."
                    ),
                    vector,
                )
            )
    scoring = dims.get("scoring") or {}
    h_edge = (scoring.get("home_offense_vs_away_defense") or {}).get("edge")
    a_edge = (scoring.get("away_offense_vs_home_defense") or {}).get("edge")
    if h_edge is not None and a_edge is not None:
        total_env = h_edge + a_edge
        strength = _strength(total_env, SCORING_ENV_THRESHOLD)
        if strength:
            code = "HIGH_SCORING_ENVIRONMENT" if total_env > 0 else "LOW_SCORING_ENVIRONMENT"
            out.append(
                _finding(
                    code,
                    "game",
                    "scoring",
                    CATEGORY_ENVIRONMENT,
                    strength,
                    total_env,
                    SCORING_ENV_THRESHOLD[0],
                    None,
                    _refs(scoring.get("home_offense_vs_away_defense") or {})
                    + _refs(scoring.get("away_offense_vs_home_defense") or {}),
                    (
                        f"Each offense meets a defense it {'out-scores' if total_env > 0 else 'is held by'} on "
                        f"opponent-adjusted points ({names['home']} {h_edge:+.2f}, {names['away']} {a_edge:+.2f})."
                    ),
                    vector,
                )
            )
    sustained_h = (sustained.get("home_offense_vs_away_defense") or {}).get("edge")
    sustained_a = (sustained.get("away_offense_vs_home_defense") or {}).get("edge")
    if sustained_h is not None and sustained_a is not None:
        refs = _refs(sustained.get("home_offense_vs_away_defense") or {}) + _refs(
            sustained.get("away_offense_vs_home_defense") or {}
        )
        if min(sustained_h, sustained_a) >= BOTH_UNITS_THRESHOLD:
            out.append(
                _finding(
                    "BOTH_OFFENSES_EFFICIENT",
                    "game",
                    "sustained_efficiency",
                    CATEGORY_ENVIRONMENT,
                    MODERATE,
                    min(sustained_h, sustained_a),
                    BOTH_UNITS_THRESHOLD,
                    None,
                    refs,
                    (
                        f"Both offenses out-rate the defense they face on sustained efficiency "
                        f"({names['home']} {sustained_h:+.2f}, {names['away']} {sustained_a:+.2f})."
                    ),
                    vector,
                )
            )
        if max(sustained_h, sustained_a) <= -BOTH_UNITS_THRESHOLD:
            out.append(
                _finding(
                    "BOTH_DEFENSES_CONTROL",
                    "game",
                    "sustained_efficiency",
                    CATEGORY_ENVIRONMENT,
                    MODERATE,
                    max(sustained_h, sustained_a),
                    -BOTH_UNITS_THRESHOLD,
                    None,
                    refs,
                    (
                        f"Both defenses out-rate the offense they face on sustained efficiency "
                        f"({names['home']} offense {sustained_h:+.2f}, {names['away']} offense {sustained_a:+.2f})."
                    ),
                    vector,
                )
            )

    # --- dependence and variance
    vol = dims.get("volatility") or {}
    variance_flags = 0
    for side in SIDES:
        v = vol.get(side) or {}
        refs = v.get("refs") or []
        share_z, success_z = v.get("explosive_yard_share_z"), v.get("success_rate_z")
        if share_z is not None and success_z is not None and share_z >= DEPENDENCE_Z and success_z <= 0.25:
            variance_flags += 1
            out.append(
                _finding(
                    f"{side.upper()}_SCORING_DEPENDENT_ON_EXPLOSIVES",
                    side,
                    "volatility",
                    CATEGORY_DEPENDENCE,
                    MODERATE,
                    share_z,
                    DEPENDENCE_Z,
                    None,
                    refs[:2],
                    (
                        f"{names[side]}'s yardage leans on explosive plays (share {share_z:+.2f} SD) more than its "
                        f"down-to-down success rate ({success_z:+.2f} SD) supports: a less repeatable path."
                    ),
                    vector,
                )
            )
        take_z, allowed_z = v.get("takeaways_per_game_z"), v.get("defense_ypp_allowed_z")
        if take_z is not None and allowed_z is not None and take_z >= DEPENDENCE_Z and allowed_z >= 0:
            variance_flags += 1
            out.append(
                _finding(
                    f"{side.upper()}_DEFENSE_TURNOVER_RELIANT",
                    side,
                    "volatility",
                    CATEGORY_DEPENDENCE,
                    MODERATE,
                    take_z,
                    DEPENDENCE_Z,
                    None,
                    [refs[2], refs[4]] if len(refs) >= 5 else refs,
                    (
                        f"{names[side]}'s defense has forced turnovers at a high rate ({take_z:+.2f} SD) while "
                        f"allowing average-or-worse yards per play: takeaways are regression-prone."
                    ),
                    vector,
                )
            )
        give_z = v.get("giveaways_per_game_z")
        if give_z is not None and give_z >= DEPENDENCE_Z:
            variance_flags += 1
            out.append(
                _finding(
                    f"{side.upper()}_OFFENSE_TURNOVER_PRONE",
                    side,
                    "volatility",
                    CATEGORY_DEPENDENCE,
                    MODERATE,
                    give_z,
                    DEPENDENCE_Z,
                    None,
                    [refs[3]] if len(refs) >= 4 else refs,
                    (
                        f"{names[side]}'s offense has given the ball away at a high rate ({give_z:+.2f} SD). "
                        f"Regression-prone: reported as volatility, never as a cause on its own."
                    ),
                    vector,
                )
            )
        pct = v.get("offense_ypp_residual_sd_percentile")
        if pct is not None and pct >= HIGH_VARIANCE_PERCENTILE:
            variance_flags += 1
            out.append(
                _finding(
                    f"{side.upper()}_HIGH_VARIANCE_OFFENSE",
                    side,
                    "volatility",
                    CATEGORY_DEPENDENCE,
                    MODERATE,
                    pct,
                    HIGH_VARIANCE_PERCENTILE,
                    None,
                    [f"{side}.offense.yards_per_play"],
                    (
                        f"{names[side]}'s offense has swung widely from game to game relative to its opponents "
                        f"(game-to-game residual variation in the {int(round(pct * 100))}th percentile of FBS)."
                    ),
                    vector,
                )
            )
    explosive_sides = {f["side"] for f in out if f["code"].endswith("_EXPLOSIVE_ADVANTAGE") and f["side"] in SIDES}
    if len(explosive_sides) == 2:
        variance_flags += 1
    if variance_flags >= 2:
        refs = sorted({r for f in out if f["category"] == CATEGORY_DEPENDENCE for r in f["metric_refs"]})
        out.append(
            _finding(
                "HIGH_VARIANCE_MATCHUP",
                "game",
                "volatility",
                CATEGORY_DEPENDENCE,
                STRONG if variance_flags >= 3 else MODERATE,
                float(variance_flags),
                2.0,
                None,
                refs,
                f"{variance_flags} independent volatility indicators are present: this matchup has wide outcomes.",
                vector,
            )
        )

    # --- data findings: what the evidence cannot see
    teams = vector.get("teams") or {}
    for side in SIDES:
        team = teams.get(side) or {}
        if (team.get("games_observed") or 0) < THIN_SAMPLE_GAMES:
            out.append(
                _finding(
                    f"{side.upper()}_THIN_SAMPLE",
                    side,
                    "data",
                    CATEGORY_DATA,
                    MODERATE,
                    float(team.get("games_observed") or 0),
                    float(THIN_SAMPLE_GAMES),
                    None,
                    [],
                    f"{names[side]} has only {team.get('games_observed') or 0} completed game(s) before kickoff.",
                    vector,
                )
            )
        if team.get("division") in ("fcs", "other"):
            out.append(
                _finding(
                    f"{side.upper()}_NON_FBS",
                    side,
                    "data",
                    CATEGORY_DATA,
                    MODERATE,
                    None,
                    None,
                    None,
                    [],
                    f"{names[side]} is not an FBS team; its ranks are not drawn from the FBS universe.",
                    vector,
                )
            )
        qb = team.get("quarterback") or {}
        if qb.get("changed"):
            out.append(
                _finding(
                    f"{side.upper()}_QB_CHANGE_RECENT",
                    side,
                    "data",
                    CATEGORY_DATA,
                    STRONG,
                    None,
                    None,
                    None,
                    [],
                    (
                        f"{names[side]}'s leading passer in its last game ({qb.get('last_game_primary')}) is not its "
                        f"season leader ({qb.get('season_primary')}): season passing numbers may not describe "
                        f"the quarterback who plays."
                    ),
                    vector,
                )
            )
        avail = (availability or {}).get(side) or {}
        if avail.get("qb_uncertain"):
            out.append(
                _finding(
                    f"{side.upper()}_QB_AVAILABILITY_UNCERTAIN",
                    side,
                    "data",
                    CATEGORY_DATA,
                    STRONG,
                    None,
                    None,
                    None,
                    [],
                    (
                        f"{names[side]} lists a quarterback as {avail.get('qb_status')} "
                        f"({avail.get('qb_name')}) as of {avail.get('observed_at')}."
                    ),
                    vector,
                )
            )

    order = {CATEGORY_MATCHUP: 0, CATEGORY_ENVIRONMENT: 1, CATEGORY_DEPENDENCE: 2, CATEGORY_DATA: 3}
    out.sort(key=lambda f: (order[f["category"]], 0 if f["strength"] == STRONG else 1, f["code"]))
    return out


def codes(findings: list[dict[str, Any]]) -> set[str]:
    return {f["code"] for f in findings}
