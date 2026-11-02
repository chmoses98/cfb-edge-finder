"""Point-in-time guards: a candidate is actionable only if every input was known, fresh and executable.

*** RESTRICTION-ONLY ***
Every guard can only REMOVE actionability. Nothing here creates, ranks or sizes a bet, so it is
safe to run by default. Missing evidence is a blocking ``MISSING_*`` verdict, never a pass.

*** THRESHOLDS ARE OWNER-ADJUSTABLE DEFAULTS ***
They are policy, not measured facts. They are named constants so a change is a visible diff,
and ``evaluate_pit_guards`` accepts overrides through ``PitPolicy``.

Checks (all relative to ``decision_time``, the instant the recommendation is generated or the
order is about to be sent -- supplied by the caller, never read from the wall clock here):

* projection freshness   -- the model's inputs ``as_of`` is not older than ``max_projection_age``
* injuries / availability -- observed no earlier than ``max_availability_age`` before decision, and
                            observed no later than the decision (no look-ahead)
* confirmed QB            -- an unconfirmed or uncertain starting QB blocks
* market quote freshness  -- the quote was captured within ``max_quote_age`` of the decision
* price provenance        -- the priced number must be an EXECUTABLE ask for the side bought;
                            a CAPTURED mid / last / bid is not a price anyone can trade at
* kickoff                 -- decision time must be earlier than kickoff minus ``kickoff_buffer``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

DEFAULT_MAX_PROJECTION_AGE = timedelta(hours=12)
DEFAULT_MAX_AVAILABILITY_AGE = timedelta(hours=3)
DEFAULT_MAX_QUOTE_AGE = timedelta(minutes=5)
DEFAULT_KICKOFF_BUFFER = timedelta(minutes=10)


class PriceProvenance(StrEnum):
    EXECUTABLE_ASK = "EXECUTABLE_ASK"  # the ask for the side being bought
    CAPTURED_MID = "CAPTURED_MID"
    CAPTURED_LAST = "CAPTURED_LAST"
    CAPTURED_BID = "CAPTURED_BID"
    UNKNOWN = "UNKNOWN"


class Guard(StrEnum):
    PROJECTION_STALE = "PROJECTION_STALE"
    MISSING_PROJECTION_AS_OF = "MISSING_PROJECTION_AS_OF"
    PROJECTION_FROM_FUTURE = "PROJECTION_FROM_FUTURE"
    AVAILABILITY_STALE = "AVAILABILITY_STALE"
    MISSING_AVAILABILITY = "MISSING_AVAILABILITY"
    AVAILABILITY_FROM_FUTURE = "AVAILABILITY_FROM_FUTURE"
    QB_UNCONFIRMED = "QB_UNCONFIRMED"
    QUOTE_STALE = "QUOTE_STALE"
    MISSING_QUOTE_TIME = "MISSING_QUOTE_TIME"
    QUOTE_FROM_FUTURE = "QUOTE_FROM_FUTURE"
    NON_EXECUTABLE_PRICE = "NON_EXECUTABLE_PRICE"
    MISSING_KICKOFF = "MISSING_KICKOFF"
    AFTER_KICKOFF_CUTOFF = "AFTER_KICKOFF_CUTOFF"


@dataclass(frozen=True)
class PitPolicy:
    max_projection_age: timedelta = DEFAULT_MAX_PROJECTION_AGE
    max_availability_age: timedelta = DEFAULT_MAX_AVAILABILITY_AGE
    max_quote_age: timedelta = DEFAULT_MAX_QUOTE_AGE
    kickoff_buffer: timedelta = DEFAULT_KICKOFF_BUFFER
    #: NFL has a confirmed-starting-QB source, so an unconfirmed QB blocks. CFB has none: requiring
    #: confirmation there would block every bet forever, so CFB sets this False and the gap is
    #: reported as a NOTE (``QB_STATUS_NOT_TRACKED``) rather than silently passed or blocking.
    require_qb_confirmation: bool = True


@dataclass(frozen=True)
class PitInputs:
    """All datetimes must be timezone-aware UTC. ``None`` means the evidence does not exist."""

    decision_time: datetime
    kickoff: datetime | None
    projection_as_of: datetime | None
    availability_observed_at: datetime | None
    qb_confirmed: bool | None  # None = unknown
    quote_captured_at: datetime | None
    price_provenance: PriceProvenance


QB_STATUS_NOT_TRACKED = "QB_STATUS_NOT_TRACKED"


@dataclass(frozen=True)
class GuardResult:
    blocking: tuple[Guard, ...] = field(default_factory=tuple)
    #: Non-blocking disclosures (evidence this pipeline does not have, stated rather than hidden).
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def actionable(self) -> bool:
        return not self.blocking


def _check_aware(*values: datetime | None) -> None:
    for v in values:
        if v is not None and v.tzinfo is None:
            raise ValueError("naive datetime passed to PIT guards; supply timezone-aware UTC values")


def evaluate_pit_guards(inputs: PitInputs, policy: PitPolicy | None = None) -> GuardResult:
    pol = policy or PitPolicy()
    _check_aware(
        inputs.decision_time,
        inputs.kickoff,
        inputs.projection_as_of,
        inputs.availability_observed_at,
        inputs.quote_captured_at,
    )
    t = inputs.decision_time
    out: list[Guard] = []

    if inputs.projection_as_of is None:
        out.append(Guard.MISSING_PROJECTION_AS_OF)
    elif inputs.projection_as_of > t:
        out.append(Guard.PROJECTION_FROM_FUTURE)
    elif t - inputs.projection_as_of > pol.max_projection_age:
        out.append(Guard.PROJECTION_STALE)

    if inputs.availability_observed_at is None:
        out.append(Guard.MISSING_AVAILABILITY)
    elif inputs.availability_observed_at > t:
        out.append(Guard.AVAILABILITY_FROM_FUTURE)
    elif t - inputs.availability_observed_at > pol.max_availability_age:
        out.append(Guard.AVAILABILITY_STALE)

    notes: list[str] = []
    if inputs.qb_confirmed is not True:
        if pol.require_qb_confirmation:
            out.append(Guard.QB_UNCONFIRMED)
        else:
            notes.append(QB_STATUS_NOT_TRACKED)

    if inputs.quote_captured_at is None:
        out.append(Guard.MISSING_QUOTE_TIME)
    elif inputs.quote_captured_at > t:
        out.append(Guard.QUOTE_FROM_FUTURE)
    elif t - inputs.quote_captured_at > pol.max_quote_age:
        out.append(Guard.QUOTE_STALE)

    if inputs.price_provenance is not PriceProvenance.EXECUTABLE_ASK:
        out.append(Guard.NON_EXECUTABLE_PRICE)

    if inputs.kickoff is None:
        out.append(Guard.MISSING_KICKOFF)
    elif t >= inputs.kickoff - pol.kickoff_buffer:
        out.append(Guard.AFTER_KICKOFF_CUTOFF)

    return GuardResult(tuple(out), tuple(notes))
