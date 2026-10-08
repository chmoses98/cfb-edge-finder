"""Locked evaluation of pre-registered CFB hypotheses. RESEARCH ONLY.

The only module where features, market lines and outcomes meet. It
interprets the declarative specs in `hypotheses.py`; there is no
per-hypothesis code. Market tests use only games with a valid consensus
line; football tests use every eligible game with an outcome.

Units / signs
-------------
margin_s      final margin from the signal side's view
spread_s      closing spread from the signal side's view (negative = favoured)
ats_resid_s   margin_s + spread_s (> 0 covers, 0 push)
total_resid   total points - closing total
Economics at an ASSUMED -110 for spreads/totals (CFBD records no spread or
total price) and at the provider's actual American odds for moneylines.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from typing import Any

import numpy as np

from cfb_edge_finder.signal_discovery import stats
from cfb_edge_finder.signal_discovery.market_lines import american_to_decimal

WIN_110 = 100.0 / 110.0
BREAK_EVEN_110 = 110.0 / 210.0
MIN_SEASON_N = 10


# --------------------------------------------------------------------------- table


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def merge(features: Iterable[dict], markets: dict[str, dict], outcomes: dict[str, dict]) -> tuple[list[dict], Counter]:
    """Eligible feature rows + outcome (+ market when valid). Exclusions are counted, never silent."""
    rows, excl = [], Counter()
    for f in features:
        if f["eligibility"] is not None:
            excl[f"FEATURE_{f['eligibility']}"] += 1
            continue
        o = outcomes.get(f["game_id"])
        if o is None:
            excl["NO_OUTCOME"] += 1
            continue
        if o["status"] != "OK":
            excl[o["status"]] += 1
            continue
        if o["home_margin"] == 0:
            excl["TIE"] += 1
            continue
        r = dict(f)
        r.update({f"o.{k}": v for k, v in o.items()})
        m = markets.get(f["game_id"])
        market_ok = False
        if m is not None:
            if m["cfbd_home_team_id"] != f["home_id"] or m["cfbd_away_team_id"] != f["away_id"]:
                excl["MARKET_ORIENTATION_MISMATCH"] += 1
            else:
                market_ok = True
        else:
            excl["MARKET_ABSENT(football tests only)"] += 1
        r["m.spread_home"] = m["spread_home"] if market_ok else None
        r["m.spread_open_home"] = m["spread_open_home"] if market_ok else None
        r["m.total"] = m["total"] if market_ok else None
        r["m.ml_home_novig"] = m["ml_home_novig"] if market_ok else None
        r["m.ml_exec"] = m["ml_exec"] if market_ok else None
        r["m.dispersion_flag"] = m["line_dispersion_flag"] if market_ok else None
        _derive(r)
        rows.append(r)
    return rows, excl


def _derive(r: dict) -> None:
    hm, sp, tot = r["o.home_margin"], r["m.spread_home"], r["m.total"]
    r["home_win"] = 1.0 if hm > 0 else 0.0
    r["home_ats_resid"] = hm + sp if sp is not None else None
    r["total_resid"] = r["o.total_points"] - tot if tot is not None else None
    bm, bt, bh = r.get("baseline.home_margin"), r.get("baseline.total_points"), r.get("baseline.home_points")
    r["gap.margin"] = bm + sp if (bm is not None and sp is not None) else None
    r["gap.total"] = bt - tot if (bt is not None and tot is not None) else None
    if sp is not None and tot is not None:
        implied = (tot - sp) / 2.0
        r["implied_home_tt"] = implied
        r["gap.home_team_total"] = bh - implied if bh is not None else None
        r["home_tt_resid"] = r["o.home_points"] - implied
    else:
        r["implied_home_tt"] = r["gap.home_team_total"] = r["home_tt_resid"] = None
    p = r["m.ml_home_novig"]
    r["logit.ml_home_novig"] = math.log(p / (1 - p)) if p is not None and 0 < p < 1 else None
    try:
        r["raw.ypp_net"] = (r["home_off_raw.yards_per_play"] - r["home_def_raw.yards_per_play"]) - (
            r["away_off_raw.yards_per_play"] - r["away_def_raw.yards_per_play"]
        )
    except TypeError:
        r["raw.ypp_net"] = None
    q = [r.get(f"{s}_def_q.points_per_game") for s in ("home", "away")]
    r["def_quality_sum"] = sum(q) if None not in q else None
    q = [r.get(f"{s}_off_q.points_per_game") for s in ("home", "away")]
    r["off_quality_sum"] = sum(q) if None not in q else None
    r["early_season"] = (r.get("week") or 0) <= 6 and r.get("season_type") == "regular"


def season_norms(rows: list[dict]) -> dict[int, dict[str, float]]:
    """Outcome-free reference rates per season (all eligible games): orientation baselines."""
    out = {}
    by = defaultdict(list)
    for r in rows:
        by[r["season"]].append(r)
    for s, rs in by.items():
        nn = [r for r in rs if not r["neutral_site"]]
        h1 = [r["o.home_1h"] - r["o.away_1h"] for r in nn if r["o.home_1h"] is not None]
        out[s] = {
            "home_win": float(np.mean([r["home_win"] for r in nn])),
            "home_margin": float(np.mean([r["o.home_margin"] for r in nn])),
            "home_margin_1h": float(np.mean(h1)) if h1 else 0.0,
            "total": float(np.mean([r["o.total_points"] for r in rs])),
            "home_points": float(np.mean([r["o.home_points"] for r in rs])),
            "close8": float(np.mean([abs(r["o.home_margin"]) <= 8 for r in rs])),
        }
    return out


# --------------------------------------------------------------------------- populations / sides


def _sign(side: str) -> float:
    return 1.0 if side == "home" else -1.0


def _other(side: str) -> str:
    return "away" if side == "home" else "home"


def _spread_s(r: dict, side: str) -> float | None:
    sp = r["m.spread_home"]
    return None if sp is None else _sign(side) * sp


def population(r: dict, pop: dict) -> bool:
    rule = pop["rule"]
    cs, st = r["control_side"], r["control_strength"]
    if rule == "all":
        return True
    if rule == "not_neutral":
        return not r["neutral_site"]
    if rule == "control":
        return cs is not None and (pop.get("strength") is None or st == pop["strength"])
    if rule == "control_opp_pass_suppressed":
        if cs is None:
            return False
        v = r.get(f"dir.passing.{_other(cs)}")
        return v is not None and v <= pop["threshold"]
    if rule == "control_rush_aligned":
        if cs is None or r.get("net.rushing") is None:
            return False
        return _sign(cs) * r["net.rushing"] >= pop["threshold"]
    if rule == "control_market_skeptical":
        if cs is None or st != pop["strength"]:
            return False
        s = _spread_s(r, cs)
        return s is not None and s > -pop["threshold"]
    if rule == "closeness":
        return bool(r["closeness"])
    if rule == "closeness_low_pace":
        pe = r.get("possession_environment")
        return bool(r["closeness"]) and pe is not None and pe <= pop["threshold"]
    if rule == "pace_claim":
        return r["pace_claim"] == pop["level"]
    if rule == "defensive_suppression":
        return bool(r["defensive_suppression_claim"])
    if rule == "scoring_env":
        return r["scoring_env_claim"] == pop["level"]
    if rule == "disruption_weak_protection":
        sides = r["disruption_sides"]
        if len(sides) != 1:
            return False
        q = r.get(f"{_other(sides[0])}_off_q.sack_rate_allowed")
        return q is not None and q <= pop["threshold"]
    if rule == "finishing_without_efficiency":
        codes = set(r["finding_codes"])
        fin = [s for s in ("home", "away") if f"{s.upper()}_FINISHING_ADVANTAGE" in codes]
        return len(fin) == 1 and f"{fin[0].upper()}_SUSTAINED_EFFICIENCY_ADVANTAGE" not in codes
    if rule == "feature_range":
        v = r.get(pop["feature"])
        if v is None:
            return False
        lo, hi = pop.get("lo"), pop.get("hi")
        return (lo is None or v >= lo) and (hi is None or v <= hi) and all(population(r, p) for p in pop.get("and", []))
    raise ValueError(f"unknown population rule {rule}")


def side_of(r: dict, spec: dict) -> str | None:
    side = spec["side"]
    if side == "control":
        return r["control_side"]
    if side in ("home", "away"):
        return side
    if side in ("market_underdog", "market_favorite"):
        sp = r["m.spread_home"]
        if sp is None or sp == 0:
            return None
        home_fav = sp < 0
        return ("home" if home_fav else "away") if side == "market_favorite" else ("away" if home_fav else "home")
    if side == "disruption":
        return r["disruption_sides"][0] if len(r["disruption_sides"]) == 1 else None
    if side == "finishing":
        codes = set(r["finding_codes"])
        fin = [s for s in ("home", "away") if f"{s.upper()}_FINISHING_ADVANTAGE" in codes]
        return fin[0] if len(fin) == 1 else None
    if side.startswith("feature_sign:"):
        v = r.get(side.split(":", 1)[1])
        if v is None or v == 0:
            return None
        return "home" if v > 0 else "away"
    raise ValueError(f"unknown side {side}")


def team_of(r: dict, side: str) -> str:
    return f"{r[side]}"


# --------------------------------------------------------------------------- per-row side quantities


def side_rows(rows: list[dict], spec: dict, norms: dict) -> list[dict]:
    out = []
    for r in rows:
        if not population(r, spec["population"]):
            continue
        s = side_of(r, spec)
        if s is None:
            continue
        sg = _sign(s)
        n = norms[r["season"]]
        if r["neutral_site"]:
            e_win, e_margin, e_1h = 0.5, 0.0, 0.0
        else:
            e_win = n["home_win"] if s == "home" else 1 - n["home_win"]
            e_margin, e_1h = sg * n["home_margin"], sg * n["home_margin_1h"]
        sp = _spread_s(r, s)
        margin = sg * r["o.home_margin"]
        nv = r["m.ml_home_novig"]
        ml = r["m.ml_exec"]
        dec = None
        if ml is not None:
            dec = american_to_decimal(ml["home_american"] if s == "home" else ml["away_american"])
        h1 = r["o.home_1h"]
        out.append(
            {
                "game_id": r["game_id"],
                "season": r["season"],
                "week": r["week"],
                "early": r["early_season"],
                "side": s,
                "team": r[s],
                "opp": r[_other(s)],
                "neutral": r["neutral_site"],
                "conference": bool(r["conference_game"]),
                "fbs_only": not r["fcs_involved"],
                "strength": r["control_strength"],
                "win": 1.0 if margin > 0 else 0.0,
                "win_lift": (1.0 if margin > 0 else 0.0) - e_win,
                "margin": margin,
                "margin_lift": margin - e_margin,
                "margin_1h_lift": (sg * (h1 - r["o.away_1h"]) - e_1h) if h1 is not None else None,
                "close8_lift": (1.0 if abs(margin) <= 8 else 0.0) - n["close8"],
                "spread_s": sp,
                "spread_open_s": None if r["m.spread_open_home"] is None else sg * r["m.spread_open_home"],
                "ats_resid": margin + sp if sp is not None else None,
                "implied": (nv if s == "home" else 1 - nv) if nv is not None else None,
                "ml_dec": dec,
                "dispersion_flag": r["m.dispersion_flag"],
            }
        )
    return out


# --------------------------------------------------------------------------- summaries


def ats_summary(xs: list[float]) -> dict[str, Any]:
    n = len(xs)
    if n == 0:
        return {"n": 0}
    covers = sum(1 for x in xs if x > 0)
    pushes = sum(1 for x in xs if x == 0)
    losses = n - covers - pushes
    decided = covers + losses
    pl = [WIN_110 if x > 0 else (-1.0 if x < 0 else 0.0) for x in xs]
    stake = [0.0 if x == 0 else 1.0 for x in xs]
    mt = stats.mean_test(xs)
    lo, hi = stats.wilson(covers, decided)
    roi = sum(pl) / sum(stake) if sum(stake) else None
    return {
        "n": n,
        "covers": covers,
        "pushes": pushes,
        "losses": losses,
        "cover_rate": covers / decided if decided else None,
        "cover_ci": [lo, hi],
        "cover_p_vs_50": stats.prop_test(covers, decided, 0.5)["p"],
        "break_even": BREAK_EVEN_110,
        "mean_resid": mt["mean"],
        "resid_se": mt["se"],
        "resid_p": mt["p"],
        "resid_ci_boot": list(stats.bootstrap_mean_ci(xs)),
        "median_resid": float(np.median(xs)),
        "resid_quantiles": stats.quantiles(xs),
        "roi_assumed_110": roi,
        "roi_ci_boot": list(stats.bootstrap_ratio_ci(pl, stake)) if sum(stake) else [None, None],
        "pl_units": sum(pl),
        "price_basis": "ASSUMED_-110 (CFBD records no spread/total price; not an executable quote)",
    }


def ml_summary(srows: list[dict]) -> dict[str, Any]:
    rs = [x for x in srows if x["implied"] is not None]
    if not rs:
        return {"n": 0, "note": "CFBD moneylines exist only from 2021"}
    resid = [x["win"] - x["implied"] for x in rs]
    mt = stats.mean_test(resid)
    ex = [x for x in srows if x["ml_dec"] is not None]
    pl = [(x["ml_dec"] - 1.0) if x["win"] else -1.0 for x in ex]
    return {
        "n": len(rs),
        "win_rate": float(np.mean([x["win"] for x in rs])),
        "mean_novig_implied": float(np.mean([x["implied"] for x in rs])),
        "mean_resid_win_minus_implied": mt["mean"],
        "resid_p": mt["p"],
        "resid_ci_boot": list(stats.bootstrap_mean_ci(resid)),
        "brier_market": stats.brier([x["win"] for x in rs], [x["implied"] for x in rs]),
        "econ_n": len(ex),
        "econ_wins": int(sum(x["win"] for x in ex)),
        "econ_mean_break_even": float(np.mean([1 / x["ml_dec"] for x in ex])) if ex else None,
        "roi_provider_odds": (sum(pl) / len(pl)) if pl else None,
        "roi_ci_boot": list(stats.bootstrap_ratio_ci(pl, [1.0] * len(pl))) if pl else [None, None],
        "pl_units": sum(pl),
        "price_basis": "PROVIDER_CLOSING_ODDS (sportsbook reference, untimestamped; not a Kalshi executable quote)",
    }


def _split(srows: list[dict], key: Callable[[dict], Any], value: str) -> dict[str, Any]:
    groups = defaultdict(list)
    for x in srows:
        k = key(x)
        if k is not None and x.get(value) is not None:
            groups[str(k)].append(x[value])
    return {
        k: {
            "n": len(v),
            "mean": float(np.mean(v)),
            "cover": float(np.mean([a > 0 for a in v if a != 0])) if any(a != 0 for a in v) else None,
        }
        for k, v in sorted(groups.items())
    }


def stability(srows: list[dict], value: str, blocks: dict[str, list[int]]) -> dict[str, Any]:
    xs = [x for x in srows if x.get(value) is not None]
    if not xs:
        return {"n": 0}
    overall = float(np.mean([x[value] for x in xs]))
    by_season = _split(xs, lambda x: x["season"], value)
    qual = {s: v for s, v in by_season.items() if v["n"] >= MIN_SEASON_N}
    same = sum(1 for v in qual.values() if np.sign(v["mean"]) == np.sign(overall))
    loso = []
    for s in sorted({x["season"] for x in xs}):
        rest = [x[value] for x in xs if x["season"] != s]
        if rest:
            loso.append(float(np.mean(rest)))
    team_sum = defaultdict(float)
    team_n = Counter()
    for x in xs:
        team_sum[x["team"]] += x[value]
        team_n[x["team"]] += 1
    top_team = max(team_sum, key=lambda t: team_sum[t] * np.sign(overall)) if team_sum else None
    without_top = [x[value] for x in xs if x["team"] != top_team]
    top5 = sum(c for _, c in team_n.most_common(5)) / len(xs)
    out = {
        "overall_mean": overall,
        "by_season": by_season,
        "seasons_qualified": len(qual),
        "seasons_same_sign": same,
        "share_seasons_same_sign": same / len(qual) if qual else None,
        "blocks": {
            b: (
                lambda v: {
                    "n": len(v),
                    "mean": float(np.mean(v)) if v else None,
                    "ci": list(stats.bootstrap_mean_ci(v)) if len(v) >= 5 else [None, None],
                }
            )([x[value] for x in xs if x["season"] in seasons])
            for b, seasons in blocks.items()
        },
        "loso_min": min(loso) if loso else None,
        "loso_max": max(loso) if loso else None,
        "early_vs_late": _split(xs, lambda x: "early(wk<=6)" if x.get("early") else "late", value),
        "home_vs_away_side": _split(xs, lambda x: "neutral" if x.get("neutral") else x.get("side"), value),
        "favorite_vs_underdog": _split(
            xs,
            lambda x: (
                None
                if x.get("spread_s") is None or x["spread_s"] == 0
                else ("favorite" if x["spread_s"] < 0 else "underdog")
            ),
            value,
        ),
        "conference_vs_nonconf": _split(xs, lambda x: "conference" if x.get("conference") else "nonconference", value),
        "fbs_only": _split(xs, lambda x: "fbs_vs_fbs" if x.get("fbs_only") else "fcs_involved", value),
        "top_contributing_team": top_team,
        "mean_without_top_team": float(np.mean(without_top)) if without_top else None,
        "top5_team_row_share": top5,
        "distinct_teams": len(team_n),
    }
    return out


# --------------------------------------------------------------------------- hypothesis kinds


def eval_side(rows, spec, norms, blocks) -> dict[str, Any]:
    srows = side_rows(rows, spec, norms)
    fo = spec.get("football_outcome", "win")
    key = {"win": "win_lift", "close8": "close8_lift", "margin_1h": "margin_1h_lift"}[fo]
    vals = [x[key] for x in srows if x[key] is not None]
    ft = stats.mean_test(vals)
    margins = [x["margin"] for x in srows]
    football = {
        "outcome": fo,
        "n": len(srows),
        "win_rate": float(np.mean([x["win"] for x in srows])) if srows else None,
        "win_ci": list(stats.wilson(int(sum(x["win"] for x in srows)), len(srows))),
        "lift_mean": ft["mean"],
        "lift_p": ft["p"],
        "lift_ci_boot": list(stats.bootstrap_mean_ci(vals)),
        "margin_mean": float(np.mean(margins)) if margins else None,
        "margin_lift_mean": float(np.mean([x["margin_lift"] for x in srows])) if srows else None,
        "margin_quantiles": stats.quantiles(margins),
        "margin_sd": float(np.std(margins, ddof=1)) if len(margins) > 1 else None,
        "abs_margin_median": float(np.median(np.abs(margins))) if margins else None,
        "football_stability": stability(srows, key, blocks),
    }
    ats_rows = [x for x in srows if x["ats_resid"] is not None]
    ats = ats_summary([x["ats_resid"] for x in ats_rows])
    if ats_rows:
        ats["mean_spread_s"] = float(np.mean([x["spread_s"] for x in ats_rows]))
        ats["stability"] = stability(ats_rows, "ats_resid", blocks)
        disp = [x["ats_resid"] for x in ats_rows if not x["dispersion_flag"]]
        ats["sensitivity_no_dispersion_flag"] = {"n": len(disp), "mean_resid": float(np.mean(disp)) if disp else None}
        opened = [x for x in ats_rows if x["spread_open_s"] is not None]
        if opened:
            ats["vs_opening_spread"] = {
                "n": len(opened),
                "mean_resid_vs_open": float(np.mean([x["margin"] + x["spread_open_s"] for x in opened])),
                "mean_move_open_to_close_toward_side": float(
                    np.mean([x["spread_open_s"] - x["spread_s"] for x in opened])
                ),
                "cover_rate_vs_open": float(
                    np.mean(
                        [
                            (x["margin"] + x["spread_open_s"]) > 0
                            for x in opened
                            if x["margin"] + x["spread_open_s"] != 0
                        ]
                    )
                ),
            }
    ml = ml_summary(srows)
    return {"football": football, "ATS": ats, "ML": ml, "rows": srows}


def total_rows(rows, spec, norms) -> list[dict]:
    d = spec["direction"]
    out = []
    for r in rows:
        if not population(r, spec["population"]):
            continue
        out.append(
            {
                "game_id": r["game_id"],
                "season": r["season"],
                "early": r["early_season"],
                "team": r["home"],
                "side": "game",
                "neutral": r["neutral_site"],
                "conference": bool(r["conference_game"]),
                "fbs_only": not r["fcs_involved"],
                "spread_s": None,
                "total_lift": d * (r["o.total_points"] - norms[r["season"]]["total"]),
                "total_resid": d * r["total_resid"] if r["total_resid"] is not None else None,
                "total_line": r["m.total"],
            }
        )
    return out


def eval_total(rows, spec, norms, blocks) -> dict[str, Any]:
    trows = total_rows(rows, spec, norms)
    vals = [x["total_lift"] for x in trows]
    ft = stats.mean_test(vals)
    football = {
        "outcome": "total_points_vs_season_mean (direction-signed)",
        "n": len(trows),
        "lift_mean": ft["mean"],
        "lift_p": ft["p"],
        "lift_ci_boot": list(stats.bootstrap_mean_ci(vals)),
        "football_stability": stability(trows, "total_lift", blocks),
    }
    mrows = [x for x in trows if x["total_resid"] is not None]
    tot = ats_summary([x["total_resid"] for x in mrows])
    if mrows:
        tot["mean_total_line"] = float(np.mean([x["total_line"] for x in mrows]))
        tot["stability"] = stability(mrows, "total_resid", blocks)
    return {"football": football, "TOTAL": tot, "rows": trows}


def _z(values: list[float]) -> tuple[list[float], float, float]:
    a = np.asarray(values, dtype=float)
    m, s = float(a.mean()), float(a.std(ddof=1))
    return ((a - m) / s).tolist(), m, s


def eval_slope(rows, spec, norms, blocks) -> dict[str, Any]:
    kind = spec["kind"]
    feat = spec["feature"]
    controls = spec.get("controls", [])
    side_mode = spec.get("side") is not None
    data = []
    for r in rows:
        if not population(r, spec["population"]):
            continue
        x = r.get(feat)
        cs = [r.get(c) for c in controls]
        if x is None or None in cs:
            continue
        n = norms[r["season"]]
        if kind == "SLOPE_SIDE":
            if side_mode:
                s = side_of(r, spec)
                if s is None:
                    continue
                sg = _sign(s)
                yf = sg * r["o.home_margin"]
                ym = sg * r["home_ats_resid"] if r["home_ats_resid"] is not None else None
                team = r[s]
            else:
                yf, ym, team = r["o.home_margin"], r["home_ats_resid"], r["home"]
        elif kind == "SLOPE_TOTAL":
            yf, ym, team = r["o.total_points"] - n["total"], r["total_resid"], r["home"]
        elif kind == "SLOPE_TEAMPTS":
            yf, ym, team = r["o.home_points"] - n["home_points"], r["home_tt_resid"], r["home"]
        else:
            raise ValueError(kind)
        data.append(
            {
                "x": x,
                "c": cs,
                "yf": yf,
                "ym": ym,
                "season": r["season"],
                "team": team,
                "early": r["early_season"],
                "neutral": r["neutral_site"],
                "conference": bool(r["conference_game"]),
                "fbs_only": not r["fcs_involved"],
                "game_id": r["game_id"],
                "side": "home",
            }
        )
    if len(data) < 30:
        return {"football": {"n": len(data)}, "market": {"n": 0}}
    if float(np.std([d["x"] for d in data])) == 0.0:
        return {"football": {"n": len(data), "note": "feature has no variance"}, "market": {"n": 0}}
    xz, xm, xsd = _z([d["x"] for d in data])
    for d, z in zip(data, xz, strict=False):
        d["xz"] = z

    def fit(ykey: str, subset: list[dict]) -> dict[str, Any]:
        sub = [d for d in subset if d[ykey] is not None]
        if len(sub) < 30:
            return {"n": len(sub)}
        X = np.column_stack(
            [np.ones(len(sub)), [d["xz"] for d in sub]] + [[d["c"][i] for d in sub] for i in range(len(controls))]
        )
        res = stats.ols([d[ykey] for d in sub], X)
        return {"n": len(sub), "beta_per_sd": res["beta"][1], "se": res["se"][1], "p": res["p"][1], "r2": res["r2"]}

    football = fit("yf", data)
    market = fit("ym", data)

    # quintiles of the feature
    def quint(ykey: str) -> list[dict]:
        sub = sorted([d for d in data if d[ykey] is not None], key=lambda d: d["x"])
        out = []
        for i in range(5):
            chunk = sub[i * len(sub) // 5 : (i + 1) * len(sub) // 5]
            if chunk:
                ys = [d[ykey] for d in chunk]
                out.append(
                    {
                        "q": i + 1,
                        "n": len(chunk),
                        "x_lo": chunk[0]["x"],
                        "x_hi": chunk[-1]["x"],
                        "mean_y": float(np.mean(ys)),
                        "cover": float(np.mean([y > 0 for y in ys if y != 0])) if any(y != 0 for y in ys) else None,
                    }
                )
        return out

    football["quintiles"] = quint("yf")
    market["quintiles"] = quint("ym")
    # per-season slopes (market) and blocks
    seasons = {}
    for s in sorted({d["season"] for d in data}):
        f = fit("ym", [d for d in data if d["season"] == s])
        if f.get("beta_per_sd") is not None:
            seasons[str(s)] = {"n": f["n"], "beta_per_sd": f["beta_per_sd"], "p": f["p"]}
    fseasons = {}
    for s in sorted({d["season"] for d in data}):
        f = fit("yf", [d for d in data if d["season"] == s])
        if f.get("beta_per_sd") is not None:
            fseasons[str(s)] = {"n": f["n"], "beta_per_sd": f["beta_per_sd"]}
    exp = spec.get("expected_sign", 1)
    market["by_season"] = seasons
    market["share_seasons_expected_sign"] = (
        sum(1 for v in seasons.values() if np.sign(v["beta_per_sd"]) == exp) / len(seasons) if seasons else None
    )
    market["blocks"] = {b: fit("ym", [d for d in data if d["season"] in ss]) for b, ss in blocks.items()}
    football["by_season"] = fseasons
    football["share_seasons_expected_sign"] = (
        sum(1 for v in fseasons.values() if np.sign(v["beta_per_sd"]) == exp) / len(fseasons) if fseasons else None
    )
    football["blocks"] = {b: fit("yf", [d for d in data if d["season"] in ss]) for b, ss in blocks.items()}
    loso = [
        fit("ym", [d for d in data if d["season"] != s]).get("beta_per_sd") for s in sorted({d["season"] for d in data})
    ]
    loso = [v for v in loso if v is not None]
    market["loso_min"], market["loso_max"] = (min(loso), max(loso)) if loso else (None, None)
    # pre-registered follow-the-feature rule: |z| >= 1 -> back the side/direction the feature points to
    picks = [d for d in data if d["ym"] is not None and abs(d["xz"]) >= 1.0]
    signed = [exp * np.sign(d["xz"]) * d["ym"] for d in picks]
    market["follow_rule_abs_z_ge_1"] = ats_summary(signed)
    if signed:
        market["follow_rule_abs_z_ge_1"]["stability"] = stability(
            [{**d, "v": exp * float(np.sign(d["xz"])) * d["ym"], "spread_s": None} for d in picks], "v", blocks
        )
    market["feature_mean"], market["feature_sd"] = xm, xsd
    team_n = Counter(d["team"] for d in data)
    market["top5_team_row_share"] = sum(c for _, c in team_n.most_common(5)) / len(data)
    return {"football": football, "market": market}


# --------------------------------------------------------------------------- walk-forward


def walk_forward(rows: list[dict], name: str, spec: dict) -> dict[str, Any]:
    feats = spec["features"]
    target = spec["target"]
    if name == "WF-ML":
        mk = spec["market_feature"]
        usable = [r for r in rows if r.get(mk) is not None and all(r.get(f) is not None for f in feats)]
        folds = []
        all_y, all_pm, all_pc = [], [], []
        for test in range(spec["first_test_season"], max(r["season"] for r in rows) + 1):
            tr = [r for r in usable if spec["train_first_season"] <= r["season"] < test]
            te = [r for r in usable if r["season"] == test]
            if len(tr) < 200 or not te:
                continue
            Xm_tr = np.column_stack([np.ones(len(tr)), [r[mk] for r in tr]])
            Xc_tr = np.column_stack([np.ones(len(tr)), [r[mk] for r in tr]] + [[r[f] for r in tr] for f in feats])
            bm = stats.logistic([r[target] for r in tr], Xm_tr)["beta"]
            bc = stats.logistic([r[target] for r in tr], Xc_tr, l2=1.0)["beta"]
            Xm_te = np.column_stack([np.ones(len(te)), [r[mk] for r in te]])
            Xc_te = np.column_stack([np.ones(len(te)), [r[mk] for r in te]] + [[r[f] for r in te] for f in feats])
            y = [r[target] for r in te]
            pm, pc = stats.predict_logistic(bm, Xm_te), stats.predict_logistic(bc, Xc_te)
            raw = [r[mk] for r in te]
            praw = 1 / (1 + np.exp(-np.asarray(raw)))
            folds.append(
                {
                    "test_season": test,
                    "n": len(te),
                    "logloss_market_raw": stats.log_loss(y, praw),
                    "logloss_market_refit": stats.log_loss(y, pm),
                    "logloss_market_plus_football": stats.log_loss(y, pc),
                    "brier_market_raw": stats.brier(y, praw),
                    "brier_market_plus_football": stats.brier(y, pc),
                    "coef_football": dict(zip(feats, bc[2:], strict=False)),
                }
            )
            all_y += y
            all_pm += list(praw)
            all_pc += list(pc)
        agg = {}
        if all_y:
            agg = {
                "n": len(all_y),
                "logloss_market": stats.log_loss(all_y, all_pm),
                "logloss_market_plus_football": stats.log_loss(all_y, all_pc),
                "brier_market": stats.brier(all_y, all_pm),
                "brier_market_plus_football": stats.brier(all_y, all_pc),
            }
            agg["delta_logloss"] = agg["logloss_market_plus_football"] - agg["logloss_market"]
            d = np.asarray(all_y)
            ll_m = -(d * np.log(np.clip(all_pm, 1e-6, 1)) + (1 - d) * np.log(np.clip(1 - np.asarray(all_pm), 1e-6, 1)))
            ll_c = -(d * np.log(np.clip(all_pc, 1e-6, 1)) + (1 - d) * np.log(np.clip(1 - np.asarray(all_pc), 1e-6, 1)))
            agg["delta_logloss_ci_boot"] = list(stats.bootstrap_mean_ci((ll_c - ll_m).tolist()))
        return {"folds": folds, "aggregate": agg}
    usable = [r for r in rows if r.get(target) is not None and all(r.get(f) is not None for f in feats)]
    folds = []
    preds, ys = [], []
    for test in range(spec["first_test_season"], max(r["season"] for r in rows) + 1):
        tr = [r for r in usable if r["season"] < test]
        te = [r for r in usable if r["season"] == test]
        if len(tr) < 300 or not te:
            continue
        X_tr = np.column_stack([np.ones(len(tr))] + [[r[f] for r in tr] for f in feats])
        res = stats.ols([r[target] for r in tr], X_tr)
        X_te = np.column_stack([np.ones(len(te))] + [[r[f] for r in te] for f in feats])
        p = X_te @ np.asarray(res["beta"])
        y = np.asarray([r[target] for r in te])
        thr = spec["pick_threshold_points"]
        picked = [float(np.sign(pi) * yi) for pi, yi in zip(p, y, strict=False) if abs(pi) >= thr]
        folds.append(
            {
                "test_season": test,
                "n": len(te),
                "oos_corr": float(np.corrcoef(p, y)[0, 1]),
                "oos_mse_model": float(((y - p) ** 2).mean()),
                "oos_mse_zero": float((y**2).mean()),
                "picks": ats_summary(picked) if picked else {"n": 0},
                "coef": dict(zip(["intercept"] + feats, res["beta"], strict=False)),
            }
        )
        preds += p.tolist()
        ys += y.tolist()
    agg = {}
    if ys:
        p, y = np.asarray(preds), np.asarray(ys)
        thr = spec["pick_threshold_points"]
        picked = [float(np.sign(pi) * yi) for pi, yi in zip(p, y, strict=False) if abs(pi) >= thr]
        agg = {
            "n": len(y),
            "oos_corr": float(np.corrcoef(p, y)[0, 1]),
            "oos_mse_model": float(((y - p) ** 2).mean()),
            "oos_mse_zero": float((y**2).mean()),
            "picks_abs_pred_ge_threshold": ats_summary(picked) if picked else {"n": 0},
        }
        # bootstrap CI of the OOS correlation
        rng = np.random.default_rng(stats.SEED)
        idx = rng.integers(0, len(y), size=(stats.N_BOOT, len(y)))
        cs = [np.corrcoef(p[i], y[i])[0, 1] for i in idx]
        agg["oos_corr_ci_boot"] = [float(np.quantile(cs, 0.025)), float(np.quantile(cs, 0.975))]
    return {"folds": folds, "aggregate": agg}


# --------------------------------------------------------------------------- status


def primary_tests(res: dict[str, Any], spec: dict) -> tuple[float | None, float | None, float | None, float | None]:
    """(football p, football effect, market p, market effect) for the FDR families."""
    kind = spec["kind"]
    if kind in ("SIDE", "FOOTBALL_ONLY"):
        f = res["football"]
        pm = spec.get("primary_market")
        if pm == "ATS":
            m = res["ATS"]
            return f["lift_p"], f["lift_mean"], m.get("resid_p"), m.get("mean_resid")
        if pm == "ML":
            m = res["ML"]
            return f["lift_p"], f["lift_mean"], m.get("resid_p"), m.get("mean_resid_win_minus_implied")
        return f["lift_p"], f["lift_mean"], None, None
    if kind == "TOTAL":
        f, m = res["football"], res["TOTAL"]
        return f["lift_p"], f["lift_mean"], m.get("resid_p"), m.get("mean_resid")
    if kind.startswith("SLOPE"):
        f, m = res["football"], res["market"]
        return f.get("p"), f.get("beta_per_sd"), m.get("p"), m.get("beta_per_sd")
    return None, None, None, None


def assign_status(spec: dict, res: dict, qf: float | None, qm: float | None, rule: dict) -> tuple[str, list[str]]:
    why: list[str] = []
    kind = spec["kind"]
    if kind == "UNAVAILABLE":
        return "DATA_UNAVAILABLE", [spec["reason"]]
    if spec["family"] == "BASELINE":
        return "BASELINE_REFERENCE", ["reference comparator, not a candidate signal"]
    exp = spec.get("expected_sign", 1)
    _, f_eff, _, m_eff = primary_tests(res, spec)
    football_ok = qf is not None and qf < rule["q_football"] and f_eff is not None and np.sign(f_eff) == exp
    why.append(
        f"football q={qf:.3g} effect={f_eff:.3g}"
        if qf is not None and f_eff is not None
        else "football test unavailable"
    )
    if spec.get("primary_market") is None or qm is None:
        return ("FOOTBALL_VALIDATED" if football_ok else "REJECTED"), why + ["no historical market for this outcome"]
    # market stability
    if kind in ("SIDE",):
        block = res[spec["primary_market"]]
        n_m = block.get("n", 0)
        st = block.get("stability", {}) if spec["primary_market"] == "ATS" else {}
        share = st.get("share_seasons_same_sign")
        blocks_ok = (
            all(
                (v.get("mean") is not None and np.sign(v["mean"]) == np.sign(m_eff))
                for v in st.get("blocks", {}).values()
            )
            if st
            else None
        )
    elif kind == "TOTAL":
        block = res["TOTAL"]
        n_m = block.get("n", 0)
        st = block.get("stability", {})
        share = st.get("share_seasons_same_sign")
        blocks_ok = all(
            (v.get("mean") is not None and np.sign(v["mean"]) == np.sign(m_eff)) for v in st.get("blocks", {}).values()
        )
    else:
        block = res["market"]
        n_m = block.get("n", 0)
        share = block.get("share_seasons_expected_sign")
        if m_eff is not None and np.sign(m_eff) != exp and share is not None:
            share = 1 - share
        blocks_ok = all(
            (v.get("beta_per_sd") is not None and np.sign(v["beta_per_sd"]) == np.sign(m_eff))
            for v in block.get("blocks", {}).values()
        )
    why.append(f"market n={n_m} q={qm:.3g} effect={m_eff:.3g} season-share={share} blocks-agree={blocks_ok}")
    if n_m < rule["min_n_market"]:
        return ("FOOTBALL_VALIDATED" if football_ok else "DISCOVERY_ONLY"), why + ["market n below minimum"]
    stable = bool(blocks_ok) and share is not None and share >= rule["min_seasons_same_sign_share"]
    if qm < rule["q_market"] and stable:
        if m_eff * exp > 0 or (kind == "SIDE" and m_eff > 0) or (kind == "TOTAL" and m_eff > 0):
            # executable economics are required for VALUE_WATCH; spread/total prices are assumed -> MARKET_WATCH cap
            return "MARKET_WATCH", why + [
                "significant, stable residual; economics only at ASSUMED -110 or untimestamped provider odds"
            ]
        return "OVERPRICED", why + ["significant, stable residual AGAINST the signal side"]
    if qm < rule["q_market_watch"]:
        return "MARKET_WATCH", why + ["residual q < 0.10 but not stable/significant enough"]
    if football_ok:
        if kind in ("SIDE", "TOTAL"):
            ci = block.get("cover_ci") or [None, None]
            if ci[0] is not None and (ci[1] - ci[0]) / 2 <= rule["efficient_ci_halfwidth_cover"]:
                return "APPROX_EFFICIENT", why + [
                    "football signal real; closing market residual indistinguishable from zero"
                ]
        else:
            if block.get("se") is not None and abs(block.get("beta_per_sd", 0)) < 2 * block["se"] + 1e-9:
                return "APPROX_EFFICIENT", why + [
                    "football slope real; market residual slope indistinguishable from zero"
                ]
        return "FOOTBALL_VALIDATED", why + ["market residual inconclusive"]
    return "REJECTED", why + ["neither the football nor the market test cleared its threshold"]


def evaluate_all(rows: list[dict], specs: list[dict], rule: dict) -> dict[str, Any]:
    norms = season_norms(rows)
    blocks = rule["blocks"]
    results = {}
    for spec in specs:
        kind = spec["kind"]
        if kind in ("SIDE", "FOOTBALL_ONLY"):
            res = eval_side(rows, spec, norms, blocks)
        elif kind == "TOTAL":
            res = eval_total(rows, spec, norms, blocks)
        elif kind.startswith("SLOPE"):
            res = eval_slope(rows, spec, norms, blocks)
        else:
            res = {}
        results[spec["id"]] = res
    ids = [s["id"] for s in specs]
    pf = [
        primary_tests(results[i], s)[0] if s["kind"] != "UNAVAILABLE" and s["family"] != "BASELINE" else None
        for i, s in zip(ids, specs, strict=False)
    ]
    pm = [
        primary_tests(results[i], s)[2] if s["kind"] != "UNAVAILABLE" and s["family"] != "BASELINE" else None
        for i, s in zip(ids, specs, strict=False)
    ]
    qf, qm = stats.benjamini_hochberg(pf), stats.benjamini_hochberg(pm)
    hf, hm = stats.holm(pf), stats.holm(pm)
    summary = []
    for i, s in enumerate(specs):
        res = results[s["id"]]
        status, why = assign_status(s, res, qf[i], qm[i], rule)
        f_p, f_eff, m_p, m_eff = primary_tests(res, s) if s["kind"] != "UNAVAILABLE" else (None,) * 4
        res["fdr"] = {
            "football_p": f_p,
            "football_q_bh": qf[i],
            "football_p_holm": hf[i],
            "market_p": m_p,
            "market_q_bh": qm[i],
            "market_p_holm": hm[i],
        }
        res["status"], res["status_reasons"] = status, why
        summary.append(
            {
                "id": s["id"],
                "name": s["name"],
                "status": status,
                "football_effect": f_eff,
                "football_q": qf[i],
                "market_effect": m_eff,
                "market_q": qm[i],
            }
        )
    return {
        "results": results,
        "summary": summary,
        "hypotheses_tested_football": sum(p is not None for p in pf),
        "hypotheses_tested_market": sum(p is not None for p in pm),
    }
