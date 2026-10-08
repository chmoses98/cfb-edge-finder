"""Primary-window price capture: when to look, what was seen, and why a game has no price. PURE.

*** THE INVARIANT ***
No eligible game may miss the whole PRIMARY_60_180 window because OUR pipeline did not run. The conductor
attempts every game at 165, 120, 90 and 70 minutes before kickoff; a moment that passed without an attempt is
caught up on the next cycle while the window is still open. Every attempt is logged, successful or not, so a
missing price always has a reason:

* market side  -- MARKET_NOT_OFFERED, QUOTE_NOT_EXECUTABLE, PRICE_1_00: Kalshi offered nothing buyable;
* our side     -- ORIENTATION_FAILURE, CAPTURE_SYSTEM_FAILURE: we could not read or place what was there.

The two are never blurred: a failed request is never "no market", and "no market" is only ever concluded
from an attempt that actually reached Kalshi.

*** SIDE-SPECIFIC PRICES ***
Each KXNCAAFGAME market `<event>-<CODE>` pays YES iff team CODE wins. A side's price is its OWN captured ask.
NO is never `1 - YES`: the two asks routinely sum to more than a dollar. A $1.00 ask means nobody offers
that side below a dollar; it is recorded as PRICE_1_00, never as a price.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from cfb_edge_finder.catalog.contract import _NO_ASK, _NO_BID, _YES_ASK, _YES_BID, _first_money
from cfb_edge_finder.control_market.quotes import Quote, is_valid, minutes_before, parse_utc, whole_cent
from cfb_edge_finder.control_prospective import (
    ATTEMPT_SCHEMA,
    CAPTURE_OK,
    CAPTURE_PENDING,
    CAPTURE_SCHEMA,
    CAPTURE_SLOTS,
    CAPTURE_SYSTEM_FAILURE,
    CLOSING_SLOTS,
    GAME_WINNER_SERIES,
    MARKET_NOT_OFFERED,
    ORIENTATION_FAILURE,
    PRICE_1_00,
    QUOTE_NOT_EXECUTABLE,
    WINDOW_CLOSE_MIN,
    WINDOW_OPEN_MIN,
)

SOURCE = "PROSPECTIVE_CAPTURE"

#: Attempt outcomes (one row per attempt).
ATTEMPT_OK = "OK"  # Kalshi answered and the game's markets were in the answer
ATTEMPT_NO_MARKET = "NO_MARKET"  # Kalshi answered and no KXNCAAFGAME market exists for the game
ATTEMPT_FAILED = "REQUEST_FAILED"  # our request failed: no conclusion about the market


@dataclass(frozen=True)
class SlateGame:
    """One physical game the conductor watches."""

    game_key: str  # Kalshi game key, e.g. 26OCT10LSUUK
    kickoff_utc: str  # ESPN kickoff when known (the protocol reference), else the catalog's
    kickoff_source: str  # "espn_schedule" | "kalshi_catalog"
    title: str | None = None  # "Away at Home"
    espn_event_id: str | None = None

    @property
    def event_ticker(self) -> str:
        return f"{GAME_WINNER_SERIES}-{self.game_key}"


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def minutes_to(kickoff: str, now: str) -> float:
    return (parse_utc(kickoff) - parse_utc(now)).total_seconds() / 60.0


def window_bounds(kickoff: str) -> tuple[str, str]:
    k = parse_utc(kickoff)
    return iso(k - timedelta(minutes=WINDOW_OPEN_MIN)), iso(k - timedelta(minutes=WINDOW_CLOSE_MIN))


def in_primary_window(kickoff: str, at: str) -> bool:
    m = minutes_to(kickoff, at)
    return WINDOW_CLOSE_MIN <= m <= WINDOW_OPEN_MIN


def slot_label(minutes: int) -> str:
    return f"T{minutes}"


def due_slots(kickoff: str, now: str, attempted: Iterable[str]) -> list[str]:
    """Slots whose moment has passed and that have no attempt yet, while their window is still open.

    Primary slots are due only inside PRIMARY_60_180 (never after kickoff-60, never before kickoff-180);
    the descriptive closing slot only before kickoff. A game that has kicked off is never attempted."""
    m = minutes_to(kickoff, now)
    if m <= 0:
        return []
    done = set(attempted)
    due = []
    if WINDOW_CLOSE_MIN <= m <= WINDOW_OPEN_MIN:
        due += [slot_label(s) for s in CAPTURE_SLOTS if m <= s and slot_label(s) not in done]
    due += [slot_label(s) for s in CLOSING_SLOTS if m <= s and slot_label(s) not in done]
    return due


def normalize_market(raw: dict[str, Any]) -> dict[str, Any]:
    """A Kalshi API market (or a catalog market record) -> the fields a capture keeps, prices in dollars."""
    ticker = raw.get("ticker") or raw.get("market_ticker")
    return {
        "market_ticker": ticker,
        "event_ticker": raw.get("event_ticker") or (ticker.rsplit("-", 1)[0] if ticker else None),
        "team_code": ticker.rsplit("-", 1)[-1].upper() if ticker else None,
        "yes_sub_title": raw.get("yes_sub_title"),
        "status": raw.get("status"),
        "yes_bid": _first_money(raw, _YES_BID),
        "yes_ask": _first_money(raw, _YES_ASK),
        "no_bid": _first_money(raw, _NO_BID),
        "no_ask": _first_money(raw, _NO_ASK),
    }


def markets_by_game(raw_markets: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group normalized KXNCAAFGAME markets by game key."""
    out: dict[str, list[dict[str, Any]]] = {}
    for raw in raw_markets:
        m = normalize_market(raw)
        ticker = m["market_ticker"] or ""
        if not ticker.startswith(f"{GAME_WINNER_SERIES}-"):
            continue
        key = ticker.split("-")[1]
        out.setdefault(key, []).append(m)
    for v in out.values():
        v.sort(key=lambda m: m["market_ticker"])
    return out


