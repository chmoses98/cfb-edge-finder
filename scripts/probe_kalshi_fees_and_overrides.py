#!/usr/bin/env python3
"""Probe the CURRENT official Kalshi fee rules and the live CFB event
fee-override surface.

    python scripts/probe_kalshi_fees_and_overrides.py

Read-only, credential-free, run from a GitHub Actions runner -- this
repository's dev environment has no network egress to Kalshi's API hosts
OR to docs.kalshi.com (organization policy: the agent proxy answers 403 to
CONNECT, and WebFetch reports EGRESS_BLOCKED for docs.kalshi.com).

TWO QUESTIONS, BOTH OF WHICH MUST BE ANSWERED FROM PRIMARY SOURCES:

  A. What are Kalshi's CURRENT fee mechanics, verbatim? The catalog
     currently rounds a quadratic model fee up to a whole cent and labels
     the result an estimated per-contract fee. Kalshi's current docs
     distinguish a model/trade fee, a rounding fee, a rebate/accumulator
     and user balance precision -- so a single rounded-up number cannot
     honestly be called "the fee". This probe captures the real rules and
     every worked example so the artifact can say exactly what each field
     represents.

  B. Do live CFB EVENTS carry `fee_type_override` / `fee_multiplier_override`?
     Kalshi's current Event schema documents these as overrides layered on
     the parent series fee. If any CFB event sets one, the catalog's
     series-only fee lookup is publishing the wrong fee for those
     contracts. The event object is already in the discovery path, so
     honouring the precedence costs no extra request -- but it has to be
     measured before it can be claimed either way.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from typing import Any

import requests

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
TIMEOUT_SECONDS = 25.0
PAGE_LIMIT = 200
MAX_PAGES = 200

FEE_ROUNDING_DOC = "https://docs.kalshi.com/getting_started/fee_rounding"
FEE_SCHEDULE_PDF = "https://kalshi.com/docs/kalshi-fee-schedule.pdf"
FEE_REGULATORY_PAGE = "https://kalshi.com/regulatory/fee-schedule"

MAX_DOC_CHARS = 30000


def _hdr(text: str) -> None:
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


# --------------------------------------------------------------------------
# A. the official fee rules
# --------------------------------------------------------------------------
def fetch_official_fee_rules() -> None:
    _hdr("A. CURRENT OFFICIAL KALSHI FEE RULES (primary sources, verbatim)")

    # docs.kalshi.com is a normal docs site and answers plain requests.
    for url in (FEE_ROUNDING_DOC,):
        print(f"\n### GET {url}")
        try:
            resp = requests.get(
                url,
                timeout=TIMEOUT_SECONDS,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
        except requests.RequestException as exc:
            print(f"  REQUEST_ERROR {type(exc).__name__}: {exc}")
            continue
        print(f"  HTTP {resp.status_code}  {len(resp.text)} bytes")
        if resp.status_code != 200:
            print(f"  body head: {resp.text[:400]!r}")
            continue
        text = _html_to_text(resp.text)
        print(f"\n--- extracted text ({len(text)} chars) ---")
        print(text[:MAX_DOC_CHARS])
        if len(text) > MAX_DOC_CHARS:
            print(f"...[TRUNCATED {len(text) - MAX_DOC_CHARS} chars]...")

    # The PDF and the regulatory page sat behind Cloudflare on a previous
    # mission (HTTP 429 on the first request from a fresh runner). Try
    # anyway and report honestly; never fabricate schedule content.
    for url in (FEE_SCHEDULE_PDF, FEE_REGULATORY_PAGE):
        print(f"\n### GET {url}")
        try:
            resp = requests.get(
                url,
                timeout=TIMEOUT_SECONDS,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
                    )
                },
            )
        except requests.RequestException as exc:
            print(f"  REQUEST_ERROR {type(exc).__name__}: {exc}")
            continue
        print(f"  HTTP {resp.status_code}  {len(resp.content)} bytes  "
              f"content-type={resp.headers.get('Content-Type')}")
        if resp.status_code != 200:
            print("  (blocked or unavailable -- reported, not guessed)")
            continue
        if "pdf" in (resp.headers.get("Content-Type") or "").lower():
            try:
                from io import BytesIO

                from pypdf import PdfReader

                reader = PdfReader(BytesIO(resp.content))
                pdf_text = "\n".join((page.extract_text() or "") for page in reader.pages)
                print(f"\n--- PDF text ({len(pdf_text)} chars, {len(reader.pages)} pages) ---")
                print(pdf_text[:MAX_DOC_CHARS])
            except Exception as exc:  # noqa: BLE001
                print(f"  PDF parse failed: {type(exc).__name__}: {exc}")
        else:
            print(_html_to_text(resp.text)[:MAX_DOC_CHARS])


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
        .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
        .replace("&times;", "x").replace("&minus;", "-")
    )
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


# --------------------------------------------------------------------------
# B. live CFB event fee overrides
# --------------------------------------------------------------------------
def _get(path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    for attempt in range(4):
        try:
            resp = requests.get(
                f"{BASE_URL}{path}", params=clean,
                headers={"Accept": "application/json"}, timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            if attempt == 3:
                return -1, None
            time.sleep(min(2**attempt, 8))
            continue
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            if attempt == 3:
                return resp.status_code, None
            time.sleep(min(2**attempt, 8))
            continue
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, None
    return -1, None


def _paginate(path: str, params: dict[str, Any], key: str) -> tuple[list[dict], bool]:
    items: list[dict] = []
    cursor = None
    seen: set[str] = set()
    for _ in range(MAX_PAGES):
        p = dict(params, limit=PAGE_LIMIT)
        if cursor:
            p["cursor"] = cursor
        status, body = _get(path, p)
        if status != 200 or not isinstance(body, dict):
            return items, False
        items.extend(i for i in (body.get(key) or []) if isinstance(i, dict))
        cursor = body.get("cursor") or None
        if not cursor or cursor in seen:
            return items, not cursor
        seen.add(cursor)
    return items, False


def probe_event_fee_overrides() -> None:
    _hdr("B. LIVE CFB EVENT FEE-OVERRIDE SURFACE")

    series, complete = _paginate("/series", {"category": "Sports"}, "series")
    print(f"GET /series?category=Sports -> {len(series)} series (complete={complete})")

    cfb_series: dict[str, dict] = {}
    for s in series:
        ticker = str(s.get("ticker") or "")
        title = str(s.get("title") or "").upper()
        tags = [str(t).upper() for t in (s.get("tags") or [])]
        if (
            ticker.startswith(("KXNCAAF", "KXCFB", "KXCFP"))
            or "COLLEGE FOOTBALL" in title
            or ("FOOTBALL" in tags and "NCAA" in ticker)
        ):
            cfb_series[ticker] = s
    print(f"CFB series: {len(cfb_series)}")

    print("\n--- series-level fee metadata ---")
    s_types = Counter(str(s.get("fee_type")) for s in cfb_series.values())
    s_mults = Counter(str(s.get("fee_multiplier")) for s in cfb_series.values())
    print(f"  fee_type       : {dict(s_types)}")
    print(f"  fee_multiplier : {dict(s_mults)}")
    missing_fee = [t for t, s in cfb_series.items() if s.get("fee_type") in (None, "")]
    print(f"  series MISSING fee_type: {len(missing_fee)} {missing_fee[:10]}")

    # Every open CFB event, checked for the documented override fields.
    print("\n--- sweeping open events for override fields ---")
    events: list[dict] = []
    for ticker in sorted(cfb_series):
        evs, ok = _paginate("/events", {"series_ticker": ticker, "status": "open"}, "events")
        if not ok:
            print(f"  !! incomplete event sweep for {ticker}")
        for e in evs:
            e["_series_ticker"] = ticker
            events.append(e)
    print(f"open CFB events swept: {len(events)}")

    # Does the field appear AT ALL in the payload, and with what value?
    type_override_present = [e for e in events if "fee_type_override" in e]
    mult_override_present = [e for e in events if "fee_multiplier_override" in e]
    type_override_set = [e for e in events if e.get("fee_type_override") not in (None, "")]
    mult_override_set = [e for e in events if e.get("fee_multiplier_override") not in (None, "")]

    print(f"\n  events with the KEY 'fee_type_override' present      : {len(type_override_present)}")
    print(f"  events with the KEY 'fee_multiplier_override' present : {len(mult_override_present)}")
    print(f"  events with fee_type_override SET (non-null)         : {len(type_override_set)}")
    print(f"  events with fee_multiplier_override SET (non-null)    : {len(mult_override_set)}")

    changed = 0
    print("\n--- events whose EFFECTIVE fee differs from its series ---")
    for e in events:
        series_meta = cfb_series.get(e["_series_ticker"], {})
        s_type, s_mult = series_meta.get("fee_type"), series_meta.get("fee_multiplier")
        o_type = e.get("fee_type_override")
        o_mult = e.get("fee_multiplier_override")
        eff_type = o_type if o_type not in (None, "") else s_type
        eff_mult = o_mult if o_mult not in (None, "") else s_mult
        if (eff_type, eff_mult) != (s_type, s_mult):
            changed += 1
            if changed <= 25:
                print(
                    f"  {str(e.get('event_ticker')):44} series({s_type},{s_mult}) "
                    f"-> effective({eff_type},{eff_mult})"
                )
    print(f"\n  CFB events whose effective fee CHANGES due to an override: {changed}")

    # Full key inventory of an event, so a silently-renamed field is visible.
    if events:
        keys: Counter[str] = Counter()
        for e in events:
            keys.update(k for k in e if not k.startswith("_"))
        print(f"\n--- event key inventory across {len(events)} events ---")
        for k, n in sorted(keys.items()):
            example = next((repr(e[k])[:60] for e in events if e.get(k) not in (None, "", [], {})), "<always empty>")
            print(f"  {k:30} {n:5}/{len(events)}  e.g. {example}")
        fee_keys = sorted({k for k in keys if "fee" in k.lower()})
        print(f"\n  fee-related event keys observed: {fee_keys or 'NONE'}")
        print("\n--- FULL RAW JSON of one open CFB event ---")
        printable = {k: v for k, v in events[0].items() if not k.startswith("_")}
        print(json.dumps(printable, indent=2, sort_keys=True)[:2000])

    # Series payload too -- the fee fields we depend on.
    if cfb_series:
        print("\n--- FULL RAW JSON of one CFB series (fee fields) ---")
        sample = next(iter(cfb_series.values()))
        print(json.dumps({k: v for k, v in sample.items() if "fee" in k.lower() or k in ("ticker", "title")},
                         indent=2, sort_keys=True))

    # Does a MARKET carry fee fields of its own?
    print("\n--- do markets carry their own fee metadata? ---")
    if events:
        et = str(events[0].get("event_ticker"))
        mk, _ok = _paginate("/markets", {"event_ticker": et}, "markets")
        if mk:
            fee_on_market = sorted({k for k in mk[0] if "fee" in k.lower()})
            print(f"  fee-related market keys on {mk[0].get('ticker')}: {fee_on_market or 'NONE'}")


def main() -> int:
    print("Kalshi fee-rules + event-override probe")
    fetch_official_fee_rules()
    probe_event_fee_overrides()
    _hdr("PROBE COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
