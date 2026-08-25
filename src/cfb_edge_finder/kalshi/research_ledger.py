"""Milestone D: the append-only research ledger (mission sections 13/14/
18/19) -- a store of `KalshiResearchObservation` rows, plus the pure
function that derives each row's pipeline-progress readiness label.

*** WHY "research_probability_gap", NEVER "edge" ***
Mission section 10 is explicit: the raw difference between the C.2
model's probability and Kalshi's executable price must never be
presented as if it already qualifies as a betting opportunity -- that
requires fee accounting (section 12, currently UNVERIFIED -- see
`kalshi/executable_price.py`), staking logic, and qualification
thresholds that do not exist anywhere in this codebase and that this
milestone is explicitly forbidden from building. "Research probability
gap" is deliberately a purely descriptive, comparison-only term.

*** APPEND-ONLY / IMMUTABLE ***
`ResearchLedger.append()` refuses a SECOND row for the same
(snapshot_id, kalshi_market_ticker) pair -- "every discovered contract
appears exactly once per snapshot" (mission section 18) -- and never
offers any kind of update/replace method. A later observation of the
same market is always a NEW snapshot_id, appended as a new row; nothing
in this module ever mutates a stored `KalshiResearchObservation` (which
is itself a frozen pydantic model).

*** RESEARCH READINESS (mission section 19) ***
A second, small, explicitly NON-recommendation vocabulary describing how
far ONE observation got through the pipeline. Deliberately mechanical --
`derive_research_readiness` is a pure function of the observation's own
already-set fields, so a specific readiness label is always provable
from the row itself, not an independent judgment call recorded
separately (and possibly inconsistent with the row's own status
fields).
"""

from __future__ import annotations

from enum import StrEnum

from cfb_edge_finder.schemas.kalshi_observation import KalshiResearchObservation


class ResearchReadiness(StrEnum):
    DISCOVERED = "discovered"
    MAPPED = "mapped"
    SEMANTICS_VERIFIED = "semantics_verified"
    MODEL_PRICED = "model_priced"
    RESEARCH_COMPARABLE = "research_comparable"
    UNSUPPORTED = "unsupported"
    UNRESOLVED = "unresolved"


def derive_research_readiness(observation: KalshiResearchObservation) -> ResearchReadiness:
    """Pure, total function -- every `KalshiResearchObservation` maps to
    exactly one `ResearchReadiness`. No recommendation-readiness states
    (WATCH/EARLY_VALUE/ACTIONABLE) are reachable from here at all --
    this function's return type structurally cannot express one."""
    if observation.pricing_status in ("unsupported_population", "unsupported_family"):
        return ResearchReadiness.UNSUPPORTED
    if observation.parse_status == "unresolved":
        return ResearchReadiness.UNRESOLVED
    if observation.game_id is None:
        return ResearchReadiness.UNRESOLVED
    if observation.pricing_status == "model_priced" and observation.model_probability is not None:
        if observation.research_probability_gap is not None:
            return ResearchReadiness.RESEARCH_COMPARABLE
        return ResearchReadiness.MODEL_PRICED
    if observation.parse_status in ("confirmed_live", "unconfirmed") and observation.family is not None:
        return ResearchReadiness.SEMANTICS_VERIFIED
    return ResearchReadiness.MAPPED


class DuplicateObservationError(ValueError):
    """Raised when a caller attempts to append a second observation for
    the same (snapshot_id, kalshi_market_ticker) pair -- mission section
    18's "every discovered contract appears exactly once per snapshot,"
    enforced mechanically rather than left to caller discipline."""


class ResearchLedger:
    """An in-process, append-only store. Persistence (writing rows to a
    file/table) is deliberately left to a caller -- this class's own
    contract is the append-only/no-duplicate/no-mutate invariant, not
    I/O."""

    def __init__(self) -> None:
        self._rows: list[KalshiResearchObservation] = []
        self._seen: set[tuple[str, str]] = set()

    def __len__(self) -> int:
        return len(self._rows)

    def append(self, observation: KalshiResearchObservation) -> None:
        key = (observation.snapshot_id, observation.kalshi_market_ticker)
        if key in self._seen:
            raise DuplicateObservationError(
                f"snapshot {observation.snapshot_id!r} already has an observation for market "
                f"{observation.kalshi_market_ticker!r} -- this ledger is append-only and each "
                f"contract must appear exactly once per snapshot"
            )
        self._seen.add(key)
        self._rows.append(observation)

    def rows(self) -> tuple[KalshiResearchObservation, ...]:
        return tuple(self._rows)

    def rows_for_snapshot(self, snapshot_id: str) -> tuple[KalshiResearchObservation, ...]:
        return tuple(r for r in self._rows if r.snapshot_id == snapshot_id)

    def coverage_outcome_counts(self, snapshot_id: str | None = None) -> dict[str, int]:
        """Mission section 3's "coverage summary counts must sum exactly
        to all discovered markets" -- callable per-snapshot or across the
        whole ledger; the caller can always check
        `sum(counts.values()) == len(rows)` directly against this
        ledger's own row count."""
        rows = self.rows_for_snapshot(snapshot_id) if snapshot_id is not None else self.rows()
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.coverage_outcome.value] = counts.get(row.coverage_outcome.value, 0) + 1
        return counts

    def readiness_counts(self, snapshot_id: str | None = None) -> dict[str, int]:
        rows = self.rows_for_snapshot(snapshot_id) if snapshot_id is not None else self.rows()
        counts: dict[str, int] = {}
        for row in rows:
            label = derive_research_readiness(row).value
            counts[label] = counts.get(label, 0) + 1
        return counts