def attempt_id(game_key: str, slots: list[str], attempted_at: str) -> str:
    return f"{game_key}:{'+'.join(slots)}:{attempted_at}"


def capture(
    game: SlateGame,
    slots: list[str],
    attempted_at: str,
    markets: list[dict[str, Any]] | None,
    *,
    request_error: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One attempt for one game: (attempt row, quote rows).

    `markets` is the game's normalized markets from a Kalshi read that SUCCEEDED (possibly empty);
    `request_error` set means the read failed and nothing may be concluded about the market."""
    if parse_utc(attempted_at) >= parse_utc(game.kickoff_utc):
        raise ValueError(f"{game.game_key}: refusing a capture at or after kickoff")
    aid = attempt_id(game.game_key, slots, attempted_at)
    if request_error is not None:
        outcome, quotes = ATTEMPT_FAILED, []
    elif not markets:
        outcome, quotes = ATTEMPT_NO_MARKET, []
    else:
        outcome = ATTEMPT_OK
        quotes = [
            {
                "schema": CAPTURE_SCHEMA,
                "source": SOURCE,
                "attempt_id": aid,
                "captured_at": attempted_at,
                "game_key": game.game_key,
                "kickoff_utc": game.kickoff_utc,
                "minutes_before": round(minutes_to(game.kickoff_utc, attempted_at), 2),
                "title": game.title,
                **m,
            }
            for m in markets
        ]
    attempt = {
        "schema": ATTEMPT_SCHEMA,
        "attempt_id": aid,
        "attempted_at": attempted_at,
        "game_key": game.game_key,
        "espn_event_id": game.espn_event_id,
        "kickoff_utc": game.kickoff_utc,
        "kickoff_source": game.kickoff_source,
        "minutes_before": round(minutes_to(game.kickoff_utc, attempted_at), 2),
        "slots": slots,
        "in_primary_window": in_primary_window(game.kickoff_utc, attempted_at),
        "outcome": outcome,
        "markets": len(quotes),
        "error": request_error,
    }
    return attempt, quotes


# --------------------------------------------------------------------------- reading captures back


def to_quote(row: dict[str, Any]) -> Quote:
    """A capture row as the frozen market study's Quote (for its validity rule and orientation matcher)."""
    return Quote(
        source=SOURCE,
        captured_at=row["captured_at"],
        event_ticker=row["event_ticker"],
        market_ticker=row["market_ticker"],
        yes_ask=row.get("yes_ask"),
        no_ask=row.get("no_ask"),
        yes_bid=row.get("yes_bid"),
        no_bid=row.get("no_bid"),
        status=row.get("status"),
        team_name=row.get("yes_sub_title"),
        event_title=row.get("title"),
        kickoff_hint=row.get("kickoff_utc"),
        provenance=row.get("attempt_id"),
    )


def primary_entry(rows: Iterable[dict[str, Any]], market_ticker: str, kickoff: str) -> dict[str, Any] | None:
    """The LAST valid executable YES quote of `market_ticker` inside PRIMARY_60_180, or None (missing stays missing)."""
    best = None
    for row in rows:
        if row.get("market_ticker") != market_ticker:
            continue
        q = to_quote(row)
        if not is_valid(q, "yes", kickoff):
            continue
        if not WINDOW_CLOSE_MIN <= minutes_before(q, kickoff) <= WINDOW_OPEN_MIN:
            continue
        if best is None or parse_utc(row["captured_at"]) > parse_utc(best["captured_at"]):
            best = row
    return best


def side_status(
    *,
    kickoff: str,
    now: str,
    market_ticker: str | None,
    orientation_status: str | None,
    quotes: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Capture health of ONE side (a CONTROL team's YES) of one game in the primary window.

    `market_ticker` is the side's oriented market (None when the game has no oriented market for it);
    `orientation_status` is the frozen matcher's verdict for the game ("RESOLVED", "NO_GAME_WINNER_MARKET",
    "ORIENTATION_UNRESOLVED", "ORIENTATION_CONFLICT") or None when there were no quotes to orient."""
    opens, closes = window_bounds(kickoff)
    window_attempts = [a for a in attempts if a.get("in_primary_window")]
    reached = [a for a in window_attempts if a["outcome"] in (ATTEMPT_OK, ATTEMPT_NO_MARKET)]
    entry = primary_entry(quotes, market_ticker, kickoff) if market_ticker else None
    base = {
        "window_opens_at": opens,
        "window_closes_at": closes,
        "attempts": len(window_attempts),
        "attempts_failed": sum(1 for a in window_attempts if a["outcome"] == ATTEMPT_FAILED),
        "market_ticker": market_ticker,
        "entry": None
        if entry is None
        else {
            "price": entry["yes_ask"],
            "captured_at": entry["captured_at"],
            "minutes_before": entry["minutes_before"],
        },
    }
    if entry is not None:
        return {**base, "status": CAPTURE_OK}
    closed = parse_utc(now) > parse_utc(closes)
    if not closed:
        return {**base, "status": CAPTURE_PENDING}
    if not reached:
        return {**base, "status": CAPTURE_SYSTEM_FAILURE}
    if all(a["outcome"] == ATTEMPT_NO_MARKET for a in reached):
        return {**base, "status": MARKET_NOT_OFFERED}
    if orientation_status in ("ORIENTATION_UNRESOLVED", "ORIENTATION_CONFLICT") or market_ticker is None:
        return {**base, "status": ORIENTATION_FAILURE}
    in_window = [
        r for r in quotes if r.get("market_ticker") == market_ticker and in_primary_window(kickoff, r["captured_at"])
    ]
    asks = [r.get("yes_ask") for r in in_window]
    if asks and all(a is not None and a >= 1.0 - 1e-9 for a in asks):
        return {**base, "status": PRICE_1_00}
    return {**base, "status": QUOTE_NOT_EXECUTABLE}


def current_price(row: dict[str, Any] | None) -> dict[str, Any]:
    """A side's latest quote as display state: an executable whole-cent ask, or the honest reason there is none."""
    if row is None:
        return {"status": "PRICE_UNAVAILABLE", "yes_ask": None, "captured_at": None}
    ask = row.get("yes_ask")
    status = str(row.get("status") or "").lower()
    if ask is not None and ask >= 1.0 - 1e-9:
        state = PRICE_1_00
    elif status and status not in ("active", "open"):
        state = QUOTE_NOT_EXECUTABLE
    elif whole_cent(ask) is None:
        state = QUOTE_NOT_EXECUTABLE
    else:
        state = "EXECUTABLE"
    return {
        "status": state,
        "yes_ask": ask if state == "EXECUTABLE" else None,
        "captured_at": row.get("captured_at"),
        "market_ticker": row.get("market_ticker"),
    }
