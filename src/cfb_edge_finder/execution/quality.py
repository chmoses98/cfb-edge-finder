"""What is known about the FACTS, kept separate from what is believed about the GAME.

*** FOUR LAYERS, AND THEY MAY NEVER BE CONFLATED ***

    FACTUAL INPUT        objectively sourced, with a provenance and a clock
    HANDICAP ASSUMPTION  football judgement supplied from outside, with a region
    DERIVED FAIR VALUE   deterministic arithmetic on the assumption
    RECOMMENDATION       what survives robustness, fees, execution and correlation

This module owns the first layer's vocabulary. Its whole job is to make the
difference between "we looked and there are no injuries" and "we did not look"
impossible to lose, because those two produce the same handicap and only one of
them is a fact.

*** MISSING IS NOT CLEAN ***
`FieldQuality.MISSING` exists so that an absent injury report is recorded as an
absent injury report. Substituting "no injuries" for "no injury data" is the
single most expensive substitution available here: it converts the absence of
evidence into evidence of absence on the one axis most likely to move a
college-football line by a touchdown.

*** GATING IS PER FAMILY, NOT PER SPORT ***
Part of the point is to avoid over-generalising from one bad Saturday. "FCS
team totals went badly once" is not a rule; "we have fewer than N games of
scoring observations for this team, so its team-total distribution is not
defensible" is. `DataQualityGate` therefore names a FAMILY and a REASON, and
the reason has to be something a machine measured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FieldQuality(StrEnum):
    """The state of one factual field, on the reader's behalf."""

    FRESH = "fresh"
    STALE = "stale"
    PARTIAL = "partial"
    MISSING = "missing"
    CONFLICTING = "conflicting"
    LOW_COVERAGE = "low_coverage"


#: Qualities that do NOT support leaning on the field. Named as a set rather
#: than tested inline so every caller degrades on the same list.
UNRELIABLE_QUALITIES = frozenset(
    {
        FieldQuality.MISSING.value,
        FieldQuality.CONFLICTING.value,
        FieldQuality.LOW_COVERAGE.value,
    }
)


