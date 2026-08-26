"""Milestone E, mission section 3: deterministic snapshot identity.

`observation_key` is a pure function of (season, game_id, market_ticker,
timing_label, model_version, capture_window_version) -- never a random
UUID. Two calls with the same inputs always produce the same key, so
retries, concurrent runs, manual reruns, and scheduler overlap all
naturally collapse to "the same logical observation" without any
coordination between callers. `KalshiResearchObservation.snapshot_id`
(Milestone D, a UUID) is untouched and still identifies one CAPTURE EVENT
(a whole sweep); this key identifies one LOGICAL CHECKPOINT within it.
"""

from __future__ import annotations

import hashlib

CAPTURE_WINDOW_VERSION = "capture_window_v1"
"""Bumped only when research.timing's bucket window definitions change in
a way that would alter which checkpoint a given elapsed-time maps to.
Baked into the key so a redefinition can never collide with, or be
silently mistaken for, an observation captured under the old semantics."""


def observation_key(
    *,
    season: int,
    game_id: str,
    market_ticker: str,
    timing_label: str,
    model_version: str,
    capture_window_version: str = CAPTURE_WINDOW_VERSION,
) -> str:
    """Deterministic dedup key -- a new `model_version` produces a
    genuinely NEW key (mission section 6: model-version changes create a
    distinct research observation, never an in-place correction of the
    original), while identical inputs always collapse to the identical
    key regardless of when/how many times they're computed."""
    canonical = "|".join(
        [str(season), game_id, market_ticker, timing_label, model_version, capture_window_version]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def settlement_key(*, game_id: str, market_ticker: str) -> str:
    """Settlement is per-market, not per-snapshot (a market has exactly
    one final outcome) -- a separate, simpler deterministic key."""
    canonical = "|".join(["settlement", game_id, market_ticker])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
