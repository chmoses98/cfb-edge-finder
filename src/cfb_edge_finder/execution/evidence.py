"""Point-in-time evidence provenance: WHERE a timestamp came from, and which of four states it is in.

*** WHY THIS EXISTS ***
The point-in-time guards (``decision/pit_guards.py``) only see a timestamp or ``None``. Every cause of
"no timestamp" -- the collector never ran, the collector ran and the source errored, the source answered
only for one team, the projection carries no model-run time -- collapsed into the same ``MISSING_*`` verdict,
and, worse, nothing upstream produced a committed timestamp at all, so RUN CFB blocked every candidate.

An evidence item therefore carries: the source id, ``observed_at`` (when the source was actually READ),
``as_of`` (what the source says its content is current to, when it says), and one status:

    OBSERVED          read, and within the freshness window at decision time
    STALE             read, but older than the freshness window
    UNAVAILABLE       a collector ran; the source had nothing usable, errored, or answered only partly
    NEVER_COLLECTED   no collector has produced this evidence at all
    FROM_FUTURE       claims to have been observed AFTER the decision time (look-ahead; never usable)

*** WHAT THIS MODULE MUST NEVER DO ***
It never invents an observation. ``observed_at`` is copied from what a collector recorded, or it is absent.
It never stamps the current time on anything: a projection with no model-run as_of is UNAVAILABLE, not
"as of now". Only OBSERVED/STALE/FROM_FUTURE items expose a guard timestamp, and the guards then decide.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from cfb_edge_finder.execution.context import ContextField
from cfb_edge_finder.execution.quality import FieldQuality

EVIDENCE_SCHEMA_VERSION = "cfb_availability_evidence/1.0.0"
EVIDENCE_INDEX_NAME = "availability_index.json"
DEFAULT_EVIDENCE_DIR = Path("data/evidence/availability")
AVAILABILITY_SOURCE_ID = "espn.core.injuries"
QUOTE_SOURCE_ID = "kalshi.market_catalog"
PROJECTION_SOURCE_ID = "external_handicap"


class EvidenceStatus(StrEnum):
    OBSERVED = "OBSERVED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    NEVER_COLLECTED = "NEVER_COLLECTED"
    FROM_FUTURE = "FROM_FUTURE"


#: Statuses whose timestamp may be handed to the guards. UNAVAILABLE / NEVER_COLLECTED expose none, so the
#: guard keeps blocking on a missing time rather than being given something to pass on.
_TIMED = frozenset({EvidenceStatus.OBSERVED, EvidenceStatus.STALE, EvidenceStatus.FROM_FUTURE})


def parse_utc(value: Any) -> datetime | None:
    """Timezone-aware UTC, or None. A naive timestamp is missing, never guessed to be UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(UTC)


@dataclass(frozen=True)
class EvidenceItem:
    evidence_type: str
    source_id: str | None
    status: EvidenceStatus
    observed_at: datetime | None = None
    as_of: datetime | None = None
    attempted_at: datetime | None = None
    age_seconds: float | None = None
    max_age_seconds: float | None = None
    detail: str | None = None

    @property
    def guard_time(self) -> datetime | None:
        """The timestamp the PIT guard may see. None for UNAVAILABLE / NEVER_COLLECTED."""
        return self.observed_at if self.status in _TIMED else None

    def as_dict(self) -> dict[str, Any]:
        def iso(v: datetime | None) -> str | None:
            return v.isoformat() if v else None

        return {
            "evidence_type": self.evidence_type,
            "source_id": self.source_id,
            "status": self.status.value,
            "observed_at": iso(self.observed_at),
            "as_of": iso(self.as_of),
            "attempted_at": iso(self.attempted_at),
            "age_seconds": None if self.age_seconds is None else round(self.age_seconds),
            "max_age_seconds": self.max_age_seconds,
            "detail": self.detail,
        }


def _timed_status(
    observed: datetime, decision_time: datetime, max_age: timedelta
) -> tuple[EvidenceStatus, float]:
    age = (decision_time - observed).total_seconds()
    if age < 0:
        return EvidenceStatus.FROM_FUTURE, age
    if age > max_age.total_seconds():
        return EvidenceStatus.STALE, age
    return EvidenceStatus.OBSERVED, age


