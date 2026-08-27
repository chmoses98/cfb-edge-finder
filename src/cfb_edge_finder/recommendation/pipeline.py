"""Wiring stages 1-6 together over a real contract universe.

Stage 7 (sizing) and stage 8 (execution) have no representation here --
not a disabled call, not a stub, nothing. The pipeline ends at a card that
is always empty.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from cfb_edge_finder.expression.economics import build_expression_economics
from cfb_edge_finder.expression.grouping import ContractSnapshot
from cfb_edge_finder.recommendation.candidate import ResearchCandidate, build_candidates
from cfb_edge_finder.recommendation.card import ResearchCard, build_research_card
from cfb_edge_finder.recommendation.dedup import DeduplicationView, build_deduplication_view
from cfb_edge_finder.recommendation.eligibility import (
    EligibilityConfig,
    EligibilityResult,
    evaluate_eligibility,
    family_research_status,
)
from cfb_edge_finder.recommendation.evidence import EvidenceReadiness, assess_readiness
from cfb_edge_finder.recommendation.risk import (
    ConcentrationAssessment,
    ConcentrationLimits,
    evaluate_concentration,
)
from cfb_edge_finder.schemas.common import Side


@dataclass
class PipelineResult:
    candidates: list[ResearchCandidate] = field(default_factory=list)
    eligibility_results: list[EligibilityResult] = field(default_factory=list)
    dedup_view: DeduplicationView | None = None
    concentration: ConcentrationAssessment | None = None
    card: ResearchCard | None = None
    readiness_by_family: dict[str, EvidenceReadiness] = field(default_factory=dict)
    family_statuses: dict[str, str] = field(default_factory=dict)
    games: set[str] = field(default_factory=set)

    @property
    def actionable_count(self) -> int:
        return self.card.actionable_count if self.card else 0


def _series_ticker_of(market_ticker: str) -> str | None:
    head = market_ticker.split("-", 1)[0].strip()
    return head or None


def run_pipeline(
    snapshots: list[ContractSnapshot],
    *,
    config: EligibilityConfig | None = None,
    limits: ConcentrationLimits | None = None,
    settled_counts_by_family: dict[str, tuple[int, int, int]] | None = None,
    now: datetime | None = None,
    projection_snapshot_ids: dict[str, str] | None = None,
) -> PipelineResult:
    """Form candidates, evaluate every gate, group them, and build a card.

    `settled_counts_by_family` maps family -> (settled_n, game_clusters,
    clv_n). Absent or zero counts resolve to NO_SETTLED_DATA, which is the
    current live state for every family."""
    config = config or EligibilityConfig()
    result = PipelineResult()

    for snapshot in snapshots:
        semantics = snapshot.semantics
        result.games.add(semantics.game_id)
        series = _series_ticker_of(semantics.market_ticker)
        economics = {
            Side.YES: build_expression_economics(
                market_ticker=semantics.market_ticker,
                executable_side=Side.YES,
                executable_price=snapshot.executable_yes_price,
                model_probability_for_this_side=snapshot.model_probability,
                series_ticker=series,
                fee_status=snapshot.fee_status,
                fee_schedule_version=snapshot.fee_schedule_version,
            ),
            Side.NO: build_expression_economics(
                market_ticker=semantics.market_ticker,
                executable_side=Side.NO,
                executable_price=snapshot.executable_no_price,
                model_probability_for_this_side=snapshot.model_probability_no_side,
                series_ticker=series,
                fee_status=snapshot.fee_status,
                fee_schedule_version=snapshot.fee_schedule_version,
            ),
        }
        result.candidates.extend(
            build_candidates(
                snapshot,
                economics,
                projection_snapshot_id=(projection_snapshot_ids or {}).get(semantics.game_id),
            )
        )

    families = {c.market_family for c in result.candidates if c.market_family}
    for family in sorted(families):
        settled_n, clusters, clv_n = (settled_counts_by_family or {}).get(family, (0, 0, 0))
        result.readiness_by_family[family] = assess_readiness(
            family=family,
            timing_label=None,
            model_version=None,
            settled_n=settled_n,
            unique_game_clusters=clusters,
            clv_n=clv_n,
        )
        result.family_statuses[family] = family_research_status(family).value

    result.eligibility_results = [
        evaluate_eligibility(
            candidate,
            config,
            readiness=result.readiness_by_family.get(candidate.market_family or ""),
            now=now,
        )
        for candidate in result.candidates
    ]
    result.dedup_view = build_deduplication_view(result.candidates)
    result.concentration = evaluate_concentration(result.candidates, limits)
    result.card = build_research_card(
        result.candidates, result.eligibility_results, result.dedup_view, result.concentration
    )
    return result


def evidence_state_distribution(result: PipelineResult) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for readiness in result.readiness_by_family.values():
        counts[readiness.state.value] += 1
    return dict(counts)
