"""Evidence-gated game scripts. PURE, deterministic, market-blind.

*** AN ARCHETYPE IS A LABEL, NOT A CONCLUSION ***
Each archetype below states which findings it REQUIRES, which SUPPORT it and
which CONTRADICT it. A script is generated only when its required findings
exist for this game; a game whose evidence supports one script gets one, and
a game whose evidence supports none gets none (`status:
NO_SCRIPT_CLEARED_EVIDENCE`). There are no boilerplate scenarios.

*** RANKING (no probabilities in V1) ***
    evidence = sum(weight of required + supporting findings present)
             - sum(weight of contradicting findings present)
    weight: STRONG = 2, MODERATE = 1
Candidates are ordered by evidence, ties broken by a fixed archetype order.
  PRIMARY    the best-evidenced script
  DANGER     the best-evidenced remaining script that BREAKS the primary
             (a different winner, an opposite scoring environment, or a
             margin band below the primary's). Its REQUIRED findings must
             exist; it may be net-contradicted down to evidence -1, because
             a danger is the plausible path against the primary, not a
             second endorsement of it
  SECONDARY, ALTERNATE   the next best remaining scripts
At most four. `probability` is reserved and is always null in V1: script
likelihoods will be calibrated from prospectively frozen outcomes, never
from script counts.

*** OUTCOME BANDS AND THEIR AUTHORITY ***
Every numeric band carries its provenance in `outcome_shape.band_authority`:
  ARCHETYPE_DEFINITION     margin bands: what an archetype MEANS ("control" =
                           the leading team wins by 7 to 24, one-score = +/-8).
                           A definition, not an estimate; market mapping may
                           classify against it.
  UNCALIBRATED_DESCRIPTIVE total and team-points bands: drawn around the
                           opponent-adjusted scoring baseline
                           (`matchup.scoring_baseline`), which is descriptive
                           and has NOT passed the scoring-band promotion gate
                           (docs/SCRIPT_ENGINE.md section 15). Published as
                           research; market mapping may NOT classify a
                           contract as supported or contradicted by it.
  CALIBRATED               reserved. Only the promotion gate can grant it; no
                           V1 band carries it.
The qualitative scoring statements (`total_environment`, `home_scoring`,
`away_scoring`) are football conclusions and are unaffected. No line, total
or price informs any band or any authority, and the market mapping reads
bands only after the artifact is frozen.

*** CAUSAL CHAINS ***
Each step is a football mechanism and cites the findings that justify it.
A chain may not argue from a result to a result ("X is favoured, so X
leads, so X wins"); every step traces to matchup evidence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from cfb_edge_finder.scripting.findings import CATEGORY_DATA, STRONG

PRIMARY = "PRIMARY"
SECONDARY = "SECONDARY"
ALTERNATE = "ALTERNATE"
DANGER = "DANGER"
ROLES = (PRIMARY, SECONDARY, ALTERNATE, DANGER)
MAX_SCRIPTS = 4

ARCHETYPE_ORDER = (
    "HOME_CONTROL",
    "AWAY_CONTROL",
    "FAVORITE_PULLS_AWAY",
    "UNDERDOG_HANGS_AROUND",
    "COMPETITIVE_SHOOTOUT",
    "COMPETITIVE_GRIND",
    "COMPETITIVE_TOSSUP",
    "PACE_DRIVEN_OVER",
    "DEFENSIVE_SUPPRESSION",
    "EXPLOSIVE_UPSET",
    "TURNOVER_DISRUPTION",
)

#: Archetypes that cannot both appear: they describe the same game twice.
EXCLUSIVE_GROUPS = (
    frozenset({"COMPETITIVE_GRIND", "DEFENSIVE_SUPPRESSION"}),
    frozenset({"COMPETITIVE_SHOOTOUT", "PACE_DRIVEN_OVER"}),
    frozenset({"COMPETITIVE_TOSSUP", "COMPETITIVE_SHOOTOUT"}),
    frozenset({"COMPETITIVE_TOSSUP", "COMPETITIVE_GRIND"}),
)

ONE_SCORE = 8

ARCHETYPE_DEFINITION = "ARCHETYPE_DEFINITION"
UNCALIBRATED_DESCRIPTIVE = "UNCALIBRATED_DESCRIPTIVE"
CALIBRATED = "CALIBRATED"
BAND_AUTHORITIES = (ARCHETYPE_DEFINITION, UNCALIBRATED_DESCRIPTIVE, CALIBRATED)

#: Bands whose numbers ARE the archetype's definition.
DEFINITIONAL_BANDS = frozenset({"home_margin"})
#: Authority of every band drawn around the scoring baseline. It changes to
#: CALIBRATED only through the promotion gate (docs/SCRIPT_ENGINE.md section
#: 15), which requires out-of-sample evidence on actual scores -- never on
#: market totals.
SCORING_BAND_AUTHORITY = UNCALIBRATED_DESCRIPTIVE


def band_authority(bands: dict[str, Any]) -> dict[str, str]:
    """The authority of every numeric band a script states."""
    return {
        name: ARCHETYPE_DEFINITION if name in DEFINITIONAL_BANDS else SCORING_BAND_AUTHORITY
        for name, band in sorted(bands.items())
        if band is not None
    }


@dataclass
class Candidate:
    archetype: str
    lead: str | None  # "home" | "away" | None -- the side the script is about
    required: list[str]
    supporting: list[str]
    contradicting: list[str]
    shape: dict[str, Any]
    chain: list[dict[str, Any]]
    title: str
    summary: str
    evidence: float = 0.0
    notes: list[str] = field(default_factory=list)


def _weight(finding: dict[str, Any]) -> float:
    return 2.0 if finding["strength"] == STRONG else 1.0


def _other(side: str) -> str:
    return "away" if side == "home" else "home"


def _u(side: str) -> str:
    return side.upper()


def _band(lo: float | None, hi: float | None) -> list[int] | None:
    if lo is None or hi is None:
        return None
    lo_i, hi_i = int(round(lo)), int(round(hi))
    return [min(lo_i, hi_i), max(lo_i, hi_i)]


def _margin_band(lead: str, lo: float, hi: float) -> list[int]:
    """A band on the LEAD team's margin, expressed as a HOME margin band."""
    return _band(lo, hi) if lead == "home" else _band(-hi, -lo)


