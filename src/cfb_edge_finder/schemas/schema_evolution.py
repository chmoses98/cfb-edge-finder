"""Which fields a given corpus schema version was capable of storing.

The problem this exists to solve, concretely. `market_status` was added
to the observation schema on 2026-08-27T17:44:56Z (commit a70bc04). Every
one of the 1,724 rows captured before that moment has
`market_status: None` -- not because the market had no status, but
because the collector had nowhere to put it. A row captured AFTER that
change with `market_status: None` means something entirely different: the
Kalshi payload carried no status, or the collector broke.

Those two rows are byte-identical. Without a version to compare against,
downstream code must either treat both as defects (drowning a real
regression in 1,724 false positives) or treat both as expected (hiding
the regression completely). Neither is acceptable, so the schema version
has to carry the answer.

*** THE POLICY ***

1. Adding a field that downstream code will REQUIRE bumps
   CORPUS_SCHEMA_VERSION. Adding a purely optional annotation does not.
2. Every such field is registered in FIELD_INTRODUCED_IN below, with the
   version that first persisted it.
3. Downstream code asks `field_expected_in(field, row_version)` rather
   than testing `value is None`. A missing value is then classifiable as
   LEGACY_SCHEMA_FIELD_ABSENT or CURRENT_SCHEMA_DEFECT.
4. Old rows are NEVER rewritten to add a field they did not capture.
   Their absence is a true fact about what was recorded, and inventing a
   plausible value would silently manufacture research inputs.

Point 4 is the one worth defending: it would be easy to backfill
`market_status: "active"` on the legacy rows on the reasoning that they
were priced, so they were probably active. "Probably" is doing far too
much work there -- it would fabricate the exact field the closing-capture
gate relies on to refuse fabricated quotes.
"""

from __future__ import annotations

from enum import StrEnum

CORPUS_SCHEMA_V1 = "research_corpus_v1"
"""Original prospective corpus schema. No `market_status` field."""

CORPUS_SCHEMA_V2 = "research_corpus_v2"
"""Adds `market_status`, recorded verbatim from Kalshi at capture time.

Introduced by commit a70bc04 (2026-08-27T17:44:56Z), which added the
field to the observation schema and populated it in ladder_pricing --
but did NOT bump this version, so rows written between that commit and
this one are v1-labelled yet v2-shaped. That window contains zero rows
(the corpus's newest capture is 2026-08-27T02:19:19Z, and the next
legitimately-due supported capture is later still), so the ambiguity is
closed here before it can ever apply to real data rather than being
papered over afterwards."""

ORDERED_SCHEMA_VERSIONS: tuple[str, ...] = (CORPUS_SCHEMA_V1, CORPUS_SCHEMA_V2)

CURRENT_CORPUS_SCHEMA_VERSION = CORPUS_SCHEMA_V2

FIELD_INTRODUCED_IN: dict[str, str] = {
    "market_status": CORPUS_SCHEMA_V2,
}
"""Field name -> first schema version able to persist it. Only fields
whose ABSENCE downstream code must interpret belong here."""


class FieldAvailability(StrEnum):
    """Why a field's value is missing -- the distinction section 4 of the
    Week 1 readiness mission requires."""

    PRESENT = "PRESENT"
    LEGACY_SCHEMA_FIELD_ABSENT = "LEGACY_SCHEMA_FIELD_ABSENT"
    """The row's schema predates the field. Expected, not a defect, and
    never actionable -- the value is unknowable, not permissive."""

    CURRENT_SCHEMA_DEFECT = "CURRENT_SCHEMA_DEFECT"
    """The row's schema should have carried this field and did not. A
    genuine defect: either the upstream payload lacked it or the
    collector regressed."""


def schema_rank(version: str | None) -> int:
    """Position in ORDERED_SCHEMA_VERSIONS. An unknown or absent version
    ranks below every known one: an unrecognised stamp is treated as
    older, never as newer, so it can never be credited with fields it may
    not have."""
    if version is None:
        return -1
    try:
        return ORDERED_SCHEMA_VERSIONS.index(version)
    except ValueError:
        return -1


def field_expected_in(field_name: str, row_schema_version: str | None) -> bool:
    """Was `field_name` storable by rows stamped `row_schema_version`?"""
    introduced = FIELD_INTRODUCED_IN.get(field_name)
    if introduced is None:
        return True
    return schema_rank(row_schema_version) >= schema_rank(introduced)


def classify_field_availability(
    field_name: str, value: object | None, row_schema_version: str | None
) -> FieldAvailability:
    """Classify one field on one row.

    Deliberately takes the value rather than just the version: a legacy
    row that somehow DOES carry the field is PRESENT, because the point
    is what was actually recorded, not what the version predicts."""
    if value is not None:
        return FieldAvailability.PRESENT
    if field_expected_in(field_name, row_schema_version):
        return FieldAvailability.CURRENT_SCHEMA_DEFECT
    return FieldAvailability.LEGACY_SCHEMA_FIELD_ABSENT
