"""In-memory coverage ledger with invariant checks.

Direct reproduction of edge-finder-api's dual-denominator coverage
accounting pattern (docs/MLB_ARCHITECTURE_AUDIT.md section 2): the ledger
does not just count states it already knows about -- assert_no_missing()
is given an independently-derived set of "tickers that should exist" (e.g.
from a raw discovery sweep) and raises if anything is missing, so a bug
that drops a market BEFORE it ever reaches the ledger is still caught.

Two orthogonal axes are tracked per entry (see schemas/common.py and
schemas/coverage.py for the full rationale): `transition()` moves a
market's CoverageOutcome through its audit-trailed pipeline history, while
`set_recommendation_readiness()` sets the separate, non-audit-trailed
business-value judgment -- changing one can never affect the other, and
`summary()` (coverage completeness) is computed from CoverageOutcome alone,
so a market sitting at WATCH or EARLY_VALUE is exactly as "accounted for"
as one that is ACTIONABLE or PASS.

This is intentionally a plain in-memory structure for the foundation
phase -- persistence (JSON/JSONL under data/, per docs/STORAGE_STRATEGY.md)
is a Milestone E concern, not this one.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cfb_edge_finder.schemas.common import CoverageOutcome, RecommendationReadiness
from cfb_edge_finder.schemas.coverage import CoverageLedgerEntry, StatusTransition


class CoverageInvariantError(Exception):
    """Raised when a discovered market is missing from the ledger, or an
    unknown ticker is transitioned without first being recorded as
    discovered. Both indicate a market silently fell out of the pipeline.
    """


class CoverageLedger:
    def __init__(self) -> None:
        self._entries: dict[str, CoverageLedgerEntry] = {}

    def record_discovered(
        self, market_ticker: str, game_id: str | None = None, at: datetime | None = None, reason: str | None = None
    ) -> CoverageLedgerEntry:
        if market_ticker in self._entries:
            raise CoverageInvariantError(f"{market_ticker!r} already recorded; use transition() instead")
        at = at or datetime.now(UTC)
        transition = StatusTransition(outcome=CoverageOutcome.DISCOVERED, at=at, reason=reason)
        entry = CoverageLedgerEntry(
            market_ticker=market_ticker,
            game_id=game_id,
            current_outcome=CoverageOutcome.DISCOVERED,
            history=[transition],
        )
        self._entries[market_ticker] = entry
        return entry

    def transition(
        self,
        market_ticker: str,
        new_outcome: CoverageOutcome,
        at: datetime | None = None,
        reason: str | None = None,
        game_id: str | None = None,
    ) -> CoverageLedgerEntry:
        if market_ticker not in self._entries:
            raise CoverageInvariantError(
                f"cannot transition {market_ticker!r} to {new_outcome.value!r}: never recorded as discovered"
            )
        entry = self._entries[market_ticker]
        at = at or datetime.now(UTC)
        new_history = [*entry.history, StatusTransition(outcome=new_outcome, at=at, reason=reason)]
        readiness = entry.recommendation_readiness
        # Moving off EVALUATED invalidates any prior recommendation
        # readiness -- it is only meaningful while current_outcome is
        # EVALUATED.
        if new_outcome != CoverageOutcome.EVALUATED:
            readiness = None
        # Reconstructed via the full constructor (not model_copy, which does
        # NOT re-run validators in pydantic v2) so the entry's invariants --
        # history/current_outcome agreement, readiness-requires-EVALUATED --
        # are actually re-checked on every mutation, not just at first
        # construction.
        updated = CoverageLedgerEntry(
            market_ticker=entry.market_ticker,
            game_id=game_id if game_id is not None else entry.game_id,
            current_outcome=new_outcome,
            history=new_history,
            recommendation_readiness=readiness,
        )
        self._entries[market_ticker] = updated
        return updated

    def set_recommendation_readiness(
        self, market_ticker: str, readiness: RecommendationReadiness
    ) -> CoverageLedgerEntry:
        """Set the orthogonal recommendation-readiness axis. Does not touch
        current_outcome or history -- only valid once current_outcome is
        already EVALUATED (CoverageLedgerEntry's validator enforces this).
        """
        if market_ticker not in self._entries:
            raise CoverageInvariantError(f"cannot set recommendation readiness for {market_ticker!r}: unknown ticker")
        entry = self._entries[market_ticker]
        # See transition()'s comment: reconstructed via the full
        # constructor, not model_copy, so the EVALUATED-only invariant is
        # actually re-checked here rather than silently bypassed.
        updated = CoverageLedgerEntry(
            market_ticker=entry.market_ticker,
            game_id=entry.game_id,
            current_outcome=entry.current_outcome,
            history=entry.history,
            recommendation_readiness=readiness,
        )
        self._entries[market_ticker] = updated
        return updated

    def get(self, market_ticker: str) -> CoverageLedgerEntry | None:
        return self._entries.get(market_ticker)

    def summary(self) -> dict[CoverageOutcome, int]:
        """Counts by CoverageOutcome only -- deliberately blind to
        recommendation_readiness, so completeness accounting can never be
        skewed by a market's business-value judgment. See
        `readiness_summary()` for the orthogonal breakdown.
        """
        counts: dict[CoverageOutcome, int] = {outcome: 0 for outcome in CoverageOutcome}
        for entry in self._entries.values():
            counts[entry.current_outcome] += 1
        return counts

    def readiness_summary(self) -> dict[RecommendationReadiness | None, int]:
        """Counts by RecommendationReadiness (including None, for markets
        that are either not yet EVALUATED or EVALUATED but not yet judged).
        This total always equals len(self) too, but partitions along the
        orthogonal axis -- proving neither axis can hide entries from the
        other.
        """
        counts: dict[RecommendationReadiness | None, int] = {readiness: 0 for readiness in RecommendationReadiness}
        counts[None] = 0
        for entry in self._entries.values():
            counts[entry.recommendation_readiness] += 1
        return counts

    def assert_no_missing(self, discovered_tickers: set[str]) -> None:
        """discovered_tickers should come from a source INDEPENDENT of this
        ledger's own record_discovered() calls (e.g. the raw Kalshi sweep
        response) so a bug in the code path that calls record_discovered()
        is still caught here rather than passing silently.
        """
        missing = discovered_tickers - self._entries.keys()
        if missing:
            raise CoverageInvariantError(
                f"{len(missing)} ticker(s) discovered but never entered the coverage ledger: "
                f"{sorted(missing)[:10]}{'...' if len(missing) > 10 else ''}"
            )

    def __len__(self) -> int:
        return len(self._entries)
