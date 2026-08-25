"""Milestone D: CFB-Kalshi-specific coverage-outcome reasons.

*** WHY THIS EXISTS ALONGSIDE, NOT INSTEAD OF, schemas.common.CoverageOutcome ***
`CoverageOutcome` (Milestone A, schemas/common.py) is a deliberately small,
sport/vendor-agnostic CLOSED vocabulary answering one question only: "did
the pipeline manage to produce a fair-value evaluation for this market?"
It is reused verbatim here, unmodified -- see that module's own docstring
for why adding a value to it is a schema-versioning decision this mission
does not make.

Milestone D needs a second, ORTHOGONAL-IN-DETAIL classification: not "how
far did this market get through the pipeline" but "specifically why did it
land where it did." `CoverageLedgerEntry.transition()` and `MarketRecord`
already have a free-text `reason`/`coverage_reason` field for exactly this
purpose -- this module supplies a CLOSED, testable vocabulary for that
field (rather than ad hoc strings) and the deterministic mapping from each
reason to the underlying CoverageOutcome it corresponds to, so:
  - `KalshiCfbCoverageReason` is what a human/report reads (specific, CFB-
    Kalshi-shaped).
  - `to_coverage_outcome()` is what the EXISTING, already-tested
    CoverageLedger/MarketRecord machinery is driven with (generic, proven).
No new ledger, no new persisted schema, no duplicate accounting logic --
this module is purely a vocabulary + a lookup table on top of what already
exists.
"""

from __future__ import annotations

from enum import StrEnum

from cfb_edge_finder.schemas.common import CoverageOutcome


class KalshiCfbCoverageReason(StrEnum):
    """Every discovered Kalshi CFB market ends at exactly one of these.
    See mission section 3's suggested list -- this is that list, made a
    real, closed, testable enum rather than free-text strings scattered
    through the codebase.
    """

    MAPPED_SUPPORTED = "mapped_supported"
    """Mapped to exactly one canonical game, family is CORE_V1
    (game_winner/point_spread/game_total), population is FBS-vs-FBS --
    eligible for model pricing."""

    MAPPED_UNSUPPORTED_FAMILY = "mapped_unsupported_family"
    """Mapped to a game, but the market family itself has no pricer yet
    (e.g. first_half_total, team_total) -- see
    projections.distribution.price_market's UnsupportedMarketFamilyError."""

    MAPPED_UNSUPPORTED_POPULATION = "mapped_unsupported_population"
    """Mapped to a game and a CORE_V1 family, but the game itself is a
    population this pass explicitly does not price -- FBS-vs-FCS
    (UNSUPPORTED_FOR_PRICING per every C.2 document) is the only current
    member."""

    AMBIGUOUS_GAME_MAPPING = "ambiguous_game_mapping"
    """Team identities resolved, but more than one (or zero) candidate
    CFBD game matches the extracted date/team-pair evidence -- e.g. two
    games between the same programs in one season, or no game found near
    the market's own close/expiration time."""

    AMBIGUOUS_TEAM_MAPPING = "ambiguous_team_mapping"
    """A team name/alias extracted from the market's own ticker/title
    could not be resolved to exactly one canonical team_id -- e.g. bare
    "Miami" (FL vs OH), matched against
    teams.registry.AmbiguousTeamAliasError."""

    PARSE_UNRESOLVED = "parse_unresolved"
    """The ticker/title/rules text could not be confidently parsed into a
    (market_family, side, line, team, operator) tuple at all -- e.g. a
    spread/total market whose settlement grammar could not be proven from
    genuine evidence (see contract_semantics.py)."""

    STALE_OR_CLOSED = "stale_or_closed"
    """Discovered but no longer open for trading (status != "open") at
    capture time, or its close_time has already passed."""

    NON_GAME_FUTURES = "non_game_futures"
    """A season/futures family (national champion, conference champion,
    CFP qualifier, Heisman, AP poll rank, win totals, undefeated season,
    coach markets, FCS champion) -- see cfb_market_family_registry.py's
    MarketScope.FUTURES entries. Deliberately isolated from the single-
    game pricing engine (mission section 21)."""

    DUPLICATE_OR_ALIAS = "duplicate_or_alias"
    """The same physical contract observed twice under different tickers
    (or the same ticker observed twice in one discovery sweep) --
    recorded once, every subsequent sighting classified here rather than
    silently double-counted."""

    OTHER_EXPLICIT_REASON = "other_explicit_reason"
    """A genuine, real classification need this vocabulary didn't
    anticipate. Always paired with a free-text detail string explaining
    exactly what happened -- never used as a silent catch-all."""


_REASON_TO_COVERAGE_OUTCOME: dict[KalshiCfbCoverageReason, CoverageOutcome] = {
    KalshiCfbCoverageReason.MAPPED_SUPPORTED: CoverageOutcome.EVALUATED,
    KalshiCfbCoverageReason.MAPPED_UNSUPPORTED_FAMILY: CoverageOutcome.UNSUPPORTED_MARKET,
    KalshiCfbCoverageReason.MAPPED_UNSUPPORTED_POPULATION: CoverageOutcome.UNSUPPORTED_MARKET,
    KalshiCfbCoverageReason.AMBIGUOUS_GAME_MAPPING: CoverageOutcome.TICKER_UNRESOLVED,
    KalshiCfbCoverageReason.AMBIGUOUS_TEAM_MAPPING: CoverageOutcome.TICKER_UNRESOLVED,
    KalshiCfbCoverageReason.PARSE_UNRESOLVED: CoverageOutcome.TICKER_UNRESOLVED,
    KalshiCfbCoverageReason.STALE_OR_CLOSED: CoverageOutcome.GAME_STARTED,
    KalshiCfbCoverageReason.NON_GAME_FUTURES: CoverageOutcome.UNSUPPORTED_MARKET,
    KalshiCfbCoverageReason.DUPLICATE_OR_ALIAS: CoverageOutcome.EVALUATION_FAILED,
    KalshiCfbCoverageReason.OTHER_EXPLICIT_REASON: CoverageOutcome.EVALUATION_FAILED,
}


def to_coverage_outcome(reason: KalshiCfbCoverageReason) -> CoverageOutcome:
    """Deterministic, total (every enum member has an entry -- see the
    module-load-time completeness assertion below) mapping from the
    specific Milestone D reason to the generic CoverageOutcome the
    existing CoverageLedger/MarketRecord machinery is driven with."""
    return _REASON_TO_COVERAGE_OUTCOME[reason]


def _assert_mapping_is_total() -> None:
    missing = set(KalshiCfbCoverageReason) - set(_REASON_TO_COVERAGE_OUTCOME)
    if missing:
        raise ValueError(f"KalshiCfbCoverageReason member(s) missing from the CoverageOutcome mapping: {missing!r}")


_assert_mapping_is_total()
