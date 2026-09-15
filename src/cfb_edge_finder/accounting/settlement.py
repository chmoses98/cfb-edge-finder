"""What the exchange paid on a wager the owner placed. ACCOUNTING, not prediction.

A settlement is a LATER and SEPARATE observation than the wager it settles, and
it is recorded as a separate row rather than by editing one. The wager ledger is
append-only because a wager record is a claim about money that already moved;
correcting one in place would destroy the evidence of what was believed before,
and that reasoning does not stop applying just because the new information is
welcome.

WHY THERE IS NO "PENDING" ROW
-----------------------------
A market settles once. So a settlement is written once, when it is established,
and a wager with no settlement row is simply unsettled -- which is exactly what
the absence already means to every reader. Writing PENDING rows would create the
one thing this design avoids: a row that has to be superseded later, in a store
whose whole guarantee is that rows are not.

A SETTLED ROW WITH NO MONEY ON IT IS STILL WORTH WRITING
---------------------------------------------------------
"The market settled and the return could not be established" is a durable fact
with a durable cause -- the exchange published no settlement price, or a fee was
charged on a position covering more than one of the owner's orders. Both stay
true. So the row is written with the money field absent and the REASON recorded,
never with a zero standing in for it.

RECORDING IS NOT ENDORSING
---------------------------
The CFB model recommended none of these wagers and settled none of them either.
A won-lost record assembled from these rows is a fact about a bankroll.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = "cfb_wager_settlement.v1"

SETTLED = "SETTLED"

WON = "WON"
LOST = "LOST"

#: The same fields the wager record refuses, for the same reason: a settlement
#: proves what the exchange paid and proves nothing about what recommended the
#: bet.
FORBIDDEN_PROVENANCE_FIELDS = (
    "recommendation_id",
    "model_evaluation_id",
    "model_fair_probability",
    "model_supported",
    "projection_id",
    "rating",
    "edge",
    "expected_value",
    "qualification",
    "readiness",
)


@dataclass
class WagerSettlement:
    """One wager's settlement, as far as the exchange establishes it."""

    settlement_id: str
    schema_version: str
    #: The wager this settles. The venue's own order identity, so a settlement
    #: and its wager can be joined without either naming the other's record.
    source_bet_key: str
    market_ticker: str
    side: str

    settlement_status: str
    settled_at: str

    #: Absent when the exchange's own outcome is not one this system maps -- a
    #: void, a scalar settlement. Forcing those into a binary would misstate them.
    result: str | None = None

    #: Absent with a REASON rather than zero. Zero is a settlement that paid
    #: nothing, which is a different claim from one that could not be computed.
    gross_return: float | None = None
    net_profit_loss: float | None = None
    refusals: list = field(default_factory=list)

    venue: str = "kalshi"

    def to_dict(self):
        return asdict(self)


def validate(record: dict) -> list[str]:
    """Every reason this row may not be written. Empty means it may."""
    problems: list[str] = []

    for name in FORBIDDEN_PROVENANCE_FIELDS:
        if name in record:
            problems.append(
                f"{name!r} would assert model provenance this settlement does not have"
            )

    for name in ("settlement_id", "source_bet_key", "market_ticker", "side",
                 "settlement_status", "settled_at"):
        value = record.get(name)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{name} is required and must be a non-empty string")

    if record.get("settlement_status") != SETTLED:
        # PENDING is the ABSENCE of a row here, deliberately. See the module
        # docstring: a row that has to be superseded later has no place in a
        # store whose guarantee is that rows are not.
        problems.append(
            f"settlement_status must be {SETTLED!r}; an unsettled wager is "
            "recorded by having no settlement row at all, not by a row saying so"
        )

    if record.get("side") not in ("YES", "NO"):
        problems.append(f"side must be YES or NO; got {record.get('side')!r}")

    if record.get("result") not in (None, WON, LOST):
        problems.append(
            f"result must be {WON!r}, {LOST!r} or absent; got {record.get('result')!r}"
        )

    for name in ("gross_return", "net_profit_loss"):
        value = record.get(name)
        if value is None:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            problems.append(f"{name} must be a number when it is stated at all")

    refusals = record.get("refusals")
    if not isinstance(refusals, list):
        problems.append("refusals must be a list, even when empty")
    else:
        # A money field absent with no reason is the shape a reader fills in
        # with a zero.
        missing = [n for n in ("gross_return", "net_profit_loss") if record.get(n) is None]
        if missing and not refusals:
            problems.append(
                f"{', '.join(missing)} absent with no refusal recorded; a null "
                "with no explanation is how an unknown becomes a zero"
            )
        if not missing and refusals:
            problems.append(
                "every figure is established, so a refusal has nothing to refuse"
            )

    return problems
