"""Raw CFBD (or ESPN) game row -> canonical GameRecord.

STATUS: exact raw field names assumed here (`homeTeam`, `neutralSite`,
`startDate`, etc.) follow CFBD's well-documented v2 schema but were not
independently live-verified this session -- see cfbd_client.py's module
docstring. `normalize_cfbd_game` is defensive about field-name uncertainty
where the schema is genuinely unclear (e.g. which key carries a bowl's
display name) rather than asserting one exact key as fact.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cfb_edge_finder.ids import canonical_game_id
from cfb_edge_finder.ingestion.team_matching import resolve_team_id
from cfb_edge_finder.ingestion.week_labels import derive_week_metadata
from cfb_edge_finder.schemas.game import GameRecord, GameStatus

CFBD_SOURCE_NAME = "cfbd"

# Candidate keys for a postseason game's human-readable descriptor, tried in
# order -- CFBD's exact field name for this was not independently verified
# this session (see module docstring).
_POSTSEASON_DESCRIPTOR_KEYS = ("notes", "name", "gameName")

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
        home_team_id = resolve_team_id(raw["homeTeam"], CFBD_SOURCE_NAME)
        away_team_id = resolve_team_id(raw["awayTeam"], CFBD_SOURCE_NAME)
        neutral_site = bool(raw.get("neutralSite", False))

        week_meta = derive_week_metadata(
            season_type_raw=raw["seasonType"],
            week_raw=raw.get("week"),
            postseason_descriptor=_find_postseason_descriptor(raw),
        )

        game_id = canonical_game_id(
            season, week_meta.week_label, away_team_id, home_team_id, neutral_site=neutral_site
        )

        raw_start_date = raw.get("startDate")
        kickoff_utc = _parse_kickoff(raw_start_date, bool(raw.get("startTimeTBD", False)))

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