class _Ctx:
    def __init__(self, findings: list[dict[str, Any]], names: dict[str, str], baseline: dict[str, Any]):
        self.by_code = {f["code"]: f for f in findings if f["category"] != CATEGORY_DATA}
        self.names = names
        self.base = baseline

    def has(self, code: str) -> bool:
        return code in self.by_code

    def present(self, codes: list[str]) -> list[str]:
        return [c for c in codes if c in self.by_code]

    def strong(self, code: str) -> bool:
        return code in self.by_code and self.by_code[code]["strength"] == STRONG

    def pts(self, side: str) -> float | None:
        return self.base.get(f"{side}_points")

    def total(self) -> float | None:
        return self.base.get("total_points")


def _step(text: str, *codes: str) -> dict[str, Any]:
    return {"step": text, "findings": [c for c in codes if c]}


def _lead_side(ctx: _Ctx) -> str | None:
    for side in ("home", "away"):
        if ctx.has(f"{_u(side)}_SUSTAINED_EFFICIENCY_ADVANTAGE"):
            return side
    return None


def _control(ctx: _Ctx, s: str) -> Candidate | None:
    req = f"{_u(s)}_SUSTAINED_EFFICIENCY_ADVANTAGE"
    if not ctx.has(req):
        return None
    o = _other(s)
    lead_name, other_name = ctx.names[s], ctx.names[o]
    supporting = ctx.present(
        [
            f"{_u(s)}_EARLY_DOWN_ADVANTAGE",
            f"{_u(s)}_RUSH_ADVANTAGE",
            f"{_u(s)}_FINISHING_ADVANTAGE",
            f"{_u(s)}_DISRUPTION_ADVANTAGE",
            f"{_u(s)}_DEFENSIVE_CONTROL",
            f"{_u(s)}_SCORING_ADVANTAGE",
            "LOW_POSSESSION_ENVIRONMENT",
        ]
    )
    contradicting = ctx.present(
        [
            f"{_u(o)}_PASS_EXPLOSIVE_ADVANTAGE",
            f"{_u(o)}_RUSH_EXPLOSIVE_ADVANTAGE",
            f"{_u(o)}_EXPLOSIVE_ADVANTAGE",
            f"{_u(o)}_DISRUPTION_ADVANTAGE",
            "HIGH_VARIANCE_MATCHUP",
            f"{_u(s)}_SCORING_DEPENDENT_ON_EXPLOSIVES",
            f"{_u(s)}_OFFENSE_TURNOVER_PRONE",
        ]
    )
    chain = []
    if ctx.has(f"{_u(s)}_EARLY_DOWN_ADVANTAGE"):
        chain.append(
            _step(f"{lead_name} wins early downs against {other_name}'s defense", f"{_u(s)}_EARLY_DOWN_ADVANTAGE")
        )
    else:
        chain.append(_step(f"{lead_name} owns the down-to-down efficiency matchup", req))
    if ctx.has(f"{_u(s)}_RUSH_ADVANTAGE"):
        chain.append(
            _step(
                f"{lead_name}'s run game finds yardage, keeping it out of obvious passing downs",
                f"{_u(s)}_RUSH_ADVANTAGE",
            )
        )
    else:
        chain.append(_step(f"{lead_name} stays out of obvious passing downs", req))
    chain.append(_step(f"{lead_name} sustains longer possessions", req, *ctx.present(["LOW_POSSESSION_ENVIRONMENT"])))
    if ctx.has(f"{_u(s)}_FINISHING_ADVANTAGE"):
        chain.append(
            _step(
                f"{lead_name} finishes drives with touchdowns rather than field goals", f"{_u(s)}_FINISHING_ADVANTAGE"
            )
        )
    chain.append(_step(f"{other_name} receives fewer possessions and plays from behind", req))
    trailing = f"{other_name} becomes more pass-dependent while trailing"
    if ctx.has(f"{_u(s)}_DISRUPTION_ADVANTAGE"):
        trailing += f", into a {lead_name} pass rush that creates negative plays"
    chain.append(
        _step(trailing, req, f"{_u(s)}_DISRUPTION_ADVANTAGE" if ctx.has(f"{_u(s)}_DISRUPTION_ADVANTAGE") else "")
    )
    shape = {
        "winner_lean": _u(s),
        "margin_environment": f"{_u(s)}_BY_ONE_TO_THREE_SCORES",
        "total_environment": "NOT_STATED",
        f"{s}_scoring": "AT_OR_ABOVE_BASELINE",
        f"{o}_scoring": "BELOW_BASELINE",
        "pace": f"{_u(s)}_CONTROLLED_POSSESSIONS" if ctx.has("LOW_POSSESSION_ENVIRONMENT") else "NOT_STATED",
        "bands": {
            "home_margin": _margin_band(s, 7, 24),
            "total_points": None,
            f"{s}_points": _band(_sub(ctx.pts(s), 3), _add(ctx.pts(s), 14)),
            f"{o}_points": _band(_floor0(_sub(ctx.pts(o), 14)), _add(ctx.pts(o), 2)),
        },
    }
    return Candidate(
        f"{_u(s)}_CONTROL",
        s,
        [req],
        supporting,
        contradicting,
        shape,
        chain,
        f"{lead_name} controls",
        (
            f"{lead_name}'s sustained-efficiency edge turns into longer drives and a multi-score lead "
            f"that {other_name} chases."
        ),
    )


