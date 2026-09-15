"""ACCOUNTING, not prediction. Deliberately outside model authority.

    CFB MODEL STATUS = RESEARCH ONLY / DISABLED
    CFB ACCOUNTING   = ENABLED

Those two lines are the whole point of this package, and they are not in
tension. The owner placed real College Football wagers on Kalshi. They moved
his bankroll, so his accounting has to know about them. That is a fact about
his money, not a claim about this repository's model.

WHAT THIS PACKAGE MAY NEVER BECOME
----------------------------------
`tests/test_no_recommendation_surface.py` forbids staking and execution
surfaces inside the PREDICTIVE packages -- betting, projections, research,
modeling, kalshi, recommendation, expression -- so that the CFB model cannot
quietly grow into a recommendation engine.

This package is not in that list, and that is a reason for MORE care rather
than less: an accounting ledger is exactly the shape of thing that could become
a staking surface by accretion. So it carries its own rule, asserted by its own
test:

  * nothing here may size, recommend, rank or price a bet. It records what
    already happened, at a venue, with money that already moved;
  * no predictive package may import it. A projection that could read the
    wager ledger is one refactor away from being trained on it.

RECORDING IS NOT ENDORSING
--------------------------
A row here says the owner placed this bet. It does not say the CFB model
recommended it, was consulted about it, or was right. Every field that could
imply otherwise is refused outright rather than left empty, because an empty
field invites being filled in later.
"""

from .wager import (  # noqa: F401
    ENTRY_METHOD_IMPORTED_RECEIPT,
    FORBIDDEN_PROVENANCE_FIELDS,
    SCHEMA_VERSION,
    AccountedWager,
    validate,
)
