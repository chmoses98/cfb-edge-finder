"""The CONTROL margin calibration artifact: promoted, frozen, verified. PURE + file I/O.

*** WHAT IT IS ***
For each CONTROL tier (side x strength of the sustained-efficiency finding),
the distribution of the CONTROL side's full-game margin over the historical
games that carried that football claim: n, win rate, median, mean, central
50% [p25, p75] and central 80% [p10, p90]. Fitted ONCE, on the development
seasons 2021-2025, by the Wave 2 research study, and frozen there under
SHA-256 683d075d... before any validation season was scored.

*** WHAT IT IS NOT ***
A prediction interval, a probability, a fair price or an expected value. It
is a HISTORICAL EMPIRICAL RANGE: what past games with the same football claim
did. The per-threshold rates of the research file (p_margin_ge_3, ...) are
deliberately NOT promoted -- they are the ingredients of a contract-level
probability, and production must not derive one from claim frequency.

*** PROMOTION, NOT REFITTING ***
`promote` copies values from the research file (verified against its hash)
and attaches the 2014-2020 validation results from the holdout report as a
separate block. Nothing is recomputed, and no 2014-2020 or 2026 game can
change a promoted number. The production artifact carries its own SHA-256,
pinned in `CONTROL_CALIBRATION_SHA256`; `load_calibration` refuses a file
whose content no longer matches it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CALIBRATION_SCHEMA = "cfb_control_margin_calibration/1.0.0"
CALIBRATION_VERSION = "cfb-control-margin/2021-2025/1.0.0"
CALIBRATION_PATH = Path("data/scripting/calibration/control_margin_v2.json")

#: The Wave 2 frozen research parameters this artifact was promoted from.
SOURCE_PARAMETERS_PATH = Path("data/scripting/validation/archetype_v2_frozen_parameters.json")
SOURCE_PARAMETERS_SHA256 = "683d075d99efdfed16f7fb35a9fd58234808d1a354f8fb8e3352c64e972bf7aa"
SOURCE_HOLDOUT_REPORT_PATH = Path("data/scripting/validation/archetype_v2_holdout_report.json")

#: The promoted artifact's own content hash. Changing a promoted value changes
#: it, and every V2 claims artifact cites it.
CONTROL_CALIBRATION_SHA256 = "626c649b91379bc7d8855f121b32c2d148d4e9b129ea674b612a9a0978e89730"

CONTROL_TIERS = (
    "HOME_CONTROL_MODERATE",
    "HOME_CONTROL_STRONG",
    "AWAY_CONTROL_MODERATE",
    "AWAY_CONTROL_STRONG",
)
DEVELOPMENT_SEASONS = (2021, 2022, 2023, 2024, 2025)
VALIDATION_SEASONS = (2014, 2015, 2016, 2017, 2018, 2019, 2020)

RANGE_LABEL = (
    "Historical empirical range: the full-game margins of past games (2021-2025) that carried the same "
    "football claim, from the control side's point of view."
)
RANGE_NOT = (
    "Not a prediction interval, a probability, a fair price, an expected value or a guarantee. The win rate is "
    "a historical conditional frequency, not this game's chance to win."
)


class CalibrationMismatch(RuntimeError):
    """A calibration file's content does not match its hash, or not the pinned hash."""


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def content_sha256(payload: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON without the `sha256` key (the research file's rule)."""
    body = {k: v for k, v in payload.items() if k != "sha256"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def promote(parameters: dict[str, Any], holdout_report: dict[str, Any]) -> dict[str, Any]:
    """The production artifact, copied from the frozen research parameters. No value is recomputed."""
    if content_sha256(parameters) != parameters.get("sha256") or parameters["sha256"] != SOURCE_PARAMETERS_SHA256:
        raise CalibrationMismatch("the research parameters do not match the frozen Wave 2 hash")
    if holdout_report.get("frozen_parameters_sha256") != SOURCE_PARAMETERS_SHA256:
        raise CalibrationMismatch("the holdout report was not scored against the frozen Wave 2 parameters")
    if tuple(parameters["development_seasons"]) != DEVELOPMENT_SEASONS:
        raise CalibrationMismatch(f"development seasons {parameters['development_seasons']} are not 2021-2025")
    tiers: dict[str, Any] = {}
    validation: dict[str, Any] = {}
    for tier in CONTROL_TIERS:
        src = parameters["control"][tier]
        dist = src["distribution"]
        tiers[tier] = {
            "n": dist["n"],
            "win_rate": dist["win_rate"],
            "median": dist["p50"],
            "mean": dist["mean"],
            "central_50": src["intervals"]["central_50"],
            "central_80": src["intervals"]["central_80"],
            "development_in_sample_coverage": src["development_in_sample_coverage"],
        }
        held = holdout_report["evaluation"]["control"]["tiers"][tier]
        validation[tier] = {
            "n": held["pooled"]["n"],
            "win_rate": held["pooled"]["win_rate"],
            "median": held["pooled"]["p50"],
            "coverage_50": held["interval_coverage"]["central_50"],
            "coverage_80": held["interval_coverage"]["central_80"],
            "within_tolerance": holdout_report["criteria"]["CONTROL_INTERVALS"]["tiers"][tier]["calibrated"],
        }
    artifact = {
        "schema": CALIBRATION_SCHEMA,
        "calibration_version": CALIBRATION_VERSION,
        "label": RANGE_LABEL,
        "not": RANGE_NOT,
        "variable": "control_side_margin: control-side points minus opponent points, full game",
        "intervals": {"central_50": "[p25, p75]", "central_80": "[p10, p90]"},
        "development_seasons": list(DEVELOPMENT_SEASONS),
        "development_games": parameters["development_games"],
        "tiers": tiers,
        "validation": {
            "seasons": list(VALIDATION_SEASONS),
            "role": (
                "Out-of-time validation, opened once in Wave 2 after these values were frozen. No longer sealed; "
                "it validated these exact values and was not used to change them."
            ),
            "tolerance": parameters["criteria"]["coverage_tolerance"],
            "tiers": validation,
        },
        "not_promoted": [
            "p_margin_ge_<k> and p_margin_gt_0 threshold rates: contract-level frequencies, never published",
            "p10/p25/p75/p90 beyond the two central ranges",
        ],
        "source": {
            "parameters_file": str(SOURCE_PARAMETERS_PATH),
            "parameters_sha256": SOURCE_PARAMETERS_SHA256,
            "parameters_schema": parameters["schema"],
            "research_version": parameters["v2_version"],
            "holdout_report_file": str(SOURCE_HOLDOUT_REPORT_PATH),
            "holdout_report_schema": holdout_report["schema"],
            "promoted_by": "Wave 3 production-migration review (docs/SCRIPT_ENGINE_V2_MIGRATION.md)",
        },
    }
    artifact["sha256"] = content_sha256(artifact)
    return artifact


def write_calibration(artifact: dict[str, Any], path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return artifact["sha256"]


def verify_calibration(artifact: dict[str, Any]) -> dict[str, Any]:
    """Return the artifact when its content matches its own hash AND the pinned hash; raise otherwise."""
    if content_sha256(artifact) != artifact.get("sha256"):
        raise CalibrationMismatch("control calibration content does not match its sha256")
    if artifact["sha256"] != CONTROL_CALIBRATION_SHA256:
        raise CalibrationMismatch(
            f"control calibration sha256 {artifact['sha256']} is not the pinned {CONTROL_CALIBRATION_SHA256}"
        )
    if artifact.get("schema") != CALIBRATION_SCHEMA or set(artifact.get("tiers") or {}) != set(CONTROL_TIERS):
        raise CalibrationMismatch("control calibration schema or tiers are not the promoted contract")
    return artifact


def load_calibration(root: Path | None = None) -> dict[str, Any]:
    """The verified production artifact (`root` defaults to the repository root)."""
    base = root if root is not None else Path(__file__).resolve().parents[3]
    return verify_calibration(json.loads((base / CALIBRATION_PATH).read_text(encoding="utf-8")))


def reference(artifact: dict[str, Any]) -> dict[str, Any]:
    """What a V2 claims artifact cites about the calibration it used."""
    return {
        "artifact": str(CALIBRATION_PATH),
        "schema": artifact["schema"],
        "calibration_version": artifact["calibration_version"],
        "sha256": artifact["sha256"],
        "source_parameters_sha256": artifact["source"]["parameters_sha256"],
        "development_seasons": artifact["development_seasons"],
        "validation_seasons": artifact["validation"]["seasons"],
    }


def historical_range(artifact: dict[str, Any], tier: str) -> dict[str, Any] | None:
    """The published range for one tier, with its provenance and its wording contract."""
    t = (artifact.get("tiers") or {}).get(tier)
    if t is None:
        return None
    v = artifact["validation"]["tiers"][tier]
    return {
        "label": RANGE_LABEL,
        "not": RANGE_NOT,
        "variable": "control_side_margin",
        "tier": tier,
        "n": t["n"],
        "win_rate": t["win_rate"],
        "median": t["median"],
        "mean": t["mean"],
        "central_50": t["central_50"],
        "central_80": t["central_80"],
        "development_seasons": artifact["development_seasons"],
        "validation": {
            "seasons": artifact["validation"]["seasons"],
            "n": v["n"],
            "win_rate": v["win_rate"]["rate"],
            "median": v["median"],
            "coverage_50": v["coverage_50"]["rate"],
            "coverage_80": v["coverage_80"]["rate"],
        },
        "calibration_sha256": artifact["sha256"],
    }
