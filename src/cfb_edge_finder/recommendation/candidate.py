"""One executable research expression, described neutrally.

*** THIS IS NOT A BET ***
A `ResearchCandidate` is a described opportunity-shaped object: one side
of one contract, with what it costs and what the model thinks. Calling it
a candidate rather than a bet is not squeamishness -- the name is the only
thing standing between "we measured this" and "we suggest this", and every
downstream stage reads that name.

Construction carries no eligibility opinion. A candidate exists for every
executable expression, including ones that fail every quality gate; the
gates run separately (see eligibility.py) so the reason a candidate is
unusable is recorded rather than expressed as absence.
"""

from __future__ import annotations

from dataclasses import dataclass

from cfb_edge_finder.expression.economics import ExpressionEconomics
from cfb_edge_finder.expression.grouping import ContractSnapshot
from cfb_edge_finder.expression.taxonomy import MarketDimension, truth_condition_key
from cfb_edge_finder.schemas.common import Side


@dataclass(frozen=True)
class ResearchCandidate:
    """One executable side of one contract at one captured instant."""

    # --- Identity and linkage ---
    game_id: str
    market_ticker: str
    market_family: str | None
    timing_label: str
    team: Side | None
    contract_side: Side | None
    """The contract's own side field (e.g. OVER for totals)."""
    threshold: float | None
    executable_side: Side
    """YES or NO -- which side this candidate would be entered on."""

    # --- Economics ---
    executable_price: float | None
    estimated_fee: float | None
    fee_adjusted_break_even_probability: float | None
    model_probability: float | None
    """The model's probability for THIS executable side (already
    complemented for a NO candidate)."""
    research_probability_surplus: float | None
    """model probability minus fee-adjusted break-even. Descriptive.
    Deliberately not called an edge -- see expression/economics.py."""

    # --- Grouping / provenance ---
    projection_snapshot_id: str | None
    equivalence_group_id: str | None
    dimension_group_id: str | None
    game_group_id: str
    model_version: str | None
    captured_at: str
    market_status: str | None
    fee_status: str | None
    fee_schedule_version: str | None
    pricing_status: str | None
    semantics_resolved: bool
    schema_version: str | None = None
    """Corpus schema version of the source row. Lets eligibility say WHY a
    field is missing -- schema too old vs. a current-schema defect --
    without softening the gate either way."""

    @property
    def priceable(self) -> bool:
        return self.fee_adjusted_break_even_probability is not None


def build_candidates(
    snapshot: ContractSnapshot,
    economics_by_side: dict[Side, ExpressionEconomics],
    *,
    projection_snapshot_id: str | None = None,
) -> list[ResearchCandidate]:
    """One candidate per executable side of this contract.

    Both sides are emitted even when only one is priceable: a NO side with
    no quote is a real, recordable fact about the market, and dropping it
    would make the universe look tidier than it is."""
    semantics = snapshot.semantics
    dimension = semantics.dimension
    dimension_group_id = (
        f"{semantics.game_id}|{dimension.value}" if dimension is not MarketDimension.UNKNOWN else None
    )

    candidates: list[ResearchCandidate] = []
    for side in (Side.YES, Side.NO):
        economics = economics_by_side.get(side)
        model_probability = (
            snapshot.model_probability if side is Side.YES else snapshot.model_probability_no_side
        )
        candidates.append(
            ResearchCandidate(
                game_id=semantics.game_id,
                market_ticker=semantics.market_ticker,
                market_family=semantics.family.value if semantics.family else None,
                timing_label=snapshot.timing_label,
                team=semantics.team,
                contract_side=semantics.side,
                threshold=semantics.threshold,
                executable_side=side,
                executable_price=economics.executable_price if economics else None,
                estimated_fee=economics.estimated_fee if economics else None,
                fee_adjusted_break_even_probability=(
                    economics.fee_adjusted_break_even_probability if economics else None
                ),
                model_probability=model_probability,
                research_probability_surplus=(
                    economics.research_probability_surplus if economics else None
                ),
                projection_snapshot_id=projection_snapshot_id,
                equivalence_group_id=truth_condition_key(semantics, side),
                dimension_group_id=dimension_group_id,
                game_group_id=semantics.game_id,
                model_version=snapshot.model_version,
                captured_at=snapshot.captured_at,
                market_status=snapshot.market_status,
                schema_version=snapshot.schema_version,
                fee_status=snapshot.fee_status,
                fee_schedule_version=snapshot.fee_schedule_version,
                pricing_status=snapshot.pricing_status,
                semantics_resolved=semantics.semantics_resolved,
            )
        )
    return candidates