def classify_availability(
    availability: dict[str, Any] | ContextField | None,
    *,
    decision_time: datetime,
    max_age: timedelta,
    evidence_type: str = "availability",
) -> EvidenceItem:
    """One availability domain (a packet's ``factual_context.domains.availability``) -> an evidence item."""
    if availability is None:
        return EvidenceItem(
            evidence_type, None, EvidenceStatus.NEVER_COLLECTED, max_age_seconds=max_age.total_seconds(),
            detail="no availability record exists for this game",
        )
    block = availability.as_dict() if isinstance(availability, ContextField) else availability
    quality = str(block.get("quality") or FieldQuality.MISSING.value)
    source = block.get("source")
    source_id = None if source in (None, "", "not_attempted") else str(source)
    observed = parse_utc(block.get("observed_at"))
    attempted = parse_utc(block.get("attempted_at"))
    as_of = parse_utc(block.get("source_as_of"))
    detail = block.get("detail")
    limit = max_age.total_seconds()

    def item(status: EvidenceStatus, **kw: Any) -> EvidenceItem:
        return EvidenceItem(
            evidence_type, source_id, status, as_of=as_of, attempted_at=attempted,
            max_age_seconds=limit, **kw,
        )

    if quality in (FieldQuality.FRESH.value, FieldQuality.STALE.value):
        if observed is None:
            return item(
                EvidenceStatus.UNAVAILABLE,
                detail="the record names no parseable observed_at, so it cannot be dated",
            )
        status, age = _timed_status(observed, decision_time, max_age)
        return item(status, observed_at=observed, age_seconds=age, detail=detail)
    if quality == FieldQuality.PARTIAL.value:
        return item(
            EvidenceStatus.UNAVAILABLE, observed_at=None,
            detail=f"incomplete observation (not usable as a full read): {detail}",
        )
    if quality == FieldQuality.MISSING.value:
        if attempted is not None:
            return item(EvidenceStatus.UNAVAILABLE, detail=detail)
        if source_id is None:
            return item(EvidenceStatus.NEVER_COLLECTED, detail=detail or "no collector has run")
        # A legacy record from before attempt times existed: a source is named but nothing is dated.
        return item(EvidenceStatus.UNAVAILABLE, detail=f"legacy record without an attempt time: {detail}")
    return item(EvidenceStatus.UNAVAILABLE, detail=f"quality {quality}: {detail}")


def classify_projection(
    handicap: Any | None, *, decision_time: datetime, max_age: timedelta
) -> EvidenceItem:
    """The model-run as_of of a projection (a handicap payload's ``handicapped_at``).

    The time is the one the projection's AUTHOR stated for the run that computed it from the point-in-time
    inputs it saw. It is never defaulted to now: a historical projection stamped with the current time would
    pass every freshness gate it should fail.
    """
    limit = max_age.total_seconds()
    if handicap is None:
        return EvidenceItem(
            "projection", None, EvidenceStatus.NEVER_COLLECTED, max_age_seconds=limit,
            detail="no projection (handicap) exists for this game",
        )
    source_id = str(getattr(handicap, "source", None) or PROJECTION_SOURCE_ID)
    raw = getattr(handicap, "handicapped_at", None)
    stated = parse_utc(raw)
    if raw in (None, ""):
        return EvidenceItem(
            "projection", source_id, EvidenceStatus.UNAVAILABLE, max_age_seconds=limit,
            detail="the projection carries no model-run as_of (handicapped_at); it cannot be dated",
        )
    if stated is None:
        return EvidenceItem(
            "projection", source_id, EvidenceStatus.UNAVAILABLE, max_age_seconds=limit,
            detail="handicapped_at is not a timezone-aware ISO-8601 timestamp",
        )
    status, age = _timed_status(stated, decision_time, max_age)
    return EvidenceItem(
        "projection", source_id, status, observed_at=stated, as_of=stated, age_seconds=age,
        max_age_seconds=limit,
    )


def classify_quote(
    captured_at: datetime | None, *, decision_time: datetime, max_age: timedelta
) -> EvidenceItem:
    limit = max_age.total_seconds()
    if captured_at is None:
        return EvidenceItem(
            "quote", QUOTE_SOURCE_ID, EvidenceStatus.NEVER_COLLECTED, max_age_seconds=limit,
            detail="the slate records no capture time",
        )
    status, age = _timed_status(captured_at, decision_time, max_age)
    return EvidenceItem(
        "quote", QUOTE_SOURCE_ID, status, observed_at=captured_at, as_of=captured_at, age_seconds=age,
        max_age_seconds=limit,
    )


# ------------------------------------------------------------------ committed availability evidence
#
# `collect_live_context.py` runs in CI with outbound access; RUN CFB runs against committed files. The
# per-game availability record below is the compact, committed bridge: exactly what the collector observed
# (and when), or exactly that it tried and failed. A game the collector did not reach has NO record, which
# is what NEVER_COLLECTED means -- the absence of a file is never filled in.


def record_for(game_key: str, kickoff: str | None, availability: ContextField, run_at: str) -> dict[str, Any]:
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "game_key": game_key,
        "kickoff": kickoff,
        "collector": {"script": "scripts/collect_live_context.py", "run_at": run_at},
        "availability": availability.as_dict(),
        "last_failed_attempt_at": None,
    }


def _field_from_record(record: dict[str, Any]) -> ContextField | None:
    block = record.get("availability")
    if not isinstance(block, dict):
        return None
    return ContextField(
        domain="availability",
        values=dict(block.get("values") or {}),
        source=str(block.get("source") or "not_attempted"),
        observed_at=block.get("observed_at"),
        quality=str(block.get("quality") or FieldQuality.MISSING.value),
        detail=block.get("detail"),
        attempted_at=block.get("attempted_at"),
        source_as_of=block.get("source_as_of"),
    )


