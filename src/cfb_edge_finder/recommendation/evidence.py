"""How much prospective evidence exists for a family/timing/model slice.

*** NOTHING IS VALIDATED TODAY ***
`VALIDATED` is a state this module can represent but must never be
assigned without genuine settled prospective evidence. With the corpus at
zero settled supported observations, every real slice resolves to
NO_SETTLED_DATA, and `assess_readiness` has no branch that can reach
VALIDATED from an empty sample.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EvidenceState(StrEnum):
    NO_SETTLED_DATA = "NO_SETTLED_DATA"
    """No settled supported observations at all. The current live state."""

    LOW_SAMPLE = "LOW_SAMPLE"
    RESEARCH_ACCUMULATING = "RESEARCH_ACCUMULATING"
    VALIDATION_PENDING = "VALIDATION_PENDING"
    """Enough data to attempt validation; a human has not yet reviewed it."""

    VALIDATED = "VALIDATED"
    """Reserved. Only a reviewed, approved threshold artifact may carry
    this, and `assess_readiness` never returns it -- promotion is a human
    act, not a computation. See thresholds.ApprovalState."""


NON_ACTIONABLE_EVIDENCE_STATES = frozenset(EvidenceState) - {EvidenceState.VALIDATED}

MIN_CLUSTERS_FOR_ACCUMULATING = 5
MIN_CLUSTERS_FOR_VALIDATION_PENDING = 30
"""Cluster counts, NOT profitability cutoffs. These say how much data
exists, never whether the data says anything favourable. Games are the
cluster because one game yields dozens of correlated contracts."""


@dataclass(frozen=True)
class EvidenceReadiness:
    """Describes the evidence behind one slice. Carries no verdict about
    whether anything is worth acting on."""

    family: str | None
    timing_label: str | None
    model_version: str | None
    settled_n: int
    unique_game_clusters: int
    clv_n: int
    prospective_only: bool
    threshold_artifact_version: str | None
    state: EvidenceState
    detail: str

    @property
    def actionable(self) -> bool:
        """Always False unless VALIDATED, which `assess_readiness` cannot
        produce. Kept as an explicit property so a future caller reads a
        named boolean rather than re-deriving the rule."""
        return self.state is EvidenceState.VALIDATED


def assess_readiness(
    *,
    family: str | None,
    timing_label: str | None,
    model_version: str | None,
    settled_n: int,
    unique_game_clusters: int,
    clv_n: int,
    prospective_only: bool = True,
    threshold_artifact_version: str | None = None,
) -> EvidenceReadiness:
    """Classify how much evidence a slice has.

    Deliberately terminates at VALIDATION_PENDING. Reaching VALIDATED
    requires a reviewed, approved artifact; no volume of data alone can
    promote a slice, because "enough rows" is not the same question as
    "the rows say something real"."""
    if not prospective_only:
        state = EvidenceState.NO_SETTLED_DATA
        detail = "slice includes non-prospective rows; retrospective evidence can never support promotion"
    elif settled_n <= 0:
        state = EvidenceState.NO_SETTLED_DATA
        detail = "no settled supported observations exist for this slice"
    elif unique_game_clusters < MIN_CLUSTERS_FOR_ACCUMULATING:
        state = EvidenceState.LOW_SAMPLE
        detail = (
            f"{settled_n} settled row(s) from only {unique_game_clusters} game cluster(s); "
            f"correlated contracts from a handful of games are not a sample"
        )
    elif unique_game_clusters < MIN_CLUSTERS_FOR_VALIDATION_PENDING:
        state = EvidenceState.RESEARCH_ACCUMULATING
        detail = f"{settled_n} settled row(s) across {unique_game_clusters} game cluster(s); still accumulating"
    else:
        state = EvidenceState.VALIDATION_PENDING
        detail = (
            f"{settled_n} settled row(s) across {unique_game_clusters} game cluster(s): enough to ATTEMPT "
            f"validation. Promotion remains a reviewed human decision, not a consequence of sample size."
        )

    return EvidenceReadiness(
        family=family,
        timing_label=timing_label,
        model_version=model_version,
        settled_n=settled_n,
        unique_game_clusters=unique_game_clusters,
        clv_n=clv_n,
        prospective_only=prospective_only,
        threshold_artifact_version=threshold_artifact_version,
        state=state,
        detail=detail,
    )
