"""V1 (production, as frozen) versus the V2 candidate on one block. PURE.

V1 is read from the same frozen pregame records (the published scripts) and
scored with the Wave 1 analyses; V2 from its claims. Neither is re-run or
re-parameterized here. Pre-registered comparison metrics: protocol section 10.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from cfb_edge_finder.archetype_research import analysis as W1
from cfb_edge_finder.archetype_research.stats import rate
from cfb_edge_finder.archetype_research.v2 import criteria as K
from cfb_edge_finder.archetype_research.v2.evaluate import _heterogeneity, eligible, margin, side_margin

V1_DIRECTIONAL = ("HOME_CONTROL", "AWAY_CONTROL", "FAVORITE_PULLS_AWAY", "TURNOVER_DISRUPTION", "EXPLOSIVE_UPSET")


def _v1_stable(ident: dict[str, Any]) -> list[str]:
    out = []
    for arch, b in ident.items():
        if arch.startswith("_") or not b.get("generated"):
            continue
        ci = b.get("lift_ci95_bootstrap")
        seasons = [v["lift"] for v in b["by_season"].values() if v["lift"] is not None and v["generated"] >= 15]
        if (
            ci
            and ci[0] > 1
            and len(seasons) >= K.MIN_EVALUABLE_SEASONS
            and sum(x > 1 for x in seasons) >= K.SEASONS_REQUIRED
        ):
            out.append(arch)
    return out


def compare(records: list[dict[str, Any]], v2_eval: dict[str, Any], v2_criteria: dict[str, Any]) -> dict[str, Any]:
    games = eligible(records)
    n = len(games)
    ident = W1.identification(records)
    scripts = [(r, s) for r in games for s in r["pregame"]["scripts"]]
    # V1 redundancy: PULLS_AWAY co-published with a CONTROL script for the same side
    pulls = [(r, s) for r, s in scripts if s["archetype"] == "FAVORITE_PULLS_AWAY"]
    co = sum(
        any(x["archetype"] == f"{s['lead_side'].upper()}_CONTROL" for x in r["pregame"]["scripts"]) for r, s in pulls
    )
    # V1 directional accuracy: PRIMARY scripts with a winner lean
    prim = [
        (r, s) for r, s in scripts if s["role"] == "PRIMARY" and s["outcome_shape"]["winner_lean"] in ("HOME", "AWAY")
    ]
    prim_wins = sum(side_margin(r, s["outcome_shape"]["winner_lean"].lower()) > 0 for r, s in prim)
    # V1 one-score claims: any published script stating the +/-8 band
    one_v1 = [
        r
        for r in games
        if any((s["outcome_shape"]["bands"] or {}).get("home_margin") == [-8, 8] for s in r["pregame"]["scripts"])
    ]
    one_base = sum(abs(margin(r)) <= K.ONE_SCORE for r in games) / n
    v1_one = sum(abs(margin(r)) <= K.ONE_SCORE for r in one_v1)
    se = W1.scoring_environments(records)
    marg = W1.margins(records)
    v2c = v2_eval["control"]["tiers"]
    ctl_n = sum(v2c[t]["pooled"]["n"] for t in K.CONTROL_TIERS)
    ctl_w = sum(v2c[t]["pooled"]["win_rate"]["hits"] for t in K.CONTROL_TIERS if v2c[t]["pooled"]["n"])
    cov_err = {
        t: {
            k: round(abs(v2c[t]["interval_coverage"][k]["rate"] - (0.5 if k == "central_50" else 0.8)), 4)
            for k in K.INTERVALS
        }
        for t in K.CONTROL_TIERS
        if v2c[t].get("interval_coverage")
    }
    env = v2_eval["environments"]
    clo = v2_eval["closeness"]["variants"]
    nc = v2_eval["no_claim"]
    return {
        "games": n,
        "redundancy": {
            "v1_pulls_away_co_published_with_same_side_control": rate(co, len(pulls)),
            "v1_scripts_per_game": round(len(scripts) / n, 3),
            "v2_control_tiers_mutually_exclusive": True,
            "v2_closeness_union_overlapping_moderate_control": rate(
                sum(
                    1
                    for r in games
                    if r["v2"]["closeness"]["CLOSENESS_NARROW"] and (r["v2"]["control"] or {}).get("tier") == "MODERATE"
                ),
                sum(1 for r in games if r["v2"]["closeness"]["CLOSENESS_NARROW"]),
            ),
        },
        "coverage": {
            "v1_any_script": rate(sum(1 for r in games if r["pregame"]["scripts"]), n),
            "v1_no_script": rate(sum(1 for r in games if not r["pregame"]["scripts"]), n),
            "v2_any_claim": nc["at_least_one_v2_claim"]["share"],
            "v2_no_claim": nc["no_v2_claim_at_all"]["share"],
            "v2_directional_claim": nc["directional_claim"]["share"],
        },
        "directional_accuracy": {
            "v1_primary_with_winner_lean": rate(prim_wins, len(prim)),
            "v2_control_any_tier": rate(ctl_w, ctl_n),
        },
        "stable_lift": {
            "v1_archetypes_with_stable_lift": _v1_stable(ident),
            "v2_families_passing": [f for f, ok in v2_criteria["summary"]["families_passed"].items() if ok],
        },
        "v1_confidence_audit": {
            a: {"by_confidence": b["by_confidence"], "by_evidence_bucket": b["by_evidence_bucket"]}
            for a, b in ident.items()
            if not a.startswith("_")
        },
        "v1_identification_lift": {
            a: {
                "generated": b["generated"],
                "ppv": b["label_match"]["rate"],
                "lift": b["lift"],
                "lift_ci95": b.get("lift_ci95_bootstrap"),
            }
            for a, b in ident.items()
            if not a.startswith("_")
        },
        "closeness": {
            "base_one_score_rate": round(one_base, 4),
            "v1_scripts_stating_one_score_band": {
                **rate(v1_one, len(one_v1)),
                "lift": round((v1_one / len(one_v1)) / one_base, 3) if one_v1 else None,
            },
            "v2_closeness": {
                "rate": clo["CLOSENESS"].get("one_score_le_8"),
                "lift": clo["CLOSENESS"].get("lift_vs_base"),
            },
            "v2_closeness_even": {
                "rate": clo["CLOSENESS_EVEN"].get("one_score_le_8"),
                "lift": clo["CLOSENESS_EVEN"].get("lift_vs_base"),
            },
        },
        "scoring_environment": {
            "v1": {
                a: {
                    "n": (se[a]["any_role"] or {}).get("n"),
                    "mean_total_percentile": (se[a]["any_role"] or {}).get("mean_total_percentile"),
                    "baseline_matched": (se[a]["any_role"] or {}).get("baseline_matched_percentile_difference"),
                }
                for a in ("COMPETITIVE_SHOOTOUT", "PACE_DRIVEN_OVER", "COMPETITIVE_GRIND", "DEFENSIVE_SUPPRESSION")
            },
            "v2": {
                a: {
                    "n": env[a]["n"],
                    "mean_total_percentile": env[a].get("mean_total_percentile"),
                    "baseline_matched": (env[a].get("baseline_matched") or {}).get("difference"),
                }
                for a in (
                    "ELEVATED_SCORING_ENVIRONMENT",
                    "SUPPRESSED_SCORING_ENVIRONMENT",
                    "PACE_HIGH",
                    "PACE_LOW",
                    "DEFENSIVE_SUPPRESSION",
                )
            },
        },
        "margin_description": {
            "v1_control_band_7_24_coverage": {
                a: marg[a]["pregame_any_role"].get("band_coverage") for a in ("HOME_CONTROL", "AWAY_CONTROL")
            },
            "v1_pulls_away_band_17_45_coverage": marg["FAVORITE_PULLS_AWAY"]["pregame_any_role"].get("band_coverage"),
            "v2_interval_abs_coverage_error": cov_err,
            "v2_interval_mean_abs_coverage_error": round(
                float(np.mean([v for d in cov_err.values() for v in d.values()])), 4
            )
            if cov_err
            else None,
        },
        "abstention": {
            "v1_no_script_rate": rate(sum(1 for r in games if not r["pregame"]["scripts"]), n),
            "v2_no_claim_rate": nc["no_v2_claim_at_all"]["share"],
            "v1_no_script_heterogeneity": _heterogeneity([r for r in games if not r["pregame"]["scripts"]]).get(
                "abs_margin_bucket_entropy"
            ),
            "v1_scripted_heterogeneity": _heterogeneity([r for r in games if r["pregame"]["scripts"]]).get(
                "abs_margin_bucket_entropy"
            ),
            "v2_no_claim_heterogeneity": nc["no_v2_claim_at_all"].get("abs_margin_bucket_entropy"),
            "v2_claim_heterogeneity": nc["at_least_one_v2_claim"].get("abs_margin_bucket_entropy"),
        },
    }
