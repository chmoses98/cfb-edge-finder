"""The pre-registered analyses (docs/ARCHETYPE_HISTORICAL_PROTOCOL.md sections 4-12). PURE.

Input: study records `{"pregame": <frozen record>, "pregame_hash": ..., "outcome": <taxonomy.score_game> | None}`.
Every function reads frozen findings/scripts and realized outcomes; none of
them calls or re-parameterizes a production builder.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any

import numpy as np

from cfb_edge_finder.archetype_research import BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED, MIN_SAMPLE
from cfb_edge_finder.archetype_research.stats import (
    bootstrap_lift,
    entropy,
    homogeneity,
    kmeans,
    quantiles,
    rate,
    wilson,
)
from cfb_edge_finder.archetype_research.taxonomy import (
    ALL_ARCHETYPES,
    ENVIRONMENT_LABELS,
    PRODUCTION_LABELS,
    label_match,
    oriented_side,
    other,
)
from cfb_edge_finder.scripting.scripts import ARCHETYPE_ORDER

ROLES = ("PRIMARY", "SECONDARY", "ALTERNATE", "DANGER")
TIERS = ("HIGH", "MEDIUM", "LOW")
MARGIN_BANDS = {
    "HOME_CONTROL": (7, 24),
    "AWAY_CONTROL": (7, 24),
    "FAVORITE_PULLS_AWAY": (17, 45),
    "UNDERDOG_HANGS_AROUND": (-7, 8),
    "EXPLOSIVE_UPSET": (1, 14),
    "TURNOVER_DISRUPTION": (1, 21),
}
SCORING_ARCHETYPES = {
    "COMPETITIVE_SHOOTOUT": "ELEVATED",
    "PACE_DRIVEN_OVER": "ELEVATED",
    "COMPETITIVE_GRIND": "SUPPRESSED",
    "DEFENSIVE_SUPPRESSION": "SUPPRESSED",
}


# ------------------------------------------------------------------ helpers


def eligible(records: list[dict]) -> list[dict]:
    return [r for r in records if r["pregame"]["eligibility"] is None and r["outcome"] is not None]


def scripts_of(r: dict, archetype: str, role: str | None = None) -> list[tuple[dict, dict]]:
    """(frozen script, its outcome score) pairs of one archetype in one game."""
    pairs = zip(r["pregame"]["scripts"], r["outcome"]["scripts"], strict=True)
    return [(s, o) for s, o in pairs if s["archetype"] == archetype and (role is None or s["role"] == role)]


def realized(r: dict) -> dict:
    return r["outcome"]["realized"]


def finding(pre: dict, code: str) -> dict | None:
    return next((f for f in pre["findings"] if f["code"] == code), None)


def has(pre: dict, code: str, strength: str | None = None) -> bool:
    f = finding(pre, code)
    return f is not None and (strength is None or f["strength"] == strength)


def candidate(pre: dict, archetype: str, side: str | None = None) -> dict | None:
    return next(
        (c for c in pre["candidates"] if c["archetype"] == archetype and (side is None or c["lead_side"] == side)),
        None,
    )


def evidence_bucket(score: float) -> str:
    return "<=1" if score <= 1 else "2" if score <= 2 else "3" if score <= 3 else ">=4"


def margin_for(side: str | None, r: dict) -> float:
    m = r["outcome"]["features"]["home_margin"]
    return m if side == "home" else -m if side == "away" else abs(m)


def base_indicator(archetype: str, r: dict, side_mix: dict[str, float] | None = None) -> float:
    """Realization of the archetype in this game, oriented as a script would orient it."""
    if archetype == "TURNOVER_DISRUPTION":
        mix = side_mix or {"home": 0.5, "away": 0.5}
        return sum(w * label_match(archetype, s, realized(r)) for s, w in mix.items())
    side = oriented_side(archetype, r["pregame"])
    if archetype in ("FAVORITE_PULLS_AWAY", "UNDERDOG_HANGS_AROUND", "EXPLOSIVE_UPSET") and side is None:
        return 0.0
    return float(label_match(archetype, side, realized(r)))


def _flag(n: int) -> str | None:
    return "INSUFFICIENT_SAMPLE" if n < MIN_SAMPLE else None


# ------------------------------------------------------------------ Question A


def taxonomy_quality(records: list[dict]) -> dict[str, Any]:
    games = eligible(records)
    n = len(games)
    seasons = sorted({r["pregame"]["season"] for r in games})
    out: dict[str, Any] = {"n_games": n}

    def labels_of(r: dict, env: bool) -> list[str]:
        rz = realized(r)
        return rz["labels"] + (rz["environment"] if env else [])

    for env in (False, True):
        key = "with_environment_descriptors" if env else "production_labels_only"
        counts = Counter(len(labels_of(r, env)) for r in games)
        out[key] = {
            "at_least_one": rate(sum(v for k, v in counts.items() if k >= 1), n),
            "exactly_one": rate(counts.get(1, 0), n),
            "multiple": rate(sum(v for k, v in counts.items() if k >= 2), n),
            "none_ambiguous": rate(counts.get(0, 0), n),
            "label_count_distribution": {str(k): v for k, v in sorted(counts.items())},
        }
    base = {}
    for label in ALL_ARCHETYPES:
        hits = sum(label in labels_of(r, True) for r in games)
        per = {}
        for season in seasons:
            sg = [r for r in games if r["pregame"]["season"] == season]
            per[str(season)] = rate(sum(label in labels_of(r, True) for r in sg), len(sg))
        base[label] = {
            **rate(hits, n),
            "by_season": per,
            "season_homogeneity": homogeneity([(v["hits"], v["n"]) for v in per.values()]),
        }
    base["AMBIGUOUS"] = rate(sum(realized(r)["ambiguous"] for r in games), n)
    out["base_rates"] = base
    out["primary_distribution"] = dict(Counter(realized(r)["primary"] for r in games).most_common())
    pair = {}
    for a in ALL_ARCHETYPES:
        for b in ALL_ARCHETYPES:
            if a >= b:
                continue
            both = sum(a in labels_of(r, True) and b in labels_of(r, True) for r in games)
            na = sum(a in labels_of(r, True) for r in games)
            nb = sum(b in labels_of(r, True) for r in games)
            if both:
                pair[f"{a}|{b}"] = {
                    "both": both,
                    "jaccard": round(both / (na + nb - both), 4),
                    "p_b_given_a": round(both / na, 4),
                    "p_a_given_b": round(both / nb, 4),
                }
    out["pairwise_overlap"] = pair
    sens = [r["outcome"]["realized_baseline_lead"] for r in games]
    out["sensitivity_baseline_lead"] = {
        "ambiguous": rate(sum(not s["labels"] for s in sens), n),
        "base_rates": {
            label: rate(sum(label in s["labels"] + s["environment"] for s in sens), n) for label in ALL_ARCHETYPES
        },
        "lead_agreement_with_reference": rate(
            sum(r["pregame"]["baseline_lead"] == r["pregame"]["reference_lead"] for r in games), n
        ),
    }
    explosive_known = sum(
        r["outcome"]["features"]["home"]["explosive_yard_share"] is not None
        and r["outcome"]["features"]["away"]["explosive_yard_share"] is not None
        for r in games
    )
    out["explosive_data_coverage"] = rate(explosive_known, n)
    return out


def game_shape_summary(records: list[dict]) -> dict[str, Any]:
    """Per-game descriptive features, aggregated (margin, total, pace, efficiency agreement)."""
    games = eligible(records)
    feats = [r["outcome"]["features"] for r in games]
    winner_eff = [
        f["efficiency_winner"] == ("home" if f["home_margin"] > 0 else "away") for f in feats if f["home_margin"]
    ]
    winner_sr = [
        f["success_rate_winner"] == ("home" if f["home_margin"] > 0 else "away")
        for f in feats
        if f["home_margin"] and f["success_rate_winner"]
    ]
    to_diff = [
        abs((f["home"]["turnovers"] or 0) - (f["away"]["turnovers"] or 0))
        for f in feats
        if f["home"]["turnovers"] is not None
    ]
    return {
        "abs_margin": quantiles([abs(f["home_margin"]) for f in feats]),
        "total_points": quantiles([f["total_points"] for f in feats]),
        "total_plays": quantiles([f["total_plays"] for f in feats]),
        "winner_won_efficiency": rate(sum(winner_eff), len(winner_eff)),
        "winner_won_success_rate": rate(sum(winner_sr), len(winner_sr)),
        "efficiency_basis": dict(Counter(f["efficiency_basis"] for f in feats)),
        "abs_turnover_margin": quantiles(to_diff),
        "one_score_games": rate(sum(abs(f["home_margin"]) <= 8 for f in feats), len(feats)),
        "reference_favorite_won": rate(
            sum(margin_for(r["pregame"]["reference_lead"], r) > 0 for r in games if r["pregame"]["reference_lead"]),
            sum(1 for r in games if r["pregame"]["reference_lead"]),
        ),
    }


# ------------------------------------------------------------------ Question B


def _side_mix(games: list[dict], archetype: str) -> dict[str, float]:
    sides = Counter(s["lead_side"] for r in games for s, _ in scripts_of(r, archetype))
    total = sum(sides.values())
    if not total:
        return {"home": 0.5, "away": 0.5}
    return {"home": sides.get("home", 0) / total, "away": sides.get("away", 0) / total}


def _ppv_block(games: list[dict], archetype: str, role: str | None = None) -> dict[str, Any]:
    pairs = [(r, s, o) for r in games for s, o in scripts_of(r, archetype, role)]
    n = len(pairs)
    mix = _side_mix(games, archetype)
    base = float(np.mean([base_indicator(archetype, r, mix) for r in games])) if games else 0.0
    hits = sum(o["label_match"] for _, _, o in pairs)
    out = {
        "generated": n,
        "label_match": rate(hits, n),
        "base_rate": round(base, 4),
        "lift": round((hits / n) / base, 3) if n and base else None,
        "sample_flag": _flag(n),
    }
    dc = [o["definition_check"] for _, _, o in pairs if o["definition_check"] is not None]
    out["definition_check"] = rate(sum(dc), len(dc))
    out["production_described"] = rate(sum(o["production_described"] for _, _, o in pairs), n)
    return out


def identification(records: list[dict]) -> dict[str, Any]:
    games = eligible(records)
    seasons = sorted({r["pregame"]["season"] for r in games})
    out: dict[str, Any] = {}
    for archetype in ARCHETYPE_ORDER:
        block = _ppv_block(games, archetype)
        mix = _side_mix(games, archetype)
        flagged = np.array([len(scripts_of(r, archetype)) for r in games], dtype=float)
        hit = np.array([sum(o["label_match"] for _, o in scripts_of(r, archetype)) for r in games], dtype=float)
        base_hit = np.array([base_indicator(archetype, r, mix) for r in games], dtype=float)
        block["lift_ci95_bootstrap"] = bootstrap_lift(flagged, hit, base_hit, BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED)
        block["by_role"] = {role: _ppv_block(games, archetype, role) for role in ROLES}
        # recall / specificity: realized (archetype, side) vs published (archetype, same side), any role
        realized_units, caught, caught_primary, negatives, quiet = 0, 0, 0, 0, 0
        for r in games:
            pubs = scripts_of(r, archetype)
            if archetype == "TURNOVER_DISRUPTION":
                sides = [s for s in ("home", "away") if label_match(archetype, s, realized(r))]
                is_real = bool(sides)
                hit_any = any(s["lead_side"] in sides for s, _ in pubs)
                hit_primary = any(s["lead_side"] in sides and s["role"] == "PRIMARY" for s, _ in pubs)
            else:
                side = oriented_side(archetype, r["pregame"])
                is_real = bool(base_indicator(archetype, r))
                hit_any = any(s["lead_side"] == side for s, _ in pubs) if pubs else False
                hit_primary = any(s["lead_side"] == side and s["role"] == "PRIMARY" for s, _ in pubs)
            if is_real:
                realized_units += 1
                caught += hit_any
                caught_primary += hit_primary
            else:
                negatives += 1
                quiet += not pubs
        block["recall_any_role"] = rate(caught, realized_units)
        block["recall_primary"] = rate(caught_primary, realized_units)
        block["specificity_any_role"] = rate(quiet, negatives)
        buckets: dict[str, list[bool]] = defaultdict(list)
        tiers: dict[str, list[bool]] = defaultdict(list)
        for r in games:
            for s, o in scripts_of(r, archetype):
                buckets[evidence_bucket(s["evidence_score"])].append(o["label_match"])
                tiers[r["pregame"]["confidence"]].append(o["label_match"])
        block["by_evidence_bucket"] = {k: rate(sum(v), len(v)) for k, v in sorted(buckets.items())}
        block["by_confidence"] = {k: rate(sum(tiers[k]), len(tiers[k])) for k in TIERS if tiers.get(k)}
        per = {}
        for season in seasons:
            sg = [r for r in games if r["pregame"]["season"] == season]
            per[str(season)] = _ppv_block(sg, archetype)
        block["by_season"] = per
        block["ppv_season_homogeneity"] = homogeneity(
            [(v["label_match"]["hits"], v["label_match"]["n"]) for v in per.values()]
        )
        lifts = [v["lift"] for v in per.values() if v["lift"] is not None and v["generated"] >= MIN_SAMPLE]
        block["stability"] = (
            "INSUFFICIENT_SAMPLE"
            if len(lifts) < 2
            else "UNSTABLE_DIRECTION"
            if min(lifts) < 1 < max(lifts)
            else "UNSTABLE_RATE"
            if (block["ppv_season_homogeneity"]["p"] or 1) < 0.05
            else "STABLE"
        )
        out[archetype] = block
    out["_status"] = {
        "games": len(games),
        "status_counts": dict(Counter(r["pregame"]["status"] for r in games)),
        "scripts_per_game": quantiles([len(r["pregame"]["scripts"]) for r in games]),
    }
    return out


def overall_scorecard(records: list[dict]) -> dict[str, Any]:
    """The production prospective-report headline metrics, replayed historically."""
    games = [r for r in eligible(records) if r["pregame"]["scripts"]]
    n = len(games)

    def any_of(r: dict, key: str, roles: tuple[str, ...] | None) -> bool:
        return any(o[key] for o in r["outcome"]["scripts"] if roles is None or o["role"] in roles)

    out = {}
    for key in ("label_match", "production_described"):
        out[key] = {
            "primary": rate(sum(any_of(r, key, ("PRIMARY",)) for r in games), n),
            "primary_or_secondary": rate(sum(any_of(r, key, ("PRIMARY", "SECONDARY")) for r in games), n),
            "any_script": rate(sum(any_of(r, key, None) for r in games), n),
        }
    return out


# ------------------------------------------------------------------ confusion


def confusion(records: list[dict]) -> dict[str, Any]:
    games = eligible(records)
    out = {}
    for archetype in ARCHETYPE_ORDER:
        rows = [(r, s) for r in games for s, _ in scripts_of(r, archetype, "PRIMARY")]
        if not rows:
            continue
        n = len(rows)
        prim = Counter(realized(r)["primary"] for r, _ in rows)
        incidence = Counter(label for r, _ in rows for label in set(realized(r)["labels"] + realized(r)["environment"]))
        entry = {
            "n": n,
            "sample_flag": _flag(n),
            "realized_primary": {k: round(v / n, 4) for k, v in prim.most_common()},
            "realized_primary_counts": dict(prim.most_common()),
            "label_incidence": {k: round(v / n, 4) for k, v in incidence.most_common()},
            "ambiguous": round(sum(realized(r)["ambiguous"] for r, _ in rows) / n, 4),
        }
        if archetype in MARGIN_BANDS:
            buckets = Counter()
            for r, s in rows:
                m = margin_for(s["lead_side"], r)
                buckets[
                    "lost_by_9+"
                    if m <= -9
                    else "lost_by_1-8"
                    if m < 0
                    else "won_by_1-6"
                    if m <= 6
                    else "won_by_7-16"
                    if m <= 16
                    else "won_by_17-24"
                    if m <= 24
                    else "won_by_25+"
                ] += 1
            entry["supported_side_margin_bucket"] = {k: round(v / n, 4) for k, v in sorted(buckets.items())}
        out[archetype] = entry
    return out


# ------------------------------------------------------------------ NO_SCRIPT


def no_script(records: list[dict]) -> dict[str, Any]:
    games = eligible(records)
    abstain = [r for r in games if r["pregame"]["status"] == "NO_SCRIPT_CLEARED_EVIDENCE"]
    scripted = [r for r in games if r["pregame"]["scripts"]]

    def profile(rs: list[dict]) -> dict[str, Any]:
        prim = Counter(realized(r)["primary"] for r in rs)
        return {
            "n": len(rs),
            "realized_primary": dict(prim.most_common()),
            "concentration": entropy(dict(prim)),
            "ambiguous": rate(sum(realized(r)["ambiguous"] for r in rs), len(rs)),
            "abs_margin": quantiles([abs(r["outcome"]["features"]["home_margin"]) for r in rs]),
            "total_points": quantiles([r["outcome"]["features"]["total_points"] for r in rs]),
            "one_score": rate(sum(abs(r["outcome"]["features"]["home_margin"]) <= 8 for r in rs), len(rs)),
            "reference_favorite_won": rate(
                sum(margin_for(r["pregame"]["reference_lead"], r) > 0 for r in rs if r["pregame"]["reference_lead"]),
                sum(1 for r in rs if r["pregame"]["reference_lead"]),
            ),
        }

    seasons = sorted({r["pregame"]["season"] for r in games})
    out = {
        "rate": rate(len(abstain), len(games)),
        "by_season": {
            str(s): rate(
                sum(1 for r in abstain if r["pregame"]["season"] == s),
                sum(1 for r in games if r["pregame"]["season"] == s),
            )
            for s in seasons
        },
        "by_confidence": {
            t: rate(
                sum(1 for r in abstain if r["pregame"]["confidence"] == t),
                sum(1 for r in games if r["pregame"]["confidence"] == t),
            )
            for t in TIERS
        },
        "no_script": profile(abstain),
        "scripted": profile(scripted),
        "within_tier": {
            t: {
                "no_script": profile([r for r in abstain if r["pregame"]["confidence"] == t]),
                "scripted": profile([r for r in scripted if r["pregame"]["confidence"] == t]),
            }
            for t in TIERS
        },
    }
    rejected = defaultdict(list)
    for r in abstain:
        cands = sorted(r["pregame"]["candidates"], key=lambda c: -c["evidence_score"])
        if cands:
            c = cands[0]
            rejected[c["archetype"]].append(label_match(c["archetype"], c["lead_side"], realized(r)))
    out["best_rejected_candidate"] = {
        "games_with_a_rejected_candidate": sum(len(v) for v in rejected.values()),
        "by_archetype": {k: rate(sum(v), len(v)) for k, v in sorted(rejected.items())},
    }
    return out


# ------------------------------------------------------------------ ablation


Cond = Callable[[dict], bool]


def _ablation_rows(
    games: list[dict], archetype: str, side_of: Callable[[dict], str | None], conds: list[tuple[str, Cond]]
) -> dict[str, Any]:
    units = [(r, side_of(r)) for r in games]
    base_units = [
        (r, s) for r, s in units if s is not None or archetype not in MARGIN_BANDS or archetype.endswith("CONTROL")
    ]
    hits_all = sum(label_match(archetype, s, realized(r)) for r, s in base_units)
    base = hits_all / len(base_units) if base_units else None
    out = {"A_base_rate": {**rate(hits_all, len(base_units))}}
    for name, cond in conds:
        sel = [(r, s) for r, s in base_units if cond(r)]
        h = sum(label_match(archetype, s, realized(r)) for r, s in sel)
        entry = {**rate(h, len(sel)), "sample_flag": _flag(len(sel))}
        entry["lift_vs_base"] = round((h / len(sel)) / base, 3) if sel and base else None
        out[name] = entry
    return out


def _published(archetype: str, role: str | None = None) -> Cond:
    return lambda r: any(True for _ in scripts_of(r, archetype, role))


def ablation(records: list[dict]) -> dict[str, Any]:
    games = eligible(records)
    out: dict[str, Any] = {}
    U = str.upper

    for side in ("home", "away"):
        arch = f"{U(side)}_CONTROL"
        req = f"{U(side)}_SUSTAINED_EFFICIENCY_ADVANTAGE"
        sign = 1 if side == "home" else -1
        conds: list[tuple[str, Cond]] = [
            (
                "B_efficiency_net_favours_side_any_size",
                lambda r, s=sign: (r["pregame"]["nets"]["sustained_efficiency"] or 0) * s > 0,
            ),
            ("B_required_finding_any_strength", lambda r, c=req: has(r["pregame"], c)),
            ("B_required_finding_MODERATE", lambda r, c=req: has(r["pregame"], c, "MODERATE")),
            ("B_required_finding_STRONG", lambda r, c=req: has(r["pregame"], c, "STRONG")),
            ("C_gate_cleared_candidate", lambda r, a=arch: candidate(r["pregame"], a) is not None),
            ("D_published_any_role", _published(arch)),
            ("D_published_PRIMARY", _published(arch, "PRIMARY")),
        ]
        for b in ("<=1", "2", "3", ">=4"):
            conds.append(
                (
                    f"D_published_evidence_{b}",
                    lambda r, a=arch, b=b: any(evidence_bucket(s["evidence_score"]) == b for s, _ in scripts_of(r, a)),
                )
            )
        out[arch] = _ablation_rows(games, arch, lambda r, s=side: s, conds)

    def lead(r: dict) -> str | None:
        return r["pregame"]["reference_lead"]

    def lead_has(stub: str, strength: str | None = None) -> Cond:
        return lambda r: lead(r) is not None and has(r["pregame"], f"{U(lead(r))}_{stub}", strength)

    def other_has(stub: str) -> Cond:
        return lambda r: lead(r) is not None and has(r["pregame"], f"{U(other(lead(r)))}_{stub}")

    sus = "SUSTAINED_EFFICIENCY_ADVANTAGE"
    conds = [
        ("B_lead_efficiency_advantage_any", lead_has(sus)),
        ("B_lead_efficiency_advantage_STRONG", lead_has(sus, "STRONG")),
    ]
    for edge in (
        "FINISHING_ADVANTAGE",
        "DISRUPTION_ADVANTAGE",
        "EXPLOSIVE_ADVANTAGE",
        "RUSH_ADVANTAGE",
        "PASS_ADVANTAGE",
    ):
        conds.append((f"B'_STRONG_plus_{edge}", lambda r, e=edge: lead_has(sus, "STRONG")(r) and lead_has(e)(r)))
    conds.append(
        (
            "B'_STRONG_plus_SCORING_ADVANTAGE_only_(support,not_qualifying)",
            lambda r: (
                lead_has(sus, "STRONG")(r)
                and lead_has("SCORING_ADVANTAGE")(r)
                and candidate(r["pregame"], "FAVORITE_PULLS_AWAY") is None
            ),
        )
    )
    conds += [
        ("C_gate_cleared_candidate", lambda r: candidate(r["pregame"], "FAVORITE_PULLS_AWAY") is not None),
        ("D_published_any_role", _published("FAVORITE_PULLS_AWAY")),
        ("D_published_PRIMARY", _published("FAVORITE_PULLS_AWAY", "PRIMARY")),
    ]
    out["FAVORITE_PULLS_AWAY"] = _ablation_rows(games, "FAVORITE_PULLS_AWAY", lead, conds)

    conds = [
        ("B_lead_efficiency_advantage_any", lead_has(sus)),
        ("B_lead_efficiency_advantage_MODERATE", lead_has(sus, "MODERATE")),
        ("B_lead_efficiency_advantage_STRONG", lead_has(sus, "STRONG")),
    ]
    counters = {
        "underdog_DISRUPTION_ADVANTAGE": other_has("DISRUPTION_ADVANTAGE"),
        "underdog_DEFENSIVE_CONTROL": other_has("DEFENSIVE_CONTROL"),
        "lead_OFFENSE_TURNOVER_PRONE": lead_has("OFFENSE_TURNOVER_PRONE"),
        "BOTH_DEFENSES_CONTROL": lambda r: has(r["pregame"], "BOTH_DEFENSES_CONTROL"),
        "NARROW_EFFICIENCY_GAP_resolved": lambda r: (
            has(r["pregame"], "NARROW_EFFICIENCY_GAP") and r["pregame"]["closeness_resolved"]["NARROW_EFFICIENCY_GAP"]
        ),
        "NARROW_EFFICIENCY_GAP_unresolved_(not_qualifying)": lambda r: (
            has(r["pregame"], "NARROW_EFFICIENCY_GAP")
            and not r["pregame"]["closeness_resolved"]["NARROW_EFFICIENCY_GAP"]
        ),
        "LOW_POSSESSION_ENVIRONMENT_(pace,not_qualifying)": lambda r: has(r["pregame"], "LOW_POSSESSION_ENVIRONMENT"),
    }
    for name, c in counters.items():
        conds.append((f"B'_lead_efficiency_plus_{name}", lambda r, c=c: lead_has(sus)(r) and c(r)))
    conds += [
        ("C_gate_cleared_candidate", lambda r: candidate(r["pregame"], "UNDERDOG_HANGS_AROUND") is not None),
        ("D_published_any_role", _published("UNDERDOG_HANGS_AROUND")),
        ("D_published_PRIMARY", _published("UNDERDOG_HANGS_AROUND", "PRIMARY")),
    ]
    out["UNDERDOG_HANGS_AROUND"] = _ablation_rows(games, "UNDERDOG_HANGS_AROUND", lambda r: other(lead(r)), conds)

    # Turnover disruption: unit = (game, disruption side)
    td: dict[str, Any] = {}
    unit_games = [(r, s) for r in games for s in ("home", "away")]
    hits_all = sum(label_match("TURNOVER_DISRUPTION", s, realized(r)) for r, s in unit_games)
    base = hits_all / len(unit_games) if unit_games else None
    td["A_base_rate_per_game_side"] = rate(hits_all, len(unit_games))
    td_conds = {
        "B_side_DISRUPTION_ADVANTAGE": lambda r, s: has(r["pregame"], f"{U(s)}_DISRUPTION_ADVANTAGE"),
        "B'_plus_opponent_OFFENSE_TURNOVER_PRONE": lambda r, s: (
            has(r["pregame"], f"{U(s)}_DISRUPTION_ADVANTAGE")
            and has(r["pregame"], f"{U(other(s))}_OFFENSE_TURNOVER_PRONE")
        ),
        "B'_plus_side_DEFENSE_TURNOVER_RELIANT": lambda r, s: (
            has(r["pregame"], f"{U(s)}_DISRUPTION_ADVANTAGE") and has(r["pregame"], f"{U(s)}_DEFENSE_TURNOVER_RELIANT")
        ),
        "B'_plus_HIGH_VARIANCE_MATCHUP": lambda r, s: (
            has(r["pregame"], f"{U(s)}_DISRUPTION_ADVANTAGE") and has(r["pregame"], "HIGH_VARIANCE_MATCHUP")
        ),
        "C_gate_cleared_candidate": lambda r, s: candidate(r["pregame"], "TURNOVER_DISRUPTION", s) is not None,
        "D_published_any_role": lambda r, s: any(x["lead_side"] == s for x, _ in scripts_of(r, "TURNOVER_DISRUPTION")),
        "B_volatility_finding_without_disruption_edge": lambda r, s: (
            not has(r["pregame"], f"{U(s)}_DISRUPTION_ADVANTAGE")
            and (
                has(r["pregame"], f"{U(other(s))}_OFFENSE_TURNOVER_PRONE")
                or has(r["pregame"], f"{U(s)}_DEFENSE_TURNOVER_RELIANT")
            )
        ),
    }
    for name, cond in td_conds.items():
        sel = [(r, s) for r, s in unit_games if cond(r, s)]
        h = sum(label_match("TURNOVER_DISRUPTION", s, realized(r)) for r, s in sel)
        td[name] = {
            **rate(h, len(sel)),
            "sample_flag": _flag(len(sel)),
            "lift_vs_base": round((h / len(sel)) / base, 3) if sel and base else None,
        }
    out["TURNOVER_DISRUPTION"] = td

    def no_strong(r: dict) -> bool:
        return not any(has(r["pregame"], f"{s}_{sus}", "STRONG") for s in ("HOME", "AWAY"))

    def g(code: str) -> Cond:
        return lambda r: has(r["pregame"], code)

    def resolved(r: dict) -> bool:
        return any(r["pregame"]["closeness_resolved"].values())

    env_specs = {
        "COMPETITIVE_SHOOTOUT": ["HIGH_SCORING_ENVIRONMENT", "BOTH_OFFENSES_EFFICIENT"],
        "COMPETITIVE_GRIND": ["LOW_SCORING_ENVIRONMENT", "BOTH_DEFENSES_CONTROL", "LOW_POSSESSION_ENVIRONMENT"],
    }
    for arch, reqs in env_specs.items():
        conds = [(f"B_{c}_present", g(c)) for c in reqs]
        conds.append(
            ("B_any_required_ignoring_no_strong_edge_rule", lambda r, q=reqs: any(has(r["pregame"], c) for c in q))
        )
        conds.append(("B_no_strong_efficiency_edge", no_strong))
        conds += [
            ("C_gate_cleared_candidate", lambda r, a=arch: candidate(r["pregame"], a) is not None),
            (
                "C_gate_cleared_with_resolved_closeness_(margin_stated)",
                lambda r, a=arch: candidate(r["pregame"], a) is not None and resolved(r),
            ),
            (
                "C_gate_cleared_without_closeness_(no_margin)",
                lambda r, a=arch: candidate(r["pregame"], a) is not None and not resolved(r),
            ),
            ("D_published_any_role", _published(arch)),
            ("D_published_PRIMARY", _published(arch, "PRIMARY")),
        ]
        out[arch] = _ablation_rows(games, arch, lambda r: None, conds)
        out[arch]["components"] = _env_components(games, arch)

    conds = [
        ("B_EVEN_MATCHUP_any", g("EVEN_MATCHUP")),
        (
            "B_EVEN_MATCHUP_resolved",
            lambda r: has(r["pregame"], "EVEN_MATCHUP") and r["pregame"]["closeness_resolved"]["EVEN_MATCHUP"],
        ),
        (
            "B_EVEN_MATCHUP_unresolved",
            lambda r: has(r["pregame"], "EVEN_MATCHUP") and not r["pregame"]["closeness_resolved"]["EVEN_MATCHUP"],
        ),
        ("B_NARROW_EFFICIENCY_GAP_any", g("NARROW_EFFICIENCY_GAP")),
        (
            "B_NARROW_EFFICIENCY_GAP_resolved",
            lambda r: (
                has(r["pregame"], "NARROW_EFFICIENCY_GAP")
                and r["pregame"]["closeness_resolved"]["NARROW_EFFICIENCY_GAP"]
            ),
        ),
        ("C_gate_cleared_candidate", lambda r: candidate(r["pregame"], "COMPETITIVE_TOSSUP") is not None),
        ("D_published_any_role", _published("COMPETITIVE_TOSSUP")),
        ("D_published_PRIMARY", _published("COMPETITIVE_TOSSUP", "PRIMARY")),
    ]
    out["COMPETITIVE_TOSSUP"] = _ablation_rows(games, "COMPETITIVE_TOSSUP", lambda r: None, conds)
    out["COMPETITIVE_TOSSUP"]["components"] = _env_components(games, "COMPETITIVE_TOSSUP")

    conds = [
        (
            "B_HIGH_POSSESSION_ENVIRONMENT_MODERATE",
            lambda r: has(r["pregame"], "HIGH_POSSESSION_ENVIRONMENT", "MODERATE"),
        ),
        ("B_HIGH_POSSESSION_ENVIRONMENT_STRONG", lambda r: has(r["pregame"], "HIGH_POSSESSION_ENVIRONMENT", "STRONG")),
        ("C_gate_cleared_candidate", lambda r: candidate(r["pregame"], "PACE_DRIVEN_OVER") is not None),
        (
            "C_plus_HIGH_SCORING_ENVIRONMENT_support",
            lambda r: (
                candidate(r["pregame"], "PACE_DRIVEN_OVER") is not None
                and has(r["pregame"], "HIGH_SCORING_ENVIRONMENT")
            ),
        ),
        ("D_published_any_role", _published("PACE_DRIVEN_OVER")),
        ("D_published_PRIMARY", _published("PACE_DRIVEN_OVER", "PRIMARY")),
    ]
    out["PACE_DRIVEN_OVER"] = _ablation_rows(games, "PACE_DRIVEN_OVER", lambda r: None, conds)
    out["PACE_DRIVEN_OVER"]["components"] = _env_components(games, "PACE_DRIVEN_OVER")

    conds = [
        ("B_BOTH_DEFENSES_CONTROL", g("BOTH_DEFENSES_CONTROL")),
        (
            "B_LOW_SCORING_ENVIRONMENT_without_BOTH_DEFENSES_CONTROL",
            lambda r: has(r["pregame"], "LOW_SCORING_ENVIRONMENT") and not has(r["pregame"], "BOTH_DEFENSES_CONTROL"),
        ),
        ("C_gate_cleared_candidate", lambda r: candidate(r["pregame"], "DEFENSIVE_SUPPRESSION") is not None),
        (
            "C_plus_LOW_SCORING_ENVIRONMENT_support",
            lambda r: (
                candidate(r["pregame"], "DEFENSIVE_SUPPRESSION") is not None
                and has(r["pregame"], "LOW_SCORING_ENVIRONMENT")
            ),
        ),
        ("D_published_any_role", _published("DEFENSIVE_SUPPRESSION")),
        ("D_published_PRIMARY", _published("DEFENSIVE_SUPPRESSION", "PRIMARY")),
    ]
    out["DEFENSIVE_SUPPRESSION"] = _ablation_rows(games, "DEFENSIVE_SUPPRESSION", lambda r: None, conds)
    out["DEFENSIVE_SUPPRESSION"]["components"] = _env_components(games, "DEFENSIVE_SUPPRESSION")

    out["EXPLOSIVE_UPSET"] = {
        "status": "DATA_UNAVAILABLE",
        "reason": "CFBD rows carry no explosive-play counts: the required *_EXPLOSIVE_ADVANTAGE findings cannot fire",
        "candidates_generated": sum(candidate(r["pregame"], "EXPLOSIVE_UPSET") is not None for r in games),
    }
    return out


def _env_components(games: list[dict], archetype: str) -> dict[str, Any]:
    """The two halves of a competitive/environment label, for flagged vs all games."""

    def one_score(r: dict) -> bool:
        return abs(r["outcome"]["features"]["home_margin"]) <= 8

    def above(r: dict) -> bool:
        return r["outcome"]["features"]["total_points"] > r["pregame"]["baseline"]["total_points"]

    flagged = [r for r in games if scripts_of(r, archetype)]
    out = {}
    for name, pred in (("one_score", one_score), ("total_above_baseline", above)):
        out[name] = {
            "all_games": rate(sum(pred(r) for r in games), len(games)),
            "published_any_role": rate(sum(pred(r) for r in flagged), len(flagged)),
        }
    return out


# ------------------------------------------------------------------ margins


def _margin_profile(margins: list[float], band: tuple[int, int] | None) -> dict[str, Any]:
    n = len(margins)
    out: dict[str, Any] = {**quantiles(margins), "sample_flag": _flag(n)}
    if not n:
        return out
    out["win_rate"] = rate(sum(m > 0 for m in margins), n)
    for k in (1, 7, 14, 21):
        out[f"p_margin_ge_{k}"] = round(sum(m >= k for m in margins) / n, 4)
    if band:
        inside = sum(band[0] <= m <= band[1] for m in margins)
        out["band"] = list(band)
        out["band_coverage"] = rate(inside, n)
    return out


def margins(records: list[dict]) -> dict[str, Any]:
    games = eligible(records)
    out: dict[str, Any] = {}
    for archetype in ARCHETYPE_ORDER:
        band = MARGIN_BANDS.get(archetype)
        entry: dict[str, Any] = {}
        for role_key, role in (("primary", "PRIMARY"), ("any_role", None)):
            ms = [margin_for(s["lead_side"], r) for r in games for s, _ in scripts_of(r, archetype, role)]
            entry[f"pregame_{role_key}"] = _margin_profile(ms, band if archetype in MARGIN_BANDS else (0, 8))
        if archetype in MARGIN_BANDS:
            ms = []
            for r in games:
                rz = realized(r)
                if archetype in rz["labels"]:
                    ms.append(margin_for(rz["sides"].get(archetype), r))
            entry["realized_label"] = _margin_profile(ms, band)
        else:
            entry["realized_label"] = _margin_profile(
                [
                    abs(r["outcome"]["features"]["home_margin"])
                    for r in games
                    if archetype in realized(r)["labels"] + realized(r)["environment"]
                ],
                None,
            )
        out[archetype] = entry
    out["EXPLORATORY_by_required_strength"] = _margins_by_strength(games)
    out["reference_favorite_all_games"] = _margin_profile(
        [margin_for(r["pregame"]["reference_lead"], r) for r in games if r["pregame"]["reference_lead"]], None
    )
    return out


def _margins_by_strength(games: list[dict]) -> dict[str, Any]:
    """POST-HOC EXPLORATORY (deviation D1): control/pulls-away margins split by the edge's strength."""
    out: dict[str, Any] = {}
    for archetype in ("HOME_CONTROL", "AWAY_CONTROL", "FAVORITE_PULLS_AWAY"):
        for strength in ("MODERATE", "STRONG"):
            ms = []
            for r in games:
                for s, _ in scripts_of(r, archetype):
                    f = finding(r["pregame"], f"{s['lead_side'].upper()}_SUSTAINED_EFFICIENCY_ADVANTAGE")
                    if f and f["strength"] == strength:
                        ms.append(margin_for(s["lead_side"], r))
            out[f"{archetype}|{strength}"] = _margin_profile(ms, MARGIN_BANDS[archetype])
    for side in ("home", "away"):
        arch = f"{side.upper()}_CONTROL"
        with_pull = [r for r in games if scripts_of(r, arch) and scripts_of(r, "FAVORITE_PULLS_AWAY")]
        without = [r for r in games if scripts_of(r, arch) and not scripts_of(r, "FAVORITE_PULLS_AWAY")]
        out[f"{arch}|with_PULLS_AWAY_also_published"] = _margin_profile(
            [margin_for(side, r) for r in with_pull], (7, 24)
        )
        out[f"{arch}|without_PULLS_AWAY"] = _margin_profile([margin_for(side, r) for r in without], (7, 24))
    return out


