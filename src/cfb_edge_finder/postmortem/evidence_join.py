"""Evidence join: recommendation link, model timestamps, fill ids and closing-price history, per wager.

*** A SEPARATE, DERIVED RECORD -- NEVER WRITTEN ONTO THE LEDGER ***
The routed-ledger contract refuses ``recommendation_id`` / model provenance on accounting rows ("not accounting
facts"), and the accounting ledger is append-only. So this module takes wager rows and returns a SEPARATE evidence
record per wager; it cannot modify the rows it is handed (asserted by test).

*** NOTHING IS GUESSED ***
Every field is either found in a named source or carries an explicit ``MISSING_*`` / ``UNKNOWN_*`` state:

* recommendation link -- only through ``decision_attribution.attribute`` (exact ticker + side + game, decision record
  created before the order and before kickoff, nearest earlier record, ties ``ambiguous``). With NO decision
  records the state is ``UNKNOWN_NO_DECISION_RECORDS`` -- explicitly NOT "executed_not_recommended", which would be a
  claim that nothing recommended it.
* fill ids -- present only if the routed ledger carried them; otherwise ``MISSING_NOT_IN_ROUTED_LEDGER``.
* closing price -- the LAST catalog snapshot captured before kickoff (the repository has no true close). It is a
  CLOSING PROXY and carries its own lag (``seconds_before_kickoff``). A snapshot hours before kickoff is labelled
  ``STALE_CLOSE_PROXY``; it is never presented as the close. A market absent from the catalog (e.g. a cross-category
  combo, which is not enumerable per game) is ``MISSING_MARKET_NOT_IN_CATALOG``.

CLV here is ``closing value of the position's side - fill price`` in probability points (positive = the market moved
toward the position). It is computed from the closing proxy's MID when the book was two-sided, and separately from
the closing BID (what the position could actually be sold at). Neither is a profit claim.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from cfb_edge_finder.accounting.decision_attribution import attribute

EVIDENCE_VERSION = "cfb_evidence_join/1.0.0"

#: A closing proxy captured no earlier than this before kickoff is labelled NEAR_CLOSE_PROXY. Policy label only
#: (owner-adjustable), chosen as the catalog's nominal 30-minute cadence plus one missed run.
NEAR_CLOSE_MAX_LAG = timedelta(minutes=60)

_GAME_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})([A-Z0-9]+)$")
_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
_MONTH_BY_NUM = {v: k for k, v in _MONTHS.items()}


@dataclass(frozen=True)
class Snapshot:
    ref: str
    captured_at: datetime


class SnapshotSource(Protocol):
    def snapshots(self) -> list[Snapshot]:
        """Catalog snapshots, ascending by ``captured_at``."""

    def game_keys(self, snapshot: Snapshot) -> set[str]:
        """Game keys that have a quote shard in this snapshot."""

    def quotes(self, snapshot: Snapshot, game_key: str) -> dict[str, Any] | None:
        """The game's merged quote shard (``quotes`` list + ``kickoff``), or None."""


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(UTC) if d.tzinfo else None


def candidate_game_keys(ticker_game_key: str | None) -> list[str]:
    """Same team pair, event date -1/0/+1 day: a late kickoff rolls the UTC date, so sibling events of one game
    can carry different date segments (e.g. ``26NOV03BALLMASS`` game vs ``26NOV04BALLMASS`` spread)."""
    if not ticker_game_key:
        return []
    m = _GAME_RE.match(ticker_game_key)
    if not m or m.group(2) not in _MONTHS:
        return [ticker_game_key]
    yy, mon, dd, teams = int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)), m.group(4)
    try:
        base = datetime(2000 + yy, mon, dd, tzinfo=UTC)
    except ValueError:
        return [ticker_game_key]
    out = []
    for delta in (0, -1, 1):
        d = base + timedelta(days=delta)
        out.append(f"{d.year % 100:02d}{_MONTH_BY_NUM[d.month]}{d.day:02d}{teams}")
    return out


