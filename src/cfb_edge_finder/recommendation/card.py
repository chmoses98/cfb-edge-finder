"""The card-builder skeleton and the explicit boundary to sizing.

*** THIS BUILDER ALWAYS RETURNS AN EMPTY CARD ***
Not by policy but by arithmetic: it emits a card entry only for candidates
whose `EligibilityResult.actionable` is True, and no candidate can be
actionable while the threshold provider yields NO_VALIDATED_THRESHOLD_SET.
The builder is real code exercising the real path -- it is simply that the
path currently terminates in zero.

*** THE 6/7 BOUNDARY ***
`PortfolioBoundary` exists to make it visible that qualification and
sizing are SEPARATE decisions. A qualified card says "these met the
evidence bar"; it says nothing about how much of anything to commit. This
repository implements neither side of that boundary's downstream: there is
no sizing module, no allocation, and no execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cfb_edge_finder.recommendation.candidate import ResearchCandidate
from cfb_edge_finder.recommendation.dedup import DeduplicationView
from cfb_edge_finder.recommendation.eligibility import (
    QUALIFICATION_DISABLED,
    EligibilityResult,
)
from cfb_edge_finder.recommendation.risk import ConcentrationAssessment

BET_UP_TO_UNAVAILABLE = "BET_UP_TO_UNAVAILABLE_NO_VALIDATED_THRESHOLD"
SHADOW_DISABLED = "SHADOW_DISABLED_NO_VALIDATED_THRESHOLDS"
PORTFOLIO_LAYER_ABSENT = "PORTFOLIO_LAYER_ABSENT"


@dataclass(frozen=True)
class MaximumAcceptablePrice:
    """Placeholder for a future price ceiling.

    Carries no number. A ceiling is derived from a validated probability
    estimate, and inventing one from an unvalidated model probability
    would produce a precise-looking figure with nothing behind it."""

    available: bool = False
    status: str = BET_UP_TO_UNAVAILABLE
    value: None = None


@dataclass(frozen=True)
class CardDiagnostics:
    """Counts only. Deliberately no entries, prices, or instructions."""

    candidates_considered: int = 0
    candidates_blocked_qualification_disabled: int = 0
    candidates_blocked_quality: int = 0
    equivalence_clusters: int = 0
    multi_expression_clusters: int = 0
    dominated_expressions: int = 0
    nested_ladder_groups: int = 0
    unresolved_candidates: int = 0
    risk_status: str = ""
    max_expressions_per_game_observed: int = 0
    max_expressions_per_equivalence_observed: int = 0


@dataclass(frozen=True)
class ResearchCard:
    """The builder's output. `entries` is typed as an always-empty tuple:
    a future implementation would widen this type deliberately, rather
    than an entry appearing because a condition drifted."""

    actionable_count: int
    entries: tuple[()] = ()
    status: str = QUALIFICATION_DISABLED
    shadow_status: str = SHADOW_DISABLED
    maximum_acceptable_price: MaximumAcceptablePrice = field(default_factory=MaximumAcceptablePrice)
    diagnostics: CardDiagnostics = field(default_factory=CardDiagnostics)
    detail: str = ""


def build_research_card(
    candidates: list[ResearchCandidate],
    eligibility_results: list[EligibilityResult],
    dedup_view: DeduplicationView,
    concentration: ConcentrationAssessment,
) -> ResearchCard:
    """Assemble diagnostics. Emits zero entries.

    `actionable` is recomputed from the eligibility results rather than
    assumed to be zero, so if a future change ever made a candidate
    actionable this function would report it truthfully and the safety
    test would fail loudly -- which is the point."""
    actionable = [r for r in eligibility_results if r.actionable]
    blocked_disabled = [r for r in eligibility_results if r.status == QUALIFICATION_DISABLED]
    blocked_quality = [r for r in eligibility_results if r.quality_failures]

    diagnostics = CardDiagnostics(
        candidates_considered=len(candidates),
        candidates_blocked_qualification_disabled=len(blocked_disabled),
        candidates_blocked_quality=len(blocked_quality),
        equivalence_clusters=len(dedup_view.equivalence_clusters),
        multi_expression_clusters=len(dedup_view.multi_expression_clusters),
        dominated_expressions=dedup_view.dominated_count,
        nested_ladder_groups=dedup_view.nested_ladder_count,
        unresolved_candidates=len(dedup_view.unresolved_candidates),
        risk_status=concentration.status,
        max_expressions_per_game_observed=concentration.tally.max_per_game,
        max_expressions_per_equivalence_observed=concentration.tally.max_per_equivalence,
    )

    return ResearchCard(
        actionable_count=len(actionable),
        entries=(),
        status=QUALIFICATION_DISABLED,
        detail=(
            f"{len(candidates)} candidate(s) considered; {len(actionable)} actionable. Qualification is "
            f"disabled pending a versioned, approved empirical threshold artifact, so no entry, price "
            f"ceiling, or instruction is produced."
        ),
        diagnostics=diagnostics,
    )


@dataclass(frozen=True)
class PortfolioBoundary:
    """The explicit seam between a qualified card and any sizing layer.

    Nothing downstream of this exists in this repository. The type is
    defined so the separation is a visible architectural fact rather than
    an implicit one -- deciding WHAT meets the evidence bar and deciding
    HOW MUCH to commit are different decisions requiring different
    evidence, and collapsing them is how sizing quietly inherits
    qualification's authority."""

    downstream_status: str = PORTFOLIO_LAYER_ABSENT
    detail: str = (
        "qualification and sizing are separate decisions; no sizing, allocation, or execution layer "
        "exists in this repository"
    )
