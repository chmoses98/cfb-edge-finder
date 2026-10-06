"""Future-holdout / walk-forward validation.

*** WHY THIS EXISTS BEFORE THERE IS ANYTHING TO VALIDATE ***

Because methodology invented after seeing a profitable result is not
methodology. The moment a slice looks good is the worst possible moment
to be deciding what "confirmed" means. This layer is written while there
are zero settled games, so its rules cost nothing to accept.

*** THE ONE RULE EVERYTHING ELSE SERVES ***

    No rule may ever be validated on a game used to discover it.

Enforced mechanically: `validate_candidate` intersects the discovery game
set with the validation game set and REFUSES on any overlap, naming the
offending games. It is not a warning and there is no override parameter.

*** WHY THE RULE IS FROZEN BY HASH ***

A candidate rule is hashed at freeze time. Validation records that hash
and recomputes it, so a rule quietly widened after seeing validation data
-- a threshold nudged, a family added -- produces a different hash and a
refused run. Without that, "we validated the rule" means "we validated
some rule".

*** WHAT A PASSING VALIDATION PRODUCES ***

A `ValidationReport` with a verdict. Not an approval, not an artifact,
not a shadow qualification. A human reads it and decides. There is no
code path from a verdict to an `ApprovalState`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from cfb_edge_finder.research.protocol import PROTOCOL_VERSION, document_hash
from cfb_edge_finder.research.threshold_discovery import (
    SettledResearchObservation,
    _cluster_statistics,
)

HOLDOUT_VERSION = "holdout_validation_v1"


class ValidationVerdict(StrEnum):
    """Every verdict is descriptive. None authorises anything."""

    REFUSED_DISCOVERY_LEAKAGE = "REFUSED_DISCOVERY_LEAKAGE"
    """Validation games overlap discovery games. The run does not
    proceed."""

    REFUSED_RULE_MUTATED = "REFUSED_RULE_MUTATED"
    """The frozen hash does not match the rule presented."""

    REFUSED_NOT_PROSPECTIVE = "REFUSED_NOT_PROSPECTIVE"
    """Retrospective data cannot validate a prospective claim: its
    outcomes were known when it was assembled."""

    REFUSED_INCOMPATIBLE_SCOPE = "REFUSED_INCOMPATIBLE_SCOPE"
    """Validation data falls outside the rule's declared model version,
    family, timing or price domain."""

    INSUFFICIENT_VALIDATION_SAMPLE = "INSUFFICIENT_VALIDATION_SAMPLE"

    NOT_CORROBORATED = "NOT_CORROBORATED"
    """Ran cleanly; the discovery result did not reappear. A real and
    reportable outcome, not a failure of the process."""

    CORROBORATED_PENDING_HUMAN_REVIEW = "CORROBORATED_PENDING_HUMAN_REVIEW"
    """The strongest verdict available. The name is the whole point:
    corroboration is not approval, and the next step is a person."""


@dataclass(frozen=True)
class FrozenCandidateRule:
    """A discovery finding, frozen into a testable claim.

    Every scope axis is REQUIRED. An unscoped rule would silently apply
    to populations it was never discovered on -- the same failure mode
    the threshold artifact guards against."""

    rule_id: str
    families: tuple[str, ...]
    timing_labels: tuple[str, ...]
    model_versions: tuple[str, ...]
    minimum_signed_gap: float
    price_min: float
    price_max: float
    discovery_corpus_identifier: str
    discovery_cutoff: str
    discovery_game_ids: tuple[str, ...]
    protocol_version: str = PROTOCOL_VERSION

    def content_hash(self) -> str:
        """Hash over the rule's SUBSTANCE, discovery game set included.

        The game set is part of the identity on purpose: the same
        thresholds discovered on a different set of games is a different
        claim, and hashing only the numbers would let one be swapped for
        the other silently."""
        payload = {
            "rule_id": self.rule_id,
            "families": sorted(self.families),
            "timing_labels": sorted(self.timing_labels),
            "model_versions": sorted(self.model_versions),
            "minimum_signed_gap": self.minimum_signed_gap,
            "price_min": self.price_min,
            "price_max": self.price_max,
            "discovery_corpus_identifier": self.discovery_corpus_identifier,
            "discovery_cutoff": self.discovery_cutoff,
            "discovery_game_ids": sorted(self.discovery_game_ids),
            "protocol_version": self.protocol_version,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def selects(self, obs: SettledResearchObservation) -> bool:
        """Whether this observation falls inside the rule's scope."""
        return (
            obs.family in self.families
            and obs.timing_label in self.timing_labels
            and obs.model_version in self.model_versions
            and obs.signed_gap >= self.minimum_signed_gap
            and self.price_min <= obs.executable_price <= self.price_max
        )


