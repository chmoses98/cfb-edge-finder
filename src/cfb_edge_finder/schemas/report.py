"""Milestone E, Part I: weekly and season-cumulative research report shapes.

Research-only by construction: no field here is a stake size, a bet
recommendation, or an order payload -- see
tests/test_qualification_hard_disabled.py, which scans this module too.
"""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

REPORT_SCHEMA_VERSION = "research_report_v1"


class TimingBucketCoverage(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    captured: int = 0
    missed_window: int = 0
    not_yet_due: int = 0
    other: int = 0


class GapBucketStat(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket: str
    contract_level_n: int = 0
    game_level_n: int = 0
    settled_n: int = 0
    settlement_hit_rate: float | None = None
    avg_closing_price_movement: float | None = None
    avg_fee_adjusted_research_gap: float | None = None


class WeeklyResearchReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=REPORT_SCHEMA_VERSION)
    season: int
    week_label: str
    generated_at: AwareDatetime

    games_captured: int = 0
    contracts_captured: int = 0
    timing_bucket_coverage: list[TimingBucketCoverage] = Field(default_factory=list)
    family_coverage: dict[str, int] = Field(default_factory=dict)
    missing_windows: int = 0
    mapping_errors: int = 0
    gap_bucket_distribution: list[GapBucketStat] = Field(default_factory=list)
    closing_capture_exact: int = 0
    closing_capture_near: int = 0
    closing_capture_missed: int = 0
    settled_observations: int = 0
    settlement_calibration_note: str = "no qualification/recommendation logic evaluated -- research counts only"
    avg_research_clv: float | None = None
    sample_size_note: str = (
        "counts are contract-level; see game_level_n/family_level_n in gap_bucket_distribution for "
        "correlation-aware denominators"
    )


class SeasonCumulativeReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=REPORT_SCHEMA_VERSION)
    season: int
    generated_at: AwareDatetime
    report_version: int = Field(..., description="Monotonically increasing version of this cumulative report.")

    total_observations: int = 0
    settled_observations: int = 0
    family_counts: dict[str, int] = Field(default_factory=dict)
    timing_bucket_completeness: dict[str, float] = Field(default_factory=dict)
    gap_bucket_distribution: list[GapBucketStat] = Field(default_factory=list)
    model_version_history: list[str] = Field(default_factory=list)
    weeks_included: list[str] = Field(default_factory=list)
