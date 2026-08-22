"""Baseline team-rating system: opponent-adjusted offense/defense, QB value,
roster continuity, coaching/system adjustments, home-field advantage.

The rating system itself is not implemented in this foundation phase (see
docs/ROADMAP.md Milestone C). This module exists so the package layout is
stable for downstream imports and so Milestone C has an obvious home rather
than needing a restructure.

One safety invariant IS enforced here now, ahead of Milestone C, rather
than left to convention: `home_field_advantage_points` is the single
choke point any future rating code must route a home-field adjustment
through, so a neutral-site game can never silently receive ordinary
home-field advantage just because `GameRecord.home_team_id` is still
populated (it always is, even at a neutral site -- see
`cfb_edge_finder.schemas.game.GameRecord`).
"""

from __future__ import annotations


def home_field_advantage_points(base_hfa: float, neutral_site: bool) -> float:
    """Return the home-field-advantage point adjustment to apply to the
    home team's projected mean score.

    Returns 0.0 unconditionally when neutral_site is True, regardless of
    base_hfa -- a neutral-site game must never receive ordinary home-field
    advantage. `base_hfa` itself (the actual point value to use for a real
    home game) is a Milestone C research question, not decided here.
    """
    if neutral_site:
        return 0.0
    return base_hfa
