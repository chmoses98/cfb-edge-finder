"""Wave 2A (CFB): the frozen Wave-2 rules applied to the 2014-2025 Wave-1 corpus. CHARACTERISATION ONLY. PURE.

Label RETROSPECTIVE_DISCOVERY_CORPUS: Wave 1 inspected these seasons, so nothing here is independent evidence.
The market is the CFBD median sportsbook spread (untimestamped, no price): SPORTSBOOK_CLOSE_CHARACTERIZATION, never
a Kalshi replay. The rules are the frozen Wave-2 constants (|z| >= 1 on the DSC-001 feature; STRONG CONTROL with a
CONTROL-side implied margin strictly below +3).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np

from cfb_edge_finder.signal_discovery import wave2 as W
from cfb_edge_finder.signal_discovery.evaluate import _derive_set2
from cfb_edge_finder.signal_discovery.wave2a.replay import EVIDENCE_HISTORY

SPORTSBOOK_CLOSE = "SPORTSBOOK_CLOSE_CHARACTERIZATION"
BREAK_EVEN_110 = 110 / 210


def joined_rows(features: list[dict[str, Any]], evaluation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Wave-1 feature rows (eligible) joined to the Wave-1 evaluation rows (CFBD median spread, final score)."""
    ev = {str(r["game_id"]): r for r in evaluation}
    out = []
    for f in features:
        if f.get("eligibility") is not None:
            continue
        e = ev.get(str(f["game_id"]))
        if e is None:
            continue
        row = dict(f)
        _derive_set2(row)
        row["_eval"] = e
        out.append(row)
    return out


def _side_view(e: dict[str, Any], side: str) -> dict[str, Any]:
    hp, ap, sp = e.get("o.home_points"), e.get("o.away_points"), e.get("m.spread_home")
    margin = None if None in (hp, ap) else (hp - ap) * (1 if side == "home" else -1)
    implied = None if sp is None else (-sp if side == "home" else sp)
    return {
        "margin": margin,
        "implied": implied,
        "residual": None if None in (margin, implied) else margin - implied,
    }


def pros001_rows(joined: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every corpus game the frozen PROS-001 rule selects (|z| >= 1), side-perspective."""
    out = []
    for r in joined:
        x = r.get(W.DSC001_FEATURE)
        if x is None:
            continue
        z = (x - W.DSC001_MEAN) / W.DSC001_SD
        if abs(z) < W.Z_THRESHOLD:
            continue
        side = "home" if z > 0 else "away"
        e = r["_eval"]
        out.append(
            {
                "signal_id": W.PROS_001,
                "evidence_label": EVIDENCE_HISTORY,
                "market_basis": SPORTSBOOK_CLOSE,
                "game_id": str(r["game_id"]),
                "season": int(r["season"]),
                "week": e.get("week"),
                "side": side,
                "side_team": e.get(side),
                "neutral_site": bool(e.get("neutral_site")),
                "z": z,
                **_side_view(e, side),
            }
        )
    return out


def pros002_rows(joined: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every corpus STRONG CONTROL game (tier identical to the frozen V2 rows) with its close-implied margin."""
    out = []
    for r in joined:
        e = r["_eval"]
        if e.get("control_strength") != "STRONG" or e.get("control_side") not in ("home", "away"):
            continue
        side = e["control_side"]
        v = _side_view(e, side)
        out.append(
            {
                "signal_id": W.PROS_002,
                "evidence_label": EVIDENCE_HISTORY,
                "market_basis": SPORTSBOOK_CLOSE,
                "game_id": str(r["game_id"]),
                "season": int(r["season"]),
                "week": e.get("week"),
                "side": side,
                "side_team": e.get(side),
                "neutral_site": bool(e.get("neutral_site")),
                "eligible": v["implied"] is not None and v["implied"] < W.SKEPTIC_BELOW_POINTS,
                **v,
            }
        )
    return out


def _boot_mean(values: list[float], seed: int = W.SEED) -> list[float] | None:
    if len(values) < 2:
        return None
    rng = np.random.default_rng(seed)
    arr = np.asarray(values, float)
    means = arr[rng.integers(0, len(arr), size=(W.N_BOOT, len(arr)))].mean(axis=1)
    return [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))]


def describe(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Residual, ATS-like, outright and assumed -110 economics of side-perspective rows with a line and a score."""
    rows = [r for r in rows if r.get("residual") is not None]
    n = len(rows)
    if n == 0:
        return {"n": 0, "display": W.NO_SETTLED_SAMPLE}
    res = [r["residual"] for r in rows]
    margins = [r["margin"] for r in rows]
    w = sum(1 for x in res if x > 0)
    lo = sum(1 for x in res if x < 0)
    units = w * (100 / 110) - lo
    return {
        "n": n,
        "mean_margin": float(np.mean(margins)),
        "median_margin": float(np.median(margins)),
        "outright_win_rate": sum(1 for m in margins if m > 0) / n,
        "mean_implied": float(np.mean([r["implied"] for r in rows])),
        "residual": {
            "mean": float(np.mean(res)),
            "median": float(np.median(res)),
            "ci95_bootstrap": _boot_mean(res),
            "quantiles": {q: float(np.quantile(res, q / 100)) for q in (10, 25, 50, 75, 90)},
            "positive_rate": w / n,
        },
        "ats_like": {
            "wins": w,
            "losses": lo,
            "pushes": n - w - lo,
            "rate": w / (w + lo) if w + lo else None,
            "rate_ci95": W.wilson(w, w + lo),
            "break_even_assumed_110": BREAK_EVEN_110,
        },
        "assumed_110_roi": units / (w + lo) if w + lo else None,
        "price_basis": "ASSUMED_-110 (CFBD records no spread price; not executable)",
    }


def by(rows: list[dict[str, Any]], key) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[str(key(r))].append(r)
    return {g: describe(v) for g, v in sorted(groups.items())}


def favourite_role(r: dict[str, Any]) -> str:
    if r.get("implied") is None:
        return "NO_LINE"
    return "FAVOURITE" if r["implied"] > 0 else "UNDERDOG" if r["implied"] < 0 else "PICK"


def characterise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    with_line = [r for r in rows if r.get("residual") is not None]
    team_counts = Counter(r.get("side_team") for r in with_line)
    return {
        "evidence_label": EVIDENCE_HISTORY,
        "market_basis": SPORTSBOOK_CLOSE,
        "selected": len(rows),
        "with_line_and_score": len(with_line),
        "overall": describe(rows),
        "by_season": by(rows, lambda r: r["season"]),
        "by_side": by(rows, lambda r: r["side"]),
        "by_favourite": by(rows, favourite_role),
        "by_neutral": by(rows, lambda r: "neutral" if r["neutral_site"] else "true_home_away"),
        "top_team_share": (max(team_counts.values()) / len(with_line)) if with_line else None,
        "seasons_positive_mean": sum(
            1 for v in by(rows, lambda r: r["season"]).values() if v.get("n") and v["residual"]["mean"] > 0
        ),
    }
