"""Price every listed Kalshi side from ONE game-level handicap, with the repository's existing handicap pricer. PURE.

*** RESEARCH ONLY ***
This module reuses ``execution.handicap.PeriodDistribution``, ``execution.uncertainty.scenarios`` and
``execution.evaluator.fair_from_distribution`` as pure functions. It imports no order, approval, qualification or
persistence code, changes none of it, and produces no stake, order or approval.

Two kinds of handicap, never confused:

* an ANALYST handicap (``cfb_game_handicaps/1.0.0``): the analyst's own score distribution per period, with a stated
  uncertainty region. Every priceable side it can price becomes an analyst evaluation (source ``ANALYST_HANDICAP``):
  ``EVALUATED_CANDIDATE`` when the LOWER bound over the whole region clears the fee-inclusive break-even at the ask,
  else ``EVALUATED_PASS``. The Bet Up To comes from that conservative lower bound. It is the analyst's opinion
  (``calibrated`` is always false). A handicap with no stated region is a point estimate: it can pass, never be a
  candidate, and earns no ceiling.
* a HISTORICAL SEED (``seed_handicap``): a DRAFT built from the packet's reference class so an analyst has an
  evidence-supported starting point (means, spreads and correlations are reference-class statistics; the region comes
  from the baseline's stated uncertainty, the matchup QB/absence multiplier and the sampling error of the spread and
  correlation). A draft is flagged ``draft: true`` and is REFUSED as an analyst handicap until the analyst signs it. It
  is used only as an independent cross-check of the empirical estimates.

Contracts the closed-form pricer cannot express (ties, overtime, the 1H/game joint, props) are reported with a specific
evidence gap; no probability is invented for them.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from cfb_edge_finder.execution.evaluator import fair_from_distribution
from cfb_edge_finder.execution.handicap import HandicapValidationError, PeriodDistribution
from cfb_edge_finder.execution.uncertainty import HandicapUncertainty, scenarios
from cfb_edge_finder.research_packet import derived_estimates as de
from cfb_edge_finder.research_packet import history as hist

HANDICAPS_SCHEMA = "cfb_game_handicaps/1.0.0"
SRC_ANALYST_HANDICAP = "ANALYST_HANDICAP"
SRC_SEED = "HISTORICAL_SEEDED_HANDICAP"

PERIOD_NAME = {
    "full_game": "full_game",
    "h1": "first_half",
    "h2": "second_half",
    "q1": "first_quarter",
    "q2": "second_quarter",
    "q3": "third_quarter",
    "q4": "fourth_quarter",
}
HIST_PERIOD = {"full_game": "game", "h1": "h1", "h2": "h2", "q1": "q1", "q2": "q2", "q3": "q3", "q4": "q4"}
SEED_MIN_N = hist.MIN_COMPARABLES


# ------------------------------------------------------------------------------------------------ contract semantics


def semantics_for(side: dict[str, Any], codes: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    """The pricer's semantics for a listed side's CONTRACT (YES meaning), or (None, the specific evidence gap)."""
    kind, family = side.get("kind"), side.get("family")
    period = PERIOD_NAME.get(side.get("period") or "")
    cs = side.get("contract_side")
    if family == "winning_margin":
        sp = de._special({"ticker": side["ticker"], "family": family}, codes)
        if sp is None:
            return None, "winning-margin bucket could not be parsed from the ticker"
        return {
            "kind": "margin_band",
            "period": "full_game",
            "team": sp["side"],
            "line": float(sp["lo"]),
            "comparator": "greater_or_equal",
            "cap": None if sp["hi"] is None else float(sp["hi"]),
        }, None
    if family == "overtime":
        return None, "overtime needs a discrete tie/overtime model; a normal score distribution cannot express it"
    if family == "unknown" or kind == "see_title":
        if str(side.get("ticker", "")).startswith("KXNCAAF1HFT"):
            return (
                None,
                "the 1H/game-result joint needs cross-period correlation, which the handicap does not state",
            )
        return None, f"{family} is not a score-distribution contract (no drive-order or stat data in a handicap)"
    if period is None:
        return None, f"period {side.get('period')!r} has no score-distribution route"
    if kind == "win":
        if side.get("contract_outcome") == "tie":
            return None, "a tie probability needs a discrete score model; a normal distribution cannot express it"
        if cs not in ("home", "away"):
            return None, "moneyline team could not be resolved"
        return {"kind": "moneyline", "period": period, "team": cs, "comparator": "greater"}, None
    if kind == "margin_over":
        if cs not in ("home", "away") or side.get("line") is None:
            return None, "spread team or line could not be resolved"
        return {
            "kind": "spread",
            "period": period,
            "team": cs,
            "line": float(side["line"]),
            "comparator": "greater",
        }, None
    if kind == "total_over" and side.get("line") is not None:
        return {
            "kind": "total",
            "period": period,
            "team": "none",
            "line": float(side["line"]),
            "comparator": "greater",
        }, None
    if kind == "team_points_over" and cs in ("home", "away") and side.get("line") is not None:
        return {
            "kind": "team_total",
            "period": period,
            "team": cs,
            "line": float(side["line"]),
            "comparator": "greater",
        }, None
    return None, f"contract kind {kind!r} has no closed-form route"


def yes_probability_range(sem: dict[str, Any], dist: PeriodDistribution) -> tuple[float, float, float] | None:
    """(low, point, high) YES probability over the handicap's stated region (base case plus every corner)."""
    vals: list[float] = []
    base: float | None = None
    for sc in scenarios(dist.uncertainty):
        moved = (
            dist
            if sc.is_base
            else dist.perturbed(
                margin_shift=sc.margin_shift,
                total_shift=sc.total_shift,
                sd_scale=sc.sd_scale,
                correlation_shift=sc.correlation_shift,
            )
        )
        p = fair_from_distribution(sem, moved).value
        if p is None:
            return None
        vals.append(float(p))
        if sc.is_base:
            base = float(p)
    return min(vals), float(base if base is not None else vals[0]), max(vals)


