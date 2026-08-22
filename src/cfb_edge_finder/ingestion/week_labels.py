"""Deterministic week/postseason semantic derivation (mission spec section 6).

Two deliberately independent outputs, matching the schema split already
established on GameRecord:

* `week_label` -- the stable, ID-safe slug used inside the canonical
  game_id (unchanged format from Milestone A: wkNN | bowl-<slug> |
  cfp-<slug> | conf-champ-<slug>). Sponsor names get slugified into this,
  which IS a known, documented risk (see docs/SCHEMAS.md) -- it is not
  solved here, just consistently constructed.
* Structured fields (`SeasonType`, `week_number`, `CFPRound`,
  `bowl_display_name`) -- machine-readable classification independent of
  the slug's exact spelling, satisfying "prefer stable semantic labels
  plus optional display names" without changing the already-tested ID
  format from Milestone A.

Classification of postseason games is heuristic (keyword matching on a
free-text descriptor from the source), because CFBD's raw schema does not
expose a first-class "is this a CFP quarterfinal" boolean as far as could
be verified this session (network egress blocked -- see
docs/DATA_SOURCES.md). This is intentionally fail-loud: an unrecognized
postseason descriptor raises rather than guesses, so an ingestion run
surfaces it for manual review instead of silently misclassifying a game.
"""

from __future__ import annotations

from dataclasses import dataclass

from cfb_edge_finder.ids import slugify_team as slugify_text  # generic normalizer, not team-specific despite the name
from cfb_edge_finder.ids import validate_week_label
from cfb_edge_finder.schemas.common import CFPRound, SeasonType


class UnclassifiablePostseasonError(ValueError):
    """Raised when a postseason game's free-text descriptor doesn't match
    any known pattern (CFP round, conference championship, or bowl).
    Fail-loud by design -- see module docstring.
    """


@dataclass(frozen=True)
class WeekMetadata:
    week_label: str
    season_type: SeasonType
    week_number: int | None
    cfp_round: CFPRound | None
    bowl_display_name: str | None


_CFP_ROUND_KEYWORDS: tuple[tuple[str, CFPRound], ...] = (
    ("national championship", CFPRound.NATIONAL_CHAMPIONSHIP),
    ("semifinal", CFPRound.SEMIFINAL),
    ("quarterfinal", CFPRound.QUARTERFINAL),
    ("first round", CFPRound.FIRST_ROUND),
)

_KNOWN_CONFERENCES_FOR_CHAMPIONSHIP = (
    "sec", "big ten", "acc", "big 12", "american", "mountain west",
    "conference usa", "sun belt", "mac", "pac-12",
)


def derive_week_metadata(
    *, season_type_raw: str, week_raw: int | str | None, postseason_descriptor: str | None = None
) -> WeekMetadata:
    """season_type_raw: source's own season-type string, e.g. 'regular' or
    'postseason' (CFBD's convention). week_raw: source's own week number
    (regular season). postseason_descriptor: free-text game name/notes
    field, REQUIRED for postseason games, used to classify CFP round vs
    conference championship vs bowl.
    """
    normalized_season_type = season_type_raw.strip().lower()

    if normalized_season_type == "regular":
        if week_raw is None:
            raise ValueError("regular-season game is missing a week number")
        try:
            week_number = int(week_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"regular-season week_raw {week_raw!r} is not a valid integer") from exc
        if week_number < 0 or week_number > 20:
            raise ValueError(f"regular-season week_number {week_number!r} is out of plausible range")
        label = f"wk{week_number:02d}"
        validate_week_label(label)
        return WeekMetadata(
            week_label=label,
            season_type=SeasonType.REGULAR,
            week_number=week_number,
            cfp_round=None,
            bowl_display_name=None,
        )

    if normalized_season_type != "postseason":
        raise ValueError(f"unrecognized season_type_raw {season_type_raw!r}; expected 'regular' or 'postseason'")

    if not postseason_descriptor or not postseason_descriptor.strip():
        raise UnclassifiablePostseasonError("postseason game has no descriptor to classify it from")

    descriptor = postseason_descriptor.strip()
    lowered = descriptor.lower()

    for keyword, cfp_round in _CFP_ROUND_KEYWORDS:
        if keyword in lowered:
            # Strip generic CFP/round boilerplate so the slug carries only
            # the distinguishing remainder (e.g. "Orange Bowl" out of "CFP
            # Quarterfinal - Orange Bowl"), rather than re-encoding "cfp"
            # and the round name a second time inside the slug itself.
            remainder = lowered
            for boilerplate in (keyword, "college football playoff", "cfp", "-", "playoff"):
                remainder = remainder.replace(boilerplate, " ")
            remainder = remainder.strip()
            round_slug = cfp_round.value.replace("_", "-")
            label = f"cfp-{round_slug}" if not remainder else f"cfp-{round_slug}-{slugify_text(remainder)}"
            validate_week_label(label)
            return WeekMetadata(
                week_label=label,
                season_type=SeasonType.CFP,
                week_number=None,
                cfp_round=cfp_round,
                bowl_display_name=None,
            )

    if "championship" in lowered:
        for conference in _KNOWN_CONFERENCES_FOR_CHAMPIONSHIP:
            if conference in lowered:
                label = f"conf-champ-{slugify_text(conference)}"
                validate_week_label(label)
                return WeekMetadata(
                    week_label=label,
                    season_type=SeasonType.CONFERENCE_CHAMPIONSHIP,
                    week_number=None,
                    cfp_round=None,
                    bowl_display_name=None,
                )
        raise UnclassifiablePostseasonError(
            f"descriptor {descriptor!r} contains 'championship' but no known conference name was matched"
        )

    if "bowl" in lowered:
        label = f"bowl-{slugify_text(descriptor)}"
        validate_week_label(label)
        return WeekMetadata(
            week_label=label,
            season_type=SeasonType.BOWL,
            week_number=None,
            cfp_round=None,
            bowl_display_name=descriptor,
        )

    raise UnclassifiablePostseasonError(
        f"descriptor {descriptor!r} did not match any known CFP/championship/bowl pattern"
    )
