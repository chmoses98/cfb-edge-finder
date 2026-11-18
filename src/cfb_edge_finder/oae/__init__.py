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


def unavailable_block(reason: str) -> dict:
    """Explicit stub published when the challenger block cannot be built (production fields untouched)."""
    return {"model_version": MODEL_VERSION, **AUTHORITY, "flag": FLAG, "available": False,
            "unavailable_reason": str(reason)[:500], "games": [], "skipped": []}
