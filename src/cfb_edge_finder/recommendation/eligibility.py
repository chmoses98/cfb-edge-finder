"""The eligibility boundary. Every path here ends in QUALIFICATION_DISABLED.

*** TWO KINDS OF GATE, DELIBERATELY SEPARATED ***
1. **Quality prerequisites** -- is this candidate's DATA usable at all?
   Executable side present, market open, quote fresh, fee schedule
   verified, semantics resolved, supported population. These are
   structural facts about the record, not opinions about value. A
   candidate can fail them today and that is ordinary.
2. **Empirical qualification** -- does the evidence say this is worth
   acting on? That requires a versioned, approved threshold artifact, and
   none exists.

Conflating the two would be the dangerous mistake: a candidate that passes
every DATA check has proven only that we know what it costs, not that it
is worth anything. So passing prerequisites never advances a candidate
toward actionable -- the second gate is separate, and currently closed.

*** THE DISABLED STATE IS STRUCTURAL, NOT A FLAG ***
`evaluate_eligibility` returns QUALIFICATION_DISABLED whenever the
threshold provider yields no usable artifact, and the default provider
(NullThresholdProvider) cannot yield one. There is no boolean anywhere
that turns qualification on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from cfb_edge_finder.recommendation.candidate import ResearchCandidate
from cfb_edge_finder.recommendation.evidence import EvidenceReadiness, EvidenceState
from cfb_edge_finder.recommendation.thresholds import (
    DEFAULT_THRESHOLD_PROVIDER,
    ThresholdProvider,
)
from cfb_edge_finder.schemas.common import Side
from cfb_edge_finder.schemas.schema_evolution import FieldAvailability, classify_field_availability

QUALIFICATION_DISABLED = "QUALIFICATION_DISABLED"
QUOTE_AGE_UNCONFIGURED = "UNCONFIGURED"

EXECUTABLE_MARKET_STATUSES = frozenset({"active"})
VERIFIED_FEE_STATUSES = frozenset({"VERIFIED_CURRENT"})
SUPPORTED_FAMILIES = frozenset({"moneyline", "spread", "total"})
SUPPORTED_PRICING_STATUS = "model_priced"


class QualityPrerequisite(StrEnum):
    """Structural/data-quality gates. Failing one is a statement about
    the RECORD, never about the opportunity's value."""

    EXECUTABLE_SIDE_PRESENT = "EXECUTABLE_SIDE_PRESENT"
    MARKET_EXECUTABLE = "MARKET_EXECUTABLE"
    QUOTE_FRESH = "QUOTE_FRESH"
    FEE_SCHEDULE_VERIFIED = "FEE_SCHEDULE_VERIFIED"
    SEMANTICS_RESOLVED = "SEMANTICS_RESOLVED"
    SUPPORTED_POPULATION = "SUPPORTED_POPULATION"
    MODEL_PROBABILITY_PRESENT = "MODEL_PROBABILITY_PRESENT"
    LEGACY_SCHEMA_MARKET_STATUS_UNAVAILABLE = "LEGACY_SCHEMA_MARKET_STATUS_UNAVAILABLE"
    """The row predates `market_status` (schemas/schema_evolution.py).
    Reported INSTEAD of MARKET_EXECUTABLE so a 2026-08-26 legacy row is
    not silently indistinguishable from a current row whose quote is
    genuinely broken. It is exactly as disqualifying: the status is
    unknowable, and unknowable is never executable."""


class FamilyResearchStatus(StrEnum):
    """Versioned statement of how much confidence the RESEARCH has in a
    family. Not a permission to act on it."""

    SUPPORTED_RESEARCH_FAMILY = "SUPPORTED_RESEARCH_FAMILY"
    RESEARCH_PRIMITIVE_LOWER_CONFIDENCE = "RESEARCH_PRIMITIVE_LOWER_CONFIDENCE"
    UNSUPPORTED = "UNSUPPORTED"


FAMILY_STATUS_VERSION = "family_status_v1"

FAMILY_RESEARCH_STATUS: dict[str, FamilyResearchStatus] = {
    "moneyline": FamilyResearchStatus.SUPPORTED_RESEARCH_FAMILY,
    "spread": FamilyResearchStatus.SUPPORTED_RESEARCH_FAMILY,
    # Totals are priced, which is NOT the same as validated. The totals
    # model underperformed the naive benchmark in Milestone C.2
    # backtesting, and being priceable must never quietly promote it.
    "total": FamilyResearchStatus.RESEARCH_PRIMITIVE_LOWER_CONFIDENCE,
}


@dataclass(frozen=True)
class EligibilityConfig:
    """What a future eligibility pass would be allowed to consider.

    Note what is absent: there is no minimum-surplus field, no minimum-CLV
    field, no cutoff of any kind. Those belong to a threshold artifact,
    not to configuration -- a config value would be editable without
    review, which is precisely the property a validated threshold must
    not have."""

    allowed_families: frozenset[str] = field(default_factory=lambda: frozenset(SUPPORTED_FAMILIES))
    allowed_timing_labels: frozenset[str] | None = None
    """None means 'no restriction expressed'. It does NOT mean permitted:
    qualification is still refused for want of a threshold artifact."""

    max_quote_age_seconds: float | None = None
    """None == UNCONFIGURED. A future actionable path must supply this
    explicitly; `evaluate_quality_prerequisites` refuses to certify a
    quote as fresh when it is unset, so staleness cannot pass silently."""

    require_verified_fee_schedule: bool = True
    require_supported_population: bool = True
    threshold_provider: ThresholdProvider = DEFAULT_THRESHOLD_PROVIDER

    @property
    def quote_age_policy(self) -> str:
        return QUOTE_AGE_UNCONFIGURED if self.max_quote_age_seconds is None else f"{self.max_quote_age_seconds}s"


