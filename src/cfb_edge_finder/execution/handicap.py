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

from cfb_edge_finder.execution import HANDICAP_SCHEMA_VERSION
from cfb_edge_finder.execution.semantics import PERIODS


class HandicapValidationError(ValueError):
    """The payload cannot be used. Raised rather than repaired: a payload
    we silently 'fixed' would price hundreds of contracts off an
    assumption nobody made."""


@dataclass(frozen=True)
class PeriodDistribution:
    """Each team's points in one period, approximately normal.

    `correlation` is between the two teams' scores in that period. It is
    required rather than defaulted: a shootout and a rock fight have very
    different totals for the same margin, and assuming 0 on the reader's
    behalf is exactly the kind of invisible assumption this schema exists
    to prevent."""

    home_mean: float
    away_mean: float
    home_sd: float
    away_sd: float
    correlation: float

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
        return cls(**values)

    def as_dict(self) -> dict[str, float]:
        return {
            "home_mean": self.home_mean,
            "away_mean": self.away_mean,
            "home_sd": self.home_sd,
            "away_sd": self.away_sd,
            "correlation": self.correlation,
        }


@dataclass(frozen=True)
class HandicapPayload:
    game_key: str
    packet_hash: str | None
    teams: dict[str, str]
    period_distributions: dict[str, PeriodDistribution]
    explicit_probabilities: dict[str, float] = field(default_factory=dict)
    low_confidence_families: tuple[str, ...] = ()
    declared_unpriceable_families: tuple[str, ...] = ()
    assumptions: str = ""
    confidence: str = "unstated"
    thesis: str = ""
    """The handicapper's one-paragraph case for the game. Carried into the
    final report verbatim -- the code has no view of its own to put there."""
    opposing_case: str = ""
    """The strongest argument AGAINST the thesis, in the handicapper's own
    words. Absent means absent; the report says so rather than inventing
    a counterargument."""
    handicapped_at: str | None = None
    source: str = "external_handicap"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": HANDICAP_SCHEMA_VERSION,
            "game_key": self.game_key,
            "packet_hash": self.packet_hash,
            "teams": dict(self.teams),
            "period_distributions": {k: v.as_dict() for k, v in self.period_distributions.items()},
            "explicit_probabilities": dict(self.explicit_probabilities),
            "low_confidence_families": list(self.low_confidence_families),
            "declared_unpriceable_families": list(self.declared_unpriceable_families),
            "assumptions": self.assumptions,
            "confidence": self.confidence,
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


def parse_handicap(payload: dict[str, Any]) -> HandicapPayload:
    if not isinstance(payload, dict):
        raise HandicapValidationError("handicap payload must be a JSON object")
    version = str(payload.get("schema_version") or "")
    if version and version != HANDICAP_SCHEMA_VERSION:
        raise HandicapValidationError(
            f"schema_version {version!r} is not {HANDICAP_SCHEMA_VERSION!r}"
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

    return HandicapPayload(
        game_key=str(game_key),
        packet_hash=payload.get("packet_hash"),
        teams={"home": str(teams["home"]), "away": str(teams["away"])},
        period_distributions=distributions,
        explicit_probabilities=explicit,
        low_confidence_families=tuple(str(f) for f in payload.get("low_confidence_families") or ()),
        declared_unpriceable_families=tuple(
            str(f) for f in payload.get("declared_unpriceable_families") or ()
        ),
        assumptions=str(payload.get("assumptions") or ""),
        confidence=str(payload.get("confidence") or "unstated"),
        thesis=str(payload.get("thesis") or ""),
        opposing_case=str(payload.get("opposing_case") or ""),
        handicapped_at=payload.get("handicapped_at"),
        source=str(payload.get("source") or "external_handicap"),
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
            }
            for period in sorted(periods)
        },
        "explicit_probabilities": explicit_tickers,
        "low_confidence_families": [],
        "declared_unpriceable_families": [],
        "_notes": [
            "Every period listed above is required by at least one eligible contract in this game.",
            "A period left blank makes every contract that needs it explicitly UNPRICEABLE -- which "
            "is a legitimate outcome, not a failure.",
            "explicit_probabilities values are YES probabilities. Delete any you cannot defensibly "
            "supply; do not guess to fill the form.",
            "teams.home/teams.away must stay exactly as printed above.",
        ],
    }
