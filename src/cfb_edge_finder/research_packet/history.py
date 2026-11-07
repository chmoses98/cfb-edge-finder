"""Historical outcome distributions and comparable matchups, conditioned on the repository's PRE-KICKOFF baseline. PURE.

Why this exists: ChatGPT should not have to invent a score and wrap an arbitrary normal curve around it. For any game
this module answers, from past games whose pre-kickoff baseline looked like this one:

* how far did the ACTUAL home margin / total / each team's points / any half or quarter land from the baseline
  (empirical quantiles and exceedance tables, not a fitted distribution);
* which past games were closest (named, with their baseline and their result);
* how good the baseline itself has been (bias, MAE, versus a naive league-total guess).

Rows come from ``data/research_packets/reference/history_games.jsonl.gz`` (baseline vs ESPN final and per-period
linescores, 2024-2026). They carry no market line. Comparables are a descriptive empirical reference class, NOT a
calibrated probability: the baseline is uncalibrated and the reference class is small, and both facts are in the output.
"""

from __future__ import annotations

import gzip
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

#: Fewest comparables before an empirical distribution is published as a conditional one.
MIN_COMPARABLES = 60
#: Neighbourhood half-widths tried in order until MIN_COMPARABLES games are inside, in points of (margin, total).
WINDOWS: tuple[tuple[float, float], ...] = ((3.0, 4.0), (4.5, 6.0), (6.0, 8.0), (8.0, 11.0), (11.0, 15.0))
QUANTILES = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
ORIENTATION = (
    "margin is HOME minus AWAY. A neutral-site game keeps the listed home team; the baseline already removes "
    "home-field advantage for neutral sites"
)
RAW_STATUS = "HISTORICAL_ESTIMATE"


def load_games(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    pos = q * (len(sorted_values) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return float(sorted_values[lo])
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo))


def quantile_table(values: Sequence[float]) -> dict[str, Any] | None:
    if not values:
        return None
    v = sorted(values)
    mean = sum(v) / len(v)
    sd = math.sqrt(sum((x - mean) ** 2 for x in v) / (len(v) - 1)) if len(v) > 1 else 0.0
    return {
        "n": len(v),
        "mean": round(mean, 2),
        "sd": round(sd, 2),
        **{f"p{int(q * 100):02d}": round(_quantile(v, q), 1) for q in QUANTILES},
    }


def exceedance(values: Sequence[float], thresholds: Sequence[float]) -> dict[str, float]:
    """P(value > t) for each threshold t (a half-integer line settles without a push). Empirical, not fitted."""
    n = len(values)
    if not n:
        return {}
    return {f"{t:g}": round(sum(1 for x in values if x > t) / n, 3) for t in thresholds}


def _grid(lo: float, hi: float, step: float = 1.0) -> list[float]:
    out, x = [], lo
    while x <= hi + 1e-9:
        out.append(round(x, 1))
        x += step
    return out


def period_view(g: dict[str, Any]) -> dict[str, Any] | None:
    """Quarter / half scores of one history row (home, away), or None when the linescores were not captured."""
    hp, ap = g.get("home_periods"), g.get("away_periods")
    if not hp or not ap or len(hp) != len(ap) or len(hp) < 4:
        return None
    view: dict[str, Any] = {"ot": len(hp) > 4}
    for i in range(4):
        view[f"q{i + 1}"] = (hp[i], ap[i])
    view["h1"] = (hp[0] + hp[1], ap[0] + ap[1])
    # The second half is regulation Q3+Q4 only; overtime is neither half. Kalshi's 2H markets settle on the same.
    view["h2"] = (hp[2] + hp[3], ap[2] + ap[3])
    return view


def baseline_accuracy(games: list[dict[str, Any]]) -> dict[str, Any]:
    """How the baseline has actually performed on these games, versus guessing the league-average total."""
    usable = [g for g in games if g.get("pred_total") is not None]
    if not usable:
        return {"n": 0}
    total_err = [g["pred_total"] - (g["actual_home"] + g["actual_away"]) for g in usable]
    margin_err = [(g["pred_home"] - g["pred_away"]) - (g["actual_home"] - g["actual_away"]) for g in usable]
    league = sum(g["actual_home"] + g["actual_away"] for g in usable) / len(usable)
    naive = [league - (g["actual_home"] + g["actual_away"]) for g in usable]

    def mae(xs: list[float]) -> float:
        return round(sum(abs(x) for x in xs) / len(xs), 2)

    return {
        "n": len(usable),
        "seasons": sorted({g["season"] for g in usable}),
        "total_points": {
            "bias_pred_minus_actual": round(sum(total_err) / len(total_err), 2),
            "mae": mae(total_err),
            "naive_league_total_mae": mae(naive),
        },
        "home_margin": {"bias_pred_minus_actual": round(sum(margin_err) / len(margin_err), 2), "mae": mae(margin_err)},
        "reading": (
            "the baseline's total-points error is only marginally better than guessing the league-average total: it is a "
            "descriptive matchup summary, not a sharp scorer. Treat its point estimates as loosely informative and widen "
            "every range accordingly"
        ),
    }


