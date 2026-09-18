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

  C. Does the CORRECTED fee code produce an honest fee block on the REAL
     surface? Fixtures cannot answer this: the first production run is
     what found `quadratic_with_maker_fees`, and a fee that is null on a
     third of the live menu passes every test written against a fixture
     that does not contain it. So this probe builds the actual catalog
     and audits the published `fee` blocks -- how many are computable,
     where each effective fee came from, and what the numbers are on a
     named live contract, priced by hand from the exchange's own formula
     as a cross-check.
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


# --------------------------------------------------------------------------
# C. the published fee block, on the live surface
# --------------------------------------------------------------------------


def audit_published_fee_blocks() -> None:
    """Build the real catalog and check what its `fee` blocks actually say.

    The point is not that the arithmetic is right -- unit tests cover
    that against Kalshi's worked examples. The point is that the fee
    metadata RESOLVES across the whole live surface: a fail-closed design
    is only safe if it does not fail closed on everything."""
    import math

    from cfb_edge_finder.catalog.artifacts import build_catalog
    from cfb_edge_finder.catalog.discovery import MarketDiscovery
    from cfb_edge_finder.data.kalshi_client import KalshiClient

    _hdr("C. THE PUBLISHED FEE BLOCK, MEASURED ON THE LIVE SURFACE")

    client = KalshiClient()
    started = time.time()
    run = MarketDiscovery(client.get_json).run()
    catalog = build_catalog(run, include_markets=True)
    games = catalog.get("games") or []
    print(f"built the real catalog: {len(games)} games in {time.time() - started:.0f}s")

    blocks = [m["fee"] for g in games for m in (g.get("markets") or [])]
    print(f"contracts carrying a fee block: {len(blocks)}")
    if not blocks:
        print("  NO CONTRACTS -- cannot audit fees")
        return

    computable = [b for b in blocks if b["model_trade_fee_at_yes_ask"] is not None]
    priceable = [b for b in blocks if b["basis_yes_ask"] is not None]
    print("\n--- is the fee computable? ---")
    print(f"  support distribution : {dict(Counter(b['support'] for b in blocks))}")
    print(f"  effective fee source : {dict(Counter(b['source'] for b in blocks))}")
    print(f"  effective model      : {dict(Counter(str(b['model']) for b in blocks))}")
    print(f"  effective multiplier : {dict(Counter(str(b['multiplier']) for b in blocks))}")
    print(f"  maker_fee_applies    : {dict(Counter(str(b['maker_fee_applies']) for b in blocks))}")
    print(f"  computable at the ask: {len(computable)}/{len(priceable)} contracts that HAVE an ask")
    reasons = Counter(str(b["unavailable_reason"]) for b in blocks if b["unavailable_reason"])
    print(f"  unavailable_reasons  : {dict(reasons) or 'NONE'}")

    # A null fee where an ask exists is the production defect this pass
    # corrects. It must be zero, or the reason must be a real data gap.
    null_with_ask = [b for b in priceable if b["model_trade_fee_at_yes_ask"] is None]
    print(f"\n  contracts with an ask but NO fee: {len(null_with_ask)}")
    for b in null_with_ask[:5]:
        print(f"    model={b['model']!r} reason={b['unavailable_reason']!r}")

    print("\n--- is the published fee NEVER dressed up as a net fee? ---")
    print(f"  is_net_fee values    : {dict(Counter(str(b['is_net_fee']) for b in blocks))}")
    print(f"  excludes (distinct)  : {sorted({tuple(b['excludes']) for b in blocks})}")

    # A named worked example off the live surface, recomputed by hand from
    # the exchange's own formula so the published number is checkable
    # rather than merely present.
    #
    # Degenerate quotes are skipped for the illustration: at an ask of
    # $1.00 (or $0.00) the quadratic schedule gives P(1-P) = 0 and the
    # trade fee is exactly $0.00, which is correct but shows nothing. They
    # are counted separately below, because they are the sharpest possible
    # case for why the mid figure must not masquerade as the trade fee.
    print("\n--- worked examples from named LIVE contracts ---")
    shown = 0
    candidates = [
        (g, m) for g in games for m in (g.get("markets") or [])
        if m["fee"]["model_trade_fee_at_yes_ask"] is not None
        and m["fee"]["basis_yes_ask"] is not None
        and 0.0 < m["fee"]["basis_yes_ask"] < 1.0
    ]
    # One of each live schedule, and one longshot, where the old whole-cent
    # rounding did the most damage.
    wanted = [
        ("quadratic", lambda b: 0.45 <= b["basis_yes_ask"] <= 0.55),
        ("quadratic_with_maker_fees", lambda b: 0.45 <= b["basis_yes_ask"] <= 0.55),
        (None, lambda b: b["basis_yes_ask"] <= 0.05),
    ]
    for want_model, price_ok in wanted:
        pick = next(
            ((g, m) for g, m in candidates
             if (want_model is None or m["fee"]["model"] == want_model) and price_ok(m["fee"])),
            None,
        )
        if pick is None:
            print(f"\n  (no live contract matched model={want_model!r} in the wanted price band)")
            continue
        game, market = pick
        block = market["fee"]
        ask, mult = block["basis_yes_ask"], block["multiplier"]
        by_hand = math.ceil(mult * 0.07 * 1 * ask * (1 - ask) / 1e-6 - 1e-9) * 1e-6
        print(f"\n  {market['market_ticker']}  ({game['game_key']})")
        print(f"    {market['title'][:70]!r}")
        print(f"    yes_ask={ask}  yes_mid={block['basis_yes_mid']}  "
              f"model={block['model']}  mult={mult}  source={block['source']}  "
              f"maker_fee_applies={block['maker_fee_applies']}")
        print(f"    published fee at ASK : ${block['model_trade_fee_at_yes_ask']:.6f}   <- the headline")
        print(f"    recomputed by hand   : ${by_hand:.6f}")
        if block["model_trade_fee_at_yes_mid"] is not None:
            print(f"    published fee at MID : ${block['model_trade_fee_at_yes_mid']:.6f}   "
                  f"<- market mechanic, NOT an executable-order fee")
        # The retired helper's actual behaviour: ceil the model fee to a
        # WHOLE CENT. Recomputed here rather than hardcoded, because it
        # over- or under-states by different factors at different prices
        # and a single illustrative number would misrepresent it.
        mid = block["basis_yes_mid"]
        old_basis = mid if mid is not None else ask
        old_helper = math.ceil(mult * 0.07 * old_basis * (1 - old_basis) * 100 - 1e-9) / 100
        print(f"    old whole-cent helper : ${old_helper:.6f}   "
              f"({old_helper / block['model_trade_fee_at_yes_ask']:.2f}x the real trade fee; "
              f"it rounded to a cent AND priced off the mid {old_basis})")
        assert abs(by_hand - block["model_trade_fee_at_yes_ask"]) < 1e-9
        shown += 1
    print(f"\n  worked examples shown: {shown}")

    # The degenerate case, stated rather than hidden.
    degenerate = [
        m["fee"] for g in games for m in (g.get("markets") or [])
        if m["fee"]["basis_yes_ask"] in (0.0, 1.0)
    ]
    mid_nonzero = [
        b for b in degenerate
        if b["model_trade_fee_at_yes_mid"] not in (None, 0.0)
        and b["model_trade_fee_at_yes_ask"] == 0.0
    ]
    print(f"\n--- contracts quoted at $0.00 or $1.00: {len(degenerate)} ---")
    print("  At those asks the quadratic schedule's P(1-P) term is 0, so the")
    print("  trade fee is exactly $0.00 -- correct, and a demonstration of why")
    print("  the mid figure cannot be the headline:")
    print(f"  contracts whose fee at the ASK is $0.00 while the MID figure is not: {len(mid_nonzero)}")
    if mid_nonzero:
        b = mid_nonzero[0]
        print(f"    e.g. ask={b['basis_yes_ask']} -> ${b['model_trade_fee_at_yes_ask']:.6f}, "
              f"mid={b['basis_yes_mid']} -> ${b['model_trade_fee_at_yes_mid']:.6f}")
        print("    A consumer reading the mid figure as 'the fee' would book a cost")
        print("    that the executable order does not incur.")


