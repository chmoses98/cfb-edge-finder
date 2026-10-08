"""Aggregation, uncertainty and the pre-registered verdicts. Descriptive research only.

Groups are reported in FIXED order (tiers, then pools; buckets in registered
order, empty ones included). Nothing is ever sorted by performance and no
function returns a "best" anything.
"""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Callable, Iterable
from typing import Any

import numpy as np

from cfb_edge_finder.control_market import (
    BANNED_CONCLUSION_WORDS,
    BOOTSTRAP_MIN_N,
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    CHECKPOINTS,
    EVIDENCE_MIN_CLV_N,
    EVIDENCE_MIN_N,
    POOLS,
    PRICE_BUCKETS,
    SEASON_BLOCKS,
    TIERS,
    VERDICT_MIN_N,
)

HISTORICAL = {
    "HOME_CONTROL_MODERATE": {"dev_win_rate": 0.7995, "val_win_rate": 0.7949, "dev_median": 12.0, "val_median": 13.0},
    "HOME_CONTROL_STRONG": {"dev_win_rate": 0.9013, "val_win_rate": 0.9102, "dev_median": 23.0, "val_median": 24.0},
    "AWAY_CONTROL_MODERATE": {"dev_win_rate": 0.6658, "val_win_rate": 0.679, "dev_median": 7.0, "val_median": 7.0},
    "AWAY_CONTROL_STRONG": {"dev_win_rate": 0.8095, "val_win_rate": 0.846, "dev_median": 14.5, "val_median": 17.0},
}
GROUP_ORDER = (*TIERS, *POOLS)
LOGLOSS_CLIP = 1e-15