def reference_class(games: list[dict[str, Any]], pred_margin: float, pred_total: float) -> tuple[list[dict[str, Any]], tuple[float, float]]:
    """Past games whose baseline margin and total sit inside the smallest window that holds MIN_COMPARABLES games."""
    pool = [g for g in games if g.get("pred_total") is not None]
    chosen: list[dict[str, Any]] = []
    window = WINDOWS[0]
    for window in WINDOWS:
        dm, dt = window
        chosen = [
            g
            for g in pool
            if abs((g["pred_home"] - g["pred_away"]) - pred_margin) <= dm and abs(g["pred_total"] - pred_total) <= dt
        ]
        if len(chosen) >= MIN_COMPARABLES:
            break
    return chosen, window


def _series(chosen: list[dict[str, Any]], getter) -> list[float]:  # noqa: ANN001
    out = []
    for g in chosen:
        v = getter(g)
        if v is not None:
            out.append(v)
    return out


def quantity_values(chosen: list[dict[str, Any]], qty: dict[str, Any]) -> list[float] | None:
    """The reference-class values of the quantity a contract asks about (home-oriented), or None if unsupported."""
    kind, period = qty.get("type"), qty.get("period", "game")
    team = qty.get("side")  # "home" | "away" | None

    def pv(g: dict[str, Any], key: str) -> tuple[float, float] | None:
        if key == "game":
            return (g["actual_home"], g["actual_away"])
        view = period_view(g)
        return None if view is None else view.get(key)

    if kind in ("margin_over", "win", "win_or_tie_lose"):
        vals = _series(chosen, lambda g: (lambda p: None if p is None else p[0] - p[1])(pv(g, period)))
        return vals
    if kind == "total_over":
        return _series(chosen, lambda g: (lambda p: None if p is None else p[0] + p[1])(pv(g, period)))
    if kind == "team_points_over":
        idx = 0 if team == "home" else 1
        return _series(chosen, lambda g: (lambda p: None if p is None else p[idx])(pv(g, period)))
    return None


def frequency_for(chosen: list[dict[str, Any]], qty: dict[str, Any]) -> dict[str, Any] | None:
    """Empirical frequency of the contract's YES outcome among comparable games. Descriptive, not a fair value."""
    vals = quantity_values(chosen, qty)
    if not vals:
        return None
    kind, side, t = qty["type"], qty.get("side"), qty.get("threshold")
    n = len(vals)
    if kind == "margin_over" and t is not None:
        hits = sum(1 for m in vals if (m > t if side == "home" else -m > t))
    elif kind == "win":
        if qty.get("outcome") == "tie":
            hits = sum(1 for m in vals if m == 0)
        else:
            hits = sum(1 for m in vals if (m > 0 if side == "home" else m < 0))
    elif kind in ("total_over", "team_points_over") and t is not None:
        hits = sum(1 for v in vals if v > t)
    else:
        return None
    return {"n": n, "yes_frequency": round(hits / n, 3), "evidence_tier": RAW_STATUS}


