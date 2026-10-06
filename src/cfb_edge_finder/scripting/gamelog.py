"""The football input: one row per team per completed game. PURE.

A `TeamGame` is what one team's OFFENSE did in one game, plus the counting
stats its DEFENSE was credited with (sacks, tackles for loss, passes
defended). What a team's defense ALLOWED is never stored on its own row: it
is the opponent's offense row of the same game, so the two can never
disagree.

Rows carry COUNTS, not rates. A rate is computed only when a metric is
evaluated (`metrics.py`), so a season rate is sum(numerator)/sum(denominator)
and never a mean of per-game rates.

*** THE LEAKAGE BOUNDARY LIVES HERE ***
`before(rows, cutoff)` is the only way the engine selects history, and it is
STRICT: a game whose kickoff is at or after the cutoff is excluded. Every fit
in `adjust.py` takes its rows through it.

*** TWO TIERS ***
`box` is the CORE tier: box-score counts that ESPN publishes for virtually
every FBS game, keyless. `pbp` is the ENHANCED tier: counts derived from
play-by-play (success, explosiveness, finishing, tempo), present only when
the source published a usable play log. A row with `pbp=None` is a normal
row; it simply contributes nothing to enhanced metrics.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cfb_edge_finder.scripting import TEAM_GAME_SCHEMA_VERSION

#: CORE counts. Offense unless prefixed `def_`.
BOX_KEYS = (
    "plays",
    "total_yards",
    "rush_att",
    "rush_yards",
    "pass_att",
    "pass_cmp",
    "pass_yards",
    "first_downs",
    "third_att",
    "third_conv",
    "fourth_att",
    "fourth_conv",
    "turnovers",
    "ints_thrown",
    "fumbles_lost",
    "possession_seconds",
    "penalties",
    "penalty_yards",
    "def_sacks",
    "def_tfl",
    "def_passes_defended",
    "def_qb_hurries",
)

#: ENHANCED counts, derived from a play log. Offense perspective throughout.
PBP_KEYS = (
    "scrim_plays",
    "success_plays",
    "early_plays",
    "early_success",
    "rush_plays",
    "rush_success",
    "rush_yards",
    "pass_plays",
    "pass_success",
    "pass_yards",
    "sacks_taken",
    "explosive_rush",
    "explosive_pass",
    "explosive_yards",
    "scrim_yards",
    "drives",
    "drive_points",
    "scoring_opps",
    "opp_points",
    "drive_seconds",
    "drive_plays",
    "neutral_plays",
    "neutral_pass_plays",
    "garbage_plays_excluded",
)

SITES = ("home", "away", "neutral")

#: A game counts as history only once it has certainly finished. College
#: games run 3.5-4 hours; 4.5 hours after kickoff is a conservative "final".
FINAL_AFTER = timedelta(hours=4, minutes=30)

#: The football picture for a game is drawn as of 04:00 US/Eastern on the
#: game's local date: every game on one slate day shares the same history,
#: and a 3:30 pm game still in progress can never feed a 7:00 pm one.
SLATE_TZ = ZoneInfo("America/New_York")
SLATE_DAY_STARTS = 4
DIVISIONS = ("fbs", "fcs", "other", "unknown")


@dataclass(frozen=True)
class TeamGame:
    game_id: str
    season: int
    week: int | None
    kickoff_utc: str
    team_id: str
    team: str
    opponent_id: str
    opponent: str
    team_division: str
    opponent_division: str
    site: str
    points_for: float | None
    points_against: float | None
    box: dict[str, float | None]
    pbp: dict[str, float | None] | None
    primary_passer: str | None = None
    primary_passer_att: float | None = None
    source: str = "unknown"
    pbp_source: str | None = None
    observed_at: str | None = None
    schema_version: str = TEAM_GAME_SCHEMA_VERSION
    notes: tuple[str, ...] = field(default_factory=tuple)

    def kickoff(self) -> datetime:
        return parse_utc(self.kickoff_utc)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["notes"] = list(self.notes)
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TeamGame:
        data = dict(raw)
        data["notes"] = tuple(data.get("notes") or ())
        data["box"] = {k: _num(data.get("box", {}).get(k)) for k in BOX_KEYS}
        pbp = data.get("pbp")
        data["pbp"] = None if pbp is None else {k: _num(pbp.get(k)) for k in PBP_KEYS}
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def data_cutoff(kickoff: datetime | str) -> str:
    """04:00 US/Eastern on the game's local date, as UTC ISO-8601.

    The football data cutoff for a game. Strict: history must have FINISHED
    (kickoff + FINAL_AFTER) before this instant."""
    local = parse_utc(kickoff).astimezone(SLATE_TZ)
    start = datetime(local.year, local.month, local.day, SLATE_DAY_STARTS, tzinfo=SLATE_TZ)
    if local < start:
        start -= timedelta(days=1)
    return iso_utc(start)


def before(rows: Iterable[TeamGame], cutoff: datetime | str) -> list[TeamGame]:
    """Rows of games that had FINISHED before `cutoff`, in a deterministic order.

    The one door history comes through. A game counts only once kickoff +
    FINAL_AFTER is strictly before the cutoff, so a game in progress at the
    cutoff -- or the game being analysed -- can never shape the picture."""
    limit = parse_utc(cutoff)
    kept = [row for row in rows if row.kickoff() + FINAL_AFTER < limit]
    return sorted(kept, key=row_order)


def row_order(row: TeamGame) -> tuple[str, str, str]:
    return (row.kickoff_utc, row.game_id, row.team_id)


def pair_index(rows: Iterable[TeamGame]) -> dict[tuple[str, str], TeamGame]:
    """(game_id, team_id) -> row, for looking up an opponent's row."""
    return {(row.game_id, row.team_id): row for row in rows}


def opponent_row(row: TeamGame, index: dict[tuple[str, str], TeamGame]) -> TeamGame | None:
    return index.get((row.game_id, row.opponent_id))


# ------------------------------------------------------------- storage


def write_jsonl(path: Path, rows: Iterable[TeamGame]) -> str:
    """Write rows canonically and return the content hash.

    Sorted, key-sorted, compact: an unchanged season rewrites byte-identical
    content, so the committing workflow can skip a no-op commit."""
    ordered = sorted(rows, key=row_order)
    text = "".join(json.dumps(r.as_dict(), sort_keys=True, separators=(",", ":")) + "\n" for r in ordered)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[TeamGame]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(TeamGame.from_dict(json.loads(line)))
    return sorted(out, key=row_order)


def rows_fingerprint(rows: Iterable[TeamGame]) -> str:
    """Content hash of exactly the rows a fit consumed (order-independent).

    `observed_at` is excluded: re-fetching an unchanged box score is not a
    change in the football, and must not churn an artifact hash."""
    ordered = sorted(rows, key=row_order)
    payload = []
    for row in ordered:
        data = row.as_dict()
        data.pop("observed_at", None)
        payload.append(data)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