def _side_values(row: dict[str, Any], side: str) -> dict[str, float | None]:
    """Closing bid / ask / mid for the SIDE HELD, from a quote row. NO ask = 1 - YES bid (one book serves both)."""
    def f(x: Any) -> float | None:
        return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None

    yb, ya = f(row.get("yes_bid")), f(row.get("yes_ask"))
    two_sided = row.get("book_state") == "two_sided"
    if side == "YES":
        bid, ask = yb, ya
    else:
        bid = None if ya is None else round(1.0 - ya, 6)
        ask = None if yb is None else round(1.0 - yb, 6)
    mid = None
    if two_sided and bid is not None and ask is not None:
        mid = round((bid + ask) / 2.0, 6)
    return {"bid": bid, "ask": ask, "mid": mid}


def _find_row(quotes: dict[str, Any] | None, ticker: str) -> dict[str, Any] | None:
    for row in (quotes or {}).get("quotes") or []:
        if row.get("market_ticker") == ticker:
            return row
    return None


class _Resolver:
    def __init__(self, source: SnapshotSource):
        self.source = source
        self.snaps = sorted(source.snapshots(), key=lambda s: s.captured_at)
        self._keys: dict[str, set[str]] = {}
        self._quotes: dict[tuple[str, str], dict[str, Any] | None] = {}

    def keys(self, snap: Snapshot) -> set[str]:
        if snap.ref not in self._keys:
            self._keys[snap.ref] = self.source.game_keys(snap)
        return self._keys[snap.ref]

    def quotes(self, snap: Snapshot, game_key: str) -> dict[str, Any] | None:
        k = (snap.ref, game_key)
        if k not in self._quotes:
            self._quotes[k] = self.source.quotes(snap, game_key)
        return self._quotes[k]

    def locate(self, snap: Snapshot, ticker: str, ticker_game_key: str | None) -> tuple[str | None, dict | None]:
        for key in candidate_game_keys(ticker_game_key):
            if key in self.keys(snap):
                row = _find_row(self.quotes(snap, key), ticker)
                if row is not None:
                    return key, row
        return None, None


def _kickoff_of(resolver: _Resolver, ticker: str, ticker_game_key: str | None) -> tuple[datetime | None, str | None]:
    """Kickoff from ANY snapshot that lists the game (latest first); None if no snapshot has the game."""
    for snap in reversed(resolver.snaps):
        for key in candidate_game_keys(ticker_game_key):
            if key in resolver.keys(snap):
                kick = parse_utc((resolver.quotes(snap, key) or {}).get("kickoff"))
                if kick is not None:
                    return kick, key
    return None, None


def _game_key_of(ticker: str) -> str | None:
    tail = ticker.split("-", 2)
    return tail[1] if len(tail) >= 2 else None


def close_proxy(wager: dict[str, Any], resolver: _Resolver) -> dict[str, Any]:
    ticker = str(wager.get("market_ticker") or "")
    side = str(wager.get("side") or "").upper()
    if ticker.startswith("KXMVE"):
        return {"state": "MISSING_MARKET_NOT_IN_CATALOG", "reason": "cross-category combos are not enumerable per game"}
    gk = _game_key_of(ticker)
    kickoff, game_key = _kickoff_of(resolver, ticker, gk)
    if kickoff is None:
        return {"state": "MISSING_GAME_NOT_IN_ANY_SNAPSHOT"}
    pre = [s for s in resolver.snaps if s.captured_at < kickoff]
    for snap in reversed(pre):  # latest capture before kickoff that still lists this market
        key, row = resolver.locate(snap, ticker, gk)
        if row is None:
            continue
        lag = kickoff - snap.captured_at
        vals = _side_values(row, side)
        fill = wager.get("execution_price")
        fill = float(fill) if isinstance(fill, (int, float)) else None
        out = {
            "state": "NEAR_CLOSE_PROXY" if lag <= NEAR_CLOSE_MAX_LAG else "STALE_CLOSE_PROXY",
            "snapshot": snap.ref,
            "captured_at": snap.captured_at.isoformat(),
            "kickoff": kickoff.isoformat(),
            "seconds_before_kickoff": int(lag.total_seconds()),
            "book_state": row.get("book_state"),
            "side_bid": vals["bid"],
            "side_ask": vals["ask"],
            "side_mid": vals["mid"],
            "clv_vs_mid": None if fill is None or vals["mid"] is None else round(vals["mid"] - fill, 6),
            "clv_vs_bid": None if fill is None or vals["bid"] is None else round(vals["bid"] - fill, 6),
        }
        if vals["mid"] is None:
            out["clv_vs_mid_missing_reason"] = "BOOK_NOT_TWO_SIDED"
        # A pre-kickoff close is the benchmark for a PRE-GAME entry only. An order placed at/after kickoff bought a
        # live price; scoring it against the pre-kickoff close mixes in-game movement and is not CLV. Withhold the
        # CLV (kept under a labelled diagnostic) and say why; an unknown order time cannot be proven pre-game.
        executed = parse_utc(wager.get("executed_at"))
        phase = "UNKNOWN" if executed is None else ("LIVE" if executed >= kickoff else "PRE_GAME")
        out["entry_phase"] = phase
        if phase != "PRE_GAME":
            out["live_entry_diagnostic"] = {"clv_vs_mid": out["clv_vs_mid"], "clv_vs_bid": out["clv_vs_bid"]}
            out["clv_vs_mid"] = out["clv_vs_bid"] = None
            out["clv_withheld_reason"] = "ENTRY_AT_OR_AFTER_KICKOFF" if phase == "LIVE" else "ENTRY_TIME_UNKNOWN"
        return out
    return {"state": "MISSING_MARKET_NOT_IN_CATALOG", "reason": "no pre-kickoff snapshot lists this market"}