# ------------------------------------------------------------------ scoring environments


def _percentiles(games: list[dict]) -> dict[str, float]:
    """Within-season percentile of each game's actual total (mid-rank)."""
    out = {}
    by_season = defaultdict(list)
    for r in games:
        by_season[r["pregame"]["season"]].append(r)
    for rs in by_season.values():
        totals = np.array([r["outcome"]["features"]["total_points"] for r in rs], dtype=float)
        for r in rs:
            t = r["outcome"]["features"]["total_points"]
            out[r["pregame"]["game_id"]] = float(((totals < t).sum() + 0.5 * (totals == t).sum()) / totals.size)
    return out


def _baseline_quintile(games: list[dict]) -> dict[str, tuple[int, int]]:
    out = {}
    by_season = defaultdict(list)
    for r in games:
        by_season[r["pregame"]["season"]].append(r)
    for season, rs in by_season.items():
        b = np.array([r["pregame"]["baseline"]["total_points"] for r in rs], dtype=float)
        cuts = np.percentile(b, [20, 40, 60, 80])
        for r in rs:
            out[r["pregame"]["game_id"]] = (
                season,
                int(np.searchsorted(cuts, r["pregame"]["baseline"]["total_points"], side="right")),
            )
    return out


def scoring_environments(records: list[dict]) -> dict[str, Any]:
    games = eligible(records)
    pct = _percentiles(games)
    strata = _baseline_quintile(games)
    season_mean = {
        s: float(np.mean([r["outcome"]["features"]["total_points"] for r in games if r["pregame"]["season"] == s]))
        for s in {r["pregame"]["season"] for r in games}
    }
    out: dict[str, Any] = {}
    for archetype, direction in SCORING_ARCHETYPES.items():
        entry = {"direction": direction}
        for role_key, role in (("any_role", None), ("primary", "PRIMARY")):
            flagged = [r for r in games if scripts_of(r, archetype, role)]
            ids = {r["pregame"]["game_id"] for r in flagged}
            n = len(flagged)
            if not n:
                entry[role_key] = {"n": 0}
                continue
            p = [pct[r["pregame"]["game_id"]] for r in flagged]
            upper = direction == "ELEVATED"
            third = sum((x >= 2 / 3) if upper else (x < 1 / 3) for x in p)
            quart = sum((x >= 0.75) if upper else (x < 0.25) for x in p)
            diffs, weights = [], []
            for key in {strata[i] for i in ids}:
                inside = [r for r in games if strata[r["pregame"]["game_id"]] == key]
                f = [pct[r["pregame"]["game_id"]] for r in inside if r["pregame"]["game_id"] in ids]
                u = [pct[r["pregame"]["game_id"]] for r in inside if r["pregame"]["game_id"] not in ids]
                if f and u:
                    diffs.append(np.mean(f) - np.mean(u))
                    weights.append(len(f))
            band_hits = []
            for r in flagged:
                for s, _ in scripts_of(r, archetype, role):
                    bnd = s["outcome_shape"]["bands"].get("total_points")
                    if bnd:
                        band_hits.append(bnd[0] <= r["outcome"]["features"]["total_points"] <= bnd[1])
            entry[role_key] = {
                "n": n,
                "sample_flag": _flag(n),
                "mean_total_percentile": round(float(np.mean(p)), 4),
                "in_extreme_third": rate(third, n),
                "in_extreme_quartile": rate(quart, n),
                "mean_actual_minus_season_mean": round(
                    float(
                        np.mean(
                            [
                                r["outcome"]["features"]["total_points"] - season_mean[r["pregame"]["season"]]
                                for r in flagged
                            ]
                        )
                    ),
                    2,
                ),
                "mean_actual_minus_baseline": round(
                    float(
                        np.mean(
                            [
                                r["outcome"]["features"]["total_points"] - r["pregame"]["baseline"]["total_points"]
                                for r in flagged
                            ]
                        )
                    ),
                    2,
                ),
                "mean_baseline_minus_season_mean": round(
                    float(
                        np.mean(
                            [
                                r["pregame"]["baseline"]["total_points"] - season_mean[r["pregame"]["season"]]
                                for r in flagged
                            ]
                        )
                    ),
                    2,
                ),
                "baseline_matched_percentile_difference": round(float(np.average(diffs, weights=weights)), 4)
                if diffs
                else None,
                "uncalibrated_total_band_coverage": rate(sum(band_hits), len(band_hits)),
            }
        out[archetype] = entry
    out["_reference"] = {
        "extreme_third_rate_if_uninformative": 0.3333,
        "extreme_quartile_rate_if_uninformative": 0.25,
        "baseline_total_vs_actual_corr": round(
            float(
                np.corrcoef(
                    [r["pregame"]["baseline"]["total_points"] for r in games],
                    [r["outcome"]["features"]["total_points"] for r in games],
                )[0, 1]
            ),
            4,
        ),
    }
    return out


