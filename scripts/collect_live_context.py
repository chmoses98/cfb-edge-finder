#!/usr/bin/env python3
"""Collect FACTUAL CONTEXT for the games in the live Kalshi catalog.

    python scripts/collect_live_context.py --catalog-dir data/live --out-dir data/live/context

*** WHAT THIS IS FOR ***
The live execution packet used to carry Kalshi prices and an honest list of
everything it did not have: records, form, statistics, injuries, depth charts,
weather, rest, travel. That honesty was correct, and it was also the single
biggest cost in a Saturday -- every game's handicap started with somebody
going and finding all of it while a kickoff window closed.

This fetches it. It forms NO opinion about it. Every value written is
something a source published or arithmetic on published values, and each one
carries where it came from, when it was observed, and how good it is.

*** FAIL SOFT, PER GAME, ALWAYS ***
A source that 403s costs the domains that source feeds, for the games it would
have fed, and nothing else. The run still writes every other game, still exits
0, and the missing domains are recorded as `missing` WITH THE REASON -- which
is what the batching cost model charges for and the confidence ceiling caps
on. A collector that aborted on a bad provider would turn a partial-context
slate into no-context slate, which is strictly worse.

*** AN EMPTY LIST IS NOT A MISSING ONE ***
ESPN returning zero injuries is the observation "no injuries listed". ESPN
returning 403 is "we could not look". This script never lets the second become
the first -- see `context_sources.availability_field`.

*** NO PAID DEPENDENCY ***
ESPN's public scoreboard and core endpoints and Open-Meteo are keyless.
CFBD is optional: without CFBD_API_KEY the efficiency, situational,
turnover/pace and coaching domains are written as `missing` with "CFBD was not
consulted" as the reason, and everything else still lands.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import requests  # noqa: E402

from cfb_edge_finder.execution.context import (  # noqa: E402
    CONTEXT_DOMAINS,
    ContextField,
    GameContext,
)
from cfb_edge_finder.execution.context_sources import (  # noqa: E402
    availability_field,
    cfbd_efficiency_field,
    cfbd_situational_field,
    cfbd_turnovers_field,
    coaching_field,
    competitors_of,
    completed_games_for,
    environment_field,
    identity_field,
    market_reference_field,
    recent_results_field,
    records_field,
    rest_field,
    scoring_field,
    team_names,
)
from cfb_edge_finder.teams.registry import (  # noqa: E402
    AmbiguousTeamAliasError,
    UnknownTeamAliasError,
    get_team,
    resolve_team_alias,
)

USER_AGENT = "cfb-edge-finder factual-context collector (read-only)"
# 80 is FBS (Division I-A), 81 is FCS (I-AA). Both are listed on Kalshi, so
# both are swept: a division the collector never asks about is a division the
# handicapper gets no facts for.
SCOREBOARD_GROUPS = (80, 81)

ESPN_SCOREBOARD_HOSTS = (
    (
        "site.web.api.espn.com",
        "https://site.web.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
    ),
    ("cdn.espn.com", "https://cdn.espn.com/core/college-football/scoreboard"),
)
ESPN_CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football"

#: How far back to walk the scoreboard for records, scoring, form and rest.
#:
#: 21 days rather than the whole season: it is three requests-per-week of
#: history, it covers every team's last two or three games, and the fields it
#: feeds (rest days, recent form, points per game over the observed window) are
#: all explicitly windowed. A season-long walk would be four times the requests
#: for a number this repository is careful not to present as season-to-date.
DEFAULT_LOOKBACK_DAYS = 21

EXIT_OK = 0
EXIT_BAD_INPUT = 2


class Budget:
    """A wall clock and a circuit breaker, shared by every request in a run.

    Fail-soft per REQUEST is not fail-soft per RUN, and the difference only
    shows up at scale. A dead provider costs `timeout * (retries + 1)` plus
    backoff -- about 79 seconds -- and a `--date all` slate asks for roughly
    900 requests. A full outage would therefore run for hours and be killed by
    the job's own timeout, taking the slate with it: no context AND no slate,
    which is strictly worse than the unenriched slate the fail-soft design
    exists to guarantee.

    So the run stops SPENDING once it is out of time or once the evidence is
    in that nothing is answering. What it has already collected is written,
    the rest is recorded as missing with the reason, and the exit code stays 0.
    """

    def __init__(self, seconds: float, breaker_after: int) -> None:
        self.seconds = seconds
        self.breaker_after = breaker_after
        self.started = time.monotonic()
        self.consecutive_failures = 0
        self.tripped_reason: str | None = None

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def spent(self) -> bool:
        """True once no further request should be attempted."""
        if self.tripped_reason:
            return True
        if self.seconds and self.elapsed() >= self.seconds:
            self.tripped_reason = f"the {self.seconds:.0f}s collection budget was spent"
            return True
        if self.breaker_after and self.consecutive_failures >= self.breaker_after:
            self.tripped_reason = (
                f"{self.consecutive_failures} consecutive request failures -- "
                f"the sources are not answering"
            )
            return True
        return False

    def record(self, ok: bool) -> None:
        self.consecutive_failures = 0 if ok else self.consecutive_failures + 1


# Set by main(); None means "no limit", which is what the unit tests and an
# operator debugging one game both want.
_BUDGET: Budget | None = None


def _get(url: str, params: dict | None = None, *, retries: int = 2, timeout: float = 25.0):
    """One GET. Never raises: a failure is a (None, reason) the caller records.

    Fail-soft is the whole contract of this script, so the transport layer is
    where it starts. An exception escaping here would abort a run that has
    ninety other games to write."""
    if _BUDGET is not None and _BUDGET.spent():
        return None, _BUDGET.tripped_reason
    last = "no attempt"
    for attempt in range(retries + 1):
        try:
            response = requests.get(
                url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout
            )
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}"
        else:
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    if _BUDGET is not None:
                        _BUDGET.record(False)
                    return None, "unparsable JSON"
                if _BUDGET is not None:
                    _BUDGET.record(True)
                return payload, None
            last = f"HTTP {response.status_code}"
            if response.status_code in (401, 403, 404):
                # A policy answer, not a transient one. Retrying spends the
                # run's budget to be told the same thing.
                break
        if attempt < retries:
            if _BUDGET is not None and _BUDGET.spent():
                break
            time.sleep(1.5 * (attempt + 1))
    if _BUDGET is not None:
        _BUDGET.record(False)
    return None, last


def _events_from(host: str, payload: Any) -> list[dict] | None:
    if not isinstance(payload, dict):
        return None
    events = (
        payload.get("events")
        if host == "site.web.api.espn.com"
        else ((payload.get("content") or {}).get("sbData") or {}).get("events")
    )
    return [e for e in events if isinstance(e, dict)] if isinstance(events, list) else None


def fetch_scoreboard(date_param: str, groups: tuple[int, ...] = SCOREBOARD_GROUPS):
    """Every event on a date, across divisions, deduplicated by event id.

    `groups=80` alone is FBS, and that is not the market. Kalshi lists FCS
    games -- Dayton at Eastern Kentucky, Fordham at William & Mary, Alcorn St.
    at North Alabama are all in one live catalog -- and the owner can bet them.
    Sweeping FBS only left 110 of 234 games unmatched on the first working run
    and gave every one of them an `insufficient` confidence ceiling, which is
    honest but is a fact this repository could have had for one more request
    per date.

    A group that answers with nothing is not a failure: out of season, or on a
    date one division does not play, an empty list is the correct answer. The
    date fails only if NO group on NO host answered.
    """
    merged: dict[str, dict] = {}
    answered = False
    error: str | None = None
    for group in groups:
        for host, url in ESPN_SCOREBOARD_HOSTS:
            payload, host_error = _get(
                url, {"groups": group, "dates": date_param, "limit": 400}
            )
            if payload is None:
                error = host_error or error
                continue
            events = _events_from(host, payload)
            if events is None:
                error = "payload did not carry the expected scoreboard envelope"
                continue
            answered = True
            for index, event in enumerate(events):
                # Falling back to a composite key rather than dropping an
                # event: an id is not guaranteed, and a game with no id is
                # still a game whose score we can read.
                key = str(event.get("id") or f"{group}:{date_param}:{index}")
                merged.setdefault(key, event)
            break
    if not answered:
        return [], error or "no host answered"
    return list(merged.values()), None


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _kalshi_team_keys(packet_teams: dict[str, Any]) -> dict[str, set[str]]:
    """Every spelling of each Kalshi team, normalised, for ESPN matching.

    Goes through the repository's own team registry rather than matching raw
    strings: "Ole Miss", "Mississippi" and "Miss" are one team and only the
    registry knows that. A failure to resolve is a failure to enrich, which is
    a missing domain and not a wrong one."""
    out: dict[str, set[str]] = {}
    for slot in ("home", "away"):
        raw = packet_teams.get(slot)
        if not raw:
            out[slot] = set()
            continue
        candidates = {str(raw)}
        try:
            team = get_team(resolve_team_alias(str(raw)))
        except (UnknownTeamAliasError, AmbiguousTeamAliasError):
            # An unresolvable or ambiguous name costs this game its
            # enrichment. It must never cost it a WRONG enrichment: matching
            # on the raw string alone is how "Miami" ends up carrying the
            # other Miami's injuries.
            team = None
        if team is not None:
            candidates.add(team.display_name)
            candidates.add(team.team_id.replace("-", " "))
        out[slot] = {_norm(c) for c in candidates if c}
    return out


def _match_event(packet: dict[str, Any], events: list[dict]) -> dict | None:
    """The ESPN event for one Kalshi game: same day, both teams matched.

    Both halves are required. Matching one team would pair a Saturday
    doubleheader's two games, and a mismatched event would attach the wrong
    venue, the wrong weather and the wrong injury lists to a real handicap."""
    teams = _kalshi_team_keys(((packet.get("game_metadata") or {}).get("teams")) or {})
    if not teams["home"] or not teams["away"]:
        return None
    kickoff = str(packet.get("kickoff") or "")[:10]
    best = None
    for event in events:
        if kickoff and str(event.get("date") or "")[:10] not in (kickoff, ""):
            # ESPN and Kalshi can disagree by a day across the UTC boundary, so
            # a one-day window is allowed; two is not.
            try:
                espn_day = datetime.fromisoformat(str(event["date"]).replace("Z", "+00:00"))
                packet_day = datetime.fromisoformat(str(packet["kickoff"]).replace("Z", "+00:00"))
                if abs((espn_day - packet_day).total_seconds()) > 36 * 3600:
                    continue
            except (KeyError, ValueError):
                continue
        names = set()
        for competitor in competitors_of(event):
            names |= {_norm(n) for n in team_names(competitor)}
        if teams["home"] & names and teams["away"] & names:
            best = event
            break
    return best


# Why one Kalshi game found no ESPN event. Ordered from "our fault" to
# "the provider does not have it", because the fix differs completely.
UNMATCHED_NO_TEAMS = "no_teams_parsed_from_kalshi_title"
UNMATCHED_NO_EVENT_IN_WINDOW = "espn_has_no_event_in_the_date_window"
UNMATCHED_ONE_TEAM = "one_team_matched_the_other_did_not"
UNMATCHED_NEITHER_TEAM = "neither_team_name_matched"
UNMATCHED_OUTSIDE_WINDOW = "both_teams_matched_but_outside_the_date_window"


def classify_match_failure(
    packet: dict[str, Any], events: list[dict]
) -> tuple[str, str]:
    """WHY a game matched nothing, and the nearest evidence for it.

    `_match_event` answers yes or no, which is all pricing needs and nothing a
    person can act on. A name this repository spells differently from ESPN is
    an alias fix; a game ESPN never published is a provider gap and no amount
    of alias work will close it. Counting them together hides both.

    Returns (reason, detail). The detail names the closest ESPN event, so an
    alias mismatch is visible without re-running anything.
    """
    teams = _kalshi_team_keys(((packet.get("game_metadata") or {}).get("teams")) or {})
    if not teams["home"] or not teams["away"]:
        return UNMATCHED_NO_TEAMS, str(packet.get("title") or packet.get("game_key") or "")

    kickoff_day = str(packet.get("kickoff") or "")[:10]
    in_window, out_of_window = [], []
    for event in events:
        names = set()
        for competitor in competitors_of(event):
            names |= {_norm(n) for n in team_names(competitor)}
        hit_home = bool(teams["home"] & names)
        hit_away = bool(teams["away"] & names)
        if not (hit_home or hit_away):
            continue
        same_day = str(event.get("date") or "")[:10] == kickoff_day
        near = same_day
        if not near:
            try:
                espn_day = datetime.fromisoformat(str(event["date"]).replace("Z", "+00:00"))
                packet_day = datetime.fromisoformat(
                    str(packet["kickoff"]).replace("Z", "+00:00")
                )
                near = abs((espn_day - packet_day).total_seconds()) <= 36 * 3600
            except (KeyError, ValueError, TypeError):
                near = False
        (in_window if near else out_of_window).append((hit_home, hit_away, event))

    both_out = [e for h, a, e in out_of_window if h and a]
    if both_out:
        return (
            UNMATCHED_OUTSIDE_WINDOW,
            f"{_event_label(both_out[0])} on {str(both_out[0].get('date'))[:10]} "
            f"vs kickoff {kickoff_day}",
        )
    if not in_window:
        return UNMATCHED_NO_EVENT_IN_WINDOW, f"{_packet_label(packet)} on {kickoff_day}"
    partial = [(h, a, e) for h, a, e in in_window if h != a]
    if partial:
        hit_home, _hit_away, event = partial[0]
        side = "home" if hit_home else "away"
        return (
            UNMATCHED_ONE_TEAM,
            f"{_packet_label(packet)} -- matched {side} only against "
            f"ESPN {_event_label(event)}",
        )
    return (
        UNMATCHED_NEITHER_TEAM,
        f"{_packet_label(packet)} -- nearest ESPN {_event_label(in_window[0][2])}",
    )


def _packet_label(packet: dict[str, Any]) -> str:
    teams = ((packet.get("game_metadata") or {}).get("teams")) or {}
    return f"{teams.get('away') or '?'} at {teams.get('home') or '?'}"


def _event_label(event: dict) -> str:
    names = []
    for competitor in competitors_of(event):
        found = team_names(competitor)
        names.append(next(iter(found), "?") if found else "?")
    return " / ".join(names) if names else str(event.get("name") or "?")


def collect_game(
    packet: dict[str, Any],
    *,
    event: dict | None,
    history: dict[str, list[dict]],
    injuries: dict[str, dict | None],
    odds: dict | None,
    cfbd: dict[str, Any],
    as_of: datetime,
) -> GameContext:
    observed = as_of.isoformat()
    game_key = str(packet.get("game_key"))
    fields: dict[str, ContextField] = {
        domain: ContextField.missing(domain, "not collected in this run")
        for domain in CONTEXT_DOMAINS
    }
    sources: list[str] = []

    if event is None:
        for domain in ("identity", "records", "environment", "scoring", "recent_results",
                       "rest_and_travel", "availability", "market_reference"):
            fields[domain] = ContextField.missing(
                domain,
                "no ESPN scoreboard event could be matched to this Kalshi game",
                "espn.scoreboard",
            )
    else:
        sources.append("espn.scoreboard")
        fields["identity"] = identity_field(event, observed_at=observed)
        fields["records"] = records_field(event, observed_at=observed)
        fields["environment"] = environment_field(event, observed_at=observed)
        home_history = history.get("home") or []
        away_history = history.get("away") or []
        fields["scoring"] = scoring_field(home_history, away_history, observed_at=observed)
        fields["recent_results"] = recent_results_field(
            home_history, away_history, observed_at=observed
        )
        kickoff = None
        try:
            kickoff = datetime.fromisoformat(str(packet.get("kickoff")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            kickoff = None
        fields["rest_and_travel"] = rest_field(
            kickoff, home_history, away_history, observed_at=observed
        )
        fields["availability"] = availability_field(
            injuries.get("home"), injuries.get("away"), observed_at=observed
        )
        if injuries.get("home") is not None or injuries.get("away") is not None:
            sources.append("espn.core.injuries")
        fields["market_reference"] = market_reference_field(odds, observed_at=observed)
        if odds is not None:
            sources.append("espn.core.odds")

    teams = (packet.get("game_metadata") or {}).get("teams") or {}
    rows = cfbd.get("advanced")
    coaches = cfbd.get("coaches")
    fields["efficiency"] = cfbd_efficiency_field(
        rows, teams.get("home"), teams.get("away"), observed_at=observed
    )
    fields["situational"] = cfbd_situational_field(
        rows, teams.get("home"), teams.get("away"), observed_at=observed
    )
    fields["turnovers_and_pace"] = cfbd_turnovers_field(
        rows, teams.get("home"), teams.get("away"), observed_at=observed
    )
    fields["coaching"] = coaching_field(
        coaches, teams.get("home"), teams.get("away"), observed_at=observed
    )
    if rows is not None or coaches is not None:
        sources.append("cfbd")

    return GameContext(
        game_key=game_key,
        fields=fields,
        collected_at=observed,
        sources_attempted=tuple(dict.fromkeys(sources)),
    )


def _espn_team_id(event: dict | None, slot: str) -> str | None:
    if event is None:
        return None
    for competitor in competitors_of(event):
        if competitor.get("homeAway") == slot:
            return str((competitor.get("team") or {}).get("id") or "") or None
    return None


RUN_SUMMARY_NAME = "collection_run.json"


def _kickoff_of(entry: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(entry.get("kickoff")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _resolve_horizon(
    raw: str, as_of: datetime, kickoffs: list[datetime]
) -> tuple[datetime, str]:
    """How far forward to enrich, and a label saying so.

    `all` exists because the horizon has to be able to track the SLATE. A slate
    built with `--date all` spans the catalog's whole kickoff range, and pairing
    it with a fixed two-day context window enriches almost none of it -- which
    is exactly what happened on the first live run of this script: 0 of 234
    games, on a Monday, with the next kickoff three days out.
    """
    if str(raw).strip().lower() == "all":
        if not kickoffs:
            return as_of, "whole-catalog (empty catalog)"
        latest = max(kickoffs)
        return latest, f"whole-catalog (to {latest.date().isoformat()})"
    days = float(raw)
    return as_of + timedelta(days=days), f"{days:g}-day"


def _horizon_offset_days(as_of: datetime, horizon: datetime) -> int:
    """Whole days from now to the horizon, never negative.

    The scoreboard sweep walks one request per DATE, so it needs a day count
    rather than a timestamp. A past horizon would produce an empty range and a
    silently unenriched slate, so it floors at zero.
    """
    return max(0, int((horizon - as_of).total_seconds() // 86400) + 1)


def _write_run_summary(
    out_dir: Path,
    *,
    as_of: datetime,
    horizon: datetime,
    horizon_label: str,
    games_in_catalog: int,
    games_in_horizon: int,
    games_written: int,
    events_matched: int,
    scoreboard_dates: int,
    scoreboard_failures: dict[str, str],
    nearest_kickoff: datetime | None,
    stopped_early: str | None = None,
    elapsed_seconds: float | None = None,
    unmatched_reasons: dict[str, int] | None = None,
    unmatched_examples: dict[str, list[str]] | None = None,
) -> None:
    """Record what this run was ASKED for and what it reached.

    Coverage alone cannot tell an operator why a slate is unenriched. Zero
    games in the horizon and zero games reachable because every provider 403'd
    produce the identical `0 / 234 enriched`, and they call for opposite
    responses. This file is what separates them, and the slate workflow prints
    it.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / RUN_SUMMARY_NAME).write_text(
        json.dumps(
            {
                "as_of": as_of.isoformat(),
                "horizon": horizon.isoformat(),
                "horizon_label": horizon_label,
                "games_in_catalog": games_in_catalog,
                "games_in_horizon": games_in_horizon,
                "games_written": games_written,
                "espn_events_matched": events_matched,
                "scoreboard_dates_requested": scoreboard_dates,
                "scoreboard_dates_failed": len(scoreboard_failures),
                "scoreboard_failure_reasons": sorted(set(scoreboard_failures.values()))[:5],
                "nearest_kickoff": nearest_kickoff.isoformat() if nearest_kickoff else None,
                "unmatched_by_reason": dict(sorted((unmatched_reasons or {}).items())),
                "unmatched_examples": {
                    reason: sorted(examples)[:5]
                    for reason, examples in sorted((unmatched_examples or {}).items())
                },
                "stopped_early": stopped_early,
                "elapsed_seconds": round(elapsed_seconds, 1) if elapsed_seconds else None,
                "verdict": (
                    "stopped_early"
                    if stopped_early
                    else _verdict(
                        games_in_horizon, games_written, events_matched, scoreboard_failures
                    )
                ),
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _verdict(
    games_in_horizon: int,
    games_written: int,
    events_matched: int,
    scoreboard_failures: dict[str, str],
) -> str:
    """One word for what happened, so the workflow does not have to guess."""
    if games_in_horizon == 0:
        return "no_games_in_horizon"
    if scoreboard_failures and events_matched == 0:
        return "sources_unreachable"
    if events_matched == 0:
        return "no_events_matched"
    if events_matched < games_written:
        return "partial"
    return "complete"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-dir", default="data/live")
    parser.add_argument("--out-dir", default="data/live/context")
    parser.add_argument("--as-of", default=None, help="ISO timestamp; defaults to now")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument(
        "--horizon-days",
        default="3",
        help=(
            "only enrich games kicking off inside this many days; the rest are left alone. "
            "`all` means the catalog's own latest kickoff, which is what a slate built with "
            "`--date all` actually spans"
        ),
    )
    parser.add_argument(
        "--budget-seconds",
        type=float,
        default=600.0,
        help=(
            "stop making requests after this long and write what was collected. 0 disables it. "
            "This bounds the run against a provider outage, which would otherwise outlast the "
            "workflow's own timeout and cost the slate as well as the context"
        ),
    )
    parser.add_argument(
        "--breaker-after",
        type=int,
        default=25,
        help=(
            "stop making requests after this many CONSECUTIVE failures. 0 disables it. "
            "Once nothing is answering, further attempts buy no information"
        ),
    )
    parser.add_argument(
        "--skip-injuries",
        action="store_true",
        help="skip the per-team injury calls (2 requests per game)",
    )
    parser.add_argument(
        "--skip-odds", action="store_true", help="skip the per-game odds call"
    )
    parser.add_argument(
        "--cfbd",
        action="store_true",
        help=(
            "also consult CFBD for efficiency, situational and coaching context. Requires "
            "CFBD_API_KEY. Without this flag those domains are written as missing, with that as "
            "the recorded reason."
        ),
    )
    args = parser.parse_args(argv)

    as_of = (
        datetime.now(UTC)
        if not args.as_of
        else datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    )
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)

    index_path = Path(args.catalog_dir) / "cfb_market_catalog.json"
    if not index_path.exists():
        print(f"no catalog at {index_path}", file=sys.stderr)
        return EXIT_BAD_INPUT
    index = json.loads(index_path.read_text(encoding="utf-8"))

    global _BUDGET
    _BUDGET = Budget(args.budget_seconds, args.breaker_after)

    entries = list(index.get("games") or [])
    kickoffs = [k for k in (_kickoff_of(entry) for entry in entries) if k is not None]
    horizon, horizon_label = _resolve_horizon(args.horizon_days, as_of, kickoffs)

    floor = as_of - timedelta(hours=6)
    wanted: list[dict[str, Any]] = []
    for entry in entries:
        kickoff = _kickoff_of(entry)
        if kickoff is not None and floor <= kickoff <= horizon:
            wanted.append(entry)

    # The nearest kickoff STILL AHEAD of the floor. The catalog's minimum is
    # usually in the past -- a catalog retains last week -- and "widen the
    # horizon to reach it" about a game that already kicked off is advice that
    # cannot be followed.
    ahead = [kickoff for kickoff in kickoffs if kickoff >= floor]
    nearest = min(ahead) if ahead else None

    print(f"games in the {horizon_label} horizon: {len(wanted)} of {len(entries)} in the catalog")
    if not wanted:
        # WHY there is nothing to do, not just that there is nothing to do.
        # "0 enriched" downstream would otherwise be indistinguishable from a
        # provider outage, and those call for opposite responses: widen the
        # horizon, or go and look at the source.
        if nearest is not None:
            print(
                f"  nothing kicks off before {horizon.isoformat()}; the catalog's next "
                f"game is {nearest.isoformat()} "
                f"({(nearest - as_of).total_seconds() / 86400:.1f} days out). "
                f"Widen --horizon-days to reach it.",
                file=sys.stderr,
            )
        elif kickoffs:
            print(
                f"  every one of the catalog's {len(kickoffs)} kickoffs is already in the "
                f"past (latest {max(kickoffs).isoformat()}). The catalog is stale, and no "
                f"horizon will help.",
                file=sys.stderr,
            )
        else:
            print("  the catalog names no kickoff at all", file=sys.stderr)
        _write_run_summary(
            Path(args.out_dir),
            as_of=as_of,
            horizon=horizon,
            horizon_label=horizon_label,
            games_in_catalog=len(entries),
            games_in_horizon=0,
            games_written=0,
            events_matched=0,
            scoreboard_dates=0,
            scoreboard_failures={},
            nearest_kickoff=nearest,
        )
        return EXIT_OK

    # ONE SCOREBOARD REQUEST PER DATE covers the whole slate, forwards and
    # backwards. This is what makes derived scoring, form and rest affordable:
    # 115 games' history for the price of three weeks of dates.
    dates = sorted(
        {
            (as_of + timedelta(days=offset)).strftime("%Y%m%d")
            for offset in range(-args.lookback_days, _horizon_offset_days(as_of, horizon) + 2)
        }
    )
    scoreboards: dict[str, list[dict]] = {}
    failures: dict[str, str] = {}
    for date_param in dates:
        events, error = fetch_scoreboard(date_param)
        scoreboards[date_param] = events
        if error:
            failures[date_param] = error
    all_events = [event for events in scoreboards.values() for event in events]
    print(f"scoreboard dates fetched: {len(dates) - len(failures)} / {len(dates)}")
    for date_param, error in sorted(failures.items())[:5]:
        print(f"  {date_param}: {error}")

    cfbd_payload: dict[str, Any] = {"advanced": None, "coaches": None}
    if args.cfbd:
        key = os.environ.get("CFBD_API_KEY", "").strip()
        if not key:
            print("--cfbd was passed but CFBD_API_KEY is unset; skipping CFBD", file=sys.stderr)
        else:
            season = (index.get("games") or [{}])[0].get("identity", {}).get("season_year")
            rows, error = _get(
                "https://api.collegefootballdata.com/stats/game/advanced",
                {"year": season, "excludeGarbageTime": "true"},
                timeout=40.0,
            )
            if rows is None:
                print(f"CFBD advanced stats unavailable: {error}", file=sys.stderr)
            else:
                cfbd_payload["advanced"] = rows if isinstance(rows, list) else None
            coaches, error = _get(
                "https://api.collegefootballdata.com/coaches", {"year": season}, timeout=40.0
            )
            cfbd_payload["coaches"] = coaches if isinstance(coaches, list) else None

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    matched = 0
    unmatched_reasons: dict[str, int] = {}
    unmatched_examples: dict[str, list[str]] = {}
    for entry in wanted:
        packet = {
            "game_key": entry.get("game_key"),
            "kickoff": entry.get("kickoff"),
            "game_metadata": {"teams": _teams_from_title(entry)},
        }
        event = _match_event(packet, all_events)
        if event is not None:
            matched += 1
        else:
            reason, detail = classify_match_failure(packet, all_events)
            unmatched_reasons[reason] = unmatched_reasons.get(reason, 0) + 1
            unmatched_examples.setdefault(reason, []).append(detail)

        history: dict[str, list[dict]] = {"home": [], "away": []}
        injuries: dict[str, dict | None] = {"home": None, "away": None}
        odds = None
        if event is not None:
            for slot in ("home", "away"):
                competitor = next(
                    (c for c in competitors_of(event) if c.get("homeAway") == slot), None
                )
                if competitor is None:
                    continue
                names = set(team_names(competitor))
                history[slot] = [
                    row
                    for row in completed_games_for(names, all_events)
                    if row.get("date") and row["date"] < str(entry.get("kickoff"))
                ]
                if not args.skip_injuries:
                    team_id = _espn_team_id(event, slot)
                    if team_id:
                        payload, _error = _get(
                            f"{ESPN_CORE}/seasons/"
                            f"{(entry.get('identity') or {}).get('season_year') or as_of.year}"
                            f"/teams/{team_id}/injuries"
                        )
                        injuries[slot] = payload
            if not args.skip_odds and event.get("id"):
                odds, _error = _get(
                    f"{ESPN_CORE}/events/{event['id']}/competitions/{event['id']}/odds"
                )

        context = collect_game(
            packet,
            event=event,
            history=history,
            injuries=injuries,
            odds=odds,
            cfbd=cfbd_payload,
            as_of=as_of,
        )
        path = out_dir / f"{context.game_key}.json"
        path.write_text(
            json.dumps(context.as_dict(), indent=1, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        written += 1

    print(f"context written: {written} game(s) -> {out_dir}")
    print(f"  ESPN events matched: {matched} / {written}")
    if unmatched_reasons:
        print(f"  unmatched: {written - matched}, by reason:")
        for reason, count in sorted(unmatched_reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {count:4}  {reason}")
            for example in unmatched_examples.get(reason, [])[:3]:
                print(f"            e.g. {example}")
    if _BUDGET is not None and _BUDGET.tripped_reason:
        print(f"  STOPPED EARLY: {_BUDGET.tripped_reason}", file=sys.stderr)
        print(
            "  the games after that point are written with their domains missing, "
            "and the reason recorded on each one",
            file=sys.stderr,
        )
    _write_run_summary(
        out_dir,
        as_of=as_of,
        horizon=horizon,
        horizon_label=horizon_label,
        games_in_catalog=len(entries),
        games_in_horizon=len(wanted),
        games_written=written,
        events_matched=matched,
        scoreboard_dates=len(dates),
        scoreboard_failures=failures,
        nearest_kickoff=nearest,
        stopped_early=_BUDGET.tripped_reason if _BUDGET else None,
        elapsed_seconds=_BUDGET.elapsed() if _BUDGET else None,
        unmatched_reasons=unmatched_reasons,
        unmatched_examples=unmatched_examples,
    )
    # ALWAYS 0 FOR A PROVIDER PROBLEM. A collector that failed the job on a
    # 403 would take the whole slate's enrichment down with one bad endpoint,
    # and the missing domains are already recorded, counted and gated.
    return EXIT_OK


def _teams_from_title(entry: dict[str, Any]) -> dict[str, Any]:
    """Home and away from the catalog's own title, using Kalshi's convention.

    Kalshi lists the away side first ("Charlotte at Appalachian St."). This is
    the same convention `execution/semantics.py` applies and it is recorded as
    a convention there too -- a neutral-site or `vs`-titled game can be wrong,
    which is why a failed team match costs enrichment rather than producing a
    flipped one.
    """
    title = str(entry.get("title") or "")
    for separator in (" at ", " vs. ", " vs "):
        if separator in title:
            away, home = title.split(separator, 1)
            return {"home": home.strip(), "away": away.strip()}
    return {"home": None, "away": None}


if __name__ == "__main__":
    raise SystemExit(main())
