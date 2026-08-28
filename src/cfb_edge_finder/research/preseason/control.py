"""The FROZEN CONTROL model configuration.

*** WHY THIS EXISTS ***

Every preseason-prior experiment must be compared against ONE fixed
model. The classic way research goes wrong is that the control quietly
improves while candidates are being tested -- someone retunes a lambda,
someone changes a default -- and the comparison silently stops meaning
anything. This module reads the production constants at import time and
hashes them, so a control that has drifted is detectable rather than
assumed away.

*** IT READS, IT DOES NOT REDEFINE ***

Every value below is imported from the production module that owns it.
Restating a number here would create a second source of truth that could
disagree with the model actually being run, which is the exact failure
this module exists to prevent. `control_manifest()` therefore describes
whatever production currently is; `CONTROL_BASELINE_SHA256` records what
it was when the research began, and `control_has_drifted()` compares.

*** THIS MODULE CANNOT CHANGE THE MODEL ***

It contains no assignment to any production parameter and no call into a
fitting routine. It is a read-only description, verified by a test that
parses its imports.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from cfb_edge_finder.modeling.margin_correction_artifact import (
    FROZEN_MARGIN_CORRECTION_PARAMS,
    MARGIN_CORRECTION_ARTIFACT_VERSION,
    MARGIN_CORRECTION_METHOD,
    MARGIN_CORRECTION_TRAINING_CUTOFF,
    MARGIN_CORRECTION_TRAINING_N,
    MARGIN_CORRECTION_TRAINING_SEASONS,
)
from cfb_edge_finder.modeling.priors import DEFAULT_SEASON_SHRINKAGE_K, season_carryover_weight
from cfb_edge_finder.modeling.qb_continuity import (
    HIGH_CONTINUITY_THRESHOLD,
    LOW_CONTINUITY_THRESHOLD,
    QBContinuityState,
    uncertainty_multiplier,
)
from cfb_edge_finder.modeling.ratings import (
    DEFAULT_FCS_RIDGE_LAMBDA,
    DEFAULT_PACE_SHRINKAGE_K,
    DEFAULT_RIDGE_LAMBDA,
    FCS_DEFAULT_TIER,
    FCS_PSEUDO_TEAM_ID,
    FCS_TIER_MIN_GAMES,
    FCS_TIER_STRONG_THRESHOLD,
    FCS_TIER_WEAK_THRESHOLD,
    FCS_TIERS,
)
from cfb_edge_finder.modeling.score_model import (
    DEFAULT_MIN_RESIDUAL_POOL_SIZE,
    DEFAULT_N_SIMULATIONS,
    DEFAULT_RESIDUAL_SCALE,
    EARLY_SEASON_UNCERTAINTY_SCALE,
    FALLBACK_RESIDUAL_SD,
    FCS_OPPONENT_UNCERTAINTY_SCALE,
)

CONTROL_MODEL_VERSION = "0.4.0-milestone-c2-live-margin-correction"
"""The model version stamped on live 2026 observations. Recorded so a
research result can be tied to the exact production model it was measured
against; it is not used to construct anything."""

CONTROL_CONFIG_VERSION = "preseason_control_freeze_v1"

CONTROL_BASELINE_SHA256 = "3741c6f522972fa2de46493b47a80de756aabc0a038d12c33f6f3e204f66bd83"
"""The control hash at the moment this research began, 2026-08-28,
against production main 33df3f0.

`tests/test_preseason_control_freeze.py` asserts the CURRENT manifest
against this value, so a production parameter changed mid-research fails
the suite instead of silently invalidating every comparison. The test is
the mechanism; a constant nobody checked would prove nothing.

