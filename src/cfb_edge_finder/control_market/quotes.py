"""Captured pregame game-winner quotes: extraction, orientation, checkpoint selection.

*** NOTHING IS RE-PRICED ***
Every quote is a price that was genuinely captured before kickoff, by the
research capture (`RESEARCH_OBSERVATION`) or by a committed catalog snapshot
(`CATALOG_SNAPSHOT`). The YES and NO asks are each taken from their own
captured field: a NO price is never `1 - yes`, because the two executable asks
routinely sum to more than 1. A missing ask stays missing.

*** ORIENTATION ***
A KXNCAAFGAME market `<event>-<CODE>` pays YES iff team CODE wins. Each market
is resolved to one ESPN competitor by team codes and by team names; a
disagreement or an unresolvable market is excluded before any outcome exists.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from cfb_edge_finder.control_market import CHECKPOINT_WINDOWS, PRIMARY_CHECKPOINT, SERIES

SOURCE_OBSERVATION = "RESEARCH_OBSERVATION"
SOURCE_CATALOG = "CATALOG_SNAPSHOT"
#: Tie-break on identical captured_at (protocol section 5).
SOURCE_PRIORITY = {SOURCE_OBSERVATION: 0, SOURCE_CATALOG: 1}
VALID_STATUSES = frozenset({"active", "open"})
MATCH_WINDOW_SECONDS = 36 * 3600

NameKeys = Callable[[str], set[str]]


@dataclass(frozen=True)
class Quote:
    """One captured top-of-book snapshot of one KXNCAAFGAME market."""

    source: str
    captured_at: str
    event_ticker: str
    market_ticker: str
    yes_ask: float | None
    no_ask: float | None
    yes_bid: float | None = None
    no_bid: float | None = None
    status: str | None = None
    yes_mid: float | None = None  # captured YES midpoint (descriptive scoring only; never an entry price)
    sentinel: bool = False
    team_name: str | None = None  # the market's own team spelling (catalog yes_sub_title / observation slug side)
    event_title: str | None = None  # "Away at Home" (catalog) or the observation game_id slug
    kickoff_hint: str | None = None  # the source's own kickoff, used only to find the ESPN event
    provenance: str | None = None  # catalog commit sha or observation_key

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _price(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_game_winner_ticker(ticker: str | None) -> bool:
    return bool(ticker) and str(ticker).startswith(f"{SERIES}-")


# --------------------------------------------------------------------------- extraction


_SLUG = re.compile(r"^cfb-\d{4}-wk\d+-(?P<away>.+)-at-(?P<home>.+)$")


def slug_teams(game_id: str | None) -> dict[str, str] | None:
    """`cfb-2026-wk01-fresno-state-at-usc` -> {"away": "fresno state", "home": "usc"}.

    Only "<away>-at-<home>" slugs are read. A "-vs-" slug does not state which
    side is home, so it yields no team names (the team-code method still applies)."""
    m = _SLUG.match(str(game_id or ""))
    if not m:
        return None
    return {"away": m.group("away").replace("-", " "), "home": m.group("home").replace("-", " ")}


def quotes_from_observation_row(row: dict[str, Any]) -> Quote | None:
    """One research observation row -> a Quote, or None when it is not a game-winner market."""
    obs = row.get("observation") or {}
    ticker = obs.get("kalshi_market_ticker")
    if not is_game_winner_ticker(ticker):
        return None
    teams = slug_teams(obs.get("game_id"))
    side = obs.get("team")
    team_name = teams.get(side) if teams and side in ("home", "away") else None
    return Quote(
        source=SOURCE_OBSERVATION,
        captured_at=str(obs.get("captured_at")),
        event_ticker=str(obs.get("kalshi_event_ticker") or ticker.rsplit("-", 1)[0]),
        market_ticker=str(ticker),
        yes_ask=_price(obs.get("executable_yes_price")),
        no_ask=_price(obs.get("executable_no_price")),
        status=obs.get("market_status"),
        yes_mid=_price(obs.get("market_midpoint")),
        team_name=team_name,
        event_title=obs.get("game_id"),
        kickoff_hint=row.get("kickoff_utc_at_capture"),
        provenance=row.get("observation_key"),
    )


def quotes_from_catalog_game(game: dict[str, Any], captured_at: str, commit: str) -> list[Quote]:
    """Every game-winner market in one catalog game file, stamped with its snapshot time."""
    out = []
    for m in game.get("markets") or []:
        ticker = m.get("market_ticker") or m.get("ticker")
        if not (m.get("series_ticker") == SERIES or is_game_winner_ticker(ticker)):
            continue
        if not is_game_winner_ticker(ticker):
            continue
        mech = m.get("mechanics") or {}
        out.append(
            Quote(
                source=SOURCE_CATALOG,
                captured_at=captured_at,
                event_ticker=str(m.get("event_ticker") or ticker.rsplit("-", 1)[0]),
                market_ticker=str(ticker),
                yes_ask=_price(m.get("yes_ask")),
                no_ask=_price(m.get("no_ask")),
                yes_bid=_price(m.get("yes_bid")),
                no_bid=_price(m.get("no_bid")),
                status=m.get("status"),
                yes_mid=_price(mech.get("yes_mid")),
                sentinel=bool(mech.get("is_sentinel_full_width_book")),
                team_name=m.get("yes_sub_title"),
                event_title=game.get("title"),
                kickoff_hint=game.get("kickoff"),
                provenance=commit,
            )
        )
    return out


# --------------------------------------------------------------------------- validity


def whole_cent(price: float | None) -> int | None:
    """The price in integer cents, or None when it is not a whole cent in [1, 99]."""
    if price is None:
        return None
    cents = round(price * 100)
    if abs(price * 100 - cents) > 1e-6 or not 1 <= cents <= 99:
        return None
    return cents


def side_ask(quote: Quote, side: str) -> float | None:
    """The captured executable ask of `side` ("yes"/"no"). Never a complement of the other side."""
    if side == "yes":
        return quote.yes_ask
    if side == "no":
        return quote.no_ask
    raise ValueError(f"side must be 'yes' or 'no', got {side!r}")


def is_valid(quote: Quote, side: str, kickoff: str) -> bool:
    """A valid executable quote of `side`, captured strictly before kickoff (protocol section 4)."""
    if quote.sentinel:
        return False
    if quote.status is not None and str(quote.status).lower() not in VALID_STATUSES:
        return False
    if whole_cent(side_ask(quote, side)) is None:
        return False
    return parse_utc(quote.captured_at) < parse_utc(kickoff)


def minutes_before(quote: Quote, kickoff: str) -> float:
    return (parse_utc(kickoff) - parse_utc(quote.captured_at)).total_seconds() / 60.0


def _in_window(minutes: float, window: tuple[float, float, bool, bool]) -> bool:
    lo, hi, lo_inc, hi_inc = window
    above = minutes >= lo if lo_inc else minutes > lo
    below = minutes <= hi if hi_inc else minutes < hi
    return above and below


def _order(q: Quote) -> tuple[datetime, int]:
    # later captured_at wins; on a tie the research observation wins
    return parse_utc(q.captured_at), -SOURCE_PRIORITY[q.source]


def select_checkpoint(quotes: Iterable[Quote], side: str, kickoff: str, checkpoint: str) -> Quote | None:
    """The registered quote for one contract side at one checkpoint, or None (missing stays missing).

    Windowed checkpoints take the LAST valid quote inside the window; EARLY_OPEN
    takes the FIRST valid quote ever captured. Nothing outside the window is
    ever considered."""
    valid = [q for q in quotes if is_valid(q, side, kickoff)]
    if checkpoint == "EARLY_OPEN":
        return min(valid, key=lambda q: (parse_utc(q.captured_at), SOURCE_PRIORITY[q.source]), default=None)
    window = CHECKPOINT_WINDOWS[checkpoint]
    inside = [q for q in valid if _in_window(minutes_before(q, kickoff), window)]
    return max(inside, key=_order, default=None)


def select_entry(quotes: Iterable[Quote], side: str, kickoff: str) -> Quote | None:
    return select_checkpoint(quotes, side, kickoff, PRIMARY_CHECKPOINT)


# --------------------------------------------------------------------------- orientation


def _codes_split(event_ticker: str) -> list[tuple[str, str]]:
    key = event_ticker.split("-", 1)[1] if "-" in event_ticker else ""
    codes = key[7:].upper()  # strip YYMONDD
    return [(codes[:i], codes[i:]) for i in range(2, len(codes) - 1)]


def _near(event: dict[str, Any], kickoff_hint: str | None) -> bool:
    if not kickoff_hint:
        return False
    try:
        return abs((parse_utc(event["date"]) - parse_utc(kickoff_hint)).total_seconds()) <= MATCH_WINDOW_SECONDS
    except (KeyError, TypeError, ValueError):
        return False


def match_event_by_codes(event_ticker: str, kickoff_hint: str | None, schedule: list[dict]) -> dict | None:
    hits = []
    for event in schedule:
        if not _near(event, kickoff_hint):
            continue
        abbrs = {str(c.get("abbreviation") or "").upper() for c in event.get("competitors") or []}
        if any({a, h} == abbrs and a != h for a, h in _codes_split(event_ticker)):
            hits.append(event)
    return hits[0] if len(hits) == 1 else None


def competitor_keys(competitor: dict[str, Any], keys: NameKeys) -> set[str]:
    """Production ESPN name keys (`name_keys`, as `script_engine.load_football` builds them) when present."""
    if competitor.get("name_keys") is not None:
        return set(competitor["name_keys"])
    out: set[str] = set()
    for name in [*(competitor.get("names") or []), competitor.get("location")]:
        if name:
            out |= keys(str(name))
    return out


def _event_title_teams(quote: Quote) -> list[str]:
    title = quote.event_title or ""
    teams = slug_teams(title)
    if teams:
        return [teams["away"], teams["home"]]
    for sep in (" at ", " vs. ", " vs "):
        if sep in title:
            return [p.strip() for p in title.split(sep, 1)]
    return []


def match_event_by_names(quote: Quote, schedule: list[dict], keys: NameKeys) -> dict | None:
    names = _event_title_teams(quote)
    if len(names) != 2:
        return None
    a, b = keys(names[0]), keys(names[1])
    if not a or not b:
        return None
    hits = []
    for event in schedule:
        if not _near(event, quote.kickoff_hint):
            continue
        comps = event.get("competitors") or []
        if len(comps) != 2:
            continue
        k0, k1 = competitor_keys(comps[0], keys), competitor_keys(comps[1], keys)
        if (a & k0 and b & k1) or (a & k1 and b & k0):
            hits.append(event)
    return hits[0] if len(hits) == 1 else None


def resolve_market(quote: Quote, schedule: list[dict], keys: NameKeys) -> dict[str, Any]:
    """Which ESPN competitor this market's YES pays on, with the methods that agreed.

    `espn_event_id` is kept even when the team cannot be resolved, so an
    unorientable market is still attributed to its game as ORIENTATION_UNRESOLVED."""
    code = quote.market_ticker.rsplit("-", 1)[-1].upper()
    by_code = by_name = None
    events: set[str] = set()
    ev = match_event_by_codes(quote.event_ticker, quote.kickoff_hint, schedule)
    if ev is not None:
        events.add(str(ev["id"]))
        comp = [c for c in ev["competitors"] if str(c.get("abbreviation") or "").upper() == code]
        if len(comp) == 1:
            by_code = (str(ev["id"]), str(comp[0]["team_id"]))
    ev2 = match_event_by_names(quote, schedule, keys)
    if ev2 is not None:
        events.add(str(ev2["id"]))
        mk = keys(quote.team_name) if quote.team_name else set()
        comp = [c for c in ev2["competitors"] if mk & competitor_keys(c, keys)] if mk else []
        if len(comp) == 1:
            by_name = (str(ev2["id"]), str(comp[0]["team_id"]))
    event_id = next(iter(events)) if len(events) == 1 else None
    if len(events) > 1 or (by_code and by_name and by_code != by_name):
        return {"status": "ORIENTATION_CONFLICT", "espn_event_id": event_id, "by_code": by_code, "by_name": by_name}
    resolved = by_code or by_name
    if resolved is None:
        return {"status": "ORIENTATION_UNRESOLVED", "espn_event_id": event_id}
    methods = [m for m, v in (("team_codes", by_code), ("team_names", by_name)) if v]
    return {"status": "RESOLVED", "espn_event_id": resolved[0], "team_id": resolved[1], "methods": methods}


def orient_markets(quotes: Iterable[Quote], schedule: list[dict], keys: NameKeys) -> dict[str, dict[str, Any]]:
    """market_ticker -> orientation, reconciled over every distinct piece of evidence.

    Every distinct (team spelling, title, kickoff, source) for a ticker is
    resolved; all resolutions must agree, and the two markets of one event
    must name two different competitors of the same ESPN event."""
    evidence: dict[str, dict[tuple, Quote]] = defaultdict(dict)
    for q in quotes:
        evidence[q.market_ticker][(q.team_name, q.event_title, q.kickoff_hint, q.source)] = q
    per_ticker: dict[str, dict[str, Any]] = {}
    for ticker, items in evidence.items():
        results = [resolve_market(q, schedule, keys) for q in items.values()]
        events = {r["espn_event_id"] for r in results if r.get("espn_event_id")}
        event_id = next(iter(events)) if len(events) == 1 else None
        resolved = {(r["espn_event_id"], r["team_id"]) for r in results if r["status"] == "RESOLVED"}
        if any(r["status"] == "ORIENTATION_CONFLICT" for r in results) or len(resolved) > 1 or len(events) > 1:
            per_ticker[ticker] = {"status": "ORIENTATION_CONFLICT", "espn_event_id": event_id}
        elif not resolved:
            per_ticker[ticker] = {"status": "ORIENTATION_UNRESOLVED", "espn_event_id": event_id}
        else:
            event_id, team_id = next(iter(resolved))
            methods = sorted({m for r in results if r["status"] == "RESOLVED" for m in r["methods"]})
            per_ticker[ticker] = {
                "status": "RESOLVED",
                "espn_event_id": event_id,
                "team_id": team_id,
                "methods": methods,
            }
    # the markets of one Kalshi event must be different competitors of one ESPN event
    by_event: dict[str, list[str]] = defaultdict(list)
    for ticker in per_ticker:
        by_event[ticker.rsplit("-", 1)[0]].append(ticker)
    for tickers in by_event.values():
        events = {per_ticker[t]["espn_event_id"] for t in tickers if per_ticker[t].get("espn_event_id")}
        teams = [per_ticker[t]["team_id"] for t in tickers if per_ticker[t]["status"] == "RESOLVED"]
        if len(events) > 1 or len(teams) != len(set(teams)):
            for t in tickers:
                per_ticker[t] = {"status": "ORIENTATION_CONFLICT", "espn_event_id": per_ticker[t].get("espn_event_id")}
    return per_ticker


def unattributed_market_for(
    game: dict[str, Any], abbreviations: set[str], orientation: dict[str, dict[str, Any]], kickoff_hints: dict[str, str]
) -> bool:
    """Label-only diagnostic: an unoriented KXNCAAFGAME market whose ticker code is one of this game's ESPN
    abbreviations, within 36 h of kickoff. Such a game is ORIENTATION_UNRESOLVED, not NO_GAME_WINNER_MARKET."""
    for ticker, o in orientation.items():
        if o["status"] == "RESOLVED" or o.get("espn_event_id"):
            continue
        code = ticker.rsplit("-", 1)[-1].upper()
        if code in abbreviations and _near({"date": game["kickoff_utc"]}, kickoff_hints.get(ticker)):
            return True
    return False


def contract_for(control_team_id: str, game_id: str, orientation: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The primary (CONTROL team YES) and secondary (opponent NO) contracts of one game.

    Returns {"status", "primary": (ticker, "yes") | None, "secondary": (ticker, "no") | None}."""
    markets = {t: o for t, o in orientation.items() if o.get("espn_event_id") == game_id}
    if not markets:
        return {"status": "NO_GAME_WINNER_MARKET", "primary": None, "secondary": None}
    if any(o["status"] == "ORIENTATION_CONFLICT" for o in markets.values()):
        return {"status": "ORIENTATION_CONFLICT", "primary": None, "secondary": None}
    resolved = {t: o for t, o in markets.items() if o["status"] == "RESOLVED"}
    own = [t for t, o in resolved.items() if o["team_id"] == control_team_id]
    opp = [t for t, o in resolved.items() if o["team_id"] != control_team_id]
    if len(own) > 1 or len(opp) > 1:
        return {"status": "ORIENTATION_CONFLICT", "primary": None, "secondary": None}
    secondary = (opp[0], "no") if opp else None
    if not own:
        return {"status": "ORIENTATION_UNRESOLVED", "primary": None, "secondary": secondary}
    return {"status": "RESOLVED", "primary": (own[0], "yes"), "secondary": secondary}
