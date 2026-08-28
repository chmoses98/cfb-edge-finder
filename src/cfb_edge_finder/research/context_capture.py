"""Research-ONLY contextual capture. Structurally cannot reach the model.

*** WHY THIS EXISTS ***

The Week 1 input audit established that the model's point estimate
carries no 2026 information at all: `season_carryover_weight(0) == 0.0`,
so a Week 1 projection is entirely 2025 ratings. Quarterback identity,
injuries, transfers and coaching changes are invisible to it.

The tempting response is to start adjusting probabilities for those
things. That would be handicapping by intuition, and it would destroy the
only clean measurement available: whether those factors actually explain
model-market error. So instead we RECORD the context, prospectively, with
provenance, and change nothing.

*** THE INVARIANT ***

    No field in this module may ever influence GameDistribution,
    model_probability, projected_margin, or projected_total.

Enforced structurally, not by intention: nothing under `modeling/`,
`projections/` or `ratings/` imports this module, and a test parses
imports to prove it. The same discipline as `cfb_edge_finder.sizing`.

*** SOURCE DISCIPLINE ***

Only sources that are reproducible and attributable. CFBD for roster and
coaching facts; NWS/NOAA for weather. Explicitly NOT random blogs,
aggregator scrapes, or beat-writer speculation -- a field whose value
cannot be reproduced later is worse than a missing field, because it
looks like data.

Where no dependable source exists -- and for CFB injuries none does,
there being no mandatory injury report -- the gap is RECORDED as
`SOURCE_UNAVAILABLE` rather than filled by guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

CONTEXT_CAPTURE_VERSION = "context_capture_v1"

MODEL_IMPACT = "NONE_RESEARCH_ONLY"
"""Asserted by tests/test_context_capture.py, not merely stated here."""


class ContextAvailability(StrEnum):
    OBSERVED = "OBSERVED"
    """A real value from a named, reproducible source."""

    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    """No dependable source exists for this field. A recorded gap, which
    is honest, rather than an invented value, which is not."""

    NOT_YET_CAPTURED = "NOT_YET_CAPTURED"
    """A source exists and is wired, but this record predates the
    capture. Distinguished from SOURCE_UNAVAILABLE for the same reason
    market_status distinguishes a legacy row from a current defect."""

    DERIVED_PROXY = "DERIVED_PROXY"
    """Computed from something adjacent rather than observed directly --
    e.g. returning passing production standing in for QB continuity. Must
    never be read as the thing itself."""


class ContextSource(StrEnum):
    CFBD_RETURNING_PRODUCTION = "CFBD_RETURNING_PRODUCTION"
    CFBD_GAMES = "CFBD_GAMES"
    CFBD_COACHES = "CFBD_COACHES"
    NWS_NOAA = "NWS_NOAA"
    NONE_AVAILABLE = "NONE_AVAILABLE"


@dataclass(frozen=True)
class ContextField:
    """One contextual observation with its full provenance.

    Value, source, availability and timestamp travel together on purpose:
    a value without a source cannot be audited, and a value without a
    timestamp cannot be shown to have predated the game."""

    name: str
    value: str | float | bool | None
    availability: ContextAvailability
    source: ContextSource
    observed_at: datetime | None
    detail: str = ""

    @property
    def is_usable_evidence(self) -> bool:
        """Only a genuinely observed value from a real source counts as
        evidence in a later ablation. A proxy is reported but must be
        analysed as a proxy."""
        return (
            self.availability is ContextAvailability.OBSERVED
            and self.source is not ContextSource.NONE_AVAILABLE
            and self.observed_at is not None
        )


@dataclass(frozen=True)
class GameContextRecord:
    """Prospective context for one game, captured before kickoff.

    `captured_at` must precede kickoff for this to be prospective
    evidence; `is_prospective` checks it rather than assuming it."""

    game_id: str
    captured_at: datetime
    kickoff_utc: datetime | None
    capture_mode: str
    fields: tuple[ContextField, ...] = ()
    context_capture_version: str = CONTEXT_CAPTURE_VERSION
    model_impact: str = MODEL_IMPACT

    @property
    def is_prospective(self) -> bool:
        if self.capture_mode != "PROSPECTIVE" or self.kickoff_utc is None:
            return False
        return self.captured_at < self.kickoff_utc

    def field_named(self, name: str) -> ContextField | None:
        for item in self.fields:
            if item.name == name:
                return item
        return None

    def availability_summary(self) -> dict[str, int]:
        counts = {a.value: 0 for a in ContextAvailability}
        for item in self.fields:
            counts[item.availability.value] += 1
        return counts

    def to_payload(self) -> dict:
        return {
            "game_id": self.game_id,
            "captured_at": self.captured_at.isoformat(),
            "kickoff_utc": self.kickoff_utc.isoformat() if self.kickoff_utc else None,
            "capture_mode": self.capture_mode,
            "context_capture_version": self.context_capture_version,
            "model_impact": self.model_impact,
            "is_prospective": self.is_prospective,
            "fields": [
                {
                    "name": f.name,
                    "value": f.value,
                    "availability": f.availability.value,
                    "source": f.source.value,
                    "observed_at": f.observed_at.isoformat() if f.observed_at else None,
                    "detail": f.detail,
                }
                for f in self.fields
            ],
        }


CONTEXT_FIELD_PLAN: dict[str, tuple[ContextSource, ContextAvailability, str]] = {
    "qb_continuity_proxy": (
        ContextSource.CFBD_RETURNING_PRODUCTION,
        ContextAvailability.DERIVED_PROXY,
        "Team-level returning passing PPA. A PROXY for passing-game continuity, never "
        "QB identity: a team can return 100% of its passing production around a brand-new "
        "transfer quarterback.",
    ),
    "expected_starting_qb": (
        ContextSource.NONE_AVAILABLE,
        ContextAvailability.SOURCE_UNAVAILABLE,
        "No reproducible depth-chart feed is wired. Recorded as a gap rather than "
        "scraped from beat reporting.",
    ),
    "qb_new_starter_flag": (
        ContextSource.NONE_AVAILABLE,
        ContextAvailability.SOURCE_UNAVAILABLE,
        "Requires QB identity, which is unavailable. The continuity proxy is not a "
        "substitute and must not be relabelled as one.",
    ),
    "material_injury_status": (
        ContextSource.NONE_AVAILABLE,
        ContextAvailability.SOURCE_UNAVAILABLE,
        "College football has no mandatory injury report and no structured API. This is "
        "an editorial/NLP problem, not a subscribable feed.",
    ),
    "head_coach_change": (
        ContextSource.CFBD_COACHES,
        ContextAvailability.NOT_YET_CAPTURED,
        "CFBD /coaches can answer this season-over-season. Wired as a plan; not captured "
        "in this mission.",
    ),
    "weather_snapshot": (
        ContextSource.NWS_NOAA,
        ContextAvailability.NOT_YET_CAPTURED,
        "NWS/NOAA is free and needs no key. Forecast-oriented, so it must be captured "
        "PROSPECTIVELY -- it cannot be reconstructed after the fact, which is precisely "
        "why the plan records it now.",
    ),
    "venue": (ContextSource.CFBD_GAMES, ContextAvailability.OBSERVED, "From the game record."),
    "neutral_site_flag": (
        ContextSource.CFBD_GAMES,
        ContextAvailability.OBSERVED,
        "From the game record. This is the ONE contextual input the model already uses "
        "correctly -- it forces the home-field term to zero.",
    ),
}
"""The declared plan, including its gaps. Publishing the gaps is the
point: an unlisted missing field looks like an oversight, a listed one is
a known limitation with a reason."""


def build_context_record(
    *,
    game_id: str,
    captured_at: datetime,
    kickoff_utc: datetime | None,
    observed: dict[str, str | float | bool | None] | None = None,
    capture_mode: str = "PROSPECTIVE",
) -> GameContextRecord:
    """Build a record from the declared plan plus any observed values.

    A field absent from `observed` keeps its planned availability, so a
    gap stays visibly a gap. A field present but valued None is recorded
    as NOT_YET_CAPTURED rather than OBSERVED -- a null is not an
    observation."""
    values = observed or {}
    fields: list[ContextField] = []
    for name, (source, planned, detail) in sorted(CONTEXT_FIELD_PLAN.items()):
        if name in values and values[name] is not None:
            availability = (
                ContextAvailability.DERIVED_PROXY
                if planned is ContextAvailability.DERIVED_PROXY
                else ContextAvailability.OBSERVED
            )
            fields.append(
                ContextField(name, values[name], availability, source, captured_at, detail)
            )
        else:
            availability = (
                planned
                if planned in (ContextAvailability.SOURCE_UNAVAILABLE,)
                else ContextAvailability.NOT_YET_CAPTURED
            )
            fields.append(ContextField(name, None, availability, source, None, detail))
    return GameContextRecord(
        game_id=game_id,
        captured_at=captured_at,
        kickoff_utc=kickoff_utc,
        capture_mode=capture_mode,
        fields=tuple(fields),
    )


@dataclass
class ContextCoverageReport:
    records: list[GameContextRecord] = field(default_factory=list)

    def coverage(self) -> dict[str, dict[str, int]]:
        """Per-field availability across every record -- what a future
        ablation can and cannot actually use."""
        out: dict[str, dict[str, int]] = {}
        for record in self.records:
            for item in record.fields:
                bucket = out.setdefault(item.name, {a.value: 0 for a in ContextAvailability})
                bucket[item.availability.value] += 1
        return dict(sorted(out.items()))

    @property
    def usable_field_names(self) -> list[str]:
        names = {f.name for r in self.records for f in r.fields if f.is_usable_evidence}
        return sorted(names)
