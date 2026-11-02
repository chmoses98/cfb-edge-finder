"""Separate bad handicaps, bad pricing, bad expressions and normal variance -- only where evidence supports it.

A loss is not evidence of a mistake. Four causes are distinguishable ONLY with specific evidence:

``BAD_PRICING``     we paid above what a calibrated pregame probability supported: fill > bet-up-to
                    (needs a usable calibrated estimate).
``BAD_HANDICAP``    the price was acceptable under our own belief, but the market closed against us
                    (negative fee-adjusted CLV) -- the belief was wrong (needs estimate AND close quote).
``BAD_EXPRESSION``  an equivalent expression of the SAME truth condition was available cheaper at fill time
                    (needs the alternative's all-in cost; see ``expression/economics.find_dominated_expressions``).
``VARIANCE``        a close quote exists, the closing price was at least as good as our fill after fees
                    (CLV >= -tolerance), and nothing above applies: no measurable process failure.
                    This rests on the close being efficient; it is the null, not a proof.
``INSUFFICIENT_EVIDENCE``  anything else. Includes the common case of a negative CLV with no pregame
                    probability -- handicap and pricing are NOT separable then, and we do not pick one.

Nothing is inferred when evidence is absent; each verdict carries the evidence it used (``basis``) or the
evidence it lacked (``missing``).

``family_performance`` reports ROI per family with a CLUSTER bootstrap (resampling games, because the
positions inside one game are correlated) so a hot 28-bet family is shown with its interval, not a headline.
"""

from __future__ import annotations

from collections import defaultdict
from enum import StrEnum
from typing import Any

import numpy as np

from cfb_edge_finder.expression.fair_value import ProbabilityEstimate, bet_up_to, order_fee_dollars
from cfb_edge_finder.expression.ticker_ontology import Family, parse_ticker

#: Owner-adjustable: CLV within this many dollars-per-contract of zero is treated as noise.
CLV_NOISE_TOLERANCE = 0.01
#: A bootstrap interval over fewer independent games than this is not reported as informative.
MIN_GAMES_FOR_CI = 10


class LossCause(StrEnum):
    BAD_PRICING = "BAD_PRICING"
    BAD_HANDICAP = "BAD_HANDICAP"
    BAD_EXPRESSION = "BAD_EXPRESSION"
    VARIANCE = "VARIANCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def classify_loss(
    record: dict[str, Any],
    *,
    estimate: ProbabilityEstimate | None = None,
    cheaper_equivalent_all_in: float | None = None,
    clv_tolerance: float = CLV_NOISE_TOLERANCE,
) -> dict[str, Any]:
    """Classify ONE lost position from its reconciliation record. Only call for settled losses."""
    ex = record["execution"]
    fill, contracts = ex.get("average_price"), ex.get("contracts")
    series = record["ticker"].split("-")[0]
    fee_pc = None
    if fill and contracts:
        fee = order_fee_dollars(float(fill), float(contracts), series)
        fee_pc = None if fee is None else fee / contracts
    all_in = None if fill is None or fee_pc is None else fill + fee_pc
    close_mid = (record.get("close") or {}).get("close_price_for_side_mid")
    clv_after_fee = None if close_mid is None or all_in is None else round(close_mid - all_in, 6)
    missing: list[str] = []
    basis: list[str] = []

    if record["ontology"]["family"] == Family.COMBO_UNDECODED.value:
        return _out(LossCause.INSUFFICIENT_EVIDENCE, basis, ["UNDECODED_COMBO_LEGS"], clv_after_fee, all_in)

    if cheaper_equivalent_all_in is not None and all_in is not None and cheaper_equivalent_all_in < all_in - 1e-9:
        basis.append(f"equivalent expression cheaper by {all_in - cheaper_equivalent_all_in:.4f}/contract")
        return _out(LossCause.BAD_EXPRESSION, basis, missing, clv_after_fee, all_in)

    usable = estimate is not None and estimate.usable
    if not usable:
        missing.append("CALIBRATED_PREGAME_PROBABILITY")
    if close_mid is None:
        missing.append("CLOSE_QUOTE")

    if usable and fill is not None and contracts:
        cap = bet_up_to(estimate, float(contracts), series)
        if cap is None or fill > cap + 1e-9:
            basis.append(
                f"fill {fill:.2f} above bet-up-to {cap if cap is not None else 'none (no price clears break-even)'}"
            )
            return _out(LossCause.BAD_PRICING, basis, missing, clv_after_fee, all_in)
        if clv_after_fee is not None and clv_after_fee < -clv_tolerance:
            basis.append(f"price within bet-up-to but market closed against us by {-clv_after_fee:.4f} after fees")
            return _out(LossCause.BAD_HANDICAP, basis, missing, clv_after_fee, all_in)

    if clv_after_fee is not None and clv_after_fee >= -clv_tolerance:
        basis.append(f"closing price at least as good as fill after fees (CLV {clv_after_fee:+.4f})")
        return _out(LossCause.VARIANCE, basis, missing, clv_after_fee, all_in)

    if clv_after_fee is not None and clv_after_fee < -clv_tolerance and not usable:
        missing.append("HANDICAP_VS_PRICING_NOT_SEPARABLE_WITHOUT_PREGAME_PROBABILITY")
    return _out(LossCause.INSUFFICIENT_EVIDENCE, basis, missing, clv_after_fee, all_in)