# ------------------------------------------------------------------ rolling origin


def rolling_origin(records: list[dict]) -> dict[str, Any]:
    games = eligible(records)
    seasons = sorted({r["pregame"]["season"] for r in games})
    out: dict[str, Any] = {}
    for i in range(1, len(seasons)):
        hist = [r for r in games if r["pregame"]["season"] < seasons[i]]
        test = [r for r in games if r["pregame"]["season"] == seasons[i]]
        rows = {}
        for archetype in ARCHETYPE_ORDER:
            h, t = _ppv_block(hist, archetype), _ppv_block(test, archetype)
            if not h["generated"] or not t["generated"]:
                continue
            ci = h["label_match"]["ci95"]
            rows[archetype] = {
                "history_ppv": h["label_match"]["rate"],
                "history_ci95": ci,
                "history_lift": h["lift"],
                "test_ppv": t["label_match"]["rate"],
                "test_n": t["generated"],
                "test_lift": t["lift"],
                "test_ppv_inside_history_ci": ci[0] <= t["label_match"]["rate"] <= ci[1] if ci else None,
                "test_lift_same_side_of_1": None
                if h["lift"] is None or t["lift"] is None
                else (h["lift"] > 1) == (t["lift"] > 1),
            }
        out[f"{seasons[0]}-{seasons[i - 1]}->{seasons[i]}"] = rows
    return out


