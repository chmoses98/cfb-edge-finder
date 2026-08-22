"""Raw CFBD (or ESPN) game row -> canonical GameRecord.

STATUS: raw field names assumed here were checked against CFBD's own
officially-generated client library documentation on GitHub this session
(github.com was reachable even though CFBD's own domains were not -- see
docs/MILESTONE_B.md for the exact pages fetched and what they returned).
That is real, primary-source schema documentation, but it is still NOT a
live API payload -- two different official client repos (cfb.js and
cfbd-python) disagreed with each other on some field names (e.g.
`homeDivision` vs `home_classification`), most likely because one repo is
staler than the other. Where the two disagreed, this module trusts the
more complete/current-seeming source (cfbd-python, which also documents
the `playoff` field cfb.js's docs don't have at all) but stays DEFENSIVE
by checking multiple candidate keys rather than asserting one as fact --
see `_first_present` below. This is still not a substitute for live
verification.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cfb_edge_finder.ids import canonical_game_id
from cfb_edge_finder.ingestion.team_matching import resolve_team_id_for_game
from cfb_edge_finder.ingestion.week_labels import derive_week_metadata
from cfb_edge_finder.schemas.game import GameRecord, GameStatus

CFBD_SOURCE_NAME = "cfbd"

# Candidate keys for a postseason game's human-readable descriptor, tried in
# order -- used only as the fallback classifier when no structured
# `playoff` object is present (see week_labels.py).
_POSTSEASON_DESCRIPTOR_KEYS = ("notes", "name", "gameName")

# Candidate keys for each field where this session's two independent
# schema sources disagreed (see module docstring) -- tried in order,
# first present wins. Genuinely uncertain without a live payload.
_HOME_CLASSIFICATION_KEYS = ("homeClassification", "homeDivision", "home_classification")
_AWAY_CLASSIFICATION_KEYS = ("awayClassification", "awayDivision", "away_classification")
_START_TIME_TBD_KEYS = ("startTimeTBD", "startTimeTbd", "start_time_tbd")


def _first_present(raw: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in raw:
            return raw[key]
    return default


def home_classification(raw: dict[str, Any]) -> str | None:
    return _first_present(raw, _HOME_CLASSIFICATION_KEYS)


def away_classification(raw: dict[str, Any]) -> str | None:
    return _first_present(raw, _AWAY_CLASSIFICATION_KEYS)

_STATUS_MAP = {
    "scheduled": "scheduled",
    "in_progress": "in_progress",
    "inprogress": "in_progress",
    "live": "in_progress",
    "final": "final",
    "completed": "final",
    "postponed": "postponed",
    "canceled": "canceled",
    "cancelled": "canceled",
}


class GameNormalizationError(ValueError):
    """Wraps any failure while normalizing one raw game row, with enough
    context (source, raw id) to include in an ingestion summary without
    aborting the whole batch -- see scripts/ingest_schedule.py.
    """

    def __init__(self, source: str, raw_game_id: str, cause: Exception):
        self.source = source
        self.raw_game_id = raw_game_id
        self.cause = cause
        super().__init__(f"failed to normalize {source} game {raw_game_id!r}: {cause}")


def _parse_kickoff(raw_start_date: str | None, start_time_tbd: bool) -> datetime | None:
    if start_time_tbd or not raw_start_date:
        return None
    text = raw_start_date.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        # CFBD's documented convention is UTC; a naive timestamp here means
        # the source omitted an offset, which we do not silently assume is
        # UTC without saying so -- see docs/MILESTONE_B.md known limitations.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _map_status(raw: dict[str, Any]) -> GameStatus:
    raw_status = raw.get("status")
    if isinstance(raw_status, str) and raw_status.strip().lower() in _STATUS_MAP:
        return _STATUS_MAP[raw_status.strip().lower()]  # type: ignore[return-value]
    if raw.get("completed") is True:
        return "final"
    return "scheduled"


def _find_postseason_descriptor(raw: dict[str, Any]) -> str | None:
    for key in _POSTSEASON_DESCRIPTOR_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def normalize_cfbd_game(raw: dict[str, Any], *, observed_at: datetime) -> GameRecord:
    """raw: one element of CFBDClient.fetch_games()'s response.
    observed_at: when this ingestion run pulled the data (our own
    bookkeeping timestamp, distinct from the source's own data).
    """
    raw_game_id = str(raw.get("id", "<missing-id>"))
    try:
        season = int(raw["season"])
        home_team_id = resolve_team_id_for_game(raw["homeTeam"], CFBD_SOURCE_NAME, home_classification(raw))
        away_team_id = resolve_team_id_for_game(raw["awayTeam"], CFBD_SOURCE_NAME, away_classification(raw))
        neutral_site = bool(raw.get("neutralSite", False))

        week_meta = derive_week_metadata(
            season_type_raw=raw["seasonType"],
            week_raw=raw.get("week"),
            postseason_descriptor=_find_postseason_descriptor(raw),
            playoff=raw.get("playoff"),
        )

        game_id = canonical_game_id(
            season, week_meta.week_label, away_team_id, home_team_id, neutral_site=neutral_site
        )

        raw_start_date = raw.get("startDate")
        start_time_tbd = bool(_first_present(raw, _START_TIME_TBD_KEYS, False))
        kickoff_utc = _parse_kickoff(raw_start_date, start_time_tbd)

        return GameRecord(
            game_id=game_id,
            season=season,
            week_label=week_meta.week_label,
            season_type=week_meta.season_type,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_team_name=raw["homeTeam"],
            away_team_name=raw["awayTeam"],
            neutral_site=neutral_site,
            kickoff_utc=kickoff_utc,
            venue=raw.get("venue"),
            source_game_ids={CFBD_SOURCE_NAME: raw_game_id},
            status=_map_status(raw),
            week_number=week_meta.week_number,
            cfp_round=week_meta.cfp_round,
            bowl_display_name=week_meta.bowl_display_name,
            kickoff_source_raw=raw_start_date,
            primary_source=CFBD_SOURCE_NAME,
            discovered_at=observed_at,
            last_updated_at=observed_at,
        )
    except GameNormalizationError:
        raise
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: any failure here must be attributed, not crash the batch
        raise GameNormalizationError(CFBD_SOURCE_NAME, raw_game_id, exc) from exc
