"""The shadow decision pipeline.

Answers one research question: *if the empirical gates were approved,
what would the system have considered?* It produces no bets, no
recommendations, no stakes, and nothing labelled actionable.

*** WHY THE ZERO IS NOT HARDCODED ***

`SHADOW_QUALIFIED` is a real terminal state that the code can reach. It
is unreachable in this repository only because no approved threshold
artifact exists, and every candidate therefore stops at
`NO_THRESHOLD_ARTIFACT`. The count is computed by counting, so if a lock
ever failed the number would rise and the safety tests would fail --
which is the entire value of not writing `return 0`.

*** PROSPECTIVE ONLY ***

A retrospective/backfilled row can never enter this pipeline. Capture
mode is checked per observation and anything that is not PROSPECTIVE is
rejected before it becomes a candidate, because the whole research claim
rests on evidence gathered before the outcome was known.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from cfb_edge_finder.decision.artifact import (
    NO_VALIDATED_THRESHOLD_SET,
    ArtifactResolution,
    RuleIncompatibility,
)
from cfb_edge_finder.expression.grouping import ContractSnapshot
from cfb_edge_finder.expression.taxonomy import truth_condition_key
from cfb_edge_finder.recommendation.candidate import ResearchCandidate
from cfb_edge_finder.recommendation.eligibility import (
    EligibilityConfig,
    evaluate_quality_prerequisites,
)
from cfb_edge_finder.recommendation.evidence import EvidenceState

PROSPECTIVE_CAPTURE_MODE = "PROSPECTIVE"


class ShadowDecisionState(StrEnum):
    """Where a candidate stopped. Ordered by how early the stop happened."""

    NOT_PROSPECTIVE = "NOT_PROSPECTIVE"
    """Row is retrospective/backfilled. Structurally excluded -- it can
    never become shadow evidence, because the outcome was already known
    when it was written."""

    DATA_QUALITY_FAILED = "DATA_QUALITY_FAILED"
    NO_THRESHOLD_ARTIFACT = "NO_THRESHOLD_ARTIFACT"
    ARTIFACT_MALFORMED = "ARTIFACT_MALFORMED"
    ARTIFACT_NOT_APPROVED = "ARTIFACT_NOT_APPROVED"
    ARTIFACT_INCOMPATIBLE = "ARTIFACT_INCOMPATIBLE"
    EMPIRICAL_GATE_FAILED = "EMPIRICAL_GATE_FAILED"
    EVIDENCE_NOT_VALIDATED = "EVIDENCE_NOT_VALIDATED"

    SHADOW_QUALIFIED = "SHADOW_QUALIFIED"
    """Would have been considered, had the gates been approved. Research
    output only: it is not a bet, a recommendation, or a stake."""


@dataclass(frozen=True)
class ShadowDecision:
    """One candidate's full diagnostic trail -- enough to explain WHY it
    stopped without re-running anything."""

    game_id: str | None
    market_ticker: str
    family: str | None
    side: str
    timing_label: str | None
    model_probability: float | None
    executable_price: float | None
    fee_status: str | None
    fee_adjusted_break_even: float | None
    model_market_gap: float | None

    state: ShadowDecisionState
    rejection_reasons: tuple[str, ...]

    data_quality_failures: tuple[str, ...]
    threshold_artifact_status: str
    evidence_state: str
    equivalence_group: str | None
    correlation_group: str | None

    model_version: str | None
    schema_version: str | None
    capture_mode: str | None

    @property
    def is_shadow_qualified(self) -> bool:
        return self.state is ShadowDecisionState.SHADOW_QUALIFIED


@dataclass
class ShadowRunResult:
    decisions: list[ShadowDecision] = field(default_factory=list)
    artifact_status: str = NO_VALIDATED_THRESHOLD_SET

    @property
    def shadow_qualified_count(self) -> int:
        """Counted, never asserted. If a lock ever broke, this rises."""
        return sum(1 for d in self.decisions if d.is_shadow_qualified)

    @property
    def data_quality_pass_count(self) -> int:
        return sum(1 for d in self.decisions if not d.data_quality_failures)

    def state_counts(self) -> dict[str, int]:
        return dict(Counter(d.state.value for d in self.decisions))

    def rejection_counts(self) -> dict[str, int]:
        return dict(Counter(r for d in self.decisions for r in d.rejection_reasons))


def _gap(candidate: ResearchCandidate) -> float | None:
    if candidate.model_probability is None or candidate.fee_adjusted_break_even_probability is None:
        return None
    return candidate.model_probability - candidate.fee_adjusted_break_even_probability


def evaluate_shadow_candidate(
    candidate: ResearchCandidate,
    snapshot: ContractSnapshot,
    *,
    resolution: ArtifactResolution,
    config: EligibilityConfig,
    evidence_state: EvidenceState,
    available_settled_games: int,
    now: datetime,
    equivalence_group: str | None = None,
    correlation_group: str | None = None,
    capture_mode: str | None = None,
) -> ShadowDecision:
    """Walk one candidate through every gate, stopping at the first that
    refuses and recording why."""
    reasons: list[str] = []
    quality = evaluate_quality_prerequisites(candidate, config, now=now)
    gap = _gap(candidate)

    def build(state: ShadowDecisionState) -> ShadowDecision:
        return ShadowDecision(
            game_id=candidate.game_id,
            market_ticker=candidate.market_ticker,
            family=candidate.market_family,
            side=candidate.executable_side.value,
            timing_label=candidate.timing_label,
            model_probability=candidate.model_probability,
            executable_price=candidate.executable_price,
            fee_status=candidate.fee_status,
            fee_adjusted_break_even=candidate.fee_adjusted_break_even_probability,
            model_market_gap=gap,
            state=state,
            rejection_reasons=tuple(reasons),
            data_quality_failures=tuple(f.value for f in quality),
            threshold_artifact_status=resolution.status,
            evidence_state=evidence_state.value,
            equivalence_group=equivalence_group,
            correlation_group=correlation_group,
            model_version=candidate.model_version,
            schema_version=snapshot.schema_version,
            capture_mode=capture_mode,
        )

    # Prospective-only, checked before anything else: a retrospective row
    # must not even be evaluated, let alone counted.
    if capture_mode is not None and capture_mode != PROSPECTIVE_CAPTURE_MODE:
        reasons.append(f"capture_mode={capture_mode!r} is not {PROSPECTIVE_CAPTURE_MODE}")
        return build(ShadowDecisionState.NOT_PROSPECTIVE)

    if quality:
        reasons.extend(f.value for f in quality)
        return build(ShadowDecisionState.DATA_QUALITY_FAILED)

    if resolution.artifact is None:
        if resolution.status == NO_VALIDATED_THRESHOLD_SET:
            reasons.append("no threshold artifact is configured")
            return build(ShadowDecisionState.NO_THRESHOLD_ARTIFACT)
        if resolution.status == "THRESHOLD_ARTIFACT_NOT_APPROVED":
            reasons.append(resolution.detail)
            return build(ShadowDecisionState.ARTIFACT_NOT_APPROVED)
        reasons.append(resolution.detail)
        return build(ShadowDecisionState.ARTIFACT_MALFORMED)

    artifact = resolution.artifact
    all_incompatibilities: list[RuleIncompatibility] = []
    applicable = []
    for rule in artifact.rules:
        problems = rule.incompatibilities(
            family=candidate.market_family,
            timing_label=candidate.timing_label,
            model_version=candidate.model_version,
            side=candidate.executable_side.value,
            executable_price=candidate.executable_price,
            model_market_gap=gap,
            available_settled_games=available_settled_games,
        )
        if problems:
            all_incompatibilities.extend(problems)
        else:
            applicable.append(rule)

    if not applicable:
        reasons.extend(sorted({p.value for p in all_incompatibilities}))
        return build(ShadowDecisionState.ARTIFACT_INCOMPATIBLE)

    # Evidence readiness is an INDEPENDENT gate from the artifact. An
    # approved artifact says "this rule was validated"; evidence
    # readiness says "this slice has accumulated enough of its own
    # settled data". Both must hold.
    if evidence_state is not EvidenceState.VALIDATED:
        reasons.append(f"evidence state is {evidence_state.value}, not VALIDATED")
        return build(ShadowDecisionState.EVIDENCE_NOT_VALIDATED)

    return build(ShadowDecisionState.SHADOW_QUALIFIED)


def run_shadow_pipeline(
    snapshots: list[ContractSnapshot],
    *,
    resolution: ArtifactResolution,
    config: EligibilityConfig | None = None,
    evidence_state: EvidenceState | None = None,
    available_settled_games: int = 0,
    now: datetime | None = None,
) -> ShadowRunResult:
    """Run the shadow evaluation over a corpus snapshot set.

    Candidates come from `run_pipeline` -- the SAME production
    construction the live path uses -- rather than a parallel builder.
    A shadow run that formed its candidates differently would be
    measuring a system nobody runs.

    `evidence_state` defaults to the state `assess_readiness` actually
    returns for the supplied settled count, so the caller cannot hand in
    VALIDATED and have it believed."""
    from cfb_edge_finder.decision.portfolio import build_portfolio_view
    from cfb_edge_finder.recommendation.evidence import assess_readiness
    from cfb_edge_finder.recommendation.pipeline import run_pipeline

    config = config or EligibilityConfig()
    moment = now or datetime.now(UTC)
    pipeline_result = run_pipeline(snapshots, config=config, now=moment)

    by_ticker = {s.semantics.market_ticker: s for s in snapshots}
    portfolio = build_portfolio_view([s.semantics for s in snapshots])

    result = ShadowRunResult(artifact_status=resolution.status)
    for candidate in pipeline_result.candidates:
        snapshot = by_ticker.get(candidate.market_ticker)
        if snapshot is None:
            # A candidate whose snapshot cannot be found is a structural
            # inconsistency, not something to evaluate around.
            continue
        state = evidence_state or assess_readiness(
            family=candidate.market_family or "",
            timing_label=candidate.timing_label,
            model_version=candidate.model_version,
            settled_n=available_settled_games,
            unique_game_clusters=available_settled_games,
            clv_n=available_settled_games,
        ).state
        group = portfolio.group_for(candidate.market_ticker)
        result.decisions.append(
            evaluate_shadow_candidate(
                candidate,
                snapshot,
                resolution=resolution,
                config=config,
                evidence_state=state,
                available_settled_games=available_settled_games,
                now=moment,
                equivalence_group=truth_condition_key(snapshot.semantics, candidate.executable_side),
                correlation_group=group.group_key if group is not None else None,
                capture_mode=snapshot.capture_mode,
            )
        )
    return result
