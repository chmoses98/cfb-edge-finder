"""Same-game exposure primitives for a FUTURE risk layer (mission
section 20).

*** THIS BUILDS NO CARD AND SELECTS NOTHING ***
It emits, for each contract, the identifiers a later risk engine would
need to notice that

    Team A ML YES,  Team B ML NO,  Team A -3.5 YES,  Team A -7.5 YES

are not four independent positions: the first two are the SAME event, and
all four move together with one final margin. Recognizing that is a
prerequisite for any future risk control. Constructing or ranking a set of
positions is explicitly not part of this milestone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from cfb_edge_finder.expression.taxonomy import ContractSemantics, MarketDimension, truth_condition_key
from cfb_edge_finder.schemas.common import MarketFamily, Side


class ExposureDirection(StrEnum):
    """Which way an expression leans on its dimension."""

    TEAM_FAVORABLE = "TEAM_FAVORABLE"
    """Pays when the named team does BETTER (wins, or covers a bigger margin)."""
    TEAM_UNFAVORABLE = "TEAM_UNFAVORABLE"
    HIGHER_TOTAL = "HIGHER_TOTAL"
    LOWER_TOTAL = "LOWER_TOTAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ContractExposure:
    """Everything a future risk layer needs to detect overlap."""

    market_ticker: str
    executable_side: Side
    game_id: str
    dimension: MarketDimension
    team_exposure: Side | None
    """Which team this expression benefits from, in HOME/AWAY terms.
    None for totals, which express no team lean."""
    direction: ExposureDirection
    threshold: float | None
    equivalence_group_key: str | None
    """None when semantics are unresolved -- such a contract must never be
    treated as sharing an event with anything."""


def build_exposure(semantics: ContractSemantics, executable_side: Side) -> ContractExposure:
    key = truth_condition_key(semantics, executable_side)
    team_exposure: Side | None = None
    direction = ExposureDirection.UNKNOWN

    if semantics.family in (MarketFamily.MONEYLINE, MarketFamily.SPREAD) and semantics.team in (
        Side.HOME,
        Side.AWAY,
    ):
        # A NO on "team T does well" is exposure to the OPPOSING team.
        if executable_side is Side.YES:
            team_exposure = semantics.team
            direction = ExposureDirection.TEAM_FAVORABLE
        else:
            team_exposure = Side.AWAY if semantics.team is Side.HOME else Side.HOME
            direction = ExposureDirection.TEAM_FAVORABLE
    elif semantics.family is MarketFamily.TOTAL:
        direction = (
            ExposureDirection.HIGHER_TOTAL if executable_side is Side.YES else ExposureDirection.LOWER_TOTAL
        )

    return ContractExposure(
        market_ticker=semantics.market_ticker,
        executable_side=executable_side,
        game_id=semantics.game_id,
        dimension=semantics.dimension,
        team_exposure=team_exposure,
        direction=direction,
        threshold=semantics.threshold,
        equivalence_group_key=key,
    )


def overlapping_exposures(exposures: list[ContractExposure]) -> dict[str, list[ContractExposure]]:
    """Groups exposures by game, the coarsest unit through which any two
    of them can move together. A future risk layer would start here."""
    grouped: dict[str, list[ContractExposure]] = {}
    for exposure in exposures:
        grouped.setdefault(exposure.game_id, []).append(exposure)
    return grouped
