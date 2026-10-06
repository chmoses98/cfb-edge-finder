"""The handicap payload: the interface between whoever handicapped the
game and the deterministic code that prices its contracts.

*** THIS IS NOT A MODEL OUTPUT ***
Nothing in this repository produces one of these. A human -- or an LLM
doing the handicapping -- fills it in, and the evaluator treats it as the
only source of fair probability that exists. That is the point: the
retired projection model is not permitted anywhere near the live path, and
the way to keep it out is to make the fair-probability input arrive from
outside the system entirely.

*** ONE DISTRIBUTION PRICES MANY RUNGS ***
A game with 29 alternate spreads, 19 totals and 28 team totals does not
need 76 hand-entered probabilities. It needs ONE score distribution per
period; every rung of every ladder is then a closed-form consequence of
it. That is the whole reason this schema is shaped around periods rather
than around contracts.

*** AND ONE REGION SAYS HOW MUCH TO TRUST THEM (2.0.0) ***
A distribution alone is a point estimate wearing five numbers. Propagated
through the algebra it produces several hundred fair values that all look
equally sharp, and a reader four hundred rows deep has no way to tell a
rung whose edge survives a three-point margin error from one that does not.
So every period also carries an `uncertainty` block -- how far the margin
could be out, how far the total could be out, how much wider or narrower
the real spread of outcomes could be -- and the evaluator reports what
happens to every contract across that region. See `uncertainty.py`.

A 1.0.0 payload is still accepted and still prices everything. It simply
cannot produce a ROBUST recommendation, because nothing in it says what the
handicapper would still believe if they were a field goal wrong.

*** AND A THIRD BLOCK SAYS WHAT THE FACTS SUPPORT ***
`data_quality` is about the INPUTS, not the game: which factual domains were
actually observed, how fresh they were, and which market families this game's
evidence cannot defensibly support. It can only ever LOWER what a handicap
claims -- it caps confidence and it closes families -- and it never supplies a
probability. See `quality.py`.

*** WHAT IT REFUSES TO DO ***
It will not invent. A contract whose probability is not derivable from the
supplied distributions and is not named in `explicit_probabilities` comes
back UNPRICEABLE -- visible, counted, and excluded from the shortlist. A
plausible number would be worse than an absent one, because an absent one
cannot be bet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cfb_edge_finder.execution import (
    HANDICAP_SCHEMA_VERSION,
    HANDICAP_SCHEMA_VERSION_LEGACY,
)
from cfb_edge_finder.execution.quality import DataQuality, cap_confidence
from cfb_edge_finder.execution.semantics import PERIODS
from cfb_edge_finder.execution.uncertainty import (
    HandicapUncertainty,
    UncertaintyValidationError,
)

ACCEPTED_SCHEMA_VERSIONS = (HANDICAP_SCHEMA_VERSION, HANDICAP_SCHEMA_VERSION_LEGACY)


class HandicapValidationError(ValueError):
    """The payload cannot be used. Raised rather than repaired: a payload
    we silently 'fixed' would price hundreds of contracts off an
    assumption nobody made."""


@dataclass(frozen=True)
class PeriodDistribution:
    """Each team's points in one period, approximately normal, plus how far
    wrong the handicapper thinks it might be.

    `correlation` is between the two teams' scores in that period. It is
    required rather than defaulted: a shootout and a rock fight have very
    different totals for the same margin, and assuming 0 on the reader's
    behalf is exactly the kind of invisible assumption this schema exists
    to prevent.

    `uncertainty` is optional in the WIRE FORMAT and never optional in
    EFFECT: absent, it becomes `HandicapUncertainty.absent()`, which every
    consumer can distinguish from a stated region of width zero."""

    home_mean: float
    away_mean: float
    home_sd: float
    away_sd: float
    correlation: float
    uncertainty: HandicapUncertainty = field(default_factory=HandicapUncertainty.absent)

    @classmethod
    def parse(cls, period: str, payload: Any) -> PeriodDistribution:
        if not isinstance(payload, dict):
            raise HandicapValidationError(f"period {period!r}: expected an object, got {type(payload).__name__}")
        missing = [
            key
            for key in ("home_mean", "away_mean", "home_sd", "away_sd", "correlation")
            if key not in payload
        ]
        if missing:
            raise HandicapValidationError(f"period {period!r}: missing {', '.join(missing)}")
        try:
            values = {key: float(payload[key]) for key in
                      ("home_mean", "away_mean", "home_sd", "away_sd", "correlation")}
        except (TypeError, ValueError) as exc:
            raise HandicapValidationError(f"period {period!r}: non-numeric value ({exc})") from exc
        if values["home_mean"] < 0 or values["away_mean"] < 0:
            raise HandicapValidationError(f"period {period!r}: a mean score cannot be negative")
        if values["home_sd"] <= 0 or values["away_sd"] <= 0:
            raise HandicapValidationError(f"period {period!r}: standard deviations must be positive")
        if not -0.99 <= values["correlation"] <= 0.99:
            raise HandicapValidationError(
                f"period {period!r}: correlation {values['correlation']} is outside [-0.99, 0.99]"
            )
        try:
            uncertainty = HandicapUncertainty.parse(period, payload.get("uncertainty"))
        except UncertaintyValidationError as exc:
            raise HandicapValidationError(str(exc)) from exc
        # A stated correlation band that would push the correlation outside
        # the permitted range is refused here rather than clamped at pricing
        # time: clamping would quietly narrow a region the handicapper chose,
        # and a narrower region is a stronger robustness claim than they made.
        if uncertainty.stated and uncertainty.correlation_delta:
            low = values["correlation"] - uncertainty.correlation_delta
            high = values["correlation"] + uncertainty.correlation_delta
            if low < -0.99 or high > 0.99:
                raise HandicapValidationError(
                    f"period {period!r}: correlation {values['correlation']} with a "
                    f"correlation_delta of {uncertainty.correlation_delta} reaches outside "
                    "[-0.99, 0.99]; narrow one or the other rather than having pricing clamp it"
                )
        return cls(**values, uncertainty=uncertainty)

    def as_dict(self) -> dict[str, Any]:
        return {
            "home_mean": self.home_mean,
            "away_mean": self.away_mean,
            "home_sd": self.home_sd,
            "away_sd": self.away_sd,
            "correlation": self.correlation,
            "uncertainty": self.uncertainty.as_dict(),
        }

    def perturbed(
        self,
        *,
        margin_shift: float = 0.0,
        total_shift: float = 0.0,
        sd_scale: float = 1.0,
        correlation_shift: float = 0.0,
    ) -> PeriodDistribution:
        """The same handicap moved to one corner of its own region.

        Margin and total are moved ORTHOGONALLY: half of a margin shift goes
        on each team in opposite directions (total unchanged), half of a total
        shift goes on each team in the same direction (margin unchanged). A
        scheme that moved one team only would change both at once and make the
        two axes impossible to read apart.

        Means are floored at zero because a negative expected score is not a
        football opinion; the floor is applied AFTER the shift so a corner that
        would go negative is clipped rather than dropped, keeping the corner
        count the same for every game.
        """
        half_margin = margin_shift / 2.0
        half_total = total_shift / 2.0
        return PeriodDistribution(
            home_mean=max(0.0, self.home_mean + half_margin + half_total),
            away_mean=max(0.0, self.away_mean - half_margin + half_total),
            home_sd=max(1e-6, self.home_sd * sd_scale),
            away_sd=max(1e-6, self.away_sd * sd_scale),
            correlation=min(0.99, max(-0.99, self.correlation + correlation_shift)),
            uncertainty=self.uncertainty,
        )


@dataclass(frozen=True)
class HandicapPayload:
    game_key: str
    packet_hash: str | None
    teams: dict[str, str]
    period_distributions: dict[str, PeriodDistribution]
    explicit_probabilities: dict[str, float] = field(default_factory=dict)
    explicit_probability_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    """[low, high] for a ticker whose probability was supplied by hand.

    Without one, an explicit probability is a point estimate and cannot
    support a robustness claim -- exactly like a period with no uncertainty
    region. The ranges are keyed by full ticker, same as the probabilities."""
    low_confidence_families: tuple[str, ...] = ()
    declared_unpriceable_families: tuple[str, ...] = ()
    assumptions: str = ""
    confidence: str = "unstated"
    data_quality: DataQuality = field(default_factory=DataQuality)
    context_hash: str | None = None
    """The factual context this handicap was formed against.

    A handicap survives a price move; it does not necessarily survive the
    starting quarterback changing. This is what lets `state.py` tell those two
    apart instead of invalidating everything on any packet change."""
    thesis: str = ""
    """The handicapper's one-paragraph case for the game. Carried into the
    final report verbatim -- the code has no view of its own to put there."""
    opposing_case: str = ""
    """The strongest argument AGAINST the thesis, in the handicapper's own
    words. Absent means absent; the report says so rather than inventing
    a counterargument."""
    handicapped_at: str | None = None
    source: str = "external_handicap"
    schema_version_supplied: str = HANDICAP_SCHEMA_VERSION

    @property
    def is_legacy_point_estimate(self) -> bool:
        """True when nothing in this payload states how wrong it could be.

        Read by the evaluator, which refuses to call anything robust on the
        strength of a payload that never claimed a region."""
        return not any(
            dist.uncertainty.supports_robustness
            for dist in self.period_distributions.values()
        ) and not self.explicit_probability_ranges

    @property
    def effective_confidence(self) -> str:
        word, _lowered = cap_confidence(self.confidence, self.data_quality.confidence_ceiling)
        return word

    @property
    def confidence_was_capped(self) -> bool:
        _word, lowered = cap_confidence(self.confidence, self.data_quality.confidence_ceiling)
        return lowered

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": HANDICAP_SCHEMA_VERSION,
            "schema_version_supplied": self.schema_version_supplied,
            "game_key": self.game_key,
            "packet_hash": self.packet_hash,
            "context_hash": self.context_hash,
            "teams": dict(self.teams),
            "period_distributions": {k: v.as_dict() for k, v in self.period_distributions.items()},
            "explicit_probabilities": dict(self.explicit_probabilities),
            "explicit_probability_ranges": {
                k: list(v) for k, v in self.explicit_probability_ranges.items()
            },
            "low_confidence_families": list(self.low_confidence_families),
            "declared_unpriceable_families": list(self.declared_unpriceable_families),
            "assumptions": self.assumptions,
            "confidence": self.confidence,
            "effective_confidence": self.effective_confidence,
            "confidence_was_capped_by_data_quality": self.confidence_was_capped,
            "data_quality": self.data_quality.as_dict(),
            "thesis": self.thesis,
            "opposing_case": self.opposing_case,
            "handicapped_at": self.handicapped_at,
            "source": self.source,
        }


_DISTRIBUTION_KEYS = ("home_mean", "away_mean", "home_sd", "away_sd", "correlation")


def _is_blank_block(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    present = [block.get(key) for key in _DISTRIBUTION_KEYS]
    return all(value is None or value == "" for value in present)


def _parse_explicit_ranges(payload: Any, explicit: dict[str, float]) -> dict[str, tuple[float, float]]:
    raw = payload or {}
    if not isinstance(raw, dict):
        raise HandicapValidationError("explicit_probability_ranges must be an object keyed by ticker")
    ranges: dict[str, tuple[float, float]] = {}
    for ticker, value in raw.items():
        if value is None or value == "":
            continue
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise HandicapValidationError(
                f"explicit_probability_ranges[{ticker!r}] must be a two-element [low, high]"
            )
        try:
            low, high = float(value[0]), float(value[1])
        except (TypeError, ValueError) as exc:
            raise HandicapValidationError(
                f"explicit_probability_ranges[{ticker!r}] is not numeric"
            ) from exc
        if not (0.0 <= low <= 1.0 and 0.0 <= high <= 1.0):
            raise HandicapValidationError(
                f"explicit_probability_ranges[{ticker!r}] is outside [0, 1]"
            )
        if low > high:
            raise HandicapValidationError(
                f"explicit_probability_ranges[{ticker!r}] has low above high"
            )
        point = explicit.get(str(ticker))
        if point is None:
            # A range with no point estimate is not usable: there is nothing
            # for it to be the uncertainty OF, and treating the midpoint as
            # the estimate would invent a number the handicapper did not give.
            raise HandicapValidationError(
                f"explicit_probability_ranges[{ticker!r}] has no matching explicit_probabilities "
                "entry; a range is the uncertainty around a stated probability, not a substitute "
                "for one"
            )
        if not low <= point <= high:
            raise HandicapValidationError(
                f"explicit_probability_ranges[{ticker!r}] is [{low}, {high}] but the stated "
                f"probability is {point}, outside it"
            )
        ranges[str(ticker)] = (low, high)
    return ranges


def parse_handicap(payload: dict[str, Any]) -> HandicapPayload:
    if not isinstance(payload, dict):
        raise HandicapValidationError("handicap payload must be a JSON object")
    version = str(payload.get("schema_version") or "")
    if version and version not in ACCEPTED_SCHEMA_VERSIONS:
        raise HandicapValidationError(
            f"schema_version {version!r} is not one of {', '.join(ACCEPTED_SCHEMA_VERSIONS)}"
        )
    game_key = payload.get("game_key")
    if not game_key:
        raise HandicapValidationError("handicap payload has no game_key")

    teams = payload.get("teams")
    if not isinstance(teams, dict) or not teams.get("home") or not teams.get("away"):
        raise HandicapValidationError(
            "handicap payload must echo teams.home and teams.away; that echo is what "
            "catches a home/away flip before it inverts every spread in the game"
        )

    distributions: dict[str, PeriodDistribution] = {}
    raw_distributions = payload.get("period_distributions") or {}
    if not isinstance(raw_distributions, dict):
        raise HandicapValidationError("period_distributions must be an object keyed by period")
    for period, block in raw_distributions.items():
        if period not in PERIODS:
            raise HandicapValidationError(
                f"unknown period {period!r}; expected one of {', '.join(PERIODS)}"
            )
        if _is_blank_block(block):
            # An untouched template slot means "I did not handicap this
            # period", which is a legitimate answer. Every contract that
            # needed it becomes an explicit UNPRICEABLE downstream. A
            # PARTIALLY filled block is not treated this way -- that is
            # much more likely to be a mistake than a decision.
            continue
        distributions[str(period)] = PeriodDistribution.parse(str(period), block)

    explicit: dict[str, float] = {}
    raw_explicit = payload.get("explicit_probabilities") or {}
    if not isinstance(raw_explicit, dict):
        raise HandicapValidationError("explicit_probabilities must be an object keyed by ticker")
    for ticker, value in raw_explicit.items():
        if value is None or value == "":
            # Same reasoning: a null the reader left in the template is an
            # absent probability, not a malformed one.
            continue
        try:
            probability = float(value)
        except (TypeError, ValueError) as exc:
            raise HandicapValidationError(
                f"explicit probability for {ticker!r} is not numeric"
            ) from exc
        if not 0.0 <= probability <= 1.0:
            raise HandicapValidationError(
                f"explicit probability for {ticker!r} is {probability}, outside [0, 1]"
            )
        explicit[str(ticker)] = probability

    ranges = _parse_explicit_ranges(payload.get("explicit_probability_ranges"), explicit)

    try:
        data_quality = DataQuality.parse(payload.get("data_quality"))
    except ValueError as exc:
        raise HandicapValidationError(str(exc)) from exc

    return HandicapPayload(
        game_key=str(game_key),
        packet_hash=payload.get("packet_hash"),
        context_hash=payload.get("context_hash"),
        teams={"home": str(teams["home"]), "away": str(teams["away"])},
        period_distributions=distributions,
        explicit_probabilities=explicit,
        explicit_probability_ranges=ranges,
        low_confidence_families=tuple(str(f) for f in payload.get("low_confidence_families") or ()),
        declared_unpriceable_families=tuple(
            str(f) for f in payload.get("declared_unpriceable_families") or ()
        ),
        assumptions=str(payload.get("assumptions") or ""),
        confidence=str(payload.get("confidence") or "unstated"),
        data_quality=data_quality,
        thesis=str(payload.get("thesis") or ""),
        opposing_case=str(payload.get("opposing_case") or ""),
        handicapped_at=payload.get("handicapped_at"),
        source=str(payload.get("source") or "external_handicap"),
        schema_version_supplied=version or HANDICAP_SCHEMA_VERSION,
    )


def load_handicaps(path: Path) -> list[HandicapPayload]:
    """Accepts one payload, a list of payloads, or {"handicaps": [...]},
    because an LLM asked for "the payloads" will return any of the three
    and none of them is wrong."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "handicaps" in raw:
        raw = raw["handicaps"]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise HandicapValidationError("expected a payload, a list of payloads, or {'handicaps': [...]}")
    return [parse_handicap(item) for item in raw]


