"""Milestone E, mission section 7: the closed vocabulary for "what happened
when the scheduler considered capturing ONE (game, market, timing_label)
checkpoint." Every checkpoint the scheduler ever looks at resolves to
exactly one of these -- there is no silent "nothing recorded" outcome.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class CaptureState(StrEnum):
    CAPTURED = "captured"
    NOT_YET_DUE = "not_yet_due"
    MARKET_NOT_AVAILABLE = "market_not_available"
    MISSED_WINDOW = "missed_window"
    GAME_RESCHEDULED = "game_rescheduled"
    WORKFLOW_FAILURE = "workflow_failure"
    OTHER_EXPLICIT_REASON = "other_explicit_reason"


TERMINAL_CAPTURE_STATES = frozenset(
    {
        CaptureState.CAPTURED,
        CaptureState.MISSED_WINDOW,
    }
)
"""CAPTURED and MISSED_WINDOW are the only states a checkpoint stops
changing from on its own -- MARKET_NOT_AVAILABLE/GAME_RESCHEDULED/
WORKFLOW_FAILURE/OTHER_EXPLICIT_REASON can all still resolve to CAPTURED
or MISSED_WINDOW on a later scan; NOT_YET_DUE is the only pre-terminal
default."""


class CaptureStateRecord(BaseModel):
    """One append-only row: what the scheduler observed for one checkpoint
    at one scan. The checkpoint's CURRENT state is the latest row for its
    (game_id, market_ticker, timing_label) key -- callers fold the
    append-only log themselves (see research/persistence.py); this schema
    stores history, not a mutable pointer."""

    model_config = ConfigDict(frozen=True)

    game_id: str
    kalshi_market_ticker: str
    timing_label: str
    state: CaptureState
    observed_at: AwareDatetime
    detail: str = Field(default="")
    run_id: str | None = Field(
        default=None, description="CI run identifier this observation was recorded under, if any"
    )