# --------------------------------------------------------------------------
# D. what is inside a 0/100 book?
# --------------------------------------------------------------------------


def probe_empty_book_shape() -> None:
    """Characterize every live contract quoted $0.00 bid / $1.00 ask.

    The question is precisely: does a 100-cent spread mean "nobody is
    quoting" or "somebody is quoting 0 and 100"? Quoted SIZE answers it,
    and no amount of reasoning about prices can."""
    from cfb_edge_finder.catalog.artifacts import build_catalog
    from cfb_edge_finder.catalog.discovery import MarketDiscovery
    from cfb_edge_finder.data.kalshi_client import KalshiClient

    _hdr("D. THE SHAPE OF A 0/100 BOOK, MEASURED LIVE")

    client = KalshiClient()
    run = MarketDiscovery(client.get_json).run()
    catalog = build_catalog(run, include_markets=True)
    markets = [m for g in catalog.get("games") or [] for m in (g.get("markets") or [])]
    print(f"contracts in the live catalog: {len(markets)}")

    def size(m: dict[str, Any], key: str) -> Any:
        return m.get(key)

    zero_hundred = [
        m for m in markets
        if m.get("yes_bid") in (0.0, 0) and m.get("yes_ask") in (1.0, 1)
    ]
    print(f"\ncontracts quoted YES bid $0.00 / YES ask $1.00 : {len(zero_hundred)}")
    if not zero_hundred:
        print("  none on this capture -- nothing to characterize")
        return

    def bucket(values: list[Any]) -> dict[str, int]:
        out: Counter[str] = Counter()
        for v in values:
            if v is None:
                out["null"] += 1
            elif float(v) == 0.0:
                out["zero"] += 1
            else:
                out["positive"] += 1
        return dict(out)

    print("\n--- quoted size and activity on those contracts ---")
    for key in ("yes_bid_size", "yes_ask_size", "no_bid_size", "no_ask_size",
                "volume", "volume_24h", "open_interest", "liquidity_dollars"):
        print(f"  {key:20} {bucket([size(m, key) for m in zero_hundred])}")

    no_size_at_all = [
        m for m in zero_hundred
        if not any(float(m.get(k) or 0.0) > 0.0
                   for k in ("yes_bid_size", "yes_ask_size", "no_bid_size", "no_ask_size"))
    ]
    some_size = [m for m in zero_hundred if m not in no_size_at_all]
    print(f"\n  0/100 contracts with NO positive size on any side : {len(no_size_at_all)}")
    print(f"  0/100 contracts with SOME positive quoted size     : {len(some_size)}")

    print("\n--- what the NO side says on those contracts ---")
    for key in ("no_bid", "no_ask"):
        vals = Counter(str(m.get(key)) for m in zero_hundred)
        print(f"  {key:10} {dict(vals)}")

    print("\n--- FULL RAW PAYLOAD of one 0/100 contract ---")
    sample = no_size_at_all[0] if no_size_at_all else zero_hundred[0]
    printable = {k: v for k, v in sample.items() if k not in ("raw", "mechanics", "fee")}
    print(json.dumps(printable, indent=2, sort_keys=True, default=str)[:2200])
    print("\n  its mechanics block:")
    print(json.dumps(sample.get("mechanics"), indent=2, sort_keys=True, default=str))
    if some_size:
        print("\n--- and one 0/100 contract that DOES carry quoted size ---")
        s2 = some_size[0]
        print(f"  {s2['market_ticker']}  {s2.get('title', '')[:60]!r}")
        print(f"    yes {s2.get('yes_bid')}/{s2.get('yes_ask')}  "
              f"sizes {s2.get('yes_bid_size')}/{s2.get('yes_ask_size')}  "
              f"no {s2.get('no_bid')}/{s2.get('no_ask')}  "
              f"sizes {s2.get('no_bid_size')}/{s2.get('no_ask_size')}  "
              f"vol={s2.get('volume')} oi={s2.get('open_interest')}")

    # For contrast: what does a NORMAL contract's size look like? Without
    # this, "size is zero" might just mean the catalog never captures size.
    normal = [
        m for m in markets
        if m.get("yes_bid") not in (None, 0.0, 0) and m.get("yes_ask") not in (None, 1.0, 1)
    ]
    print(f"\n--- contrast: {len(normal)} contracts with an ordinary quote ---")
    for key in ("yes_bid_size", "yes_ask_size", "no_bid_size", "no_ask_size"):
        print(f"  {key:20} {bucket([size(m, key) for m in normal[:4000]])}  (first 4000)")

    # How widespread is a one-sided or absent size generally? The fix has
    # to hold for those too, not only for the 0/100 shape.
    print("\n--- how many contracts lack a positive size on ONE side? ---")
    no_bid_size = [m for m in markets if not float(m.get("yes_bid_size") or 0.0) > 0.0]
    no_ask_size = [m for m in markets if not float(m.get("yes_ask_size") or 0.0) > 0.0]
    print(f"  no positive yes_bid_size : {len(no_bid_size)} / {len(markets)}")
    print(f"  no positive yes_ask_size : {len(no_ask_size)} / {len(markets)}")
    print(f"  no positive size on EITHER: "
          f"{len([m for m in markets if m in no_bid_size and m in no_ask_size])}")


def main() -> int:
    print("Kalshi fee-rules + event-override probe")
    fetch_official_fee_rules()
    probe_event_fee_overrides()
    try:
        audit_published_fee_blocks()
    except Exception as exc:  # a probe reports its own failure; it never hides it
        _hdr(f"C. FAILED: {type(exc).__name__}: {exc}")
        raise
    try:
        probe_empty_book_shape()
    except Exception as exc:
        _hdr(f"D. FAILED: {type(exc).__name__}: {exc}")
        raise
    _hdr("PROBE COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