class ConfidenceCeiling(StrEnum):
    """The highest confidence a handicap may claim given its factual inputs.

    This is a CEILING and never a floor. A handicapper who says `low` with
    perfect data stays low; a handicapper who says `high` with nothing but a
    kickoff time is lowered to what the data supports, and the lowering is
    recorded with its reason.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT = "insufficient"


_CEILING_ORDER = {
    ConfidenceCeiling.INSUFFICIENT.value: 0,
    ConfidenceCeiling.LOW.value: 1,
    ConfidenceCeiling.MEDIUM.value: 2,
    ConfidenceCeiling.HIGH.value: 3,
}

#: The handicapper's own confidence words, on the same scale. `unstated` is
#: deliberately the LOWEST rank a stated word can beat rather than a synonym
#: for medium: a confidence nobody wrote down is not a middling confidence.
_CONFIDENCE_ORDER = {
    "unstated": 0,
    "insufficient": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}


def rank_confidence(word: str | None) -> int:
    return _CONFIDENCE_ORDER.get(str(word or "unstated").strip().lower(), 0)


def rank_ceiling(word: str | None) -> int:
    return _CEILING_ORDER.get(str(word or "insufficient").strip().lower(), 0)


def cap_confidence(stated: str | None, ceiling: str | None) -> tuple[str, bool]:
    """(the confidence that may actually be used, whether it was lowered)."""
    stated_word = str(stated or "unstated").strip().lower()
    if rank_confidence(stated_word) <= rank_ceiling(ceiling):
        return stated_word, False
    ceiling_word = str(ceiling or "insufficient").strip().lower()
    return ceiling_word, True


@dataclass(frozen=True)
class FieldProvenance:
    """Where one factual value came from, and when it was true.

    `observed_at` is when the SOURCE observed it, never when we fetched it.
    A three-day-old injury report fetched a second ago is three days old, and
    a freshness signal keyed on the fetch would call it current.
    """

    source: str
    observed_at: str | None = None
    quality: str = FieldQuality.MISSING.value
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "observed_at": self.observed_at,
            "quality": self.quality,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DataQualityGate:
    """A market family this game's FACTS cannot support, and why.

    A gate makes its family `unpriceable_insufficient_data` -- a counted,
    named, terminal bucket. It never forces a probability and it never quietly
    drops a contract, so the exhaustive-reconciliation invariant survives it
    unchanged.
    """

    family: str
    reason: str
    measured: str

    def as_dict(self) -> dict[str, Any]:
        return {"family": self.family, "reason": self.reason, "measured": self.measured}


@dataclass(frozen=True)
class DataQuality:
    """One game's factual-input state, as the handicap layer sees it."""

    confidence_ceiling: str = ConfidenceCeiling.INSUFFICIENT.value
    coverage: dict[str, str] = field(default_factory=dict)
    """domain -> FieldQuality, e.g. {"injuries": "missing", "weather": "fresh"}."""
    gates: tuple[DataQualityGate, ...] = ()
    missing_domains: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def gated_families(self) -> frozenset[str]:
        return frozenset(gate.family for gate in self.gates)

    def gate_for(self, family: str) -> DataQualityGate | None:
        for gate in self.gates:
            if gate.family == family:
                return gate
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "confidence_ceiling": self.confidence_ceiling,
            "coverage": dict(sorted(self.coverage.items())),
            "gates": [gate.as_dict() for gate in self.gates],
            "missing_domains": list(self.missing_domains),
            "notes": list(self.notes),
        }

    def merged_with_measurement(self, measured: DataQuality) -> DataQuality:
        """This handicap's data-quality claims, floored by what was MEASURED.

        *** A HANDICAPPER MAY LOWER THIS AND MAY NOT RAISE IT ***
        The ceiling and the gates are statements about which facts were
        actually fetched, and no amount of football knowledge makes an
        unfetched injury report present. So:

          ceiling  the LOWER of the two. A handicapper who says "low" with
                   perfect data stays low; one who says "high" with nothing
                   fetched is floored at what was fetched.
          gates    the UNION. A gate the measurement raised cannot be removed
                   by omitting it from a payload -- which is what would happen
                   if the payload's block simply replaced the packet's.
          coverage the measurement's, always. It is an observation, not a claim.

        Without this, a handicap payload that dropped `data_quality` entirely
        would silently re-open every family the measurement closed, and the
        fail-closed behaviour would be one forgotten key away from off.
        """
        by_family = {gate.family: gate for gate in measured.gates}
        for gate in self.gates:
            by_family.setdefault(gate.family, gate)
        lower = (
            self.confidence_ceiling
            if rank_ceiling(self.confidence_ceiling) <= rank_ceiling(measured.confidence_ceiling)
            else measured.confidence_ceiling
        )
        return DataQuality(
            confidence_ceiling=lower,
            coverage=dict(measured.coverage),
            gates=tuple(by_family[family] for family in sorted(by_family)),
            missing_domains=measured.missing_domains,
            notes=tuple(dict.fromkeys(measured.notes + self.notes)),
        )

    @classmethod
    def parse(cls, payload: Any) -> DataQuality:
        if payload is None:
            return cls()
        if not isinstance(payload, dict):
            raise ValueError(f"data_quality must be an object, got {type(payload).__name__}")

        ceiling = str(payload.get("confidence_ceiling") or ConfidenceCeiling.INSUFFICIENT.value)
        if ceiling not in _CEILING_ORDER:
            raise ValueError(
                f"data_quality.confidence_ceiling {ceiling!r} is not one of "
                f"{sorted(_CEILING_ORDER)}"
            )

        raw_coverage = payload.get("coverage") or {}
        if not isinstance(raw_coverage, dict):
            raise ValueError("data_quality.coverage must be an object keyed by domain")
        coverage: dict[str, str] = {}
        for domain, value in raw_coverage.items():
            word = str(value or FieldQuality.MISSING.value)
            if word not in set(FieldQuality):
                raise ValueError(
                    f"data_quality.coverage[{domain!r}] is {word!r}, not one of "
                    f"{sorted(q.value for q in FieldQuality)}"
                )
            coverage[str(domain)] = word

        gates: list[DataQualityGate] = []
        for entry in payload.get("gates") or []:
            if not isinstance(entry, dict):
                raise ValueError("data_quality.gates entries must be objects")
            family = str(entry.get("family") or "").strip()
            if not family:
                raise ValueError("a data-quality gate must name the family it closes")
            gates.append(
                DataQualityGate(
                    family=family,
                    reason=str(entry.get("reason") or "insufficient factual support"),
                    measured=str(entry.get("measured") or "not stated"),
                )
            )

        missing = tuple(str(d) for d in (payload.get("missing_domains") or []))
        if not missing:
            missing = tuple(
                sorted(d for d, q in coverage.items() if q == FieldQuality.MISSING.value)
            )

        return cls(
            confidence_ceiling=ceiling,
            coverage=coverage,
            gates=tuple(gates),
            missing_domains=missing,
            notes=tuple(str(n) for n in (payload.get("notes") or [])),
        )
