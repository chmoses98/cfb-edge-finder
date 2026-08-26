"""Milestone E, Part J (mission sections 24-25): the future qualification
interface, MECHANICALLY DISABLED.

This schema exists so a later milestone (H, per docs/ROADMAP.md) can wire
in real qualification/sizing logic WITHOUT a breaking schema change -- but
`QualificationStatus` is a two-member closed enum that structurally cannot
express a recommendation. There is no BET/PLAY/WATCH/ACTIONABLE/tier value
reachable from this type; see tests/test_qualification_hard_disabled.py
for the mechanical proof (a substring scan mirroring
tests/test_no_recommendation_surface.py, plus a construction-time
assertion below).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

_FORBIDDEN_VALUE_SUBSTRINGS = (
    "bet",
    "play",
    "stake",
    "tier_a",
    "tier_b",
    "tier_c",
    "order",
    "execute",
)


class QualificationStatus(StrEnum):
    """Exactly two members, both explicitly non-actionable. Adding a third
    member (e.g. ACTIONABLE) is a conscious, reviewed schema change for a
    future milestone -- never an incidental edit to this file."""

    RESEARCH_ONLY = "research_only"
    QUALIFICATION_DISABLED = "qualification_disabled"


class QualificationRecord(BaseModel):
    """Neutral future-facing fields (mission section 24's suggested list),
    all inert today: `status` can only ever be one of the two
    non-actionable enum members above, and every other field is
    free-text/version metadata with no behavior attached -- nothing here
    is read by any pricing, capture, or reporting code path in this
    codebase. Constructing one at all is optional; nothing in Milestone E
    creates one by default."""

    model_config = ConfigDict(frozen=True)

    status: QualificationStatus = QualificationStatus.QUALIFICATION_DISABLED
    threshold_version: str | None = None
    sizing_version: str | None = None
    risk_group: str | None = None
    correlation_cluster: str | None = None
    note: str = "RESEARCH_ONLY -- no qualification, staking, or execution logic is implemented in this codebase."

    @model_validator(mode="after")
    def _no_actionable_language_anywhere(self) -> QualificationRecord:
        for field_name in ("threshold_version", "sizing_version", "risk_group", "correlation_cluster", "note"):
            value = getattr(self, field_name)
            if value is None:
                continue
            lowered = value.lower()
            for forbidden in _FORBIDDEN_VALUE_SUBSTRINGS:
                if forbidden in lowered:
                    raise ValueError(
                        f"QualificationRecord.{field_name} contains forbidden substring {forbidden!r} "
                        f"({value!r}) -- this interface must remain mechanically non-actionable"
                    )
        return self