# ------------------------------------------------------------------ exploratory clustering

CLUSTER_FEATURES = (
    "abs_margin",
    "total_points",
    "winner_success_diff",
    "winner_ypp_diff",
    "winner_turnover_edge",
    "winner_havoc_edge",
    "total_plays",
    "winner_ppo_diff",
)


def _cluster_row(r: dict) -> list[float] | None:
    f = r["outcome"]["features"]
    w = "home" if f["home_margin"] > 0 else "away"
    lo = "away" if w == "home" else "home"
    W, L = f[w], f[lo]
    vals = [
        abs(f["home_margin"]),
        f["total_points"],
        None if W["success_rate"] is None or L["success_rate"] is None else W["success_rate"] - L["success_rate"],
        None
        if W["yards_per_play"] is None or L["yards_per_play"] is None
        else W["yards_per_play"] - L["yards_per_play"],
        None if W["turnovers"] is None or L["turnovers"] is None else L["turnovers"] - W["turnovers"],
        None
        if W["sacks_tfl_made"] is None or L["sacks_tfl_made"] is None
        else W["sacks_tfl_made"] - L["sacks_tfl_made"],
        f["total_plays"],
        None
        if W["points_per_opportunity"] is None or L["points_per_opportunity"] is None
        else W["points_per_opportunity"] - L["points_per_opportunity"],
    ]
    return None if any(v is None for v in vals) else [float(v) for v in vals]


