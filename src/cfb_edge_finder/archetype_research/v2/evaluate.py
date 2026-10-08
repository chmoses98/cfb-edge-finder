"""Evaluate frozen V2 claims against revealed outcomes. PURE. No fitting happens here.

`evaluate(records, frozen)` measures every pre-registered quantity on one
block of study records (development OR holdout, never pooled for model
selection). The only development-fitted inputs are the frozen CONTROL
intervals in `frozen`; they are read, never recomputed. `criteria` turns an
evaluation into the pre-registered pass/fail verdicts.

Reference quantities (base rates, within-season total percentiles,
baseline-total quintiles) are computed WITHIN the evaluated block, as the
protocol states: a lift compares a claim to its own block's unconditional
rate.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any

import numpy as np

from cfb_edge_finder.archetype_research.stats import entropy, homogeneity, quantiles, rate, wilson
from cfb_edge_finder.archetype_research.v2 import EXPLOSIVE_UPSET_STATUS, RESAMPLES, SEED
from cfb_edge_finder.archetype_research.v2 import criteria as K

Rec = dict[str, Any]


# ------------------------------------------------------------------ accessors


def eligible(records: list[Rec]) -> list[Rec]:
    return [r for r in records if r["pregame"]["eligibility"] is None and r.get("outcome") is not None]


def season(r: Rec) -> int:
    return int(r["pregame"]["season"])


def margin(r: Rec) -> float:
    return float(r["outcome"]["features"]["home_margin"])


def side_margin(r: Rec, side: str) -> float:
    return margin(r) if side == "home" else -margin(r)


def total(r: Rec) -> float:
    return float(r["outcome"]["features"]["total_points"])


def plays(r: Rec) -> float:
    return float(r["outcome"]["features"]["total_plays"])


def v2(r: Rec) -> dict[str, Any]:
    return r["v2"]


def control_tier(r: Rec) -> str | None:
    c = v2(r)["control"]
    return None if c is None else c["claim"]


def env_claim(r: Rec) -> dict[str, Any] | None:
    e = v2(r)["scoring_environment"]
    return e if e and e.get("claim") else None


# ------------------------------------------------------------------ statistics helpers


def _newcombe(h1: int, n1: int, h2: int, n2: int) -> list[float] | None:
    """Newcombe hybrid-score 95% interval for p1 - p2."""
    if not n1 or not n2:
        return None
    p1, p2 = h1 / n1, h2 / n2
    l1, u1 = wilson(h1, n1)
    l2, u2 = wilson(h2, n2)
    d = p1 - p2
    lo = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return [round(lo, 4), round(hi, 4)]


def _boot_mean_ci(values: list[float], seed: int) -> list[float] | None:
    if len(values) < 2:
        return None
    a = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    means = a[rng.integers(0, a.size, size=(RESAMPLES, a.size))].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return [round(float(lo), 4), round(float(hi), 4)]


def margin_distribution(margins: list[float]) -> dict[str, Any]:
    """The stored historical empirical distribution of a supported-side margin."""
    n = len(margins)
    out: dict[str, Any] = {"n": n}
    if not n:
        return out
    a = np.asarray(margins, dtype=float)
    q = np.percentile(a, K.QUANTILES)
    out.update({f"p{k}": round(float(v), 2) for k, v in zip(K.QUANTILES, q, strict=True)})
    out["mean"] = round(float(a.mean()), 2)
    out["win_rate"] = rate(int((a > 0).sum()), n)
    for t in K.MARGIN_THRESHOLDS:
        key = "p_margin_gt_0" if t == 0 else f"p_margin_ge_{t}"
        out[key] = round(float((a > 0).mean() if t == 0 else (a >= t).mean()), 4)
    return out


def _coverage(margins: list[float], interval: list[float]) -> dict[str, Any]:
    hits = sum(1 for m in margins if interval[0] <= m <= interval[1])
    return rate(hits, len(margins))


def _within_season_percentiles(games: list[Rec], key: Callable[[Rec], float]) -> dict[str, float]:
    out: dict[str, float] = {}
    by = defaultdict(list)
    for r in games:
        by[season(r)].append(r)
    for rs in by.values():
        vals = np.array([key(r) for r in rs], dtype=float)
        for r in rs:
            x = key(r)
            out[r["pregame"]["game_id"]] = float(((vals < x).sum() + 0.5 * (vals == x).sum()) / vals.size)
    return out


def _baseline_strata(games: list[Rec]) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    by = defaultdict(list)
    for r in games:
        by[season(r)].append(r)
    for s, rs in by.items():
        b = np.array([r["pregame"]["baseline"]["total_points"] for r in rs], dtype=float)
        cuts = np.percentile(b, [20, 40, 60, 80])
        for r in rs:
            out[r["pregame"]["game_id"]] = (
                s,
                int(np.searchsorted(cuts, r["pregame"]["baseline"]["total_points"], "right")),
            )
    return out


def _matched_difference(games: list[Rec], flagged: set[str], pct: dict[str, float], strata: dict) -> dict[str, Any]:
    """Weighted mean over strata of (flagged - unflagged) percentile, with a game bootstrap interval."""
    ids = [r["pregame"]["game_id"] for r in games]
    keys = sorted({strata[i] for i in ids})
    kidx = {k: j for j, k in enumerate(keys)}
    s = np.array([kidx[strata[i]] for i in ids])
    f = np.array([i in flagged for i in ids])
    p = np.array([pct[i] for i in ids])

    def stat(sel: np.ndarray) -> float | None:
        ss, ff, pp = s[sel], f[sel], p[sel]
        k = len(keys)
        nf = np.bincount(ss[ff], minlength=k)
        nu = np.bincount(ss[~ff], minlength=k)
        sf = np.bincount(ss[ff], weights=pp[ff], minlength=k)
        su = np.bincount(ss[~ff], weights=pp[~ff], minlength=k)
        ok = (nf > 0) & (nu > 0)
        if not ok.any():
            return None
        d = sf[ok] / nf[ok] - su[ok] / nu[ok]
        return float(np.average(d, weights=nf[ok]))

    point = stat(np.arange(len(ids)))
    if point is None:
        return {"difference": None, "ci95": None}
    rng = np.random.default_rng(SEED)
    boots = []
    for _ in range(RESAMPLES):
        v = stat(rng.integers(0, len(ids), size=len(ids)))
        if v is not None:
            boots.append(v)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"difference": round(point, 4), "ci95": [round(float(lo), 4), round(float(hi), 4)]}


def _season_rule(per: dict[str, dict[str, Any]], ok: Callable[[dict], bool], min_n: int) -> dict[str, Any]:
    evaluable = {s: v for s, v in per.items() if v["n"] >= min_n}
    passing = [s for s, v in evaluable.items() if ok(v)]
    return {
        "evaluable_seasons": sorted(evaluable),
        "passing_seasons": sorted(passing),
        "passes": len(evaluable) >= K.MIN_EVALUABLE_SEASONS and len(passing) >= K.SEASONS_REQUIRED,
        "inconclusive": len(evaluable) < K.MIN_EVALUABLE_SEASONS,
    }


# ------------------------------------------------------------------ CONTROL


def control(games: list[Rec], frozen: dict[str, Any] | None) -> dict[str, Any]:
    seasons = sorted({season(r) for r in games})
    base_win = {
        "home": rate(sum(margin(r) > 0 for r in games), len(games)),
        "away": rate(sum(margin(r) < 0 for r in games), len(games)),
    }
    out: dict[str, Any] = {"base_side_win_rate": base_win, "tiers": {}}
    for tier in K.CONTROL_TIERS:
        side = "home" if tier.startswith("HOME") else "away"
        sel = [r for r in games if control_tier(r) == tier]
        ms = [side_margin(r, side) for r in sel]
        dist = margin_distribution(ms)
        entry: dict[str, Any] = {"pooled": dist}
        if ms:
            entry["win_rate_lift"] = round(dist["win_rate"]["rate"] / base_win[side]["rate"], 3)
            entry["v1_control_band_7_24_coverage"] = _coverage(ms, list(K.V1_BANDS["CONTROL"]))
            entry["v1_pulls_away_band_17_45_coverage"] = _coverage(ms, list(K.V1_BANDS["FAVORITE_PULLS_AWAY"]))
        intervals = (frozen or {}).get("control", {}).get(tier, {}).get("intervals")
        if intervals and ms:
            entry["interval_coverage"] = {k: _coverage(ms, v) for k, v in intervals.items()}
        per = {}
        for s in seasons:
            sg = [r for r in games if season(r) == s]
            sm = [side_margin(r, side) for r in sg if control_tier(r) == tier]
            b = sum((margin(r) > 0) if side == "home" else (margin(r) < 0) for r in sg) / len(sg)
            row: dict[str, Any] = {
                "n": len(sm),
                "win_rate": round(sum(m > 0 for m in sm) / len(sm), 4) if sm else None,
                "base_side_win_rate": round(b, 4),
                "median_margin": float(np.median(sm)) if sm else None,
            }
            if intervals and sm:
                row["coverage"] = {k: _coverage(sm, v)["rate"] for k, v in intervals.items()}
            per[str(s)] = row
        entry["by_season"] = per
        entry["win_rate_season_homogeneity"] = homogeneity(
            [(round(v["win_rate"] * v["n"]), v["n"]) for v in per.values() if v["n"]]
        )
        out["tiers"][tier] = entry
    order = {}
    for side in ("HOME", "AWAY"):
        mod, strong = out["tiers"][f"{side}_CONTROL_MODERATE"], out["tiers"][f"{side}_CONTROL_STRONG"]
        pm, ps = mod["pooled"], strong["pooled"]
        seasons_ok = []
        for s in seasons:
            a, b = mod["by_season"][str(s)], strong["by_season"][str(s)]
            if a["n"] >= K.SEASON_MIN_N["control"] and b["n"] >= K.SEASON_MIN_N["control"]:
                seasons_ok.append((s, b["win_rate"] > a["win_rate"]))
        order[side] = {
            "strong_minus_moderate_win_rate": None
            if not pm["n"] or not ps["n"]
            else round(ps["win_rate"]["rate"] - pm["win_rate"]["rate"], 4),
            "win_rate_difference_ci95": None
            if not pm["n"] or not ps["n"]
            else _newcombe(ps["win_rate"]["hits"], ps["n"], pm["win_rate"]["hits"], pm["n"]),
            "strong_minus_moderate_median": None if not pm["n"] or not ps["n"] else round(ps["p50"] - pm["p50"], 2),
            "season_ordering": {
                "evaluable_seasons": [s for s, _ in seasons_ok],
                "strong_higher_seasons": [s for s, ok in seasons_ok if ok],
            },
        }
    out["ordering"] = order
    return out


# ------------------------------------------------------------------ CLOSENESS


def closeness(games: list[Rec]) -> dict[str, Any]:
    seasons = sorted({season(r) for r in games})
    absm = {r["pregame"]["game_id"]: abs(margin(r)) for r in games}
    base = sum(v <= K.ONE_SCORE for v in absm.values()) / len(games)
    out: dict[str, Any] = {"base_one_score_rate": round(base, 4), "variants": {}}
    no_edge = lambda r: v2(r)["control"] is None  # noqa: E731
    variants: dict[str, Callable[[Rec], bool]] = {
        "CLOSENESS": lambda r: v2(r)["closeness"]["CLOSENESS"],
        "CLOSENESS_EVEN": lambda r: v2(r)["closeness"]["CLOSENESS_EVEN"],
        "CLOSENESS_NARROW": lambda r: v2(r)["closeness"]["CLOSENESS_NARROW"],
        "REFERENCE_no_efficiency_edge": no_edge,
        "REFERENCE_no_efficiency_edge_without_EVEN": lambda r: no_edge(r) and not v2(r)["closeness"]["CLOSENESS_EVEN"],
    }
    for name, sel in variants.items():
        rs = [r for r in games if sel(r)]
        a = [absm[r["pregame"]["game_id"]] for r in rs]
        n = len(a)
        entry: dict[str, Any] = {"n": n}
        if n:
            one = sum(x <= K.ONE_SCORE for x in a)
            entry.update(
                {
                    "one_score_le_8": rate(one, n),
                    "le_7": rate(sum(x <= 7 for x in a), n),
                    "le_3": rate(sum(x <= 3 for x in a), n),
                    "median_abs_margin": float(np.median(a)),
                    "lift_vs_base": round((one / n) / base, 3),
                }
            )
            per = {}
            for s in seasons:
                sg = [r for r in games if season(r) == s]
                sb = sum(abs(margin(r)) <= K.ONE_SCORE for r in sg) / len(sg)
                sa = [abs(margin(r)) for r in sg if sel(r)]
                per[str(s)] = {
                    "n": len(sa),
                    "one_score_rate": round(sum(x <= K.ONE_SCORE for x in sa) / len(sa), 4) if sa else None,
                    "base": round(sb, 4),
                    "lift": round((sum(x <= K.ONE_SCORE for x in sa) / len(sa)) / sb, 3) if sa and sb else None,
                }
            entry["by_season"] = per
        out["variants"][name] = entry
    out["unconditional"] = {
        "le_3": round(sum(v <= 3 for v in absm.values()) / len(games), 4),
        "le_7": round(sum(v <= 7 for v in absm.values()) / len(games), 4),
        "le_8": round(base, 4),
        "median_abs_margin": float(np.median(list(absm.values()))),
    }
    return out


# ------------------------------------------------------------------ environments


def _env_block(
    games: list[Rec],
    sel: Callable[[Rec], bool],
    direction: str,
    pct_total: dict[str, float],
    pct_plays: dict[str, float],
    strata: dict,
    label: str | None,
) -> dict[str, Any]:
    rs = [r for r in games if sel(r)]
    n = len(rs)
    if not n:
        return {"n": 0}
    up = direction == "UP"
    p = [pct_total[r["pregame"]["game_id"]] for r in rs]
    pp = [pct_plays[r["pregame"]["game_id"]] for r in rs]
    third = sum((x >= 2 / 3) if up else (x < 1 / 3) for x in p)
    quart = sum((x >= 0.75) if up else (x < 0.25) for x in p)
    flagged = {r["pregame"]["game_id"] for r in rs}
    out: dict[str, Any] = {
        "n": n,
        "direction": direction,
        "mean_total_percentile": round(float(np.mean(p)), 4),
        "mean_total_percentile_ci95": _boot_mean_ci(p, SEED),
        "extreme_third": rate(third, n),
        "extreme_third_lift": round((third / n) / (1 / 3), 3),
        "extreme_quartile": rate(quart, n),
        "mean_actual_minus_baseline": round(
            float(np.mean([total(r) - r["pregame"]["baseline"]["total_points"] for r in rs])), 2
        ),
        "mean_total_plays_percentile": round(float(np.mean(pp)), 4),
        "mean_total_plays_percentile_ci95": _boot_mean_ci(pp, SEED + 1),
        "baseline_matched": _matched_difference(games, flagged, pct_total, strata),
    }
    if label:
        hits = sum(label in r["outcome"]["realized"]["environment"] for r in rs)
        base = sum(label in r["outcome"]["realized"]["environment"] for r in games) / len(games)
        out["realized_label"] = {
            **rate(hits, n),
            "base": round(base, 4),
            "lift": round((hits / n) / base, 3) if base else None,
        }
    per = {}
    for s in sorted({season(r) for r in games}):
        sp = [pct_total[r["pregame"]["game_id"]] for r in rs if season(r) == s]
        spp = [pct_plays[r["pregame"]["game_id"]] for r in rs if season(r) == s]
        per[str(s)] = {
            "n": len(sp),
            "mean_total_percentile": round(float(np.mean(sp)), 4) if sp else None,
            "mean_plays_percentile": round(float(np.mean(spp)), 4) if spp else None,
        }
    out["by_season"] = per
    return out


def environments(games: list[Rec]) -> dict[str, Any]:
    pct_total = _within_season_percentiles(games, total)
    pct_plays = _within_season_percentiles(games, plays)
    strata = _baseline_strata(games)
    ec = env_claim
    blocks: dict[str, tuple[Callable[[Rec], bool], str, str | None]] = {
        "ELEVATED_SCORING_ENVIRONMENT": (
            lambda r: (ec(r) or {}).get("claim") == "ELEVATED_SCORING_ENVIRONMENT",
            "UP",
            None,
        ),
        "ELEVATED|strengthened": (
            lambda r: (ec(r) or {}).get("claim") == "ELEVATED_SCORING_ENVIRONMENT" and ec(r)["strengthened"],
            "UP",
            None,
        ),
        "ELEVATED|base_only": (
            lambda r: (ec(r) or {}).get("claim") == "ELEVATED_SCORING_ENVIRONMENT" and not ec(r)["strengthened"],
            "UP",
            None,
        ),
        "ELEVATED|with_pace_support": (
            lambda r: (ec(r) or {}).get("claim") == "ELEVATED_SCORING_ENVIRONMENT" and bool(ec(r)["supports"]),
            "UP",
            None,
        ),
        "SUPPRESSED_SCORING_ENVIRONMENT": (
            lambda r: (ec(r) or {}).get("claim") == "SUPPRESSED_SCORING_ENVIRONMENT",
            "DOWN",
            None,
        ),
        "SUPPRESSED|strengthened": (
            lambda r: (ec(r) or {}).get("claim") == "SUPPRESSED_SCORING_ENVIRONMENT" and ec(r)["strengthened"],
            "DOWN",
            None,
        ),
        "SUPPRESSED|base_only": (
            lambda r: (ec(r) or {}).get("claim") == "SUPPRESSED_SCORING_ENVIRONMENT" and not ec(r)["strengthened"],
            "DOWN",
            None,
        ),
        "PACE_HIGH": (lambda r: (v2(r)["pace"] or {}).get("claim") == "PACE_HIGH", "UP", "PACE_DRIVEN_OVER"),
        "PACE_HIGH|without_elevated_scoring": (
            lambda r: (
                (v2(r)["pace"] or {}).get("claim") == "PACE_HIGH"
                and (ec(r) or {}).get("claim") != "ELEVATED_SCORING_ENVIRONMENT"
            ),
            "UP",
            "PACE_DRIVEN_OVER",
        ),
        "PACE_LOW": (lambda r: (v2(r)["pace"] or {}).get("claim") == "PACE_LOW", "DOWN", None),
        "DEFENSIVE_SUPPRESSION": (
            lambda r: v2(r)["defensive_suppression"] is not None,
            "DOWN",
            "DEFENSIVE_SUPPRESSION",
        ),
        "DEFENSIVE_SUPPRESSION|without_suppressed_scoring": (
            lambda r: (
                v2(r)["defensive_suppression"] is not None
                and (ec(r) or {}).get("claim") != "SUPPRESSED_SCORING_ENVIRONMENT"
            ),
            "DOWN",
            "DEFENSIVE_SUPPRESSION",
        ),
    }
    out = {name: _env_block(games, sel, d, pct_total, pct_plays, strata, lab) for name, (sel, d, lab) in blocks.items()}
    out["_mixed_scoring_environment_games"] = sum(
        1 for r in games if (v2(r)["scoring_environment"] or {}).get("status") == "MIXED"
    )
    out["_realized_label_base"] = {
        lab: round(sum(lab in r["outcome"]["realized"]["environment"] for r in games) / len(games), 4)
        for lab in ("PACE_DRIVEN_OVER", "DEFENSIVE_SUPPRESSION")
    }
    return out


# ------------------------------------------------------------------ DISRUPTION


def _side_won(r: Rec, side: str) -> bool:
    return side_margin(r, side) > 0


def _td(r: Rec, side: str) -> bool:
    rz = r["outcome"]["realized"]
    return "TURNOVER_DISRUPTION" in rz["labels"] and rz["sides"].get("TURNOVER_DISRUPTION") == side


def _mechanism(r: Rec, side: str) -> bool | None:
    f = r["outcome"]["features"]
    other = "away" if side == "home" else "home"
    a, b = f[side].get("sacks_tfl_made"), f[other].get("sacks_tfl_made")
    return None if a is None or b is None else a > b


def disruption(games: list[Rec]) -> dict[str, Any]:
    units_all = [(r, s) for r in games for s in ("home", "away")]
    base_td = sum(_td(r, s) for r, s in units_all) / len(units_all)
    mech_all = [m for r, s in units_all if (m := _mechanism(r, s)) is not None]
    base_mech = sum(mech_all) / len(mech_all) if mech_all else None
    home_win = sum(margin(r) > 0 for r in games) / len(games)
    side_base = {"home": home_win, "away": sum(margin(r) < 0 for r in games) / len(games)}
    seasons = sorted({season(r) for r in games})

    def units(pred: Callable[[dict], bool]) -> list[tuple[Rec, str]]:
        return [(r, c["side"]) for r in games for c in v2(r)["disruption"] if pred(c)]

    def block(us: list[tuple[Rec, str]]) -> dict[str, Any]:
        n = len(us)
        if not n:
            return {"n": 0}
        wins = sum(_side_won(r, s) for r, s in us)
        td = sum(_td(r, s) for r, s in us)
        mech = [m for r, s in us if (m := _mechanism(r, s)) is not None]
        exp_win = float(np.mean([side_base[s] for _, s in us]))
        per = {}
        for y in seasons:
            su = [(r, s) for r, s in us if season(r) == y]
            sg = [(r, s) for r in games if season(r) == y for s in ("home", "away")]
            sb = sum(_td(r, s) for r, s in sg) / len(sg)
            per[str(y)] = {
                "n": len(su),
                "td_rate": round(sum(_td(r, s) for r, s in su) / len(su), 4) if su else None,
                "td_base": round(sb, 4),
                "td_lift": round((sum(_td(r, s) for r, s in su) / len(su)) / sb, 3) if su and sb else None,
                "win_rate": round(sum(_side_won(r, s) for r, s in su) / len(su), 4) if su else None,
            }
        return {
            "n": n,
            "win_rate": rate(wins, n),
            "expected_win_rate_from_side_mix": round(exp_win, 4),
            "turnover_disruption_label": rate(td, n),
            "td_label_lift": round((td / n) / base_td, 3) if base_td else None,
            "mechanism_sacks_tfl_edge": rate(sum(mech), len(mech)),
            "margin": margin_distribution([side_margin(r, s) for r, s in us]),
            "by_season": per,
        }

    out = {
        "base": {
            "td_label_per_side": round(base_td, 4),
            "mechanism_per_side": None if base_mech is None else round(base_mech, 4),
            "home_win": round(home_win, 4),
        },
        "DISRUPTION_EDGE": block(units(lambda c: True)),
        "DISRUPTION_EDGE|with_volatility": block(units(lambda c: c["volatility"])),
        "DISRUPTION_EDGE|without_volatility": block(units(lambda c: not c["volatility"])),
        "DISRUPTION_EDGE|side_also_has_efficiency_edge": block(units(lambda c: c["side_has_efficiency_edge"])),
        "DISRUPTION_EDGE|no_efficiency_edge_either_side": block(
            units(lambda c: not c["side_has_efficiency_edge"] and not c["opponent_has_efficiency_edge"])
        ),
    }
    w, wo = out["DISRUPTION_EDGE|with_volatility"], out["DISRUPTION_EDGE|without_volatility"]
    if w["n"] and wo["n"]:
        out["volatility_increment"] = {
            "td_label_difference": round(
                w["turnover_disruption_label"]["rate"] - wo["turnover_disruption_label"]["rate"], 4
            ),
            "td_label_difference_ci95": _newcombe(
                w["turnover_disruption_label"]["hits"], w["n"], wo["turnover_disruption_label"]["hits"], wo["n"]
            ),
            "win_rate_difference": round(w["win_rate"]["rate"] - wo["win_rate"]["rate"], 4),
            "win_rate_difference_ci95": _newcombe(w["win_rate"]["hits"], w["n"], wo["win_rate"]["hits"], wo["n"]),
        }
    return out


# ------------------------------------------------------------------ data quality, no-claim, exploratory


def _heterogeneity(rs: list[Rec]) -> dict[str, Any]:
    if not rs:
        return {"n": 0}
    buckets = Counter(
        "0-3" if a <= 3 else "4-8" if a <= 8 else "9-16" if a <= 16 else "17-24" if a <= 24 else "25+"
        for a in (abs(margin(r)) for r in rs)
    )
    return {
        "n": len(rs),
        "abs_margin_bucket_entropy": entropy(dict(buckets)),
        "abs_margin": quantiles([abs(margin(r)) for r in rs]),
        "total_points": quantiles([total(r) for r in rs]),
        "one_score": rate(sum(abs(margin(r)) <= K.ONE_SCORE for r in rs), len(rs)),
    }


def no_claim(games: list[Rec]) -> dict[str, Any]:
    def any_claim(r: Rec) -> bool:
        c = v2(r)
        return bool(
            c["control"]
            or c["closeness"]["CLOSENESS"]
            or (c["scoring_environment"] or {}).get("claim")
            or c["pace"]
            or c["defensive_suppression"]
            or c["disruption"]
        )

    groups: dict[str, Callable[[Rec], bool]] = {
        "no_directional_claim": lambda r: v2(r)["control"] is None,
        "directional_claim": lambda r: v2(r)["control"] is not None,
        "no_closeness_claim": lambda r: not v2(r)["closeness"]["CLOSENESS"],
        "no_environment_claim": lambda r: (
            not (v2(r)["scoring_environment"] or {}).get("claim")
            and not v2(r)["pace"]
            and not v2(r)["defensive_suppression"]
        ),
        "no_v2_claim_at_all": lambda r: not any_claim(r),
        "at_least_one_v2_claim": any_claim,
    }
    n = len(games)
    out = {
        name: {"share": rate(sum(sel(r) for r in games), n), **_heterogeneity([r for r in games if sel(r)])}
        for name, sel in groups.items()
    }
    out["all_games"] = _heterogeneity(games)
    return out


def data_quality(games: list[Rec]) -> dict[str, Any]:
    """CONTROL win rate by DATA-QUALITY confidence vs by OUTCOME STRENGTH (edge tier)."""
    out: dict[str, Any] = {"by_confidence": {}, "by_prior_games": {}, "by_adjustment_stability": {}, "by_strength": {}}
    rows = [(r, v2(r)["control"]) for r in games if v2(r)["control"]]
    for key, f in (
        ("by_confidence", lambda r, c: r["pregame"]["confidence"]),
        (
            "by_prior_games",
            lambda r, c: (
                "1-2"
                if min(r["pregame"]["prior_games"]) <= 2
                else "3-4"
                if min(r["pregame"]["prior_games"]) <= 4
                else "5+"
            ),
        ),
        ("by_adjustment_stability", lambda r, c: "stable" if r["pregame"]["adjustment_stable"] else "unstable"),
        ("by_strength", lambda r, c: c["tier"]),
    ):
        groups: dict[str, list[float]] = defaultdict(list)
        for r, c in rows:
            groups[f(r, c)].append(side_margin(r, c["side"]))
        out[key] = {
            k: {**rate(sum(m > 0 for m in v), len(v)), "median_margin": float(np.median(v))}
            for k, v in sorted(groups.items())
        }
    cross: dict[str, list[float]] = defaultdict(list)
    for r, c in rows:
        cross[f"{c['side']}|{c['tier']}|{r['pregame']['confidence']}"].append(side_margin(r, c["side"]))
    out["by_side_strength_confidence"] = {
        k: {**rate(sum(m > 0 for m in v), len(v)), "median_margin": float(np.median(v))}
        for k, v in sorted(cross.items())
    }
    return out


def inefficient_win(games: list[Rec]) -> dict[str, Any]:
    """EXPLORATORY ONLY: winner by 9-24 who lost the efficiency battle."""

    def fam(r: Rec) -> bool:
        f = r["outcome"]["features"]
        m = f["home_margin"]
        if m == 0 or f["efficiency_winner"] is None:
            return False
        return 9 <= abs(m) <= 24 and f["efficiency_winner"] != ("home" if m > 0 else "away")

    sel = [r for r in games if v2(r)["exploratory"]["finishing_advantage_without_efficiency_edge"]]
    base = sum(fam(r) for r in games)
    hit = sum(fam(r) for r in sel)
    return {
        "label": "EXPLORATORY",
        "family_rate": rate(base, len(games)),
        "within_finishing_without_efficiency_edge": rate(hit, len(sel)),
        "lift": round((hit / len(sel)) / (base / len(games)), 3) if sel and base else None,
    }


def evaluate(records: list[Rec], frozen: dict[str, Any] | None) -> dict[str, Any]:
    games = eligible(records)
    return {
        "n_games": len(games),
        "seasons": sorted({season(r) for r in games}),
        "control": control(games, frozen),
        "closeness": closeness(games),
        "environments": environments(games),
        "disruption": disruption(games),
        "no_claim": no_claim(games),
        "data_quality": data_quality(games),
        "inefficient_win_exploratory": inefficient_win(games),
        "explosive_upset": {"status": EXPLOSIVE_UPSET_STATUS},
    }


# ------------------------------------------------------------------ criteria


def _lb(block: dict[str, Any]) -> float | None:
    ci = (block or {}).get("ci95")
    return None if not ci else ci[0]


def criteria(ev: dict[str, Any]) -> dict[str, Any]:
    """The pre-registered pass/fail rules (docs/ARCHETYPE_V2_HOLDOUT_PROTOCOL.md section 7)."""
    out: dict[str, Any] = {}
    ctl = ev["control"]
    tiers = {}
    for tier in K.CONTROL_TIERS:
        t = ctl["tiers"][tier]
        side = "home" if tier.startswith("HOME") else "away"
        base = ctl["base_side_win_rate"][side]["rate"]
        pooled = t["pooled"]
        c1 = bool(pooled["n"]) and (_lb(pooled["win_rate"]) or 0) > base
        c2 = _season_rule(
            t["by_season"],
            lambda v: v["win_rate"] is not None and v["win_rate"] > v["base_side_win_rate"],
            K.SEASON_MIN_N["control"],
        )
        tiers[tier] = {"C1_win_rate_lower_bound_above_base": c1, "C2_seasons": c2, "passes": c1 and c2["passes"]}
    ordering = {}
    for side in ("HOME", "AWAY"):
        o = ctl["ordering"][side]
        so = o["season_ordering"]
        c3 = (
            (o["strong_minus_moderate_win_rate"] or 0) > 0
            and (o["strong_minus_moderate_median"] or 0) > 0
            and len(so["evaluable_seasons"]) >= K.MIN_EVALUABLE_SEASONS
            and len(so["strong_higher_seasons"]) >= K.SEASONS_REQUIRED
        )
        ordering[side] = {"C3_strong_above_moderate": c3}
    out["CONTROL"] = {
        "tiers": tiers,
        "ordering": ordering,
        "passes": all(v["passes"] for v in tiers.values())
        and all(v["C3_strong_above_moderate"] for v in ordering.values()),
    }

    calib = {}
    for tier in K.CONTROL_TIERS:
        cov = ctl["tiers"][tier].get("interval_coverage")
        if not cov:
            calib[tier] = {"calibrated": False, "within_hard_limits": False}
            continue
        ok = all(K.COVERAGE_TOLERANCE[k][0] <= cov[k]["rate"] <= K.COVERAGE_TOLERANCE[k][1] for k in K.INTERVALS)
        hard = all(K.COVERAGE_HARD_LIMITS[k][0] <= cov[k]["rate"] <= K.COVERAGE_HARD_LIMITS[k][1] for k in K.INTERVALS)
        calib[tier] = {
            "coverage": {k: cov[k]["rate"] for k in K.INTERVALS},
            "calibrated": ok,
            "within_hard_limits": hard,
        }
    out["CONTROL_INTERVALS"] = {
        "tiers": calib,
        "passes": sum(v["calibrated"] for v in calib.values()) >= K.INTERVAL_TIERS_REQUIRED
        and all(v["within_hard_limits"] for v in calib.values()),
    }

    clo = ev["closeness"]
    for name in ("CLOSENESS", "CLOSENESS_EVEN"):
        v = clo["variants"][name]
        k1 = (
            bool(v["n"])
            and v["lift_vs_base"] >= K.CLOSENESS_MIN_LIFT
            and (_lb(v["one_score_le_8"]) or 0) > clo["base_one_score_rate"]
        )
        k2 = _season_rule(
            v.get("by_season", {}), lambda s: s["lift"] is not None and s["lift"] > 1, K.SEASON_MIN_N["closeness"]
        )
        out[name] = {"K1_lift_and_lower_bound": k1, "K2_seasons": k2, "passes": k1 and k2["passes"]}

    env = ev["environments"]

    def env_rule(
        name: str, up: bool, key: str = "mean_total_percentile", min_n: int = K.SEASON_MIN_N["environment"]
    ) -> dict:
        b = env[name]
        if not b["n"]:
            return {"passes": False, "reason": "no claims"}
        ci = b[f"{key}_ci95"]
        e1 = ci is not None and (ci[0] > 0.5 if up else ci[1] < 0.5)
        seasons = _season_rule(
            {
                s: {
                    "n": v["n"],
                    "x": v["mean_total_percentile" if key == "mean_total_percentile" else "mean_plays_percentile"],
                }
                for s, v in b["by_season"].items()
            },
            lambda v: v["x"] is not None and ((v["x"] > 0.5) if up else (v["x"] < 0.5)),
            min_n,
        )
        return {"E1_mean_percentile_ci_excludes_half": e1, "E3_seasons": seasons}

    for name, up in (("ELEVATED_SCORING_ENVIRONMENT", True), ("SUPPRESSED_SCORING_ENVIRONMENT", False)):
        b = env[name]
        r = env_rule(name, up)
        if b["n"]:
            e2 = b["extreme_third_lift"] >= K.EXTREME_THIRD_MIN_LIFT and (_lb(b["extreme_third"]) or 0) > 1 / 3
            bm = b["baseline_matched"]["ci95"]
            e4 = bm is not None and (bm[0] > 0 if up else bm[1] < 0)
            r.update({"E2_extreme_third": e2, "E4_incremental_beyond_baseline": e4})
            r["passes"] = r["E1_mean_percentile_ci_excludes_half"] and e2 and r["E3_seasons"]["passes"]
        out[name] = r

    for name, up in (("PACE_HIGH", True), ("PACE_LOW", False)):
        b = env[name]
        r = env_rule(name, up, key="mean_total_plays_percentile")
        if b["n"]:
            r["P_total_points_direction"] = b["mean_total_percentile_ci95"] is not None and (
                b["mean_total_percentile_ci95"][0] > 0.5 if up else b["mean_total_percentile_ci95"][1] < 0.5
            )
            passes = r["E1_mean_percentile_ci_excludes_half"] and r["E3_seasons"]["passes"]
            if up:
                lab = b["realized_label"]
                r["P2_realized_pace_label"] = (
                    lab["lift"] is not None
                    and lab["lift"] >= K.LABEL_MIN_LIFT
                    and ((lab["ci95"] or [0])[0] > lab["base"])
                )
                passes = passes and r["P2_realized_pace_label"]
            r["passes"] = passes
        out[name] = r

    b = env["DEFENSIVE_SUPPRESSION"]
    if b["n"]:
        s1 = b["extreme_third_lift"] >= K.EXTREME_THIRD_MIN_LIFT and (_lb(b["extreme_third"]) or 0) > 1 / 3
        s2 = b["mean_total_percentile_ci95"] is not None and b["mean_total_percentile_ci95"][1] < 0.5
        lab = b["realized_label"]
        s3 = lab["lift"] is not None and lab["lift"] >= K.LABEL_MIN_LIFT and (lab["ci95"] or [0])[0] > lab["base"]
        s4 = _season_rule(
            {s: {"n": v["n"], "x": v["mean_total_percentile"]} for s, v in b["by_season"].items()},
            lambda v: v["x"] is not None and v["x"] < 0.5,
            K.SEASON_MIN_N["suppression"],
        )
        out["DEFENSIVE_SUPPRESSION"] = {
            "S1_bottom_third": s1,
            "S2_mean_percentile": s2,
            "S3_realized_label": s3,
            "S4_seasons": s4,
            "passes": s1 and s2 and s3 and s4["passes"],
        }
    else:
        out["DEFENSIVE_SUPPRESSION"] = {"passes": False, "reason": "no claims"}

    d = ev["disruption"]
    blk = d["DISRUPTION_EDGE"]
    if blk["n"]:
        d1 = (_lb(blk["win_rate"]) or 0) > blk["expected_win_rate_from_side_mix"]
        d2 = (blk["td_label_lift"] or 0) >= K.LABEL_MIN_LIFT and (_lb(blk["turnover_disruption_label"]) or 0) > d[
            "base"
        ]["td_label_per_side"]
        d3 = (_lb(blk["mechanism_sacks_tfl_edge"]) or 0) > (d["base"]["mechanism_per_side"] or 1)
        d4 = _season_rule(
            {s: {"n": v["n"], "x": v["td_lift"]} for s, v in blk["by_season"].items()},
            lambda v: v["x"] is not None and v["x"] > 1,
            K.SEASON_MIN_N["disruption"],
        )
        vi = d.get("volatility_increment") or {}
        vol = bool(vi.get("td_label_difference_ci95")) and vi["td_label_difference_ci95"][0] > 0
        out["DISRUPTION"] = {
            "D1_win_rate": d1,
            "D2_td_label": d2,
            "D3_mechanism": d3,
            "D4_seasons": d4,
            "passes": d1 and d2 and d3 and d4["passes"],
            "volatility_adds_value": vol,
        }
    else:
        out["DISRUPTION"] = {"passes": False, "reason": "no claims", "volatility_adds_value": False}

    passed = {f: bool(out[f]["passes"]) for f in K.FAMILIES}
    others = (
        passed["CLOSENESS"] or passed["CLOSENESS_EVEN"],
        passed["ELEVATED_SCORING_ENVIRONMENT"] and passed["SUPPRESSED_SCORING_ENVIRONMENT"],
        passed["PACE_HIGH"],
        passed["DEFENSIVE_SUPPRESSION"],
        passed["DISRUPTION"],
    )
    n_other = sum(others)
    if passed["CONTROL"] and passed["CONTROL_INTERVALS"] and n_other >= 3:
        verdict = "V2 GENERALIZES STRONGLY ON SEALED HOLDOUT"
    elif passed["CONTROL"] or n_other >= 2:
        verdict = "V2 PARTIALLY GENERALIZES"
    else:
        verdict = "V2 FAILS SEALED HOLDOUT"
    if passed["CONTROL"] and passed["CONTROL_INTERVALS"]:
        recommendation = "V2 DESERVES A FORMAL PRODUCTION-MIGRATION REVIEW"
    elif passed["CONTROL"] or n_other >= 2:
        recommendation = "V2 DESERVES LIMITED FOLLOW-UP"
    else:
        recommendation = "DO NOT PROMOTE V2"
    out["summary"] = {
        "families_passed": passed,
        "non_control_families_passed": n_other,
        "verdict": verdict,
        "recommendation": recommendation,
        "surviving_for_wave_3": [f for f, ok in passed.items() if ok],
    }
    return out