def _pulls_away(ctx: _Ctx, s: str) -> Candidate | None:
    req = f"{_u(s)}_SUSTAINED_EFFICIENCY_ADVANTAGE"
    if not ctx.strong(req):
        return None
    o = _other(s)
    second = ctx.present(
        [
            f"{_u(s)}_FINISHING_ADVANTAGE",
            f"{_u(s)}_SCORING_ADVANTAGE",
            f"{_u(s)}_DISRUPTION_ADVANTAGE",
            f"{_u(s)}_EXPLOSIVE_ADVANTAGE",
            f"{_u(s)}_RUSH_ADVANTAGE",
            f"{_u(s)}_PASS_ADVANTAGE",
        ]
    )
    if not second:
        return None
    lead_name, other_name = ctx.names[s], ctx.names[o]
    contradicting = ctx.present(
        [
            f"{_u(o)}_PASS_EXPLOSIVE_ADVANTAGE",
            f"{_u(o)}_RUSH_EXPLOSIVE_ADVANTAGE",
            f"{_u(o)}_DISRUPTION_ADVANTAGE",
            f"{_u(o)}_DEFENSIVE_CONTROL",
            "LOW_POSSESSION_ENVIRONMENT",
            "HIGH_VARIANCE_MATCHUP",
        ]
    )
    chain = [
        _step(f"{lead_name}'s efficiency edge is large on both sides of the ball", req),
        _step(f"{lead_name} has a second advantage to stack on it", *second[:2]),
        _step(f"{other_name} cannot trade possessions evenly and falls multiple scores behind", req),
        _step(f"{other_name} abandons its plan to chase points, which extends {lead_name}'s lead", req),
    ]
    shape = {
        "winner_lean": _u(s),
        "margin_environment": f"{_u(s)}_BY_THREE_PLUS_SCORES",
        "total_environment": "NOT_STATED",
        f"{s}_scoring": "ABOVE_BASELINE",
        f"{o}_scoring": "BELOW_BASELINE",
        "pace": "NOT_STATED",
        "bands": {
            "home_margin": _margin_band(s, 17, 45),
            "total_points": None,
            f"{s}_points": _band(_add(ctx.pts(s), 3), _add(ctx.pts(s), 24)),
            f"{o}_points": _band(0, _floor0(_sub(ctx.pts(o), 3))),
        },
    }
    return Candidate(
        "FAVORITE_PULLS_AWAY",
        s,
        [req],
        second,
        contradicting,
        shape,
        chain,
        f"{lead_name} pulls away",
        f"{lead_name}'s efficiency edge compounds with a second advantage and the margin keeps growing.",
    )


_COUNTERS = (
    "{o}_DISRUPTION_ADVANTAGE",
    "{o}_DEFENSIVE_CONTROL",
    "LOW_POSSESSION_ENVIRONMENT",
    "{s}_SCORING_DEPENDENT_ON_EXPLOSIVES",
    "{s}_OFFENSE_TURNOVER_PRONE",
    "{o}_PASS_EXPLOSIVE_ADVANTAGE",
    "{o}_RUSH_EXPLOSIVE_ADVANTAGE",
    "BOTH_DEFENSES_CONTROL",
    "NARROW_EFFICIENCY_GAP",
)


