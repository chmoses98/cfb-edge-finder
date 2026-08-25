"""Milestone D hardening: minimal, deterministic FCS-team IDENTITY check.

*** WHY THIS IS NOT AN FCS REGISTRY OR MODEL ***
`teams.registry` is FBS-only by design -- Milestone D's predictive model
is, and remains, FBS-focused, and this hardening pass is explicitly
forbidden from expanding that ("Do not ingest/model the entire FCS
statistical universe. Do not build an FCS projection engine."). This
module answers exactly one narrow question -- "is this raw team name a
real, current FCS program, per CFBD's own /teams data" -- so a genuine
FCS-vs-FCS Kalshi market (both sides fail `teams.registry.resolve_team_alias`
because neither is an FBS program) can be classified as a distinct,
understood, unsupported population (`cfb_coverage_reason.FCS_VS_FCS`)
instead of collapsing into the same bucket as a genuinely unresolvable
market. It builds no aliases, no fuzzy matching, no ratings, no schedule
-- only an exact-match (case/whitespace-insensitive) name set, mirroring
`teams.registry`'s own no-fuzzy-matching philosophy (see that module's
docstring and `game_mapping.py`'s "WHY NO FUZZY MATCHING").

Source data: `CFBDClient.fetch_all_division_teams()` (GET /teams --
covers FBS AND FCS, unlike `fetch_teams()`'s FBS-only GET /teams/fbs;
see that client method's own docstring)."""

from __future__ import annotations


def normalize_school_name(name: str) -> str:
    """Whitespace-collapsing, case-insensitive normalization -- exact
    match only, never a fuzzy/similarity comparison."""
    return " ".join(name.split()).casefold()


def build_fcs_school_name_set(cfbd_teams: list[dict]) -> frozenset[str]:
    """`cfbd_teams`: raw dicts from `CFBDClient.fetch_all_division_teams()`.
    Keeps only `classification == "fcs"` school names, exact-match
    normalized. Deliberately ignores every other field (mascot,
    conference, and anything ratings-relevant) -- identity only."""
    return frozenset(
        normalize_school_name(team["school"])
        for team in cfbd_teams
        if str(team.get("classification", "")).casefold() == "fcs" and team.get("school")
    )


def is_known_fcs_school(raw_name: str | None, fcs_school_names: frozenset[str]) -> bool:
    """Exact match only (post-normalization) -- a name this set doesn't
    contain verbatim is never guessed as FCS; the caller's existing
    failure classification is unaffected."""
    if not raw_name:
        return False
    return normalize_school_name(raw_name) in fcs_school_names
