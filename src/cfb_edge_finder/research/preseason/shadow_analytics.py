"""CONTROL vs SHADOW comparison, and the hypothesis fixed before results.

*** THE HYPOTHESIS IS REGISTERED HERE, BEFORE ANY 2026 SETTLEMENT ***

At the time this module is committed the corpus holds ZERO settled 2026
games and ZERO CLOSING captures. `PROSPECTIVE_HYPOTHESIS` is therefore a
genuine prediction rather than a description, and its hash is recorded so
a later report can prove which text it followed.

*** WHY IT MUST NOT MOVE ***

The historical result -- about one point of Week 1 margin MAE on 47
confirmation games -- is suggestive, not settled. 2026 is the independent
test. Changing the hypothesis after seeing 2026, or picking the market
families or weeks where the shadow happened to look good, would convert
that test into another development set and leave nothing to confirm it
with.

*** AT n = 0 THIS REPORTS INSUFFICIENT EVIDENCE ***

Not a null result, not a lean, not an early indication. There is a real
difference between "we measured no effect" and "we have not measured",
and only the second is true today.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass, field
from enum import StrEnum

from cfb_edge_finder.research.preseason.shadow_capture import (
    SHADOW_RECORD_SCHEMA_VERSION_V1,
)
from cfb_edge_finder.research.preseason.shadow_contract_pricing import (
    PROBABILITY_SEMANTICS_VERSION,
)

HYPOTHESIS_VERSION = "prospective_shadow_hypothesis_v1"

REGISTERED_AT = "2026-08-28"
SETTLED_2026_GAMES_AT_REGISTRATION = 0
CLOSING_CAPTURES_AT_REGISTRATION = 0

PRIMARY_HYPOTHESIS = (
    "For supported FBS-vs-FBS games in Weeks 1-3 of the 2026 season, "
    "shadow-preseason-talent-v1 will reduce absolute margin error relative to CONTROL, "
    "measured as a paired per-game difference with a game-clustered interval."
)

SECONDARY_HYPOTHESES = (
    "improve winner Brier score and log loss",
    "reduce favourite-tail margin bias",
    "improve contract-level calibration",
    "produce model-market residuals better aligned with closing prices",
)

PREREGISTERED_POPULATION = {
    "capture_mode": "PROSPECTIVE only",
    "teams": "FBS vs FBS only",
    "primary_segment": "weeks 1-3",
    "also_reported": ["week_1", "weeks_2_3", "weeks_4_plus"],
    "timing_labels": ["T_24H", "T_6H", "T_90", "T_60", "T_30", "CLOSING"],
    "market_families": ["moneyline", "spread", "total"],
    "families_chosen_before_results": True,
    "shadow_must_be_available": True,
}

PROHIBITED = (
    "refitting beta on 2026",
    "changing the decay structure based on 2026 outcomes",
    "selecting games, weeks or families where the shadow looks good",
    "modifying the shadow because Kalshi disagrees with it",
    "reporting a direction before the interval is computed",
)


class EvidenceState(StrEnum):
    INSUFFICIENT_NATURAL_EVIDENCE = "INSUFFICIENT_NATURAL_EVIDENCE"
    """No settled prospective games. NOT a null result -- nothing was
    measured."""

    MEASURED = "MEASURED"


class EvidenceProvenance(StrEnum):
    """WHERE a control-vs-shadow comparison's shadow numbers came from.

    The distinction is load-bearing. `shadow_snapshot.py` can RECONSTRUCT
    a shadow value for any past observation by re-applying the frozen
    beta -- useful for a research table, and worthless as prospective
    evidence, because it was computed after the fact and could have been
    computed differently. Only rows the live scanner wrote at capture
    time are prospective."""

    PROSPECTIVE_SHADOW_CAPTURE = "PROSPECTIVE_SHADOW_CAPTURE"
    """Written by the live scanner at capture time, before the game.
    The ONLY provenance admissible for headline prospective validation."""

    RECONSTRUCTED_RESEARCH = "RECONSTRUCTED_RESEARCH"
    """Re-derived after the fact from a stored control observation.
    Fine for exploration; never headline evidence."""


def hypothesis_hash() -> str:
    payload = {
        "version": HYPOTHESIS_VERSION,
        "primary": PRIMARY_HYPOTHESIS,
        "secondary": list(SECONDARY_HYPOTHESES),
        "population": PREREGISTERED_POPULATION,
        "prohibited": list(PROHIBITED),
        "registered_at": REGISTERED_AT,
        "settled_at_registration": SETTLED_2026_GAMES_AT_REGISTRATION,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def hypothesis_manifest() -> dict:
    return {
        "hypothesis_version": HYPOTHESIS_VERSION,
        "hypothesis_sha256": hypothesis_hash(),
        "registered_at": REGISTERED_AT,
        "settled_2026_games_at_registration": SETTLED_2026_GAMES_AT_REGISTRATION,
        "closing_captures_at_registration": CLOSING_CAPTURES_AT_REGISTRATION,
        "primary": PRIMARY_HYPOTHESIS,
        "secondary": list(SECONDARY_HYPOTHESES),
        "population": PREREGISTERED_POPULATION,
        "prohibited": list(PROHIBITED),
    }


@dataclass(frozen=True)
class SettledShadowPair:
    """One settled game with both arms' predictions."""

    provenance: EvidenceProvenance
    game_id: str
    week: int
    timing_label: str
    control_probability: float
    shadow_probability: float
    control_margin: float
    shadow_margin: float
    actual_home_margin: int
    probability_semantics_version: str | None = None
    """Which capture instrumentation produced `shadow_probability`.

    None or the v1 value means the probability channel is NOT usable:
    v1 wrote one P(home wins) per game onto every contract regardless of
    family or side. The MARGIN channel of those same rows is unaffected
    and stays eligible."""

    @property
    def home_won(self) -> bool:
        return self.actual_home_margin > 0

    @property
    def probability_channel_eligible(self) -> bool:
        return self.probability_semantics_version == PROBABILITY_SEMANTICS_VERSION