def entry_context(wager: dict[str, Any], resolver: _Resolver) -> dict[str, Any]:
    """The last snapshot at or before the order: how stale the last known book was when the order went in."""
    ticker = str(wager.get("market_ticker") or "")
    side = str(wager.get("side") or "").upper()
    placed = parse_utc(wager.get("executed_at"))
    if placed is None:
        return {"state": "MISSING_EXECUTION_TIME"}
    gk = _game_key_of(ticker)
    for snap in reversed([s for s in resolver.snaps if s.captured_at <= placed]):
        key, row = resolver.locate(snap, ticker, gk)
        if row is None:
            continue
        vals = _side_values(row, side)
        fill = wager.get("execution_price")
        fill = float(fill) if isinstance(fill, (int, float)) else None
        return {
            "state": "SNAPSHOT_BEFORE_ORDER",
            "captured_at": snap.captured_at.isoformat(),
            "snapshot_age_seconds_at_order": int((placed - snap.captured_at).total_seconds()),
            "side_ask": vals["ask"],
            "fill_minus_snapshot_ask": None if fill is None or vals["ask"] is None else round(fill - vals["ask"], 6),
        }
    return {"state": "MISSING_NO_SNAPSHOT_BEFORE_ORDER"}


def fill_evidence(wager: dict[str, Any]) -> dict[str, Any]:
    order_id = wager.get("kalshi_order_id") or wager.get("order_id")
    fills = wager.get("kalshi_fill_ids") or wager.get("fill_ids") or []
    if order_id and fills:
        return {"state": "PRESENT", "order_id": order_id, "fill_ids": list(fills)}
    return {"state": "MISSING_NOT_IN_ROUTED_LEDGER", "order_id": order_id or None, "fill_ids": list(fills)}


def _router_view(w: dict[str, Any]) -> dict[str, Any]:
    """Ledger-export wager -> the router-style keys ``decision_attribution.attribute`` reads. A copy; the input
    is never touched."""
    return {
        "source_bet_key": w.get("source_bet_key"),
        "market_ticker": w.get("kalshi_ticker") or w.get("market_ticker"),
        "side": (w.get("selection") or w.get("side") or ""),
        "executed_at": w.get("placed_at") or w.get("executed_at"),
        "execution_price": w.get("average_price") if w.get("average_price") is not None else w.get("execution_price"),
        "stake": w.get("stake"),
        "game_date": (w.get("extensions") or {}).get("game_date") or w.get("game_date"),
        "kalshi_order_id": w.get("kalshi_order_id") or w.get("order_id"),
        "kalshi_fill_ids": list(w.get("kalshi_fill_ids") or w.get("fill_ids") or []),
    }


def _stats(values: list[float]) -> dict[str, Any]:
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "ci95": None}
    mean = sum(values) / n
    if n < 2:
        return {"n": n, "mean": round(mean, 6), "ci95": None}
    sd = (sum((v - mean) ** 2 for v in values) / (n - 1)) ** 0.5
    half = 1.96 * sd / n**0.5
    return {"n": n, "mean": round(mean, 6), "ci95": [round(mean - half, 6), round(mean + half, 6)]}


