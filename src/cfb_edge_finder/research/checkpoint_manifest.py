"""What the system knew at one prospective checkpoint.

*** THE QUESTION THIS ANSWERS, MONTHS LATER ***

    "At 15:47 UTC on 2026-08-29, when we recorded a CLOSING quote for
     this contract -- what exactly did the system know?"

Without an answer, a research finding from Week 1 cannot be defended or
reproduced: the model changes, the schema changes, the fee schedule
changes, and a stored probability becomes an orphan number.

*** WHY A MANIFEST RATHER THAN COPYING THE DATA ***

The corpus already stores every observation. Duplicating it would double
storage and create a second copy that can silently disagree with the
first. A manifest stores IDENTIFIERS and VERSIONS -- the things needed to
re-find and re-interpret the original row -- and nothing that is already
one dereference away.

*** COMPLETENESS IS MEASURED, NOT ASSUMED ***

`missing_fields` names what a manifest lacks, and `is_complete` is
computed from it. A manifest that quietly omitted a field would be worse
than no manifest, because it would look authoritative. The required set
is deliberately small and every member is something without which the
row genuinely cannot be re-interpreted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

CHECKPOINT_MANIFEST_VERSION = "checkpoint_manifest_v1"

REQUIRED_FIELDS = (
    "game_id",
    "captured_at",
    "timing_label",
    "observation_schema_version",
    "trigger_source",
)
"""The irreducible set for ANY observation. Without one of these the row
cannot be placed in time, attributed to a run, or read under the right
schema.

`kickoff_utc` is deliberately NOT required: a market can be observed
before its game's kickoff is known, and demanding it would make honest
early rows look defective."""

REQUIRED_WHEN_PRICED = ("model_version",)
"""Required only of a PRICED observation. An unpriced contract carries no
model version by design -- ladder_pricing.py sets it only when
pricing_status is "model_priced" -- so demanding it universally would
report 883 correct rows as incomplete. Absence is a defect only where a
value was owed."""


@dataclass(frozen=True)
class CheckpointManifest:
    """Identifiers and versions for one observation at one instant."""

    game_id: str
    captured_at: str
    timing_label: str

    kickoff_utc: str | None = None
    code_sha: str | None = None
    model_version: str | None = None
    model_training_cutoff: str | None = None
    projection_snapshot_id: str | None = None

    market_tickers: tuple[str, ...] = ()
    executable_yes_price: float | None = None
    executable_no_price: float | None = None
    market_status: str | None = None

    fee_schedule_version: str | None = None
    semantics_version: str | None = None
    mapping_version: str | None = None
    context_capture_version: str | None = None
    trigger_source: str | None = None
    observation_schema_version: str | None = None
    capture_mode: str | None = None
    pricing_status: str | None = None
    """Whether the contract was model-priced. Decides which fields are
    OWED -- an unpriced row is not an incomplete priced one."""

    manifest_version: str = CHECKPOINT_MANIFEST_VERSION

    @property
    def is_priced(self) -> bool:
        """Read from `pricing_status`, not inferred from the presence of
        a snapshot id -- every captured row carries a SCAN snapshot id
        whether or not the football model ever ran on it, so inferring
        from it reported all 1,998 rows as priced."""
        return self.pricing_status == "model_priced"

    @property
    def missing_fields(self) -> tuple[str, ...]:
        required = list(REQUIRED_FIELDS)
        if self.is_priced:
            required.extend(REQUIRED_WHEN_PRICED)
        absent = []
        for name in required:
            value = getattr(self, name, None)
            if value in (None, "", ()):
                absent.append(name)
        return tuple(absent)

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields

    def content_hash(self) -> str:
        """Stable identity for this manifest. Lets a later report cite a
        checkpoint without re-embedding it."""
        payload = {
            k: (sorted(v) if isinstance(v, tuple) else v)
            for k, v in sorted(self.to_payload().items())
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    def to_payload(self) -> dict:
        return {
            "manifest_version": self.manifest_version,
            "game_id": self.game_id,
            "captured_at": self.captured_at,
            "timing_label": self.timing_label,
            "kickoff_utc": self.kickoff_utc,
            "code_sha": self.code_sha,
            "model_version": self.model_version,
            "model_training_cutoff": self.model_training_cutoff,
            "projection_snapshot_id": self.projection_snapshot_id,
            "market_tickers": list(self.market_tickers),
            "executable_yes_price": self.executable_yes_price,
            "executable_no_price": self.executable_no_price,
            "market_status": self.market_status,
            "fee_schedule_version": self.fee_schedule_version,
            "semantics_version": self.semantics_version,
            "mapping_version": self.mapping_version,
            "context_capture_version": self.context_capture_version,
            "trigger_source": self.trigger_source,
            "observation_schema_version": self.observation_schema_version,
            "capture_mode": self.capture_mode,
            "pricing_status": self.pricing_status,
        }


def manifest_from_corpus_row(row: dict, *, code_sha: str | None = None) -> CheckpointManifest:
    """Build a manifest from a persisted corpus row.

    Reads only what the row actually carries. A field the row does not
    have becomes None and shows up in `missing_fields`, rather than being
    back-filled from today's values -- which would silently claim the row
    was captured under a version it never saw."""
    obs = row.get("observation") or {}
    timing = obs.get("snapshot_timing") or {}
    model = obs.get("model_version") or {}
    versions = row.get("data_versions") or {}

    ticker = obs.get("kalshi_market_ticker")
    return CheckpointManifest(
        game_id=str(obs.get("game_id") or ""),
        captured_at=str(obs.get("captured_at") or ""),
        timing_label=str(timing.get("label") or ""),
        kickoff_utc=row.get("kickoff_utc_at_capture"),
        code_sha=code_sha,
        model_version=model.get("model_version") if isinstance(model, dict) else None,
        model_training_cutoff=obs.get("training_cutoff"),
        projection_snapshot_id=obs.get("snapshot_id"),
        # NOTE: this is the SCAN snapshot id the observation was captured
        # under. It is present on every row, priced or not, so it must
        # never be used to decide whether the model ran.
        market_tickers=(ticker,) if ticker else (),
        executable_yes_price=obs.get("executable_yes_price"),
        executable_no_price=obs.get("executable_no_price"),
        market_status=obs.get("market_status"),
        fee_schedule_version=obs.get("fee_schedule_version"),
        semantics_version=obs.get("parse_status"),
        mapping_version=versions.get("mapping_version") if isinstance(versions, dict) else None,
        context_capture_version=versions.get("context_capture_version")
        if isinstance(versions, dict)
        else None,
        trigger_source=row.get("run_id"),
        observation_schema_version=row.get("schema_version"),
        capture_mode=row.get("capture_mode"),
        pricing_status=obs.get("pricing_status"),
    )


@dataclass
class ManifestCompletenessReport:
    manifests: list[CheckpointManifest] = field(default_factory=list)

    @property
    def complete_count(self) -> int:
        return sum(1 for m in self.manifests if m.is_complete)

    @property
    def incomplete_count(self) -> int:
        return len(self.manifests) - self.complete_count

    def missing_field_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.manifests:
            for name in m.missing_fields:
                counts[name] = counts.get(name, 0) + 1
        return dict(sorted(counts.items()))

    def to_payload(self) -> dict:
        return {
            "manifest_version": CHECKPOINT_MANIFEST_VERSION,
            "total": len(self.manifests),
            "complete": self.complete_count,
            "incomplete": self.incomplete_count,
            "missing_field_counts": self.missing_field_counts(),
        }