@dataclass
class ShadowComparison:
    state: EvidenceState
    n_games: int = 0
    detail: str = ""
    control_margin_mae: float | None = None
    shadow_margin_mae: float | None = None
    paired_margin_delta: float | None = None
    margin_ci: tuple[float | None, float | None] = (None, None)
    control_log_loss: float | None = None
    shadow_log_loss: float | None = None
    control_brier: float | None = None
    shadow_brier: float | None = None
    # Channel-aware evidence accounting. The margin and probability
    # channels of the SAME row can have different eligibility, because
    # the v1 probability defect never touched the margin.
    n_probability_games: int = 0
    probability_state: EvidenceState = EvidenceState.INSUFFICIENT_NATURAL_EVIDENCE
    probability_exclusions: dict[str, int] = field(default_factory=dict)
    segments: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "hypothesis": hypothesis_manifest(),
            "state": self.state.value,
            "n_settled_games": self.n_games,
            "detail": self.detail,
            "control_margin_mae": self.control_margin_mae,
            "shadow_margin_mae": self.shadow_margin_mae,
            "paired_margin_delta": self.paired_margin_delta,
            "margin_ci_low": self.margin_ci[0],
            "margin_ci_high": self.margin_ci[1],
            "control_log_loss": self.control_log_loss,
            "shadow_log_loss": self.shadow_log_loss,
            "control_brier": self.control_brier,
            "shadow_brier": self.shadow_brier,
            "n_margin_paired": self.n_games,
            "n_probability_paired": self.n_probability_games,
            "probability_state": self.probability_state.value,
            "probability_exclusions": dict(sorted(self.probability_exclusions.items())),
            "segments": dict(sorted(self.segments.items())),
        }


def _log_loss(prob: float, won: bool) -> float:
    p = min(max(prob, 1e-12), 1 - 1e-12)
    return -math.log(p if won else 1 - p)


