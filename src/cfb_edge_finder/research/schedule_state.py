"""FRESH SCHEDULE STATE -- the fast-moving half of the football state,
split out so it can age independently of the slow model half.

*** THE PROBLEM THIS SOLVES (live incident, 2026-09-03) ***
`research/football_state.py` bundles two things with completely
different clocks into ONE artifact and ONE freshness verdict:

  SLOW  completed-game history, advanced stats, team classifications --
        the model's inputs. They change roughly weekly.
  FRESH kickoff time and game status -- the only facts that decide
        whether a capture is deadline-safe. They change on the order of
        hours, and a wrong one is a data-integrity failure.

Because they share `FootballState.freshness()`, the SLOW half was held
hostage to the FRESH half's 6-hour bound: when the CFBD quota hit zero
on 2026-09-03 the schedule component aged past six hours, the whole
artifact went `FOOTBALL_STATE_STALE_HARD`, `resolve_football_state`
returned `state=None`, and every 5-minute run fail-closed with exit 1 --
even though nothing about the model's inputs (fetched the same day) had
gone stale, and the games' kickoffs were freely readable from a keyless
public source.

*** THE SPLIT ***
This module owns a SECOND durable artifact,
`data/research/schedule_state/{season}.json`, holding one
`GameScheduleFact` per game: kickoff, status, provider, provider event
id, and the timestamp at which THAT fact was retrieved. It is not a
replacement for the football-state artifact -- identity (which teams,
which week, which canonical `game_id`), classification, FCS identity and
all model history stay CFBD-derived and durable, exactly as before.

The freshness that matters downstream is therefore now PER GAME: a
game's effective `schedule_source_timestamp` is the timestamp of the
freshest evidence for THAT game, which is the ESPN fact when one exists
and the CFBD artifact's `schedule_fetched_at` otherwise. Every existing
guard is applied unchanged against that value -- in particular
`scan_logic.guard_capture_allowed`'s 6-hour bound, which is neither
widened nor bypassed here. A game with no fresh evidence is rejected by
that same guard, alone, instead of taking the whole run down with it.

*** WHAT ESPN IS AND IS NOT ALLOWED TO CHANGE ***
ALLOWED : `kickoff_utc` and `status` -- the two facts that move.
FORBIDDEN: identity. `canonical_game_id` is a function of season, week
label, both team ids and the neutral-site flag, so letting a fallback
source rewrite any of those would mint a NEW game_id and orphan every
row already captured against the old one. Home/away orientation and
neutral site are therefore used only as MATCH CONDITIONS -- they must
agree, or the game is refused -- never as updates.

*** FAIL-CLOSED MATCHING (mission section D) ***
Matching reuses `research/result_provider.py`'s already-live-verified
ESPN parsing (`parse_espn_event`) and its strict identity matcher
(`match_espn_event`): exact-match canonical team resolution only, both
teams must resolve, season must agree, exactly one candidate event may
match, and a flipped orientation is an explicit refusal rather than a
silent correction. Every refusal is recorded with its reason in
`ScheduleState.rejections` and leaves the game on its CFBD facts.

*** RESCHEDULE SAFETY (mission section E) ***
A changed kickoff never rewrites history. The old kickoff is preserved
in the emitted `scan_logic.ScheduleChangeRecord` alongside the new one,
the provider and detection time are recorded, and nothing re-labels or
re-writes an already-captured row: observation keys are a function of
season/game_id/ticker/label/model_version, not of kickoff, so re-running
`resolve_due_labels` against the NEW kickoff simply schedules whatever is
still uncaptured. The dangerous direction -- a game moved EARLIER -- is
exactly the one this fixes: under a stale schedule the old, later kickoff
would keep `resolve_due_labels` happily emitting "pregame" labels for a
game that had already started, and the clock guard could not see it
because it compares against that same stale kickoff.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cfb_edge_finder.data.espn_schedule_client import EspnScheduleClient, ScoreboardFetch
from cfb_edge_finder.ids import slugify_team
from cfb_edge_finder.research.result_provider import parse_espn_event
from cfb_edge_finder.research.scan_logic import (
    RESCHEDULE_THRESHOLD_MINUTES,
    ScheduleChangeRecord,
    detect_reschedule,
)
from cfb_edge_finder.schemas.game import GameRecord
from cfb_edge_finder.teams.registry import (
    AmbiguousTeamAliasError,
    UnknownTeamAliasError,
    get_team,
    resolve_team_alias,
)

SCHEDULE_STATE_SCHEMA_VERSION = "schedule_state_v1"
SCHEDULE_STATE_SUBDIR = "schedule_state"
"""Under data/research/ so git_durable_store's staging allowlist covers
it unchanged, exactly like football_state and cfbd_access."""

PROVIDER_ESPN = "espn"
PROVIDER_CFBD = "cfbd"

NEAR_HORIZON_HOURS = 30.0
"""A game inside this window is deadline-relevant: its date bucket is
refetched on EVERY run. 30h rather than 24h so the whole T_24H window
(18h-30h before kickoff) is always covered by same-run evidence."""

DEEP_HORIZON_HOURS = 8 * 24.0 + 12.0
"""How far ahead schedule facts are maintained at all. T_7D's window
opens 192h (8d) before kickoff -- the earliest moment any numeric
checkpoint can be due -- so anything nearer than this can legitimately
owe a capture. The extra 12h is slack for scheduler drift."""

FAR_BUCKET_REFRESH_MINUTES = 30.0
"""Date buckets outside NEAR_HORIZON_HOURS are refetched at most this
often. A game five days out cannot reach a deadline in 30 minutes, and
30-minute-old evidence is an order of magnitude inside the 6h capture
bound it will be judged against. This is what keeps a 5-minute loop from
making ten keyless requests every five minutes."""

LOOKBACK_HOURS = 24.0
"""Recently-started games stay in view so a status transition
(scheduled -> in_progress/final) is OBSERVED rather than inferred from a
clock."""

MAX_RESCHEDULE_SHIFT_HOURS = 168.0
"""A single matched event may move a kickoff by at most a week. Two FBS
teams can legitimately meet twice in one season (regular season, then a
conference championship); `match_espn_event` already refuses when two
events match the same pair, but this is the second, independent bound so
no single mis-seasoned event can teleport a kickoff. Beyond it the game
is refused and left on its CFBD facts, with the reason recorded."""

SCHEDULE_STATE_FRESH = "SCHEDULE_STATE_FRESH"
SCHEDULE_STATE_PARTIAL = "SCHEDULE_STATE_PARTIAL"
SCHEDULE_STATE_UNAVAILABLE = "SCHEDULE_STATE_UNAVAILABLE"

# ESPN status.type.name -> our GameStatus literal. Live-verified
# vocabulary on 2026-09-03 carried STATUS_SCHEDULED and STATUS_FINAL;
# the others are ESPN's documented siblings. Anything NOT in this map is
# refused rather than guessed -- an unrecognized status is exactly the
# case where guessing "scheduled" could mint a pregame row for a game
# that is not pregame.
_ESPN_STATUS_TO_GAME_STATUS = {
    "STATUS_SCHEDULED": "scheduled",
    "STATUS_IN_PROGRESS": "in_progress",
    "STATUS_HALFTIME": "in_progress",
    "STATUS_END_PERIOD": "in_progress",
    "STATUS_FIRST_HALF": "in_progress",
    "STATUS_SECOND_HALF": "in_progress",
    "STATUS_FINAL": "final",
    "STATUS_FINAL_OVERTIME": "final",
    "STATUS_POSTPONED": "postponed",
    "STATUS_CANCELED": "canceled",
    "STATUS_CANCELLED": "canceled",
    "STATUS_FORFEIT": "canceled",
}


@dataclass(frozen=True)
class GameScheduleFact:
    """One game's fresh schedule facts and where they came from."""

    game_id: str
    kickoff_utc: datetime | None
    status: str
    provider: str
    provider_event_id: str | None
    fetched_at: datetime
    espn_status_name: str | None = None
    neutral_site: bool | None = None
    """MATCH EVIDENCE ONLY -- never written back onto the GameRecord, see
    the module docstring on canonical game_id stability."""

    def as_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "kickoff_utc": self.kickoff_utc.isoformat() if self.kickoff_utc else None,
            "status": self.status,
            "provider": self.provider,
            "provider_event_id": self.provider_event_id,
            "fetched_at": self.fetched_at.isoformat(),
            "espn_status_name": self.espn_status_name,
            "neutral_site": self.neutral_site,
        }

    @staticmethod
    def from_dict(raw: dict) -> GameScheduleFact | None:
        try:
            kickoff_raw = raw.get("kickoff_utc")
            kickoff = datetime.fromisoformat(kickoff_raw) if kickoff_raw else None
            if kickoff is not None and kickoff.tzinfo is None:
                return None
            fetched_at = datetime.fromisoformat(raw["fetched_at"])
            if fetched_at.tzinfo is None:
                return None
            return GameScheduleFact(
                game_id=str(raw["game_id"]),
                kickoff_utc=kickoff,
                status=str(raw["status"]),
                provider=str(raw["provider"]),
                provider_event_id=raw.get("provider_event_id"),
                fetched_at=fetched_at,
                espn_status_name=raw.get("espn_status_name"),
                neutral_site=raw.get("neutral_site"),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def age_hours(self, now: datetime) -> float:
        return (now - self.fetched_at).total_seconds() / 3600.0


@dataclass(frozen=True)
class ScheduleState:
    """The durable fresh-schedule artifact."""

    season: int
    facts: dict[str, GameScheduleFact] = field(default_factory=dict)
    bucket_fetched_at: dict[str, datetime] = field(default_factory=dict)
    schema_version: str = SCHEDULE_STATE_SCHEMA_VERSION

    def payload_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "season": self.season,
            "facts": {gid: fact.as_dict() for gid, fact in sorted(self.facts.items())},
            "bucket_fetched_at": {b: ts.isoformat() for b, ts in sorted(self.bucket_fetched_at.items())},
        }