def canonical_order(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows in a fixed order (population, game) so the seeded bootstrap is independent of input order."""
    return sorted(rows, key=lambda r: (r["population"], r["game_id"]))


def group_tiers(name: str) -> tuple[str, ...]:
    return POOLS.get(name, (name,))


def price_bucket(price: float | None) -> str | None:
    if price is None:
        return None
    cents = round(price * 100)
    for lo, hi in PRICE_BUCKETS:
        if lo <= cents <= hi:
            return f"{lo}-{hi}"
    return None


BUCKET_LABELS = tuple(f"{lo}-{hi}" for lo, hi in PRICE_BUCKETS)


def season_block(week: int | None) -> str | None:
    if week is None:
        return None
    for name, (lo, hi) in SEASON_BLOCKS.items():
        if lo <= int(week) <= hi:
            return name
    return None


def prior_bucket(prior_games: list[int] | None) -> str | None:
    if not prior_games:
        return None
    m = min(prior_games)
    return "4+" if m >= 4 else str(m)


# --------------------------------------------------------------------------- statistics


def wilson(hits: int, n: int, z: float = 1.959964) -> list[float] | None:
    if n == 0:
        return None
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(centre - half, 4), round(centre + half, 4)]


def bootstrap(
    columns: dict[str, list[float]], stat: Callable[[dict[str, np.ndarray]], np.ndarray], n: int
) -> dict[str, Any]:
    """Percentile 95% interval, resampling games (rows) with replacement; seeded and withheld below n=5."""
    if n < BOOTSTRAP_MIN_N:
        return {"ci95": None, "reason": f"withheld: n={n} < {BOOTSTRAP_MIN_N}"}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, n, size=(BOOTSTRAP_RESAMPLES, n))
    draws = stat({k: np.asarray(v, dtype=float)[idx] for k, v in columns.items()})
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return {
        "ci95": [round(float(lo), 4), round(float(hi), 4)],
        "resamples": BOOTSTRAP_RESAMPLES,
        "seed": BOOTSTRAP_SEED,
    }


def _roi_stat(c: dict[str, np.ndarray]) -> np.ndarray:
    return c["pnl"].sum(axis=1) / c["outlay"].sum(axis=1)


def _mean_stat(c: dict[str, np.ndarray]) -> np.ndarray:
    return c["x"].mean(axis=1)


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    return round(float(np.percentile(values, q)), 4)


def _r(x: float | None, k: int = 4) -> float | None:
    return None if x is None else round(x, k)


def brier(forecasts: list[float], outcomes: list[int]) -> float | None:
    if not forecasts:
        return None
    return round(sum((f - o) ** 2 for f, o in zip(forecasts, outcomes, strict=True)) / len(forecasts), 5)


def log_loss(forecasts: list[float], outcomes: list[int]) -> float | None:
    if not forecasts:
        return None
    total = 0.0
    for f, o in zip(forecasts, outcomes, strict=True):
        f = min(max(f, LOGLOSS_CLIP), 1 - LOGLOSS_CLIP)
        total += -(o * math.log(f) + (1 - o) * math.log(1 - f))
    return round(total / len(forecasts), 5)


# --------------------------------------------------------------------------- summaries


def economics_summary(rows: list[dict[str, Any]], contract: str = "primary") -> dict[str, Any]:
    """Primary economic metrics over rows whose `contract` economics are available."""
    econ = [r[contract]["economics"] for r in rows if r[contract]["economics"]["available"]]
    n = len(econ)
    out: dict[str, Any] = {"n": n}
    if n == 0:
        return out
    prices = [e["entry_price"] for e in econ]
    fees = [e["fee"] for e in econ]
    pnl = [e["fee_adjusted_pnl"] for e in econ]
    outlay = [e["outlay"] for e in econ]
    wins = sum(1 for e in econ if e["settlement_value"] == 1.0)
    total_entry, total_fee, total_outlay = sum(prices), sum(fees), sum(outlay)
    payout = float(wins)
    win_rate = wins / n
    break_even = total_outlay / n
    out.update(
        {
            "wins": wins,
            "losses": n - wins,
            "win_rate": _r(win_rate),
            "win_rate_wilson95": wilson(wins, n),
            "win_rate_bootstrap95": bootstrap(
                {"x": [1.0 if e["settlement_value"] == 1.0 else 0.0 for e in econ]}, _mean_stat, n
            )["ci95"],
            "entry_price_mean": _r(statistics.fmean(prices)),
            "entry_price_median": _r(statistics.median(prices)),
            "entry_price_pctl": {f"p{q}": _pct(prices, q) for q in (10, 25, 50, 75, 90)},
            "total_entry_cost": _r(total_entry),
            "total_fees": _r(total_fee),
            "total_outlay": _r(total_outlay),
            "gross_payout": _r(payout),
            "gross_pnl": _r(payout - total_entry),
            "fee_adjusted_pnl": _r(payout - total_outlay),
            "roi_on_outlay": _r((payout - total_outlay) / total_outlay),
            "roi_on_outlay_bootstrap": bootstrap({"pnl": pnl, "outlay": outlay}, _roi_stat, n),
            "roi_on_entry": _r((payout - total_outlay) / total_entry),
            "pnl_per_contract_mean": _r(statistics.fmean(pnl)),
            "pnl_per_contract_median": _r(statistics.median(pnl)),
            "break_even_win_rate": _r(break_even),
            "realized_minus_break_even": _r(win_rate - break_even),
        }
    )
    return out


def market_scoring(rows: list[dict[str, Any]], tier_of: Callable[[dict], str]) -> dict[str, Any]:
    """Brier / log loss of the entry ask (and mid where both sides were captured) as forecasts, on identical rows.

    Executable asks are not fair probabilities (spread and fees are embedded);
    the historical tier frequency is context, not a model probability."""
    econ = [r for r in rows if r["primary"]["economics"]["available"]]
    y = [int(r["primary"]["economics"]["settlement_value"]) for r in econ]
    ask = [r["primary"]["economics"]["entry_price"] for r in econ]
    hist = [HISTORICAL[tier_of(r)]["dev_win_rate"] for r in econ]
    mids = [
        (r["primary"]["entry"]["mid"], yy) for r, yy in zip(econ, y, strict=True) if r["primary"]["entry"].get("mid")
    ]
    return {
        "n": len(econ),
        "brier_entry_ask": brier(ask, y),
        "log_loss_entry_ask": log_loss(ask, y),
        "brier_historical_tier_rate": brier(hist, y),
        "log_loss_historical_tier_rate": log_loss(hist, y),
        "mid_n": len(mids),
        "brier_entry_mid": brier([m for m, _ in mids], [o for _, o in mids]),
        "paired_model_vs_market": (
            "NOT_COMPUTED: V2 CONTROL carries no probability by design; no valid model probability exists"
        ),
        "caveat": (
            "executable asks embed spread and fees; historical rates are conditional frequencies, not fair prices"
        ),
    }


def clv_summary(rows: list[dict[str, Any]], contract: str = "primary") -> dict[str, Any]:
    with_entry = [r for r in rows if r[contract]["entry"]["price"] is not None]
    avail = [r[contract]["clv"] for r in with_entry if r[contract]["clv"]["available"]]
    n = len(avail)
    out: dict[str, Any] = {"entry_n": len(with_entry), "clv_n": n, "missing": len(with_entry) - n}
    if n == 0:
        return out
    moves = [c["clv"] for c in avail]
    out.update(
        {
            "mean_clv": _r(statistics.fmean(moves)),
            "median_clv": _r(statistics.median(moves)),
            "favorable_pct": _r(sum(1 for c in avail if c["direction"] == "FAVORABLE") / n),
            "flat_pct": _r(sum(1 for c in avail if c["direction"] == "FLAT") / n),
            "unfavorable_pct": _r(sum(1 for c in avail if c["direction"] == "UNFAVORABLE") / n),
            "mean_logit_movement": _r(statistics.fmean(c["logit_movement"] for c in avail)),
            "mean_clv_bootstrap95": bootstrap({"x": moves}, _mean_stat, n)["ci95"],
        }
    )
    return out


def movement_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """EARLY_OPEN -> PRIMARY -> CLOSING on the CONTROL team's YES ask (price up = market moved toward CONTROL)."""
    out: dict[str, Any] = {}
    for cp in CHECKPOINTS:
        prices = [r["primary"]["checkpoints"][cp]["price"] for r in rows if r["primary"]["checkpoints"][cp]]
        out[cp] = {"n": len(prices), "mean_price": _r(statistics.fmean(prices)) if prices else None}
    for a, b in (("EARLY_OPEN", "CLOSING"), ("EARLY_OPEN", "PRIMARY_60_180"), ("PRIMARY_60_180", "CLOSING")):
        pairs = [
            (r["primary"]["checkpoints"][a]["price"], r["primary"]["checkpoints"][b]["price"])
            for r in rows
            if r["primary"]["checkpoints"][a]
            and r["primary"]["checkpoints"][b]
            and r["primary"]["checkpoints"][a]["captured_at"] < r["primary"]["checkpoints"][b]["captured_at"]
        ]
        d = [y - x for x, y in pairs]
        out[f"{a}->{b}"] = {
            "n": len(d),
            "mean_move": _r(statistics.fmean(d)) if d else None,
            "median_move": _r(statistics.median(d)) if d else None,
            "toward_control_pct": _r(sum(1 for v in d if v > 0) / len(d)) if d else None,
            "away_from_control_pct": _r(sum(1 for v in d if v < 0) / len(d)) if d else None,
            "flat_pct": _r(sum(1 for v in d if v == 0) / len(d)) if d else None,
            "mean_abs_move": _r(statistics.fmean(abs(v) for v in d)) if d else None,
        }
    return out


def coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reasons: dict[str, int] = {}
    for r in rows:
        if not r["primary"]["economics"]["available"]:
            reason = r["primary"]["exclusion"]
            reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "eligible_control_games": len(rows),
        "with_game_winner_market": sum(1 for r in rows if r["primary"]["contract_status"] != "NO_GAME_WINNER_MARKET"),
        "with_valid_entry_quote": sum(1 for r in rows if r["primary"]["entry"]["price"] is not None),
        "missing_entry_quote": sum(1 for r in rows if r["primary"]["entry"]["price"] is None),
        "with_valid_settlement": sum(1 for r in rows if r["settlement"]["status"] == "SETTLED"),
        "primary_economics_n": sum(1 for r in rows if r["primary"]["economics"]["available"]),
        "exclusions": dict(sorted(reasons.items())),
    }


