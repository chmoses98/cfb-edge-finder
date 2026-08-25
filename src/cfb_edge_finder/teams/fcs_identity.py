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
    conference, and anything ratings-relevant) -- identity only.

    Milestone D closure: a live root-cause audit of the
    AMBIGUOUS_TEAM_MAPPING population (GH Actions run 32886794099) found
    that Kalshi's live rules_primary text abbreviates "<X> State" as
    "<X> St." for FCS programs too, exactly like the FBS side (see
    teams/registry.py's `_generate_state_abbreviation_aliases`) -- e.g.
    real live sightings of "Weber St.", "Jackson St.", "Tennessee St.",
    "Indiana St.", "Portland St.", "Youngstown St.", "Idaho St.",
    "Alabama St.", "Murray St." never matched CFBD's own full-word school
    names ("Weber State", etc.), so genuine FCS-vs-FCS/FBS-vs-FCS markets
    involving these programs fell through to an unexplained
    AMBIGUOUS_TEAM_MAPPING instead of the FCS identity check ever seeing
    them. This set now includes BOTH the full CFBD name and its
    deterministic "St."-abbreviated form for every FCS school ending in
    " state" -- same exact-match-only transformation as the FBS side,
    never a fuzzy match."""
    names: set[str] = set()
    for team in cfbd_teams:
        if str(team.get("classification", "")).casefold() != "fcs" or not team.get("school"):
            continue
        normalized = normalize_school_name(team["school"])
        names.add(normalized)
        if normalized.endswith(" state"):
            names.add(normalized[: -len(" state")] + " st.")
    return frozenset(names)


def is_known_fcs_school(raw_name: str | None, fcs_school_names: frozenset[str]) -> bool:
    """Exact match only (post-normalization) -- a name this set doesn't
    contain verbatim is never guessed as FCS; the caller's existing
    failure classification is unaffected."""
    if not raw_name:
        return False
    return normalize_school_name(raw_name) in fcs_school_names
