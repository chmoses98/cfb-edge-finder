"""Milestone D: turns a parsed Kalshi contract + a resolved game side into
a fair model probability, via the EXISTING, generic
`projections.distribution.price_market` -- this module contains no
probability math of its own (mission section 8's mandatory separation:
football probability must never bake in Kalshi-specific semantics, and
the reverse -- this module never touches GameDistribution internals,
only calls the one public `price_market` entry point).

*** THE SPREAD SIGN CONVENTION, DERIVED CAREFULLY (not guessed) ***
`price_market`'s SPREAD family takes a `home_line` in the standard
sportsbook convention: NEGATIVE = home favored (see
projections/distribution.py's `prob_home_covers` docstring: "home covers
if (home_score + home_line) > away_score, i.e. margin > -home_line").

A Kalshi spread contract's OWN grammar is different and confirmed from
real live evidence (contract_semantics.py): "<TEAM> wins by over <T>
points" for a NAMED team, not a signed home-relative number. Converting
one to the other:
  - named team is HOME, wants P(home_margin > T):
    P(margin > T) = prob_home_covers(d, home_line) needs -home_line = T,
    so home_line = -T.
  - named team is AWAY, wants P(away_margin > T) = P(home_margin < -T):
    P(margin < -T) = prob_away_covers(d, home_line) needs -home_line = -T,
    so home_line = T.
Both cases are implemented explicitly below, each with the derivation
inline -- never a single "just negate it" shortcut applied to both sides
by accident.
"""

from __future__ import annotations

from dataclasses import dataclass

from cfb_edge_finder.kalshi.contract_semantics import ParsedContract
from cfb_edge_finder.projections.distribution import UnsupportedMarketFamilyError, price_market
from cfb_edge_finder.schemas.common import MarketFamily, Side
from cfb_edge_finder.schemas.projection import GameDistribution


@dataclass(frozen=True)
class ModelPriceResult:
    model_probability: float | None
    detail: str
    error: str | None = None


def price_parsed_contract(
    parsed: ParsedContract,
    distribution: GameDistribution,
    *,
    named_team_side: Side | None,
) -> ModelPriceResult:
    """`named_team_side` is Side.HOME or Side.AWAY -- WHICH side of the
    mapped game `parsed.raw_team_name` resolved to (the caller determines
    this via teams.registry + the game mapping; this module has no team-
    identity knowledge of its own). Required for SPREAD and MONEYLINE
    (both are per-team contracts); ignored for TOTAL (a total has no team
    side at all)."""
    if parsed.reason is not None:
        return ModelPriceResult(
            model_probability=None, detail="parsing already failed upstream", error=parsed.reason.value
        )

    if parsed.market_family == MarketFamily.TOTAL:
        if parsed.line is None or parsed.side is None:
            return ModelPriceResult(
                model_probability=None, detail="total contract missing line/side", error="missing_line"
            )
        prob = price_market(distribution, MarketFamily.TOTAL, parsed.side, line=parsed.line)
        return ModelPriceResult(model_probability=prob, detail=f"P(total {parsed.side.value} {parsed.line})")

    if parsed.market_family == MarketFamily.SPREAD:
        if named_team_side is None:
            return ModelPriceResult(
                model_probability=None, detail="spread contract has no resolved team side", error="unresolved_side"
            )
        if parsed.line is None:
            return ModelPriceResult(model_probability=None, detail="spread contract missing line", error="missing_line")
        threshold = parsed.line
        if named_team_side == Side.HOME:
            home_line = -threshold  # see module docstring derivation
        elif named_team_side == Side.AWAY:
            home_line = threshold  # see module docstring derivation
        else:
            return ModelPriceResult(
                model_probability=None, detail=f"unexpected team side {named_team_side!r}", error="invalid_side"
            )
        prob = price_market(distribution, MarketFamily.SPREAD, named_team_side, line=home_line)
        return ModelPriceResult(
            model_probability=prob,
            detail=f"P({named_team_side.value} wins by more than {threshold}) via home_line={home_line}",
        )

    if parsed.market_family == MarketFamily.MONEYLINE:
        if named_team_side is None:
            return ModelPriceResult(
                model_probability=None, detail="moneyline contract has no resolved team side", error="unresolved_side"
            )
        prob = price_market(distribution, MarketFamily.MONEYLINE, named_team_side)
        return ModelPriceResult(model_probability=prob, detail=f"P({named_team_side.value} wins)")

    try:
        prob = price_market(distribution, parsed.market_family, parsed.side or Side.HOME, line=parsed.line)
    except UnsupportedMarketFamilyError as exc:
        return ModelPriceResult(model_probability=None, detail=str(exc), error="unsupported_family")
    return ModelPriceResult(model_probability=prob, detail="priced via generic dispatch")
