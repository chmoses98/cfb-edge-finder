"""Canonical FBS team registry and alias resolution.

*** DATA PROVENANCE WARNING (read before trusting `REGISTRY`) ***
The team list and conference assignments below are populated from general
knowledge, not a live CFBD fetch -- this session's network egress to
CFBD/ESPN was blocked (see docs/DATA_SOURCES.md and docs/MILESTONE_B.md).
They are a best-effort seed, NOT independently verified against CFBD
itself for the 2026 season. Conference realignment has been unusually
active in recent years; the Pac-12 membership list in particular is
flagged as high-uncertainty below.

One specific gap WAS found and corrected via genuine (non-CFBD) research:
web search against multiple independent, dated sources confirmed CFBD
currently reports 138 FBS teams for 2026, not the 134 originally seeded
here -- see `_transitional_seed` below for the four FCS-to-FBS additions
(Delaware, Missouri State, North Dakota State, Sacramento State) that
close that gap. This was cross-checked against several independent
outlets, not a single source, but it is still not a CFBD API response and
should be reconciled against a live `/teams/fbs` fetch when one becomes
possible.
`vendor_ids` is deliberately left EMPTY for every team in this seed --
fabricating specific numeric CFBD/ESPN team IDs from memory would be
exactly the kind of unverified-but-authoritative-looking data this project
explicitly refuses to produce (see kalshi/executable_price.py for the same
principle applied to fee rates). Vendor IDs get populated by
`cfb_edge_finder.ingestion.team_matching` as real ingestion runs observe
them, not hardcoded here.

Design (mission spec section 4):

* `team_id` is a normalized slug (`cfb_edge_finder.ids.slugify_team`), never
  a vendor ID -- vendor IDs are tracked in `TeamRecord.vendor_ids`, mirroring
  `GameRecord.source_game_ids`'s pattern for the same reason.
* Alias resolution is EXACT STRING MATCH ONLY. No fuzzy/similarity matching
  exists anywhere in this module, on purpose: a fuzzy matcher can silently
  resolve two different schools to the same team_id and nobody would notice
  until money was on the line. `resolve_team_alias()` raises loudly on
  anything it doesn't recognize with total confidence.
* Genuinely ambiguous short names (e.g. bare "Miami", which could mean
  Miami (FL) or Miami (OH)) are listed in `AMBIGUOUS_ALIASES` and MUST
  raise `AmbiguousTeamAliasError` rather than silently guessing one of them.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from cfb_edge_finder.ids import slugify_team


class Subdivision(StrEnum):
    FBS = "fbs"
    FCS = "fcs"
    """FCS opponents show up in FBS teams' non-conference schedules but are
    NOT populated in this registry's seed data -- see get_team()'s handling
    of an unresolved opponent."""


class TeamRecord(BaseModel):
    model_config = {"frozen": True}

    team_id: str = Field(..., description="Canonical slug, e.g. 'ohio-state' -- never a vendor ID")
    display_name: str
    conference: str | None = Field(
        default=None,
        description="Best-effort as of training data, NOT live-verified this session -- see module docstring",
    )
    subdivision: Subdivision = Subdivision.FBS
    primary_vendor: str | None = Field(
        default=None, description="Which key in vendor_ids should be treated as authoritative, once populated"
    )
    vendor_ids: dict[str, str] = Field(
        default_factory=dict, description="{'cfbd': '...'} etc. -- deliberately empty in the seed, see module docstring"
    )
    active: bool = True
    season_start: int | None = Field(
        default=None, description="First season this team_id/branding is valid for, if renamed"
    )
    season_end: int | None = Field(
        default=None, description="Last season this team_id/branding is valid for, if since renamed"
    )


class UnknownTeamAliasError(KeyError):
    """Raised when an alias string is not recognized at all -- neither a
    known unambiguous alias nor a known ambiguous one. This is the ordinary
    "we've never seen this string" case, distinct from AmbiguousTeamAliasError.
    """


class AmbiguousTeamAliasError(ValueError):
    """Raised when an alias string is a KNOWN short form that maps to more
    than one team (e.g. bare "Miami"). Never silently resolved -- the
    caller must supply a qualified form or resolve it manually.
    """

    def __init__(self, alias: str, candidates: list[str]):
        self.alias = alias
        self.candidates = candidates
        super().__init__(f"{alias!r} is ambiguous between {candidates!r}; supply a qualified alias instead")


# --- Seed registry -----------------------------------------------------
# Grouped by conference for readability. See module docstring for the
# provenance caveat. `_seed` entries are (display_name, conference).

_seed: tuple[tuple[str, str], ...] = (
    # SEC
    ("Alabama", "SEC"), ("Arkansas", "SEC"), ("Auburn", "SEC"), ("Florida", "SEC"),
    ("Georgia", "SEC"), ("Kentucky", "SEC"), ("LSU", "SEC"), ("Mississippi State", "SEC"),
    ("Missouri", "SEC"), ("Oklahoma", "SEC"), ("Ole Miss", "SEC"), ("South Carolina", "SEC"),
    ("Tennessee", "SEC"), ("Texas", "SEC"), ("Texas A&M", "SEC"), ("Vanderbilt", "SEC"),
    # Big Ten
    ("Illinois", "Big Ten"), ("Indiana", "Big Ten"), ("Iowa", "Big Ten"), ("Maryland", "Big Ten"),
    ("Michigan", "Big Ten"), ("Michigan State", "Big Ten"), ("Minnesota", "Big Ten"), ("Nebraska", "Big Ten"),
    ("Northwestern", "Big Ten"), ("Ohio State", "Big Ten"), ("Oregon", "Big Ten"), ("Penn State", "Big Ten"),
    ("Purdue", "Big Ten"), ("Rutgers", "Big Ten"), ("UCLA", "Big Ten"), ("USC", "Big Ten"),
    ("Washington", "Big Ten"), ("Wisconsin", "Big Ten"),
    # ACC
    ("Boston College", "ACC"), ("California", "ACC"), ("Clemson", "ACC"), ("Duke", "ACC"),
    ("Florida State", "ACC"), ("Georgia Tech", "ACC"), ("Louisville", "ACC"), ("Miami (FL)", "ACC"),
    ("NC State", "ACC"), ("North Carolina", "ACC"), ("Pittsburgh", "ACC"), ("SMU", "ACC"),
    ("Stanford", "ACC"), ("Syracuse", "ACC"), ("Virginia", "ACC"), ("Virginia Tech", "ACC"),
    ("Wake Forest", "ACC"),
    # Big 12
    ("Arizona", "Big 12"), ("Arizona State", "Big 12"), ("Baylor", "Big 12"), ("BYU", "Big 12"),
    ("Cincinnati", "Big 12"), ("Colorado", "Big 12"), ("Houston", "Big 12"), ("Iowa State", "Big 12"),
    ("Kansas", "Big 12"), ("Kansas State", "Big 12"), ("Oklahoma State", "Big 12"), ("TCU", "Big 12"),
    ("Texas Tech", "Big 12"), ("UCF", "Big 12"), ("Utah", "Big 12"), ("West Virginia", "Big 12"),
    # Pac-12 (rebuilt) -- HIGH UNCERTAINTY, mid-transition as of training data; verify live.
    ("Oregon State", "Pac-12"), ("Washington State", "Pac-12"), ("Boise State", "Pac-12"),
    ("Colorado State", "Pac-12"), ("Fresno State", "Pac-12"), ("San Diego State", "Pac-12"),
    ("Utah State", "Pac-12"),
    # American (AAC)
    ("Army", "American"), ("Charlotte", "American"), ("East Carolina", "American"),
    ("Florida Atlantic", "American"), ("Memphis", "American"), ("Navy", "American"),
    ("North Texas", "American"), ("Rice", "American"), ("South Florida", "American"),
    ("Temple", "American"), ("Tulane", "American"), ("Tulsa", "American"),
    ("UAB", "American"), ("UTSA", "American"),
    # Mountain West
    ("Air Force", "Mountain West"), ("Hawaii", "Mountain West"), ("Nevada", "Mountain West"),
    ("New Mexico", "Mountain West"), ("San Jose State", "Mountain West"), ("UNLV", "Mountain West"),
    ("Wyoming", "Mountain West"),
    # Conference USA
    ("FIU", "Conference USA"), ("Jacksonville State", "Conference USA"), ("Kennesaw State", "Conference USA"),
    ("Liberty", "Conference USA"), ("Louisiana Tech", "Conference USA"), ("Middle Tennessee", "Conference USA"),
    ("New Mexico State", "Conference USA"), ("Sam Houston", "Conference USA"), ("UTEP", "Conference USA"),
    ("Western Kentucky", "Conference USA"),
    # Sun Belt
    ("Appalachian State", "Sun Belt"), ("Arkansas State", "Sun Belt"), ("Coastal Carolina", "Sun Belt"),
    ("Georgia Southern", "Sun Belt"), ("Georgia State", "Sun Belt"), ("James Madison", "Sun Belt"),
    ("Louisiana", "Sun Belt"), ("Marshall", "Sun Belt"), ("Old Dominion", "Sun Belt"),
    ("South Alabama", "Sun Belt"), ("Southern Miss", "Sun Belt"), ("Texas State", "Sun Belt"),
    ("Troy", "Sun Belt"), ("Louisiana-Monroe", "Sun Belt"),
    # MAC
    ("Akron", "MAC"), ("Ball State", "MAC"), ("Bowling Green", "MAC"), ("Buffalo", "MAC"),
    ("Central Michigan", "MAC"), ("Eastern Michigan", "MAC"), ("Kent State", "MAC"), ("Miami (OH)", "MAC"),
    ("Northern Illinois", "MAC"), ("Ohio", "MAC"), ("Toledo", "MAC"), ("Western Michigan", "MAC"),
    # Independents
    ("Notre Dame", "FBS Independents"), ("UConn", "FBS Independents"), ("UMass", "FBS Independents"),
)

# FCS-to-FBS transitional additions, verified via web search against
# multiple independent sources (Deseret News, CBS Sports, ESPN, Wikipedia)
# during the Milestone B validation follow-up -- NOT a live CFBD fetch, but
# real, cross-checked, dated reporting rather than a from-memory guess.
# These four are exactly what closed the gap between this registry's
# original 134-team count and CFBD's currently-reported 138 FBS teams for
# the 2026 season. `season_start` reflects each program's first season
# playing a full FBS schedule as reported by those sources.
_transitional_seed: tuple[tuple[str, str, int], ...] = (
    ("Delaware", "Conference USA", 2025),
    ("Missouri State", "Conference USA", 2025),
    ("North Dakota State", "Mountain West", 2026),
    ("Sacramento State", "MAC", 2026),
)

REGISTRY: tuple[TeamRecord, ...] = tuple(
    TeamRecord(team_id=slugify_team(display_name), display_name=display_name, conference=conference)
    for display_name, conference in _seed
) + tuple(
    TeamRecord(
        team_id=slugify_team(display_name), display_name=display_name, conference=conference, season_start=season_start
    )
    for display_name, conference, season_start in _transitional_seed
)

_BY_ID: dict[str, TeamRecord] = {team.team_id: team for team in REGISTRY}

if len(_BY_ID) != len(REGISTRY):
    raise RuntimeError("duplicate team_id in the seed registry -- two display names slugified to the same id")


def get_team(team_id: str) -> TeamRecord | None:
    return _BY_ID.get(team_id)


# --- Aliases -------------------------------------------------------------
# Exact-match only. Every value here must be a team_id present in REGISTRY
# (checked below at import time -- a typo fails loudly at startup, not at
# first use).

ALIASES: dict[str, str] = {
    # Unambiguous shorthand / branding / legacy names
    "Miami (FL)": "miami-fl",
    "Miami Hurricanes": "miami-fl",
    "Miami (OH)": "miami-oh",
    "Miami RedHawks": "miami-oh",
    "Ole Miss": "ole-miss",
    "Mississippi": "ole-miss",
    "Louisiana": "louisiana",
    "Louisiana-Lafayette": "louisiana",
    "UL Lafayette": "louisiana",
    "Louisiana Ragin Cajuns": "louisiana",
    "Louisiana-Monroe": "louisiana-monroe",
    "UL Monroe": "louisiana-monroe",
    "ULM": "louisiana-monroe",
    "UConn": "uconn",
    "Connecticut": "uconn",
    "Hawaii": "hawaii",
    "Hawai'i": "hawaii",
    "Hawaiʻi": "hawaii",  # okina character some sources use
    "UTSA": "utsa",
    "UT San Antonio": "utsa",
    "Texas-San Antonio": "utsa",
    "UCF": "ucf",
    "Central Florida": "ucf",
    "USC": "usc",
    "Southern California": "usc",
    "South Carolina": "south-carolina",
    "NC State": "nc-state",
    "North Carolina State": "nc-state",
    "Pitt": "pittsburgh",
    "Pittsburgh": "pittsburgh",
    "Texas A&M": "texas-a-m",
    "Texas A and M": "texas-a-m",
    "Western Kentucky": "western-kentucky",
    "WKU": "western-kentucky",
    "Western Michigan": "western-michigan",
    "WMU": "western-michigan",
    "UMass": "umass",
    "Massachusetts": "umass",
    "San Jose State": "san-jose-state",
    "SJSU": "san-jose-state",
}

# Genuinely ambiguous short forms -- MUST fail loud, never silently resolved.
AMBIGUOUS_ALIASES: dict[str, list[str]] = {
    "Miami": ["miami-fl", "miami-oh"],
}


def _validate_aliases() -> None:
    for alias, team_id in ALIASES.items():
        if team_id not in _BY_ID:
            raise RuntimeError(f"alias {alias!r} points at unknown team_id {team_id!r} -- fix the seed data")
    for alias, candidates in AMBIGUOUS_ALIASES.items():
        for team_id in candidates:
            if team_id not in _BY_ID:
                raise RuntimeError(f"ambiguous alias {alias!r} lists unknown team_id {team_id!r} -- fix the seed data")
        overlap = set(ALIASES) & set(AMBIGUOUS_ALIASES)
        if overlap:
            raise RuntimeError(f"alias(es) {overlap!r} listed as both unambiguous and ambiguous -- fix the seed data")


_validate_aliases()


def resolve_team_alias(raw_name: str) -> str:
    """Resolve a raw, vendor-reported team name string to a canonical
    team_id. Exact string match only (see module docstring for why).

    Resolution order:
    1. Exact match against a REGISTRY display_name -> that team's team_id.
    2. Exact match against ALIASES -> the mapped team_id.
    3. Exact match against AMBIGUOUS_ALIASES -> raises AmbiguousTeamAliasError.
    4. Otherwise -> raises UnknownTeamAliasError.
    """
    for team in REGISTRY:
        if team.display_name == raw_name:
            return team.team_id
    if raw_name in ALIASES:
        return ALIASES[raw_name]
    if raw_name in AMBIGUOUS_ALIASES:
        raise AmbiguousTeamAliasError(raw_name, AMBIGUOUS_ALIASES[raw_name])
    raise UnknownTeamAliasError(raw_name)
