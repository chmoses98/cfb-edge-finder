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


def _get(url: str, params: dict | None = None, *, retries: int = 2, timeout: float = 25.0):
    """One GET. Never raises: a failure is a (None, reason) the caller records.

    Fail-soft is the whole contract of this script, so the transport layer is
    where it starts. An exception escaping here would abort a run that has
    ninety other games to write."""
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
                    return response.json(), None
                except ValueError:
                    return None, "unparsable JSON"
            last = f"HTTP {response.status_code}"
            if response.status_code in (401, 403, 404):
                # A policy answer, not a transient one. Retrying spends the
                # run's budget to be told the same thing.
                break
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
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


def fetch_scoreboard(date_param: str) -> tuple[list[dict], str | None]:
    for host, url in ESPN_SCOREBOARD_HOSTS:
        payload, error = _get(url, {"groups": 80, "dates": date_param, "limit": 400})
        if payload is None:
            continue
        events = _events_from(host, payload)
        if events is not None:
            return events, None
        error = "payload did not carry the expected scoreboard envelope"
    return [], error or "no host answered"


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-dir", default="data/live")
    parser.add_argument("--out-dir", default="data/live/context")
    parser.add_argument("--as-of", default=None, help="ISO timestamp; defaults to now")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument(
        "--horizon-days",
        type=float,
        default=3.0,
        help="only enrich games kicking off inside this many days; the rest are left alone",
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

    horizon = as_of + timedelta(days=args.horizon_days)
    wanted: list[dict[str, Any]] = []
    for entry in index.get("games") or []:
        try:
            kickoff = datetime.fromisoformat(str(entry.get("kickoff")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if as_of - timedelta(hours=6) <= kickoff <= horizon:
            wanted.append(entry)

    print(f"games in the {args.horizon_days}-day horizon: {len(wanted)}")
    if not wanted:
        return EXIT_OK

    # ONE SCOREBOARD REQUEST PER DATE covers the whole slate, forwards and
    # backwards. This is what makes derived scoring, form and rest affordable:
    # 115 games' history for the price of three weeks of dates.
    dates = sorted(
        {
            (as_of + timedelta(days=offset)).strftime("%Y%m%d")
            for offset in range(-args.lookback_days, int(args.horizon_days) + 2)
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
    for entry in wanted:
        packet = {
            "game_key": entry.get("game_key"),
            "kickoff": entry.get("kickoff"),
            "game_metadata": {"teams": _teams_from_title(entry)},
        }
        event = _match_event(packet, all_events)
        if event is not None:
            matched += 1

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