If production legitimately changes, this constant moves in the SAME
commit and every prior result is re-labelled as measured against the old
control -- never silently carried forward."""


@dataclass(frozen=True)
class ControlManifest:
    """A complete, reproducible description of the control model."""

    config_version: str
    model_version: str
    payload: dict

    def content_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    def to_dict(self) -> dict:
        return {
            "config_version": self.config_version,
            "model_version": self.model_version,
            "content_sha256": self.content_hash(),
            **self.payload,
        }


def early_season_carryover_curve() -> dict[str, float]:
    """The carryover weight at each early-season game count.

    Included in the manifest because it is the single most important
    control behaviour for this research: at zero games played the weight
    is exactly 0, so a Week 1 point estimate is entirely prior-season.
    Computed by calling the production function, not restated."""
    return {str(g): season_carryover_weight(g) for g in range(0, 9)}


def control_manifest() -> ControlManifest:
    """Describe the control model as production currently defines it."""
    payload = {
        "ratings": {
            "ridge_lambda": DEFAULT_RIDGE_LAMBDA,
            "fcs_ridge_lambda": DEFAULT_FCS_RIDGE_LAMBDA,
            "pace_shrinkage_k": DEFAULT_PACE_SHRINKAGE_K,
            "pace_mode_default": "matchup",
        },
        "priors": {
            "season_shrinkage_k": DEFAULT_SEASON_SHRINKAGE_K,
            "carryover_weight_by_games_played": early_season_carryover_curve(),
            "week1_carryover_weight": season_carryover_weight(0),
        },
        "simulation": {
            "n_simulations": DEFAULT_N_SIMULATIONS,
            "residual_scale": DEFAULT_RESIDUAL_SCALE,
            "fallback_residual_sd": FALLBACK_RESIDUAL_SD,
            "min_residual_pool_size": DEFAULT_MIN_RESIDUAL_POOL_SIZE,
        },
        "uncertainty": {
            "early_season_uncertainty_scale": EARLY_SEASON_UNCERTAINTY_SCALE,
            "fcs_opponent_uncertainty_scale": FCS_OPPONENT_UNCERTAINTY_SCALE,
            "qb_uncertainty_multipliers": {
                state.value: uncertainty_multiplier(state) for state in QBContinuityState
            },
        },
        "qb_continuity_proxy": {
            "high_continuity_threshold": HIGH_CONTINUITY_THRESHOLD,
            "low_continuity_threshold": LOW_CONTINUITY_THRESHOLD,
            "affects_point_estimate": False,
            "affects_uncertainty_only": True,
        },
        "margin_correction": {
            "artifact_version": MARGIN_CORRECTION_ARTIFACT_VERSION,
            "method": MARGIN_CORRECTION_METHOD,
            "training_seasons": list(MARGIN_CORRECTION_TRAINING_SEASONS),
            "training_cutoff": str(MARGIN_CORRECTION_TRAINING_CUTOFF),
            "training_n": MARGIN_CORRECTION_TRAINING_N,
            "a": FROZEN_MARGIN_CORRECTION_PARAMS.a,
            "b": FROZEN_MARGIN_CORRECTION_PARAMS.b,
        },
        "fcs_treatment": {
            "pseudo_team_id": FCS_PSEUDO_TEAM_ID,
            "tiers": list(FCS_TIERS),
            "default_tier": FCS_DEFAULT_TIER,
            "tier_min_games": FCS_TIER_MIN_GAMES,
            "tier_weak_threshold": FCS_TIER_WEAK_THRESHOLD,
            "tier_strong_threshold": FCS_TIER_STRONG_THRESHOLD,
            "priced_for_research": False,
        },
        "preseason_information_used_in_point_estimate": [],
        "preseason_information_used_in_uncertainty_only": ["qb_continuity_proxy"],
    }
    return ControlManifest(
        config_version=CONTROL_CONFIG_VERSION,
        model_version=CONTROL_MODEL_VERSION,
        payload=payload,
    )


def control_has_drifted(expected_sha256: str) -> bool:
    """True when production no longer matches the recorded control.

    Called by a test rather than by experiment code: an experiment that
    checked this itself could be tempted to carry on anyway."""
    return control_manifest().content_hash() != expected_sha256
