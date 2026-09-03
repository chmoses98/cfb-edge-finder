"""The frozen V2 shadow artifact: load it, verify it, price with it.

*** WHY A FROZEN ARTIFACT AND NOT A MODEL THAT RUNS ***
V2 must not retrain every five minutes, and -- more importantly -- it
must not retrain AT ALL during the validation slate. Games begin today;
if team state were refreshed after Thursday's results and then compared
against Saturday, the "prospective test" would silently become a
partly-hindsight one, and the whole slate's evidence would be worthless.

So production inference is a LOOKUP. `scripts/build_v2_shadow_artifact.py`
fits the frozen research model once, from durable research-data with zero
metered CFBD calls, and writes every per-game prediction into this
artifact. There is no code path in the capture loop that can re-derive a
different number after a game completes, because there is no fitting code
in the capture loop at all.

*** WHAT IS VERIFIED BEFORE ANYTHING IS PRICED ***
  schema_version   must match exactly -- a future schema is refused, not
                   best-effort parsed
  artifact_sha256  recomputed over the canonical body and compared
  model_version    read FROM the artifact and stamped onto every row, so
                   a row can never claim a version its numbers did not
                   come from
  reproduction     the artifact must carry a passing reproduction record;
                   an artifact that never proved it reproduces research
                   is refused

Every failure raises `V2ArtifactError`. The collector catches it, records
the reason, and captures the canonical 0.5.0 row anyway -- V2 failing is
never allowed to cost a canonical observation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cfb_edge_finder.modeling.v2.pricing import contract_probability

ARTIFACT_SCHEMA_VERSION = "v2_shadow_artifact_v1"
ARTIFACT_SUBDIR = "v2_shadow"
"""Under data/research/ so git_durable_store's staging allowlist covers
it unchanged, like every other durable artifact."""


class V2ArtifactError(RuntimeError):
    """The artifact is missing, malformed, unverified, or of a schema this
    build does not understand. Always fail closed: no shadow row is worth
    a wrong one."""


@dataclass(frozen=True)
class GamePrediction:
    game_id: str
    season: int
    week: int
    home_team: str
    away_team: str
    pred_margin: float
    pred_total: float
    sd_margin: float
    sd_total: float
    p_home: float


@dataclass(frozen=True)
class V2Artifact:
    model_version: str
    artifact_sha256: str
    spec_id: str
    spec_sha256: str
    training_cutoff: str
    prediction_season: int
    built_at: str
    dataset_built_at: str | None
    dataset_cache_fetched_at: str | None
    reproduction: dict
    predictions: dict[str, GamePrediction]

    def for_game(self, game_id: str) -> GamePrediction | None:
        return self.predictions.get(str(game_id))

    def summary_dict(self) -> dict:
        return {
            "v2_model_version": self.model_version,
            "v2_artifact_sha256": self.artifact_sha256,
            "v2_spec_id": self.spec_id,
            "v2_training_cutoff": self.training_cutoff,
            "v2_games": len(self.predictions),
            "v2_built_at": self.built_at,
        }

    # ------------------------------------------------------------ pricing

    def price_contract(
        self, game_id: str, *, family: str, threshold: float, side_is_over_or_home: bool = True
    ) -> float | None:
        """P(this contract settles YES) under the frozen V2 distribution.

        `family` is 'spread' (threshold = home margin line) or 'total'.
        Returns None when the game is not in the frozen slate -- an
        unknown game is never priced from a neighbouring one."""
        pred = self.for_game(game_id)
        if pred is None:
            return None
        if family == "spread":
            point, sd = pred.pred_margin, pred.sd_margin
        elif family == "total":
            point, sd = pred.pred_total, pred.sd_total
        else:
            raise ValueError(f"unsupported contract family {family!r}")
        p_over = contract_probability(point, sd, threshold)
        return p_over if side_is_over_or_home else 1.0 - p_over


def _canonical_sha256(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k not in ("artifact_sha256", "built_at")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def artifact_path(repo_dir: Path, season: int) -> Path:
    return repo_dir / "data" / "research" / ARTIFACT_SUBDIR / f"{season}.artifact.json"


def load_artifact(path: Path, *, season: int | None = None) -> V2Artifact:
    """Load and FULLY verify. Raises V2ArtifactError on anything it does
    not positively recognise -- never returns a partially-trusted model."""
    if not path.exists():
        raise V2ArtifactError(f"no V2 artifact at {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise V2ArtifactError(f"unreadable V2 artifact: {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict):
        raise V2ArtifactError("V2 artifact is not a JSON object")

    schema = payload.get("schema_version")
    if schema != ARTIFACT_SCHEMA_VERSION:
        raise V2ArtifactError(
            f"V2 artifact schema {schema!r} != expected {ARTIFACT_SCHEMA_VERSION!r} -- refusing to guess"
        )

    declared = payload.get("artifact_sha256")
    recomputed = _canonical_sha256(payload)
    if declared != recomputed:
        raise V2ArtifactError(f"V2 artifact sha256 mismatch: declared {declared!r}, recomputed {recomputed!r}")

    reproduction = payload.get("reproduction") or {}
    if not reproduction.get("passed"):
        raise V2ArtifactError(
            "V2 artifact does not carry a PASSING reproduction record -- a model that has not been "
            "shown to reproduce its research is never shadowed"
        )

    model_version = payload.get("model_version")
    if not isinstance(model_version, str) or not model_version:
        raise V2ArtifactError("V2 artifact carries no model_version")

    prediction_season = payload.get("prediction_season")
    if season is not None and prediction_season != season:
        raise V2ArtifactError(f"V2 artifact is for season {prediction_season}, not {season}")

    predictions: dict[str, GamePrediction] = {}
    for raw in payload.get("games") or []:
        try:
            predictions[str(raw["game_id"])] = GamePrediction(
                game_id=str(raw["game_id"]),
                season=int(raw["season"]),
                week=int(raw["week"]),
                home_team=str(raw["home_team"]),
                away_team=str(raw["away_team"]),
                pred_margin=float(raw["pred_margin"]),
                pred_total=float(raw["pred_total"]),
                sd_margin=float(raw["sd_margin"]),
                sd_total=float(raw["sd_total"]),
                p_home=float(raw["p_home"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise V2ArtifactError(f"malformed game row in V2 artifact: {type(exc).__name__}: {exc}") from exc
    if not predictions:
        raise V2ArtifactError("V2 artifact carries no game predictions")

    for pred in predictions.values():
        if not (pred.sd_margin > 0 and pred.sd_total > 0):
            raise V2ArtifactError(f"non-positive uncertainty for {pred.game_id}")

    return V2Artifact(
        model_version=model_version,
        artifact_sha256=recomputed,
        spec_id=str(payload.get("spec_id", "")),
        spec_sha256=str(payload.get("spec_sha256", "")),
        training_cutoff=str(payload.get("training_cutoff", "")),
        prediction_season=int(prediction_season) if prediction_season is not None else -1,
        built_at=str(payload.get("built_at", "")),
        dataset_built_at=(payload.get("dataset") or {}).get("built_at"),
        dataset_cache_fetched_at=(payload.get("dataset") or {}).get("cache_fetched_at"),
        reproduction=reproduction,
        predictions=predictions,
    )


def assert_no_outcomes_after(artifact: V2Artifact, cutoff: datetime) -> None:
    """Guard for mission section 20: the artifact's evidence must predate
    the slate it is being judged on.

    The dataset's own build timestamp is the load-bearing fact -- the
    features were computed from that snapshot and nothing since -- so a
    build stamped after the first kickoff is refused rather than
    explained away."""
    stamp = artifact.dataset_built_at or artifact.built_at
    if not stamp:
        raise V2ArtifactError("V2 artifact carries no build timestamp to check against the slate")
    try:
        built = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V2ArtifactError(f"unparsable V2 artifact timestamp {stamp!r}") from exc
    if built.tzinfo is None:
        raise V2ArtifactError(f"V2 artifact timestamp {stamp!r} is not timezone-aware")
    if built > cutoff:
        raise V2ArtifactError(
            f"V2 artifact evidence ({built.isoformat()}) is NEWER than the slate cutoff "
            f"({cutoff.isoformat()}) -- refusing to shadow a model that may have seen slate outcomes"
        )
