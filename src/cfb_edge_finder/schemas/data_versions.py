"""Milestone E, mission section 27: one manifest recording every version
identifier a durable research row depends on, so no schema drift is ever
unlabeled.

Deliberately a plain, flat record (not references into other tables) --
mirrors ModelVersion/DataProvenance (schemas/provenance.py) in embedding
values directly so a row stays self-describing even years later.
"""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict


class DataVersionManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_version: str
    feature_version: str
    cfbd_capture_timestamp: AwareDatetime | None = None
    kalshi_capture_timestamp: AwareDatetime | None = None
    mapping_version: str
    fee_schedule_version: str | None = None
    settlement_version: str | None = None
    snapshot_schema_version: str
