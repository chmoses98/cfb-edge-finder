#!/usr/bin/env python3
"""Milestone D hardening pass, mission items 2/3: verify Kalshi's CURRENT
published fee schedule from the two genuine official sources supplied for
this pass -- read only, no credentials.

    python scripts/validate_kalshi_fees_live.py

This dev environment's own network egress to kalshi.com is blocked by the
agent proxy (confirmed via WebFetch: EGRESS_BLOCKED). This script re-runs
from a GitHub-hosted runner (unrestricted egress) against:
  1. https://kalshi.com/regulatory/fee-schedule -- the current official
     regulatory fee-schedule PAGE.
  2. https://kalshi.com/docs/kalshi-fee-schedule.pdf -- the current
     official fee-schedule PDF (self-identifies as "Fee Schedule for
     Feb 2026").
Both are printed IN FULL (bounded only to avoid runaway CI logs), not a
short excerpt -- a prior version of this script only printed ~400-char
snippets, which is not enough to confirm an exact formula, effective
date, or a sport-specific exception. `_FEE_KEYWORD_RE`/keyword scan below
never trusts a formula automatically: a human/reviewer must read the
printed text and confirm the exact current formula before
`kalshi/executable_price.py` or `kalshi/fee_schedule.py` are ever wired
to anything but an explicitly UNVERIFIED state.

Also re-checks the older candidate URLs (mission's "do NOT blindly assume
the older [0.07/0.0175] formula is still controlling" instruction) so the
two are directly comparable in one run.
"""

from __future__ import annotations

import re

import requests

TIMEOUT_SECONDS = 30.0
MAX_PRINTED_CHARS = 20000

PRIMARY_SOURCES = (
    "https://kalshi.com/regulatory/fee-schedule",
    "https://kalshi.com/docs/kalshi-fee-schedule.pdf",
)

OLDER_CANDIDATE_URLS = (
    "https://kalshi.com/docs/fee-schedule",
    "https://docs.kalshi.com",
    "https://docs.kalshi.com/reference/fees",
    "https://docs.kalshi.com/getting-started/fees",
    "https://trading-api.kalshi.com/trade-api/v2/exchange/schedule",
    "https://api.elections.kalshi.com/trade-api/v2/exchange/schedule",
)

_FEE_KEYWORD_RE = re.compile(r"fee", re.IGNORECASE)
_CFB_KEYWORD_RE = re.compile(
    r"KXNCAAFGAME|KXNCAAFSPREAD|KXNCAAFTOTAL|NCAA\s*Football|college\s*football", re.IGNORECASE
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    """Deliberately minimal: no new HTML-parsing dependency (bs4) for a
    one-off verification script -- strips tags and collapses whitespace,
    good enough for a human to read the resulting text against."""
    text = _TAG_RE.sub(" ", html)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def _extract_pdf_text(content: bytes) -> str | None:
    try:
        from io import BytesIO

        from pypdf import PdfReader
    except ImportError:
        return None
    reader = PdfReader(BytesIO(content))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _print_bounded(label: str, text: str) -> None:
    print(f"\n--- {label} ({len(text)} chars) ---")
    if len(text) > MAX_PRINTED_CHARS:
        print(text[:MAX_PRINTED_CHARS])
        print(f"...[TRUNCATED, {len(text) - MAX_PRINTED_CHARS} more chars]...")
    else:
        print(text)


def _report_keyword_hits(text: str) -> None:
    fee_hits = len(_FEE_KEYWORD_RE.findall(text))
    cfb_hits = _CFB_KEYWORD_RE.findall(text)
    print(f"'fee'-keyword mentions: {fee_hits}")
    print(f"CFB-specific-exception keyword hits: {cfb_hits if cfb_hits else 'NONE'}")


def _fetch_primary_page(url: str) -> None:
    print(f"\n=== PRIMARY SOURCE: GET {url} ===")
    try:
        resp = requests.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as exc:
        print(f"REQUEST FAILED: {exc.__class__.__name__}: {exc}")
        return
    content_type = resp.headers.get("content-type", "?")
    print(f"HTTP {resp.status_code}, content-type={content_type!r}")
    if resp.status_code != 200:
        print("Not usable (non-200).")
        return

    if url.endswith(".pdf") or "pdf" in content_type.lower():
        pdf_text = _extract_pdf_text(resp.content)
        if pdf_text is None:
            print(f"pypdf not importable -- cannot extract PDF text. Raw byte length: {len(resp.content)}")
            print("Install with: pip install pypdf")
            return
        _print_bounded("Extracted PDF text", pdf_text)
        _report_keyword_hits(pdf_text)
    else:
        text = _strip_html(resp.text)
        _print_bounded("Extracted page text", text)
        _report_keyword_hits(text)


def _fetch_older_candidate(url: str) -> None:
    print(f"\n--- (older candidate) GET {url} ---")
    try:
        resp = requests.get(url, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        print(f"REQUEST FAILED: {exc.__class__.__name__}: {exc}")
        return
    print(f"HTTP {resp.status_code}, content-type={resp.headers.get('content-type', '?')!r}")
    if resp.status_code != 200:
        print("Not usable (non-200).")
        return
    body = resp.text
    fee_mentions = len(_FEE_KEYWORD_RE.findall(body))
    print(f"Body length: {len(body)} chars, 'fee'-keyword mentions: {fee_mentions}")
    if fee_mentions > 0:
        match = _FEE_KEYWORD_RE.search(body)
        if match is not None:
            start = max(0, match.start() - 200)
            end = min(len(body), match.start() + 400)
            print(f"Excerpt around first 'fee' mention:\n{body[start:end]!r}")


def main() -> int:
    print("=== Kalshi fee-schedule verification (live, read-only, no credentials) ===")
    print("Mission constraint: do NOT blindly assume the older 0.07/0.0175 formula is still")
    print("controlling -- verify the current schedule directly from the two sources below.")

    for url in PRIMARY_SOURCES:
        _fetch_primary_page(url)

    print("\n=== Older candidate URLs (for direct comparison against the primary sources above) ===")
    for url in OLDER_CANDIDATE_URLS:
        _fetch_older_candidate(url)

    print("\n=== SUMMARY ===")
    print(
        "A human/reviewer must read the full extracted text printed above and manually confirm "
        "the exact current formula, effective date, and any CFB-specific exception before "
        "kalshi/fee_schedule.py is wired to anything but an explicitly UNVERIFIED state -- this "
        "script does not parse or trust a formula automatically."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
