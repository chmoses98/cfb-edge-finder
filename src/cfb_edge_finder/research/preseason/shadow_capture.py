"""Prospective CONTROL-vs-SHADOW capture. Linked, additive, never canonical.

*** THE ARCHITECTURE, AND WHY THIS ONE ***

A shadow record is a SEPARATE row that LINKS to the canonical observation
by `observation_key`, rather than a new field inside it. Three reasons:

1. The canonical observation stays byte-identical. Existing v1/v2 rows
   are never rewritten, and a reader that knows nothing about shadows
   parses the corpus exactly as before.
2. Shadow capture can fail without touching canonical capture. If talent
   is missing the control row still lands; only the shadow record carries
   an unavailable reason.
3. The shadow can be re-derived or dropped wholesale without risking the
   prospective evidence the whole system exists to collect.

*** CANONICAL model_probability IS NEVER TOUCHED ***

Nothing here writes to a corpus row. `build_shadow_record` takes the
control's already-computed numbers as INPUTS and returns a new object.
There is no code path from this module into the canonical write.

*** FAIL CLOSED, AND SAY WHY ***

Missing or timing-invalid talent yields a record with an explicit
`ShadowUnavailableReason` and NO adjustment. It never imputes a zero
delta silently: "we had no talent data" and "talent said these teams are
equal" are different claims, and collapsing them would corrupt the very
coverage statistics that tell us how much of 2026 the shadow actually
saw.

*** PROSPECTIVE ONLY ***

`build_shadow_record` requires `capture_mode == "PROSPECTIVE"` and a
captured_at strictly before kickoff. A shadow value for a past capture
would be a backfilled number wearing a prospective label -- the exact
thing that would make 2026 useless as confirmation evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from cfb_edge_finder.research.preseason.shadow_prior import (
    SHADOW_MODEL_VERSION,
    TALENT_BETA,
)

SHADOW_RECORD_SCHEMA_VERSION = "shadow_observation_v1"

PROSPECTIVE = "PROSPECTIVE"


class ShadowUnavailableReason(StrEnum):
    """Why a shadow value could not be produced. Every one is explicit."""

    TALENT_MISSING_HOME = "TALENT_MISSING_HOME"
    TALENT_MISSING_AWAY = "TALENT_MISSING_AWAY"
    TALENT_MISSING_BOTH = "TALENT_MISSING_BOTH"
    TALENT_SEASON_MISALIGNED = "TALENT_SEASON_MISALIGNED"
    """The talent row does not precede the season being predicted."""
    UNSUPPORTED_POPULATION = "UNSUPPORTED_POPULATION"
    """Not FBS-vs-FBS. The candidate was validated on that population
    only and must not be extrapolated beyond it."""
    CONTROL_NOT_PRICED = "CONTROL_NOT_PRICED"
    """No control projection to adjust. A shadow without a control is not
    a comparison."""
    NOT_PROSPECTIVE = "NOT_PROSPECTIVE"
    CAPTURED_AT_OR_AFTER_KICKOFF = "CAPTURED_AT_OR_AFTER_KICKOFF"


class ShadowBackfillError(RuntimeError):
    """A shadow value was requested for a non-prospective capture."""


@dataclass(frozen=True)
class ShadowObservation:
    """One shadow record, linked to a canonical observation.

    Carries the CONTROL values too, so a later analysis can compare the
    two arms without re-joining to the canonical corpus and risking a
    mismatch."""

    schema_version: str
    observation_key: str
    game_id: str
    timing_label: str
    captured_at: str
    market_ticker: str
    market_family: str | None
    executable_yes_price: float | None
    executable_no_price: float | None

    control_model_version: str | None
    control_probability: float | None
    control_projected_margin: float | None

    shadow_model_version: str
    shadow_probability: float | None
    shadow_projected_margin: float | None

    talent_home: float | None
    talent_away: float | None
    talent_differential: float | None
    talent_source_version: str
    beta: float

    shadow_minus_control_probability: float | None
    shadow_minus_control_margin: float | None

    available: bool
    unavailable_reason: str | None
    capture_mode: str
    code_sha: str | None
    provenance: str

    @property
    def is_canonical(self) -> bool:
        """Always False. Present so it can be asserted rather than
        argued about."""
        return False

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "observation_key": self.observation_key,
            "game_id": self.game_id,
            "timing_label": self.timing_label,
            "captured_at": self.captured_at,
            "market_ticker": self.market_ticker,
            "market_family": self.market_family,
            "executable_yes_price": self.executable_yes_price,
            "executable_no_price": self.executable_no_price,
            "control_model_version": self.control_model_version,
            "control_probability": self.control_probability,
            "control_projected_margin": self.control_projected_margin,
            "shadow_model_version": self.shadow_model_version,
            "shadow_probability": self.shadow_probability,
            "shadow_projected_margin": self.shadow_projected_margin,
            "talent_home": self.talent_home,
            "talent_away": self.talent_away,
            "talent_differential": self.talent_differential,
            "talent_source_version": self.talent_source_version,
            "beta": self.beta,
            "shadow_minus_control_probability": self.shadow_minus_control_probability,
            "shadow_minus_control_margin": self.shadow_minus_control_margin,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "capture_mode": self.capture_mode,
            "code_sha": self.code_sha,
            "provenance": self.provenance,
            "is_canonical": self.is_canonical,
        }


def _unavailable(
    *, reason: ShadowUnavailableReason, base: dict, control_probability=None,
    control_margin=None, talent_home=None, talent_away=None,
) -> ShadowObservation:
    return ShadowObservation(
        **base,
        control_probability=control_probability,
        control_projected_margin=control_margin,
        shadow_model_version=SHADOW_MODEL_VERSION,
        shadow_probability=None,
        shadow_projected_margin=None,
        talent_home=talent_home,
        talent_away=talent_away,
        talent_differential=None,
        beta=TALENT_BETA,
        shadow_minus_control_probability=None,
        shadow_minus_control_margin=None,
        available=False,
        unavailable_reason=reason.value,
    )


def build_shadow_record(
    *,
    observation_key: str,
    game_id: str,
    timing_label: str,
    captured_at: datetime,
    kickoff_utc: datetime | None,
    market_ticker: str,
    market_family: str | None,
    executable_yes_price: float | None,
    executable_no_price: float | None,
    control_model_version: str | None,
    control_probability: float | None,
    control_projected_margin: float | None,
    control_margin_samples,
    talent_home: float | None,
    talent_away: float | None,
    talent_source_version: str,
    both_fbs: bool,
    capture_mode: str,
    code_sha: str | None = None,
    provenance: str = "prospective shadow capture",
) -> ShadowObservation:
    """Build one shadow record beside a canonical observation.

    `control_margin_samples` is the control's OWN simulated margin
    distribution. Shifting it and re-reading P(margin > 0) keeps the
    shadow's winner probability consistent with its shifted margin --
    moving the margin while leaving the probability alone would produce
    an arm that contradicts itself. A zero margin resolves to AWAY,
    matching research/settlement.py."""
    base = dict(
        schema_version=SHADOW_RECORD_SCHEMA_VERSION,
        observation_key=observation_key,
        game_id=game_id,
        timing_label=timing_label,
        captured_at=captured_at.isoformat(),
        market_ticker=market_ticker,
        market_family=market_family,
        executable_yes_price=executable_yes_price,
        executable_no_price=executable_no_price,
        control_model_version=control_model_version,
        talent_source_version=talent_source_version,
        capture_mode=capture_mode,
        code_sha=code_sha,
        provenance=provenance,
    )

    if capture_mode != PROSPECTIVE:
        raise ShadowBackfillError(
            f"shadow capture requested for capture_mode={capture_mode!r}; a shadow value for a "
            f"non-prospective row would be a backfilled number wearing a prospective label, "
            f"which destroys 2026 as confirmation evidence"
        )
    if kickoff_utc is not None and captured_at >= kickoff_utc:
        return _unavailable(
            reason=ShadowUnavailableReason.CAPTURED_AT_OR_AFTER_KICKOFF, base=base,
            control_probability=control_probability, control_margin=control_projected_margin,
        )
    if not both_fbs:
        return _unavailable(
            reason=ShadowUnavailableReason.UNSUPPORTED_POPULATION, base=base,
            control_probability=control_probability, control_margin=control_projected_margin,
        )
    if control_probability is None or control_projected_margin is None:
        return _unavailable(reason=ShadowUnavailableReason.CONTROL_NOT_PRICED, base=base)
    if talent_home is None and talent_away is None:
        return _unavailable(
            reason=ShadowUnavailableReason.TALENT_MISSING_BOTH, base=base,
            control_probability=control_probability, control_margin=control_projected_margin,
        )
    if talent_home is None:
        return _unavailable(
            reason=ShadowUnavailableReason.TALENT_MISSING_HOME, base=base,
            control_probability=control_probability, control_margin=control_projected_margin,
            talent_away=talent_away,
        )
    if talent_away is None:
        return _unavailable(
            reason=ShadowUnavailableReason.TALENT_MISSING_AWAY, base=base,
            control_probability=control_probability, control_margin=control_projected_margin,
            talent_home=talent_home,
        )

    differential = float(talent_home) - float(talent_away)
    delta = TALENT_BETA * differential
    shadow_margin_value = control_projected_margin + delta

    shadow_probability = None
    if control_margin_samples is not None and len(control_margin_samples):
        import numpy as np

        shifted = np.asarray(control_margin_samples, dtype=float) + delta
        shadow_probability = float(np.mean(shifted > 0))

    return ShadowObservation(
        **base,
        control_probability=control_probability,
        control_projected_margin=control_projected_margin,
        shadow_model_version=SHADOW_MODEL_VERSION,
        shadow_probability=shadow_probability,
        shadow_projected_margin=shadow_margin_value,
        talent_home=float(talent_home),
        talent_away=float(talent_away),
        talent_differential=differential,
        beta=TALENT_BETA,
        shadow_minus_control_probability=(
            None if shadow_probability is None else shadow_probability - control_probability
        ),
        shadow_minus_control_margin=delta,
        available=True,
        unavailable_reason=None,
    )


@dataclass
class ShadowCoverageReport:
    """How much of the slate the shadow actually saw.

    Reported separately from control coverage: a shadow that silently
    covered a third of games would otherwise look like a fair comparison
    on the full slate."""

    records: list[ShadowObservation]

    @property
    def total(self) -> int:
        return len(self.records)

    @property
    def available(self) -> int:
        return sum(1 for r in self.records if r.available)

    @property
    def unavailable(self) -> int:
        return self.total - self.available

    def reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            if record.unavailable_reason:
                counts[record.unavailable_reason] = counts.get(record.unavailable_reason, 0) + 1
        return dict(sorted(counts.items()))

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "available": self.available,
            "unavailable": self.unavailable,
            "coverage_rate": (self.available / self.total) if self.total else 0.0,
            "unavailable_reasons": self.reason_counts(),
        }
