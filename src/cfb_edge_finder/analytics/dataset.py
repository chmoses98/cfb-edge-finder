"""Joining the immutable ledgers into an analysis dataset (mission
sections 2, 13, 21, 30).

*** READ-ONLY, ALWAYS ***
Both source ledgers are opened for reading and never written. Nothing
here recomputes a historical snapshot with today's model or today's
prices: every row carries the model probability and executable prices
that were ACTUALLY captured at that checkpoint, which is the entire point
of prospective collection. A metric derived from a re-priced snapshot
would be a backtest wearing a prospective label.

*** PROSPECTIVE-ONLY PROVENANCE (section 21) ***
`capture_mode == "PROSPECTIVE"` is enforced on every row admitted to the
primary dataset. Historical or retrospective fixtures used to test this
code must never reach a headline ROI or CLV number, and
`rejected_non_prospective` counts anything filtered out so the exclusion
is visible rather than silent.

*** UNSUPPORTED POPULATIONS (section 13) ***
FBS-vs-FCS and FCS-vs-FCS are UNSUPPORTED_FOR_PRICING. They are
partitioned into `diagnostic_rows` -- available for inspection, never
mixed into supported headline metrics.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from cfb_edge_finder.analytics.metrics import (
    CLOSING_CAPTURED_STATUS,
    ClosingLineValue,
    ProbabilityGaps,
    closing_line_value,
    probability_gaps,
)
from cfb_edge_finder.schemas.common import Side

PROSPECTIVE_CAPTURE_MODE = "PROSPECTIVE"
SETTLED_STATES = frozenset({"SETTLED_YES", "SETTLED_NO"})
SUPPORTED_FAMILIES = frozenset({"moneyline", "spread", "total"})


@dataclass(frozen=True)
class AnalysisRow:
    """One settled, supported, prospective observation with its metrics.

    Every linkage field the mission requires is carried explicitly so a
    number in a report can always be traced back to the exact captured
    snapshot that produced it."""

    observation_key: str
    attribution_key: str
    game_id: str
    market_ticker: str
    family: str
    timing_label: str
    model_version: str | None
    captured_at: str
    settled_at: str | None

    event_true: bool
    model_probability: float
    entry_yes_price: float | None
    entry_no_price: float | None
    entry_midpoint: float | None

    gaps: ProbabilityGaps
    yes_clv: ClosingLineValue
    no_clv: ClosingLineValue
    closing_status: str

    yes_research_unit_pnl: float | None
    yes_fee_adjusted_research_unit_pnl: float | None
    yes_estimated_fee: float | None
    no_research_unit_pnl: float | None
    no_fee_adjusted_research_unit_pnl: float | None
    no_estimated_fee: float | None

    fee_status: str | None
    fee_schedule_version: str | None


@dataclass
class DatasetHealth:
    """Problems found while building the dataset. Mission section 30: any
    of these must be visible; several are hard errors."""

    duplicate_observation_keys: int = 0
    duplicate_attribution_keys: int = 0
    settlement_mismatches: int = 0
    impossible_probabilities: int = 0
    missing_provenance: int = 0
    malformed_close_links: int = 0
    unsupported_leaked_into_primary: int = 0
    rejected_non_prospective: int = 0
    malformed_rows: int = 0

    @property
    def has_fatal(self) -> bool:
        """Conditions that invalidate the analysis rather than merely
        limiting it. A settlement mismatch means a contract's outcome is
        in dispute; a duplicate key means the corpus invariant broke;
        unsupported leakage means a headline number is contaminated."""
        return bool(
            self.duplicate_observation_keys
            or self.duplicate_attribution_keys
            or self.settlement_mismatches
            or self.unsupported_leaked_into_primary
            or self.impossible_probabilities
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "duplicate_observation_keys": self.duplicate_observation_keys,
            "duplicate_attribution_keys": self.duplicate_attribution_keys,
            "settlement_mismatches": self.settlement_mismatches,
            "impossible_probabilities": self.impossible_probabilities,
            "missing_provenance": self.missing_provenance,
            "malformed_close_links": self.malformed_close_links,
            "unsupported_leaked_into_primary": self.unsupported_leaked_into_primary,
            "rejected_non_prospective": self.rejected_non_prospective,
            "malformed_rows": self.malformed_rows,
        }


@dataclass
class AnalysisDataset:
    rows: list[AnalysisRow] = field(default_factory=list)
    diagnostic_rows: list[AnalysisRow] = field(default_factory=list)
    """Unsupported populations -- inspectable, never in headline metrics."""

    total_observations: int = 0
    supported_observations: int = 0
    attributions_seen: int = 0
    terminal_attributions: int = 0
    games: set[str] = field(default_factory=set)
    model_versions: set[str] = field(default_factory=set)
    closing_status_counts: Counter = field(default_factory=Counter)
    health: DatasetHealth = field(default_factory=DatasetHealth)
    load_seconds: float = 0.0
    ledger_load_count: int = 0

    @property
    def settled_supported_n(self) -> int:
        return len(self.rows)

    @property
    def closing_available_n(self) -> int:
        return sum(1 for r in self.rows if r.closing_status == CLOSING_CAPTURED_STATUS)


def _iter_jsonl(path: Path, health: DatasetHealth):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                health.malformed_rows += 1


def _valid_probability(p: float | None) -> bool:
    return p is None or (0.0 <= p <= 1.0)


def build_dataset(observations_path: Path, attributions_path: Path) -> AnalysisDataset:
    """Join the two ledgers into an analysis dataset.

    Each file is read EXACTLY ONCE and indexed by key, so the join is
    O(observations + attributions) rather than a nested scan. This repo
    has already paid once for an O(n x m) rescan in the capture path (see
    docs/PERFORMANCE.md); analytics does not repeat it."""
    started = time.perf_counter()
    dataset = AnalysisDataset()
    health = dataset.health

    observations: dict[str, dict] = {}
    seen_obs_keys: set[str] = set()
    for obj in _iter_jsonl(observations_path, health):
        key = obj.get("observation_key")
        if not isinstance(key, str):
            health.missing_provenance += 1
            continue
        if key in seen_obs_keys:
            health.duplicate_observation_keys += 1
            continue
        seen_obs_keys.add(key)
        observations[key] = obj
    dataset.ledger_load_count += 1
    dataset.total_observations = len(observations)

    seen_attr_keys: set[str] = set()
    for attr in _iter_jsonl(attributions_path, health):
        dataset.attributions_seen += 1
        akey = attr.get("attribution_key")
        if not isinstance(akey, str):
            health.missing_provenance += 1
            continue
        if akey in seen_attr_keys:
            health.duplicate_attribution_keys += 1
            continue
        seen_attr_keys.add(akey)

        state = attr.get("state")
        if state == "SETTLEMENT_MISMATCH":
            health.settlement_mismatches += 1
            continue
        if state not in SETTLED_STATES:
            continue
        dataset.terminal_attributions += 1

        obs = observations.get(attr.get("observation_key", ""))
        if obs is None:
            health.missing_provenance += 1
            continue

        # --- Prospective-only enforcement (section 21) ---
        if obs.get("capture_mode") != PROSPECTIVE_CAPTURE_MODE:
            health.rejected_non_prospective += 1
            continue

        observation = obs.get("observation") or {}
        family = attr.get("family") or observation.get("family")
        model_probability = observation.get("model_probability")
        event_true = attr.get("event_true")
        if model_probability is None or event_true is None:
            health.missing_provenance += 1
            continue
        if not _valid_probability(model_probability):
            health.impossible_probabilities += 1
            continue

        entry_yes = observation.get("executable_yes_price")
        entry_no = observation.get("executable_no_price")
        if not (_valid_probability(entry_yes) and _valid_probability(entry_no)):
            health.impossible_probabilities += 1
            continue

        closing = attr.get("closing") or {}
        closing_status = closing.get("closing_status") or "CLOSING_MISSING_NO_SCAN_IN_WINDOW"
        if closing.get("closing_captured") and closing.get("closing_yes_price") is None:
            health.malformed_close_links += 1

        yes_econ = attr.get("yes_economics") or {}
        no_econ = attr.get("no_economics") or {}

        row = AnalysisRow(
            observation_key=attr["observation_key"],
            attribution_key=akey,
            game_id=attr.get("game_id") or "unmapped",
            market_ticker=attr.get("kalshi_market_ticker") or "",
            family=family or "unknown",
            timing_label=attr.get("timing_label") or observation.get("snapshot_timing", {}).get("label", "unknown"),
            model_version=attr.get("model_version"),
            captured_at=attr.get("captured_at") or observation.get("captured_at") or "",
            settled_at=attr.get("settled_at"),
            event_true=bool(event_true),
            model_probability=float(model_probability),
            entry_yes_price=entry_yes,
            entry_no_price=entry_no,
            entry_midpoint=observation.get("market_midpoint"),
            gaps=probability_gaps(
                model_probability=model_probability,
                executable_yes_price=entry_yes,
                executable_no_price=entry_no,
            ),
            yes_clv=closing_line_value(
                side=Side.YES, entry_price=entry_yes,
                closing_price=closing.get("closing_yes_price"), closing_status=closing_status,
            ),
            no_clv=closing_line_value(
                side=Side.NO, entry_price=entry_no,
                closing_price=closing.get("closing_no_price"), closing_status=closing_status,
            ),
            closing_status=closing_status,
            yes_research_unit_pnl=yes_econ.get("research_unit_pnl"),
            yes_fee_adjusted_research_unit_pnl=yes_econ.get("fee_adjusted_research_unit_pnl"),
            yes_estimated_fee=yes_econ.get("estimated_fee"),
            no_research_unit_pnl=no_econ.get("research_unit_pnl"),
            no_fee_adjusted_research_unit_pnl=no_econ.get("fee_adjusted_research_unit_pnl"),
            no_estimated_fee=no_econ.get("estimated_fee"),
            fee_status=attr.get("fee_status"),
            fee_schedule_version=attr.get("fee_schedule_version"),
        )

        if row.family in SUPPORTED_FAMILIES:
            dataset.rows.append(row)
            dataset.games.add(row.game_id)
            if row.model_version:
                dataset.model_versions.add(row.model_version)
            dataset.closing_status_counts[row.closing_status] += 1
        else:
            dataset.diagnostic_rows.append(row)
    dataset.ledger_load_count += 1

    dataset.supported_observations = sum(
        1
        for o in observations.values()
        if (o.get("observation") or {}).get("pricing_status") == "model_priced"
    )
    # Belt-and-braces: nothing unsupported may have reached the primary set.
    dataset.health.unsupported_leaked_into_primary = sum(
        1 for r in dataset.rows if r.family not in SUPPORTED_FAMILIES
    )
    dataset.load_seconds = time.perf_counter() - started
    return dataset
