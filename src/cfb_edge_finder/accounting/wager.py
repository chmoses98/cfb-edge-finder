"""One wager the owner actually placed. Reconstructed, never recommended."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = "cfb_accounted_wager.v1"

ENTRY_METHOD_IMPORTED_RECEIPT = "IMPORTED_RECEIPT"

#: Fields that would assert this repository's model had something to do with
#: the bet. Refused outright rather than left empty: an empty field invites
#: being filled in later, and the distance between "null" and "plausible" is
#: one well-meaning backfill.
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
class AccountedWager:
    """One ORDER, not one fill.

    The venue matched the order in pieces; the owner decided once. Summing the
    pieces is not an approximation, it is the wager. ``execution_price`` is a
    genuine quantity-weighted average across the order's own fills.
    """

    wager_id: str
    schema_version: str
    #: Deterministic and venue-derived. Re-importing the same order produces
    #: the same key, which is what makes a repeated backfill a no-op rather
    #: than a second row.
    source_bet_key: str
    import_batch_id: str
    entry_method: str

    #: The contest's own date, from event evidence. Never derived from a market
    #: close timestamp: that is a UTC instant, and a Saturday night kickoff
    #: closes on Sunday.
    game_date: str
    market_ticker: str
    side: str

    executed_at: str
    contracts: float
    execution_price: float
    stake: float
    fees_paid: float | None = None
    fees_are_estimated: bool = False

    #: Absent until established. Never zero as a placeholder -- zero is a
    #: settled result that happens to pay nothing, which is a different claim.
    settlement_status: str | None = None
    result: str | None = None
    gross_return: float | None = None
    net_profit_loss: float | None = None

    venue: str = "kalshi"
    season: int | None = None
    week: int | None = None
    event_refs: dict = field(default_factory=dict)
    notes: str = ""

    def to_dict(self):
        return asdict(self)


def validate(record: dict) -> list[str]:
    """Every reason this row may not be written. Empty means it may."""
    problems: list[str] = []

    for name in FORBIDDEN_PROVENANCE_FIELDS:
        if name in record:
            problems.append(
                f"{name!r} would assert model provenance this wager does not have"
            )

    for name in ("wager_id", "source_bet_key", "import_batch_id",
                 "market_ticker", "side", "game_date", "executed_at"):
        value = record.get(name)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{name} is required and must be a non-empty string")

    if record.get("entry_method") != ENTRY_METHOD_IMPORTED_RECEIPT:
        problems.append(
            f"entry_method must be {ENTRY_METHOD_IMPORTED_RECEIPT!r}; "
            f"got {record.get('entry_method')!r}"
        )

    if record.get("side") not in ("YES", "NO"):
        problems.append(f"side must be YES or NO; got {record.get('side')!r}")

    for name in ("contracts", "execution_price", "stake"):
        value = record.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            problems.append(f"{name} is required and must be a number")
        elif value < 0:
            problems.append(f"{name} may not be negative")

    if record.get("fees_are_estimated") and record.get("net_profit_loss") is not None:
        problems.append(
            "net_profit_loss may not be stated from estimated fees; that is a "
            "modelled number wearing a realised one's clothes"
        )

    return problems
