"""The V2 SHADOW ledger: a second opinion, recorded beside the canonical
row and never in place of it.

*** SHADOW MEANS SHADOW (mission section 12) ***
0.5.0 stays canonical. For every due, supported, FBS-vs-FBS contract the
collector captures its canonical 0.5.0 observation exactly as before, and
THEN -- only if that succeeded, and only if nothing goes wrong -- appends
one linked V2 row here. V2 never replaces a canonical probability, never
touches timing eligibility, market mapping, settlement or staking, and
can never block a capture: every failure is caught, counted and recorded
as a reason string, and the canonical row is already written by then.

*** DISTINCT FROM THE TALENT SHADOW ***
`research/preseason/shadow_sidecar.py` records a different experiment (an
early-season talent prior against the 0.4.0 control) in
`data/research/shadow/`. Overloading it would make two unrelated
experiments share a schema and a file, and the first schema change would
corrupt the other's evidence. This ledger is its own file, its own
schema, its own version field:

    data/research/v2_shadow/{season}.jsonl

*** APPEND-ONLY, DEDUPED ON THE CANONICAL KEY ***
Each row carries the canonical `observation_key` it shadows, so the two
ledgers join exactly and a V2 row can never be mistaken for an
independent observation. Dedup is on
`observation_key | v2_model_version`, so a push retry writes zero
duplicates and a future V2 version can coexist beside this one rather
than overwriting its evidence.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

V2_SHADOW_SCHEMA_VERSION = "v2_shadow_observation_v1"
V2_SHADOW_SUBDIR = "v2_shadow"

MAX_V2_SHADOW_ROWS = 400_000
"""Bounded like the heartbeat ledger so a git-backed store cannot grow
without limit. At one row per captured contract this is many seasons."""


@dataclass(frozen=True)
class V2ShadowRow:
    """One V2 opinion about one canonical observation."""

    schema_version: str
    observation_key: str
    """The CANONICAL 0.5.0 row this shadows -- the join key, and the
    reason a shadow row can never be read as a standalone observation."""
    season: int
    game_id: str
    kalshi_market_ticker: str
    timing_label: str
    captured_at: str
    kickoff_utc: str | None

    v2_model_version: str
    v2_artifact_sha256: str
    v2_spec_id: str
    v2_training_cutoff: str

    market_family: str | None = None
    threshold: float | None = None
    """The contract's own line. Half-point strikes are priced verbatim;
    only integer thresholds receive the continuity correction (see
    modeling/v2/pricing.py)."""
    threshold_is_half_point: bool | None = None

    v2_pred_margin: float | None = None
    v2_pred_total: float | None = None
    v2_sd_margin: float | None = None
    v2_sd_total: float | None = None
    v2_p_home: float | None = None
    v2_probability: float | None = None
    """V2's probability for THIS contract as written (YES side)."""

    control_model_version: str | None = None
    control_probability: float | None = None
    v2_minus_control: float | None = None
    executable_yes_price: float | None = None
    market_implied_probability: float | None = None
    v2_minus_market: float | None = None

    run_id: str | None = None
    unavailable_reason: str | None = None
    """Set when V2 could not price this contract. The row is still
    written: 'V2 had no opinion' is evidence, and a silent gap is not."""

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


@dataclass
class V2ShadowTelemetry:
    rows_written: int = 0
    rows_duplicate: int = 0
    contracts_priced: int = 0
    unavailable: int = 0
    unavailable_reasons: dict[str, int] = field(default_factory=dict)

    def note_unavailable(self, reason: str) -> None:
        self.unavailable += 1
        key = reason.split(":")[0][:60]
        self.unavailable_reasons[key] = self.unavailable_reasons.get(key, 0) + 1


def ledger_path(repo_dir: Path, season: int) -> Path:
    return repo_dir / "data" / "research" / V2_SHADOW_SUBDIR / f"{season}.jsonl"


def dedup_key(observation_key: str, model_version: str) -> str:
    return f"{observation_key}|{model_version}"


def load_existing_keys(path: Path) -> set[str]:
    """Dedup keys already on disk. A malformed line is skipped rather
    than raised on: a corrupt tail must not stop today's capture."""
    keys: set[str] = set()
    if not path.exists():
        return keys
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = row.get("observation_key")
                version = row.get("v2_model_version")
                if isinstance(key, str) and isinstance(version, str):
                    keys.add(dedup_key(key, version))
    except OSError:
        return keys
    return keys


