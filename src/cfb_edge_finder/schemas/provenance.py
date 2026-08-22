"""Provenance and model-versioning primitives.

Audit note: edge-finder-api's CANONICAL_SCHEMAS.md documents, as an
explicitly unresolved gap, that no object in that pipeline actually carries
modelVersion/calibrationVersion/pipelineRunId except its newest artifact
type. cfb-edge-finder bakes ModelVersion and DataProvenance into every
projection and snapshot record from day one specifically to avoid
retrofitting that gap later.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ModelVersion(BaseModel):
    """Identifies exactly which code/model produced a record.

    model_version is a semver-style string for the overall projection
    pipeline. Component versions are tracked separately because they can
    change independently (e.g. a ratings refresh without a pricing-engine
    change).
    """

    model_config = ConfigDict(frozen=True)

    model_version: str = Field(..., description="Semver of the overall projection pipeline, e.g. '0.1.0'")
    ratings_component_version: str | None = Field(
        default=None, description="Version of the team-ratings component used, if any exists yet"
    )
    pricing_engine_version: str = Field(..., description="Version of the distribution->market pricing math")
    git_commit_sha: str | None = Field(default=None, description="Commit SHA the pipeline ran at, if captured")


class DataProvenance(BaseModel):
    """Answers: 'what did the model know, and how fresh/confirmed was it?'

    completeness_flags is intentionally open-ended (e.g.
    {"qb_confirmed": True, "injury_report_fresh": False}) rather than a
    fixed set of booleans, because which inputs matter will evolve well
    before this schema is next revised -- see docs/SCHEMAS.md.
    """

    model_config = ConfigDict(frozen=True)

    team_ratings_version: str | None = None
    roster_snapshot_version: str | None = None
    injury_snapshot_version: str | None = None
    schedule_source: str = Field(..., description="e.g. 'cfbd', 'espn'")
    data_timestamp: datetime = Field(..., description="Timestamp of the underlying input data, not of this record")
    completeness_flags: dict[str, bool] = Field(default_factory=dict)
