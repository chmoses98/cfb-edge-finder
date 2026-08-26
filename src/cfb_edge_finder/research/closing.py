"""Milestone E, Part D: the rigorous definition of "closing."

CLOSING is the last clean, executable, PREGAME quote before the market
stops being tradeable / the game starts -- never a post-kickoff price,
never a stale midpoint, never "whatever the last hourly scan happened to
see." This module never captures anything itself; it SELECTS the closing
row from among a market's already-captured `ResearchCorpusRow`s (or, live,
decides whether a just-fetched quote qualifies), and grades how good that
selection is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ClosingQuality = Literal["EXACT", "NEAR_CLOSE", "MISSED"]

EXACT_MAX_MINUTES = 10.0
"""A quote captured within 10 minutes of kickoff/market-close is the real
closing line -- tight enough that price movement in the gap is expected
to be negligible for research purposes."""

NEAR_CLOSE_MAX_MINUTES = 60.0
"""Beyond 10 but within 60 minutes: usable as an APPROXIMATE closing
reference (mission section 11's fallback window), but never presented as
exact -- always paired with its own gap-to-kickoff."""


@dataclass(frozen=True)
class ClosingCandidate:
    market_ticker: str
    captured_at: datetime
    game_status_at_capture: str
    executable_yes_price: float | None
    minutes_before_kickoff: float


@dataclass(frozen=True)
class ClosingResult:
    captured: bool
    quality: ClosingQuality
    candidate: ClosingCandidate | None
    minutes_to_kickoff: float | None
    detail: str


def classify_closing_quality(minutes_before_kickoff: float) -> ClosingQuality:
    if minutes_before_kickoff < 0:
        raise ValueError(
            f"minutes_before_kickoff must be >= 0 -- a post-kickoff quote is never eligible as closing "
            f"(got {minutes_before_kickoff!r})"
        )
    if minutes_before_kickoff <= EXACT_MAX_MINUTES:
        return "EXACT"
    if minutes_before_kickoff <= NEAR_CLOSE_MAX_MINUTES:
        return "NEAR_CLOSE"
    return "MISSED"


def select_closing_candidate(candidates: list[ClosingCandidate]) -> ClosingResult:
    """`candidates` should already be filtered to ONE market's own rows.
    Only rows captured while the game was still `"scheduled"` (never
    in_progress/final -- mission section 10's "never post-kickoff price")
    and with a real executable price are eligible at all."""
    eligible = [
        c
        for c in candidates
        if c.game_status_at_capture == "scheduled"
        and c.executable_yes_price is not None
        and c.minutes_before_kickoff >= 0
    ]
    if not eligible:
        return ClosingResult(
            captured=False,
            quality="MISSED",
            candidate=None,
            minutes_to_kickoff=None,
            detail="no eligible pregame executable-price observation found for this market",
        )
    best = min(eligible, key=lambda c: c.minutes_before_kickoff)
    quality = classify_closing_quality(best.minutes_before_kickoff)
    if quality == "MISSED":
        return ClosingResult(
            captured=False,
            quality="MISSED",
            candidate=best,
            minutes_to_kickoff=best.minutes_before_kickoff,
            detail=(
                f"nearest pregame observation was {best.minutes_before_kickoff:.1f} min before kickoff -- "
                f"beyond the {NEAR_CLOSE_MAX_MINUTES:.0f}-minute fallback window, so this is MISSED, not "
                f"approximate"
            ),
        )
    return ClosingResult(
        captured=True,
        quality=quality,
        candidate=best,
        minutes_to_kickoff=best.minutes_before_kickoff,
        detail=(
            f"{quality} closing capture, {best.minutes_before_kickoff:.1f} min before kickoff"
            if quality == "EXACT"
            else (
                f"approximate closing only -- nearest pregame observation was "
                f"{best.minutes_before_kickoff:.1f} min before kickoff (fallback window "
                f"<= {NEAR_CLOSE_MAX_MINUTES:.0f} min); not treated as exact"
            )
        ),
    )