def _hangs_around(ctx: _Ctx, s: str) -> Candidate | None:
    req = f"{_u(s)}_SUSTAINED_EFFICIENCY_ADVANTAGE"
    if not ctx.has(req):
        return None
    o = _other(s)
    counters = ctx.present([c.format(s=_u(s), o=_u(o)) for c in _COUNTERS])
    if not counters:
        return None
    lead_name, other_name = ctx.names[s], ctx.names[o]
    contradicting = ctx.present([f"{_u(s)}_FINISHING_ADVANTAGE", "HIGH_POSSESSION_ENVIRONMENT"]) + (
        [req] if ctx.strong(req) else []
    )
    mechanism = {
        f"{_u(o)}_DISRUPTION_ADVANTAGE": f"{other_name}'s pass rush creates drive-killing negative plays",
        f"{_u(o)}_DEFENSIVE_CONTROL": f"{other_name}'s defense holds its own on a down-to-down basis",
        "LOW_POSSESSION_ENVIRONMENT": "a low-possession game limits how far any edge can compound",
        f"{_u(s)}_SCORING_DEPENDENT_ON_EXPLOSIVES": (
            f"{lead_name}'s offense relies on explosive plays that may not come"
        ),
        f"{_u(s)}_OFFENSE_TURNOVER_PRONE": f"{lead_name}'s offense gives away possessions",
        f"{_u(o)}_PASS_EXPLOSIVE_ADVANTAGE": f"{other_name} can answer with explosive passes",
        f"{_u(o)}_RUSH_EXPLOSIVE_ADVANTAGE": f"{other_name} can answer with explosive runs",
        "BOTH_DEFENSES_CONTROL": "both defenses control the offense they face",
        "NARROW_EFFICIENCY_GAP": f"{lead_name}'s efficiency edge is real but not decisive",
    }
    chain = [_step(f"{lead_name} owns the efficiency edge", req)]
    chain += [_step(mechanism[c], c) for c in counters[:3]]
    chain.append(
        _step(f"{lead_name}'s drives stall often enough that {other_name} stays within one score", req, counters[0])
    )
    shape = {
        "winner_lean": _u(s),
        "margin_environment": "ONE_SCORE",
        "total_environment": "NOT_STATED",
        f"{s}_scoring": "BELOW_BASELINE",
        f"{o}_scoring": "AT_OR_ABOVE_BASELINE",
        "pace": "FEWER_POSSESSIONS" if ctx.has("LOW_POSSESSION_ENVIRONMENT") else "NOT_STATED",
        "bands": {
            "home_margin": _margin_band(s, -7, ONE_SCORE),
            "total_points": None,
            f"{s}_points": _band(_floor0(_sub(ctx.pts(s), 10)), _add(ctx.pts(s), 3)),
            f"{o}_points": _band(_floor0(_sub(ctx.pts(o), 3)), _add(ctx.pts(o), 10)),
        },
    }
    return Candidate(
        "UNDERDOG_HANGS_AROUND",
        o,
        [req, counters[0]],
        counters[1:],
        contradicting,
        shape,
        chain,
        f"{other_name} hangs around",
        (
            f"{lead_name} has the better efficiency profile, but {mechanism[counters[0]].lower()} "
            f"keeps {other_name} within one score."
        ),
    )


def _explosive_upset(ctx: _Ctx, s: str) -> Candidate | None:
    if not ctx.has(f"{_u(s)}_SUSTAINED_EFFICIENCY_ADVANTAGE"):
        return None
    o = _other(s)
    explosive = ctx.present(
        [f"{_u(o)}_PASS_EXPLOSIVE_ADVANTAGE", f"{_u(o)}_RUSH_EXPLOSIVE_ADVANTAGE", f"{_u(o)}_EXPLOSIVE_ADVANTAGE"]
    )
    if not explosive:
        return None
    lead_name, other_name = ctx.names[s], ctx.names[o]
    supporting = ctx.present(
        [
            "HIGH_VARIANCE_MATCHUP",
            f"{_u(o)}_HIGH_VARIANCE_OFFENSE",
            f"{_u(s)}_OFFENSE_TURNOVER_PRONE",
            f"{_u(o)}_DISRUPTION_ADVANTAGE",
        ]
    )
    contradicting = ctx.present([f"{_u(s)}_DEFENSIVE_CONTROL"]) + (
        [f"{_u(s)}_SUSTAINED_EFFICIENCY_ADVANTAGE"] if ctx.strong(f"{_u(s)}_SUSTAINED_EFFICIENCY_ADVANTAGE") else []
    )
    chain = [
        _step(f"{lead_name} wins more snaps than it loses", f"{_u(s)}_SUSTAINED_EFFICIENCY_ADVANTAGE"),
        _step(f"{other_name} creates explosive plays against a {lead_name} defense that allows them", *explosive),
        _step(f"{other_name} scores on fewer, faster possessions", explosive[0]),
        _step(
            f"{lead_name}'s per-snap edge does not convert into enough points to hold on", explosive[0], *supporting[:1]
        ),
    ]
    shape = {
        "winner_lean": _u(o),
        "margin_environment": f"{_u(o)}_BY_ONE_TO_TWO_SCORES",
        "total_environment": "NOT_STATED",
        f"{o}_scoring": "ABOVE_BASELINE",
        f"{s}_scoring": "NOT_STATED",
        "pace": "NOT_STATED",
        "bands": {
            "home_margin": _margin_band(o, 1, 14),
            "total_points": None,
            f"{o}_points": _band(_add(ctx.pts(o), 3), _add(ctx.pts(o), 21)),
            f"{s}_points": None,
        },
    }
    return Candidate(
        "EXPLOSIVE_UPSET",
        o,
        [f"{_u(s)}_SUSTAINED_EFFICIENCY_ADVANTAGE", explosive[0]],
        explosive[1:] + supporting,
        contradicting,
        shape,
        chain,
        f"{other_name} wins on explosive plays",
        f"{lead_name} wins more snaps, but {other_name}'s explosive plays against this defense flip the scoreboard.",
    )


