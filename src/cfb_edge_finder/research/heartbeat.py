"""Operational telemetry for collector invocations -- one small row per run.

Separate from the observation corpus on purpose. The corpus is immutable
research evidence and must stay uncontaminated by operational noise; this
is the opposite kind of data -- high frequency, low value per row, useful
only for answering "is the machine running".

Deliberately NOT recorded here: prices, probabilities, per-market rows,
anything a research conclusion could be drawn from. A heartbeat says how
many markets were seen, never what they were quoted at.

Append-only, same as the corpus, and written to its own family
directory so a heartbeat write can never interleave with or corrupt an
observation write. Sharded by the UTC date of `invoked_at` on the same
rule as every other family (research.shards) -- the season monolith this
used to be is exactly what hit GitHub's blob limit for `observations`.

Heartbeats are the ONE family with no dedup key: a run that repeats an
earlier run's field values is still a distinct invocation, so rows are
appended unconditionally and `trim_heartbeats` bounds growth instead.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from cfb_edge_finder.research import shards

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

    cfbd_access_state: str | None = None
    """research/cfbd_access.py state: CFBD_ACCESS_OK /
    CFBD_QUOTA_EXHAUSTED / CFBD_ACCESS_UNKNOWN. None means the run
    predates quota observability -- never 'healthy'."""
    cfbd_quota_limit: int | None = None
    cfbd_quota_remaining: int | None = None
    cfbd_quota_resets_at: str | None = None
    """Authoritative reset instant from CFBD's own unmetered GET /info
    (first of the next calendar month, 00:00 UTC -- live-verified, run
    33349348575). None when no /info evidence exists -- never derived
    from billing conventions."""
    cfbd_next_probe_at: str | None = None
    """While quota-gated: when the next unmetered recovery probe is due."""

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


def heartbeat_base_dir(repo_dir: Path) -> Path:
    return repo_dir / "data" / "research"


def heartbeat_sources(repo_dir: Path, season: int) -> list[Path]:
    """Every file holding this season's heartbeats -- the legacy
    `{season}.jsonl` monolith while it still exists, then the date
    shards in chronological order."""
    return shards.source_paths(heartbeat_base_dir(repo_dir), shards.HEARTBEATS_SUBDIR, season)


def append_heartbeat(repo_dir: Path, season: int, beat: Heartbeat) -> list[Path]:
    """Append one heartbeat to its UTC-date shard. Never raises into the
    caller's control flow: a telemetry failure must not fail a collection
    run that otherwise succeeded, because that would turn an
    observability problem into a data-loss problem. Returns the files
    written (empty on a swallowed failure)."""
    try:
        written = shards.append_lines(
            heartbeat_base_dir(repo_dir),
            shards.HEARTBEATS_SUBDIR,
            season,
            [(shards.shard_date_for(asdict(beat), shards.HEARTBEATS_SUBDIR), beat.to_json())],
        )
    except OSError:
        return []
    return list(written)


def load_heartbeats(source) -> list[dict]:
    """Accepts a single file (a legacy monolith, or one a workflow
    materialised with `git show`), a shard directory, or the list from
    `heartbeat_sources`."""
    rows = []
    for _path, line in shards.iter_raw_lines(source):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def trim_heartbeats(repo_dir: Path, season: int, max_rows: int = MAX_HEARTBEAT_ROWS) -> int:
    """Keep only the newest `max_rows` ACROSS the season, oldest shards
    dropped first. Returns rows removed.

    Shard-aware on purpose: trimming by rewriting one file would have
    silently stopped bounding anything once the ledger was sharded. Whole
    shards that fall entirely outside the window are removed and at most
    one boundary shard is rewritten; newer shards are never touched, so
    already-pushed blobs stay byte-stable."""
    base = heartbeat_base_dir(repo_dir)
    paths = shards.source_paths(base, shards.HEARTBEATS_SUBDIR, season)
    counts = [(path, sum(1 for _ in shards.iter_raw_lines(path))) for path in paths]
    total = sum(count for _path, count in counts)
    if total <= max_rows:
        return 0

    to_drop = total - max_rows
    removed = 0
    for path, count in counts:
        if removed >= to_drop:
            break
        if count <= to_drop - removed:
            path.unlink()
            removed += count
            continue
        keep_from = to_drop - removed
        kept = [line for _p, line in shards.iter_raw_lines(path)][keep_from:]
        path.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
        removed += keep_from
    return removed


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
