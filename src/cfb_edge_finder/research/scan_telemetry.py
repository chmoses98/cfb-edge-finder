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

    # "The sidecar could not be built" and "the sidecar ran and nothing
    # was eligible" leave every counter below at 0. Without this field
    # the two are indistinguishable in the log -- which is exactly how a
    # silently-None sidecar survived a live run on main.
    # --- Two-lane architecture (football-state decoupling) ---
    football_state_source: str = "not_resolved"
    """Where this run's football inputs came from: 'cache' (ZERO CFBD
    requests), 'live_full_refresh', 'live_schedule_refresh',
    'cache_after_refresh_failure', or 'unavailable' (fail-closed run)."""
    football_state_freshness: str = "unknown"
    football_state_schedule_age_minutes: float = 0.0
    cfbd_requests: int = 0
    """Live CFBD HTTP requests attempted by this run's football-state
    resolution. The headline decoupling invariant: 0 whenever the durable
    state was fresh."""
    kickoff_uncertain_games: int = 0
    """Games skipped fail-closed because a mapped Kalshi market's own
    close_time disagreed with the cached kickoff beyond tolerance."""
    cfbd_access_state: str = "not_assessed"
    """research/cfbd_access.py gate state this run: CFBD_ACCESS_OK,
    CFBD_QUOTA_EXHAUSTED (metered calls gated off), or
    CFBD_ACCESS_UNKNOWN."""
    reconciled_missed_checkpoints: int = 0
    """Terminal MISSED_WINDOW rows written by after-the-fact
    reconciliation this run (accounting only, never backfill)."""

    shadow_sidecar_state: str = "NOT_ATTEMPTED"
    shadow_rows_written: int = 0
    shadow_rows_duplicate: int = 0
    shadow_contracts_priced: int = 0
    shadow_game_transforms: int = 0
    shadow_games_offered: int = 0
    shadow_failures: int = 0
    # A bare failure count is not a diagnosis. Carries the exception type
    # names the sidecar actually caught, e.g. {"ModuleNotFoundError": 12}.
    shadow_failure_types: dict[str, int] = field(default_factory=dict)
    # Why a contract had no shadow, e.g. {"TALENT_MISSING_HOME": 4}.
    shadow_unavailable_reasons: dict[str, int] = field(default_factory=dict)
    """Research-sidecar counters. Kept beside the canonical counters so
    shadow coverage is visible in the same heartbeat, and separate from
    them so a shadow failure can never be mistaken for a collection
    failure."""
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