@dataclass
class ScheduleRefreshOutcome:
    """What one fresh-schedule attempt did, for telemetry/heartbeat and
    for the operational-state classifier."""

    state: ScheduleState
    provider: str
    verdict: str
    fetches: list[ScoreboardFetch] = field(default_factory=list)
    rejections: dict[str, str] = field(default_factory=dict)
    changes: list[ScheduleChangeRecord] = field(default_factory=list)
    refreshed_games: int = 0
    buckets_attempted: int = 0
    buckets_ok: int = 0
    error: str | None = None

    def summary_dict(self) -> dict:
        return {
            "schedule_provider": self.provider,
            "schedule_state_verdict": self.verdict,
            "schedule_buckets_attempted": self.buckets_attempted,
            "schedule_buckets_ok": self.buckets_ok,
            "schedule_games_refreshed": self.refreshed_games,
            "schedule_games_rejected": len(self.rejections),
            "schedule_changes_detected": len(self.changes),
            "schedule_hosts": sorted({f.host for f in self.fetches if f.ok}),
            "schedule_error": self.error,
        }


# --------------------------------------------------------------- persistence


def _paths(repo_dir: Path, season: int) -> tuple[Path, Path]:
    base = repo_dir / "data" / "research" / SCHEDULE_STATE_SUBDIR
    return base / f"{season}.json", base / f"{season}.manifest.json"


