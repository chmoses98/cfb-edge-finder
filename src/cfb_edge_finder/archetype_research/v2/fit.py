"""Development fit and parameter freezing. RESEARCH ONLY.

The ONLY place a V2 number is learned. It accepts development seasons
(2021-2025) and nothing else: a holdout or prospective season raises
`SealedSeasonError`, so neither the sealed holdout nor the 2026 ledger can
tune V2. The fitted parameters are written once, hashed, and committed with
the protocol; `load_frozen` refuses a file whose content no longer matches
its hash.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cfb_edge_finder.archetype_research.v2 import (
    DEVELOPMENT_SEASONS,
    HOLDOUT_SEASONS,
    PROSPECTIVE_SEASONS,
    V2_VERSION,
)
from cfb_edge_finder.archetype_research.v2 import criteria as K
from cfb_edge_finder.archetype_research.v2.claims import claims_hash, v2_claims
from cfb_edge_finder.archetype_research.v2.evaluate import (
    control_tier,
    eligible,
    margin_distribution,
    side_margin,
)


class SealedSeasonError(RuntimeError):
    """A holdout or prospective season reached the development fit."""


class FrozenParametersMismatch(RuntimeError):
    """The frozen parameter file was altered after it was hashed."""


def attach_claims(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add the V2 claims (pregame-only) to study records, in place, and return them."""
    for r in records:
        r["v2"] = v2_claims(r["pregame"])
        r["v2_hash"] = claims_hash(r["v2"])
    return records


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def parameters_hash(params: dict[str, Any]) -> str:
    body = {k: v for k, v in params.items() if k != "sha256"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def fit_development(records: list[dict[str, Any]], sources: dict[str, Any] | None = None) -> dict[str, Any]:
    seasons = {int(r["pregame"]["season"]) for r in records}
    forbidden = seasons - set(DEVELOPMENT_SEASONS)
    if forbidden:
        kind = "prospective" if forbidden & set(PROSPECTIVE_SEASONS) else "sealed/holdout or unknown"
        raise SealedSeasonError(f"development fit refused {kind} seasons {sorted(forbidden)}")
    games = eligible(records)
    control: dict[str, Any] = {}
    for tier in K.CONTROL_TIERS:
        side = "home" if tier.startswith("HOME") else "away"
        ms = [side_margin(r, side) for r in games if control_tier(r) == tier]
        dist = margin_distribution(ms)
        intervals = {name: [dist[f"p{lo}"], dist[f"p{hi}"]] for name, (lo, hi) in K.INTERVALS.items()}
        in_sample = {
            name: round(sum(1 for m in ms if iv[0] <= m <= iv[1]) / len(ms), 4) for name, iv in intervals.items()
        }
        control[tier] = {"distribution": dist, "intervals": intervals, "development_in_sample_coverage": in_sample}
    params = {
        "schema": "cfb_archetype_v2_frozen_parameters/1.0.0",
        "v2_version": V2_VERSION,
        "label": "HISTORICAL EMPIRICAL DISTRIBUTIONS fitted on DEVELOPMENT seasons only; not probabilities",
        "development_seasons": sorted(seasons),
        "holdout_seasons": list(HOLDOUT_SEASONS),
        "development_games": len(games),
        "criteria": K.describe(),
        "control": control,
        "sources": sources or {},
    }
    params["sha256"] = parameters_hash(params)
    return params


def write_frozen(params: dict[str, Any], path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(params, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return params["sha256"]


def load_frozen(path: Path) -> dict[str, Any]:
    params = json.loads(Path(path).read_text(encoding="utf-8"))
    if parameters_hash(params) != params.get("sha256"):
        raise FrozenParametersMismatch(str(path))
    return params
