"""Contract probabilities under the frozen V2 distribution.

*** THE HALF-POINT CORRECTION (mission section 14) ***
The research audit found a real correctness bug in the V1 path: it
applies a continuity correction on top of Kalshi's half-point strikes.

Football margins and totals are INTEGERS. A continuity correction exists
to translate "P(integer outcome > k)" into a continuous model's language:

    P(X > 3)  == P(X >= 4)  == 1 - F(3.5)      <- correction needed
    P(X > 3.5)                == 1 - F(3.5)      <- ALREADY exact

A half-point strike cannot be landed on, so it needs no correction at
all. Applying one anyway shifts the threshold to 4.0 and prices a
different contract than the one being traded -- a systematic, one-sided
error of half a point on every half-point market, which is most of them.

So: correct integer thresholds, use half-point thresholds verbatim. This
is implemented ONLY in the V2 path. 0.5.0's live and historical semantics
are deliberately untouched by this mission -- changing them would
silently redefine what every already-captured 0.5.0 row means.

Ported from `research/v2/uncertainty.prob_greater` with the Normal branch
only: the frozen spec records that Student-t was not selected for margin,
so production carries no branch it would never take.
"""

from __future__ import annotations

import numpy as np
from scipy import stats

CONTINUITY = 0.5
"""Half a point, applied only to integer thresholds -- see the module
docstring for why a half-point strike must never receive it."""


def effective_threshold(threshold: float, continuity: float = CONTINUITY) -> float:
    """The cut point a continuous model should integrate from.

    Integer threshold -> shifted by the continuity correction.
    Half-point threshold -> returned unchanged.
    """
    t = float(threshold)
    return t + continuity if float(np.isclose(t, round(t))) else t


def contract_probability(
    point: float, sd: float, threshold: float, *, continuity: float = CONTINUITY
) -> float:
    """P(outcome > threshold) for an integer-valued outcome.

    `point` and `sd` are the frozen V2 prediction and its conditional
    scale. Normal tails, per the frozen spec."""
    if not sd > 0:
        raise ValueError(f"sd must be positive, got {sd!r}")
    cut = effective_threshold(threshold, continuity)
    return float(1.0 - stats.norm.cdf((cut - float(point)) / float(sd)))


def contract_probability_array(
    point: np.ndarray, sd: np.ndarray, threshold: np.ndarray, *, continuity: float = CONTINUITY
) -> np.ndarray:
    """Vectorised form, kept faithful to the research implementation's
    `np.where(is_int, t + continuity, t)`."""
    t = np.asarray(threshold, float)
    is_int = np.isclose(t, np.round(t))
    cut = np.where(is_int, t + continuity, t)
    return 1.0 - stats.norm.cdf((cut - np.asarray(point, float)) / np.asarray(sd, float))


def home_win_probability(pred_margin: float, sd_margin: float) -> float:
    """P(home wins) = P(margin > 0). Margin 0 is impossible in CFB
    (overtime resolves ties), but the threshold is still an integer, so
    the continuity correction applies and the arithmetic stays consistent
    with every other integer-threshold contract."""
    return contract_probability(pred_margin, sd_margin, 0.0)


def probability_less(point: float, sd: float, threshold: float, *, continuity: float = CONTINUITY) -> float:
    """P(outcome < threshold) for an integer-valued outcome.

    The mirror of `contract_probability`, and it needs its own function
    rather than `1 - P(>)`: for an INTEGER threshold those two differ by
    the push probability P(X == t), which is exactly the case a spread on
    an integer line can land on. This keeps the strictness convention the
    canonical path already uses (`prob_less_than` = P(X <= t-1)) and
    changes ONLY the half-point behaviour:

        integer t     -> Phi((t - 0.5 - mu) / sd)      (correction)
        half-point t  -> Phi((t - mu) / sd)            (exact already)
    """
    if not sd > 0:
        raise ValueError(f"sd must be positive, got {sd!r}")
    t = float(threshold)
    cut = t - continuity if float(np.isclose(t, round(t))) else t
    return float(stats.norm.cdf((cut - float(point)) / float(sd)))


def price_observation_v2(observation, prediction) -> tuple[float | None, str]:
    """V2's probability for the contract a canonical observation records.

    Reads the observation's OWN parse -- `family` (= parsed.market_family),
    `threshold` (= parsed.line), `side` (= parsed.side) and `team`
    (= the resolved home/away side) -- rather than re-parsing the market.
    That is deliberate: those fields are literally what
    `kalshi/ladder_pricing` priced the canonical row from, so the shadow
    and the canonical row provably agree about WHAT the contract is, and
    the only differences are the distribution and the continuity rule.

    Mirrors `kalshi/market_pricing.price_parsed_contract`'s branch
    structure and its spread sign convention (`home_line = -line` for a
    home-named contract, `+line` for an away-named one). Anything not
    positively recognised returns (None, reason); nothing raises.
    """
    from cfb_edge_finder.schemas.kalshi_observation import MarketFamily, Side

    family = getattr(observation, "family", None)
    line = getattr(observation, "threshold", None)
    side = getattr(observation, "side", None)
    team = getattr(observation, "team", None)

    if family in (MarketFamily.TOTAL, MarketFamily.ALT_TOTAL):
        if line is None or side is None:
            return None, "total contract missing line/side"
        if side == Side.OVER:
            return contract_probability(prediction.pred_total, prediction.sd_total, float(line)), (
                f"V2 P(total over {line})"
            )
        if side == Side.UNDER:
            return probability_less(prediction.pred_total, prediction.sd_total, float(line)), (
                f"V2 P(total under {line})"
            )
        return None, f"unsupported total side {side!r}"

    if family in (MarketFamily.SPREAD, MarketFamily.ALT_SPREAD):
        if team is None:
            return None, "spread contract has no resolved team side"
        if line is None:
            return None, "spread contract missing line"
        threshold = float(line)
        if team == Side.HOME:
            # home_line = -threshold; home covers iff margin > -home_line
            return contract_probability(prediction.pred_margin, prediction.sd_margin, threshold), (
                f"V2 P(home wins by more than {threshold})"
            )
        if team == Side.AWAY:
            # home_line = +threshold; away covers iff margin < -home_line
            return probability_less(prediction.pred_margin, prediction.sd_margin, -threshold), (
                f"V2 P(away covers {threshold})"
            )
        return None, f"unexpected team side {team!r}"

    if family == MarketFamily.MONEYLINE:
        if team == Side.HOME:
            return home_win_probability(prediction.pred_margin, prediction.sd_margin), "V2 P(home wins)"
        if team == Side.AWAY:
            return probability_less(prediction.pred_margin, prediction.sd_margin, 0.0), "V2 P(away wins)"
        return None, "moneyline contract has no resolved team side"

    return None, f"family {getattr(family, 'value', family)!r} is outside the frozen V2 spec"
