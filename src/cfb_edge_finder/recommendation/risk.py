"""Correlated-exposure metadata for a FUTURE risk layer. No sizing.

*** WHAT THIS COMPUTES AND WHAT IT REFUSES TO ***
It computes IDENTIFIERS: which game, which dimension, which team
direction, which exact event. Those let a future layer notice that four
"positions" are one thesis. It computes no dollars, no fractions of
anything, and no allocation -- there is no bankroll concept in this
package at all, and the limits below are inert.

*** WHY THE LIMITS ARE OFF ***
A limit is a judgement about how much correlated exposure is acceptable,
and that judgement needs evidence about how correlated outcomes actually
are. With zero settled observations there is nothing to calibrate against,
so the limits exist as structure with `enabled=False` and every evaluation
returns RISK_LIMITS_DISABLED_PENDING_VALIDATION.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from cfb_edge_finder.recommendation.candidate import ResearchCandidate
from cfb_edge_finder.schemas.common import Side

RISK_LIMITS_DISABLED = "RISK_LIMITS_DISABLED_PENDING_VALIDATION"


@dataclass(frozen=True)
class ExposureKeys:
    """The identifiers through which two candidates can move together,
    coarsest first."""

    game_exposure_id: str
    dimension_exposure_id: str | None
    team_direction_exposure_id: str | None
    """Which team this candidate benefits from, within this game. A NO on
    'team T does well' is exposure to the OPPOSING team, so two
    candidates can share this key while naming different tickers."""
    equivalence_exposure_id: str | None
    """The exact terminal event. Two candidates sharing this are the SAME
    thesis expressed twice, not two theses."""
    model_thesis_id: str
    """The coarsest unit a future layer would count: one game's one
    dimension under one projection snapshot."""


def build_exposure_keys(candidate: ResearchCandidate) -> ExposureKeys:
    team_direction = None
    if candidate.team in (Side.HOME, Side.AWAY):
        benefiting = candidate.team
        if candidate.executable_side is Side.NO:
            benefiting = Side.AWAY if candidate.team is Side.HOME else Side.HOME
        team_direction = f"{candidate.game_id}|TEAM|{benefiting.value}"

    snapshot = candidate.projection_snapshot_id or "no-projection"
    return ExposureKeys(
        game_exposure_id=candidate.game_group_id,
        dimension_exposure_id=candidate.dimension_group_id,
        team_direction_exposure_id=team_direction,
        equivalence_exposure_id=candidate.equivalence_group_id,
        model_thesis_id=f"{candidate.dimension_group_id or candidate.game_group_id}|{snapshot}",
    )


@dataclass(frozen=True)
class ConcentrationLimits:
    """Shape of a future concentration policy. Inert.

    Every field defaults to None meaning 'no limit expressed'. That is not
    'unlimited' -- `evaluate_concentration` refuses to enforce anything
    while `enabled` is False, so an unset limit can never be read as
    permission."""

    enabled: bool = False
    max_expressions_per_game: int | None = None
    max_expressions_per_dimension: int | None = None
    max_exact_equivalents: int | None = None
    max_same_team_directional: int | None = None


@dataclass
class ExposureTally:
    """Counts of how many candidates share each exposure key."""

    per_game: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    per_dimension: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    per_team_direction: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    per_equivalence: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add(self, keys: ExposureKeys) -> None:
        self.per_game[keys.game_exposure_id] += 1
        if keys.dimension_exposure_id:
            self.per_dimension[keys.dimension_exposure_id] += 1
        if keys.team_direction_exposure_id:
            self.per_team_direction[keys.team_direction_exposure_id] += 1
        if keys.equivalence_exposure_id:
            self.per_equivalence[keys.equivalence_exposure_id] += 1

    @property
    def max_per_game(self) -> int:
        return max(self.per_game.values(), default=0)

    @property
    def max_per_dimension(self) -> int:
        return max(self.per_dimension.values(), default=0)

    @property
    def max_per_equivalence(self) -> int:
        return max(self.per_equivalence.values(), default=0)


@dataclass(frozen=True)
class ConcentrationAssessment:
    enforced: bool
    status: str
    tally: ExposureTally
    detail: str


def tally_exposure(candidates: list[ResearchCandidate]) -> ExposureTally:
    tally = ExposureTally()
    for candidate in candidates:
        tally.add(build_exposure_keys(candidate))
    return tally


def evaluate_concentration(
    candidates: list[ResearchCandidate], limits: ConcentrationLimits | None = None
) -> ConcentrationAssessment:
    """Tally exposure and report. Enforces nothing.

    Returns the counts a future layer would gate on, so the structure is
    exercised and testable, while `enforced` stays False."""
    limits = limits or ConcentrationLimits()
    tally = tally_exposure(candidates)
    if not limits.enabled:
        return ConcentrationAssessment(
            enforced=False,
            status=RISK_LIMITS_DISABLED,
            tally=tally,
            detail=(
                "exposure counted, no limit applied: concentration policy needs evidence about how "
                "correlated these outcomes actually are, and the corpus has no settled observations"
            ),
        )
    # Unreachable today; ConcentrationLimits.enabled has no code path that
    # sets it True. Retained so the contract is visible.
    return ConcentrationAssessment(
        enforced=False,
        status=RISK_LIMITS_DISABLED,
        tally=tally,
        detail="limits were marked enabled but enforcement remains unimplemented in this milestone",
    )
