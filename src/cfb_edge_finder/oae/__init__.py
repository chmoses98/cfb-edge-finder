"""Opponent-adjusted efficiency (OAE) game and period forecasts: additive, flag-gated, NOT activated.

Frozen extraction of the validated pieces of research PR #307 (opponent-adjusted ratings -> full-game margin,
winner, spread) and PR #308 (period models for the families rated RELIABLE on the 2025-26 holdout). Nothing in the
pricing, trading or execution paths imports this package. The only caller is the projection-data publish stage,
and only when ``CFB_OAE_FORECASTS=1``; with the flag unset the published artifact is byte-identical to main.

Full-game totals and team totals are deliberately NOT emitted (PARTIAL in validation: total probabilities biased
toward the over by about +0.06).
"""
from __future__ import annotations

import os
from collections.abc import Mapping

FLAG = "CFB_OAE_FORECASTS"
ARTIFACT_KEY = "oae_challenger"
MODEL_VERSION = "cfb-oae-1.0.0"


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only for the explicit value ``"1"``; anything else (unset, empty, "0", "true") is off."""
    return (os.environ if env is None else env).get(FLAG, "").strip() == "1"


# Authority labels (same convention as production model.authority = research_only); never bet authority.
AUTHORITY = {"status": "not_activated", "activated": False, "authority": "research_only", "role": "shadow",
             "bet_authority": False, "operating_forecast": False,
             "operating_forecast_ref": "top-level `model` / `games` of this artifact (unchanged by this block)"}


# Promotion key (producer side). Only the exact value "1", and only with CFB_OAE_FORECASTS=1, makes the block
# declare itself the operating CFB game forecast. SIFT's feed additionally requires its own reviewed manifest
# (sift-alpha sift_feed/operating_models.json) to name MODEL_VERSION before it serves it; either key off = no operating
# CFB forecast (the incumbent state: production publishes none). Bet authority stays false either way; the artifact's
# `model` / `games` never change.
OPERATING_FLAG = "CFB_OAE_OPERATING"
OPERATING_AUTHORITY = {"status": "activated", "activated": True, "authority": "operating_forecast", "role": "operating",
                       "bet_authority": False, "operating_forecast": True,
                       "operating_forecast_ref": ("oae_challenger.games (owner-promoted with CFB_OAE_OPERATING=1); "
                                                  "the top-level `model` / `games` of this artifact are unchanged")}


def operating(env: Mapping[str, str] | None = None) -> bool:
    """True only for the explicit value ``"1"``."""
    return (os.environ if env is None else env).get(OPERATING_FLAG, "").strip() == "1"


def authority(env: Mapping[str, str] | None = None) -> dict:
    return dict(OPERATING_AUTHORITY if operating(env) else AUTHORITY)


def unavailable_block(reason: str) -> dict:
    """Explicit stub published when the challenger block cannot be built (production fields untouched)."""
    return {"model_version": MODEL_VERSION, **authority(), "flag": FLAG, "available": False,
            "unavailable_reason": str(reason)[:500], "games": [], "skipped": []}