def football_summary(rows: list[dict[str, Any]], tier: str | None) -> dict[str, Any]:
    """Football performance of every eligible CONTROL game with a settled score (market data not required)."""
    settled = [r for r in rows if r["settlement"]["status"] == "SETTLED"]
    n = len(settled)
    wins = sum(1 for r in settled if r["settlement"]["control_won"])
    margins = [r["settlement"]["control_margin"] for r in settled]
    out = {
        "n": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": _r(wins / n) if n else None,
        "wilson95": wilson(wins, n),
        "median_margin": statistics.median(margins) if margins else None,
    }
    if tier in HISTORICAL:
        h = HISTORICAL[tier]
        out["historical_development_win_rate"] = h["dev_win_rate"]
        out["historical_validation_win_rate"] = h["val_win_rate"]
        out["continuation"] = football_continuation(out["wilson95"], h["dev_win_rate"], n)
    return out


def football_continuation(ci: list[float] | None, dev_rate: float, n: int) -> str:
    if n < VERDICT_MIN_N or ci is None:
        return "INSUFFICIENT"
    return "BELOW_HISTORICAL" if ci[1] < dev_rate else "CONSISTENT"


def verdict(econ: dict[str, Any], clv: dict[str, Any]) -> dict[str, str]:
    """The pre-registered per-tier market-efficiency classification."""
    n = econ.get("n", 0)
    if n < VERDICT_MIN_N:
        return {"verdict": "INSUFFICIENT_DATA", "basis": f"n={n} < {VERDICT_MIN_N}"}
    roi = econ["roi_on_outlay"]
    ci = econ["roi_on_outlay_bootstrap"]["ci95"]
    if roi > 0:
        clv_ok = clv.get("clv_n", 0) >= EVIDENCE_MIN_CLV_N and (clv.get("mean_clv") or 0) > 0
        if ci and ci[0] > 0 and n >= EVIDENCE_MIN_N and clv_ok:
            return {"verdict": "EVIDENCE_OF_UNDERPRICING", "basis": f"ROI {roi} CI {ci} n={n} CLV ok"}
        return {"verdict": "POSSIBLE_UNDERPRICING", "basis": f"ROI {roi} CI {ci} n={n} clv_ok={clv_ok}"}
    if ci and ci[1] < 0:
        return {"verdict": "OVERPRICED", "basis": f"ROI {roi} CI {ci} excludes 0"}
    return {"verdict": "APPROXIMATELY_EFFICIENT", "basis": f"ROI {roi} CI {ci} includes 0"}