def _turnover_disruption(ctx: _Ctx, d: str) -> Candidate | None:
    req = f"{_u(d)}_DISRUPTION_ADVANTAGE"
    if not ctx.has(req):
        return None
    o = _other(d)
    volatile = ctx.present(
        [f"{_u(o)}_OFFENSE_TURNOVER_PRONE", f"{_u(d)}_DEFENSE_TURNOVER_RELIANT", "HIGH_VARIANCE_MATCHUP"]
    )
    if not volatile:
        return None
    lead_name, other_name = ctx.names[d], ctx.names[o]
    contradicting = ctx.present([f"{_u(o)}_SUSTAINED_EFFICIENCY_ADVANTAGE", f"{_u(o)}_DEFENSIVE_CONTROL"])
    chain = [
        _step(f"{lead_name}'s front creates sacks and tackles for loss against {other_name}'s protection", req),
        _step(f"{other_name} faces long-yardage downs it does not convert", req),
        _step(f"negative plays and giveaways hand {lead_name} short fields", req, volatile[0]),
        _step(f"{lead_name} wins the possession battle on disruption rather than on sustained offense", req),
    ]
    shape = {
        "winner_lean": _u(d),
        "margin_environment": f"{_u(d)}_BY_ONE_TO_THREE_SCORES",
        "total_environment": "NOT_STATED",
        f"{o}_scoring": "BELOW_BASELINE",
        f"{d}_scoring": "NOT_STATED",
        "pace": "NOT_STATED",
        "bands": {
            "home_margin": _margin_band(d, 1, 21),
            "total_points": None,
            f"{o}_points": _band(0, _floor0(_sub(ctx.pts(o), 3))),
            f"{d}_points": None,
        },
    }
    return Candidate(
        "TURNOVER_DISRUPTION",
        d,
        [req, volatile[0]],
        volatile[1:],
        contradicting,
        shape,
        chain,
        f"{lead_name} wins on disruption",
        (
            f"{lead_name}'s pressure produces negative plays and short fields: a repeatable source of "
            f"{other_name}'s volatility."
        ),
    )


def _no_dominant_edge(ctx: _Ctx) -> bool:
    return not any(ctx.strong(f"{s}_SUSTAINED_EFFICIENCY_ADVANTAGE") for s in ("HOME", "AWAY"))


def _shootout(ctx: _Ctx) -> Candidate | None:
    if not _no_dominant_edge(ctx):
        return None
    required = ctx.present(["HIGH_SCORING_ENVIRONMENT", "BOTH_OFFENSES_EFFICIENT"])
    if not required:
        return None
    supporting = (
        ctx.present(
            [
                "HIGH_POSSESSION_ENVIRONMENT",
                "HOME_PASS_EXPLOSIVE_ADVANTAGE",
                "AWAY_PASS_EXPLOSIVE_ADVANTAGE",
                "HOME_EXPLOSIVE_ADVANTAGE",
                "AWAY_EXPLOSIVE_ADVANTAGE",
            ]
        )
        + required[1:]
    )
    contradicting = ctx.present(
        [
            "HOME_SUSTAINED_EFFICIENCY_ADVANTAGE",
            "AWAY_SUSTAINED_EFFICIENCY_ADVANTAGE",
            "LOW_POSSESSION_ENVIRONMENT",
            "BOTH_DEFENSES_CONTROL",
            "HOME_DEFENSIVE_CONTROL",
            "AWAY_DEFENSIVE_CONTROL",
        ]
    )
    H, A = ctx.names["home"], ctx.names["away"]
    chain = [
        _step(f"both offenses out-rate the defense in front of them ({H} and {A})", *required),
        _step("neither defense gets enough stops to separate", required[0]),
        _step("possessions trade scores", required[0], *ctx.present(["HIGH_POSSESSION_ENVIRONMENT"])),
        _step("the margin stays within one score while the total climbs", required[0]),
    ]
    t = ctx.total()
    shape = {
        "winner_lean": "NONE",
        "margin_environment": "ONE_SCORE",
        "total_environment": "ELEVATED",
        "home_scoring": "ABOVE_BASELINE",
        "away_scoring": "ABOVE_BASELINE",
        "pace": "MORE_POSSESSIONS" if ctx.has("HIGH_POSSESSION_ENVIRONMENT") else "NOT_STATED",
        "bands": {
            "home_margin": [-ONE_SCORE, ONE_SCORE],
            "total_points": _band(_add(t, 4), _add(t, 28)),
            "home_points": _band(_add(ctx.pts("home"), 2), _add(ctx.pts("home"), 17)),
            "away_points": _band(_add(ctx.pts("away"), 2), _add(ctx.pts("away"), 17)),
        },
    }
    return Candidate(
        "COMPETITIVE_SHOOTOUT",
        None,
        required[:1],
        supporting,
        contradicting,
        shape,
        chain,
        "Competitive shootout",
        f"Both offenses hold the upper hand against the defense they face; {H} and {A} trade scores in a close game.",
    )