def compare(
    pairs: list[SettledShadowPair],
    *,
    require_prospective_capture: bool = True,
) -> ShadowComparison:
    """Compare the two arms on settled games.

    `require_prospective_capture` defaults True: reconstructed rows are
    DROPPED from the headline comparison. A reconstructed shadow value
    was computed after the outcome existed and could have been computed
    differently; letting it stand beside genuinely captured rows would
    quietly convert the prospective test into a retrospective one.

    With no admissible pairs this returns INSUFFICIENT_NATURAL_EVIDENCE
    and no numbers at all -- reporting a delta of 0.0 would invite a
    reader to treat absence of measurement as a measured null."""
    if require_prospective_capture:
        pairs = [
            p for p in pairs
            if p.provenance is EvidenceProvenance.PROSPECTIVE_SHADOW_CAPTURE
        ]
    if not pairs:
        return ShadowComparison(
            state=EvidenceState.INSUFFICIENT_NATURAL_EVIDENCE,
            n_games=0,
            detail=(
                "No settled prospective 2026 games with both arms recorded. Nothing has been "
                "measured; this is not a null result."
            ),
        )

    control_err = [abs(p.control_margin - p.actual_home_margin) for p in pairs]
    shadow_err = [abs(p.shadow_margin - p.actual_home_margin) for p in pairs]
    diffs = [s - c for c, s in zip(control_err, shadow_err, strict=True)]
    mean_diff = statistics.fmean(diffs)
    if len(diffs) >= 2:
        se = statistics.stdev(diffs) / math.sqrt(len(diffs))
        ci = (mean_diff - 1.96 * se, mean_diff + 1.96 * se)
    else:
        ci = (None, None)

    return ShadowComparison(
        state=EvidenceState.MEASURED,
        n_games=len(pairs),
        detail=f"{len(pairs)} settled game(s) with both arms recorded.",
        control_margin_mae=statistics.fmean(control_err),
        shadow_margin_mae=statistics.fmean(shadow_err),
        paired_margin_delta=mean_diff,
        margin_ci=ci,
        **_probability_metrics(pairs),
    )


def _probability_metrics(pairs: list[SettledShadowPair]) -> dict:
    """Winner metrics on the probability-eligible subset only.

    The v1 capture wrote one P(home wins) per game onto every contract,
    so its probability channel cannot enter a headline comparison. Its
    MARGIN channel is untouched and stays in, which is why eligibility is
    decided per channel rather than per row. Excluded rows are COUNTED
    and named, never silently dropped."""
    eligible = [p for p in pairs if p.probability_channel_eligible]
    exclusions: dict[str, int] = {}
    for p in pairs:
        if p.probability_channel_eligible:
            continue
        key = (
            "PROBABILITY_SEMANTICS_V1"
            if p.probability_semantics_version in (None, SHADOW_RECORD_SCHEMA_VERSION_V1)
            else f"PROBABILITY_SEMANTICS_{p.probability_semantics_version}"
        )
        exclusions[key] = exclusions.get(key, 0) + 1

    if not eligible:
        return {
            "n_probability_games": 0,
            "probability_state": EvidenceState.INSUFFICIENT_NATURAL_EVIDENCE,
            "probability_exclusions": exclusions,
        }
    return {
        "n_probability_games": len(eligible),
        "probability_state": EvidenceState.MEASURED,
        "probability_exclusions": exclusions,
        "control_log_loss": statistics.fmean(
            _log_loss(p.control_probability, p.home_won) for p in eligible
        ),
        "shadow_log_loss": statistics.fmean(
            _log_loss(p.shadow_probability, p.home_won) for p in eligible
        ),
        "control_brier": statistics.fmean(
            (p.control_probability - (1.0 if p.home_won else 0.0)) ** 2 for p in eligible
        ),
        "shadow_brier": statistics.fmean(
            (p.shadow_probability - (1.0 if p.home_won else 0.0)) ** 2 for p in eligible
        ),
    }
