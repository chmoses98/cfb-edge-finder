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


class CFPRound(StrEnum):
    """Structured, sponsor-name-independent round identity for a College
    Football Playoff game. Only meaningful when SeasonType is CFP.

    This is deliberately a separate field from the free-text/slug
    `GameRecord.week_label` (e.g. "cfp-quarterfinal") used in the canonical
    game_id -- see docs/MILESTONE_B.md "Week and postseason semantics" for
    why the two are kept independent rather than merging this enum into
    the ID format.
    """

    FIRST_ROUND = "first_round"
    QUARTERFINAL = "quarterfinal"
    SEMIFINAL = "semifinal"
    NATIONAL_CHAMPIONSHIP = "national_championship"


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


class CoverageOutcome(StrEnum):
    """Every discovered Kalshi market must resolve to exactly one of these.

    This enum answers ONE question only: "did the pipeline manage to
    produce a fair-value evaluation for this market, and if not, why not?"
    It deliberately does NOT answer "is this market worth betting" -- that
    is a separate, orthogonal question, answered by RecommendationReadiness
    below. Mixing the two into one flat vocabulary was an earlier design
    (a merged MarketStatus enum) that risked a market being miscounted as
    "not yet evaluated" by a future coverage report simply because it
    landed at a middling recommendation tier -- see the pre-merge audit
    note in docs/ARCHITECTURE.md section 4. Splitting them means coverage
    completeness ("every discovered market is accounted for") can be
    proven without any reference to recommendation-worthiness at all.

    Non-terminal (open pipeline states):
      DISCOVERED         -- seen in a raw market sweep, nothing else known yet
      MAPPED              -- parsed and matched to a known game, not yet evaluated

    Terminal (pipeline is done with this market for this evaluation cycle):
      EVALUATED             -- successfully produced a fair_probability. Says
                                nothing about whether it's a good bet -- see
                                RecommendationReadiness for that.
      TICKER_UNRESOLVED     -- ticker could not be parsed into game/family/line
      MISSING_INPUT          -- game matched, but required model inputs are absent
      EVALUATION_FAILED      -- an unexpected error occurred while pricing it
      UNSUPPORTED_MARKET     -- market family/period not yet supported by the pricer
      GAME_STARTED            -- game began before evaluation completed; excluded

    Silent disappearance from this vocabulary is a bug, not a valid outcome
    -- see cfb_edge_finder.kalshi.coverage_ledger.CoverageLedger.
    """

    DISCOVERED = "discovered"
    MAPPED = "mapped"
    EVALUATED = "evaluated"
    TICKER_UNRESOLVED = "ticker_unresolved"
    MISSING_INPUT = "missing_input"
    EVALUATION_FAILED = "evaluation_failed"
    UNSUPPORTED_MARKET = "unsupported_market"
    GAME_STARTED = "game_started"


TERMINAL_COVERAGE_OUTCOMES = frozenset(
    {
        CoverageOutcome.EVALUATED,
        CoverageOutcome.TICKER_UNRESOLVED,
        CoverageOutcome.MISSING_INPUT,
        CoverageOutcome.EVALUATION_FAILED,
        CoverageOutcome.UNSUPPORTED_MARKET,
        CoverageOutcome.GAME_STARTED,
    }
)
"""Outcomes after which no further automatic transition is expected this
evaluation cycle. DISCOVERED and MAPPED are the only non-terminal states."""


class RecommendationReadiness(StrEnum):
    """A business-value judgment, ONLY meaningful once a market's
    CoverageOutcome is EVALUATED (a market that is MISSING_INPUT or
    UNSUPPORTED_MARKET has no fair probability to judge and has no
    RecommendationReadiness at all -- it stays None).

    This is deliberately a separate field/enum from CoverageOutcome, not a
    finer-grained set of "terminal states" bolted onto it, so that a
    market sitting at WATCH or EARLY_VALUE can never be miscounted as
    incomplete or dropped by coverage accounting -- it is fully EVALUATED
    (CoverageOutcome) and separately WATCH (RecommendationReadiness); both
    facts are tracked, neither shadows the other.

      PASS          -- evaluated, no qualifying edge found
      WATCH          -- evaluated, below the qualification bar but worth
                         monitoring as price/inputs change
      EARLY_VALUE     -- edge detected but data completeness/confidence too
                          low to promote to ACTIONABLE yet
      ACTIONABLE       -- qualifies as a recommendable edge

    No code in this codebase currently sets ACTIONABLE or computes a
    qualification bar -- that is Milestone G/H, not this schema. This enum
    exists now purely so the *shape* of that future distinction doesn't
    require a breaking schema change later.
    """

    PASS = "pass"
    WATCH = "watch"
    EARLY_VALUE = "early_value"
    ACTIONABLE = "actionable"
