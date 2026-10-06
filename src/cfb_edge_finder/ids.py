"""Canonical ID construction for games and teams.

Design (see docs/SCHEMAS.md for rationale):

* Team IDs are normalized slugs, not raw source names, because school names
  vary across data sources ("Ohio State" vs "Ohio St." vs "OSU").
* The canonical game ID is built ONLY from stable, pre-kickoff-immutable
  inputs: season, a week label, and the two team slugs. Kickoff time is
  deliberately excluded because it moves (flex scheduling, weather delays)
  and a stable ID must not change when that happens.
* Vendor-assigned game IDs (e.g. a CFBD numeric id) are never used as the
  canonical ID itself -- they are stored separately as
  `GameRecord.source_game_ids` so the system is not locked to one vendor's
  ID scheme, but cross-referencing remains possible.
* For neutral-site games, "home"/"away" is bookkeeping only and different
  vendors are known to disagree about which team they label home (see
  audit note on `canonical_game_id` below) -- the ID is therefore built
  from alphabetically-sorted team slugs for neutral-site games specifically,
  so it cannot silently fork into two different IDs for the same physical
  game depending on which vendor's designation was ingested first.

Known, deliberately-not-"fixed"-here risk (documented, not a code bug):
bowl game slugs are frequently sponsor-branded ("bowl-duke-s-mayo" this
year, a different sponsor next year, or even mid-season naming-rights
changes). Two vendors could plausibly report different sponsor names for
the same physical bowl within one season, which would fork the canonical
ID the same way the home/away disagreement above would. This needs a
stable, non-sponsor bowl-identity mapping (e.g. keyed by host city/stadium)
in Milestone B's ingestion layer, not a change to this module -- this
module correctly builds an ID from whatever week_label it's given; the
risk lives in how that week_label gets resolved from a raw vendor payload,
which is out of scope until real ingestion exists.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WEEK_LABEL_RE = re.compile(
    r"^(wk\d{2}|bowl-[a-z0-9-]+|cfp-[a-z0-9-]+|conf-champ-[a-z0-9-]+|allstar-[a-z0-9-]+)$"
)


def slugify_team(name: str) -> str:
    """Normalize a team/school name into a stable, source-independent slug.

    >>> slugify_team("Ohio State")
    'ohio-state'
    >>> slugify_team("Texas A&M")
    'texas-a-m'
    """
    if not name or not name.strip():
        raise ValueError("team name must be non-empty")
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("-", normalized.lower()).strip("-")
    if not slug:
        raise ValueError(f"team name {name!r} normalized to an empty slug")
    return slug


def validate_week_label(week_label: str) -> str:
    """Validate a week label against the closed vocabulary.

    Regular season: 'wk01'..'wk15'.
    Postseason: 'conf-champ-<conference-slug>', 'bowl-<bowl-slug>',
    'cfp-<round-slug>' (e.g. 'cfp-quarterfinal', 'cfp-national-championship').
    """
    if not _WEEK_LABEL_RE.match(week_label):
        raise ValueError(
            f"week_label {week_label!r} does not match the canonical vocabulary "
            f"(wkNN | bowl-<slug> | cfp-<slug> | conf-champ-<slug> | allstar-<slug>)"
        )
    return week_label


def canonical_game_id(
    season: int,
    week_label: str,
    away_team_slug: str,
    home_team_slug: str,
    neutral_site: bool = False,
) -> str:
    """Build the canonical, stable game ID.

    Format (site-based games): cfb-{season}-{week_label}-{away_slug}-at-{home_slug}
    Format (neutral-site games): cfb-{season}-{week_label}-{team_a}-vs-{team_b}
    (team_a/team_b alphabetically sorted)

    Audit note (neutral-site vendor disagreement, addressed): for a genuine
    neutral-site game, "home"/"away" is a bookkeeping designation with no
    physical meaning, and vendors are known to disagree about which team
    they call home for exactly these games (e.g. Kickoff Classic / Ireland /
    Australia games). If the ID were built from away-at-home order the way
    site-based games are, ingesting the same physical game from two vendors
    that disagree on the designation would silently produce two different
    canonical IDs for one game -- a real collision-safety failure. Sorting
    the two team slugs alphabetically for neutral_site=True makes the ID
    invariant to that disagreement: it depends only on the *set* of two
    teams, never on which one a particular vendor happened to call home.
    True home-field-advantage modeling still lives entirely in
    `GameRecord.home_team_id`/`away_team_id` and `neutral_site` -- this only
    changes how the identity string is built, not which team is "true home"
    for rating purposes (see `cfb_edge_finder.ratings.home_field_advantage_points`).

    Collision policy: two source records producing the same canonical ID
    within a season is treated as a data-quality failure at ingestion time
    (fail loud), not silently overwritten or deduplicated -- see
    docs/SCHEMAS.md "Canonical game ID" section, and
    `assert_unique_game_ids` below for a ready-made check. This is expected
    to be rare (FBS teams essentially never play each other twice in the
    same labeled week) but is NOT assumed to be impossible.
    """
    if season < 1869 or season > 2100:
        raise ValueError(f"season {season!r} is out of plausible range")
    validate_week_label(week_label)
    if away_team_slug == home_team_slug:
        raise ValueError("away_team_slug and home_team_slug must differ")
    if neutral_site:
        team_a, team_b = sorted((away_team_slug, home_team_slug))
        return f"cfb-{season}-{week_label}-{team_a}-vs-{team_b}"
    return f"cfb-{season}-{week_label}-{away_team_slug}-at-{home_team_slug}"


def assert_unique_game_ids(game_ids: Iterable[str]) -> None:
    """Fail loud if any canonical game_id appears more than once.

    This is the collision-safety proof the "collision policy" docstring
    above promises: canonical_game_id() itself is a pure, stateless
    function with no knowledge of other games, so uniqueness across a real
    ingested set has to be checked by the caller (a future Milestone B
    ingestion step) against the IDs it actually produced. Mirrors
    `cfb_edge_finder.kalshi.coverage_ledger.CoverageLedger.assert_no_missing`'s
    fail-loud-on-violation pattern for the market side.
    """
    counts = Counter(game_ids)
    duplicates = {game_id: n for game_id, n in counts.items() if n > 1}
    if duplicates:
        raise ValueError(f"duplicate canonical game_id(s) detected: {duplicates}")
