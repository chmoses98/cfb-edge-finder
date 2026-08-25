"""Milestone D hardening pass, mission items 2-4: a research-only, cents-
safe Kalshi fee calculation utility, with explicit provenance -- built to
supersede `kalshi/executable_price.py`'s loudly-unverified placeholder
once (if ever) a current schedule is actually confirmed.

*** VERIFICATION ATTEMPT: GENUINELY FAILED, NOT SKIPPED ***
This pass was given two specific official sources to verify against:
  - https://kalshi.com/regulatory/fee-schedule
  - https://kalshi.com/docs/kalshi-fee-schedule.pdf (self-identifies as
    "Fee Schedule for Feb 2026", effective Feb 5, 2026)
and an explicit instruction: do NOT blindly assume the older 0.07/0.0175
formula is still controlling -- verify the CURRENT schedule directly.

Two independent, genuine live attempts were made from a GitHub Actions
runner (this dev sandbox's own egress to kalshi.com is blocked by
network policy, confirmed via WebFetch: EGRESS_BLOCKED):
  1. Plain HTTP GET, with browser-like headers and a 3-attempt/5s+15s
     backoff retry (scripts/validate_kalshi_fees_live.py, GH Actions run
     32848440217). Both sources returned HTTP 429 on EVERY attempt,
     including the very first request from a fresh runner.
  2. A real headless browser (Chromium via Playwright), warmed up against
     https://kalshi.com/ first so any bot-protection challenge could
     clear exactly like a human visitor's would
     (scripts/validate_kalshi_fees_browser_live.py, GH Actions run
     32848750358). The warmup page's own title rendered as "Vercel
     Security Checkpoint" -- confirming this is Kalshi's own active bot-
     mitigation product, not a transient rate limit -- and both sources
     STILL returned HTTP 429 afterward.

Both attempts are genuine, legitimate reads of Kalshi's own public
regulatory disclosure pages (no authentication bypassed, no scraping of
non-public data) and both were consistently blocked. Escalating further
(CAPTCHA-solving, residential proxies, etc.) would cross from "a
legitimate retry" into evading an access control, which this mission
does not authorize. The two current official sources therefore remain
GENUINELY UNVERIFIED after real, repeated, well-evidenced effort -- this
is the honest answer this module's provenance records, not silently
assumed away.

*** WHY THE OLDER FORMULA IS STILL USED HERE, BUT NEVER AS "CURRENT" ***
Per the mission's own explicit instruction, this module does NOT claim
the older `round up(0.07 x C x P x (1-P))` / maker `round up(0.0175 x C
x P x (1-P))` formula is Kalshi's current, controlling Feb-2026 schedule
-- `LEGACY_UNVERIFIED_TAKER_SCHEDULE`/`LEGACY_UNVERIFIED_MAKER_SCHEDULE`
below are both stamped `verified=False` and carry the exact evidence
trail above in their own `notes` field. They exist so this module's
CENTS-SAFE CALCULATION MACHINERY (the genuinely hard, bug-prone part --
see `calculate_fee_cents` below) is real, tested, and ready to be pointed
at a verified constant the instant one is confirmed, rather than
inventing that machinery from scratch under time pressure later. No
CFB-specific exception could be determined either, for the same reason:
the current schedule that would need to be searched for one was never
reachable (mission item 3).

*** WHY CENTS, NOT FLOATS ***
`Decimal` throughout, at cent-level precision, is used specifically
because the mission calls out "use cents/dollars carefully to avoid unit
bugs" -- a plain float formula like the one in
`executable_price.py.expected_fee_drag` is fine for a probability-scale
research estimate, but real MONEY-facing fee cents must round
deterministically (ROUND_CEILING, matching "round up" from both the
older schedule's own documented formula and standard exchange-fee
convention) rather than accumulate float error.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal


@dataclass(frozen=True)
class FeeScheduleVersion:
    """Provenance for exactly which fee schedule a calculation used --
    mission item 4's "authoritative provenance: fee schedule version/
    effective date, source, fee type, calculation version" requirement."""

    version_label: str
    fee_type: str
    """"taker" (immediately-matched) or "maker"."""
    rate: Decimal
    effective_date: str | None
    """ISO date string, or None if genuinely unknown (as here)."""
    source: str
    verified: bool
    """True only if this rate was read and confirmed directly from a
    live, currently-reachable official Kalshi source THIS session. Never
    set True on inference, precedent, or a related sport's behavior."""
    calculation_version: str
    notes: str


