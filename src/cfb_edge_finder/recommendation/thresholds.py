"""The contract a future empirical threshold artifact must satisfy, and
the provider abstraction that supplies one.

*** NO THRESHOLD VALUES EXIST HERE, ON PURPOSE ***
This module defines the SHAPE of a threshold artifact and the rules for
when one may be applied. It contains no cutoff values, and the default
provider returns NO_VALIDATED_THRESHOLD_SET. A number written here today
would be somebody's intuition wearing the authority of code; the corpus
has zero settled observations to justify one.

*** WHY COMPATIBILITY IS ENFORCED RATHER THAN TRUSTED ***
Empirical evidence is evidence about a specific configuration. A cutoff
validated on winner markets at T_30 under model 0.4.0 says nothing about
totals at EARLY_OPEN under model 0.5.0 -- the model change alone
invalidates it, because the probabilities it was calibrated against no
longer exist. Silent reuse across any of those axes would be the quietest
possible way to ship an unvalidated bet, so each axis is checked
explicitly and a mismatch is an outright refusal rather than a warning.

*** PROMOTION IS A HUMAN ACT ***
There is deliberately no function that inspects performance and returns an
approved artifact. Approval is a state a person sets after review; see
ApprovalState and the absence of any auto-promotion API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

NO_VALIDATED_THRESHOLD_SET = "NO_VALIDATED_THRESHOLD_SET"


class ApprovalState(StrEnum):
    """How far a threshold artifact has progressed through human review.

    Ordering is informational only -- nothing advances a state
    automatically, and no code in this repository emits
    APPROVED_FOR_LIVE."""

    DRAFT_RESEARCH = "DRAFT_RESEARCH"
    REVIEWED = "REVIEWED"
    APPROVED_FOR_SHADOW = "APPROVED_FOR_SHADOW"
    APPROVED_FOR_LIVE = "APPROVED_FOR_LIVE"


LIVE_APPROVAL_STATES = frozenset({ApprovalState.APPROVED_FOR_LIVE})
NON_LIVE_APPROVAL_STATES = frozenset(ApprovalState) - LIVE_APPROVAL_STATES


class ThresholdIncompatibility(StrEnum):
    """Why an artifact may not be applied to a candidate."""

    NO_ARTIFACT = "NO_VALIDATED_THRESHOLD_SET"
    MODEL_VERSION_MISMATCH = "MODEL_VERSION_MISMATCH"
    TIMING_LABEL_MISMATCH = "TIMING_LABEL_MISMATCH"
    FAMILY_MISMATCH = "FAMILY_MISMATCH"
    NOT_APPROVED_FOR_LIVE = "NOT_APPROVED_FOR_LIVE"
    NOT_PROSPECTIVE_ONLY = "NOT_PROSPECTIVE_ONLY"
    INSUFFICIENT_DECLARED_EVIDENCE = "INSUFFICIENT_DECLARED_EVIDENCE"


@dataclass(frozen=True)
class ThresholdProvenance:
    """Where an artifact's evidence came from. Every field is required
    for a future artifact to be applicable: an artifact that cannot say
    which corpus, which model and how many settled games produced it is
    not evidence, it is an assertion."""

    source_corpus_identifier: str
    prospective_only: bool
    settled_game_count: int
    created_at: datetime
    analytics_code_version: str
    model_version: str
    approval_state: ApprovalState


@dataclass(frozen=True)
class ThresholdArtifact:
    """A versioned, externally supplied empirical threshold set.

    `values` is deliberately an opaque mapping rather than named numeric
    fields. Naming a `min_probability_surplus` field here would invite a
    default, and a default is exactly the magic number this design
    forbids. A future artifact carries its own keys, loaded from a
    reviewed file, never from source."""

    artifact_version: str
    provenance: ThresholdProvenance
    applicable_model_versions: frozenset[str]
    applicable_timing_labels: frozenset[str]
    applicable_families: frozenset[str]
    values: dict[str, float] = field(default_factory=dict)

    def compatibility_failures(
        self, *, model_version: str | None, timing_label: str | None, family: str | None
    ) -> list[ThresholdIncompatibility]:
        """Every reason this artifact may NOT be applied. Returns a list
        rather than a first failure so a diagnostic can show all of them
        at once."""
        failures: list[ThresholdIncompatibility] = []
        if self.provenance.approval_state not in LIVE_APPROVAL_STATES:
            failures.append(ThresholdIncompatibility.NOT_APPROVED_FOR_LIVE)
        if not self.provenance.prospective_only:
            failures.append(ThresholdIncompatibility.NOT_PROSPECTIVE_ONLY)
        if self.provenance.settled_game_count <= 0:
            failures.append(ThresholdIncompatibility.INSUFFICIENT_DECLARED_EVIDENCE)
        # `None` never matches: an unknown axis is a mismatch, not a
        # wildcard. A candidate that cannot say which model priced it must
        # not inherit evidence gathered under some other model.
        if model_version is None or model_version not in self.applicable_model_versions:
            failures.append(ThresholdIncompatibility.MODEL_VERSION_MISMATCH)
        if timing_label is None or timing_label not in self.applicable_timing_labels:
            failures.append(ThresholdIncompatibility.TIMING_LABEL_MISMATCH)
        if family is None or family not in self.applicable_families:
            failures.append(ThresholdIncompatibility.FAMILY_MISMATCH)
        return failures


@dataclass(frozen=True)
class ThresholdResolution:
    """The outcome of asking a provider for thresholds. `artifact` is
    None whenever the set is unusable, so a caller cannot accidentally
    read `values` off a rejected artifact."""

    available: bool
    reason: str
    artifact: ThresholdArtifact | None = None
    failures: tuple[ThresholdIncompatibility, ...] = ()


class ThresholdProvider:
    """Interface a future artifact loader will implement."""

    def resolve(
        self, *, model_version: str | None, timing_label: str | None, family: str | None
    ) -> ThresholdResolution:  # pragma: no cover - abstract
        raise NotImplementedError


class NullThresholdProvider(ThresholdProvider):
    """The ONLY provider wired up today, and the default everywhere.

    Returns NO_VALIDATED_THRESHOLD_SET unconditionally. There is no
    constructor argument, environment variable, or config flag that makes
    it return anything else -- producing an artifact requires a different
    class that a human deliberately writes and wires in."""

    def resolve(
        self, *, model_version: str | None, timing_label: str | None, family: str | None
    ) -> ThresholdResolution:
        return ThresholdResolution(
            available=False,
            reason=NO_VALIDATED_THRESHOLD_SET,
            artifact=None,
            failures=(ThresholdIncompatibility.NO_ARTIFACT,),
        )


class StaticThresholdProvider(ThresholdProvider):
    """Serves one caller-supplied artifact, applying every compatibility
    rule. Exists so the compatibility contract is testable; it holds no
    artifact of its own and cannot be constructed without one being
    handed in from outside this repository's source."""

    def __init__(self, artifact: ThresholdArtifact) -> None:
        self._artifact = artifact

    def resolve(
        self, *, model_version: str | None, timing_label: str | None, family: str | None
    ) -> ThresholdResolution:
        failures = self._artifact.compatibility_failures(
            model_version=model_version, timing_label=timing_label, family=family
        )
        if failures:
            return ThresholdResolution(
                available=False,
                reason="; ".join(f.value for f in failures),
                artifact=None,
                failures=tuple(failures),
            )
        return ThresholdResolution(available=True, reason="compatible", artifact=self._artifact)


DEFAULT_THRESHOLD_PROVIDER = NullThresholdProvider()
"""The default used by every eligibility path. Replacing it is a
deliberate act by a human with a reviewed artifact in hand."""