@dataclass(frozen=True)
class EligibilityResult:
    """The outcome for one candidate. `actionable` is the only field a
    future caller should branch on, and it is False on every path this
    repository can currently reach."""

    candidate_ticker: str
    executable_side: Side
    actionable: bool
    status: str
    quality_failures: tuple[QualityPrerequisite, ...]
    threshold_reason: str
    family_status: FamilyResearchStatus
    evidence_state: EvidenceState
    detail: str


def evaluate_quality_prerequisites(
    candidate: ResearchCandidate, config: EligibilityConfig, *, now: datetime | None = None
) -> list[QualityPrerequisite]:
    """Which DATA-quality gates this candidate fails.

    An empty list means the record is usable, not that the opportunity is
    good. Those are different claims and this function only makes the
    first one."""
    failures: list[QualityPrerequisite] = []

    if candidate.executable_price is None or candidate.fee_adjusted_break_even_probability is None:
        failures.append(QualityPrerequisite.EXECUTABLE_SIDE_PRESENT)
    if (candidate.market_status or "").strip().lower() not in EXECUTABLE_MARKET_STATUSES:
        # Same verdict either way -- only the reported reason differs, so
        # a legacy backlog cannot drown out a live regression.
        availability = classify_field_availability("market_status", candidate.market_status, candidate.schema_version)
        failures.append(
            QualityPrerequisite.LEGACY_SCHEMA_MARKET_STATUS_UNAVAILABLE
            if availability is FieldAvailability.LEGACY_SCHEMA_FIELD_ABSENT
            else QualityPrerequisite.MARKET_EXECUTABLE
        )
    if not candidate.semantics_resolved:
        failures.append(QualityPrerequisite.SEMANTICS_RESOLVED)
    if candidate.model_probability is None:
        failures.append(QualityPrerequisite.MODEL_PROBABILITY_PRESENT)

    if config.require_verified_fee_schedule and (candidate.fee_status or "") not in VERIFIED_FEE_STATUSES:
        failures.append(QualityPrerequisite.FEE_SCHEDULE_VERIFIED)

    if config.require_supported_population and (
        candidate.market_family not in config.allowed_families
        or candidate.pricing_status != SUPPORTED_PRICING_STATUS
    ):
        failures.append(QualityPrerequisite.SUPPORTED_POPULATION)

    # Freshness: an UNCONFIGURED max age can never certify a quote as
    # fresh. Treating "no policy" as "any age is fine" is exactly how a
    # stale price reaches an actionable path unnoticed.
    if config.max_quote_age_seconds is None:
        failures.append(QualityPrerequisite.QUOTE_FRESH)
    elif now is not None and candidate.captured_at:
        try:
            captured = datetime.fromisoformat(candidate.captured_at.replace("Z", "+00:00"))
        except ValueError:
            failures.append(QualityPrerequisite.QUOTE_FRESH)
        else:
            if (now - captured).total_seconds() > config.max_quote_age_seconds:
                failures.append(QualityPrerequisite.QUOTE_FRESH)

    return failures


def family_research_status(family: str | None) -> FamilyResearchStatus:
    return FAMILY_RESEARCH_STATUS.get(family or "", FamilyResearchStatus.UNSUPPORTED)


def evaluate_eligibility(
    candidate: ResearchCandidate,
    config: EligibilityConfig | None = None,
    *,
    readiness: EvidenceReadiness | None = None,
    now: datetime | None = None,
) -> EligibilityResult:
    """Evaluate one candidate. ALWAYS returns actionable=False today.

    The threshold provider is consulted last and its refusal is decisive:
    even a candidate that passes every data gate and sits in a
    fully-evidenced slice cannot become actionable without a compatible,
    approved artifact."""
    config = config or EligibilityConfig()
    quality_failures = evaluate_quality_prerequisites(candidate, config, now=now)
    resolution = config.threshold_provider.resolve(
        model_version=candidate.model_version,
        timing_label=candidate.timing_label,
        family=candidate.market_family,
    )
    evidence_state = readiness.state if readiness is not None else EvidenceState.NO_SETTLED_DATA

    if not resolution.available:
        detail = (
            f"qualification is disabled: {resolution.reason}. Data-quality gates "
            f"{'passed' if not quality_failures else 'failed (' + ', '.join(f.value for f in quality_failures) + ')'}, "
            f"which is a statement about the record only -- it never makes a candidate actionable."
        )
        return EligibilityResult(
            candidate_ticker=candidate.market_ticker,
            executable_side=candidate.executable_side,
            actionable=False,
            status=QUALIFICATION_DISABLED,
            quality_failures=tuple(quality_failures),
            threshold_reason=resolution.reason,
            family_status=family_research_status(candidate.market_family),
            evidence_state=evidence_state,
            detail=detail,
        )

    # Unreachable with the default provider. Retained so the contract a
    # future artifact must satisfy is explicit: even WITH a compatible
    # approved artifact, data-quality failures and an unvalidated evidence
    # slice each independently block actionability.
    blocked = bool(quality_failures) or evidence_state is not EvidenceState.VALIDATED
    return EligibilityResult(
        candidate_ticker=candidate.market_ticker,
        executable_side=candidate.executable_side,
        actionable=not blocked,
        status=QUALIFICATION_DISABLED if blocked else "THRESHOLD_ARTIFACT_APPLIED",
        quality_failures=tuple(quality_failures),
        threshold_reason=resolution.reason,
        family_status=family_research_status(candidate.market_family),
        evidence_state=evidence_state,
        detail="a compatible approved artifact was supplied; remaining gates decide",
    )
