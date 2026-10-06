"""Correlation and exposure GROUPING -- the structure a future portfolio
rule will consume, and nothing more.

*** WHAT THIS PRODUCES ***

Groups. Sets of contracts that are not independent football theses.
Twenty contracts from one CFB game are not twenty views; they are mostly
one view expressed twenty ways, and any sizing rule that cannot see that
will treat one edge as twenty and concentrate without knowing it.

*** WHAT THIS DELIBERATELY REFUSES TO PRODUCE ***

No correlation coefficients. No covariance matrix. No "maximum two
positions per game". Those are empirical or policy decisions and this
repository has zero settled prospective observations to derive them from.
A coefficient invented here would read as authoritative and be fiction;
a limit invented here would be a magic number wearing a lab coat. Both
wait for a mission that has real settled data.

*** THE ONE HONEST 'UNDETERMINED' ***

A game's MARGIN and its TOTAL are genuinely dependent -- but the SIGN and
MAGNITUDE of that dependence vary by matchup style and are not derivable
from settlement semantics the way exact equivalence is. That pair is
therefore reported as UNDETERMINED_PENDING_EMPIRICAL_MEASUREMENT rather
than guessed. Similarly, the same team appearing in two DIFFERENT games
is not same-game correlation: those are separate football outcomes, and
conflating them would be a different error in the opposite direction.

*** SPORT SCOPE ***

This is college football. `game_id`, MARGIN and TOTAL here mean CFB final
scores under `research/settlement.py`. Nothing in this module is
sport-agnostic and nothing is shared with any other sport's repository.

*** BUILT ON THE EXISTING TAXONOMY, NOT BESIDE IT ***

Structural relationships come from `expression/taxonomy.classify_pair`
and directions from `expression/exposure.build_exposure`. This module
only REFINES that output for portfolio purposes (splitting same-margin
pairs into same-team ladders vs offsetting sides). Re-deriving
equivalence here would create two rules that could silently disagree.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from cfb_edge_finder.expression.exposure import ContractExposure, ExposureDirection
from cfb_edge_finder.expression.taxonomy import (
    ContractSemantics,
    CorrelationClass,
    MarketDimension,
    classify_pair,
    truth_condition_key,
)
from cfb_edge_finder.schemas.common import Side

EXPOSURE_LIMITS_ABSENT = "EXPOSURE_LIMITS_ABSENT_PENDING_EMPIRICAL_POLICY"
"""There are no position limits in this module, and that is the point.
Carried as an explicit status so a consumer cannot read silence as
permission."""

UNRESOLVED_DIMENSION_LABEL = "UNRESOLVED_SEMANTICS"


class ThesisRelationship(StrEnum):
    """How two contracts relate as FOOTBALL theses. Derived from
    settlement semantics only -- never from ticker strings, never from
    outcomes this corpus does not have."""

    EXACT_EQUIVALENT_EVENT = "EXACT_EQUIVALENT_EVENT"
    """Identical settlement condition. `home ML YES` and `away ML NO` are
    one thesis with two spellings, not two theses."""

    NESTED_LADDER_SAME_TEAM = "NESTED_LADDER_SAME_TEAM"
    """Same team, same dimension, different thresholds -- rungs of one
    margin view (`Team A ML`, `Team A -3.5`, `Team A -7.5`)."""

    NESTED_LADDER_SAME_TOTAL = "NESTED_LADDER_SAME_TOTAL"
    """Rungs of one total view (`o51.5`, `o55.5`)."""

    OFFSETTING_SAME_DIMENSION = "OFFSETTING_SAME_DIMENSION"
    """Same dimension, opposing teams -- the exposures work against each
    other. Still one dimension, still not two independent theses."""

    SAME_GAME_DIFFERENT_DIMENSION = "SAME_GAME_DIFFERENT_DIMENSION"
    """Margin vs total in one game. Real dependence, unknown magnitude."""

    INDEPENDENT_DIFFERENT_GAMES = "INDEPENDENT_DIFFERENT_GAMES"
    """Different games -- including the SAME TEAM in two different games,
    which is not same-game correlation."""

    UNRESOLVED_SEMANTICS = "UNRESOLVED_SEMANTICS"
    """Semantics too incomplete to classify. Never optimistically treated
    as independent, and never treated as equivalent."""


class DependenceMagnitude(StrEnum):
    """How much is actually KNOWN about the strength of a relationship.

    Kept separate from `ThesisRelationship` on purpose: the structure is
    derivable, the magnitude mostly is not, and collapsing the two would
    let a derivable fact lend false credibility to a guess."""

    IDENTICAL = "IDENTICAL"
    """Perfect dependence, provable from settlement semantics."""

    MONOTONE_SAME_DIRECTION = "MONOTONE_SAME_DIRECTION"
    """Same latent number, same lean -- strictly positive dependence.
    Its size is not asserted."""

    MONOTONE_OPPOSING_DIRECTION = "MONOTONE_OPPOSING_DIRECTION"
    """Same latent number, opposing lean. Size not asserted."""

    UNDETERMINED_PENDING_EMPIRICAL_MEASUREMENT = "UNDETERMINED_PENDING_EMPIRICAL_MEASUREMENT"
    """Related, but neither sign nor size follows from semantics."""

    STRUCTURALLY_UNRELATED = "STRUCTURALLY_UNRELATED"
    """Different games. No shared settlement quantity."""

    UNKNOWN_INCOMPLETE_SEMANTICS = "UNKNOWN_INCOMPLETE_SEMANTICS"


_MAGNITUDE_BY_RELATIONSHIP = {
    ThesisRelationship.EXACT_EQUIVALENT_EVENT: DependenceMagnitude.IDENTICAL,
    ThesisRelationship.NESTED_LADDER_SAME_TEAM: DependenceMagnitude.MONOTONE_SAME_DIRECTION,
    ThesisRelationship.NESTED_LADDER_SAME_TOTAL: DependenceMagnitude.MONOTONE_SAME_DIRECTION,
    ThesisRelationship.OFFSETTING_SAME_DIMENSION: DependenceMagnitude.MONOTONE_OPPOSING_DIRECTION,
    ThesisRelationship.SAME_GAME_DIFFERENT_DIMENSION: (
        DependenceMagnitude.UNDETERMINED_PENDING_EMPIRICAL_MEASUREMENT
    ),
    ThesisRelationship.INDEPENDENT_DIFFERENT_GAMES: DependenceMagnitude.STRUCTURALLY_UNRELATED,
    ThesisRelationship.UNRESOLVED_SEMANTICS: DependenceMagnitude.UNKNOWN_INCOMPLETE_SEMANTICS,
}


def dependence_magnitude(relationship: ThesisRelationship) -> DependenceMagnitude:
    """What is known about the strength of a relationship. Never a number."""
    return _MAGNITUDE_BY_RELATIONSHIP[relationship]


def classify_relationship(a: ContractSemantics, b: ContractSemantics) -> ThesisRelationship:
    """Portfolio-level relationship between two contracts.

    Delegates the hard part (exact equivalence) to `classify_pair`, then
    refines the same-margin bucket into same-team ladders vs offsetting
    sides -- a distinction a portfolio rule needs and a pure taxonomy
    does not."""
    structural = classify_pair(a, b)

    if structural is CorrelationClass.UNRELATED_GAME:
        return ThesisRelationship.INDEPENDENT_DIFFERENT_GAMES
    if structural is CorrelationClass.EQUIVALENCE_UNRESOLVED:
        return ThesisRelationship.UNRESOLVED_SEMANTICS
    if structural is CorrelationClass.EXACT_EQUIVALENT:
        return ThesisRelationship.EXACT_EQUIVALENT_EVENT
    if structural is CorrelationClass.SAME_GAME_DIFFERENT_DIMENSION:
        return ThesisRelationship.SAME_GAME_DIFFERENT_DIMENSION
    if structural is CorrelationClass.SAME_TOTAL_DIMENSION_NESTED:
        return ThesisRelationship.NESTED_LADDER_SAME_TOTAL
    if structural is CorrelationClass.SAME_MARGIN_DIMENSION_NESTED:
        # Both read the final margin. Same team is a ladder; different
        # teams lean against each other.
        if a.team is not None and b.team is not None:
            return (
                ThesisRelationship.NESTED_LADDER_SAME_TEAM
                if a.team == b.team
                else ThesisRelationship.OFFSETTING_SAME_DIMENSION
            )
        return ThesisRelationship.UNRESOLVED_SEMANTICS

    return ThesisRelationship.UNRESOLVED_SEMANTICS


def thesis_group_key(semantics: ContractSemantics) -> str:
    """The dimension-level key: one latent CFB quantity in one game.

    Team is deliberately NOT part of the key. `Team A -3.5` and
    `Team B +3.5` read the same final margin; splitting them by team
    would present one number as two theses, which is the exact error this
    module exists to prevent.

    Contracts whose semantics are unresolved get a per-game UNRESOLVED
    key. They are held together rather than split into singletons,
    because splitting them would ASSERT independence that has not been
    established -- the more dangerous direction of the two."""
    if not semantics.semantics_resolved or semantics.dimension is MarketDimension.UNKNOWN:
        return f"{semantics.game_id}|{UNRESOLVED_DIMENSION_LABEL}"
    return f"{semantics.game_id}|{semantics.dimension.value}"


@dataclass(frozen=True)
class ExposureGroup:
    """Contracts sharing one thesis at some level of the hierarchy."""

    group_key: str
    game_id: str | None
    dimension: str
    market_tickers: tuple[str, ...]

    @property
    def contract_count(self) -> int:
        return len(self.market_tickers)

    @property
    def is_unresolved(self) -> bool:
        return self.dimension == UNRESOLVED_DIMENSION_LABEL


@dataclass
class PortfolioView:
    """Grouped exposure at the three levels of the taxonomy hierarchy.

    Carries no limits, no coefficients, and no ranking. `limits_status`
    says so out loud."""

    game_groups: list[ExposureGroup] = field(default_factory=list)
    exposure_groups: list[ExposureGroup] = field(default_factory=list)
    """Dimension level -- one latent quantity in one game. This is the
    level a sizing rule must count."""
    equivalence_groups: list[ExposureGroup] = field(default_factory=list)
    """Truth-condition level -- contracts that settle together always.
    Only groups with more than one member are listed."""
    limits_status: str = EXPOSURE_LIMITS_ABSENT

    @property
    def distinct_theses(self) -> int:
        """Dimension groups, NOT contracts. Counting contracts is exactly
        how one game's ladder becomes twenty imaginary independent edges."""
        return len(self.exposure_groups)

    @property
    def contract_count(self) -> int:
        return sum(group.contract_count for group in self.exposure_groups)

    @property
    def unresolved_group_count(self) -> int:
        return sum(1 for group in self.exposure_groups if group.is_unresolved)

    @property
    def contains_unresolved_semantics(self) -> bool:
        return self.unresolved_group_count > 0

    def group_for(self, market_ticker: str) -> ExposureGroup | None:
        for group in self.exposure_groups:
            if market_ticker in group.market_tickers:
                return group
        return None

    def equivalence_group_for(self, market_ticker: str) -> ExposureGroup | None:
        for group in self.equivalence_groups:
            if market_ticker in group.market_tickers:
                return group
        return None


