"""In-memory coverage ledger with invariant checks.

Direct reproduction of edge-finder-api's dual-denominator coverage
accounting pattern (docs/MLB_ARCHITECTURE_AUDIT.md section 2): the ledger
does not just count states it already knows about -- assert_no_missing()
is given an independently-derived set of "tickers that should exist" (e.g.
from a raw discovery sweep) and raises if anything is missing, so a bug
that drops a market BEFORE it ever reaches the ledger is still caught.

This is intentionally a plain in-memory structure for the foundation
phase -- persistence (JSON/JSONL under data/, per docs/STORAGE_STRATEGY.md)
is a Milestone E concern, not this one.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cfb_edge_finder.schemas.common import MarketStatus
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
        transition = StatusTransition(status=MarketStatus.DISCOVERED, at=at, reason=reason)
        entry = CoverageLedgerEntry(
            market_ticker=market_ticker,
            game_id=game_id,
            current_status=MarketStatus.DISCOVERED,
            history=[transition],
        )
        self._entries[market_ticker] = entry
        return entry

    def transition(
        self,
        market_ticker: str,
        new_status: MarketStatus,
        at: datetime | None = None,
        reason: str | None = None,
        game_id: str | None = None,
    ) -> CoverageLedgerEntry:
        if market_ticker not in self._entries:
            raise CoverageInvariantError(
                f"cannot transition {market_ticker!r} to {new_status.value!r}: never recorded as discovered"
            )
        entry = self._entries[market_ticker]
        at = at or datetime.now(UTC)
        new_history = [*entry.history, StatusTransition(status=new_status, at=at, reason=reason)]
        updated = entry.model_copy(
            update={
                "current_status": new_status,
                "history": new_history,
                "game_id": game_id if game_id is not None else entry.game_id,
            }
        )
        self._entries[market_ticker] = updated
        return updated

    def get(self, market_ticker: str) -> CoverageLedgerEntry | None:
        return self._entries.get(market_ticker)

    def summary(self) -> dict[MarketStatus, int]:
        counts: dict[MarketStatus, int] = {status: 0 for status in MarketStatus}
        for entry in self._entries.values():
            counts[entry.current_status] += 1
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