def price_sides(
    sides: list[dict[str, Any]],
    codes_by_game: dict[str, dict[str, str]],
    payloads: dict[str, dict[str, PeriodDistribution]],
    *,
    source: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Entries for every priceable side of every game that has a handicap, plus the gaps that stopped any side.

    ``entries`` use the audit's evaluation-entry shape (status EVALUATED_CANDIDATE / EVALUATED_PASS) and carry
    ``source``. Nothing is invented: a side the pricer cannot express stays out of ``entries`` and appears in ``gaps``.
    """
    entries: list[dict[str, Any]] = []
    gaps: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    priced = 0
    for s in sides:
        dists = payloads.get(s["game_key"])
        if dists is None or s["status"] == "UNPRICEABLE":
            continue
        sem, why = semantics_for(s, codes_by_game.get(s["game_key"]) or {})
        if sem is None:
            gaps[s["game_key"]][why or "unsupported"] += 1
            continue
        dist = dists.get(sem["period"])
        if dist is None:
            gaps[s["game_key"]][f"handicap supplied no score distribution for {sem['period']}"] += 1
            continue
        rng = yes_probability_range(sem, dist)
        if rng is None:
            gaps[s["game_key"]]["pricer returned no probability"] += 1
            continue
        lo, pt, hi = rng if s["side"] == "YES" else (1 - rng[2], 1 - rng[1], 1 - rng[0])
        lo, pt, hi = (min(0.995, max(0.005, x)) for x in (lo, pt, hi))
        point_only = not dist.uncertainty.supports_robustness
        clears = lo > float(s["all_in"])
        priced += 1
        entries.append(
            {
                "game_key": s["game_key"],
                "ticker": s["ticker"],
                "side": s["side"],
                "source": source,
                "status": "EVALUATED_CANDIDATE" if (clears and not point_only) else "EVALUATED_PASS",
                "reason_code": (
                    "POINT_ESTIMATE_NO_UNCERTAINTY"
                    if point_only
                    else "HANDICAP_LOWER_BOUND_CLEARS_BREAKEVEN"
                    if clears
                    else "HANDICAP_LOWER_BOUND_BELOW_BREAKEVEN"
                ),
                "reason": (
                    f"{source}: {sem['kind']} {sem['period']} priced from the game handicap; YES range over the stated "
                    f"region {lo:.3f}/{pt:.3f}/{hi:.3f} vs fee-inclusive break-even {float(s['all_in']):.3f} at the ask"
                ),
                "probability": {"low": lo, "point": pt, "high": hi},
            }
        )
    return entries, {"priced_sides": priced, "gaps": {k: dict(v) for k, v in gaps.items()}}


# ------------------------------------------------------------------------------------------------ analyst handicaps


def parse_handicaps(
    doc: Any,
) -> tuple[dict[str, dict[str, PeriodDistribution]], dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Validate an analyst handicaps file. Returns (period distributions by game, metadata by game, rejections).

    A game is accepted only with the analyst's name, a handicap time, at least one evidence source, the thesis and the
    opposing case, and NOT flagged ``draft``. A rejected game is priced by nobody: the audit names why.
    """
    rejections: list[dict[str, str]] = []
    if not isinstance(doc, dict) or doc.get("schema_version") != HANDICAPS_SCHEMA:
        return (
            {},
            {},
            [{"game_key": "*", "code": "HANDICAPS_SCHEMA", "detail": f"expected schema_version {HANDICAPS_SCHEMA!r}"}],
        )
    dists: dict[str, dict[str, PeriodDistribution]] = {}
    meta: dict[str, dict[str, Any]] = {}
    for h in doc.get("handicaps") or []:
        gk = str(h.get("game_key"))

        def reject(code: str, detail: str, gk: str = gk) -> None:
            rejections.append({"game_key": gk, "code": code, "detail": detail})

        if gk in dists or gk in {r["game_key"] for r in rejections}:
            reject("DUPLICATE_HANDICAP", "more than one handicap for this game; all rejected")
            dists.pop(gk, None)
            meta.pop(gk, None)
            continue
        if h.get("draft") or (h.get("payload") or {}).get("draft"):
            reject("HANDICAP_IS_DRAFT", "a historical-seed draft is not an analyst handicap until the analyst signs it")
            continue
        missing = [k for k in ("analyst", "handicapped_at") if not str(h.get(k) or "").strip()]
        if missing:
            reject("HANDICAP_UNSIGNED", f"missing {', '.join(missing)}")
            continue
        ev = h.get("evidence")
        if not isinstance(ev, list) or not ev or not all(isinstance(x, str) and x.strip() for x in ev):
            reject("HANDICAP_WITHOUT_EVIDENCE", "name the evidence (sources and timestamps) behind the handicap")
            continue
        payload = h.get("payload") or {}
        if not str(payload.get("thesis") or "").strip() or not str(payload.get("opposing_case") or "").strip():
            reject("HANDICAP_WITHOUT_THESIS", "state the thesis and the strongest opposing case")
            continue
        try:
            periods = {
                name: PeriodDistribution.parse(name, pd)
                for name, pd in (payload.get("period_distributions") or {}).items()
            }
        except HandicapValidationError as exc:
            reject("HANDICAP_INVALID", str(exc))
            continue
        if not periods:
            reject("HANDICAP_INVALID", "no period distributions")
            continue
        unknown = sorted(set(periods) - set(PERIOD_NAME.values()))
        if unknown:
            reject("HANDICAP_INVALID", f"unknown periods {unknown}")
            continue
        dists[gk] = periods
        meta[gk] = {
            "analyst": h["analyst"],
            "handicapped_at": h["handicapped_at"],
            "evidence": ev,
            "thesis": payload.get("thesis"),
            "opposing_case": payload.get("opposing_case"),
            "periods": sorted(periods),
            "region_stated": {n: d.uncertainty.supports_robustness for n, d in periods.items()},
        }
    return dists, meta, rejections


# ------------------------------------------------------------------------------------------------ historical seed


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _sd(xs: list[float]) -> float:
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _corr(a: list[float], b: list[float]) -> float:
    ma, mb = _mean(a), _mean(b)
    sa, sb = _sd(a), _sd(b)
    if sa == 0 or sb == 0:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / ((len(a) - 1) * sa * sb)


def seed_handicap(
    chosen: list[dict[str, Any]], qb: dict[str, Any] | None, baseline_uncertainty: float
) -> dict[str, Any] | None:
    """A DRAFT handicap from the reference class: reference-class means, spreads and correlations per period.

    Region: the baseline's stated uncertainty times the matchup multiplier moves the margin and the total (scaled by
    the period's share of scoring); the spread's and the correlation's own sampling error set the sd-scale and
    correlation bands. All data-derived or the stated policy; nothing hand-tuned. None when the class is too thin.
    """
    if len(chosen) < SEED_MIN_N:
        return None
    mh = (qb or {}).get("home", {}).get("multiplier", 1.0)
    ma_ = (qb or {}).get("away", {}).get("multiplier", 1.0)
    game_points = _mean([g["actual_home"] + g["actual_away"] for g in chosen])
    out: dict[str, Any] = {}
    for period, pname in PERIOD_NAME.items():
        hp_key = HIST_PERIOD[period]
        pairs = []
        for g in chosen:
            if hp_key == "game":
                pairs.append((g["actual_home"], g["actual_away"]))
            else:
                v = hist.period_view(g)
                if v is not None:
                    pairs.append(v[hp_key])
        if len(pairs) < SEED_MIN_N:
            continue
        h = [p[0] for p in pairs]
        a = [p[1] for p in pairs]
        n = len(pairs)
        r = max(-0.9, min(0.9, _corr(h, a)))
        share = (_mean(h) + _mean(a)) / game_points if game_points else 1.0
        se_sd = 1.96 / math.sqrt(2 * (n - 1))
        d_rho = min(1.96 * (1 - r * r) / math.sqrt(n - 3), 0.99 - abs(r) - 1e-6)
        shift = baseline_uncertainty * (mh + ma_) * share
        out[pname] = {
            "home_mean": round(_mean(h), 3),
            "away_mean": round(_mean(a), 3),
            "home_sd": round(max(_sd(h), 1e-3), 3),
            "away_sd": round(max(_sd(a), 1e-3), 3),
            "correlation": round(r, 4),
            "uncertainty": {
                "margin_points": round(shift, 3),
                "total_points": round(shift, 3),
                "sd_scale_low": round(1 - se_sd, 4),
                "sd_scale_high": round(1 + se_sd, 4),
                "correlation_delta": round(max(d_rho, 0.0), 4),
            },
            "reference_n": n,
        }
    return out or None


def seeded_distributions(seed: dict[str, Any]) -> dict[str, PeriodDistribution]:
    out = {}
    for name, p in seed.items():
        payload = {k: v for k, v in p.items() if k != "reference_n"}
        out[name] = PeriodDistribution.parse(name, payload)
    return out


def ranges_overlap(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    return bool(a and b and a["low"] <= b["high"] and b["low"] <= a["high"])


__all__ = [
    "HANDICAPS_SCHEMA",
    "HandicapUncertainty",
    "SRC_ANALYST_HANDICAP",
    "SRC_SEED",
    "parse_handicaps",
    "price_sides",
    "ranges_overlap",
    "seed_handicap",
    "seeded_distributions",
    "semantics_for",
    "yes_probability_range",
]