def save_schedule_state(repo_dir: Path, state: ScheduleState, *, now: datetime) -> None:
    payload_path, manifest_path = _paths(repo_dir, state.season)
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_bytes = json.dumps(state.payload_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    sha = hashlib.sha256(payload_bytes).hexdigest()
    if not payload_path.exists() or hashlib.sha256(payload_path.read_bytes()).hexdigest() != sha:
        payload_path.write_bytes(payload_bytes)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": state.schema_version,
                "season": state.season,
                "n_facts": len(state.facts),
                "written_at": now.isoformat(),
                "payload_sha256": sha,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def load_schedule_state(repo_dir: Path, season: int) -> ScheduleState:
    """A missing or unusable artifact is an EMPTY state, never an error:
    fresh schedule facts are an enhancement layered over the CFBD ones,
    so their absence must degrade to 'no fresh facts' (and therefore to
    the existing guards) rather than to a failure."""
    payload_path, manifest_path = _paths(repo_dir, season)
    if not payload_path.exists() or not manifest_path.exists():
        return ScheduleState(season=season)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload_bytes = payload_path.read_bytes()
        if hashlib.sha256(payload_bytes).hexdigest() != manifest.get("payload_sha256"):
            return ScheduleState(season=season)
        payload = json.loads(payload_bytes)
        if payload.get("schema_version") != SCHEDULE_STATE_SCHEMA_VERSION:
            return ScheduleState(season=season)
        facts: dict[str, GameScheduleFact] = {}
        for gid, raw in (payload.get("facts") or {}).items():
            fact = GameScheduleFact.from_dict(raw) if isinstance(raw, dict) else None
            if fact is not None:
                facts[str(gid)] = fact
        buckets: dict[str, datetime] = {}
        for bucket, raw_ts in (payload.get("bucket_fetched_at") or {}).items():
            try:
                parsed = datetime.fromisoformat(str(raw_ts))
            except ValueError:
                continue
            if parsed.tzinfo is not None:
                buckets[str(bucket)] = parsed
        return ScheduleState(season=int(payload["season"]), facts=facts, bucket_fetched_at=buckets)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        return ScheduleState(season=season)


# ------------------------------------------------------------------ buckets


def required_buckets(games: list[GameRecord], *, now: datetime) -> tuple[list[str], list[str]]:
    """(near_buckets, far_buckets) -- the `YYYYMMDD` date buckets whose
    games are inside the maintained horizon, split by urgency.

    Buckets are derived from the games themselves rather than from a
    contiguous calendar range so an off-week costs no requests at all.
    Each kickoff contributes its own UTC date AND the day before, because
    ESPN buckets by US LOCAL date, which never runs ahead of UTC (the
    same rule `result_provider.scoreboard_dates_for` established live)."""
    near: set[str] = set()
    far: set[str] = set()
    lo = now - timedelta(hours=LOOKBACK_HOURS)
    hi = now + timedelta(hours=DEEP_HORIZON_HOURS)
    near_edge = now + timedelta(hours=NEAR_HORIZON_HOURS)
    for game in games:
        kickoff = game.kickoff_utc
        if kickoff is None or kickoff < lo or kickoff > hi:
            continue
        day = kickoff.astimezone(UTC).date()
        target = near if kickoff <= near_edge else far
        target.add(day.strftime("%Y%m%d"))
        target.add((day - timedelta(days=1)).strftime("%Y%m%d"))
    far -= near
    return sorted(near), sorted(far)


def _bucket_is_stale(state: ScheduleState, bucket: str, *, now: datetime) -> bool:
    last = state.bucket_fetched_at.get(bucket)
    if last is None:
        return True
    return (now - last).total_seconds() / 60.0 >= FAR_BUCKET_REFRESH_MINUTES


# ------------------------------------------------------------------ parsing


def espn_status_for(event) -> tuple[str | None, str | None]:
    """(GameStatus, espn_status_name). An unrecognized ESPN status name
    yields (None, name) -- the caller refuses the fact rather than
    guessing a status that decides whether a pregame row may be written."""
    name = event.status_name
    if not isinstance(name, str) or not name:
        return None, None
    return _ESPN_STATUS_TO_GAME_STATUS.get(name), name


# ------------------------------------------------------------------ identity


@dataclass(frozen=True)
class EspnScheduleEvent:
    """One scoreboard event reduced to what a SCHEDULE refresh needs, with
    the raw team names retained.

    `result_provider.parse_espn_event` is reused for the date/status/shape
    work it already does live-verified, but its `EspnEventFacts` discards
    the team NAMES once registry resolution fails -- and for settlement
    that is exactly right, because a settlement row records a score and a
    wrong identity there corrupts a settled result. A schedule refresh has
    a weaker obligation (it only moves a kickoff and a status) and a
    stronger prior (it is matching against a specific CFBD game whose own
    team ids are already canonical), so it keeps the names and applies the
    rule in `_side_matches`."""

    facts: object
    home_name: str | None
    away_name: str | None

    @property
    def event_id(self) -> str | None:
        return self.facts.event_id

    @property
    def event_date(self):
        return self.facts.event_date

    @property
    def season_year(self) -> int | None:
        return self.facts.season_year


def parse_schedule_event(raw: dict) -> EspnScheduleEvent:
    facts = parse_espn_event(raw)
    comp = (raw.get("competitions") or [{}])[0]
    sides = {c.get("homeAway"): c for c in (comp.get("competitors") or []) if isinstance(c, dict)}

    def _name(side: str) -> str | None:
        team = (sides.get(side) or {}).get("team") or {}
        for key in ("location", "displayName"):
            value = team.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    return EspnScheduleEvent(facts=facts, home_name=_name("home"), away_name=_name("away"))


def _side_matches(espn_name: str | None, cfbd_team_id: str) -> bool:
    """Does this ESPN team name denote the same program as this canonical
    CFBD team id? EXACT matching only, by the two canonicalizations the
    production code already uses -- never similarity.

    1. `resolve_team_alias` (literal exact match against the registry's
       display names and curated aliases). Required for any program the
       registry knows, i.e. every FBS team.
    2. Failing that, `slugify_team` -- but ONLY when the CFBD side is
       itself not an FBS program. This is not a loosening invented here:
       `ingestion/team_matching.resolve_team_id_for_game` assigns exactly
       this slug to a non-FBS opponent, because the registry curates FBS
       programs only and an FCS opponent will never appear in it "by
       design, not by omission". Applying the identical function to the
       other vendor's own name for the same team is the same exact match
       from the other side.

    An AMBIGUOUS alias never matches, mirroring
    `resolve_team_id_for_game`'s rule that ambiguity is an identity risk
    independent of subdivision.

    Why this matters: without rule 2 the fallback covered 46% of
    FBS-participant games (live run 33791551291) -- every FBS-vs-FCS game
    was dropped because its FCS opponent could not be resolved, and those
    are a large share of an early-season slate."""
    if not espn_name:
        return False
    try:
        return resolve_team_alias(espn_name) == cfbd_team_id
    except AmbiguousTeamAliasError:
        return False
    except UnknownTeamAliasError:
        pass
    if get_team(cfbd_team_id) is not None:
        # A program the registry DOES curate must resolve properly; a slug
        # match for a known FBS team would mean the registry and the feed
        # disagree, which is a signal, not a shortcut.
        return False
    try:
        return slugify_team(espn_name) == cfbd_team_id
    except ValueError:
        return False


def match_schedule_event(
    game: GameRecord, events: list[EspnScheduleEvent], *, max_shift_hours: float = MAX_RESCHEDULE_SHIFT_HOURS
) -> tuple[EspnScheduleEvent | None, str | None]:
    """Exactly one event whose (home, away) denote this game's canonical
    pair, in this orientation, in this season. Anything else refuses with
    an explicit reason.

    Same fail-closed shape as `result_provider.match_espn_event`: more than
    one candidate is AMBIGUOUS, a flipped orientation is an explicit
    refusal rather than a silent correction, and an implausible kickoff
    distance is refused. Two FBS teams meeting twice in a season produce
    two candidates and are therefore refused, which is the intended
    conservative answer."""
    candidates = [
        e
        for e in events
        if e.event_date is not None and (e.season_year is None or e.season_year == game.season)
    ]
    exact = [
        e
        for e in candidates
        if _side_matches(e.home_name, game.home_team_id) and _side_matches(e.away_name, game.away_team_id)
    ]
    if len(exact) > 1:
        ids = sorted({e.event_id or "?" for e in exact})
        return None, (
            f"ambiguous: {len(exact)} ESPN events match home={game.home_team_id} "
            f"away={game.away_team_id} (event ids {ids})"
        )
    if not exact:
        flipped = [
            e
            for e in candidates
            if _side_matches(e.home_name, game.away_team_id) and _side_matches(e.away_name, game.home_team_id)
        ]
        if flipped:
            return None, (
                f"orientation mismatch: ESPN reports {flipped[0].home_name!r} as HOME where the durable "
                f"schedule has {game.home_team_id} -- refusing to update either orientation"
            )
        return None, f"no ESPN event matched home={game.home_team_id} away={game.away_team_id}"

    event = exact[0]
    if game.kickoff_utc is not None:
        shift_hours = abs((event.event_date - game.kickoff_utc).total_seconds()) / 3600.0
        if shift_hours > max_shift_hours:
            return None, (
                f"kickoff shift {shift_hours:.1f}h exceeds the {max_shift_hours:.0f}h bound "
                f"(ESPN {event.event_date.isoformat()} vs durable {game.kickoff_utc.isoformat()})"
            )
    return event, None


# ------------------------------------------------------------------ refresh


def refresh_schedule_state(
    repo_dir: Path,
    games: list[GameRecord],
    *,
    season: int,
    now: datetime,
    client: EspnScheduleClient | None = None,
    force_all_buckets: bool = False,
) -> ScheduleRefreshOutcome:
    """Fetch ESPN scoreboards for the buckets that need it and fold the
    results into the durable schedule-state artifact.

    Never raises for a provider problem. A total ESPN outage yields
    `SCHEDULE_STATE_UNAVAILABLE` with whatever previously-stored facts
    remain (each carrying its own honest, older `fetched_at`, so the
    existing 6h guard ages them out on its own) -- nothing is invented
    and no timestamp is ever refreshed without a real retrieval."""
    client = client or EspnScheduleClient()
    prior = load_schedule_state(repo_dir, season)
    near, far = required_buckets(games, now=now)
    to_fetch = list(near) + [b for b in far if force_all_buckets or _bucket_is_stale(prior, b, now=now)]

    outcome = ScheduleRefreshOutcome(
        state=prior, provider=PROVIDER_ESPN, verdict=SCHEDULE_STATE_UNAVAILABLE, buckets_attempted=len(to_fetch)
    )
    if not to_fetch:
        # Nothing inside the horizon: not an outage, just an empty slate.
        outcome.verdict = SCHEDULE_STATE_FRESH if not near and not far else SCHEDULE_STATE_PARTIAL
        return outcome

    # *** DEDUPE BY EVENT ID -- NOT OPTIONAL ***
    # Every kickoff contributes BOTH its own UTC date bucket and the day
    # before (ESPN buckets by US local date), so consecutive buckets
    # overlap and the same event is returned more than once. Feeding those
    # duplicates to `match_espn_event` makes every single game look
    # AMBIGUOUS -- two events matching the same (home, away) -- and the
    # fallback would fail closed on the entire slate while reporting
    # perfect provider health. Caught by
    # test_moved_earlier_past_kickoff_stops_pregame_capture.
    events_by_id: dict[str, dict] = {}
    unidentified: list[dict] = []
    fetched_buckets: list[str] = []
    for bucket in to_fetch:
        fetch = client.fetch_scoreboard(bucket)
        outcome.fetches.append(fetch)
        if not fetch.ok:
            continue
        fetched_buckets.append(bucket)
        for raw in fetch.events:
            event_id = raw.get("id")
            if event_id is None:
                unidentified.append(raw)
            else:
                events_by_id.setdefault(str(event_id), raw)
    events = list(events_by_id.values()) + unidentified
    outcome.buckets_ok = len(fetched_buckets)

    if not fetched_buckets:
        errors = sorted({f.error or f"HTTP {f.http_status}" for f in outcome.fetches})
        outcome.error = "; ".join(errors)[:400]
        outcome.verdict = SCHEDULE_STATE_UNAVAILABLE
        return outcome

    parsed = [parse_schedule_event(e) for e in events]

    facts = dict(prior.facts)
    buckets_ts = dict(prior.bucket_fetched_at)
    for bucket in fetched_buckets:
        buckets_ts[bucket] = now

    fetched_bucket_set = set(fetched_buckets)
    for game in games:
        kickoff = game.kickoff_utc
        if kickoff is None:
            continue
        # Only reason about games whose bucket we actually just fetched:
        # otherwise a game simply keeps whatever fact it already had.
        day = kickoff.astimezone(UTC).date()
        own_buckets = {day.strftime("%Y%m%d"), (day - timedelta(days=1)).strftime("%Y%m%d")}
        if not (own_buckets & fetched_bucket_set):
            continue

        event, reason = match_schedule_event(game, parsed)
        if event is None:
            outcome.rejections[game.game_id] = reason or "no ESPN event matched"
            continue

        status, espn_status_name = espn_status_for(event.facts)
        if status is None:
            outcome.rejections[game.game_id] = (
                f"unrecognized ESPN status name {espn_status_name!r} -- refusing to infer a game status"
            )
            continue
        if event.event_date is None:
            outcome.rejections[game.game_id] = "ESPN event carried no parsable date"
            continue
        if game.status != "scheduled" and status == "scheduled":
            # A fresher source claiming a finished game is upcoming is
            # not a refresh, it is a contradiction. Refuse rather than
            # resurrect a completed game into a capturable one.
            outcome.rejections[game.game_id] = (
                f"contradiction: durable schedule says status={game.status!r} but ESPN says scheduled"
            )
            continue
        if detect_reschedule(game.kickoff_utc, event.event_date, threshold_minutes=RESCHEDULE_THRESHOLD_MINUTES):
            outcome.changes.append(
                ScheduleChangeRecord(
                    game_id=game.game_id,
                    previous_kickoff_utc=game.kickoff_utc,
                    new_kickoff_utc=event.event_date,
                    detected_at=now,
                )
            )
        facts[game.game_id] = GameScheduleFact(
            game_id=game.game_id,
            kickoff_utc=event.event_date,
            status=status,
            provider=PROVIDER_ESPN,
            provider_event_id=event.event_id,
            fetched_at=now,
            espn_status_name=espn_status_name,
            neutral_site=None,
        )
        outcome.refreshed_games += 1

    outcome.state = ScheduleState(season=season, facts=facts, bucket_fetched_at=buckets_ts)
    outcome.verdict = SCHEDULE_STATE_FRESH if len(fetched_buckets) == len(to_fetch) else SCHEDULE_STATE_PARTIAL
    if outcome.rejections:
        outcome.error = f"{len(outcome.rejections)} game(s) refused fail-closed"
    return outcome


# ------------------------------------------------------------------- apply


@dataclass(frozen=True)
class AppliedSchedule:
    """Games carrying the freshest schedule facts available, plus the
    per-game evidence timestamp every downstream guard is judged against."""

    games: list[GameRecord]
    schedule_source_timestamps: dict[str, datetime]
    fresh_game_ids: frozenset[str]
    changes: tuple[ScheduleChangeRecord, ...] = ()

    def timestamp_for(self, game_id: str, default: datetime) -> datetime:
        return self.schedule_source_timestamps.get(game_id, default)


def apply_schedule_state(
    games: list[GameRecord],
    state: ScheduleState,
    *,
    cfbd_schedule_fetched_at: datetime,
    now: datetime,
    max_fact_age_hours: float,
) -> AppliedSchedule:
    """Overlay fresh kickoff/status onto the CFBD-derived games.

    Only `kickoff_utc` and `status` are ever taken from the fact, and only
    while the fact itself is inside `max_fact_age_hours` -- an ESPN fact
    is not permanently fresh either, it just has its own honest clock.
    Identity fields are untouched by construction (see the module
    docstring): a fact can never change which game this is."""
    updated: list[GameRecord] = []
    timestamps: dict[str, datetime] = {}
    fresh: set[str] = set()
    for game in games:
        fact = state.facts.get(game.game_id)
        if fact is None or fact.age_hours(now) > max_fact_age_hours:
            timestamps[game.game_id] = cfbd_schedule_fetched_at
            updated.append(game)
            continue
        timestamps[game.game_id] = fact.fetched_at
        fresh.add(game.game_id)
        if fact.kickoff_utc == game.kickoff_utc and fact.status == game.status:
            updated.append(game)
            continue
        updated.append(
            game.model_copy(
                update={
                    "kickoff_utc": fact.kickoff_utc,
                    "status": fact.status,
                    "primary_source": game.primary_source,
                    "last_updated_at": now,
                }
            )
        )
    return AppliedSchedule(
        games=updated,
        schedule_source_timestamps=timestamps,
        fresh_game_ids=frozenset(fresh),
    )


def kickoffs_within_horizon(
    client: EspnScheduleClient, *, now: datetime, horizon_hours: float
) -> tuple[int | None, list[ScoreboardFetch]]:
    """How many ESPN events kick off inside the next `horizon_hours` --
    or None when ESPN could not be read at all.

    This exists for the FAIL-CLOSED path, where there may be no usable
    football-state artifact and therefore no local knowledge of whether
    anything is about to start. Without it, 'we know nothing' and 'we
    know nothing is due' would be the same answer, and the collector
    would have to choose between crying wolf forever and going silent
    through a game day. Two keyless requests buy the real answer."""
    buckets = sorted(
        {
            (now + timedelta(hours=offset)).astimezone(UTC).date().strftime("%Y%m%d")
            for offset in (-24.0, 0.0, horizon_hours)
        }
    )
    fetches = [client.fetch_scoreboard(bucket) for bucket in buckets]
    if not any(f.ok for f in fetches):
        return None, fetches
    horizon = now + timedelta(hours=horizon_hours)
    count = 0
    for fetch in fetches:
        if not fetch.ok:
            continue
        for raw in fetch.events:
            event = parse_espn_event(raw)
            status, _name = espn_status_for(event)  # shape only; identity is irrelevant to a horizon count
            if status != "scheduled" or event.event_date is None:
                continue
            if now < event.event_date <= horizon:
                count += 1
    return count, fetches