def _grind(ctx: _Ctx) -> Candidate | None:
    if not _no_dominant_edge(ctx):
        return None
    required = ctx.present(["LOW_SCORING_ENVIRONMENT", "BOTH_DEFENSES_CONTROL", "LOW_POSSESSION_ENVIRONMENT"])
    if not required:
        return None
    supporting = required[1:] + ctx.present(["HOME_DEFENSIVE_CONTROL", "AWAY_DEFENSIVE_CONTROL"])
    contradicting = ctx.present(
        [
            "HIGH_POSSESSION_ENVIRONMENT",
            "HIGH_SCORING_ENVIRONMENT",
            "HOME_PASS_EXPLOSIVE_ADVANTAGE",
            "AWAY_PASS_EXPLOSIVE_ADVANTAGE",
            "HOME_SUSTAINED_EFFICIENCY_ADVANTAGE",
            "AWAY_SUSTAINED_EFFICIENCY_ADVANTAGE",
        ]
    )
    t = ctx.total()
    # Each step says only what its cited finding says: a grind required by
    # snap volume alone must not claim the defenses out-rate the offenses.
    defensive = ctx.present(["LOW_SCORING_ENVIRONMENT", "BOTH_DEFENSES_CONTROL"])
    chain = []
    if defensive:
        chain.append(_step("neither offense out-rates the defense in front of it", *defensive))
        chain.append(_step("drives stall before scoring range; field position decides", defensive[0]))
    if ctx.has("LOW_POSSESSION_ENVIRONMENT"):
        chain.append(
            _step(
                "both teams run fewer snaps than an average FBS game, capping scoring chances",
                "LOW_POSSESSION_ENVIRONMENT",
            )
        )
    chain.append(_step("few possessions and few points keep the margin inside one score", *required))
    summary = (
        "Neither offense has the upper hand; a low-scoring game stays within one score."
        if defensive
        else (
            "Fewer snaps than an average FBS game cap the scoring chances; "
            "the game stays low-volume and within one score."
        )
    )
    shape = {
        "winner_lean": "NONE",
        "margin_environment": "ONE_SCORE",
        "total_environment": "SUPPRESSED",
        "home_scoring": "BELOW_BASELINE",
        "away_scoring": "BELOW_BASELINE",
        "pace": "FEWER_POSSESSIONS" if ctx.has("LOW_POSSESSION_ENVIRONMENT") else "NOT_STATED",
        "bands": {
            "home_margin": [-ONE_SCORE, ONE_SCORE],
            "total_points": _band(_floor0(_sub(t, 28)), _sub(t, 4)),
            "home_points": _band(_floor0(_sub(ctx.pts("home"), 17)), _sub(ctx.pts("home"), 2)),
            "away_points": _band(_floor0(_sub(ctx.pts("away"), 17)), _sub(ctx.pts("away"), 2)),
        },
    }
    return Candidate(
        "COMPETITIVE_GRIND",
        None,
        required[:1],
        supporting,
        contradicting,
        shape,
        chain,
        "Competitive grind",
        summary,
    )


def _tossup(ctx: _Ctx) -> Candidate | None:
    if not ctx.has("EVEN_MATCHUP"):
        return None
    supporting = ctx.present(["LOW_POSSESSION_ENVIRONMENT"])
    contradicting = ctx.present(
        [
            "HOME_SUSTAINED_EFFICIENCY_ADVANTAGE",
            "AWAY_SUSTAINED_EFFICIENCY_ADVANTAGE",
            "HOME_SCORING_ADVANTAGE",
            "AWAY_SCORING_ADVANTAGE",
            "HIGH_VARIANCE_MATCHUP",
        ]
    )
    H, A = ctx.names["home"], ctx.names["away"]
    chain = [
        _step(f"neither {H} nor {A} owns the efficiency or scoring matchup", "EVEN_MATCHUP"),
        _step("possessions trade evenly; no unit consistently wins its matchup", "EVEN_MATCHUP"),
        _step("the game is decided late, inside one score", "EVEN_MATCHUP"),
    ]
    shape = {
        "winner_lean": "NONE",
        "margin_environment": "ONE_SCORE",
        "total_environment": "NOT_STATED",
        "home_scoring": "NOT_STATED",
        "away_scoring": "NOT_STATED",
        "pace": "NOT_STATED",
        "bands": {
            "home_margin": [-ONE_SCORE, ONE_SCORE],
            "total_points": None,
            "home_points": None,
            "away_points": None,
        },
    }
    return Candidate(
        "COMPETITIVE_TOSSUP",
        None,
        ["EVEN_MATCHUP"],
        supporting,
        contradicting,
        shape,
        chain,
        "Even matchup, one-score game",
        f"No unit-level edge separates {H} and {A}; the game stays inside one score.",
    )


