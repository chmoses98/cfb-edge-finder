"""Versioned, auditable empirical threshold artifacts -- schema, parser,
and fail-closed resolution.

*** WHAT THIS IS FOR ***

Eventually, settled prospective games will support statements like "at
this timing label, for this family, in this model-market gap region, the
fee-adjusted result was X with confidence interval Y over N independent
games". Those statements are what a decision engine may act on.

This module defines how such a statement is CARRIED: as a reviewed,
versioned file with full provenance, not as a constant in source.

*** WHAT IT DELIBERATELY DOES NOT DO ***

It ships no artifact and no threshold value. It provides no default that
would let an absent file behave like a permissive one. And it contains no
path -- none -- from "the sample got big enough" to "approved". Approval
is a human act recorded in the file; code only ever reads it.

That last property is the whole point. A system that can promote its own
evidence will eventually promote evidence that happens to look good on a
small sample, which is precisely the failure this architecture exists to
make impossible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from cfb_edge_finder.recommendation.thresholds import ApprovalState

ARTIFACT_SCHEMA_VERSION = "shadow_threshold_artifact_v1"

NO_VALIDATED_THRESHOLD_SET = "NO_VALIDATED_THRESHOLD_SET"
INCOMPATIBLE_THRESHOLD_ARTIFACT = "INCOMPATIBLE_THRESHOLD_ARTIFACT"
THRESHOLD_ARTIFACT_NOT_APPROVED = "THRESHOLD_ARTIFACT_NOT_APPROVED"
THRESHOLD_ARTIFACT_MALFORMED = "THRESHOLD_ARTIFACT_MALFORMED"

SHADOW_APPROVAL_STATES = frozenset({ApprovalState.APPROVED_FOR_SHADOW, ApprovalState.APPROVED_FOR_LIVE})
"""States that make an artifact eligible for SHADOW evaluation.