@dataclass
class ValidationReport:
    verdict: ValidationVerdict
    detail: str
    rule_id: str
    frozen_rule_hash: str
    recomputed_rule_hash: str

    discovery_corpus_identifier: str = ""
    discovery_cutoff: str = ""
    discovery_game_count: int = 0
    validation_start: str | None = None
    validation_end: str | None = None

    observations: int = 0
    distinct_contracts: int = 0
    distinct_games: int = 0
    mean_research_unit_pl: float | None = None
    cluster_ci_low: float | None = None
    cluster_ci_high: float | None = None
    mean_clv: float | None = None
    settle_rate: float | None = None

    leaked_game_ids: tuple[str, ...] = ()
    protocol_version: str = PROTOCOL_VERSION
    protocol_document_sha256: str = field(default_factory=document_hash)
    holdout_version: str = HOLDOUT_VERSION

    @property
    def approves_anything(self) -> bool:
        """Always False. Present so the property can be asserted rather
        than argued about."""
        return False

    def to_payload(self) -> dict:
        return {
            "holdout_version": self.holdout_version,
            "protocol_version": self.protocol_version,
            "protocol_document_sha256": self.protocol_document_sha256,
            "verdict": self.verdict.value,
            "detail": self.detail,
            "rule_id": self.rule_id,
            "frozen_rule_hash": self.frozen_rule_hash,
            "recomputed_rule_hash": self.recomputed_rule_hash,
            "discovery": {
                "corpus_identifier": self.discovery_corpus_identifier,
                "cutoff": self.discovery_cutoff,
                "game_count": self.discovery_game_count,
            },
            "validation": {
                "start": self.validation_start,
                "end": self.validation_end,
                "observations": self.observations,
                "distinct_contracts": self.distinct_contracts,
                "distinct_games": self.distinct_games,
                "mean_research_unit_pl": self.mean_research_unit_pl,
                "cluster_ci_low": self.cluster_ci_low,
                "cluster_ci_high": self.cluster_ci_high,
                "mean_clv": self.mean_clv,
                "settle_rate": self.settle_rate,
            },
            "leaked_game_ids": list(self.leaked_game_ids),
            "approves_anything": self.approves_anything,
        }


def validate_candidate(
    rule: FrozenCandidateRule,
    *,
    frozen_hash: str,
    validation_observations: list[SettledResearchObservation],
    minimum_validation_games: int,
    validation_start: datetime | None = None,
    validation_end: datetime | None = None,
) -> ValidationReport:
    """Run a frozen rule against unseen prospective data.

    Refusals are checked in order of severity, most fundamental first, so
    a leaked game set is never masked by a sample-size complaint."""
    recomputed = rule.content_hash()
    report = ValidationReport(
        verdict=ValidationVerdict.NOT_CORROBORATED,
        detail="",
        rule_id=rule.rule_id,
        frozen_rule_hash=frozen_hash,
        recomputed_rule_hash=recomputed,
        discovery_corpus_identifier=rule.discovery_corpus_identifier,
        discovery_cutoff=rule.discovery_cutoff,
        discovery_game_count=len(rule.discovery_game_ids),
        validation_start=validation_start.isoformat() if validation_start else None,
        validation_end=validation_end.isoformat() if validation_end else None,
    )

    if recomputed != frozen_hash:
        report.verdict = ValidationVerdict.REFUSED_RULE_MUTATED
        report.detail = (
            f"rule hash {recomputed} does not match the frozen {frozen_hash}; the rule "
            f"changed after it was frozen"
        )
        return report

    non_prospective = [o for o in validation_observations if o.capture_mode != "PROSPECTIVE"]
    if non_prospective:
        report.verdict = ValidationVerdict.REFUSED_NOT_PROSPECTIVE
        report.detail = (
            f"{len(non_prospective)} validation observation(s) are not PROSPECTIVE; "
            f"retrospective data cannot validate a prospective claim"
        )
        return report

    discovery_games = set(rule.discovery_game_ids)
    validation_games = {o.game_id for o in validation_observations}
    leaked = discovery_games & validation_games
    if leaked:
        report.verdict = ValidationVerdict.REFUSED_DISCOVERY_LEAKAGE
        report.leaked_game_ids = tuple(sorted(leaked))
        report.detail = (
            f"{len(leaked)} game(s) appear in BOTH the discovery and validation sets: "
            f"{sorted(leaked)[:5]}. A rule may never be validated on a game used to "
            f"discover it."
        )
        return report

    selected = [o for o in validation_observations if rule.selects(o)]
    if not selected:
        report.verdict = ValidationVerdict.REFUSED_INCOMPATIBLE_SCOPE
        report.detail = (
            "no validation observation falls inside the rule's declared scope "
            "(family, timing, model version, gap or price domain)"
        )
        return report

    games = {o.game_id for o in selected}
    report.observations = len(selected)
    report.distinct_contracts = len({o.market_ticker for o in selected})
    report.distinct_games = len(games)

    if len(games) < minimum_validation_games:
        report.verdict = ValidationVerdict.INSUFFICIENT_VALIDATION_SAMPLE
        report.detail = (
            f"{len(games)} independent game(s) below the declared minimum "
            f"{minimum_validation_games}"
        )
        return report

    mean, _se, low, high = _cluster_statistics(selected)
    report.mean_research_unit_pl = mean
    report.cluster_ci_low = low
    report.cluster_ci_high = high
    report.settle_rate = sum(1 for o in selected if o.settled_yes) / len(selected)

    clv_rows = [o for o in selected if o.closing_price is not None]
    if clv_rows:
        report.mean_clv = sum(o.closing_price - o.executable_price for o in clv_rows) / len(clv_rows)

    # Corroboration requires the interval to exclude zero on the FAVOURABLE
    # side. A wide interval straddling zero is not corroboration, and a
    # negative one is a negative result reported as such.
    if low is not None and low > 0:
        report.verdict = ValidationVerdict.CORROBORATED_PENDING_HUMAN_REVIEW
        report.detail = (
            f"mean research-unit P/L {mean:+.4f} with a game-clustered 95% interval of "
            f"[{low:+.4f}, {high:+.4f}] over {len(games)} independent games. Corroborated, "
            f"NOT approved -- a human decides what happens next."
        )
    else:
        report.verdict = ValidationVerdict.NOT_CORROBORATED
        report.detail = (
            f"mean research-unit P/L {mean:+.4f} with a game-clustered 95% interval of "
            f"[{low}, {high}] over {len(games)} independent games; the discovery result did "
            f"not reappear."
        )
    return report
