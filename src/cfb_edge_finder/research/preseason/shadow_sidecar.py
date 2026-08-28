"""The live shadow SIDECAR: one transform per game, many contracts.

*** SIDECAR, NOT A DEPENDENCY ***

Canonical CONTROL capture is authoritative and must never depend on this
module succeeding. `ShadowSidecar.for_contract` catches its own failures
and returns an unavailable record; the caller persists the canonical
observation either way. A collection run that lost a CLOSING window
because a talent lookup raised would be a far worse outcome than a
missing shadow row.

*** ONE PROJECTION PER GAME, NOT PER TICKER ***

The scanner already builds ONE `CachedGameProjection` per game and prices
every contract from it. The shadow mirrors that exactly: one
`ShadowTransform` per (game, timing label), cached, and every contract on
that game reads from it. Rebuilding the transform per ticker would
reintroduce the per-contract model work this repository has already had
to fix once.

*** MARGIN DRAWS ARE READ ONCE AND SHARED ***

Both arms price from the SAME corrected margin array, so a difference
between them is the talent delta and nothing else. Drawing separately
would let Monte Carlo noise look like a model effect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from cfb_edge_finder.research.preseason.shadow_capture import (
    ShadowObservation,
    ShadowUnavailableReason,
    build_shadow_record,
)
from cfb_edge_finder.research.preseason.shadow_prior import SHADOW_MODEL_VERSION, TALENT_BETA
from cfb_edge_finder.research.preseason.shadow_transform import ShadowTransform, transform

SIDECAR_VERSION = "shadow_sidecar_v1"


@dataclass
class ShadowTelemetry:
    """Counted, never asserted. Coverage that is merely claimed tells a
    later reader nothing about how much of the season the shadow saw."""

    games_offered: int = 0
    """Distinct (game, timing label) pairs the sidecar was asked about.
    The denominator for transform coverage: a shadow that produced 94
    transforms from 94 offers saw every game, while 94 from 200 did
    not."""
    shadow_game_transforms: int = 0
    control_contracts_priced: int = 0
    shadow_contracts_priced: int = 0
    shadow_failures: int = 0
    failure_types: dict[str, int] = field(default_factory=dict)
    """Exception type names behind `shadow_failures`.

    The broad except below is necessary -- a research side effect must
    never cost a prospective capture -- but a bare count is undiagnosable.
    During development it swallowed an AttributeError from a typo, and
    only the counter revealed anything was wrong. Recording the TYPE
    costs nothing and turns "12 failures" into "12 AttributeError".
    """
    unavailable_reasons: dict[str, int] = field(default_factory=dict)

    def note_failure(self, exc: BaseException) -> None:
        name = type(exc).__name__
        self.failure_types[name] = self.failure_types.get(name, 0) + 1
        self.shadow_failures += 1

    def note_unavailable(self, reason: str) -> None:
        self.unavailable_reasons[reason] = self.unavailable_reasons.get(reason, 0) + 1

    @property
    def shadow_coverage(self) -> float:
        if not self.control_contracts_priced:
            return 0.0
        return self.shadow_contracts_priced / self.control_contracts_priced

    def to_dict(self) -> dict:
        return {
            "sidecar_version": SIDECAR_VERSION,
            "games_offered": self.games_offered,
            "shadow_game_transforms": self.shadow_game_transforms,
            "control_contracts_priced": self.control_contracts_priced,
            "shadow_contracts_priced": self.shadow_contracts_priced,
            "shadow_failures": self.shadow_failures,
            "failure_types": dict(sorted(self.failure_types.items())),
            "shadow_coverage": self.shadow_coverage,
            "unavailable_reasons": dict(sorted(self.unavailable_reasons.items())),
        }


@dataclass
class ShadowSidecar:
    """Per-run shadow state. One transform per (game, label), cached."""

    talent_by_team: dict[str, float]
    talent_season: int
    talent_source_version: str
    talent_fetched_at: str | None = None
    code_sha: str | None = None
    shadow_capture_started_at: str | None = None
    beta: float = TALENT_BETA

    telemetry: ShadowTelemetry = field(default_factory=ShadowTelemetry)
    _transforms: dict[tuple[str, str], ShadowTransform | None] = field(default_factory=dict)
    _reasons: dict[tuple[str, str], str] = field(default_factory=dict)

    def transform_for_game(
        self,
        *,
        game_id: str,
        timing_label: str,
        home_team_id: str,
        away_team_id: str,
        corrected_margin_samples: np.ndarray,
        control_margin_corrected: float,
        control_probability_canonical: float,
        control_expected_home: float,
        control_expected_away: float,
        both_fbs: bool,
    ) -> ShadowTransform | None:
        """One transform per (game, label). Cached; computed at most once."""
        key = (game_id, timing_label)
        if key in self._transforms:
            return self._transforms[key]
        self.telemetry.games_offered += 1

        if not both_fbs:
            self._transforms[key] = None
            self._reasons[key] = ShadowUnavailableReason.UNSUPPORTED_POPULATION.value
            return None

        home = self.talent_by_team.get(home_team_id)
        away = self.talent_by_team.get(away_team_id)
        if home is None and away is None:
            reason = ShadowUnavailableReason.TALENT_MISSING_BOTH
        elif home is None:
            reason = ShadowUnavailableReason.TALENT_MISSING_HOME
        elif away is None:
            reason = ShadowUnavailableReason.TALENT_MISSING_AWAY
        else:
            reason = None

        if reason is not None:
            self._transforms[key] = None
            self._reasons[key] = reason.value
            return None

        result = transform(
            corrected_margin_samples=corrected_margin_samples,
            control_margin_corrected=control_margin_corrected,
            control_probability_canonical=control_probability_canonical,
            control_expected_home=control_expected_home,
            control_expected_away=control_expected_away,
            home_talent=home,
            away_talent=away,
            beta=self.beta,
        )
        self._transforms[key] = result
        self.telemetry.shadow_game_transforms += 1
        return result

    def for_contract(
        self,
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
        projection_snapshot_id: str | None,
        home_team_id: str,
        away_team_id: str,
        corrected_margin_samples: np.ndarray | None,
        control_margin_corrected: float | None,
        control_expected_home: float | None,
        control_expected_away: float | None,
        both_fbs: bool,
        capture_mode: str,
        control_distribution=None,
        contract_family=None,
        contract_side=None,
        contract_threshold: float | None = None,
        named_team_side=None,
    ) -> ShadowObservation | None:
        """Build one contract's shadow record, or None on internal error.

        Returns None -- never raises -- on anything unexpected, so a
        surprise here can cost a shadow row but never a canonical
        capture. The failure is counted rather than swallowed silently."""
        try:
            self.telemetry.control_contracts_priced += 1
            if (
                corrected_margin_samples is None
                or control_margin_corrected is None
                or control_probability is None
                or control_expected_home is None
                or control_expected_away is None
            ):
                record = build_shadow_record(
                    observation_key=observation_key, game_id=game_id,
                    timing_label=timing_label, captured_at=captured_at,
                    kickoff_utc=kickoff_utc, market_ticker=market_ticker,
                    market_family=market_family,
                    executable_yes_price=executable_yes_price,
                    executable_no_price=executable_no_price,
                    control_model_version=control_model_version,
                    control_probability=None, control_projected_margin=None,
                    control_margin_samples=None,
                    talent_home=None, talent_away=None,
                    talent_source_version=self.talent_source_version,
                    both_fbs=both_fbs, capture_mode=capture_mode,
                    code_sha=self.code_sha,
                )
                self.telemetry.note_unavailable(record.unavailable_reason or "UNKNOWN")
                return record

            # Computed for the per-game CACHE and the telemetry counters
            # (this is what makes "one transform per game" true and
            # measurable). `build_shadow_record` below independently
            # derives the same delta from the same inputs; the two are
            # asserted equal by test, so a divergence between the cached
            # transform and the persisted record fails loudly rather than
            # producing two subtly different shadows.
            cached_transform = self.transform_for_game(
                game_id=game_id, timing_label=timing_label,
                home_team_id=home_team_id, away_team_id=away_team_id,
                corrected_margin_samples=corrected_margin_samples,
                control_margin_corrected=control_margin_corrected,
                control_probability_canonical=control_probability,
                control_expected_home=control_expected_home,
                control_expected_away=control_expected_away,
                both_fbs=both_fbs,
            )

            record = build_shadow_record(
                observation_key=observation_key, game_id=game_id,
                timing_label=timing_label, captured_at=captured_at,
                kickoff_utc=kickoff_utc, market_ticker=market_ticker,
                market_family=market_family,
                executable_yes_price=executable_yes_price,
                executable_no_price=executable_no_price,
                control_model_version=control_model_version,
                control_probability=control_probability,
                control_projected_margin=control_margin_corrected,
                control_margin_samples=corrected_margin_samples,
                # Pass the ACTUAL looked-up values, not None-when-failed.
                # Blanking both on any failure collapsed a one-sided miss
                # into TALENT_MISSING_BOTH and destroyed the very
                # diagnostic precision the unavailable reasons exist for.
                talent_home=self.talent_by_team.get(home_team_id),
                talent_away=self.talent_by_team.get(away_team_id),
                talent_source_version=self.talent_source_version,
                both_fbs=both_fbs, capture_mode=capture_mode,
                # The contract's own proposition, taken from what the
                # CANONICAL observation recorded, so the shadow prices
                # what the control priced rather than a re-derivation
                # that could drift from it.
                control_distribution=control_distribution,
                contract_family=contract_family,
                contract_side=contract_side,
                contract_threshold=contract_threshold,
                named_team_side=named_team_side,
                code_sha=self.code_sha,
            )
            if record.available:
                if cached_transform is not None and record.shadow_minus_control_margin is not None:
                    # Cheap invariant: the cached transform and the
                    # persisted record must agree on the adjustment.
                    if abs(cached_transform.delta - record.shadow_minus_control_margin) > 1e-9:
                        raise AssertionError(
                            "cached shadow transform and persisted record disagree on delta: "
                            f"{cached_transform.delta} vs {record.shadow_minus_control_margin}"
                        )
                self.telemetry.shadow_contracts_priced += 1
            else:
                reason = record.unavailable_reason or self._reasons.get(
                    (game_id, timing_label), "UNKNOWN"
                )
                self.telemetry.note_unavailable(reason)
            return record
        except Exception as exc:  # noqa: BLE001
            # Deliberately broad: a surprise in research code must never
            # cost a canonical prospective capture, least of all a
            # CLOSING one, which cannot be recovered. The exception TYPE
            # is recorded so the failure stays diagnosable.
            self.telemetry.note_failure(exc)
            return None


def shadow_key(observation_key: str, shadow_model_version: str = SHADOW_MODEL_VERSION) -> str:
    """Canonical dedup identity for a shadow row.

    Includes the shadow model version so a LATER candidate can coexist
    beside this one rather than overwriting the evidence this one is
    collecting. Append-only: the same control observation under the same
    shadow version always yields the same key, so a retry dedupes."""
    return f"{observation_key}|{shadow_model_version}"
