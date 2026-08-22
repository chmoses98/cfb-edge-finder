"""GameDistribution -> market probability math.

This is the core of the "one game-level distribution prices many contracts"
pattern (mission spec section 3). Every function here is closed-form and
cheap -- pricing a new market from an existing GameDistribution never
requires rerunning a football model. A Kalshi price update should trigger a
call into this module (or a cached read of a previous call), never a
football-model rerun, unless the underlying game inputs actually changed.

Modeling assumptions (all explicit, all deliberately simple for V1 -- see
docs/ARCHITECTURE.md):

* Each team's score is treated as approximately Normal. Margin and total
  are then also Normal, with parameters derived analytically from the two
  team distributions and their correlation.
* Real football scores are integers, so a 0.5-point continuity correction
  is applied when evaluating P(X > threshold) / P(X < threshold), matching
  standard practice for approximating a discrete distribution with a
  continuous one.
* Push probability (the rare case of an exact-integer margin/total landing
  precisely on a whole-number line) is not separately modeled; it is
  implicitly the small gap between P(over) and (1 - P(under)) for whole
  number lines. This is a known, documented limitation, not an oversight.
* First-half markets are NOT priced by this module -- they require a
  separate first-half GameDistribution (a future milestone), because
  full-game score parameters cannot be decomposed into a first-half
  distribution without additional modeling. Attempting to price a
  first-half MarketFamily here raises UnsupportedMarketFamilyError so the
  caller can record MarketStatus.UNSUPPORTED_MARKET rather than silently
  mispricing it.
"""

from __future__ import annotations

from statistics import NormalDist

from cfb_edge_finder.schemas.common import MarketFamily, Side
from cfb_edge_finder.schemas.projection import GameDistribution

CONTINUITY_CORRECTION = 0.5

_FIRST_HALF_FAMILIES = frozenset(
    {
        MarketFamily.FIRST_HALF_MONEYLINE,
        MarketFamily.FIRST_HALF_SPREAD,
        MarketFamily.FIRST_HALF_TOTAL,
    }
)


class UnsupportedMarketFamilyError(ValueError):
    """Raised when asked to price a market family this pricer does not (yet) support."""


def margin_distribution(d: GameDistribution) -> NormalDist:
    """Distribution of (home_score - away_score)."""
    mean = d.home_mean - d.away_mean
    variance = d.home_sd**2 + d.away_sd**2 - 2 * d.correlation * d.home_sd * d.away_sd
    return NormalDist(mean, max(variance, 1e-9) ** 0.5)


def total_distribution(d: GameDistribution) -> NormalDist:
    """Distribution of (home_score + away_score)."""
    mean = d.home_mean + d.away_mean
    variance = d.home_sd**2 + d.away_sd**2 + 2 * d.correlation * d.home_sd * d.away_sd
    return NormalDist(mean, max(variance, 1e-9) ** 0.5)


def team_total_distribution(d: GameDistribution, side: Side) -> NormalDist:
    if side == Side.HOME:
        return NormalDist(d.home_mean, d.home_sd)
    if side == Side.AWAY:
        return NormalDist(d.away_mean, d.away_sd)
    raise ValueError(f"team_total_distribution requires Side.HOME or Side.AWAY, got {side!r}")


def prob_greater_than(dist: NormalDist, threshold: float, continuity: float = CONTINUITY_CORRECTION) -> float:
    return min(1.0, max(0.0, 1 - dist.cdf(threshold + continuity)))


def prob_less_than(dist: NormalDist, threshold: float, continuity: float = CONTINUITY_CORRECTION) -> float:
    return min(1.0, max(0.0, dist.cdf(threshold - continuity)))


def prob_home_win(d: GameDistribution) -> float:
    return prob_greater_than(margin_distribution(d), 0.0)


def prob_away_win(d: GameDistribution) -> float:
    return prob_less_than(margin_distribution(d), 0.0)


def prob_home_covers(d: GameDistribution, home_line: float) -> float:
    """home_line follows standard spread convention: negative = home favored.
    Home covers if (home_score + home_line) > away_score, i.e. margin > -home_line.
    """
    return prob_greater_than(margin_distribution(d), -home_line)


def prob_away_covers(d: GameDistribution, home_line: float) -> float:
    return prob_less_than(margin_distribution(d), -home_line)


def prob_over(d: GameDistribution, total_line: float) -> float:
    return prob_greater_than(total_distribution(d), total_line)


def prob_under(d: GameDistribution, total_line: float) -> float:
    return prob_less_than(total_distribution(d), total_line)


def prob_team_total_over(d: GameDistribution, side: Side, total_line: float) -> float:
    return prob_greater_than(team_total_distribution(d, side), total_line)


def prob_team_total_under(d: GameDistribution, side: Side, total_line: float) -> float:
    return prob_less_than(team_total_distribution(d, side), total_line)


def price_market(
    d: GameDistribution,
    market_family: MarketFamily,
    side: Side,
    line: float | None = None,
    team: Side | None = None,
) -> float:
    """Single dispatch entry point: (distribution, market spec) -> fair probability.

    Covers moneyline, spread, alt_spread, total, alt_total, and team_total --
    the exact set from the mission's Auburn/Baylor example. alt_spread and
    alt_total are priced identically to spread/total; they only differ in
    which `line` value is supplied by the caller.

    `team` is only used (and required) for TEAM_TOTAL, since that market
    needs both a team (Side.HOME/Side.AWAY) and a direction (Side.OVER/
    Side.UNDER, passed as `side`) -- the two are independent axes and are
    deliberately not conflated into one Side value.
    """
    if market_family in _FIRST_HALF_FAMILIES:
        raise UnsupportedMarketFamilyError(
            f"{market_family.value} requires a first-half GameDistribution, not yet built (see roadmap Milestone D+)"
        )

    if market_family == MarketFamily.MONEYLINE:
        if side == Side.HOME:
            return prob_home_win(d)
        if side == Side.AWAY:
            return prob_away_win(d)
        raise ValueError(f"moneyline requires Side.HOME or Side.AWAY, got {side!r}")

    if market_family in (MarketFamily.SPREAD, MarketFamily.ALT_SPREAD):
        if line is None:
            raise ValueError(f"{market_family.value} requires a line")
        if side == Side.HOME:
            return prob_home_covers(d, line)
        if side == Side.AWAY:
            return prob_away_covers(d, line)
        raise ValueError(f"{market_family.value} requires Side.HOME or Side.AWAY, got {side!r}")

    if market_family in (MarketFamily.TOTAL, MarketFamily.ALT_TOTAL):
        if line is None:
            raise ValueError(f"{market_family.value} requires a line")
        if side == Side.OVER:
            return prob_over(d, line)
        if side == Side.UNDER:
            return prob_under(d, line)
        raise ValueError(f"{market_family.value} requires Side.OVER or Side.UNDER, got {side!r}")

    if market_family == MarketFamily.TEAM_TOTAL:
        if line is None:
            raise ValueError("team_total requires a line")
        if team not in (Side.HOME, Side.AWAY):
            raise ValueError(f"team_total requires team=Side.HOME or Side.AWAY, got {team!r}")
        if side == Side.OVER:
            return prob_team_total_over(d, team, line)
        if side == Side.UNDER:
            return prob_team_total_under(d, team, line)
        raise ValueError(f"team_total requires side=Side.OVER or Side.UNDER, got {side!r}")

    raise UnsupportedMarketFamilyError(f"no pricer implemented for {market_family!r}")
