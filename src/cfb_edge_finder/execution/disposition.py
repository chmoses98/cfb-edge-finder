"""Every discovered contract gets EXACTLY ONE mechanical status.

*** THE RULE ***
    contracts_discovered == contracts_eligible + all explicit exclusions

Not "approximately". The reconciliation in `slate.py` asserts it, and the
CLI refuses to publish a slate that does not balance.

*** MECHANICAL, NEVER EDITORIAL ***
Nothing here asks whether a contract looks interesting, liquid, sharp or
worth betting. The only questions asked are objective ones a machine can
check without an opinion about football:

  has the game already started?         -> game_started
  is the contract still trading?        -> market_closed / _paused / _unopened
  is the capture old enough to be a lie?-> stale_quote
  can I actually buy either side?       -> missing_executable_price
  do I know what fee I would pay?       -> unsupported_fee_model
  did I see this ticker already?        -> duplicate
  can I even state what YES means?      -> unsupported_market_semantics
  is this contract about this game?     -> mapping_failure / not_game_scoped

Everything that survives all eight is ELIGIBLE, including families this
repository has never seen. "I do not know how to price it" is NOT a
mechanical exclusion -- that judgement belongs to the evaluator, after a
handicap exists, where it becomes a counted UNPRICEABLE rather than a
disappearance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from cfb_edge_finder.execution.semantics import SemanticsStatus, TeamSlot


class MechanicalStatus(StrEnum):
    ELIGIBLE = "eligible"
    GAME_STARTED = "game_started"
    MARKET_CLOSED = "market_closed"
    MARKET_PAUSED = "market_paused"
    MARKET_UNOPENED = "market_unopened"
    STALE_QUOTE = "stale_quote"
    MISSING_EXECUTABLE_PRICE = "missing_executable_price"
    MAPPING_FAILURE = "mapping_failure"
    DUPLICATE = "duplicate"
    UNSUPPORTED_FEE_MODEL = "unsupported_fee_model"
    UNSUPPORTED_MARKET_SEMANTICS = "unsupported_market_semantics"
    NOT_GAME_SCOPED = "not_game_scoped"


EXCLUSION_STATUSES = tuple(s for s in MechanicalStatus if s is not MechanicalStatus.ELIGIBLE)

TRADEABLE_MARKET_STATUSES = frozenset({"active", "open", "initialized"})
PAUSED_MARKET_STATUSES = frozenset({"paused"})
UNOPENED_MARKET_STATUSES = frozenset({"unopened", "not_open", "pending"})

DEFAULT_MAX_CAPTURE_AGE_MINUTES = 120.0
"""How old the market capture may be before EVERY contract in it is
`stale_quote`.