APPROVED_FOR_LIVE is included because anything cleared for live use is by
construction cleared for shadow. DRAFT_RESEARCH and REVIEWED are not:
being written down and being read are not approval."""

LIVE_APPROVAL_STATES = frozenset({ApprovalState.APPROVED_FOR_LIVE})


class ArtifactProblem(StrEnum):
    """Why a candidate artifact is unusable. Every one is fail-closed."""

    FILE_MISSING = "FILE_MISSING"
    UNPARSEABLE_JSON = "UNPARSEABLE_JSON"
    SCHEMA_VERSION_UNKNOWN = "SCHEMA_VERSION_UNKNOWN"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_FIELD_TYPE = "INVALID_FIELD_TYPE"
    INVALID_APPROVAL_STATE = "INVALID_APPROVAL_STATE"
    NO_RULES = "NO_RULES"
    RULE_SCOPE_EMPTY = "RULE_SCOPE_EMPTY"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"
    NOT_PROSPECTIVE_ONLY = "NOT_PROSPECTIVE_ONLY"


class RuleIncompatibility(StrEnum):
    """Why an otherwise-valid rule does not apply to a given candidate."""

    MODEL_VERSION_MISMATCH = "MODEL_VERSION_MISMATCH"
    FAMILY_MISMATCH = "FAMILY_MISMATCH"
    TIMING_MISMATCH = "TIMING_MISMATCH"
    SIDE_MISMATCH = "SIDE_MISMATCH"
    PRICE_OUT_OF_RANGE = "PRICE_OUT_OF_RANGE"
    GAP_OUT_OF_REGION = "GAP_OUT_OF_REGION"
    SAMPLE_BELOW_RULE_MINIMUM = "SAMPLE_BELOW_RULE_MINIMUM"


@dataclass(frozen=True)
class EvidenceSummary:
    """What the artifact CLAIMS its rules were derived from.

    Recorded so a reviewer can check the claim against the corpus, and so
    a future reader can tell which corpus window and methodology produced
    a rule. Every field is required: an artifact that cannot state its
    own sample size and window is an assertion, not evidence.

    Numeric fields are the artifact's own declarations. This module never
    computes or verifies them -- verification is a human review step, and
    pretending otherwise would recreate auto-promotion by the back door."""

    validation_corpus_identifier: str
    validation_window_start: datetime
    validation_window_end: datetime
    settled_game_count: int
    settled_observation_count: int
    clv_observation_count: int
    calibration_observation_count: int
    research_methodology_version: str
    prospective_only: bool

    fee_adjusted_roi_point: float | None = None
    fee_adjusted_roi_ci_low: float | None = None
    fee_adjusted_roi_ci_high: float | None = None
    clv_point: float | None = None
    clv_ci_low: float | None = None
    clv_ci_high: float | None = None
    calibration_error: float | None = None
    notes: str = ""

    def completeness_problems(self) -> list[ArtifactProblem]:
        problems: list[ArtifactProblem] = []
        if not self.prospective_only:
            problems.append(ArtifactProblem.NOT_PROSPECTIVE_ONLY)
        if self.settled_game_count <= 0 or self.settled_observation_count <= 0:
            problems.append(ArtifactProblem.EVIDENCE_INCOMPLETE)
        if self.validation_window_end <= self.validation_window_start:
            problems.append(ArtifactProblem.EVIDENCE_INCOMPLETE)
        if not self.validation_corpus_identifier or not self.research_methodology_version:
            problems.append(ArtifactProblem.EVIDENCE_INCOMPLETE)
        return problems


@dataclass(frozen=True)
class ThresholdRule:
    """One scoped empirical decision rule.

    `values` is an opaque mapping on purpose. Naming a
    `min_probability_surplus` field here would invite a default, and a
    default is the magic number this whole design forbids. The keys a
    future artifact uses are the future artifact's business.

    Scope axes are all REQUIRED to be non-empty where they are
    applicable: `None`/empty means "unscoped", and an unscoped rule would
    silently apply to populations it was never validated on."""

    rule_id: str
    families: frozenset[str]
    timing_labels: frozenset[str]
    model_versions: frozenset[str]
    minimum_settled_games: int
    values: dict[str, float] = field(default_factory=dict)

    sides: frozenset[str] | None = None
    price_min: float | None = None
    price_max: float | None = None
    gap_min: float | None = None
    gap_max: float | None = None

    def scope_problems(self) -> list[ArtifactProblem]:
        if not self.families or not self.timing_labels or not self.model_versions:
            return [ArtifactProblem.RULE_SCOPE_EMPTY]
        return []

    def incompatibilities(
        self,
        *,
        family: str | None,
        timing_label: str | None,
        model_version: str | None,
        side: str | None,
        executable_price: float | None,
        model_market_gap: float | None,
        available_settled_games: int,
    ) -> list[RuleIncompatibility]:
        """Every reason this rule does not apply. A list, not a first
        failure, so a diagnostic can show all of them at once.

        `None` on any axis is a MISMATCH, never a wildcard. A candidate
        that cannot say which model priced it must not inherit evidence
        gathered under a different model."""
        out: list[RuleIncompatibility] = []
        if model_version is None or model_version not in self.model_versions:
            out.append(RuleIncompatibility.MODEL_VERSION_MISMATCH)
        if family is None or family not in self.families:
            out.append(RuleIncompatibility.FAMILY_MISMATCH)
        if timing_label is None or timing_label not in self.timing_labels:
            out.append(RuleIncompatibility.TIMING_MISMATCH)
        if self.sides is not None and (side is None or side not in self.sides):
            out.append(RuleIncompatibility.SIDE_MISMATCH)
        if self.price_min is not None or self.price_max is not None:
            if executable_price is None:
                out.append(RuleIncompatibility.PRICE_OUT_OF_RANGE)
            elif (self.price_min is not None and executable_price < self.price_min) or (
                self.price_max is not None and executable_price > self.price_max
            ):
                out.append(RuleIncompatibility.PRICE_OUT_OF_RANGE)
        if self.gap_min is not None or self.gap_max is not None:
            if model_market_gap is None:
                out.append(RuleIncompatibility.GAP_OUT_OF_REGION)
            elif (self.gap_min is not None and model_market_gap < self.gap_min) or (
                self.gap_max is not None and model_market_gap > self.gap_max
            ):
                out.append(RuleIncompatibility.GAP_OUT_OF_REGION)
        if available_settled_games < self.minimum_settled_games:
            out.append(RuleIncompatibility.SAMPLE_BELOW_RULE_MINIMUM)
        return out


@dataclass(frozen=True)
class ShadowThresholdArtifact:
    """A reviewed, versioned set of empirical decision rules."""

    schema_version: str
    artifact_version: str
    created_at: datetime
    approval_state: ApprovalState
    approved_by: str
    approved_at: datetime | None
    evidence: EvidenceSummary
    rules: tuple[ThresholdRule, ...]

    @property
    def is_shadow_eligible(self) -> bool:
        return self.approval_state in SHADOW_APPROVAL_STATES

    @property
    def is_live_eligible(self) -> bool:
        return self.approval_state in LIVE_APPROVAL_STATES

    def structural_problems(self) -> list[ArtifactProblem]:
        problems = list(self.evidence.completeness_problems())
        if not self.rules:
            problems.append(ArtifactProblem.NO_RULES)
        for rule in self.rules:
            problems.extend(rule.scope_problems())
        return problems


@dataclass(frozen=True)
class ArtifactResolution:
    """The outcome of asking for thresholds.

    `artifact` is None on every unusable path, so a caller cannot read
    rules off a rejected artifact by accident."""

    status: str
    detail: str
    artifact: ShadowThresholdArtifact | None = None
    problems: tuple[ArtifactProblem, ...] = ()

    @property
    def usable_for_shadow(self) -> bool:
        return self.artifact is not None and self.artifact.is_shadow_eligible


# --- parsing -------------------------------------------------------------


def _require(raw: dict, key: str, problems: list[ArtifactProblem]):
    if key not in raw:
        problems.append(ArtifactProblem.MISSING_REQUIRED_FIELD)
        return None
    return raw[key]


def _parse_dt(value, problems: list[ArtifactProblem]) -> datetime | None:
    if not isinstance(value, str):
        problems.append(ArtifactProblem.INVALID_FIELD_TYPE)
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        problems.append(ArtifactProblem.INVALID_FIELD_TYPE)
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _frozenset_of_str(value, problems: list[ArtifactProblem]) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        problems.append(ArtifactProblem.INVALID_FIELD_TYPE)
        return frozenset()
    return frozenset(value)


def parse_artifact(raw: dict) -> tuple[ShadowThresholdArtifact | None, list[ArtifactProblem]]:
    """Strict parse. Returns (artifact, problems); a non-empty problems
    list ALWAYS means the artifact is unusable, even if an object was
    constructed -- callers must check problems, and `load_artifact` below
    enforces that for them."""
    problems: list[ArtifactProblem] = []
    if not isinstance(raw, dict):
        return None, [ArtifactProblem.INVALID_FIELD_TYPE]

    schema_version = raw.get("schema_version")
    if schema_version != ARTIFACT_SCHEMA_VERSION:
        # An unknown schema is refused outright rather than best-effort
        # parsed: a future format could move the meaning of a field, and
        # guessing would be how a stale rule silently applies.
        return None, [ArtifactProblem.SCHEMA_VERSION_UNKNOWN]

    raw_approval = raw.get("approval_state")
    try:
        approval = ApprovalState(raw_approval)
    except ValueError:
        return None, [ArtifactProblem.INVALID_APPROVAL_STATE]

    ev_raw = _require(raw, "evidence", problems)
    if not isinstance(ev_raw, dict):
        return None, problems + [ArtifactProblem.MISSING_REQUIRED_FIELD]

    window_start = _parse_dt(ev_raw.get("validation_window_start"), problems)
    window_end = _parse_dt(ev_raw.get("validation_window_end"), problems)
    created_at = _parse_dt(raw.get("created_at"), problems)
    approved_at = _parse_dt(raw["approved_at"], problems) if raw.get("approved_at") else None
    if window_start is None or window_end is None or created_at is None:
        return None, problems or [ArtifactProblem.MISSING_REQUIRED_FIELD]

    def _int(source: dict, key: str) -> int:
        val = source.get(key)
        if not isinstance(val, int) or isinstance(val, bool):
            problems.append(ArtifactProblem.INVALID_FIELD_TYPE)
            return -1
        return val

    def _opt_float(source: dict, key: str) -> float | None:
        val = source.get(key)
        if val is None:
            return None
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            problems.append(ArtifactProblem.INVALID_FIELD_TYPE)
            return None
        return float(val)

    evidence = EvidenceSummary(
        validation_corpus_identifier=str(ev_raw.get("validation_corpus_identifier", "")),
        validation_window_start=window_start,
        validation_window_end=window_end,
        settled_game_count=_int(ev_raw, "settled_game_count"),
        settled_observation_count=_int(ev_raw, "settled_observation_count"),
        clv_observation_count=_int(ev_raw, "clv_observation_count"),
        calibration_observation_count=_int(ev_raw, "calibration_observation_count"),
        research_methodology_version=str(ev_raw.get("research_methodology_version", "")),
        prospective_only=bool(ev_raw.get("prospective_only", False)),
        fee_adjusted_roi_point=_opt_float(ev_raw, "fee_adjusted_roi_point"),
        fee_adjusted_roi_ci_low=_opt_float(ev_raw, "fee_adjusted_roi_ci_low"),
        fee_adjusted_roi_ci_high=_opt_float(ev_raw, "fee_adjusted_roi_ci_high"),
        clv_point=_opt_float(ev_raw, "clv_point"),
        clv_ci_low=_opt_float(ev_raw, "clv_ci_low"),
        clv_ci_high=_opt_float(ev_raw, "clv_ci_high"),
        calibration_error=_opt_float(ev_raw, "calibration_error"),
        notes=str(ev_raw.get("notes", "")),
    )

    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, list):
        return None, problems + [ArtifactProblem.NO_RULES]
    rules: list[ThresholdRule] = []
    for entry in rules_raw:
        if not isinstance(entry, dict):
            problems.append(ArtifactProblem.INVALID_FIELD_TYPE)
            continue
        values = entry.get("values", {})
        if not isinstance(values, dict) or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in values.values()
        ):
            problems.append(ArtifactProblem.INVALID_FIELD_TYPE)
            values = {}
        sides_raw = entry.get("sides")
        rules.append(
            ThresholdRule(
                rule_id=str(entry.get("rule_id", "")),
                families=_frozenset_of_str(entry.get("families", []), problems),
                timing_labels=_frozenset_of_str(entry.get("timing_labels", []), problems),
                model_versions=_frozenset_of_str(entry.get("model_versions", []), problems),
                minimum_settled_games=_int(entry, "minimum_settled_games"),
                values={k: float(v) for k, v in values.items()},
                sides=_frozenset_of_str(sides_raw, problems) if sides_raw is not None else None,
                price_min=_opt_float(entry, "price_min"),
                price_max=_opt_float(entry, "price_max"),
                gap_min=_opt_float(entry, "gap_min"),
                gap_max=_opt_float(entry, "gap_max"),
            )
        )

    artifact = ShadowThresholdArtifact(
        schema_version=str(schema_version),
        artifact_version=str(raw.get("artifact_version", "")),
        created_at=created_at,
        approval_state=approval,
        approved_by=str(raw.get("approved_by", "")),
        approved_at=approved_at,
        evidence=evidence,
        rules=tuple(rules),
    )
    problems.extend(artifact.structural_problems())
    return artifact, problems


def load_artifact(path: Path | None) -> ArtifactResolution:
    """Resolve the threshold artifact, fail-closed at every step.

    The ladder of refusal, most fundamental first: no path or no file ->
    NO_VALIDATED_THRESHOLD_SET; unreadable or structurally invalid ->
    THRESHOLD_ARTIFACT_MALFORMED; valid but not human-approved for shadow
    -> THRESHOLD_ARTIFACT_NOT_APPROVED. Only an artifact that survives
    all three is returned, and even then it still has to prove per-rule
    compatibility against each candidate."""
    if path is None or not Path(path).exists():
        return ArtifactResolution(
            status=NO_VALIDATED_THRESHOLD_SET,
            detail="no threshold artifact is configured; empirical gates are locked",
            problems=(ArtifactProblem.FILE_MISSING,),
        )
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ArtifactResolution(
            status=THRESHOLD_ARTIFACT_MALFORMED,
            detail=f"artifact could not be read as JSON: {type(exc).__name__}",
            problems=(ArtifactProblem.UNPARSEABLE_JSON,),
        )

    artifact, problems = parse_artifact(raw)
    if artifact is None or problems:
        return ArtifactResolution(
            status=THRESHOLD_ARTIFACT_MALFORMED,
            detail=f"artifact failed validation: {sorted({p.value for p in problems})}",
            problems=tuple(problems),
        )
    if not artifact.is_shadow_eligible:
        # Deliberately NOT returning the artifact: an unapproved artifact
        # must not be reachable, or a caller could read its rules anyway.
        return ArtifactResolution(
            status=THRESHOLD_ARTIFACT_NOT_APPROVED,
            detail=(
                f"artifact {artifact.artifact_version!r} is {artifact.approval_state.value}; "
                f"shadow use requires explicit human approval"
            ),
        )
    return ArtifactResolution(
        status="THRESHOLD_ARTIFACT_APPROVED_FOR_SHADOW",
        detail=f"artifact {artifact.artifact_version!r} approved as {artifact.approval_state.value}",
        artifact=artifact,
    )