def _out(
    cause: LossCause, basis: list[str], missing: list[str], clv: float | None, all_in: float | None
) -> dict[str, Any]:
    return {
        "cause": cause.value,
        "basis": basis,
        "missing": sorted(set(missing)),
        "clv_after_fee": clv,
        "all_in_cost_per_contract": all_in,
    }


# --------------------------------------------------------------------------------------------- performance
def family_label(ticker: str) -> str:
    info = parse_ticker(ticker)
    if info.family is Family.COMBO_UNDECODED:
        return "COMBO_UNDECODED"
    if info.period is None:
        return "UNKNOWN"
    return f"{info.period.value}_{info.family.value}"


def family_performance(
    records: list[dict[str, Any]], *, reps: int = 4000, seed: int = 20261102
) -> dict[str, dict[str, Any]]:
    """Per family: n, stake, net, ROI, and a game-clustered bootstrap 95% interval on ROI.

    Net comes from the settlement (already fee-inclusive: net = gross - stake). Only settled records count.
    """
    rng = np.random.default_rng(seed)
    groups: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    for r in records:
        s = r.get("settlement")
        if not s or s.get("net_pnl") is None or r["execution"].get("stake") is None:
            continue
        game = r["ontology"]["game_key"] or f"combo:{r['record_id']}"
        groups[family_label(r["ticker"])].append((game, float(r["execution"]["stake"]), float(s["net_pnl"])))
    out: dict[str, dict[str, Any]] = {}
    for fam, rows in groups.items():
        stake = sum(x[1] for x in rows)
        net = sum(x[2] for x in rows)
        by_game: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for g, st, nt in rows:
            by_game[g][0] += st
            by_game[g][1] += nt
        arr = np.array(list(by_game.values()))
        n_games = len(arr)
        idx = rng.integers(0, n_games, size=(reps, n_games))
        rs, rn = arr[idx, 0].sum(axis=1), arr[idx, 1].sum(axis=1)
        roi = rn / np.where(rs > 0, rs, np.nan)
        lo, hi = (float(np.nanquantile(roi, 0.025)), float(np.nanquantile(roi, 0.975)))
        out[fam] = {
            "n_bets": len(rows),
            "n_games": n_games,
            "stake": round(stake, 2),
            "net": round(net, 2),
            "roi": round(net / stake, 4) if stake else None,
            "roi_ci95_game_cluster_bootstrap": [round(lo, 4), round(hi, 4)],
            "ci_reliable": n_games >= MIN_GAMES_FOR_CI,
            "ci_excludes_zero": bool(n_games >= MIN_GAMES_FOR_CI and (lo > 0 or hi < 0)),
        }
    return out