def exploratory_clusters(records: list[dict], k_max: int = 10) -> dict[str, Any]:
    """EXPLORATORY ONLY: recurring realized game shapes, and how the taxonomy covers them."""
    games = [r for r in eligible(records) if r["outcome"]["features"]["home_margin"] != 0]
    rows = [(r, _cluster_row(r)) for r in games]
    rows = [(r, v) for r, v in rows if v is not None]
    x = np.array([v for _, v in rows], dtype=float)
    mu, sd = x.mean(axis=0), x.std(axis=0)
    z = (x - mu) / np.where(sd > 0, sd, 1)
    inertia = {}
    fits = {}
    for k in range(2, k_max + 1):
        labels, cent, inert = kmeans(z, k, BOOTSTRAP_SEED)
        inertia[k], fits[k] = inert, (labels, cent)
    ks = sorted(inertia)
    second = {k: inertia[k - 1] - 2 * inertia[k] + inertia[k + 1] for k in ks[1:-1]}
    k_star = max(second, key=lambda k: second[k])
    labels, cent = fits[k_star]
    clusters = []
    for j in range(k_star):
        members = [rows[i][0] for i in range(len(rows)) if labels[i] == j]
        if not members:
            continue
        centroid = {name: round(float(cent[j][i] * sd[i] + mu[i]), 3) for i, name in enumerate(CLUSTER_FEATURES)}
        prim = Counter(realized(r)["primary"] for r in members)
        clusters.append(
            {
                "cluster": j,
                "n": len(members),
                "share": round(len(members) / len(rows), 4),
                "centroid_raw_units": centroid,
                "realized_primary": {k: round(v / len(members), 3) for k, v in prim.most_common()},
                "ambiguous": round(sum(realized(r)["ambiguous"] for r in members) / len(members), 4),
            }
        )
    clusters.sort(key=lambda c: -c["ambiguous"])
    return {
        "label": "EXPLORATORY -- not a replacement taxonomy",
        "n_games": len(rows),
        "features": list(CLUSTER_FEATURES),
        "inertia_by_k": {str(k): round(v, 1) for k, v in inertia.items()},
        "k_selected_by_second_difference": k_star,
        "clusters": clusters,
    }


