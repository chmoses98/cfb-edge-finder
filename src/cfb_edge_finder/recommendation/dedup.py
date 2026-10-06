"""Exact-equivalent de-duplication and nested-ladder awareness.

*** WHAT `canonical_expression_candidate` IS ***
Among candidates that settle on the IDENTICAL event and pay the identical
$1, it names the one with the lowest all-in cost. That is arithmetic over
prices, in the same sense that "which of these identical items is cheaper"
is arithmetic. It is emphatically NOT a claim that the event is worth
expressing, and the name avoids "best" for that reason.

*** NESTED IS NOT EQUIVALENT ***
Team A ML, Team A -3.5 and Team A -7.5 are three DIFFERENT terminal
events that happen to read one number. They must never be collapsed into
one equivalence group; doing so would let a future card carry what looks
like one thesis while actually holding three different payout conditions.
The distinction comes straight from the expression taxonomy and is
preserved here rather than re-derived.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from cfb_edge_finder.recommendation.candidate import ResearchCandidate


@dataclass
class EquivalenceCluster:
    """All candidates settling on one terminal event."""

    equivalence_group_id: str
    candidates: list[ResearchCandidate] = field(default_factory=list)

    @property
    def priceable(self) -> list[ResearchCandidate]:
        return [c for c in self.candidates if c.priceable]

    @property
    def canonical_expression_candidate(self) -> ResearchCandidate | None:
        """The cheapest all-in expression of this event, or None when
        none is priceable. A fact about cost, not a suggestion."""
        priceable = self.priceable
        if not priceable:
            return None
        return min(priceable, key=lambda c: c.fee_adjusted_break_even_probability)

    @property
    def dominated_expressions(self) -> list[ResearchCandidate]:
        """Priceable expressions costing strictly more than the cheapest.

        An unpriceable expression is never called dominated: missing
        information is not evidence of being expensive."""
        canonical = self.canonical_expression_candidate
        if canonical is None:
            return []
        floor = canonical.fee_adjusted_break_even_probability
        return [
            c
            for c in self.priceable
            if c is not canonical and c.fee_adjusted_break_even_probability > floor
        ]


@dataclass
class NestedLadderGroup:
    """Candidates sharing a dimension but expressing DIFFERENT events.

    Exists so a future card builder cannot mistake three rungs of one
    ladder for three independent theses."""

    dimension_group_id: str
    candidates: list[ResearchCandidate] = field(default_factory=list)

    @property
    def distinct_events(self) -> set[str]:
        return {c.equivalence_group_id for c in self.candidates if c.equivalence_group_id}

    @property
    def is_nested_not_equivalent(self) -> bool:
        """True when this group holds more than one terminal event -- i.e.
        it is a genuine ladder rather than one event expressed twice."""
        return len(self.distinct_events) > 1


@dataclass
class DeduplicationView:
    equivalence_clusters: dict[str, EquivalenceCluster] = field(default_factory=dict)
    nested_groups: dict[str, NestedLadderGroup] = field(default_factory=dict)
    unresolved_candidates: list[ResearchCandidate] = field(default_factory=list)

    @property
    def multi_expression_clusters(self) -> list[EquivalenceCluster]:
        return [c for c in self.equivalence_clusters.values() if len(c.candidates) > 1]

    @property
    def dominated_count(self) -> int:
        return sum(len(c.dominated_expressions) for c in self.equivalence_clusters.values())

    @property
    def nested_ladder_count(self) -> int:
        return sum(1 for g in self.nested_groups.values() if g.is_nested_not_equivalent)


def build_deduplication_view(candidates: list[ResearchCandidate]) -> DeduplicationView:
    """Group candidates by terminal event and by dimension in one pass.

    Candidates whose semantics never resolved carry no equivalence key and
    are kept aside rather than bucketed -- an unresolved contract must not
    be treated as sharing an event with anything."""
    view = DeduplicationView()
    equivalence: dict[str, EquivalenceCluster] = {}
    nested: dict[str, NestedLadderGroup] = defaultdict(lambda: NestedLadderGroup(""))

    for candidate in candidates:
        if not candidate.equivalence_group_id or not candidate.semantics_resolved:
            view.unresolved_candidates.append(candidate)
            continue
        cluster = equivalence.setdefault(
            candidate.equivalence_group_id, EquivalenceCluster(candidate.equivalence_group_id)
        )
        cluster.candidates.append(candidate)

        if candidate.dimension_group_id:
            group = nested[candidate.dimension_group_id]
            if not group.dimension_group_id:
                group.dimension_group_id = candidate.dimension_group_id
            group.candidates.append(candidate)

    view.equivalence_clusters = equivalence
    view.nested_groups = dict(nested)
    return view
