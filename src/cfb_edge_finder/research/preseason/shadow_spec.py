"""FROZEN specifications for both arms of the prospective comparison.

*** WHY BOTH ARMS ARE FROZEN AND HASHED ***

The whole value of 2026 as confirmation evidence is that neither model
changed after the games were played. A specification that can drift is
not evidence of anything, so both arms are described here, hashed, and
checked by a test on every run.

*** THE BETA REPRODUCIBILITY GAP, RECORDED RATHER THAN GLOSSED ***

`TALENT_BETA` was fit by least squares on development seasons 2021-2023
against CONTROL residuals -- and the control's residuals depend slightly
on the Monte Carlo simulation count used at fit time. Refitting the same
data at 8,000 simulations instead of 2,000 gives 0.018898 rather than
0.018993: a 0.5% difference, about 0.013 points on a typical ~2.7 point
adjustment, immaterial to every conclusion drawn.

It is recorded anyway. A "frozen" constant that cannot be reproduced from
the cache without also knowing the simulation count is not fully frozen,
and discovering that later would cast doubt on the whole comparison.
`BETA_FIT_PROVENANCE` closes that gap.

*** NEITHER SPEC MAY BE TOUCHED BY 2026 ***

No function here reads a 2026 outcome, and `assert_specs_frozen` refuses
to run if either hash moves. Refitting beta on 2026, or adjusting decay
after seeing 2026 results, would destroy the untouched-evidence property
that is the entire point of collecting prospectively.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from cfb_edge_finder.research.preseason.control import (
    CONTROL_BASELINE_SHA256,
    CONTROL_MODEL_VERSION,
    control_manifest,
)
from cfb_edge_finder.research.preseason.shadow_prior import (
    CONFIRMATION_RESULT,
    CONFIRMATION_SEASON,
    DEVELOPMENT_SEASONS,
    SELECTION_SEASON,
    SHADOW_MODEL_VERSION,
    TALENT_BETA,
)

SHADOW_SPEC_VERSION = "prospective_shadow_spec_v1"

BETA_FIT_PROVENANCE = {
    "method": "least squares, no intercept",
    "response": "actual_home_margin - control_projected_margin",
    "regressor": "home_talent_composite - away_talent_composite",
    "development_seasons": list(DEVELOPMENT_SEASONS),
    "development_n_games": 2183,
    "fit_time_n_simulations": 2000,
    "beta_at_fit_time": 0.018993,
    "beta_refit_at_8000_simulations": 0.018898,
    "sensitivity_note": (
        "The control's residuals carry Monte Carlo noise, so beta moves in the 4th decimal "
        "with the simulation count. The 0.5% spread is about 0.013 points on a typical 2.7 "
        "point adjustment and changes no conclusion. Recorded so the constant is reproducible "
        "rather than merely asserted."
    ),
    "frozen_value_used_prospectively": TALENT_BETA,
}

TALENT_SOURCE = {
    "endpoint": "/talent",
    "provider": "collegefootballdata.com",
    "cache_version": "preseason_research_cache_v1",
    "cache_location": "research-data:data/research_cache/preseason/",
    "timing_semantics": (
        "The composite for season S is settled during the S-1 signing cycle and published "
        "before S begins. derived_from_season = S-1, enforced by "
        "features.PreseasonFeature.validate_for()."
    ),
    "join_key": "resolved team id (ingestion.team_matching), NOT the CFBD display name",
}

RESEARCH_PROTOCOL_VERSION = "prospective_research_protocol_v1"


@dataclass(frozen=True)
class ArmSpec:
    """One arm of the comparison, fully described."""

    arm: str
    model_version: str
    payload: dict

    def content_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    def to_dict(self) -> dict:
        return {
            "arm": self.arm,
            "model_version": self.model_version,
            "content_sha256": self.content_hash(),
            **self.payload,
        }


def control_spec() -> ArmSpec:
    """The canonical arm. Its payload IS the production control manifest,
    read live, so a production change is detected rather than described."""
    manifest = control_manifest()
    return ArmSpec(
        arm="CONTROL",
        model_version=CONTROL_MODEL_VERSION,
        payload={
            "role": "CANONICAL -- this is what production writes as model_probability",
            "control_config_sha256": manifest.content_hash(),
            "parameters": manifest.payload,
        },
    )


def shadow_spec() -> ArmSpec:
    """The research arm. Never canonical, never written to
    model_probability."""
    return ArmSpec(
        arm="SHADOW",
        model_version=SHADOW_MODEL_VERSION,
        payload={
            "role": "RESEARCH ONLY -- recorded beside the control, never replacing it",
            "adjustment": "control_margin + beta * (home_talent - away_talent)",
            "beta": TALENT_BETA,
            "beta_fit_provenance": BETA_FIT_PROVENANCE,
            "talent_source": TALENT_SOURCE,
            "development_seasons": list(DEVELOPMENT_SEASONS),
            "selection_season": SELECTION_SEASON,
            "confirmation_season": CONFIRMATION_SEASON,
            "historical_confirmation_result": CONFIRMATION_RESULT,
            "research_protocol_version": RESEARCH_PROTOCOL_VERSION,
            "prospective_season": 2026,
            "may_be_refit_on_2026": False,
        },
    )


CONTROL_SPEC_SHA256 = "e18db59c02e0280c8fea4040dd6b95ea6809e3c7f9f517de44553aa30ec373c8"
SHADOW_SPEC_SHA256 = "af88af99eadae807daf06d865241b6ac5e87ebdc128ae7a9efc51bd745f70985"
"""Both arms as frozen on 2026-08-28, before any 2026 game was settled.

`tests/test_prospective_shadow.py` asserts the LIVE hashes against these,
so a change to either arm fails the suite rather than silently
invalidating 2026 as confirmation evidence. The test is the mechanism;
constants nobody checks would prove nothing."""


class SpecDriftError(RuntimeError):
    """A frozen specification changed."""


def assert_specs_frozen(
    *, control_sha256: str, shadow_sha256: str
) -> None:
    """Refuse to capture if either arm has drifted.

    Called at the top of every shadow capture: a side-by-side comparison
    where one side quietly moved is not a comparison."""
    if control_spec().content_hash() != control_sha256:
        raise SpecDriftError(
            "the CONTROL specification has changed; 2026 can no longer serve as untouched "
            "confirmation evidence for the frozen shadow candidate"
        )
    if shadow_spec().content_hash() != shadow_sha256:
        raise SpecDriftError(
            "the SHADOW specification has changed; refitting beta or altering the adjustment "
            "destroys the untouched-evidence property this collection exists to create"
        )
    if control_spec().payload["control_config_sha256"] != CONTROL_BASELINE_SHA256:
        raise SpecDriftError("the production control model no longer matches its frozen baseline")


def specs_payload() -> dict:
    return {
        "shadow_spec_version": SHADOW_SPEC_VERSION,
        "control": control_spec().to_dict(),
        "shadow": shadow_spec().to_dict(),
    }