def _build(pairs: dict[str, set[str]], *, dimension_from_key: bool) -> list[ExposureGroup]:
    groups: list[ExposureGroup] = []
    for key in sorted(pairs):
        game_id: str | None = None
        dimension = "EQUIVALENT_EVENT"
        if dimension_from_key:
            parts = key.split("|")
            game_id = parts[0]
            dimension = parts[1] if len(parts) > 1 else UNRESOLVED_DIMENSION_LABEL
        groups.append(
            ExposureGroup(
                group_key=key,
                game_id=game_id,
                dimension=dimension,
                market_tickers=tuple(sorted(pairs[key])),
            )
        )
    return groups


def build_portfolio_view(semantics: list[ContractSemantics]) -> PortfolioView:
    """Group contracts into CFB theses.

    Deterministic: keys are sorted and members are sorted sets, so the
    same input always produces byte-identical output regardless of input
    order."""
    by_game: dict[str, set[str]] = defaultdict(set)
    by_dimension: dict[str, set[str]] = defaultdict(set)
    by_truth: dict[str, set[str]] = defaultdict(set)

    for item in semantics:
        by_game[f"{item.game_id}|GAME"].add(item.market_ticker)
        by_dimension[thesis_group_key(item)].add(item.market_ticker)
        # Keyed by the truth condition ALONE. `home ML YES` and
        # `away ML NO` name the same event, so they must land in the same
        # bucket -- prefixing the key with the side would put them in
        # different ones and report zero equivalences on a corpus full of
        # them. A single contract contributes under both of its sides,
        # because either side can express its respective event.
        for side in (Side.YES, Side.NO):
            key = truth_condition_key(item, side)
            if key is not None:
                by_truth[key].add(item.market_ticker)

    equivalences = {key: tickers for key, tickers in by_truth.items() if len(tickers) > 1}

    return PortfolioView(
        game_groups=_build(by_game, dimension_from_key=True),
        exposure_groups=_build(by_dimension, dimension_from_key=True),
        equivalence_groups=_build(equivalences, dimension_from_key=False),
    )


def direction_conflicts(exposures: list[ContractExposure]) -> list[tuple[str, str]]:
    """Ticker pairs that lean OPPOSITE ways on the same dimension of the
    same game.

    Reported, not forbidden: holding both sides can be deliberate. A
    future rule decides what to do; this only makes it visible. Pairs are
    sorted for determinism."""
    conflicts: set[tuple[str, str]] = set()
    for i, left in enumerate(exposures):
        for right in exposures[i + 1 :]:
            if left.game_id != right.game_id or left.dimension is not right.dimension:
                continue
            if left.dimension is MarketDimension.UNKNOWN:
                continue
            if left.direction is ExposureDirection.UNKNOWN or right.direction is ExposureDirection.UNKNOWN:
                continue
            opposed = False
            if left.team_exposure is not None and right.team_exposure is not None:
                opposed = left.team_exposure is not right.team_exposure
            else:
                opposed = left.direction is not right.direction
            if opposed:
                conflicts.add(tuple(sorted((left.market_ticker, right.market_ticker))))
    return sorted(conflicts)
