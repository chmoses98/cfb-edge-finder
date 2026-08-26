"""Milestone E, Part J: the only place a `QualificationRecord` may be
constructed by application code in this codebase today.

`default_disabled_record()` is the single value every capture path is
allowed to attach -- always QUALIFICATION_DISABLED, never anything else.
There is deliberately no function here that takes a gap/probability and
returns a qualification decision: that logic does not exist yet (Milestone
H, per docs/ROADMAP.md), and this module must not become a place to sneak
it in early.
"""

from __future__ import annotations

from cfb_edge_finder.schemas.qualification import QualificationRecord, QualificationStatus


def default_disabled_record() -> QualificationRecord:
    return QualificationRecord(status=QualificationStatus.QUALIFICATION_DISABLED)
