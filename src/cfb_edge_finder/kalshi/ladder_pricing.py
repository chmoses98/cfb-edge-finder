"""Milestone D: turns ONE raw Kalshi market dict into ONE
`KalshiResearchObservation` row, and -- because every caller in this
module is expected to reuse the SAME `CachedGameProjection` across every
market in one game's ladder -- is the concrete place mission section
16/17's "one projection prices many contracts" requirement actually
happens. This module contains no probability math and no game-mapping
logic of its own; it only sequences the pieces already built:
`contract_semantics` (parse), `game_mapping.classify_mapped_market`
(coverage), `price_extraction` (executable price), and `market_pricing`
(model probability), and packages the result into the research-ledger
row shape `schemas.kalshi_observation.KalshiResearchObservation` already
defines.

*** WHY PER-MARKET TEAM RESOLUTION IS SEPARATE FROM GAME-LEVEL MAPPING ***
`game_mapping.map_kalshi_event_to_game` resolves game IDENTITY from the
EVENT's own title (e.g. "Southern Utah at Montana"). A single spread
LADDER under that same event has one market PER TEAM PER THRESHOLD, each
with its OWN title naming just one team (e.g. "Southern Utah wins by
over 4.5 points") -- contract_semantics.py deliberately leaves that name
unresolved (`raw_team_name`) because it has no team registry access.
This module is where that per-market name gets resolved and checked
against the ALREADY-MAPPED game's own home/away team_ids -- a distinct
failure mode from game-level mapping (`AMBIGUOUS_TEAM_MAPPING` per
market, not per game), and if a resolved name doesn't match EITHER side
of the mapped game at all, that is a genuine, real anomaly
(`OTHER_EXPLICIT_REASON`) worth surfacing explicitly rather than
silently coercing to one side.
"""

from __future__ import annotations

from datetime import datetime

from cfb_edge_finder.kalshi.cfb_coverage_reason import KalshiCfbCoverageReason, to_coverage_outcome
from cfb_edge_finder.kalshi.contract_semantics import (
    ParsedContract,
    parse_spread_market,
    parse_total_market,
    parse_winner_market,
)
from cfb_edge_finder.kalshi.game_mapping import KalshiGameMappingResult, classify_mapped_market
from cfb_edge_finder.kalshi.game_projection_cache import CachedGameProjection
from cfb_edge_finder.kalshi.market_pricing import price_parsed_contract
from cfb_edge_finder.kalshi.price_extraction import ExtractedMarketPrice, extract_market_price
from cfb_edge_finder.schemas.common import MarketFamily, Side
from cfb_edge_finder.schemas.kalshi_observation import KalshiResearchObservation, SnapshotTiming
from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion
from cfb_edge_finder.teams.registry import AmbiguousTeamAliasError, UnknownTeamAliasError, resolve_team_alias


def _resolve_named_team_side(
    raw_team_name: str, mapping: KalshiGameMappingResult
) -> tuple[Side | None, KalshiCfbCoverageReason | None, str]:
    """Resolves ONE market's own named team against the ALREADY-MAPPED
    game's home/away team_ids. Returns (side, failure_reason, detail) --
    exactly one of (side, failure_reason) is None."""
    try:
        team_id = resolve_team_alias(raw_team_name)
    except AmbiguousTeamAliasError as exc:
        return None, KalshiCfbCoverageReason.AMBIGUOUS_TEAM_MAPPING, f"{raw_team_name!r} is ambiguous: {exc}"
    except UnknownTeamAliasError as exc:
        return None, KalshiCfbCoverageReason.AMBIGUOUS_TEAM_MAPPING, f"{raw_team_name!r} is unknown: {exc}"

    if team_id == mapping.home_team_id:
        return Side.HOME, None, f"{raw_team_name!r} resolved to home team {team_id!r}"
    if team_id == mapping.away_team_id:
        return Side.AWAY, None, f"{raw_team_name!r} resolved to away team {team_id!r}"
    return (
        None,
        KalshiCfbCoverageReason.OTHER_EXPLICIT_REASON,
        f"{raw_team_name!r} resolved to {team_id!r}, which is neither the mapped game's home "
        f"({mapping.home_team_id!r}) nor away ({mapping.away_team_id!r}) team -- inconsistent evidence",
    )


_PARSERS = {
    MarketFamily.SPREAD: lambda title, floor_strike: parse_spread_market(title, floor_strike),
    MarketFamily.TOTAL: lambda title, floor_strike: parse_total_market(title, floor_strike),
    MarketFamily.MONEYLINE: lambda title, floor_strike: parse_winner_market(title),
}


