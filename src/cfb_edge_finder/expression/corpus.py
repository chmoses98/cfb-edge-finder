"""Loading captured observations into `ContractSnapshot`s.

Read-only over the observation ledger. Nothing is written, and no
historical snapshot is re-priced with today's model -- every field comes
from what was actually captured.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from cfb_edge_finder.expression.grouping import ContractSnapshot
from cfb_edge_finder.expression.taxonomy import ContractSemantics
from cfb_edge_finder.schemas.common import MarketFamily, Side

LATEST = "latest"
EARLIEST = "earliest"


@dataclass
class CorpusLoadResult:
    snapshots: list[ContractSnapshot] = field(default_factory=list)
    rows_read: int = 0
    malformed_rows: int = 0
    tickers_seen: int = 0
    snapshots_collapsed: int = 0
    timing_labels: dict[str, int] = field(default_factory=dict)
    load_seconds: float = 0.0
    ledger_load_count: int = 0


def _enum_or_none(enum_cls, value):
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        return None


def load_contract_snapshots(
    observations_path: Path,
    *,
    snapshot_selection: str = LATEST,
    timing_label: str | None = None,
) -> CorpusLoadResult:
    """One pass over the ledger, keeping ONE snapshot per ticker.

    Expression structure describes the market at an instant. Mixing an
    EARLY_OPEN ask with a T_30 ask on a sibling contract would invent
    price relationships that never coexisted, so a single snapshot per
    ticker is selected and the collapse is counted."""
    result = CorpusLoadResult()
    started = time.perf_counter()
    result.ledger_load_count = 1
    if not observations_path.exists():
        result.load_seconds = time.perf_counter() - started
        return result

    chosen: dict[str, tuple[str, ContractSnapshot]] = {}
    with observations_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                result.malformed_rows += 1
                continue
            result.rows_read += 1

            observation = row.get("observation") or {}
            ticker = observation.get("kalshi_market_ticker")
            game_id = observation.get("game_id")
            if not isinstance(ticker, str) or not isinstance(game_id, str):
                continue

            label = (observation.get("snapshot_timing") or {}).get("label") or "unknown"
            result.timing_labels[label] = result.timing_labels.get(label, 0) + 1
            if timing_label is not None and label != timing_label:
                continue

            captured_at = observation.get("captured_at") or ""
            model_version = (observation.get("model_version") or {}).get("model_version")
            snapshot = ContractSnapshot(
                semantics=ContractSemantics(
                    market_ticker=ticker,
                    game_id=game_id,
                    family=_enum_or_none(MarketFamily, observation.get("family")),
                    team=_enum_or_none(Side, observation.get("team")),
                    side=_enum_or_none(Side, observation.get("side")),
                    threshold=observation.get("threshold"),
                    semantic_operator=observation.get("semantic_operator"),
                    parse_status=observation.get("parse_status"),
                ),
                timing_label=label,
                captured_at=captured_at,
                model_probability=observation.get("model_probability"),
                executable_yes_price=observation.get("executable_yes_price"),
                executable_no_price=observation.get("executable_no_price"),
                market_status=observation.get("market_status"),
                fee_status=observation.get("fee_status"),
                fee_schedule_version=observation.get("fee_schedule_version"),
                model_version=model_version,
                pricing_status=observation.get("pricing_status"),
                series_ticker=ticker.split("-", 1)[0] or None,
                schema_version=row.get("schema_version"),
            )

            existing = chosen.get(ticker)
            if existing is None:
                chosen[ticker] = (captured_at, snapshot)
                continue
            result.snapshots_collapsed += 1
            keep_new = captured_at > existing[0] if snapshot_selection == LATEST else captured_at < existing[0]
            if keep_new:
                chosen[ticker] = (captured_at, snapshot)

    result.snapshots = [s for _, s in chosen.values()]
    result.tickers_seen = len(chosen)
    result.load_seconds = time.perf_counter() - started
    return result