def _pace_over(ctx: _Ctx) -> Candidate | None:
    if not ctx.has("HIGH_POSSESSION_ENVIRONMENT"):
        return None
    supporting = ctx.present(
        ["HIGH_SCORING_ENVIRONMENT", "BOTH_OFFENSES_EFFICIENT", "HOME_EXPLOSIVE_ADVANTAGE", "AWAY_EXPLOSIVE_ADVANTAGE"]
    )
    contradicting = ctx.present(["BOTH_DEFENSES_CONTROL", "LOW_SCORING_ENVIRONMENT"])
    t = ctx.total()
    chain = [
        _step("both teams play at a snap count above the FBS norm", "HIGH_POSSESSION_ENVIRONMENT"),
        _step("extra possessions create extra scoring chances for both sides", "HIGH_POSSESSION_ENVIRONMENT"),
        _step("the total rises with volume, whoever wins", "HIGH_POSSESSION_ENVIRONMENT", *supporting[:1]),
    ]
    shape = {
        "winner_lean": "NONE",
        "margin_environment": "NOT_STATED",
        "total_environment": "ELEVATED",
        "home_scoring": "NOT_STATED",
        "away_scoring": "NOT_STATED",
        "pace": "MORE_POSSESSIONS",
        "bands": {
            "home_margin": None,
            "total_points": _band(_add(t, 3), _add(t, 28)),
            "home_points": None,
            "away_points": None,
        },
    }
    return Candidate(
        "PACE_DRIVEN_OVER",
        None,
        ["HIGH_POSSESSION_ENVIRONMENT"],
        supporting,
        contradicting,
        shape,
        chain,
        "Pace-driven scoring",
        "Snap volume above the FBS norm on both sides pushes the total up regardless of who wins.",
    )


def _suppression(ctx: _Ctx) -> Candidate | None:
    if not ctx.has("BOTH_DEFENSES_CONTROL"):
        return None
    supporting = ctx.present(["LOW_SCORING_ENVIRONMENT", "LOW_POSSESSION_ENVIRONMENT"])
    contradicting = ctx.present(
        [
            "HIGH_POSSESSION_ENVIRONMENT",
            "HIGH_SCORING_ENVIRONMENT",
            "HOME_EXPLOSIVE_ADVANTAGE",
            "AWAY_EXPLOSIVE_ADVANTAGE",
        ]
    )
    t = ctx.total()
    chain = [
        _step("both defenses out-rate the offense they face", "BOTH_DEFENSES_CONTROL"),
        _step("scoring drives require long, low-percentage sequences", "BOTH_DEFENSES_CONTROL"),
        _step("the total stays below the scoring baseline whoever wins", "BOTH_DEFENSES_CONTROL", *supporting[:1]),
    ]
    shape = {
        "winner_lean": "NONE",
        "margin_environment": "NOT_STATED",
        "total_environment": "SUPPRESSED",
        "home_scoring": "BELOW_BASELINE",
        "away_scoring": "BELOW_BASELINE",
        "pace": "NOT_STATED",
        "bands": {
            "home_margin": None,
            "total_points": _band(_floor0(_sub(t, 30)), _sub(t, 6)),
            "home_points": None,
            "away_points": None,
        },
    }
    return Candidate(
        "DEFENSIVE_SUPPRESSION",
        None,
        ["BOTH_DEFENSES_CONTROL"],
        supporting,
        contradicting,
        shape,
        chain,
        "Defenses suppress scoring",
        "Both defenses hold the upper hand; scoring stays below the opponent-adjusted baseline.",
    )


def _add(x: float | None, d: float) -> float | None:
    return None if x is None else x + d


def _sub(x: float | None, d: float) -> float | None:
    return None if x is None else x - d


def _floor0(x: float | None) -> float | None:
    return None if x is None else max(0.0, x)


def candidates(findings: list[dict[str, Any]], names: dict[str, str], baseline: dict[str, Any]) -> list[Candidate]:
    ctx = _Ctx(findings, names, baseline)
    out: list[Candidate] = []
    for side in ("home", "away"):
        for builder in (_control, _pulls_away, _hangs_around, _explosive_upset, _turnover_disruption):
            cand = builder(ctx, side)
            if cand is not None:
                out.append(cand)
    environment = [c for c in (_shootout(ctx), _grind(ctx)) if c is not None]
    out.extend(environment)
    # The toss-up is the fallback for an even game that no scoring-environment
    # script describes; when one does, it says strictly more.
    if not environment:
        tossup = _tossup(ctx)
        if tossup is not None:
            out.append(tossup)
    for builder in (_pace_over, _suppression):
        cand = builder(ctx)
        if cand is not None:
            out.append(cand)
    for cand in out:
        present = cand.required + cand.supporting
        cand.evidence = sum(_weight(ctx.by_code[c]) for c in dict.fromkeys(present)) - sum(
            _weight(ctx.by_code[c]) for c in dict.fromkeys(cand.contradicting)
        )
    return out


