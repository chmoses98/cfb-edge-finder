"""Live CFB Kalshi execution orchestration.

*** WHAT THIS PACKAGE IS FOR ***
The catalog answers "what can I bet on Kalshi right now?". This package
answers the next question -- "and did we actually LOOK at every single one
of those contracts before picking a bet?" -- and refuses to produce a
shortlist until the answer is provably yes.

*** THE ONE INVARIANT ***
For every handicapped game:

    eligible_contracts == evaluated_contracts + unpriceable_contracts
    unaccounted_contracts == 0

A game that cannot satisfy that is INCOMPLETE, and an incomplete game may
not be represented as fully scanned in any report.

*** WHAT THIS PACKAGE IS NOT ***
It contains no football model and no view about any game. Every fair
probability it uses arrives from OUTSIDE, in a `HandicapPayload` supplied
by whoever (human or LLM) did the handicapping. The code's whole job is
bookkeeping: price every contract the supplied handicap CAN price, mark
every contract it CANNOT as explicitly unpriceable, and never let a
contract fall out of the count in between.

It also places no orders. Nothing in this package can reach a Kalshi
order or portfolio endpoint, and `stake_placeholder` in the final report
is exactly that -- a placeholder the operator fills in themselves.
"""

from __future__ import annotations

EXECUTION_SCHEMA_VERSION = "cfb_execution_slate/1.0.0"
HANDICAP_SCHEMA_VERSION = "cfb_handicap_payload/1.0.0"
STATE_SCHEMA_VERSION = "cfb_execution_state/1.0.0"
LEDGER_SCHEMA_VERSION = "cfb_evaluation_ledger/1.0.0"
REPORT_SCHEMA_VERSION = "cfb_bet_report/1.0.0"