Deliberately a property of the CAPTURE, not of each contract's
`updated_time`. A pregame Kalshi contract that nobody has traded for two
days carries a two-day-old `updated_time` and a perfectly live book, so a
per-contract staleness rule keyed on it would exclude most of the slate
for no reason. What actually goes stale is our OBSERVATION of the book --
which is one timestamp for the whole capture. A per-contract quote-age
gate is still available via `max_quote_age_minutes`, defaulting to off
because on this exchange it measures the wrong thing."""


@dataclass(frozen=True)
class DispositionConfig:
    as_of: datetime
    captured_at: datetime | None
    max_capture_age_minutes: float = DEFAULT_MAX_CAPTURE_AGE_MINUTES
    max_quote_age_minutes: float | None = None
    require_pregame: bool = True
    min_seconds_before_kickoff: float = 0.0
    """Contracts on a game kicking off within this many seconds are
    excluded as `game_started`. 0 means "excluded once kickoff passes"."""

    @property
    def capture_age_seconds(self) -> float | None:
        if self.captured_at is None:
            return None
        return (self.as_of - self.captured_at).total_seconds()

    @property
    def capture_is_stale(self) -> bool:
        age = self.capture_age_seconds
        if age is None:
            return True
        return age > self.max_capture_age_minutes * 60.0


@dataclass(frozen=True)
class Disposition:
    status: str
    reason: str

    @property
    def eligible(self) -> bool:
        return self.status == MechanicalStatus.ELIGIBLE.value


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def executable_entry(price: Any) -> float | None:
    """A price you could actually pay. Strictly inside (0, 1): a 0 ask is
    not a free contract and a 1.00 ask has no upside, and both appear on
    this exchange as sentinel values rather than as offers."""
    value = _as_float(price)
    if value is None:
        return None
    if value <= 0.0 or value >= 1.0:
        return None
    return value


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def fee_support(contract: dict[str, Any]) -> tuple[str, str | None]:
    fee = contract.get("fee")
    if not isinstance(fee, dict):
        return "metadata_unavailable", None
    return str(fee.get("support") or "metadata_unavailable"), fee.get("model")


def classify(
    contract: dict[str, Any],
    *,
    semantics_status: str,
    team_slot: str,
    kickoff: datetime | None,
    config: DispositionConfig,
    seen_tickers: set[str],
    game_scoped: bool = True,
) -> Disposition:
    """One contract -> one status. First match wins, and the order is the
    order in which a reason makes the later checks moot."""
    ticker = str(contract.get("market_ticker") or "")

    if not ticker:
        return Disposition(
            MechanicalStatus.MAPPING_FAILURE.value, "contract carries no market ticker"
        )
    if ticker in seen_tickers:
        return Disposition(
            MechanicalStatus.DUPLICATE.value,
            f"ticker {ticker} already dispositioned in this capture",
        )

    if not game_scoped:
        return Disposition(
            MechanicalStatus.NOT_GAME_SCOPED.value,
            "contract belongs to season-level inventory, not to one physical game",
        )

    if semantics_status == SemanticsStatus.UNSTATEABLE.value:
        return Disposition(
            MechanicalStatus.UNSUPPORTED_MARKET_SEMANTICS.value,
            "no title, subtitle or rules text: what YES means cannot be stated",
        )
    if team_slot == TeamSlot.UNRESOLVED.value:
        return Disposition(
            MechanicalStatus.MAPPING_FAILURE.value,
            "contract is team-scoped but no team could be resolved from ticker, subtitle or custom_strike",
        )

    if config.require_pregame:
        if kickoff is None:
            return Disposition(
                MechanicalStatus.MAPPING_FAILURE.value,
                "game has no kickoff time, so pregame status cannot be established",
            )
        seconds_to_kickoff = (kickoff - config.as_of).total_seconds()
        if seconds_to_kickoff <= config.min_seconds_before_kickoff:
            return Disposition(
                MechanicalStatus.GAME_STARTED.value,
                f"kickoff {kickoff.isoformat()} is {-seconds_to_kickoff:.0f}s past the pregame cutoff",
            )

    status = str(contract.get("status") or "").lower()
    if status in PAUSED_MARKET_STATUSES:
        return Disposition(MechanicalStatus.MARKET_PAUSED.value, f"market status {status!r}")
    if status in UNOPENED_MARKET_STATUSES:
        return Disposition(MechanicalStatus.MARKET_UNOPENED.value, f"market status {status!r}")
    if status not in TRADEABLE_MARKET_STATUSES:
        return Disposition(
            MechanicalStatus.MARKET_CLOSED.value, f"market status {status!r} is not tradeable"
        )

    close_time = parse_timestamp(contract.get("close_time"))
    if close_time is not None and close_time <= config.as_of:
        return Disposition(
            MechanicalStatus.MARKET_CLOSED.value, f"close_time {close_time.isoformat()} has passed"
        )

    if config.capture_is_stale:
        age = config.capture_age_seconds
        return Disposition(
            MechanicalStatus.STALE_QUOTE.value,
            (
                f"capture age {age:.0f}s exceeds the {config.max_capture_age_minutes:.0f}-minute limit"
                if age is not None
                else "capture timestamp is missing, so quote age cannot be established"
            ),
        )

    if config.max_quote_age_minutes is not None:
        updated = parse_timestamp(contract.get("updated_time"))
        if updated is None:
            return Disposition(
                MechanicalStatus.STALE_QUOTE.value, "contract carries no quote timestamp"
            )
        quote_age = (config.as_of - updated).total_seconds()
        if quote_age > config.max_quote_age_minutes * 60.0:
            return Disposition(
                MechanicalStatus.STALE_QUOTE.value,
                f"quote age {quote_age:.0f}s exceeds the {config.max_quote_age_minutes:.0f}-minute limit",
            )

    support, model = fee_support(contract)
    if support != "supported":
        return Disposition(
            MechanicalStatus.UNSUPPORTED_FEE_MODEL.value,
            f"fee model {model!r} reports support={support!r}; refusing to price a fee we cannot compute",
        )

    yes_entry = executable_entry(contract.get("yes_ask"))
    no_entry = executable_entry(contract.get("no_ask"))
    if yes_entry is None and no_entry is None:
        return Disposition(
            MechanicalStatus.MISSING_EXECUTABLE_PRICE.value,
            f"neither side is buyable: yes_ask={contract.get('yes_ask')!r} no_ask={contract.get('no_ask')!r}",
        )

    fee = contract.get("fee") if isinstance(contract.get("fee"), dict) else {}
    if yes_entry is not None and _as_float(fee.get("model_trade_fee_at_yes_ask")) is None:
        if no_entry is None or _as_float(fee.get("model_trade_fee_at_no_ask")) is None:
            return Disposition(
                MechanicalStatus.UNSUPPORTED_FEE_MODEL.value,
                "no executable side has a computable trade fee",
            )
    if yes_entry is None and no_entry is not None and _as_float(fee.get("model_trade_fee_at_no_ask")) is None:
        return Disposition(
            MechanicalStatus.UNSUPPORTED_FEE_MODEL.value,
            "the only executable side has no computable trade fee",
        )

    return Disposition(MechanicalStatus.ELIGIBLE.value, "tradeable, priced, fee-modelled, pregame")