def comparables(
    games: list[dict[str, Any]], pred_margin: float | None, pred_total: float | None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Empirical outcome distributions among past games whose baseline margin and total were close to this game's.

    Returns (published summary, the chosen reference-class rows) so contract-level frequencies use the same class."""
    if pred_margin is None or pred_total is None:
        return (
            {"status": "UNAVAILABLE", "reason": "this game has no scoring baseline (a team lacks opponent-adjusted history)"},
            [],
        )
    chosen, window = reference_class(games, pred_margin, pred_total)
    status = "CONDITIONAL" if len(chosen) >= MIN_COMPARABLES else "THIN_REFERENCE_CLASS"
    margins = [g["actual_home"] - g["actual_away"] for g in chosen]
    totals = [g["actual_home"] + g["actual_away"] for g in chosen]
    views = [(g, period_view(g)) for g in chosen]
    with_p = [(g, v) for g, v in views if v is not None]
    h1_total = [v["h1"][0] + v["h1"][1] for _, v in with_p]
    h1_margin = [v["h1"][0] - v["h1"][1] for _, v in with_p]
    q1_total = [v["q1"][0] + v["q1"][1] for _, v in with_p]
    h2_total = [v["h2"][0] + v["h2"][1] for _, v in with_p]
    full_with_p = [g["actual_home"] + g["actual_away"] for g, _ in with_p]
    near = sorted(
        chosen,
        key=lambda g: abs((g["pred_home"] - g["pred_away"]) - pred_margin) / 3 + abs(g["pred_total"] - pred_total) / 4,
    )[:12]

    def closest(g: dict[str, Any]) -> dict[str, Any]:
        view = period_view(g)
        return {
            "season": g["season"],
            "week": g.get("week"),
            "away": g["away"],
            "home": g["home"],
            "baseline_home_margin": round(g["pred_home"] - g["pred_away"], 1),
            "baseline_total": round(g["pred_total"], 1),
            "actual": f"{g['away']} {int(g['actual_away'])} - {g['home']} {int(g['actual_home'])}",
            "first_half_away_home": None if view is None else f"{int(view['h1'][1])}-{int(view['h1'][0])}",
        }

    return (
        {
            "status": status,
            "evidence_tier": RAW_STATUS,
            "orientation": ORIENTATION,
            "reference_class": {
                "definition": "past games whose pre-kickoff baseline margin and total were within the window of this game's",
                "baseline_home_margin": round(pred_margin, 1),
                "baseline_total": round(pred_total, 1),
                "window_points_margin_total": list(window),
                "games": len(chosen),
                "seasons": sorted({g["season"] for g in chosen}),
                "min_for_conditional": MIN_COMPARABLES,
            },
            "actual_home_margin": quantile_table(margins),
            "actual_home_margin_minus_baseline": quantile_table([m - pred_margin for m in margins]),
            "p_home_margin_greater_than": exceedance(margins, _grid(round(pred_margin) - 30.5, round(pred_margin) + 30.5)),
            "actual_total": quantile_table(totals),
            "actual_total_minus_baseline": quantile_table([t - pred_total for t in totals]),
            "p_total_greater_than": exceedance(totals, _grid(max(10.5, math.floor(pred_total) - 30.5), math.floor(pred_total) + 30.5)),
            "home_points": quantile_table([g["actual_home"] for g in chosen]),
            "away_points": quantile_table([g["actual_away"] for g in chosen]),
            "p_home_points_greater_than": exceedance([g["actual_home"] for g in chosen], _grid(6.5, 56.5)),
            "p_away_points_greater_than": exceedance([g["actual_away"] for g in chosen], _grid(6.5, 56.5)),
            "first_half": {
                "games_with_linescores": len(with_p),
                "total": quantile_table(h1_total),
                "home_minus_away": quantile_table(h1_margin),
                "home_points": quantile_table([v["h1"][0] for _, v in with_p]),
                "away_points": quantile_table([v["h1"][1] for _, v in with_p]),
                "p_total_greater_than": exceedance(h1_total, _grid(10.5, 40.5)),
                "p_home_margin_greater_than": exceedance(h1_margin, _grid(-20.5, 20.5)),
                "p_home_leads_at_half": round(sum(1 for m in h1_margin if m > 0) / len(h1_margin), 3) if h1_margin else None,
                "p_tied_at_half": round(sum(1 for m in h1_margin if m == 0) / len(h1_margin), 3) if h1_margin else None,
                "share_of_game_points": round(sum(h1_total) / sum(full_with_p), 3) if full_with_p and sum(full_with_p) else None,
            },
            "second_half": {"total": quantile_table(h2_total), "p_total_greater_than": exceedance(h2_total, _grid(8.5, 40.5))},
            "first_quarter": {
                "games_with_linescores": len(with_p),
                "total": quantile_table(q1_total),
                "home_points": quantile_table([v["q1"][0] for _, v in with_p]),
                "away_points": quantile_table([v["q1"][1] for _, v in with_p]),
                "p_total_greater_than": exceedance(q1_total, _grid(0.5, 24.5)),
                "p_home_points_greater_than": exceedance([v["q1"][0] for _, v in with_p], _grid(0.5, 21.5)),
                "p_away_points_greater_than": exceedance([v["q1"][1] for _, v in with_p], _grid(0.5, 21.5)),
                "p_scoreless_first_quarter": round(sum(1 for t in q1_total if t == 0) / len(q1_total), 3) if q1_total else None,
            },
            "closest_games": [closest(g) for g in near],
            "caveat": (
                "an empirical reference class conditioned on an UNCALIBRATED baseline: use it for shape and spread (how wide, "
                "how skewed, how often a favourite is held below a line), not as a settled-outcome probability for this game"
            ),
        },
        chosen,
    )


def unconditional(games: list[dict[str, Any]]) -> dict[str, Any]:
    """League-wide shape of the same outcomes, for contrast with the conditional reference class."""
    margins = [g["actual_home"] - g["actual_away"] for g in games]
    totals = [g["actual_home"] + g["actual_away"] for g in games]
    views = [v for v in (period_view(g) for g in games) if v is not None]
    return {
        "games": len(games),
        "actual_home_margin": quantile_table(margins),
        "actual_total": quantile_table(totals),
        "first_half_total": quantile_table([v["h1"][0] + v["h1"][1] for v in views]),
        "first_quarter_total": quantile_table([v["q1"][0] + v["q1"][1] for v in views]),
    }