def overall_verdict(tier_verdicts: dict[str, str]) -> str:
    evaluable = [v for v in tier_verdicts.values() if v != "INSUFFICIENT_DATA"]
    if not evaluable:
        return "INSUFFICIENT 2026 MARKET DATA"
    if all(v == "EVIDENCE_OF_UNDERPRICING" for v in evaluable):
        return "2026 CONTROL SHOWS MARKET UNDERPRICING"
    if all(v == "APPROXIMATELY_EFFICIENT" for v in evaluable):
        return "2026 CONTROL FOOTBALL SIGNAL HOLDS BUT MARKET IS APPROXIMATELY EFFICIENT"
    if all(v == "OVERPRICED" for v in evaluable):
        return "2026 CONTROL SIDE IS OVERPRICED BY THE MARKET"
    return "2026 CONTROL RESULTS ARE MIXED BY TIER / PRICE"


def bucket_table(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for label in BUCKET_LABELS:
        cell = [r for r in rows if r["primary"]["economics"]["available"] and r["primary"]["bucket"] == label]
        e = economics_summary(cell)
        c = clv_summary(cell)
        out[label] = {
            "n": e["n"],
            "wins": e.get("wins", 0),
            "win_rate": e.get("win_rate"),
            "entry_price_mean": e.get("entry_price_mean"),
            "roi_on_outlay": e.get("roi_on_outlay"),
            "roi_ci95": (e.get("roi_on_outlay_bootstrap") or {}).get("ci95"),
            "fee_adjusted_pnl": e.get("fee_adjusted_pnl"),
            "win_rate_wilson95": e.get("win_rate_wilson95"),
            "clv_n": c["clv_n"],
            "mean_clv": c.get("mean_clv"),
            "sample_label": "LOW_SAMPLE" if e["n"] < 20 else ("CAUTION" if e["n"] < 50 else "OK"),
            "status": "EXPLORATORY",
        }
    return out


def slice_table(rows: list[dict[str, Any]], key: Callable[[dict], Any], labels: Iterable[Any]) -> dict[str, Any]:
    out = {}
    for label in labels:
        cell = [r for r in rows if key(r) == label]
        e = economics_summary(cell)
        out[str(label)] = {
            "eligible": len(cell),
            "n": e["n"],
            "wins": e.get("wins", 0),
            "win_rate": e.get("win_rate"),
            "entry_price_mean": e.get("entry_price_mean"),
            "roi_on_outlay": e.get("roi_on_outlay"),
            "roi_ci95": (e.get("roi_on_outlay_bootstrap") or {}).get("ci95"),
            "fee_adjusted_pnl": e.get("fee_adjusted_pnl"),
            "status": "EXPLORATORY",
        }
    return out


def group_report(rows: list[dict[str, Any]], tier: str | None) -> dict[str, Any]:
    econ = economics_summary(rows)
    clv = clv_summary(rows)
    return {
        "coverage": coverage(rows),
        "football": football_summary(rows, tier),
        "economics": econ,
        "economics_secondary_opponent_no": economics_summary(rows, "secondary"),
        "clv": clv,
        "clv_secondary_opponent_no": clv_summary(rows, "secondary"),
        "market_scoring": market_scoring(rows, lambda r: r["tier"]),
        "movement": movement_summary(rows),
        "price_buckets": bucket_table(rows),
    }


def build_report(rows: list[dict[str, Any]], populations: Iterable[str]) -> dict[str, Any]:
    """Every registered table, per population and pooled, tiers before pools, fixed order."""
    rows = canonical_order(rows)
    scopes: dict[str, list[dict[str, Any]]] = {"POOLED": rows}
    for pop in populations:
        scopes[pop] = [r for r in rows if r["population"] == pop]
    report: dict[str, Any] = {}
    for scope, scope_rows in scopes.items():
        groups = {}
        for g in GROUP_ORDER:
            g_rows = [r for r in scope_rows if r["tier"] in group_tiers(g)]
            groups[g] = group_report(g_rows, g if g in TIERS else None)
            if g in TIERS:
                groups[g]["verdict"] = verdict(groups[g]["economics"], groups[g]["clv"])
        verdicts = {t: groups[t]["verdict"]["verdict"] for t in TIERS}
        report[scope] = {
            "groups": groups,
            "overall_verdict": overall_verdict(verdicts),
            "slices": slices(scope_rows),
        }
    return report


def slices(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for g in GROUP_ORDER:
        g_rows = [r for r in rows if r["tier"] in group_tiers(g)]
        out[g] = {
            "season_block": slice_table(g_rows, lambda r: r["season_block"], SEASON_BLOCKS),
            "week": slice_table(
                g_rows, lambda r: r["week"], sorted({r["week"] for r in rows if r["week"] is not None})
            ),
            "data_confidence": slice_table(g_rows, lambda r: r["data_confidence"], ("HIGH", "MEDIUM", "LOW")),
            "prior_games_bucket": slice_table(g_rows, lambda r: r["prior_games_bucket"], ("0", "1", "2", "3", "4+")),
            "matchup_divisions": slice_table(
                g_rows, lambda r: "FCS_INVOLVED" if r["fcs_involved"] else "FBS_VS_FBS", ("FBS_VS_FBS", "FCS_INVOLVED")
            ),
            "entry_source": slice_table(
                g_rows, lambda r: r["primary"]["entry"]["source"], ("RESEARCH_OBSERVATION", "CATALOG_SNAPSHOT")
            ),
            "replay_eligibility": slice_table(
                g_rows,
                lambda r: "ELIGIBLE" if r.get("replay_eligibility") is None else "NOT_ELIGIBLE",
                ("ELIGIBLE", "NOT_ELIGIBLE"),
            ),
        }
    return out


def conclusion_language_ok(text: str) -> bool:
    """False if a conclusion field uses recommendation language."""
    lowered = text.lower()
    for word in BANNED_CONCLUSION_WORDS:
        if word.startswith("+"):
            if word in lowered:
                return False
        elif re.search(rf"\b{re.escape(word)}\b", lowered):
            return False
    return True


#: Games whose final score was seen incidentally while debugging orientation, before the freeze
#: (disclosed in docs/CONTROL_2026_MARKET_RESULTS.md). Reported with and without them.
INCIDENTALLY_SEEN_BEFORE_FREEZE = ("401856802", "401864574")


def sensitivities(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Pre-declared descriptive sensitivities of the primary economics. They cannot change a verdict."""
    filters: dict[str, Callable[[dict], bool]] = {
        "FBS_VS_FBS_ONLY": lambda r: not r["fcs_involved"],
        "REPLAY_ELIGIBLE_ONLY": lambda r: r.get("replay_eligibility") is None,
        "EXCLUDING_INCIDENTALLY_SEEN_BEFORE_FREEZE": lambda r: r["game_id"] not in INCIDENTALLY_SEEN_BEFORE_FREEZE,
    }
    out = {}
    for name, keep in filters.items():
        kept = [r for r in canonical_order(rows) if keep(r)]
        out[name] = {g: economics_summary([r for r in kept if r["tier"] in group_tiers(g)]) for g in GROUP_ORDER}
    return out
