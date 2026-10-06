"""Walk-forward ablation discipline for preseason-prior candidates.

*** THE SPLIT IS DECLARED BEFORE RESULTS EXIST ***

`WALK_FORWARD_SPLIT` below is fixed in this file, committed before any
candidate has been evaluated. Choosing a split after seeing which one
flatters a candidate is the most effective way to manufacture a finding,
and it leaves no trace in the final numbers.

*** ONE CANDIDATE AT A TIME ***

`run_ablation` accepts ONE candidate family. There is deliberately no
function that sweeps feature subsets or hyperparameter grids: with enough
combinations something always looks excellent, and its apparent edge is
selection, not signal. Combinations are permitted only after individual
families have earned their place, and each combination is then its own
declared candidate.

*** CONFIRMATION IS SPENT ONCE ***

`ConfirmationLedger` records every candidate that has touched the
confirmation seasons and refuses a second look at the same one. A
candidate that fails confirmation is REJECTED -- it cannot be adjusted
and re-run against the same data, because after the second attempt the
confirmation set is development data wearing a different name.

*** EFFECT TYPE IS PART OF THE VERDICT ***

A feature may improve the mean, the uncertainty, both, or neither. A new
quarterback might not justify subtracting points while genuinely widening
the error distribution. `EffectType` forces that question to be answered
rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from cfb_edge_finder.research.preseason.control import (
    CONTROL_BASELINE_SHA256,
    CONTROL_MODEL_VERSION,
    control_has_drifted,
)
from cfb_edge_finder.research.preseason.evaluation import PairedComparison

ABLATION_VERSION = "preseason_ablation_v1"


@dataclass(frozen=True)
class WalkForwardSplit:
    """Chronological split, declared in advance."""

    development_seasons: tuple[int, ...]
    selection_season: int
    confirmation_season: int
    excluded_seasons: tuple[int, ...]
    rationale: str

    def role_of(self, season: int) -> str:
        if season in self.excluded_seasons:
            return "EXCLUDED"
        if season in self.development_seasons:
            return "DEVELOPMENT"
        if season == self.selection_season:
            return "SELECTION"
        if season == self.confirmation_season:
            return "CONFIRMATION"
        return "OUT_OF_SCOPE"


WALK_FORWARD_SPLIT = WalkForwardSplit(
    development_seasons=(2019, 2021, 2022, 2023),
    selection_season=2024,
    confirmation_season=2025,
    excluded_seasons=(2020,),
    rationale=(
        "Chronological, with the most recent completed season held back untouched. 2020 is "
        "EXCLUDED rather than pooled: conference-only schedules, opt-outs and cancellations make "
        "its preseason-to-outcome relationship a different process, and blending it in would "
        "corrupt exactly the preseason-prior signal under study. The split is declared here "
        "before any candidate result exists; it was NOT chosen by trying alternatives. "
        "The existing 2022-2024 development / 2025 confirmation framework is a subset of this, "
        "so results remain comparable if earlier seasons prove unavailable."
    ),
)


class EffectType(StrEnum):
    POINT_ESTIMATE = "POINT_ESTIMATE"
    UNCERTAINTY = "UNCERTAINTY"
    BOTH = "BOTH"
    NEITHER = "NEITHER"
    UNDETERMINED_NO_DATA = "UNDETERMINED_NO_DATA"
    """No evaluation was possible. Distinct from NEITHER, which is a
    measured negative result."""


class CandidateVerdict(StrEnum):
    ACCEPT_PENDING_CONFIRMATION = "ACCEPT_PENDING_CONFIRMATION"
    ACCEPT_CONFIRMED = "ACCEPT_CONFIRMED"
    REJECT_NO_DEVELOPMENT_IMPROVEMENT = "REJECT_NO_DEVELOPMENT_IMPROVEMENT"
    REJECT_FAILED_CONFIRMATION = "REJECT_FAILED_CONFIRMATION"
    REJECT_DEGRADES_LATER_WEEKS = "REJECT_DEGRADES_LATER_WEEKS"
    REJECT_LEAKAGE_UNSAFE = "REJECT_LEAKAGE_UNSAFE"
    BLOCKED_NO_HISTORICAL_DATA = "BLOCKED_NO_HISTORICAL_DATA"
    """The evaluation could not be run at all. NOT a negative result:
    reporting it as REJECT would claim a measurement that never
    happened."""


SEGMENTS = ("week_1", "weeks_2_3", "weeks_4_plus", "neutral_site", "fbs_vs_fbs")
"""Reported separately for every candidate. Week 1 is the primary focus,
but a candidate that helps Week 1 and harms later weeks has not helped."""


@dataclass
class CandidateResult:
    candidate_name: str
    verdict: CandidateVerdict
    effect_type: EffectType
    development: dict[str, PairedComparison] = field(default_factory=dict)
    selection: dict[str, PairedComparison] = field(default_factory=dict)
    confirmation: dict[str, PairedComparison] = field(default_factory=dict)
    segment_notes: dict[str, str] = field(default_factory=dict)
    detail: str = ""
    control_model_version: str = CONTROL_MODEL_VERSION
    control_sha256: str = CONTROL_BASELINE_SHA256
    ablation_version: str = ABLATION_VERSION

    @property
    def promotes_to_shadow(self) -> bool:
        """Only a confirmed acceptance may be built as a shadow model.
        Everything else -- including a promising development result -- is
        not enough."""
        return self.verdict is CandidateVerdict.ACCEPT_CONFIRMED

    def to_dict(self) -> dict:
        def block(d: dict[str, PairedComparison]) -> dict:
            return {
                k: {
                    "n_games": v.n_games,
                    "control": v.control,
                    "candidate": v.candidate,
                    "mean_paired_difference": v.mean_paired_difference,
                    "ci_low": v.ci_low,
                    "ci_high": v.ci_high,
                    "improves": v.improves,
                    "degrades": v.degrades,
                }
                for k, v in sorted(d.items())
            }

        return {
            "candidate": self.candidate_name,
            "verdict": self.verdict.value,
            "effect_type": self.effect_type.value,
            "control_model_version": self.control_model_version,
            "control_sha256": self.control_sha256,
            "ablation_version": self.ablation_version,
            "development": block(self.development),
            "selection": block(self.selection),
            "confirmation": block(self.confirmation),
            "segment_notes": dict(sorted(self.segment_notes.items())),
            "promotes_to_shadow": self.promotes_to_shadow,
            "detail": self.detail,
        }


class ConfirmationSpentError(RuntimeError):
    """A candidate tried to look at confirmation data twice."""


@dataclass
class ConfirmationLedger:
    """Records which candidates have spent their one confirmation look."""

    spent: set[str] = field(default_factory=set)

    def spend(self, candidate_name: str) -> None:
        if candidate_name in self.spent:
            raise ConfirmationSpentError(
                f"{candidate_name!r} has already been evaluated on the confirmation season. "
                f"A candidate that failed confirmation is rejected; retuning it and re-running "
                f"against the same data turns confirmation into development."
            )
        self.spent.add(candidate_name)


class ControlDriftError(RuntimeError):
    """Production changed underneath the experiment."""


def assert_control_unchanged() -> None:
    """Refuse to run an experiment against a drifted control.

    Called at the top of every ablation: a comparison against a control
    that quietly moved is not a comparison at all."""
    if control_has_drifted(CONTROL_BASELINE_SHA256):
        raise ControlDriftError(
            "the production control model no longer matches the frozen baseline "
            f"{CONTROL_BASELINE_SHA256[:16]}...; every comparison in this research is "
            "invalid until the control is re-frozen and prior results re-labelled"
        )


def blocked_candidate(name: str, reason: str) -> CandidateResult:
    """A candidate that could not be evaluated.

    Reported as BLOCKED with UNDETERMINED_NO_DATA rather than REJECT: a
    rejection asserts a measurement, and no measurement was made."""
    return CandidateResult(
        candidate_name=name,
        verdict=CandidateVerdict.BLOCKED_NO_HISTORICAL_DATA,
        effect_type=EffectType.UNDETERMINED_NO_DATA,
        detail=reason,
    )


def classify_effect(
    *, margin_comparison: PairedComparison | None, coverage_delta: float | None,
    coverage_tolerance: float = 0.01,
) -> EffectType:
    """Decide whether a candidate moved the mean, the spread, both or
    neither.

    `coverage_tolerance` guards against reading noise as an uncertainty
    effect: interval coverage wanders by a point or two on a few hundred
    games regardless of the model."""
    moved_mean = margin_comparison is not None and (
        margin_comparison.improves or margin_comparison.degrades
    )
    moved_spread = coverage_delta is not None and abs(coverage_delta) > coverage_tolerance
    if moved_mean and moved_spread:
        return EffectType.BOTH
    if moved_mean:
        return EffectType.POINT_ESTIMATE
    if moved_spread:
        return EffectType.UNCERTAINTY
    return EffectType.NEITHER