def build_evidence(
    wagers: Iterable[dict[str, Any]],
    *,
    source: SnapshotSource | None,
    decision_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One evidence record per wager plus coverage counts. Pure over its inputs; reads, never writes, the wagers."""
    views = [_router_view(w) for w in wagers]
    resolver = _Resolver(source) if source is not None else None
    if decision_records:
        attr = attribute(views, decision_records)
        by_key = {e["source_bet_key"]: e for e in attr.entries}
    else:
        by_key = {}
    rows: list[dict[str, Any]] = []
    coverage: dict[str, dict[str, int]] = {"recommendation": {}, "fill_ids": {}, "close": {}, "entry": {}}

    def bump(group: str, state: str) -> None:
        coverage[group][state] = coverage[group].get(state, 0) + 1

    for w in views:
        key = w["source_bet_key"]
        if decision_records:
            e = by_key.get(key) or {"state": "UNKNOWN_NOT_EVALUATED"}
            rec = {k: e.get(k) for k in ("state", "recommendation_id", "record_id", "record_created_at") if k in e}
            rec.setdefault("state", e.get("state"))
        else:
            rec = {"state": "UNKNOWN_NO_DECISION_RECORDS", "recommendation_id": None}
        fills = fill_evidence(w)
        close = close_proxy(w, resolver) if resolver else {"state": "MISSING_NO_SNAPSHOT_SOURCE"}
        entry = entry_context(w, resolver) if resolver else {"state": "MISSING_NO_SNAPSHOT_SOURCE"}
        bump("recommendation", str(rec["state"]))
        bump("fill_ids", fills["state"])
        bump("close", close["state"])
        bump("entry", entry["state"])
        rows.append({"source_bet_key": key, "market_ticker": w["market_ticker"], "side": w["side"],
                     "recommendation": rec, "fill_ids": fills, "entry_context": entry, "close_proxy": close})
    clv = [r["close_proxy"]["clv_vs_mid"] for r in rows if r["close_proxy"].get("clv_vs_mid") is not None]
    by_state: dict[str, list[float]] = {}
    by_family: dict[str, list[float]] = {}
    for r in rows:
        v = r["close_proxy"].get("clv_vs_mid")
        if v is None:
            continue
        by_state.setdefault(r["close_proxy"]["state"], []).append(v)
        by_family.setdefault(str(r["market_ticker"]).split("-", 1)[0], []).append(v)
    lags = sorted(
        r["close_proxy"]["seconds_before_kickoff"] for r in rows if "seconds_before_kickoff" in r["close_proxy"]
    )
    return {
        "version": EVIDENCE_VERSION,
        "wagers": len(rows),
        "coverage": coverage,
        "clv_vs_mid": {
            **_stats(clv),
            "by_close_state": {k: _stats(v) for k, v in sorted(by_state.items())},
            "by_family": {k: _stats(v) for k, v in sorted(by_family.items())},
            "note": "closing PROXY (last pre-kickoff catalog capture), probability points; positive = market moved "
            "toward the position. Wager-level CLV is correlated within games and families; intervals here assume "
            "independence and are therefore too narrow.",
        },
        "close_lag_seconds": {
            "n": len(lags),
            "median": None if not lags else lags[len(lags) // 2],
            "max": None if not lags else lags[-1],
        },
        "rows": rows,
    }


def closes_from_evidence(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``{ticker: {yes_bid, yes_ask, captured_at, ...}}`` for ``cfb_loss_attribution --closing-quotes``.

    Only NEAR_CLOSE_PROXY snapshots with a TWO-SIDED book are emitted. A stale proxy is deliberately left out so the
    loss classifier keeps saying ``MISSING_CLOSE_QUOTE`` instead of treating an hours-old book as the close.
    """
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        c = r["close_proxy"]
        if c.get("state") != "NEAR_CLOSE_PROXY" or c.get("book_state") != "two_sided":
            continue
        bid, ask = c.get("side_bid"), c.get("side_ask")
        if bid is None or ask is None:
            continue
        yes_bid, yes_ask = (bid, ask) if r["side"] == "YES" else (round(1.0 - ask, 6), round(1.0 - bid, 6))
        out[r["market_ticker"]] = {
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "captured_at": c["captured_at"],
            "seconds_before_kickoff": c["seconds_before_kickoff"],
            "provenance": "NEAR_CLOSE_PROXY: last catalog capture <= 60 min before kickoff; not a true close",
        }
    return out
