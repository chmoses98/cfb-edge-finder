"""Required deep dives (prompt sections 11, 36, 38-G/H): CONTROL tiers vs the
closing spread and moneyline, and the market-disagreement regimes.
Descriptive tables over the same frozen rows; no new rule is fitted here.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from cfb_edge_finder.signal_discovery import stats
from cfb_edge_finder.signal_discovery.evaluate import ats_summary, ml_summary, season_norms, side_rows

TIERS = [("HOME", "MODERATE"), ("AWAY", "MODERATE"), ("HOME", "STRONG"), ("AWAY", "STRONG")]


def _tier_spec(side: str | None, strength: str | None) -> dict:
    return {"population": {"rule": "control", "strength": strength}, "side": "control", "_side_filter": side}


def _rows(rows, norms, side, strength):
    spec = _tier_spec(side, strength)
    srows = side_rows(rows, spec, norms)
    if side:
        srows = [x for x in srows if x["side"] == side.lower()]
    return srows


def _ats_block(srows: list[dict]) -> dict[str, Any]:
    ats_rows = [x for x in srows if x["ats_resid"] is not None]
    out = ats_summary([x["ats_resid"] for x in ats_rows])
    if not ats_rows:
        return out
    out["mean_spread_s"] = float(np.mean([x["spread_s"] for x in ats_rows]))
    out["median_spread_s"] = float(np.median([x["spread_s"] for x in ats_rows]))
    by_season = defaultdict(list)
    for x in ats_rows:
        by_season[x["season"]].append(x["ats_resid"])
    out["by_season"] = {
        str(s): {
            "n": len(v),
            "covers": sum(a > 0 for a in v),
            "pushes": sum(a == 0 for a in v),
            "losses": sum(a < 0 for a in v),
            "cover_rate": (sum(a > 0 for a in v) / max(1, sum(a != 0 for a in v))),
            "mean_resid": float(np.mean(v)),
        }
        for s, v in sorted(by_season.items())
    }
    fav = [x["ats_resid"] for x in ats_rows if x["spread_s"] < 0]
    dog = [x["ats_resid"] for x in ats_rows if x["spread_s"] > 0]
    pk = [x["ats_resid"] for x in ats_rows if x["spread_s"] == 0]
    out["as_favorite"] = ats_summary(fav) if fav else {"n": 0}
    out["as_underdog"] = ats_summary(dog) if dog else {"n": 0}
    out["pick_em_n"] = len(pk)
    buckets = [(-99, -21), (-20.5, -14), (-13.5, -7), (-6.5, -3), (-2.5, 0), (0.5, 99)]
    out["by_spread_bucket"] = {}
    for lo, hi in buckets:
        v = [x["ats_resid"] for x in ats_rows if lo <= x["spread_s"] <= hi]
        out["by_spread_bucket"][f"{lo}..{hi}"] = {
            "n": len(v),
            "cover_rate": (sum(a > 0 for a in v) / max(1, sum(a != 0 for a in v))) if v else None,
            "mean_resid": float(np.mean(v)) if v else None,
        }
    opened = [x for x in ats_rows if x["spread_open_s"] is not None]
    if opened:
        out["vs_opening_2021plus"] = {
            **{
                k: v
                for k, v in ats_summary([x["margin"] + x["spread_open_s"] for x in opened]).items()
                if k in ("n", "covers", "pushes", "losses", "cover_rate", "mean_resid", "roi_assumed_110")
            },
            "mean_move_toward_side_open_to_close": float(np.mean([x["spread_open_s"] - x["spread_s"] for x in opened])),
            "share_moved_toward_side": float(np.mean([x["spread_open_s"] - x["spread_s"] > 0 for x in opened])),
            "share_moved_away": float(np.mean([x["spread_open_s"] - x["spread_s"] < 0 for x in opened])),
        }
    out["early_late"] = {
        k: (
            lambda v: (
                {
                    "n": len(v),
                    "cover_rate": sum(a > 0 for a in v) / max(1, sum(a != 0 for a in v)),
                    "mean_resid": float(np.mean(v)),
                }
                if v
                else {"n": 0}
            )
        )([x["ats_resid"] for x in ats_rows if x["early"] == flag])
        for k, flag in (("early(wk<=6)", True), ("late", False))
    }
    return out


def control_tiers(rows: list[dict]) -> dict[str, Any]:
    norms = season_norms(rows)
    out: dict[str, Any] = {}
    groups = [(f"{s}_{t}", s, t) for s, t in TIERS] + [
        ("ALL_MODERATE", None, "MODERATE"),
        ("ALL_STRONG", None, "STRONG"),
        ("ALL_CONTROL", None, None),
    ]
    for name, side, strength in groups:
        srows = _rows(rows, norms, side, strength)
        margins = [x["margin"] for x in srows]
        out[name] = {
            "football": {
                "n": len(srows),
                "wins": int(sum(x["win"] for x in srows)),
                "win_rate": float(np.mean([x["win"] for x in srows])) if srows else None,
                "win_ci": list(stats.wilson(int(sum(x["win"] for x in srows)), len(srows))),
                "margin_mean": float(np.mean(margins)) if margins else None,
                "margin_quantiles": stats.quantiles(margins),
                "by_season_win_rate": {
                    str(s): float(np.mean([x["win"] for x in srows if x["season"] == s]))
                    for s in sorted({x["season"] for x in srows})
                },
            },
            "ATS": _ats_block(srows),
            "ML": ml_summary(srows),
        }
    # MODERATE minus STRONG residual difference (bootstrap)
    mod = [x["ats_resid"] for x in _rows(rows, norms, None, "MODERATE") if x["ats_resid"] is not None]
    strg = [x["ats_resid"] for x in _rows(rows, norms, None, "STRONG") if x["ats_resid"] is not None]
    if mod and strg:
        rng = np.random.default_rng(stats.SEED)
        a, b = np.asarray(mod), np.asarray(strg)
        d = [
            a[rng.integers(0, len(a), len(a))].mean() - b[rng.integers(0, len(b), len(b))].mean()
            for _ in range(stats.N_BOOT)
        ]
        out["moderate_minus_strong_mean_resid"] = {
            "estimate": float(a.mean() - b.mean()),
            "ci_boot": [float(np.quantile(d, 0.025)), float(np.quantile(d, 0.975))],
        }
    return out


def market_disagreement(rows: list[dict]) -> dict[str, Any]:
    norms = season_norms(rows)
    out: dict[str, Any] = {}

    def regime(spread_s: float | None) -> str | None:
        if spread_s is None:
            return None
        if spread_s <= -10:
            return "MARKET_STRONG(fav>=10)"
        if spread_s <= -3:
            return "MARKET_MODERATE(fav 3-9.5)"
        return "MARKET_SKEPTICAL(fav<3 or dog)"

    for strength in ("STRONG", "MODERATE"):
        srows = _rows(rows, norms, None, strength)
        groups = defaultdict(list)
        for x in srows:
            g = regime(x["spread_s"])
            if g:
                groups[g].append(x)
        out[f"CONTROL_{strength}"] = {
            g: {
                "n": len(v),
                "win_rate": float(np.mean([x["win"] for x in v])),
                "ATS": {
                    k: a
                    for k, a in ats_summary([x["ats_resid"] for x in v]).items()
                    if k
                    in ("n", "covers", "pushes", "cover_rate", "cover_ci", "mean_resid", "resid_p", "roi_assumed_110")
                },
                "ML": {
                    k: a
                    for k, a in ml_summary(v).items()
                    if k
                    in (
                        "n",
                        "win_rate",
                        "mean_novig_implied",
                        "mean_resid_win_minus_implied",
                        "resid_p",
                        "econ_n",
                        "roi_provider_odds",
                        "roi_ci_boot",
                    )
                },
            }
            for g, v in sorted(groups.items())
        }
    # football baseline vs market: disagreement on WHO is favoured
    dis = [
        r
        for r in rows
        if r["m.spread_home"] not in (None, 0)
        and r.get("baseline.home_margin") not in (None, 0)
        and np.sign(r["baseline.home_margin"]) != np.sign(-r["m.spread_home"])
    ]
    if dis:
        fb_side_wins = [1.0 if np.sign(r["o.home_margin"]) == np.sign(r["baseline.home_margin"]) else 0.0 for r in dis]
        fb_side_ats = [np.sign(r["baseline.home_margin"]) * r["home_ats_resid"] for r in dis]
        ml = [r for r in dis if r["m.ml_home_novig"] is not None]
        out["baseline_vs_market_favourite_disagree"] = {
            "n": len(dis),
            "football_side_win_rate": float(np.mean(fb_side_wins)),
            "market_favourite_win_rate": 1 - float(np.mean(fb_side_wins)),
            "football_side_ATS": {
                k: a
                for k, a in ats_summary(fb_side_ats).items()
                if k in ("n", "covers", "pushes", "cover_rate", "cover_ci", "mean_resid", "resid_p", "roi_assumed_110")
            },
            "football_side_mean_novig_implied_2021plus": float(
                np.mean(
                    [r["m.ml_home_novig"] if r["baseline.home_margin"] > 0 else 1 - r["m.ml_home_novig"] for r in ml]
                )
            )
            if ml
            else None,
            "football_side_win_rate_2021plus": float(
                np.mean([1.0 if np.sign(r["o.home_margin"]) == np.sign(r["baseline.home_margin"]) else 0.0 for r in ml])
            )
            if ml
            else None,
            "n_2021plus": len(ml),
        }
    # CONTROL side vs market favourite disagreement (control side is the market underdog)
    for strength in ("STRONG", "MODERATE", None):
        srows = [x for x in _rows(rows, norms, None, strength) if x["spread_s"] is not None and x["spread_s"] > 0]
        key = f"CONTROL_{strength or 'ANY'}_side_is_market_underdog"
        out[key] = {
            "n": len(srows),
            "control_side_win_rate": float(np.mean([x["win"] for x in srows])) if srows else None,
            "ATS": {
                k: a
                for k, a in ats_summary([x["ats_resid"] for x in srows]).items()
                if k in ("n", "covers", "cover_rate", "cover_ci", "mean_resid", "resid_p", "roi_assumed_110")
            }
            if srows
            else {"n": 0},
            "ML": {
                k: a
                for k, a in ml_summary(srows).items()
                if k
                in (
                    "n",
                    "win_rate",
                    "mean_novig_implied",
                    "mean_resid_win_minus_implied",
                    "econ_n",
                    "roi_provider_odds",
                    "roi_ci_boot",
                )
            },
        }
    return out


def calibration_vs_market(rows: list[dict]) -> dict[str, Any]:
    """Market-implied win probability buckets: does CONTROL membership shift realised win rate at the same price?"""
    out = {}
    ml = [r for r in rows if r["m.ml_home_novig"] is not None]
    buckets = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 0.9), (0.9, 1.0)]
    for label, pred in (("control_side", lambda r: r["control_side"]), ("no_control_home", lambda r: None)):
        if label != "control_side":
            continue
        rowsout = {}
        for lo, hi in buckets:
            sub = []
            for r in ml:
                s = pred(r)
                if s is None:
                    continue
                p = r["m.ml_home_novig"] if s == "home" else 1 - r["m.ml_home_novig"]
                if lo <= p < hi:
                    sub.append((p, 1.0 if (r["o.home_margin"] > 0) == (s == "home") else 0.0))
            if sub:
                rowsout[f"{lo:.1f}-{hi:.1f}"] = {
                    "n": len(sub),
                    "mean_implied": float(np.mean([a for a, _ in sub])),
                    "win_rate": float(np.mean([b for _, b in sub])),
                }
        out[label] = rowsout
    return out


def run(rows: list[dict], results: dict[str, Any]) -> dict[str, Any]:
    return {
        "control_tiers": control_tiers(rows),
        "market_disagreement": market_disagreement(rows),
        "control_calibration_vs_ml": calibration_vs_market(rows),
    }