#: What goes in a blank `uncertainty` block. Every value is null rather than a
#: suggested number: a pre-filled "3.0" would be answered by leaving it alone,
#: and the region would then be this file's opinion about the game rather than
#: the handicapper's.
BLANK_UNCERTAINTY = {
    "margin_points": None,
    "total_points": None,
    "sd_scale_low": None,
    "sd_scale_high": None,
    "correlation_delta": None,
}


def template_for_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """A blank payload shaped for ONE game: every period that game
    actually needs, and every ticker that can only be priced explicitly,
    pre-listed with a null so nothing has to be discovered by the reader."""
    periods: set[str] = set()
    explicit_tickers: dict[str, Any] = {}
    for record in packet.get("contracts") or []:
        semantics = record.get("semantics") or {}
        if semantics.get("requires") == "period_distribution":
            periods.add(str(semantics.get("period")))
        else:
            explicit_tickers[str(record.get("ticker"))] = None

    teams = (packet.get("game_metadata") or {}).get("teams") or {}
    return {
        "schema_version": HANDICAP_SCHEMA_VERSION,
        "game_key": packet.get("game_key"),
        "packet_hash": packet.get("packet_hash"),
        "context_hash": (packet.get("factual_context") or {}).get("context_hash"),
        "teams": {"home": teams.get("home"), "away": teams.get("away")},
        "confidence": "",
        "thesis": "",
        "opposing_case": "",
        "assumptions": "",
        "period_distributions": {
            period: {
                "home_mean": None,
                "away_mean": None,
                "home_sd": None,
                "away_sd": None,
                "correlation": None,
                "uncertainty": dict(BLANK_UNCERTAINTY),
            }
            for period in sorted(periods)
        },
        "explicit_probabilities": explicit_tickers,
        "explicit_probability_ranges": {},
        "data_quality": {
            "confidence_ceiling": "",
            "coverage": {},
            "gates": [],
            "notes": [],
        },
        "low_confidence_families": [],
        "declared_unpriceable_families": [],
        "_notes": [
            "Every period listed above is required by at least one eligible contract in this game.",
            "A period left blank makes every contract that needs it explicitly UNPRICEABLE -- which "
            "is a legitimate outcome, not a failure.",
            "uncertainty is the half-width of how wrong you might be. margin_points and "
            "total_points are in POINTS and are independent of each other; sd_scale_low/high "
            "multiply the stated standard deviations. Leaving the block blank is allowed and "
            "means NO CONTRACT IN THIS GAME CAN BE CALLED ROBUST.",
            "explicit_probabilities values are YES probabilities. Delete any you cannot defensibly "
            "supply; do not guess to fill the form.",
            "explicit_probability_ranges[ticker] = [low, high] is the same idea for a probability "
            "you typed by hand. Without one, that contract can never be robust either.",
            "data_quality.confidence_ceiling caps the confidence above; gates[] closes a market "
            "family this game's FACTS cannot support, and its contracts become "
            "unpriceable_insufficient_data rather than being priced anyway.",
            "teams.home/teams.away must stay exactly as printed above.",
        ],
    }
