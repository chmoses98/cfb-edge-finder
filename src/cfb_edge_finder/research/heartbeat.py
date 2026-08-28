"""Operational telemetry for collector invocations -- one small row per run.

Separate from the observation corpus on purpose. The corpus is immutable
research evidence and must stay uncontaminated by operational noise; this
is the opposite kind of data -- high frequency, low value per row, useful
only for answering "is the machine running".

Deliberately NOT recorded here: prices, probabilities, per-market rows,
anything a research conclusion could be drawn from. A heartbeat says how
many markets were seen, never what they were quoted at.

Append-only, same as the corpus, and written to its own file so a
heartbeat write can never interleave with or corrupt an observation
write.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

HEARTBEAT_SCHEMA_VERSION = "research_heartbeat_v1"

MAX_HEARTBEAT_ROWS = 20_000
"""Kept bounded so operational telemetry cannot grow without limit in a
git-backed store. At the intended cadence this is many months of runs;
trimming keeps the newest, since staleness questions are about recent
history."""


@dataclass(frozen=True)
class Heartbeat:
    """What one collector invocation did. Every field answers an
    operational question the Week 1 audit had to reconstruct by hand from
    Actions logs."""

    schema_version: str
    run_id: str | None
    trigger_type: str
    invoked_at: str
    started_at: str
    finished_at: str
    succeeded: bool

    markets_discovered: int = 0
    labels_due: int = 0
    labels_captured: int = 0
    duplicates_skipped: int = 0
    malformed_rows: int = 0
    api_failures: int = 0

    closing_labels_due: int = 0
    closing_labels_captured: int = 0
    """CLOSING specifically, split out from labels_due/labels_captured.
    A missed CLOSING is unrecoverable -- its window is
    0 < minutes_to_kickoff <= 14 and it is never backfilled -- so it
    cannot be left buried inside an aggregate that a healthy T_24H count
    can mask. Defaults of 0 mean 'this run predates the field', which the
    ops check reads as 'nothing observed', never as 'nothing missed'."""

    cfbd_healthy: bool | None = None
    kalshi_healthy: bool | None = None

    schedule_fetch_success: bool | None = None
    """Positive proof the schedule source answered. None means the run
    predates this field -- NOT that the fetch failed. After the
    2026-08-27 incident the distinction matters: a missing value and a
    failed fetch were previously indistinguishable, which is how a
    conductor with no credential looked healthy."""

    schedule_state: str | None = None
    """research/trigger.py SchedulePlanningState. Says WHICH zero a zero
    is -- empty schedule, nothing upcoming, nothing supported, supported
    but beyond the horizon, or a real failure."""

    total_schedule_games: int | None = None
    supported_upcoming_games: int | None = None

    next_supported_kickoff: str | None = None
    next_critical_checkpoint: str | None = None
    next_critical_checkpoint_at: str | None = None

    detail: str = ""
    diagnostics: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def heartbeat_path(repo_dir: Path, season: int) -> Path:
    return repo_dir / "data" / "research" / "heartbeats" / f"{season}.jsonl"


def append_heartbeat(repo_dir: Path, season: int, beat: Heartbeat) -> Path:
    """Append one heartbeat. Never raises into the caller's control flow:
    a telemetry failure must not fail a collection run that otherwise
    succeeded, because that would turn an observability problem into a
    data-loss problem."""
    path = heartbeat_path(repo_dir, season)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(beat.to_json() + "\n")
    except OSError:
        return path
    return path


def load_heartbeats(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def trim_heartbeats(path: Path, max_rows: int = MAX_HEARTBEAT_ROWS) -> int:
    """Keep only the newest `max_rows`. Returns rows removed."""
    rows = load_heartbeats(path)
    if len(rows) <= max_rows:
        return 0
    keep = rows[-max_rows:]
    body = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in keep)
    path.write_text(body, encoding="utf-8")
    return len(rows) - len(keep)


def last_successful_run(rows: list[dict], trigger_type: str | None = None) -> datetime | None:
    """Most recent successful run, optionally restricted to one trigger.

    The per-trigger form is what makes a half-dead trigger layer visible:
    if the conductor has stopped but cron happened to fire ten minutes
    ago, the overall answer looks fine while the mechanism that actually
    protects CLOSING is dead."""
    best: datetime | None = None
    for row in rows:
        if not row.get("succeeded"):
            continue
        if trigger_type is not None and row.get("trigger_type") != trigger_type:
            continue
        stamp = row.get("finished_at") or row.get("started_at")
        if not stamp:
            continue
        try:
            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            continue
        if best is None or parsed > best:
            best = parsed
    return best
