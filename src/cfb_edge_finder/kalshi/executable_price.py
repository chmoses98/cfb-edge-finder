"""Fee-aware net EV -- shape only, NOT money-facing until a fee schedule is verified.

STATUS: `fee_rate` has NO default in either function below. This is
deliberate: an earlier version of this module defaulted to a placeholder
0.07 constant, which meant any caller that forgot to pass a fee rate would
silently get a number that *looked* like a real answer. That is exactly
the "unverified assumption looks authoritative" failure mode this module
must not have. Every call site is now forced to make an explicit,
visible choice about which fee_rate it is using.

`UNVERIFIED_PLACEHOLDER_FEE_RATE` exists only for tests/research that need
*a* number and don't care if it's exactly right; its name is intentionally
loud. It must never be wired in as a function default, an implicit
fallback, or anything else that would let it flow into a real EV
calculation without a human consciously choosing it.

edge-finder-api's fee-aware net-EV layer (lib/edgelab/kalshi_fees.py,
docs/PRODUCTION_FEE_AWARE_NET_EV.md -- see docs/MLB_ARCHITECTURE_AUDIT.md
section 4) is fully sport-agnostic Kalshi-platform logic and should
eventually be reused near-verbatim for the *shape* below. The exact fee
formula and constants could NOT be verified against Kalshi's current
published fee schedule from this environment (docs.kalshi.com is blocked
by the network egress proxy here) -- this has been attempted twice and
failed both times, so this module does not claim verification it doesn't
have. Verifying the real schedule and wiring a provenance-tagged constant
in its place is Milestone G work, not this one.
"""

from __future__ import annotations

UNVERIFIED_PLACEHOLDER_FEE_RATE = 0.07
"""NOT CONFIRMED against docs.kalshi.com. For tests/research only -- see
module docstring. Never use this as a default parameter value."""


def expected_fee_drag(executable_price: float, fee_rate: float) -> float:
    """Approximates Kalshi's per-contract taker fee, expressed as a
    probability-scale drag: fee_rate * p * (1 - p). This mirrors the general
    shape of exchange fee schedules that scale with the contract's
    proximity to a 50/50 price, but the shape itself is also unverified --
    see module docstring. `fee_rate` is required; there is no default.
    """
    if not 0.0 <= executable_price <= 1.0:
        raise ValueError(f"executable_price must be in [0, 1], got {executable_price!r}")
    return fee_rate * executable_price * (1 - executable_price)


def net_executable_edge(fair_probability: float, executable_price: float, fee_rate: float) -> float:
    """raw edge vs the executable price, minus expected fee drag.

    Positive means the fair probability exceeds what the executable price
    plus expected fees would require to break even. `fee_rate` is
    required; there is no default -- see module docstring.
    """
    raw_edge = fair_probability - executable_price
    return raw_edge - expected_fee_drag(executable_price, fee_rate)
