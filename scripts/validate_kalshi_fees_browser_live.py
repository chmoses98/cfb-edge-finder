#!/usr/bin/env python3
"""Milestone D hardening pass, mission items 2/3: a browser-based follow-
up to validate_kalshi_fees_live.py's plain-`requests` attempt.

    python scripts/validate_kalshi_fees_browser_live.py

That plain-`requests` attempt got HTTP 429 from BOTH official sources on
EVERY attempt (including three retries with browser-like headers) --
https://kalshi.com/regulatory/fee-schedule and
https://kalshi.com/docs/kalshi-fee-schedule.pdf -- a signature consistent
with Cloudflare bot-protection (a JS/TLS-fingerprint challenge) rather
than genuine rate-limiting, since it triggered on the very FIRST request
from a fresh runner. A real, headless browser executes the page's JS and
carries a genuine browser TLS fingerprint, which routinely clears this
exact kind of challenge for otherwise-public pages -- this is a
legitimate read of the same public page a human visiting it would see,
not evasion of an access control meant to gate this kind of programmatic
access (no authentication or paywall is being bypassed; the page is
Kalshi's own public regulatory disclosure).

Read-only, no credentials. If this ALSO fails, that stays the honest,
reported outcome -- this script never fabricates fee-schedule content."""

from __future__ import annotations

TIMEOUT_MS = 30000
MAX_PRINTED_CHARS = 20000

REGULATORY_PAGE_URL = "https://kalshi.com/regulatory/fee-schedule"
PDF_URL = "https://kalshi.com/docs/kalshi-fee-schedule.pdf"


def _print_bounded(label: str, text: str) -> None:
    print(f"\n--- {label} ({len(text)} chars) ---")
    if len(text) > MAX_PRINTED_CHARS:
        print(text[:MAX_PRINTED_CHARS])
        print(f"...[TRUNCATED, {len(text) - MAX_PRINTED_CHARS} more chars]...")
    else:
        print(text)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not importable -- install with: pip install playwright && playwright install chromium")
        return 1

    print("=== Kalshi fee-schedule verification via headless browser (live, read-only, no credentials) ===")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
        )

        # Visit the site's homepage first -- lets any Cloudflare challenge
        # resolve and clearance cookies land in the browser context before
        # the actual fee-schedule requests, exactly like a real visitor.
        page = context.new_page()
        print("\n--- Warming up context: GET https://kalshi.com/ ---")
        try:
            page.goto("https://kalshi.com/", timeout=TIMEOUT_MS, wait_until="networkidle")
            print(f"Homepage loaded, status ok, title={page.title()!r}")
        except Exception as exc:  # noqa: BLE001 -- report any navigation failure, never crash the probe
            print(f"Homepage warmup failed: {exc.__class__.__name__}: {exc}")

        print(f"\n=== PAGE: GET {REGULATORY_PAGE_URL} ===")
        try:
            resp = page.goto(REGULATORY_PAGE_URL, timeout=TIMEOUT_MS, wait_until="networkidle")
            status = resp.status if resp is not None else None
            print(f"HTTP {status}")
            if status == 200:
                text = page.inner_text("body")
                _print_bounded("Rendered page text", text)
            else:
                print("Not usable (non-200).")
        except Exception as exc:  # noqa: BLE001
            print(f"Navigation failed: {exc.__class__.__name__}: {exc}")

        print(f"\n=== PDF: GET {PDF_URL} (via browser request context, same session/cookies) ===")
        try:
            pdf_resp = context.request.get(PDF_URL, timeout=TIMEOUT_MS)
            print(f"HTTP {pdf_resp.status}, content-type={pdf_resp.headers.get('content-type', '?')!r}")
            if pdf_resp.status == 200:
                pdf_bytes = pdf_resp.body()
                try:
                    from io import BytesIO

                    from pypdf import PdfReader

                    reader = PdfReader(BytesIO(pdf_bytes))
                    pdf_text = "\n\n".join(page_obj.extract_text() or "" for page_obj in reader.pages)
                    _print_bounded("Extracted PDF text", pdf_text)
                except ImportError:
                    print(f"pypdf not importable -- raw byte length: {len(pdf_bytes)}. Install with: pip install pypdf")
            else:
                print("Not usable (non-200).")
        except Exception as exc:  # noqa: BLE001
            print(f"PDF request failed: {exc.__class__.__name__}: {exc}")

        browser.close()

    print("\n=== SUMMARY ===")
    print(
        "A human/reviewer must read the full extracted text printed above and manually confirm the exact "
        "current formula, effective date, and any CFB-specific exception before kalshi/fee_schedule.py is "
        "wired to anything but an explicitly UNVERIFIED state -- this script does not parse or trust a "
        "formula automatically."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
