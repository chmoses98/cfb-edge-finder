"""QB/passing-game continuity -- uncertainty-only, V1 (mission spec section 10).

*** WHAT DATA ACTUALLY EXISTS, LEAKAGE-SAFE, PREGAME ***
CFBD's `/player/returning` endpoint (see data/cfbd_client.py's
`fetch_returning_production` and docs/MILESTONE_C.md's data audit) is
published before the season starts and reports TEAM-LEVEL returning
production, including `percent_passing_ppa` and `passing_usage` -- the
share of last season's passing-game production (points-per-play-added,
and raw usage) that is returning this season. There is no field that
directly says "the starting QB is the same person as last year" -- this
module treats `percent_passing_ppa` as a documented PROXY for
passing-game continuity, not a QB-identity signal. A team could have
100% returning passing PPA and a new starter (a great receiving corps
returning around a new QB) or 0% and a continuing starter whose top
targets all left -- this proxy is directionally useful, not precise, and
is used ONLY to inflate uncertainty, never to shift the point estimate.

*** WHY UNCERTAINTY-ONLY IN V1 ***
Mission section 10: "do not manually hardcode star-player opinions,"
and V1 must be "conservative." Point-estimate QB adjustments (e.g. "a
new starter is worth -3 points") require either a validated
per-player-quality metric (which this project does not have a trustworthy
source for -- same principle as UNVERIFIED_PLACEHOLDER_FEE_RATE in
kalshi/executable_price.py) or enough historical seasons of transition
outcomes to fit one empirically (a real next-step improvement, not V1).
So V1's QB architecture supports being wired up with a point-estimate
adjustment later (see `QBContinuityState` and `uncertainty_multiplier`
below -- the shape is ready) without actually asserting one now.
"""

from __future__ import annotations

from enum import StrEnum


class QBContinuityState(StrEnum):
    RETURNING_STARTER = "returning_starter"
    """percent_passing_ppa above the HIGH_CONTINUITY_THRESHOLD."""
    NEW_STARTER = "new_starter"
    """percent_passing_ppa below the LOW_CONTINUITY_THRESHOLD."""
    MIXED_OR_UNCERTAIN = "mixed_or_uncertain"
    """Between the two thresholds -- genuinely ambiguous signal, not a
    missing-data case (see UNKNOWN)."""
    UNKNOWN = "unknown"
    """No returning-production data available for this team/season at
    all -- e.g. a team new to the corpus. Must inflate uncertainty at
    least as much as NEW_STARTER, never be treated as equivalent to
    RETURNING_STARTER by a missing-value default."""


HIGH_CONTINUITY_THRESHOLD = 0.70
LOW_CONTINUITY_THRESHOLD = 0.35
"""Both thresholds are round, documented, provisional choices -- not fit
against outcome data. See docs/MILESTONE_C.md "QB continuity" for why:
validating exact threshold placement needs a labeled "did the starting QB
actually change" dataset this project does not have a trustworthy source
for yet (see module docstring)."""

_UNCERTAINTY_MULTIPLIER = {
    QBContinuityState.RETURNING_STARTER: 1.00,
    QBContinuityState.MIXED_OR_UNCERTAIN: 1.10,
    QBContinuityState.NEW_STARTER: 1.20,
    QBContinuityState.UNKNOWN: 1.20,
}
"""Multiplies the team's offensive score standard deviation (see
score_model.py). 1.20 (not higher) is a deliberately conservative,
round, documented placeholder -- inflating uncertainty is meant to widen
the projection's admitted error bars, not to silently move the point
estimate via the back door of an oversized variance bump."""


def classify_continuity(percent_passing_ppa: float | None) -> QBContinuityState:
    if percent_passing_ppa is None:
        return QBContinuityState.UNKNOWN
    if percent_passing_ppa >= HIGH_CONTINUITY_THRESHOLD:
        return QBContinuityState.RETURNING_STARTER
    if percent_passing_ppa <= LOW_CONTINUITY_THRESHOLD:
        return QBContinuityState.NEW_STARTER
    return QBContinuityState.MIXED_OR_UNCERTAIN


def uncertainty_multiplier(state: QBContinuityState) -> float:
    return _UNCERTAINTY_MULTIPLIER[state]
