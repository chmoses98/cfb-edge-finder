"""Milestone E, Part A: the durable, season-long corpus row.

`KalshiResearchObservation` (Milestone D, schemas/kalshi_observation.py)
already carries every football/pricing field a captured observation needs.
What it deliberately does NOT carry is anything about DURABLE STORAGE
identity -- a deterministic dedup key, which capture-window semantics
version produced it, or whether it came from the live scheduler versus a
retrospective backfill. Rather than overload that frozen, already-tested
schema with storage concerns outside its own stated purpose (the same
"additive, not overloaded" precedent that schema's own docstring already
follows for ProspectiveSnapshot), `ResearchCorpusRow` wraps ONE
`KalshiResearchObservation` with exactly those fields -- this is the unit
`research/persistence.py`'s durable store actually reads/writes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from cfb_edge_finder.schemas.data_versions import DataVersionManifest
from cfb_edge_finder.schemas.kalshi_observation import KalshiResearchObservation

CORPUS_SCHEMA_VERSION = "research_corpus_v1"

CaptureMode = Literal["PROSPECTIVE", "RETROSPECTIVE_BACKFILL"]
"""PROSPECTIVE: captured by the live scheduler before/at the checkpoint it
claims (mission section 26's "old observations remain tied to old
version" case -- this codebase's scheduler only ever stamps PROSPECTIVE,
see research/timing.py's scan entry point). RETROSPECTIVE_BACKFILL: any
future backfill/replay tool re-deriving history under a newer model
version -- mechanically distinct so a report can never conflate the two
and call backfilled research "prospective" by accident."""


class ResearchCorpusRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    observation_key: str = Field(
        ..., description="Deterministic dedup identity -- see research.identity.observation_key(). Never a random UUID."
    )
    schema_version: str = Field(default=CORPUS_SCHEMA_VERSION)
    capture_window_version: str = Field(
        ..., description="Which timing-bucket window definitions were in effect when this key was derived."
    )
    capture_mode: CaptureMode = "PROSPECTIVE"
    season: int
    kickoff_utc_at_capture: AwareDatetime | None = Field(
        default=None,
        description="The game's kickoff_utc as known AT capture time -- see closing.py/reschedule handling.",
    )
    game_status_at_capture: str = Field(
        ..., description="GameRecord.status at capture time -- stale-guard evidence."
    )
    schedule_source_timestamp: AwareDatetime | None = Field(
        default=None,
        description="Freshness of the schedule data used for the stale-schedule guard (mission section 9).",
    )
    data_versions: DataVersionManifest
    observation: KalshiResearchObservation
    run_id: str | None = Field(default=None, description="CI workflow run identifier that captured this row, if any.")
