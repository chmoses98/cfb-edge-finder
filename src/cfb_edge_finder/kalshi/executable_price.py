"""Fee-aware net EV placeholder.

STATUS: intentionally a thin, clearly-labeled placeholder for this
foundation phase, not verified production math. edge-finder-api's fee-aware
net-EV layer (lib/edgelab/kalshi_fees.py, docs/PRODUCTION_FEE_AWARE_NET_EV.md
-- see docs/MLB_ARCHITECTURE_AUDIT.md section 4) is fully sport-agnostic
Kalshi-platform logic and should eventually be reused near-verbatim, but its
exact fee formula and constants must be re-verified against Kalshi's current
published fee schedule (docs.kalshi.com) before this module is trusted with
real money -- do NOT treat DEFAULT_TAKER_FEE_RATE below as confirmed.

The shape (raw edge vs executable price, minus expected fee drag, equals
net executable edge) is the part worth locking in now; the constant is not.
"""

from __future__ import annotations

DEFAULT_TAKER_FEE_RATE = 0.07  # UNVERIFIED placeholder -- confirm at docs.kalshi.com before production use


def expected_fee_drag(executable_price: float, fee_rate: float = DEFAULT_TAKER_FEE_RATE) -> float:
    """Approximates Kalshi's per-contract taker fee, expressed as a
    probability-scale drag: fee_rate * p * (1 - p). This mirrors the general
    shape of exchange fee schedules that scale with the contract's
    proximity to a 50/50 price, but the constant is a placeholder -- see
    module docstring.
    """
    if not 0.0 <= executable_price <= 1.0:
        raise ValueError(f"executable_price must be in [0, 1], got {executable_price!r}")
    return fee_rate * executable_price * (1 - executable_price)


def net_executable_edge(
    fair_probability: float, executable_price: float, fee_rate: float = DEFAULT_TAKER_FEE_RATE
) -> float:
    """raw edge vs the executable price, minus expected fee drag.

    Positive means the fair probability exceeds what the executable price
    plus expected fees would require to break even.
    """
    raw_edge = fair_probability - executable_price
    return raw_edge - expected_fee_drag(executable_price, fee_rate)
