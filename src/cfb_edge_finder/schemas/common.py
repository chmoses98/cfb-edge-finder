"""Closed vocabularies shared across every schema.

Every enum here is a CLOSED set deliberately. Adding a new value is a
schema-versioning decision (see docs/SCHEMAS.md "Version/provenance
scheme"), not something a script should invent ad hoc. This is the
sport-agnostic pattern audited from edge-finder-api's canonical status
enums (Accepted/Rejected/Missing Data/Evaluation Failed, and the
coverage-ledger terminal states) -- see docs/MLB_ARCHITECTURE_AUDIT.md.
"""

from __future__ import annotations

from enum import StrEnum


class SeasonType(StrEnum):
    REGULAR = "regular"
    CONFERENCE_CHAMPIONSHIP = "conference_championship"
    BOWL = "bowl"
    CFP = "cfp"


class MarketFamily(StrEnum):
    MONEYLINE = "moneyline"
    SPREAD = "spread"
    ALT_SPREAD = "alt_spread"
    TOTAL = "total"
    ALT_TOTAL = "alt_total"
    TEAM_TOTAL = "team_total"
    FIRST_HALF_MONEYLINE = "first_half_moneyline"
    FIRST_HALF_SPREAD = "first_half_spread"
    FIRST_HALF_TOTAL = "first_half_total"
    OTHER = "other"


class Side(StrEnum):
    HOME = "home"
    AWAY = "away"
    OVER = "over"
    UNDER = "under"
    YES = "yes"
    NO = "no"


class MarketStatus(StrEnum):
    """Every discovered Kalshi market must resolve to exactly one of these.

    Non-terminal (open pipeline states):
      DISCOVERED         -- seen in a raw market sweep, nothing else known yet
      TICKER_UNRESOLVED   -- ticker could not be parsed into game/family/line
      MAPPED              -- parsed and matched to a known game, not yet evaluated
      WATCH                -- evaluated, below qualification bar but worth monitoring

    Terminal (pipeline is done with this market for this evaluation cycle):
      MISSING_INPUT        -- game matched, but required model inputs are absent
      EVALUATION_FAILED    -- an unexpected error occurred while pricing it
      UNSUPPORTED_MARKET   -- market family/period not yet supported by the pricer
      GAME_STARTED          -- game began before evaluation completed; excluded
      REJECTED              -- evaluated, fair-priced, but no qualifying edge
      EARLY_VALUE            -- edge detected but data completeness/confidence too
                                 low to promote to ACCEPTED yet
      ACCEPTED               -- qualifies as a recommendable edge

    Silent disappearance from this vocabulary is a bug, not a valid outcome
    -- see cfb_edge_finder.kalshi.coverage_ledger.CoverageLedger.
    """

    DISCOVERED = "discovered"
    TICKER_UNRESOLVED = "ticker_unresolved"
    MAPPED = "mapped"
    MISSING_INPUT = "missing_input"
    EVALUATION_FAILED = "evaluation_failed"
    UNSUPPORTED_MARKET = "unsupported_market"
    GAME_STARTED = "game_started"
    REJECTED = "rejected"
    WATCH = "watch"
    EARLY_VALUE = "early_value"
    ACCEPTED = "accepted"


TERMINAL_MARKET_STATUSES = frozenset(
    {
        MarketStatus.MISSING_INPUT,
        MarketStatus.EVALUATION_FAILED,
        MarketStatus.UNSUPPORTED_MARKET,
        MarketStatus.GAME_STARTED,
        MarketStatus.REJECTED,
        MarketStatus.ACCEPTED,
    }
)
"""Statuses after which no further automatic transition is expected this
cycle. WATCH and EARLY_VALUE are deliberately non-terminal: they are
expected to be re-evaluated on the next pricing pass as market price or
model inputs change."""