def _conflicts(primary: Candidate, other: Candidate) -> bool:
    """Would `other` happening break `primary`?

    A different winner, an opposite scoring environment, or -- for the same
    winner -- a margin band that sits BELOW the primary's (the leader wins by
    less than the primary needs). A bigger version of the same thesis
    ("pulls away" beside "controls") is an alternate, never a danger."""
    pw, ow = primary.shape["winner_lean"], other.shape["winner_lean"]
    if pw != "NONE" and ow != "NONE" and pw != ow:
        return True
    pt, ot = primary.shape["total_environment"], other.shape["total_environment"]
    if {pt, ot} == {"ELEVATED", "SUPPRESSED"}:
        return True
    pb, ob = primary.shape["bands"].get("home_margin"), other.shape["bands"].get("home_margin")
    if not (pb and ob):
        return False
    if pw == "NONE":
        # A one-score primary is broken by a script that mostly leaves its band.
        overlap = max(0, min(pb[1], ob[1]) - max(pb[0], ob[0]))
        return overlap <= 0.25 * (ob[1] - ob[0])
    sign = 1 if pw == "HOME" else -1
    p_lo = min(sign * pb[0], sign * pb[1])
    o_mid = sign * (ob[0] + ob[1]) / 2
    return o_mid < p_lo


def _sort_key(c: Candidate) -> tuple[float, int, str]:
    return (-c.evidence, ARCHETYPE_ORDER.index(c.archetype), c.lead or "")


#: A DANGER script needs its REQUIRED findings present, but may be net
#: contradicted down to this evidence score: it is the plausible path that
#: breaks the primary, not a second endorsement of it.
DANGER_MIN_EVIDENCE = -1.0


def select(cands: list[Candidate]) -> list[tuple[str, Candidate]]:
    ordered = sorted(cands, key=_sort_key)
    pool = [c for c in ordered if c.evidence > 0]
    if not pool:
        return []
    primary = pool[0]
    chosen: list[tuple[str, Candidate]] = [(PRIMARY, primary)]
    rest = [c for c in pool[1:] if not _excluded(primary, c)]
    danger_pool = [
        c for c in ordered if c is not primary and c.evidence >= DANGER_MIN_EVIDENCE and not _excluded(primary, c)
    ]
    danger = next((c for c in danger_pool if _conflicts(primary, c)), None)
    others = [c for c in rest if c is not danger]
    picked: list[Candidate] = [primary]
    for role in (SECONDARY, ALTERNATE):
        nxt = next((c for c in others if all(not _excluded(p, c) for p in picked)), None)
        if nxt is None:
            break
        chosen.append((role, nxt))
        picked.append(nxt)
        others.remove(nxt)
    if danger is not None and all(not _excluded(p, danger) for p in picked):
        chosen.append((DANGER, danger))
    return chosen[:MAX_SCRIPTS]


def _excluded(a: Candidate, b: Candidate) -> bool:
    if a.archetype == b.archetype and a.lead == b.lead:
        return True
    return any({a.archetype, b.archetype} == set(group) for group in EXCLUSIVE_GROUPS)


def _script_id(event_id: str, cand: Candidate) -> str:
    raw = f"{event_id}|{cand.archetype}|{cand.lead or 'game'}"
    return "scr_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def build_scripts(
    event_id: str,
    findings: list[dict[str, Any]],
    names: dict[str, str],
    baseline: dict[str, Any],
    data_confidence: str,
) -> dict[str, Any]:
    all_candidates = candidates(findings, names, baseline)
    chosen = select(all_candidates)
    scripts = []
    for rank, (role, cand) in enumerate(chosen, start=1):
        scripts.append(
            {
                "script_id": _script_id(event_id, cand),
                "rank": rank,
                "role": role,
                "archetype": cand.archetype,
                "lead_side": cand.lead,
                "title": cand.title,
                "summary": cand.summary,
                "causal_chain": cand.chain,
                "required_findings": list(dict.fromkeys(cand.required)),
                "supporting_findings": list(dict.fromkeys(cand.supporting)),
                "contradicting_findings": list(dict.fromkeys(cand.contradicting)),
                "evidence_score": cand.evidence,
                "outcome_shape": {**cand.shape, "band_authority": band_authority(cand.shape["bands"])},
                "data_confidence": data_confidence,
                "probability": None,
            }
        )
    considered = [
        {
            "archetype": c.archetype,
            "lead_side": c.lead,
            "evidence_score": c.evidence,
            "selected": any(c is chosen_c for _, chosen_c in chosen),
        }
        for c in sorted(all_candidates, key=_sort_key)
    ]
    if not scripts:
        status = "NO_SCRIPT_CLEARED_EVIDENCE"
    elif len(scripts) == 1:
        status = "SINGLE_SCRIPT"
    else:
        status = "SCRIPTS_GENERATED"
    return {
        "status": status,
        "scripts": scripts,
        "candidates_considered": considered,
        "band_policy": {
            "home_margin": ARCHETYPE_DEFINITION,
            "total_points": SCORING_BAND_AUTHORITY,
            "home_points": SCORING_BAND_AUTHORITY,
            "away_points": SCORING_BAND_AUTHORITY,
            "rule": (
                "Margin bands define the archetype. Scoring bands are drawn around the descriptive, uncalibrated "
                "scoring baseline: research context only, never grounds for calling a total or team-total "
                "contract supported, until the scoring-band promotion gate passes."
            ),
        },
        "ranking_rule": (
            "evidence = weight(required + supporting findings) - weight(contradicting findings), STRONG=2, "
            "MODERATE=1; PRIMARY = best evidence; DANGER = best remaining script that breaks the primary; "
            "SECONDARY/ALTERNATE = next best. probability is null in V1 (not yet calibrated)."
        ),
    }
