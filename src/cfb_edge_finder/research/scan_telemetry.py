"""Run-level performance telemetry for the scheduled research scanner.

*** WHY RUN-LEVEL, NEVER PER-TICKER ***
The regression this exists to make obvious is a SCALING one: the scanner
used to re-read the whole observations corpus once per market ticker, so
its runtime grew with the corpus even on runs that captured nothing (see
research/persistence.py's one-load-per-run contract). A per-ticker log
line would bury that in thousands of lines of noise and add its own I/O
cost to the very loop being measured; one compact JSON object per run
makes "history_load_count went above 1" or "wall_clock_seconds doubled at
constant market count" immediately greppable across runs instead.

These are OBSERVABILITY counters only. Nothing here participates in a
research decision: no field is read back by pricing, mapping, scheduling,
or persistence, and the corpus rows a run writes are byte-for-byte
identical whether or not telemetry is collected.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class ScanTelemetry:
    """One scanner run's performance record.

    `history_load_count` is the headline invariant: it must be exactly 1
    per scan attempt (see tests/test_research_scan_performance.py, which
    asserts it rather than trusting review to catch a reintroduced
    per-ticker read)."""

    trigger_type: str = "local"
    """'schedule', 'workflow_dispatch', or 'local'. Provenance only --
    manual and scheduled runs share identical due-label logic and
    duplicate protection (mission section 20), so this records HOW a run
    started, never changes WHAT it does."""

    run_started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    run_completed_at: str | None = None
    wall_clock_seconds: float = 0.0

    discovered_market_count: int = 0
    observation_count: int = 0
    history_row_count: int = 0
    history_load_count: int = 0
    history_load_seconds: float = 0.0
    distinct_games: int = 0
    game_projection_count: int = 0
    ratings_fit_count: int = 0
    history_fetch_count: int = 0
    """CFBD multi-season history fetches this run. 0 on a scan with
    nothing due (the fetch is deferred to the first projection), 1
    otherwise -- never more."""
    history_fetch_seconds: float = 0.0
    priced_contract_count: int = 0
    unresolved_count: int = 0
    api_failure_count: int = 0
    closing_due_count: int = 0
    closing_captured_count: int = 0
    duplicate_count: int = 0
    malformed_row_count: int = 0
    persistence_write_seconds: float = 0.0

    market_discovery_seconds: float = 0.0
    game_mapping_seconds: float = 0.0
    projection_seconds: float = 0.0
    contract_pricing_seconds: float = 0.0

    _started_monotonic: float = field(default_factory=time.perf_counter, repr=False)

    @contextmanager
    def phase(self, attribute: str):
        """Accumulates elapsed wall time into one named `*_seconds` field.
        Additive (never assigning) so a phase entered once per event still
        totals correctly across the whole run."""
        started = time.perf_counter()
        try:
            yield
        finally:
            setattr(self, attribute, getattr(self, attribute) + (time.perf_counter() - started))

    def finish(self) -> None:
        self.wall_clock_seconds = time.perf_counter() - self._started_monotonic
        self.run_completed_at = datetime.now(UTC).isoformat()

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if not k.startswith("_")}