def load_availability_evidence(evidence_dir: Path | None) -> dict[str, ContextField]:
    """game_key -> the committed availability field. A missing directory is an empty store."""
    if evidence_dir is None:
        return {}
    root = Path(evidence_dir)
    if not root.exists():
        return {}
    out: dict[str, ContextField] = {}
    for path in sorted(root.glob("*.json")):
        if path.name == EVIDENCE_INDEX_NAME:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict) or not record.get("game_key"):
            continue
        field_ = _field_from_record(record)
        if field_ is not None:
            out[str(record["game_key"])] = field_
    return out


def publish_availability_evidence(
    *,
    context_dir: Path,
    catalog_game_keys: set[str],
    evidence_dir: Path,
    run_at: str,
    kickoffs: dict[str, str | None] | None = None,
) -> dict[str, int]:
    """Fold a collector run's per-game contexts into the committed per-game evidence.

    * a new OBSERVED availability replaces the old record;
    * a new failed/unavailable attempt NEVER overwrites an older real observation (that observation is still
      true, merely older); it only records ``last_failed_attempt_at``;
    * a game with no prior record takes the new one even if it is a failed attempt (UNAVAILABLE);
    * a record for a game no longer in the catalog is pruned.
    """
    evidence_dir.mkdir(parents=True, exist_ok=True)
    kickoffs = kickoffs or {}
    counts = {"observed": 0, "unavailable": 0, "kept_older_observation": 0, "pruned": 0}
    for path in sorted(Path(context_dir).glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        game_key = str(payload.get("game_key") or "")
        if not game_key or game_key not in catalog_game_keys:
            continue
        block = (payload.get("domains") or {}).get("availability") or {}
        new = ContextField(
            domain="availability",
            values=dict(block.get("values") or {}),
            source=str(block.get("source") or "not_attempted"),
            observed_at=block.get("observed_at"),
            quality=str(block.get("quality") or FieldQuality.MISSING.value),
            detail=block.get("detail"),
            attempted_at=block.get("attempted_at"),
            source_as_of=block.get("source_as_of"),
        )
        if new.attempted_at is None and new.observed_at is None:
            continue  # the collector did not touch availability for this game: record nothing, invent nothing
        target = evidence_dir / f"{game_key}.json"
        record = record_for(game_key, kickoffs.get(game_key), new, run_at)
        new_ok = new.quality == FieldQuality.FRESH.value and parse_utc(new.observed_at) is not None
        if target.exists() and not new_ok:
            try:
                old = json.loads(target.read_text(encoding="utf-8"))
                old_field = _field_from_record(old)
            except (OSError, json.JSONDecodeError):
                old_field = None
            if old_field is not None and old_field.quality == FieldQuality.FRESH.value:
                old["last_failed_attempt_at"] = new.attempted_at
                record = old
                counts["kept_older_observation"] += 1
        elif new_ok:
            counts["observed"] += 1
        if record["availability"]["quality"] != FieldQuality.FRESH.value:
            counts["unavailable"] += 1
        target.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for path in sorted(evidence_dir.glob("*.json")):
        if path.name != EVIDENCE_INDEX_NAME and path.stem not in catalog_game_keys:
            path.unlink()
            counts["pruned"] += 1
    return counts


def write_index(evidence_dir: Path, catalog_game_keys: set[str], run_at: str) -> dict[str, Any]:
    """Counts-only summary beside the records, so a reader can see coverage without opening 250 files."""
    fields = load_availability_evidence(evidence_dir)
    statuses = {"fresh": 0, "partial": 0, "missing_attempted": 0, "missing_not_attempted": 0}
    games: dict[str, dict[str, Any]] = {}
    for key, f in sorted(fields.items()):
        if f.quality == FieldQuality.FRESH.value:
            statuses["fresh"] += 1
        elif f.quality == FieldQuality.PARTIAL.value:
            statuses["partial"] += 1
        elif f.attempted_at:
            statuses["missing_attempted"] += 1
        else:
            statuses["missing_not_attempted"] += 1
        games[key] = {"observed_at": f.observed_at, "attempted_at": f.attempted_at, "quality": f.quality}
    doc = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "generated_at": run_at,
        "source_id": AVAILABILITY_SOURCE_ID,
        "catalog_games": len(catalog_game_keys),
        "games_with_a_record": len(fields),
        "games_without_a_record_never_collected": len(catalog_game_keys - set(fields)),
        "by_record_quality": statuses,
        "games": games,
        "reading": (
            "A game with no record here was never collected; it is NOT 'no injuries'. A record's "
            "observed_at is when the source was read; freshness is judged against the decision time."
        ),
    }
    (Path(evidence_dir) / EVIDENCE_INDEX_NAME).write_text(
        json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return doc


__all__ = [
    "AVAILABILITY_SOURCE_ID",
    "DEFAULT_EVIDENCE_DIR",
    "EVIDENCE_INDEX_NAME",
    "EvidenceItem",
    "EvidenceStatus",
    "classify_availability",
    "classify_projection",
    "classify_quote",
    "load_availability_evidence",
    "parse_utc",
    "publish_availability_evidence",
    "write_index",
]
