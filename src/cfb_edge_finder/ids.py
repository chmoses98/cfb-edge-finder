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
"""

from __future__ import annotations

import re
import unicodedata

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


def canonical_game_id(season: int, week_label: str, away_team_slug: str, home_team_slug: str) -> str:
    """Build the canonical, stable game ID.

    Format: cfb-{season}-{week_label}-{away_slug}-at-{home_slug}

    Collision policy: two source records producing the same canonical ID
    within a season is treated as a data-quality failure at ingestion time
    (fail loud), not silently overwritten or deduplicated -- see
    docs/SCHEMAS.md "Canonical game ID" section. This is expected to be rare
    (FBS teams essentially never play each other twice in the same labeled
    week) but is NOT assumed to be impossible.
    """
    if season < 1869 or season > 2100:
        raise ValueError(f"season {season!r} is out of plausible range")
    validate_week_label(week_label)
    if away_team_slug == home_team_slug:
        raise ValueError("away_team_slug and home_team_slug must differ")
    return f"cfb-{season}-{week_label}-{away_team_slug}-at-{home_team_slug}"
