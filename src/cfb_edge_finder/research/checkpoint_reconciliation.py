"""After-the-fact checkpoint reconciliation: turns permanent ambiguous
ABSENCE into explicit terminal accounting.

*** THE HOLE THIS CLOSES (live incident, 2026-08-29) ***
The design promise is that a market reaching kickoff without a CLOSING
capture must say WHY in the durable capture-state log. The in-scan
accounting honors that -- but only for markets the CURRENT scan
discovers. When the collector was hard-down across an entire closing
window (CFBD 429 from 16:50Z), every run crashed BEFORE accounting, and
by the time collection could resume the affected markets (SJSU@USC,
NCST@UVA) had left `active` status, so no later scan would ever discover
them again. Result: their T_6H..CLOSING checkpoints are absent with no
recorded reason -- exactly the ambiguity the promise forbids.

*** WHAT RECONCILIATION DOES -- AND POINTEDLY DOES NOT ***
It re-reads only DURABLE local data (the observations ledger and the
capture-state log -- zero network), reconstructs each known ticker's
last-recorded kickoff, and for every pregame label whose window has
provably passed without a capture writes one terminal
`CaptureState.MISSED_WINDOW` row whose detail names the reconciliation.
This is ACCOUNTING ONLY:

  - no observation row is ever created -- a missed window stays missed;
  - no existing row is modified -- capture_state is append-only and the
    existing (game, ticker, label, state) dedup key makes reconciliation
    idempotent across runs;
  - windows still open, games with unknown kickoff, and already-terminal
    checkpoints are left alone;
  - the window math is `timing.resolve_all_bucket_states` -- the SAME
    classifier the in-scan accounting uses, so the two paths cannot
    disagree about what "missed" means.

Because it needs no external dependency, reconciliation also runs on the
fail-closed path (football state unavailable), so the FIRST run after an
outage -- successful or not -- resolves the outage's silence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cfb_edge_finder.research import persistence, timing
from cfb_edge_finder.schemas.capture_state import (
    TERMINAL_CAPTURE_STATES,
    CaptureState,
    CaptureStateRecord,
)

RECONCILED_DETAIL = (
    "reconciled after the fact: window passed with no capture recorded "
    "(collector outage or external-dependency failure during the window); "
    "accounting only -- never a backfilled observation"
)


@dataclass(frozen=True)
class _TickerFacts:
    game_id: str
    kickoff_utc: datetime | None
    captured_labels: frozenset[str]


def _parse_iso(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _ticker_facts_from_ledger(observations_path: Path) -> dict[str, _TickerFacts]:
    """One lenient pass over the raw ledger: per ticker, the game, the
    LAST-recorded kickoff (latest capture wins -- reschedules recorded by
    later captures supersede earlier kickoffs), and the labels genuinely
    captured. Malformed lines are skipped exactly as the corpus loaders
    skip them."""
    facts: dict[str, dict] = {}
    if not observations_path.exists():
        return {}
    with observations_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            observation = row.get("observation") or {}
            ticker = observation.get("kalshi_market_ticker")
            game_id = observation.get("game_id")
            if not isinstance(ticker, str) or not isinstance(game_id, str):
                continue
            label = (observation.get("snapshot_timing") or {}).get("label")
            captured_at = str(observation.get("captured_at") or "")
            entry = facts.setdefault(
                ticker, {"game_id": game_id, "kickoff": None, "kick_seen_at": "", "labels": set()}
            )
            if isinstance(label, str):
                entry["labels"].add(label)
            kickoff = _parse_iso(row.get("kickoff_utc_at_capture"))
            if kickoff is not None and captured_at >= entry["kick_seen_at"]:
                entry["kickoff"] = kickoff
                entry["kick_seen_at"] = captured_at
    return {
        ticker: _TickerFacts(
            game_id=entry["game_id"],
            kickoff_utc=entry["kickoff"],
            captured_labels=frozenset(entry["labels"]),
        )
        for ticker, entry in facts.items()
    }


def _ticker_facts_from_index(index) -> dict[str, _TickerFacts]:
    """The zero-extra-read path: the scanner's ObservationIndex already
    derived game/kickoff/labels per ticker in its one pass -- consuming
    it keeps the corpus's read-the-file-exactly-once invariant intact."""
    facts: dict[str, _TickerFacts] = {}
    for ticker, game_id in index.ticker_game_ids.items():
        kick = index.ticker_kickoffs.get(ticker)
        facts[ticker] = _TickerFacts(
            game_id=game_id,
            kickoff_utc=_parse_iso(kick[1]) if kick else None,
            captured_labels=frozenset(index.captured_labels_for(ticker)),
        )
    return facts


def build_reconciliation_rows(
    observations_path: Path,
    capture_state_path: Path,
    *,
    now: datetime,
    run_id: str | None,
    index=None,
) -> list[CaptureStateRecord]:
    """The rows that would make every provably-passed, uncaptured,
    not-yet-terminally-accounted checkpoint explicit. Pure function of
    durable local data -- callers append them through the normal
    deduplicating persistence path. With `index` supplied (the scanner's
    already-loaded ObservationIndex) the ledger is NOT re-read; without
    one (the fail-closed path, which loads no index) a single lenient
    pass is made here."""
    facts = _ticker_facts_from_index(index) if index is not None else _ticker_facts_from_ledger(observations_path)
    if not facts:
        return []

    existing = persistence.read_capture_state_rows(capture_state_path) if capture_state_path.exists() else []
    terminal: set[tuple[str, str]] = {
        (row.kalshi_market_ticker, row.timing_label)
        for row in existing
        if row.state in TERMINAL_CAPTURE_STATES
    }

    rows: list[CaptureStateRecord] = []
    for ticker, fact in sorted(facts.items()):
        if fact.kickoff_utc is None:
            continue  # unknown kickoff: cannot prove a window passed -- leave alone
        # game_started=True only once the last-known kickoff has passed;
        # for future games this still terminally accounts windows whose
        # far edge is already behind `now` (resolve_all_bucket_states
        # marks exactly those MISSED_WINDOW).
        states = timing.resolve_all_bucket_states(
            kickoff_utc=fact.kickoff_utc,
            now=now,
            already_captured_labels=set(fact.captured_labels),
            game_started=now >= fact.kickoff_utc,
        )
        for label, state in states.items():
            if state is not CaptureState.MISSED_WINDOW:
                continue
            if (ticker, label) in terminal:
                continue
            rows.append(
                CaptureStateRecord(
                    game_id=fact.game_id,
                    kalshi_market_ticker=ticker,
                    timing_label=label,
                    state=CaptureState.MISSED_WINDOW,
                    observed_at=now,
                    detail=RECONCILED_DETAIL,
                    run_id=run_id,
                )
            )
    return rows


def reconcile(
    observations_path: Path,
    capture_state_path: Path,
    *,
    now: datetime,
    run_id: str | None,
    index=None,
) -> int:
    """Append the reconciliation rows; returns how many were genuinely
    new (the dedup key absorbs re-runs)."""
    rows = build_reconciliation_rows(observations_path, capture_state_path, now=now, run_id=run_id, index=index)
    if not rows:
        return 0
    result = persistence.append_capture_state_rows(capture_state_path, rows)
    return result.written
