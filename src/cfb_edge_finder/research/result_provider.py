"""Canonical game-RESULT provider for research settlement: CFBD primary,
strictly-validated fail-closed ESPN fallback.

*** WHY THIS EXISTS (2026-08-29/30 CFBD quota outage) ***
Research settlement was CFBD-only. CFBD has been returning HTTP 429 for
over a day, so completed opening-slate games could not be canonically
settled even though their results are public. A settlement fact is a
GAME FACT (final score + finality), not model output -- so a second
free, keyless source of the same fact is legitimate, PROVIDED identity
and finality are validated strictly enough that a wrong-game or
not-actually-final settlement is impossible. Everything downstream
(settle_market, attribution, economics) is untouched: this module only
changes where the GameResult fact comes from, never what is done with it.

*** THE FAIL-CLOSED CONTRACT ***
  * CFBD stays primary. ESPN is consulted ONLY when the CFBD season
    fetch fails RECOVERABLY (429, 5xx, transport) -- a fallback is for
    SOURCE AVAILABILITY, never for overriding a valid primary answer.
    Auth/config errors and real 4xx client errors still raise: they are
    our bugs, and masking them behind a fallback would hide them.
  * Game identity is matched ONLY through the exact-match team registry
    (`teams.registry.resolve_team_alias` -- no fuzzy matching exists in
    this codebase, on purpose) against durable, CFBD-derived schedule
    identity (the football-state artifact, else the preseason schedule
    cache). Zero matching events, multiple matching events, flipped
    home/away orientation, an unresolvable or ambiguous team name, or a
    kickoff outside tolerance each FAIL CLOSED for that game: it simply
    does not settle this run, with the reason recorded.
  * Finality requires ESPN's explicit three-fold signal, live-verified
    2026-08-30 (run 33330066488): status.type.name == "STATUS_FINAL"
    AND state == "post" AND completed is True. Score presence, clock
    state, or a winner flag are NEVER treated as finality. Contradictory
    finality evidence fails closed. ESPN "postponed"/"canceled" claims
    are deliberately NOT turned into VOID settlements here -- a void is
    a primary-source decision, so those games stay NOT_YET_FINAL until
    CFBD recovers and says so itself.
  * If ESPN itself cannot be fetched while CFBD is down, the whole run
    aborts (ResultProviderUnavailable) -- no partial improvisation.

Provenance is additive: every fallback-sourced GameResult records
source="espn_fallback", the ESPN event id, the verbatim finality
evidence, and the exact CFBD failure that activated the fallback.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from cfb_edge_finder.data.espn_client import ESPNClient
from cfb_edge_finder.ingestion.game_normalization import GameNormalizationError, normalize_cfbd_game
from cfb_edge_finder.research import football_state
from cfb_edge_finder.research.settlement import extract_game_result
from cfb_edge_finder.schemas.settlement import GameFinalStatus, GameResult
from cfb_edge_finder.teams.registry import (
    AmbiguousTeamAliasError,
    UnknownTeamAliasError,
    resolve_team_alias,
)

CFBD_PRIMARY = "cfbd"
ESPN_FALLBACK = "espn_fallback"

ESPN_KICKOFF_TOLERANCE_HOURS = 12.0
"""A matched event's scheduled start must sit within this window of the
durable schedule's kickoff. Wide enough for a same-day weather slide,
narrow enough that a rescheduled-to-another-weekend game can never be
settled off stale identity. Two FBS teams meet at most once per regular
season, so an exact home+away identity match plus this bound is
unambiguous by construction."""


class ResultProviderUnavailable(RuntimeError):
    """BOTH sources failed: CFBD recoverably down and the ESPN fallback
    could not be fetched/used at all. The run must stop -- fail closed,
    settle nothing -- rather than settle from partial data."""


def _is_recoverable_cfbd_error(exc: requests.RequestException) -> bool:
    """429/5xx (already bounded-retried inside CFBDClient) and transport
    failures are availability problems -- fallback territory. Any other
    HTTP status (401/403/400/404...) is a configuration or contract bug
    that a fallback must surface, not mask."""
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        if response is None:
            return False
        return response.status_code == 429 or 500 <= response.status_code < 600
    return True  # ConnectionError / Timeout / other transport-level failure


# ---------------------------------------------------------------- identity


@dataclass(frozen=True)
class GameIdentity:
    game_id: str
    season: int
    home_team_id: str
    away_team_id: str
    kickoff_utc: datetime | None


def load_identity_map(repo_dir: Path, season: int, now: datetime) -> tuple[dict[str, GameIdentity], str]:
    """Durable, CFBD-derived schedule identity for fallback matching --
    NEVER from the fallback source itself (that would let ESPN vouch for
    its own identity). Preference order:

      1. the football-state artifact (data/research/football_state/) --
         the two-lane architecture's durable schedule snapshot, when one
         has been successfully written;
      2. the preseason schedule cache (data/research_cache/preseason/) --
         the same raw CFBD /games rows the projection corpus is built
         from, committed with a sha256 manifest.

    Both run through the EXACT production normalizer
    (`normalize_cfbd_game`), so game_id / home / away / kickoff are
    byte-identical to what capture recorded. Returns ({}, reason) when
    neither exists -- every game then fails closed as identity-less."""
    state, _reason = football_state.load_football_state(repo_dir, season)
    if state is not None:
        games = state.to_scan_inputs(now).games
        return (
            {
                g.game_id: GameIdentity(
                    game_id=g.game_id,
                    season=g.season,
                    home_team_id=g.home_team_id,
                    away_team_id=g.away_team_id,
                    kickoff_utc=g.kickoff_utc,
                )
                for g in games
            },
            f"football_state artifact (schedule_fetched_at={state.schedule_fetched_at.isoformat()})",
        )

    cache_path = repo_dir / "data" / "research_cache" / "preseason" / f"{season}.json"
    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {}, f"preseason schedule cache unreadable: {exc}"
        identity: dict[str, GameIdentity] = {}
        for raw in payload.get("games") or []:
            try:
                game = normalize_cfbd_game(raw, observed_at=now)
            except GameNormalizationError:
                continue
            identity[game.game_id] = GameIdentity(
                game_id=game.game_id,
                season=game.season,
                home_team_id=game.home_team_id,
                away_team_id=game.away_team_id,
                kickoff_utc=game.kickoff_utc,
            )
        return identity, "preseason schedule cache"

    return {}, "no durable schedule identity source found (no football_state artifact, no preseason cache)"


# ------------------------------------------------------------- ESPN parsing


@dataclass(frozen=True)
class EspnEventFacts:
    """One ESPN scoreboard event reduced to exactly the fields settlement
    identity/finality/score validation reads -- parsed defensively, with
    any identity-resolution failure RECORDED (making the event a
    non-candidate) rather than guessed around."""

    event_id: str | None
    name: str | None
    event_date: datetime | None
    season_year: int | None
    status_id: str | None
    status_name: str | None
    status_state: str | None
    status_completed: bool | None
    status_detail: str | None
    home_team_id: str | None = None
    away_team_id: str | None = None
    home_score_raw: str | None = None
    away_score_raw: str | None = None
    home_winner: bool | None = None
    away_winner: bool | None = None
    home_periods: int | None = None
    away_periods: int | None = None
    resolution_error: str | None = None


def _parse_espn_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _resolve_espn_team(team: dict) -> str:
    """Canonical team_id via the registry's EXACT-match resolution only.
    `team.location` is ESPN's school name (live-verified to match CFBD
    naming, unicode included); displayName ("USC Trojans") is tried
    second for robustness. Unknown or ambiguous raises -- the caller
    records it and the event becomes a non-candidate."""
    location = team.get("location")
    display_name = team.get("displayName")
    if isinstance(location, str) and location:
        try:
            return resolve_team_alias(location)
        except UnknownTeamAliasError:
            pass  # fall through to displayName -- still exact-match only
    if isinstance(display_name, str) and display_name:
        return resolve_team_alias(display_name)
    raise UnknownTeamAliasError(f"ESPN team has no usable name fields: {team!r}")


def parse_espn_event(event: dict) -> EspnEventFacts:
    competitions = event.get("competitions") or []
    comp = competitions[0] if competitions else {}
    status = ((comp.get("status") or event.get("status") or {}).get("type")) or {}
    season_year_raw = (event.get("season") or {}).get("year")

    base: dict[str, Any] = dict(
        event_id=str(event["id"]) if event.get("id") is not None else None,
        name=event.get("name"),
        event_date=_parse_espn_date(comp.get("date") or event.get("date")),
        season_year=int(season_year_raw) if isinstance(season_year_raw, int) else None,
        status_id=status.get("id"),
        status_name=status.get("name"),
        status_state=status.get("state"),
        status_completed=status.get("completed") if isinstance(status.get("completed"), bool) else None,
        status_detail=status.get("detail"),
    )

    competitors = comp.get("competitors") or []
    sides = {c.get("homeAway"): c for c in competitors if isinstance(c, dict)}
    if len(competitors) != 2 or set(sides) != {"home", "away"}:
        return EspnEventFacts(
            **base, resolution_error=f"expected exactly one home and one away competitor, got {len(competitors)}"
        )

    try:
        home_id = _resolve_espn_team(sides["home"].get("team") or {})
        away_id = _resolve_espn_team(sides["away"].get("team") or {})
    except (UnknownTeamAliasError, AmbiguousTeamAliasError) as exc:
        return EspnEventFacts(**base, resolution_error=f"team identity unresolved (exact-match only): {exc}")

    def _periods(c: dict) -> int | None:
        linescores = c.get("linescores")
        return len(linescores) if isinstance(linescores, list) and linescores else None

    return EspnEventFacts(
        **base,
        home_team_id=home_id,
        away_team_id=away_id,
        home_score_raw=sides["home"].get("score"),
        away_score_raw=sides["away"].get("score"),
        home_winner=sides["home"].get("winner") if isinstance(sides["home"].get("winner"), bool) else None,
        away_winner=sides["away"].get("winner") if isinstance(sides["away"].get("winner"), bool) else None,
        home_periods=_periods(sides["home"]),
        away_periods=_periods(sides["away"]),
    )


# ------------------------------------------------------------ ESPN matching


def scoreboard_dates_for(kickoff_utc: datetime) -> list[str]:
    """ESPN's `dates=` parameter buckets by US LOCAL date (live-verified:
    a 2026-08-30T02:00Z kickoff sat in the 20260829 bucket and 20260830
    was empty). US local dates never run AHEAD of UTC, so the kickoff's
    UTC date plus the prior day covers every bucket the event can be in."""
    day = kickoff_utc.date()
    return [day.strftime("%Y%m%d"), (day - timedelta(days=1)).strftime("%Y%m%d")]


def match_espn_event(
    identity: GameIdentity, events: list[EspnEventFacts]
) -> tuple[EspnEventFacts | None, str | None]:
    """Exactly one event whose canonical (home, away) equals the durable
    schedule's -- anything else fails closed with the reason. This IS the
    cross-source identity validation: the schedule side is CFBD-derived,
    the event side is ESPN, and they must agree on identity, orientation,
    season, and kickoff before a score is even looked at."""
    candidates = [
        e
        for e in events
        if e.resolution_error is None
        and e.home_team_id is not None
        and e.away_team_id is not None
        and (e.season_year is None or e.season_year == identity.season)
    ]
    exact = [
        e for e in candidates if e.home_team_id == identity.home_team_id and e.away_team_id == identity.away_team_id
    ]

    if len(exact) > 1:
        ids = sorted({e.event_id or "?" for e in exact})
        return None, (
            f"ambiguous: {len(exact)} ESPN events match home={identity.home_team_id} "
            f"away={identity.away_team_id} (event ids {ids})"
        )
    if not exact:
        flipped = [
            e for e in candidates if e.home_team_id == identity.away_team_id and e.away_team_id == identity.home_team_id
        ]
        if flipped:
            return None, (
                f"orientation mismatch: ESPN reports {flipped[0].home_team_id} as HOME where the durable schedule "
                f"has {identity.home_team_id} -- refusing to settle either orientation"
            )
        return None, f"no ESPN event matched home={identity.home_team_id} away={identity.away_team_id}"

    event = exact[0]
    if identity.kickoff_utc is not None and event.event_date is not None:
        delta_hours = abs((event.event_date - identity.kickoff_utc).total_seconds()) / 3600.0
        if delta_hours > ESPN_KICKOFF_TOLERANCE_HOURS:
            return None, (
                f"kickoff out of tolerance: ESPN event date {event.event_date.isoformat()} vs durable schedule "
                f"kickoff {identity.kickoff_utc.isoformat()} ({delta_hours:.1f}h > {ESPN_KICKOFF_TOLERANCE_HOURS}h)"
            )
    return event, None


# ----------------------------------------------------------- ESPN -> result

_ESPN_FINAL_NAME = "STATUS_FINAL"
_ESPN_POST_STATE = "post"


def _status_evidence(event: EspnEventFacts) -> str:
    return (
        f"espn status.type id={event.status_id!r} name={event.status_name!r} state={event.status_state!r} "
        f"completed={event.status_completed!r} detail={event.status_detail!r} (event {event.event_id}, "
        f"{event.name!r})"
    )


def _parse_score(raw: str | None) -> int | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return int(text) if text.isdigit() else None


def espn_game_result(
    event: EspnEventFacts, *, game_id: str, season: int, now: datetime, fallback_reason: str
) -> tuple[GameResult | None, str | None]:
    """One matched, identity-validated event -> a GameResult, or a
    fail-closed reason. Finality demands the full three-fold explicit
    signal; partial or contradictory finality evidence never settles."""
    is_final = (
        event.status_completed is True
        and event.status_state == _ESPN_POST_STATE
        and event.status_name == _ESPN_FINAL_NAME
    )
    looks_finalish = (
        event.status_completed is True
        or event.status_state == _ESPN_POST_STATE
        or event.status_name == _ESPN_FINAL_NAME
    )
    if not is_final:
        if looks_finalish:
            # e.g. completed=true while state != "post": some finality
            # markers present, others absent. Never settle on a partial
            # signal -- and never report "not final" either, because we
            # cannot tell which half of the contradiction is true.
            return None, f"contradictory ESPN finality evidence -- {_status_evidence(event)}"
        # Coherently not final (scheduled/in-progress/postponed/...).
        # Deliberately NOT mapped to POSTPONED/CANCELED even when ESPN
        # claims one: voiding markets is a primary-source decision, so
        # the game stays pending until CFBD itself says so.
        return (
            GameResult(
                game_id=game_id,
                season=season,
                status=GameFinalStatus.NOT_YET_FINAL,
                source=ESPN_FALLBACK,
                source_game_id=event.event_id,
                fallback_reason=fallback_reason,
                status_evidence=_status_evidence(event),
                captured_at=now,
            ),
            None,
        )

    home_points = _parse_score(event.home_score_raw)
    away_points = _parse_score(event.away_score_raw)
    if home_points is None or away_points is None:
        return None, (
            f"final ESPN event with unparseable score home={event.home_score_raw!r} away={event.away_score_raw!r} "
            f"-- {_status_evidence(event)}"
        )

    if home_points != away_points and event.home_winner is not None and event.away_winner is not None:
        expected_home_winner = home_points > away_points
        if event.home_winner != expected_home_winner or event.away_winner != (not expected_home_winner):
            return None, (
                f"ESPN winner flags (home={event.home_winner}, away={event.away_winner}) contradict scores "
                f"{home_points}-{away_points} -- {_status_evidence(event)}"
            )

    went_to_overtime: bool | None = None
    if event.home_periods is not None or event.away_periods is not None:
        periods = max(event.home_periods or 0, event.away_periods or 0)
        if periods > 4:
            went_to_overtime = True
        elif periods == 4:
            went_to_overtime = False

    return (
        GameResult(
            game_id=game_id,
            season=season,
            home_points=home_points,
            away_points=away_points,
            status=GameFinalStatus.FINAL,
            went_to_overtime=went_to_overtime,
            source=ESPN_FALLBACK,
            source_game_id=event.event_id,
            fallback_reason=fallback_reason,
            status_evidence=_status_evidence(event),
            captured_at=now,
        ),
        None,
    )


# ------------------------------------------------------------- orchestrator


@dataclass
class ResultProviderOutcome:
    results_by_game_id: dict[str, GameResult]
    provider: str  # CFBD_PRIMARY or ESPN_FALLBACK
    fallback_reason: str | None = None
    unresolved: dict[str, str] = field(default_factory=dict)
    """Fallback mode only: game_id -> why it FAILED CLOSED this run."""
    identity_source: str | None = None
    espn_dates_fetched: list[str] = field(default_factory=list)

    def summary_dict(self) -> dict:
        finals = sum(1 for r in self.results_by_game_id.values() if r.status is GameFinalStatus.FINAL)
        return {
            "result_provider": self.provider,
            "fallback_reason": self.fallback_reason,
            "identity_source": self.identity_source,
            "espn_dates_fetched": self.espn_dates_fetched,
            "games_with_results": len(self.results_by_game_id),
            "games_final": finals,
            "games_unresolved_fail_closed": len(self.unresolved),
            "unresolved_detail": dict(sorted(self.unresolved.items())),
        }


def resolve_game_results(
    *,
    season: int,
    now: datetime,
    cfbd_client,
    repo_dir: Path,
    needed_game_ids: set[str],
    espn_client: ESPNClient | None = None,
) -> ResultProviderOutcome:
    """The single entry point both settlement scripts use. CFBD first,
    always; on a RECOVERABLE CFBD failure only, the strict ESPN path for
    exactly the games the caller needs. See module docstring for the
    full fail-closed contract."""
    try:
        raw_games = cfbd_client.fetch_games(season=season, season_type=None)
    except requests.RequestException as exc:
        if not _is_recoverable_cfbd_error(exc):
            raise
        status = getattr(getattr(exc, "response", None), "status_code", None)
        fallback_reason = f"cfbd unavailable (recoverable): {type(exc).__name__}" + (
            f" HTTP {status}" if status is not None else ""
        )
    else:
        results: dict[str, GameResult] = {}
        for raw in raw_games:
            try:
                game = normalize_cfbd_game(raw, observed_at=now)
            except GameNormalizationError:
                continue
            results[game.game_id] = extract_game_result(raw, game_id=game.game_id, season=season, captured_at=now)
        return ResultProviderOutcome(results_by_game_id=results, provider=CFBD_PRIMARY)

    # ---- ESPN fallback: CFBD is recoverably down.
    client = espn_client or ESPNClient()
    identity_map, identity_source = load_identity_map(repo_dir, season, now)

    results = {}
    unresolved: dict[str, str] = {}
    events_by_date: dict[str, list[EspnEventFacts]] = {}

    for game_id in sorted(needed_game_ids):
        identity = identity_map.get(game_id)
        if identity is None:
            unresolved[game_id] = f"identity unavailable: game not in durable CFBD-derived schedule ({identity_source})"
            continue
        if identity.kickoff_utc is None:
            unresolved[game_id] = "kickoff unknown in durable schedule -- cannot bound the ESPN date query"
            continue

        for date in scoreboard_dates_for(identity.kickoff_utc):
            if date not in events_by_date:
                try:
                    body = client.fetch_scoreboard(date)
                except requests.RequestException as exc:
                    raise ResultProviderUnavailable(
                        f"{fallback_reason}; ESPN scoreboard fetch for {date} also failed: "
                        f"{type(exc).__name__}: {exc} -- both sources unavailable, settling NOTHING"
                    ) from exc
                events_by_date[date] = [parse_espn_event(e) for e in (body.get("events") or []) if isinstance(e, dict)]

        seen: set[str] = set()
        merged: list[EspnEventFacts] = []
        for date in scoreboard_dates_for(identity.kickoff_utc):
            for event in events_by_date[date]:
                key = event.event_id or f"anon-{id(event)}"
                if key in seen:
                    continue
                seen.add(key)
                merged.append(event)

        event, reason = match_espn_event(identity, merged)
        if reason is not None:
            unresolved[game_id] = reason
            continue
        assert event is not None
        result, reason = espn_game_result(
            event, game_id=game_id, season=season, now=now, fallback_reason=fallback_reason
        )
        if reason is not None:
            unresolved[game_id] = reason
            continue
        assert result is not None
        results[game_id] = result

    return ResultProviderOutcome(
        results_by_game_id=results,
        provider=ESPN_FALLBACK,
        fallback_reason=fallback_reason,
        unresolved=unresolved,
        identity_source=identity_source,
        espn_dates_fetched=sorted(events_by_date),
    )
