"""DISCONNECTED stake-sizing mathematics.

*** THIS PACKAGE IS NOT WIRED TO ANYTHING ***

Nothing in the decision, recommendation, research, expression, or scripts
layers imports `cfb_edge_finder.sizing`, and a structural test enforces
that. The mathematics is written now, while it can be checked calmly
against boundary cases, precisely so that it is NOT written later under
the pressure of a live Saturday.

Being correct is not the same as being usable. Every function here
demands its inputs explicitly: there is no default bankroll, no default
Kelly multiplier, no default cap, no default haircut. A caller cannot
accidentally size a position by omitting an argument, because omitting an
argument is a TypeError. Numbers that would matter are the caller's to
state and defend, not this module's to assume.

Using any of this for real money additionally requires an approved
empirical threshold artifact and validated prospective evidence, neither
of which exists.
"""

from __future__ import annotations

SIZING_IS_DISCONNECTED = "SIZING_MATH_DISCONNECTED_FROM_DECISION_PIPELINE"
"""Asserted by `tests/test_sizing_disconnection.py`, not merely stated."""
