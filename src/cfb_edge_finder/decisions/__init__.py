"""Decision records: what the live CFB workflow decided, kept where a postmortem can read it.

*** WHY THIS PACKAGE EXISTS ***
The 2026-09-26 postmortem could attribute none of the season's 93 wagers to a
recommendation, because nothing the live workflow produced -- the handicap
payloads, the candidate artifacts, the decision-time prices -- was kept anywhere
the postmortem could later reach. The Actions artifacts expire in seven days
and, on a public repository, are public; the operator's local files are local.

A decision record is the versioned, self-contained statement of one `candidates`
run: the games and their theses, every candidate market that was evaluated,
which ones survived, at what observed price, with what bet-up-to, and when. It
is written ATOMICALLY to a PRIVATE store the operator points at, and it is never
committed to this repository or to the router's, because it carries the prices
and sizes of the owner's betting decisions.

*** THE TWO SIDES ***
`record` is the contract and the builder (producer side, run by
`cfb_edge_finder.execution candidates`). `store` is where records live and how
they are read back (consumer side, run by `scripts/cfb_postmortem.py`). Neither
imports the accounting ledger or any predictive package: a decision record is
evidence ABOUT a decision, and it must have no path into a canonical wager row.
"""

from cfb_edge_finder.decisions.record import (  # noqa: F401
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    build_decision_record,
    validate_decision_record,
)
from cfb_edge_finder.decisions.store import (  # noqa: F401
    ENV_STORE,
    PRIVATE_MARKER,
    DecisionStore,
    DecisionStoreUnavailable,
)
