"""Milestone E, Part E: postgame settlement.

Uses the SAME verified semantic operators Milestone D already parses
(contract_semantics.py's confirmed strict-">" grammar) -- never a generic
sportsbook rule. `extract_game_result` reads a raw CFBD `/games` row
defensively (candidate field-name keys, mirroring
ingestion/game_normalization.py's own `_first_present` pattern, since the
same two-schema-source disagreement documented there applies to score
fields too and has not been live-verified independently this pass).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from cfb_edge_finder.schemas.common import MarketFamily, Side
from cfb_edge_finder.schemas.kalshi_observation import KalshiResearchObservation
from cfb_edge_finder.schemas.settlement import (
    GameFinalStatus,
    GameResult,
    MarketSettlement,
    MarketSettlementStatus,
)

SETTLEMENT_VERSION = "settlement_v1"

_HOME_POINTS_KEYS = ("homePoints", "home_points")
_AWAY_POINTS_KEYS = ("awayPoints", "away_points")
_STATUS_TO_FINAL_STATUS = {
    "final": GameFinalStatus.FINAL,
    "completed": GameFinalStatus.FINAL,
    "postponed": GameFinalStatus.POSTPONED,
    "canceled": GameFinalStatus.CANCELED,
    "cancelled": GameFinalStatus.CANCELED,
}


def _first_present(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in raw and raw[key] is not None:
            return raw[key]
    return None


def extract_game_result(
    raw_cfbd_game: dict[str, Any], *, game_id: str, season: int, captured_at: datetime
) -> GameResult:
    home_points = _first_present(raw_cfbd_game, _HOME_POINTS_KEYS)
    away_points = _first_present(raw_cfbd_game, _AWAY_POINTS_KEYS)
    raw_status = str(raw_cfbd_game.get("status", "")).strip().lower()

    if raw_status in _STATUS_TO_FINAL_STATUS:
        status = _STATUS_TO_FINAL_STATUS[raw_status]
    elif raw_cfbd_game.get("completed") is True and home_points is not None and away_points is not None:
        status = GameFinalStatus.FINAL
    else:
        status = GameFinalStatus.NOT_YET_FINAL

    return GameResult(
        game_id=game_id,
        season=season,
        home_points=int(home_points) if home_points is not None else None,
        away_points=int(away_points) if away_points is not None else None,
        status=status,
        went_to_overtime=None,  # not exposed by CFBD's /games response -- see module docstring
        source_game_id=str(raw_cfbd_game.get("id")) if raw_cfbd_game.get("id") is not None else None,
        captured_at=captured_at,
    )


def settle_market(
    observation: KalshiResearchObservation, game_result: GameResult, *, settled_at: datetime
) -> MarketSettlement:
    """Settles ONE observation's market against the game's final result.
    Contract semantics are read directly from the observation's own
    already-parsed fields (family/threshold/side/team/semantic_operator)
    -- never re-derived. A game going to overtime changes nothing here:
    the contract settles on the FINAL score regardless of periods,
    exactly like contract_semantics.py's winner-market grammar already
    documents."""
    base = dict(
        game_id=observation.game_id or "",
        kalshi_market_ticker=observation.kalshi_market_ticker,
        family=observation.family,
        settled_at=settled_at,
        settlement_version=SETTLEMENT_VERSION,
        game_result=game_result,
    )

    if game_result.status == GameFinalStatus.POSTPONED:
        return MarketSettlement(**base, status=MarketSettlementStatus.VOID_POSTPONED, detail="game postponed")
    if game_result.status == GameFinalStatus.CANCELED:
        return MarketSettlement(**base, status=MarketSettlementStatus.VOID_CANCELED, detail="game canceled")
    if game_result.status == GameFinalStatus.NOT_YET_FINAL:
        return MarketSettlement(**base, status=MarketSettlementStatus.PENDING_NOT_FINAL, detail="game not yet final")
    if game_result.home_points is None or game_result.away_points is None:
        return MarketSettlement(
            **base, status=MarketSettlementStatus.UNSETTLEABLE_MISSING_FIELDS, detail="final status but no score"
        )

    home_margin = float(game_result.home_points - game_result.away_points)
    total_points = float(game_result.home_points + game_result.away_points)
    actual_winner = Side.HOME if home_margin > 0 else Side.AWAY
    common = dict(actual_winner=actual_winner, actual_home_margin=home_margin, actual_total_points=total_points)

    if observation.semantic_operator is not None and observation.semantic_operator != ">":
        return MarketSettlement(
            **base,
            **common,
            status=MarketSettlementStatus.UNSETTLEABLE_UNKNOWN_OPERATOR,
            detail=f"semantic_operator {observation.semantic_operator!r} has no settlement rule implemented",
        )

    if observation.family == MarketFamily.MONEYLINE:
        if observation.team not in (Side.HOME, Side.AWAY):
            return MarketSettlement(
                **base, **common, status=MarketSettlementStatus.UNSETTLEABLE_MISSING_FIELDS,
                detail="moneyline observation missing resolved team side",
            )
        contract_settlement = Side.YES if observation.team == actual_winner else Side.NO
        return MarketSettlement(
            **base, **common, status=MarketSettlementStatus.SETTLED, derived_contract_settlement=contract_settlement,
            detail=f"winner settled: actual={actual_winner.value}, contract team={observation.team.value}",
        )

    if observation.family == MarketFamily.SPREAD:
        if observation.team not in (Side.HOME, Side.AWAY) or observation.threshold is None:
            return MarketSettlement(
                **base, **common, status=MarketSettlementStatus.UNSETTLEABLE_MISSING_FIELDS,
                detail="spread observation missing team/threshold",
            )
        team_margin = home_margin if observation.team == Side.HOME else -home_margin
        covered = team_margin > observation.threshold
        contract_settlement = Side.YES if covered else Side.NO
        return MarketSettlement(
            **base, **common, status=MarketSettlementStatus.SETTLED, derived_contract_settlement=contract_settlement,
            detail=f"spread settled: team_margin={team_margin:+.1f} vs threshold={observation.threshold:+.1f}",
        )

    if observation.family == MarketFamily.TOTAL:
        if observation.threshold is None or observation.side != Side.OVER:
            return MarketSettlement(
                **base, **common, status=MarketSettlementStatus.UNSETTLEABLE_MISSING_FIELDS,
                detail="total observation missing threshold or unexpected side",
            )
        over_hit = total_points > observation.threshold
        contract_settlement = Side.YES if over_hit else Side.NO
        return MarketSettlement(
            **base, **common, status=MarketSettlementStatus.SETTLED, derived_contract_settlement=contract_settlement,
            detail=f"total settled: total_points={total_points:.1f} vs threshold={observation.threshold:.1f}",
        )

    return MarketSettlement(
        **base, **common, status=MarketSettlementStatus.UNSETTLEABLE_UNKNOWN_OPERATOR,
        detail=f"no settlement rule implemented for family {observation.family!r}",
    )


def flag_mismatch(settlement: MarketSettlement, official: Side | None) -> MarketSettlement:
    """Attaches an official Kalshi settlement outcome once/if observed
    (mission section 13: preserve BOTH the derived outcome and the
    official one, flagging disagreement rather than silently preferring
    either)."""
    mismatch = (
        official is not None
        and settlement.derived_contract_settlement is not None
        and official != settlement.derived_contract_settlement
    )
    return settlement.model_copy(
        update={"official_kalshi_settlement": official, "settlement_mismatch_flagged": mismatch}
    )