CALCULATION_VERSION = "fee_schedule_v1_cents_decimal_ceiling"

_VERIFICATION_EVIDENCE = (
    "UNVERIFIED as the current Feb 2026 schedule: both "
    "https://kalshi.com/regulatory/fee-schedule and "
    "https://kalshi.com/docs/kalshi-fee-schedule.pdf returned HTTP 429 on every attempt from a "
    "GitHub Actions runner -- (1) plain HTTP with browser-like headers and 3 retries (run "
    "32848440217), and (2) a real headless-browser fetch warmed up against kalshi.com first, whose "
    "own page title rendered 'Vercel Security Checkpoint' (run 32848750358), confirming active "
    "bot-mitigation rather than a transient rate limit. This is the OLDER, previously-documented "
    "general-schedule formula -- NOT confirmed as still controlling. No CFB-specific exception "
    "(KXNCAAFGAME/KXNCAAFSPREAD/KXNCAAFTOTAL) could be determined either, since the current "
    "schedule that would need to be searched for one was never reachable."
)

LEGACY_UNVERIFIED_TAKER_SCHEDULE = FeeScheduleVersion(
    version_label="legacy_unverified_taker_v1",
    fee_type="taker",
    rate=Decimal("0.07"),
    effective_date=None,
    source="an older official Kalshi fee schedule (exact URL not preserved in this session's evidence chain)",
    verified=False,
    calculation_version=CALCULATION_VERSION,
    notes=_VERIFICATION_EVIDENCE,
)

LEGACY_UNVERIFIED_MAKER_SCHEDULE = FeeScheduleVersion(
    version_label="legacy_unverified_maker_v1",
    fee_type="maker",
    rate=Decimal("0.0175"),
    effective_date=None,
    source="an older official Kalshi fee schedule (exact URL not preserved in this session's evidence chain)",
    verified=False,
    calculation_version=CALCULATION_VERSION,
    notes=(
        _VERIFICATION_EVIDENCE
        + " Not used anywhere in this research ledger: the capture pipeline is read-only and never "
        "places orders, so the maker/taker distinction is moot for a research-only fee estimate -- "
        "the taker rate is used as the conservative reference throughout."
    ),
)


def calculate_fee_cents(price_cents: int, contracts: int, schedule: FeeScheduleVersion) -> int:
    """`round up(rate x C x P x (1-P))`, computed and rounded entirely in
    integer cents via `Decimal` -- never a plain float -- and rounded UP
    (ROUND_CEILING) to the next whole cent, matching the documented
    formula's own "round up" behavior. `price_cents` must be an integer
    in [1, 99] (Kalshi's own tradeable price range -- a contract can
    never price at exactly 0 or 100 cents while still trading); raises
    ValueError outside that range or for `contracts < 1`, rather than
    silently producing a meaningless fee for an impossible price/quantity."""
    if not 1 <= price_cents <= 99:
        raise ValueError(f"price_cents must be in [1, 99], got {price_cents!r}")
    if contracts < 1:
        raise ValueError(f"contracts must be >= 1, got {contracts!r}")

    p = Decimal(price_cents) / Decimal(100)
    raw_dollars = schedule.rate * Decimal(contracts) * p * (Decimal(1) - p)
    raw_cents = raw_dollars * Decimal(100)
    return int(raw_cents.to_integral_value(rounding=ROUND_CEILING))


def calculate_fee_dollars(price_cents: int, contracts: int, schedule: FeeScheduleVersion) -> Decimal:
    """Same calculation as `calculate_fee_cents`, returned in dollars
    (still a `Decimal`, never a float) for callers that want the money-
    scale amount directly rather than cents."""
    return Decimal(calculate_fee_cents(price_cents, contracts, schedule)) / Decimal(100)
