"""Prospective, read-only capture of every Kalshi CFB first-quarter total rung (series KXNCAAF1QTOTAL).

What is recorded, per run
  * a run manifest (always): sweeps and whether each COMPLETED, request log with raw-body sha256, counts, errors, fingerprint;
  * one row per market whose quote/status/contract fingerprint CHANGED since the last row (or on a periodic FULL board);
    unquoted contracts are explicit rows with `quote_status="NO_QUOTE"`, never omitted;
  * raw API pages (gzip) whenever anything changed or on a FULL board, hashed over the ORIGINAL response bytes;
  * optional order-book depth for open markets whose game starts within a bounded horizon.

Eligibility is computed AT OBSERVATION TIME from the observation clock and Kalshi's milestone start. Kalshi's start can be a
placeholder (`time-tbd`), so `start_independently_verified` is False here and the audit re-checks it against ESPN; a row is only
`eligible_pregame=True` when the market is open, quoted, the start is known and observed_at < start. Nothing is ever back-dated:
a price seen after kickoff is recorded as such and is never promoted to pregame."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from cfb_edge_finder.catalog.identity import MILESTONE_TYPE_FOOTBALL_GAME, milestone_from_payload, parse_event_ticker
from cfb_edge_finder.catalog.pagination import paginate
from cfb_edge_finder.data.kalshi_client import DEFAULT_BASE_URL, RETRY_ATTEMPTS, KalshiClient

from .archive_io import canonical, sha256

SERIES = "KXNCAAF1QTOTAL"
ROW_SCHEMA = "cfb_1q_ladder_row/1.0"
RUN_SCHEMA = "cfb_1q_ladder_run/1.0"
FULL_BOARD_EVERY = timedelta(hours=6)
ORDERBOOK_HORIZON = timedelta(hours=3)
MAX_ORDERBOOKS = 300
ORDERBOOK_BUDGET = timedelta(minutes=4)  # wall-clock cap so the run can finish inside its job timeout
SWEEP_STATUSES = ("unopened", "open", "closed", "settled")
SETTLED_LOOKBACK = timedelta(days=7)
QUOTE_KEY_MARKERS = (
    "bid",
    "ask",
    "price",
    "volume",
    "open_interest",
    "liquidity",
    "status",
    "result",
    "settlement",
    "expiration_value",
)
BUCKETS = ((72 * 60, ">72h"), (24 * 60, "24-72h"), (6 * 60, "6-24h"), (60, "1-6h"), (15, "15-60m"), (0, "0-15m"))


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ") if dt else None


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        p = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if p.year <= 1:
        return None
    return p if p.tzinfo else p.replace(tzinfo=UTC)


def quote_view(raw: dict[str, Any]) -> dict[str, Any]:
    """Every field that can move between observations. Selected by name markers so a Kalshi schema change cannot silently drop one."""
    return {k: raw[k] for k in sorted(raw) if any(m in k for m in QUOTE_KEY_MARKERS)}


def contract_view(raw: dict[str, Any]) -> dict[str, Any]:
    """Exact contract definition (rules text, strike, close/expiry times) - hashed separately so a rules change is visible."""
    keys = (
        "ticker",
        "event_ticker",
        "series_ticker",
        "title",
        "subtitle",
        "yes_sub_title",
        "no_sub_title",
        "rules_primary",
        "rules_secondary",
        "strike_type",
        "floor_strike",
        "cap_strike",
        "custom_strike",
        "open_time",
        "close_time",
        "expected_expiration_time",
        "expiration_time",
        "latest_expiration_time",
        "settlement_timer_seconds",
        "can_close_early",
        "early_close_condition",
        "market_type",
        "tick_size",
        "price_level_structure",
    )
    return {k: raw[k] for k in keys if k in raw}


def _price(raw: dict[str, Any], name: str) -> float | None:
    """Dollar price from `<name>_dollars` ("0.4300") or legacy integer cents `<name>`; None if absent/unparseable."""
    for key, scale in ((f"{name}_dollars", 1.0), (name, None)):
        if key in raw and raw[key] not in (None, ""):
            try:
                v = float(raw[key])
            except (TypeError, ValueError):
                return None
            return v if scale else (v / 100.0 if v > 1.0 else v)
    return None


def has_quote(raw: dict[str, Any]) -> bool:
    """A live YES quote exists: a positive bid, or an ask strictly between 0 and 1 (an ask of 0/1 is the empty-book sentinel)."""
    yb, ya = _price(raw, "yes_bid"), _price(raw, "yes_ask")
    return bool((yb is not None and yb > 0) or (ya is not None and 0 < ya < 1))


def lead_bucket(minutes: float | None) -> str:
    if minutes is None:
        return "unknown_start"
    if minutes < 0:
        return "after_start"
    for floor, name in BUCKETS:
        if minutes >= floor:
            return name
    return "0-15m"


def eligibility(
    observed_at: datetime, start: datetime | None, milestone_status: str | None, market: dict[str, Any]
) -> dict[str, Any]:
    status = str(market.get("status") or "").lower()
    tradeable = status in ("open", "active")
    placeholder = (
        milestone_status != "scheduled"
    )  # only a plain `scheduled` milestone has a start we trust (time-tbd/created/inprogress/closed/complete do not)
    lead = None if start is None else (start - observed_at).total_seconds() / 60.0
    reasons = []
    if start is None:
        reasons.append("START_UNKNOWN")
    elif lead is not None and lead <= 0:
        reasons.append("AT_OR_AFTER_START")
    if placeholder and start is not None:
        reasons.append(f"MILESTONE_STATUS_{str(milestone_status or 'UNKNOWN').upper().replace('-', '_')}")
    if not tradeable:
        reasons.append(f"MARKET_STATUS_{status.upper() or 'UNKNOWN'}")
    if not has_quote(market):
        reasons.append("NO_QUOTE")
    return {
        "eligible_pregame": not reasons,
        "ineligible_reasons": reasons,
        "lead_minutes": None if lead is None else round(lead, 2),
        "lead_bucket": lead_bucket(lead),
        "start_source": "kalshi_milestone" if start else None,
        "start_independently_verified": False,
    }


class RawKalshiGetter:
    """GET with the same bounded retry policy as KalshiClient, recording the sha256 of the ORIGINAL response bytes of every call."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20.0,
        http_get: Callable[..., Any] = requests.get,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = utcnow,
        attempts: int = RETRY_ATTEMPTS,
    ) -> None:
        self.base_url, self.timeout, self._get, self._sleep, self._clock, self.attempts = (
            base_url.rstrip("/"),
            timeout,
            http_get,
            sleep,
            clock,
            attempts,
        )
        self.log: list[dict[str, Any]] = []
        self.bodies: dict[str, bytes] = {}

    def __call__(self, path: str, params: dict[str, Any] | None = None) -> dict:
        import json

        params = {k: v for k, v in (params or {}).items() if v is not None}
        last: Exception | None = None
        for attempt in range(self.attempts):
            requested = self._clock()
            try:
                resp = self._get(
                    f"{self.base_url}{path}",
                    params=params,
                    headers={"Accept": "application/json"},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                self.log.append(
                    {
                        "path": path,
                        "params": params,
                        "attempt": attempt,
                        "status": None,
                        "error": f"{type(exc).__name__}: {exc}"[:200],
                        "requested_at": iso(requested),
                    }
                )
                last = exc
                if attempt == self.attempts - 1:
                    raise
                self._sleep(KalshiClient._retry_delay_seconds(None, attempt))
                continue
            observed = self._clock()
            raw = resp.content
            digest = sha256(raw)
            self.log.append(
                {
                    "path": path,
                    "params": params,
                    "attempt": attempt,
                    "status": resp.status_code,
                    "bytes": len(raw),
                    "sha256": digest,
                    "requested_at": iso(requested),
                    "observed_at": iso(observed),
                }
            )
            if resp.status_code < 400:
                self.bodies.setdefault(digest, raw)
                return json.loads(raw)
            retryable = resp.status_code == 429 or 500 <= resp.status_code < 600
            last = requests.HTTPError(f"HTTP {resp.status_code} for {path}")
            if not retryable or attempt == self.attempts - 1:
                raise last
            self._sleep(KalshiClient._retry_delay_seconds(resp, attempt))
        raise last or RuntimeError("unreachable")


@dataclass
class CaptureResult:
    manifest: dict[str, Any]
    rows: list[dict[str, Any]]
    raw_pages: dict[str, bytes]
    state: dict[str, Any]
    wrote_raw: bool = False
    errors: list[str] = field(default_factory=list)


def _sweep_markets(getter, status: str, min_close_ts: int | None):
    params: dict[str, Any] = {"series_ticker": SERIES, "status": status}
    if min_close_ts is not None:
        params["min_close_ts"] = min_close_ts
    return paginate(getter, "/markets", params, "markets")


def capture(
    getter: RawKalshiGetter,
    *,
    run_id: str,
    prev_state: dict[str, Any] | None,
    orderbooks: bool = True,
    clock: Callable[[], datetime] = utcnow,
) -> CaptureResult:
    """One full capture. Never raises for a provider failure: failures become explicit, labelled manifest entries."""
    started = clock()
    prev = prev_state or {"tickers": {}, "last_full_board": None}
    errors: list[str] = []
    sweeps: dict[str, Any] = {}
    markets: dict[str, dict[str, Any]] = {}
    seen_first: dict[str, str] = {}
    for status in SWEEP_STATUSES:
        floor = int((started - SETTLED_LOOKBACK).timestamp()) if status in ("closed", "settled") else None
        n0 = len(getter.log)
        sw = _sweep_markets(getter, status, floor)
        if (
            not sw.complete and not sw.items and floor is not None
        ):  # some Kalshi status/timestamp filter combinations are rejected: retry unfiltered
            sw = _sweep_markets(getter, status, None)
        sweeps[status] = {
            "complete": sw.complete,
            "pages": sw.pages_fetched,
            "items": len(sw.items),
            "failure": sw.failure_reason,
            "partial": sw.partial,
        }
        if not sw.complete:
            errors.append(f"sweep {status}: {sw.failure_reason}")
        for m in sw.items:
            t = m.get("ticker")
            if isinstance(t, str) and t.startswith(SERIES + "-"):
                markets[t] = m
                seen_first.setdefault(t, status)
        sweeps[status]["request_indices"] = [n0, len(getter.log)]
    ms_sweep = paginate(getter, "/milestones", {"type": MILESTONE_TYPE_FOOTBALL_GAME}, "milestones")
    sweeps["milestones"] = {
        "complete": ms_sweep.complete,
        "pages": ms_sweep.pages_fetched,
        "items": len(ms_sweep.items),
        "failure": ms_sweep.failure_reason,
    }
    if not ms_sweep.complete:
        errors.append(f"milestones: {ms_sweep.failure_reason}")
    starts: dict[str, tuple[datetime | None, str | None, str]] = {}
    for payload in ms_sweep.items:
        ms = milestone_from_payload(payload)
        if ms.league != "NCAAFB":
            continue
        for ev in ms.event_tickers:
            starts[ev] = (ms.start_date, ms.status, ms.milestone_id)
    observed_at = clock()
    all_complete = all(s_["complete"] for s_ in sweeps.values())
    full_due = prev.get("last_full_board") is None or (
        parse_ts(prev["last_full_board"]) and observed_at - parse_ts(prev["last_full_board"]) >= FULL_BOARD_EVERY
    )
    full_board = bool(full_due)  # a due full board is still attempted, but only a COMPLETE one resets the clock (below)
    rows: list[dict[str, Any]] = []
    new_state = {"tickers": dict(prev.get("tickers", {})), "last_full_board": prev.get("last_full_board")}
    n_ob = 0
    for ticker in sorted(markets):
        raw = markets[ticker]
        ev = raw.get("event_ticker") or ticker.rsplit("-", 1)[0]
        start, mstatus, milestone_id = starts.get(ev, (None, None, None))
        elig = eligibility(observed_at, start, mstatus, raw)
        qv, cv = quote_view(raw), contract_view(raw)
        fp = sha256(canonical({"q": qv, "c": cv, "start": iso(start), "ms": mstatus}))
        old = new_state["tickers"].get(ticker)
        changed = old is None or old.get("fingerprint") != fp
        if not (changed or full_board):
            continue
        row = {
            "schema": ROW_SCHEMA,
            "run_id": run_id,
            "ticker": ticker,
            "event_ticker": ev,
            "game_key": (getattr(parse_event_ticker(ev), "game_key", None) if parse_event_ticker(ev) else None),
            "milestone_id": milestone_id,
            "observed_at": iso(observed_at),
            "sweep_status": seen_first.get(ticker),
            "row_reason": "FIRST_SIGHTING" if old is None else ("CHANGED" if changed else "FULL_BOARD"),
            "board": ("FULL" if all_complete else "FULL_INCOMPLETE") if full_board else "CHANGED_ONLY",
            "market": raw,
            "quote": qv,
            "quote_status": "QUOTED" if has_quote(raw) else "NO_QUOTE",
            "contract_sha256": sha256(canonical(cv)),
            "fingerprint": fp,
            "kalshi_start": iso(start),
            "milestone_status": mstatus,
            **elig,
        }
        orderbook_failed = False
        if (
            orderbooks
            and elig["eligible_pregame"]
            and elig["lead_minutes"] is not None
            and elig["lead_minutes"] <= ORDERBOOK_HORIZON.total_seconds() / 60
            and n_ob < MAX_ORDERBOOKS
            and clock() - observed_at < ORDERBOOK_BUDGET
        ):
            n_ob += 1
            try:
                body = getter(f"/markets/{ticker}/orderbook", {})
                got = clock()
                pre = start is not None and got < start
                row["orderbook"] = {
                    "status": "CAPTURED" if pre else "CAPTURED_AT_OR_AFTER_START",
                    "fetched_at": iso(got),
                    "pregame": bool(pre),
                    "payload": body,
                    "sha256_canonical": sha256(canonical(body)),
                }
                if not pre:
                    row["eligible_pregame"] = False
                    row["ineligible_reasons"] = list(row["ineligible_reasons"]) + [
                        "ORDERBOOK_FETCHED_AT_OR_AFTER_START"
                    ]
            except Exception as exc:  # noqa: BLE001
                row["orderbook"] = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"[:200]}
                orderbook_failed = True
                errors.append(f"orderbook {ticker}: {type(exc).__name__}")
        else:
            row["orderbook"] = {"status": "NOT_REQUESTED", "reason": "outside_horizon_or_ineligible_or_cap"}
        rows.append(row)
        if (
            not orderbook_failed
        ):  # a failed depth fetch keeps the old fingerprint so the next run re-emits the row and retries
            new_state["tickers"][ticker] = {
                "fingerprint": fp,
                "last_row_at": iso(observed_at),
                "status": raw.get("status"),
            }
    if full_board and all_complete:
        new_state["last_full_board"] = iso(observed_at)
    missing_events = sorted({t.rsplit("-", 1)[0] for t in markets} - set(starts))
    # tickers we recorded before that no sweep returned this time (listing removed, settled > lookback, or a partial sweep): surfaced, never silent
    not_seen = sorted(
        t
        for t, v in prev.get("tickers", {}).items()
        if t not in markets and str(v.get("status")) not in ("settled", "finalized")
    )
    seen_raw = set(prev.get("raw_seen", []))
    pages = {
        sha: body for sha, body in getter.bodies.items() if sha not in seen_raw
    }  # identical pages are archived once, not per run
    new_state["raw_seen"] = (
        sorted(seen_raw | set(getter.bodies))[-4000:] if (rows or full_board) else sorted(seen_raw)[-4000:]
    )
    wrote_raw = bool(rows) or full_board
    status_counts: dict[str, int] = {}
    for m in markets.values():
        status_counts[str(m.get("status"))] = status_counts.get(str(m.get("status")), 0) + 1
    manifest = {
        "schema": RUN_SCHEMA,
        "run_id": run_id,
        "series": SERIES,
        "started_at": iso(started),
        "observed_at": iso(observed_at),
        "finished_at": iso(clock()),
        "board": ("FULL" if all_complete else "FULL_INCOMPLETE") if full_board else "CHANGED_ONLY",
        "sweeps": sweeps,
        "capture_complete": all(s["complete"] for s in sweeps.values()),
        "markets_seen": len(markets),
        "events_seen": len({t.rsplit("-", 1)[0] for t in markets}),
        "market_status_counts": status_counts,
        "rows_written": len(rows),
        "quoted_rows": sum(r["quote_status"] == "QUOTED" for r in rows),
        "no_quote_rows": sum(r["quote_status"] == "NO_QUOTE" for r in rows),
        "eligible_pregame_rows": sum(r["eligible_pregame"] for r in rows),
        "events_without_milestone_start": missing_events,
        "previously_seen_not_returned": not_seen[:200],
        "previously_seen_not_returned_count": len(not_seen),
        "orderbooks_requested": n_ob,
        "errors": errors,
        "requests": getter.log,
        "raw_pages_written": wrote_raw,
        "raw_pages_deduped": sorted(set(getter.bodies) - set(pages))[:50],
        "authenticated": False,
        "writes_orders": False,
    }
    return CaptureResult(
        manifest=manifest,
        rows=rows,
        raw_pages=pages if wrote_raw else {},
        state=new_state,
        wrote_raw=wrote_raw,
        errors=errors,
    )