def price_one_market(
    raw_market: dict,
    *,
    family_hint: MarketFamily,
    event_ticker: str,
    mapping: KalshiGameMappingResult,
    home_classification: str | None,
    away_classification: str | None,
    cached_projection: CachedGameProjection | None,
    captured_at: datetime,
    snapshot_id: str,
    snapshot_timing: SnapshotTiming,
    model_version: ModelVersion | None,
    training_cutoff: str | None,
    provenance: DataProvenance,
    fee_status: str = "unverified",
) -> KalshiResearchObservation:
    """The single entry point for pricing ONE market. `family_hint` comes
    from the caller's own discovery step (which series_ticker the market
    was fetched under -- see docs/MILESTONE_D.md "Discovery method"),
    not re-derived here from ticker-string guessing. `cached_projection`
    should be the SAME object across every call for markets belonging to
    the same game -- see module docstring and `GameProjectionCache`.
    Never raises: every failure path still returns a valid, explicit
    `KalshiResearchObservation` row."""
    market_ticker = str(raw_market.get("ticker", ""))
    title = str(raw_market.get("title", "") or "")
    floor_strike = raw_market.get("floor_strike")
    floor_strike_f = float(floor_strike) if isinstance(floor_strike, (int, float)) else None

    parser = _PARSERS.get(family_hint)
    parsed: ParsedContract = (
        parser(title, floor_strike_f)
        if parser is not None
        else ParsedContract(
            reason=KalshiCfbCoverageReason.MAPPED_UNSUPPORTED_FAMILY,
            detail=f"no contract-semantics parser registered for {family_hint!r}",
        )
    )

    extracted: ExtractedMarketPrice = extract_market_price(raw_market)

    named_side: Side | None = None
    side_failure: KalshiCfbCoverageReason | None = None
    side_detail = ""
    if parsed.reason is None and mapping.reason is None and parsed.raw_team_name is not None:
        named_side, side_failure, side_detail = _resolve_named_team_side(parsed.raw_team_name, mapping)

    coverage_reason = classify_mapped_market(
        mapping,
        market_family=parsed.market_family,
        home_classification=home_classification,
        away_classification=away_classification,
    )
    if coverage_reason == KalshiCfbCoverageReason.MAPPED_SUPPORTED and side_failure is not None:
        coverage_reason = side_failure

    parse_status = "confirmed_live" if parsed.reason is None else "unresolved"
    if parsed.reason is None and parsed.semantics_confidence != "confirmed_live":
        parse_status = "unconfirmed"

    model_probability: float | None = None
    pricing_detail = "not priced"
    pricing_status = "not_priced"
    if coverage_reason == KalshiCfbCoverageReason.MAPPED_SUPPORTED:
        if cached_projection is None:
            pricing_detail = "no cached game projection supplied"
            pricing_status = "not_priced"
        else:
            distribution = cached_projection.projection.to_game_distribution()
            result = price_parsed_contract(parsed, distribution, named_team_side=named_side)
            model_probability = result.model_probability
            pricing_detail = result.detail
            pricing_status = "model_priced" if model_probability is not None else "not_priced"
    elif coverage_reason == KalshiCfbCoverageReason.MAPPED_UNSUPPORTED_POPULATION:
        pricing_status = "unsupported_population"
        pricing_detail = "mapped and semantically parsed, but not FBS-vs-FBS -- C.2 model never priced this"
    elif coverage_reason == KalshiCfbCoverageReason.FCS_VS_FCS:
        pricing_status = "unsupported_population"
        pricing_detail = "both teams deterministically identified as FCS -- FBS-only C.2 model never priced this"
    elif coverage_reason == KalshiCfbCoverageReason.MAPPED_UNSUPPORTED_FAMILY:
        pricing_status = "unsupported_family"
        pricing_detail = "mapped, but this market family is outside CORE_V1"

    research_probability_gap = None
    if model_probability is not None and extracted.executable_yes_price is not None:
        research_probability_gap = model_probability - extracted.executable_yes_price

    detail_parts = [parsed.detail]
    if side_detail:
        detail_parts.append(side_detail)
    detail_parts.append(pricing_detail)

    return KalshiResearchObservation(
        snapshot_id=snapshot_id,
        captured_at=captured_at,
        snapshot_timing=snapshot_timing,
        game_id=mapping.game_id,
        kalshi_event_ticker=event_ticker,
        kalshi_market_ticker=market_ticker,
        family=parsed.market_family,
        threshold=parsed.line,
        side=parsed.side,
        team=named_side,
        semantic_operator=parsed.operator,
        model_probability=model_probability,
        executable_yes_price=extracted.executable_yes_price,
        executable_no_price=extracted.executable_no_price,
        market_midpoint=extracted.midpoint,
        research_probability_gap=research_probability_gap,
        fee_status=fee_status,
        model_version=model_version if pricing_status == "model_priced" else None,
        training_cutoff=training_cutoff if pricing_status == "model_priced" else None,
        coverage_outcome=to_coverage_outcome(coverage_reason),
        coverage_reason=coverage_reason.value,
        parse_status=parse_status,
        pricing_status=pricing_status,
        provenance=provenance,
        uncertainty=(
            cached_projection.projection.to_uncertainty_profile()
            if cached_projection is not None and pricing_status == "model_priced"
            else None
        ),
    )