def append_rows(path: Path, rows: list[V2ShadowRow]) -> int:
    """One appending, fsync'd batch -- the same durability discipline the
    observation ledger uses. Returns the number of rows written."""
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    import os

    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(row.to_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return len(rows)


ARTIFACT_ID_SOURCE = "cfbd"
"""The vendor whose game id keys the frozen artifact. `V2Artifact.games[*].game_id`
is the CFBD id the V2 dataset was built from (see
scripts/build_v2_shadow_artifact.py), while the canonical `GameRecord.game_id`
is the season/week/slug id. The two must never be confused for one another."""


def resolve_artifact_game_id(artifact, canonical_game_id: str | None, matched_game) -> tuple[str | None, list[str]]:
    """Find the key under which the frozen artifact knows this game.

    Tries, in order: the matched GameRecord's CFBD source id, then the
    canonical id itself (for an artifact keyed that way). Returns the first
    id the artifact positively knows and the full list that was tried, so
    an unavailable row can say exactly what was looked up. Never raises."""
    tried: list[str] = []
    try:
        source_ids = getattr(matched_game, "source_game_ids", None) or {}
        cfbd_id = source_ids.get(ARTIFACT_ID_SOURCE)
        if cfbd_id:
            tried.append(str(cfbd_id))
    except Exception:  # noqa: BLE001 -- a malformed record must not stop the shadow
        pass
    if canonical_game_id and canonical_game_id not in tried:
        tried.append(str(canonical_game_id))
    for candidate in tried:
        try:
            if artifact.for_game(candidate) is not None:
                return candidate, tried
        except Exception:  # noqa: BLE001 -- an exploding artifact is reported by the caller
            raise
    return None, tried


def is_half_point(threshold: float | None) -> bool | None:
    if threshold is None:
        return None
    return abs(threshold - round(threshold)) > 1e-9


def build_row(
    *,
    artifact,
    observation_key: str,
    season: int,
    game_id: str,
    market_ticker: str,
    timing_label: str,
    captured_at: datetime,
    kickoff_utc: datetime | None,
    market_family: str | None,
    threshold: float | None,
    side_is_over_or_home: bool,
    control_model_version: str | None,
    control_probability: float | None,
    executable_yes_price: float | None,
    run_id: str | None,
    artifact_game_id: str | None = None,
) -> V2ShadowRow:
    """One shadow row. Never raises -- an unavailable V2 opinion is
    recorded as a reason, because a silent gap in the shadow ledger would
    be indistinguishable from a contract V2 simply agreed about.

    `game_id` is the CANONICAL game id and is what the row records.
    `artifact_game_id` is the key the frozen artifact is indexed by (the
    CFBD game id the V2 dataset was built from). They are different
    namespaces: the first live season wrote 422 rows, every one of them
    "not in the frozen V2 slate", because the canonical slug was used as
    the artifact key. When omitted the canonical id is tried, which is
    only right for an artifact that happens to be keyed that way.
    """
    base = dict(
        schema_version=V2_SHADOW_SCHEMA_VERSION,
        observation_key=observation_key,
        season=season,
        game_id=game_id,
        kalshi_market_ticker=market_ticker,
        timing_label=timing_label,
        captured_at=captured_at.isoformat(),
        kickoff_utc=kickoff_utc.isoformat() if kickoff_utc else None,
        v2_model_version=artifact.model_version,
        v2_artifact_sha256=artifact.artifact_sha256,
        v2_spec_id=artifact.spec_id,
        v2_training_cutoff=artifact.training_cutoff,
        market_family=market_family,
        threshold=threshold,
        threshold_is_half_point=is_half_point(threshold),
        control_model_version=control_model_version,
        control_probability=control_probability,
        executable_yes_price=executable_yes_price,
        run_id=run_id,
    )

    lookup_id = artifact_game_id if artifact_game_id is not None else game_id
    pred = artifact.for_game(lookup_id)
    if pred is None:
        return V2ShadowRow(
            **base,
            unavailable_reason=f"game {game_id} not in the frozen V2 slate (artifact key tried: {lookup_id!r})",
        )
    if market_family not in ("spread", "total"):
        return V2ShadowRow(
            **base,
            v2_pred_margin=pred.pred_margin,
            v2_pred_total=pred.pred_total,
            v2_sd_margin=pred.sd_margin,
            v2_sd_total=pred.sd_total,
            v2_p_home=pred.p_home,
            unavailable_reason=f"contract family {market_family!r} is not priced by the frozen V2 spec",
        )
    if threshold is None:
        return V2ShadowRow(
            **base,
            v2_pred_margin=pred.pred_margin,
            v2_pred_total=pred.pred_total,
            v2_sd_margin=pred.sd_margin,
            v2_sd_total=pred.sd_total,
            v2_p_home=pred.p_home,
            unavailable_reason="contract threshold unavailable",
        )

    probability = artifact.price_contract(
        lookup_id, family=market_family, threshold=threshold, side_is_over_or_home=side_is_over_or_home
    )
    market_p = None if executable_yes_price is None else float(executable_yes_price)
    return V2ShadowRow(
        **base,
        v2_pred_margin=pred.pred_margin,
        v2_pred_total=pred.pred_total,
        v2_sd_margin=pred.sd_margin,
        v2_sd_total=pred.sd_total,
        v2_p_home=pred.p_home,
        v2_probability=probability,
        v2_minus_control=(
            None if (probability is None or control_probability is None) else probability - control_probability
        ),
        market_implied_probability=market_p,
        v2_minus_market=(None if (probability is None or market_p is None) else probability - market_p),
    )