def ambiguous_anatomy(records: list[dict]) -> dict[str, Any]:
    """Rule-based description of the games no production label covers (pre-registered §12)."""
    games = eligible(records)
    amb = [r for r in games if realized(r)["ambiguous"]]
    reasons = Counter()
    for r in amb:
        f = r["outcome"]["features"]
        m = f["home_margin"]
        w = "home" if m > 0 else "away"
        lead = r["pregame"]["reference_lead"]
        won_eff = f["efficiency_winner"] == w
        a = abs(m)
        if a <= 8:
            key = "one_score_without_label"
        elif not won_eff:
            key = f"winner_by_{'9-24' if a <= 24 else '25+'}_LOST_efficiency"
        elif a >= 25 and lead is not None and lead != w:
            key = "underdog_blowout_25+_won_efficiency"
        elif a >= 25 and lead is None:
            key = "blowout_25+_no_reference_lead"
        else:
            key = "other"
        reasons[key] += 1
    n = len(amb)
    turnovers = [
        (r["outcome"]["features"]["home"]["turnovers"] or 0) - (r["outcome"]["features"]["away"]["turnovers"] or 0)
        for r in amb
    ]
    return {
        "n_ambiguous": n,
        "share_of_games": round(n / len(games), 4) if games else None,
        "families": {k: {"n": v, "share_of_ambiguous": round(v / n, 4)} for k, v in reasons.most_common()},
        "abs_margin": quantiles([abs(r["outcome"]["features"]["home_margin"]) for r in amb]),
        "winner_turnover_edge": quantiles(
            [(-t if r["outcome"]["features"]["home_margin"] > 0 else t) for r, t in zip(amb, turnovers, strict=True)]
        ),
    }


def wilson_rate(hits: int, n: int) -> list[float] | None:  # re-export for the report
    return wilson(hits, n)


__all__ = [
    "ablation",
    "ambiguous_anatomy",
    "confusion",
    "eligible",
    "exploratory_clusters",
    "game_shape_summary",
    "identification",
    "margins",
    "no_script",
    "overall_scorecard",
    "rolling_origin",
    "scoring_environments",
    "taxonomy_quality",
    "PRODUCTION_LABELS",
    "ENVIRONMENT_LABELS",
]
