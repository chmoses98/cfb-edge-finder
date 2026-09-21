"""The factual context layer: what is objectively known about a game.

*** WHY THIS EXISTS ***
The live packet used to carry Kalshi prices and nothing else, and said so at
the top: no records, no offensive or defensive statistics, no injuries, no
depth chart, no weather, no rest or travel. That honesty was correct and it
was also the single biggest cost in the workflow -- every game's handicap
began with the handicapper going and finding all of it, one game at a time,
while a kickoff window closed.

So the repository fetches the facts. It does NOT form an opinion about them.

*** THE LINE THIS MODULE MUST NOT CROSS ***
A record is a fact. "Georgia is 8.5 points better than Arkansas" is a
prediction, and producing one here would reintroduce the retired model through
the side door. Nothing in this module ranks, rates, projects, adjusts for
opponent quality with a fitted coefficient, or emits a number that could be
read as a power rating. Every field is either something a source published or
something arithmetic on published values (points per game, rest days, a
win-loss record) -- and every one of those carries where it came from and when
it was true.

`tests/test_execution_context.py` asserts the absence mechanically, the same
way the analysis artifact's own no-projection scan does.

*** PROVENANCE IS PER FIELD, NOT PER GAME ***
A game whose weather is four minutes old and whose injury report is four days
old does not have "context freshness: 4 minutes". Each domain carries its own
source, its own observation time and its own quality, because they degrade
independently and the handicapper needs to know WHICH one is stale.

*** MISSING IS A VALUE ***
Every domain this layer knows how to populate appears in the output whether or
not it was populated. A domain that is absent is recorded as
`quality: missing` with the reason, so "we looked and found no injuries" and
"we could not reach the injury source" are different rows and neither can be
read as the other.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from cfb_edge_finder.execution import CONTEXT_SCHEMA_VERSION
from cfb_edge_finder.execution.quality import (
    ConfidenceCeiling,
    DataQuality,
    DataQualityGate,
    FieldQuality,
)

#: Every factual domain this layer knows about. A domain here and absent from a
#: game's context is a MEASURED GAP; a domain not here is a gap nobody has even
#: named, which is worse -- so the list is exhaustive by construction and the
#: renderer emits an entry for each one.
CONTEXT_DOMAINS = (
    "identity",
    "records",
    "rest_and_travel",
    "recent_results",
    "scoring",
    "efficiency",
    "situational",
    "turnovers_and_pace",
    "availability",
    "environment",
    "coaching",
    "market_reference",
)

#: How old an observation may be and still count as `fresh`, per domain.
#: These are deliberately different: a weather forecast four hours old is
#: nearly worthless and a season-to-date scoring average four hours old is
#: identical to the one fetched now.
FRESHNESS_BUDGET_SECONDS = {
    "identity": 7 * 24 * 3600.0,
    "records": 36 * 3600.0,
    "rest_and_travel": 7 * 24 * 3600.0,
    "recent_results": 36 * 3600.0,
    "scoring": 36 * 3600.0,
    "efficiency": 36 * 3600.0,
    "situational": 36 * 3600.0,
    "turnovers_and_pace": 36 * 3600.0,
    "availability": 18 * 3600.0,
    "environment": 6 * 3600.0,
    "coaching": 30 * 24 * 3600.0,
    "market_reference": 6 * 3600.0,
}

#: Games of observed scoring below which a team-total distribution is not
#: defensible from this repository's factual inputs.
#:
#: NOT a judgement about FCS football. It is a measurement about EVIDENCE: a
#: team with two games of scoring history has a sample of two, whoever it
#: plays for, and a team total derived from it would be a number with an error
#: bar wider than the ladder it prices. FBS teams clear this in week 3 and FCS
#: teams frequently do not, which is why the gate LOOKS like an FCS rule and is
#: not one -- an FCS team with a full season of observations passes it.
MIN_GAMES_FOR_TEAM_TOTAL = 3

#: Families closed when a team's scoring evidence is below that bar.
TEAM_TOTAL_FAMILIES = (
    "team_total",
    "first_half_team_total",
    "second_half_team_total",
    "quarter_team_total",
)


@dataclass(frozen=True)
class ContextField:
    """One factual domain for one game."""

    domain: str
    values: dict[str, Any] = field(default_factory=dict)
    source: str = "not_attempted"
    observed_at: str | None = None
    quality: str = FieldQuality.MISSING.value
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "values": dict(self.values),
            "source": self.source,
            "observed_at": self.observed_at,
            "quality": self.quality,
            "detail": self.detail,
        }

    @classmethod
    def missing(cls, domain: str, detail: str, source: str = "not_attempted") -> ContextField:
        return cls(domain=domain, source=source, quality=FieldQuality.MISSING.value, detail=detail)


def freshness_of(domain: str, observed_at: str | None, *, as_of: datetime) -> tuple[str, float | None]:
    """(quality, age in seconds) for one observation.

    An unparseable or absent timestamp is `missing`, never `fresh`. A source
    that will not say when it observed something has not told us that it is
    current, and assuming so on its behalf is the substitution this whole
    module exists to prevent.
    """
    if not observed_at:
        return FieldQuality.MISSING.value, None
    try:
        moment = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
    except ValueError:
        return FieldQuality.MISSING.value, None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    age = (as_of - moment).total_seconds()
    budget = FRESHNESS_BUDGET_SECONDS.get(domain, 24 * 3600.0)
    if age < 0:
        # A forecast for a future hour legitimately carries a future
        # timestamp. It is fresh; it is not "negatively aged".
        return FieldQuality.FRESH.value, 0.0
    return (FieldQuality.FRESH.value if age <= budget else FieldQuality.STALE.value), age


@dataclass(frozen=True)
class GameContext:
    """Every factual domain for one game, with per-domain provenance."""

    game_key: str
    fields: dict[str, ContextField]
    collected_at: str
    sources_attempted: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def quality_map(self) -> dict[str, str]:
        return {domain: self.fields[domain].quality for domain in sorted(self.fields)}

    @property
    def missing_domains(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                domain
                for domain, one in self.fields.items()
                if one.quality == FieldQuality.MISSING.value
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "game_key": self.game_key,
            "collected_at": self.collected_at,
            "sources_attempted": list(self.sources_attempted),
            "domains": {domain: one.as_dict() for domain, one in sorted(self.fields.items())},
            "coverage": self.quality_map(),
            "missing_domains": list(self.missing_domains),
            "notes": list(self.notes),
            "not_a_projection": (
                "Facts and arithmetic on facts. No rating, no projected score, no opponent-adjusted "
                "coefficient, no fair value, no recommendation. The handicap is the reader's."
            ),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GameContext:
        domains = payload.get("domains") or {}
        fields: dict[str, ContextField] = {}
        for domain in CONTEXT_DOMAINS:
            block = domains.get(domain) or {}
            fields[domain] = ContextField(
                domain=domain,
                values=dict(block.get("values") or {}),
                source=str(block.get("source") or "not_attempted"),
                observed_at=block.get("observed_at"),
                quality=str(block.get("quality") or FieldQuality.MISSING.value),
                detail=block.get("detail"),
            )
        return cls(
            game_key=str(payload.get("game_key")),
            fields=fields,
            collected_at=str(payload.get("collected_at") or ""),
            sources_attempted=tuple(str(s) for s in payload.get("sources_attempted") or ()),
            notes=tuple(str(n) for n in payload.get("notes") or ()),
        )

    @classmethod
    def empty(cls, game_key: str, *, reason: str, as_of: datetime) -> GameContext:
        """A game with no collected context at all.

        Deliberately a full object with every domain marked missing rather than
        `None`. A caller that has to test for None will eventually forget, and
        the forgotten branch treats absent facts as adequate ones."""
        return cls(
            game_key=game_key,
            fields={
                domain: ContextField.missing(domain, reason) for domain in CONTEXT_DOMAINS
            },
            collected_at=as_of.isoformat(),
            notes=(reason,),
        )


def refresh_quality(context: GameContext, *, as_of: datetime) -> GameContext:
    """Re-age every domain against the clock the slate is being built at.

    Quality is a function of WHEN THE SLATE IS BUILT, not of when the context
    file was written. A context store that recorded `fresh` at collection time
    and was read two days later would report two-day-old weather as current.
    """
    aged: dict[str, ContextField] = {}
    for domain, one in context.fields.items():
        if one.quality in (FieldQuality.MISSING.value, FieldQuality.CONFLICTING.value,
                           FieldQuality.LOW_COVERAGE.value, FieldQuality.PARTIAL.value):
            aged[domain] = one
            continue
        quality, age = freshness_of(domain, one.observed_at, as_of=as_of)
        detail = one.detail
        if quality == FieldQuality.STALE.value and age is not None:
            detail = (
                f"observed {age / 3600.0:.1f}h ago, past the "
                f"{FRESHNESS_BUDGET_SECONDS.get(domain, 86400.0) / 3600.0:.0f}h budget for this domain"
            )
        aged[domain] = ContextField(
            domain=domain,
            values=one.values,
            source=one.source,
            observed_at=one.observed_at,
            quality=quality,
            detail=detail,
        )
    return GameContext(
        game_key=context.game_key,
        fields=aged,
        collected_at=context.collected_at,
        sources_attempted=context.sources_attempted,
        notes=context.notes,
    )


# ------------------------------------------------------- quality gating


#: Domains whose absence cannot be worked around by a knowledgeable reader.
#: Missing EFFICIENCY can be substituted by somebody who watches the games;
#: missing RECORDS and SCORING cannot, because there is nothing to substitute.
LOAD_BEARING_DOMAINS = ("records", "scoring")

#: Domains whose absence is a known, material hazard rather than a nuisance.
HAZARD_DOMAINS = ("availability", "environment")


def confidence_ceiling(context: GameContext) -> tuple[str, tuple[str, ...]]:
    """(the highest confidence these facts support, the reasons it is capped).

    A ladder rather than a score, because a score would invite arithmetic on
    it and there is nothing to calibrate that arithmetic against. Each rung is
    a statement about evidence:

      INSUFFICIENT  a load-bearing domain is missing outright
      LOW           a load-bearing domain is stale or thin, or BOTH hazard
                    domains are unknown
      MEDIUM        the basics are there; something material is not
      HIGH          every domain this layer knows how to fill is fresh
    """
    reasons: list[str] = []
    quality = context.quality_map()

    load_bearing_missing = [
        d for d in LOAD_BEARING_DOMAINS if quality.get(d) == FieldQuality.MISSING.value
    ]
    if load_bearing_missing:
        return (
            ConfidenceCeiling.INSUFFICIENT.value,
            tuple(
                f"{domain} is missing, and it is load-bearing for any handicap of this game"
                for domain in load_bearing_missing
            ),
        )

    load_bearing_weak = [
        d
        for d in LOAD_BEARING_DOMAINS
        if quality.get(d) in (FieldQuality.STALE.value, FieldQuality.LOW_COVERAGE.value,
                              FieldQuality.PARTIAL.value)
    ]
    hazards_unknown = [
        d for d in HAZARD_DOMAINS if quality.get(d) == FieldQuality.MISSING.value
    ]
    if load_bearing_weak:
        reasons.extend(f"{d} is {quality.get(d)}" for d in load_bearing_weak)
    if len(hazards_unknown) == len(HAZARD_DOMAINS):
        reasons.append(
            "neither availability nor environment could be established; both move college "
            "football lines and neither absence may be read as 'nothing to report'"
        )
    if load_bearing_weak or len(hazards_unknown) == len(HAZARD_DOMAINS):
        return ConfidenceCeiling.LOW.value, tuple(reasons)

    unfresh = sorted(
        d for d, q in quality.items() if q != FieldQuality.FRESH.value
    )
    if unfresh:
        return (
            ConfidenceCeiling.MEDIUM.value,
            tuple(f"{d} is {quality.get(d)}" for d in unfresh),
        )
    return ConfidenceCeiling.HIGH.value, ()


def gates_for(context: GameContext) -> tuple[DataQualityGate, ...]:
    """Market families this game's FACTS cannot support.

    Measured, never assumed, and never generalised from one bad Saturday.
    The only gate implemented today is the team-total one, and it fires on a
    COUNT OF OBSERVED GAMES rather than on a division label -- see
    `MIN_GAMES_FOR_TEAM_TOTAL`.
    """
    gates: list[DataQualityGate] = []
    scoring = context.fields.get("scoring")
    if scoring is None:
        return ()

    if scoring.quality == FieldQuality.MISSING.value:
        for family in TEAM_TOTAL_FAMILIES:
            gates.append(
                DataQualityGate(
                    family=family,
                    reason="no observed scoring history for either team",
                    measured="games_observed=0",
                )
            )
        return tuple(gates)

    observed = [
        int(scoring.values.get("home_games_observed") or 0),
        int(scoring.values.get("away_games_observed") or 0),
    ]
    thin = min(observed)
    if thin < MIN_GAMES_FOR_TEAM_TOTAL:
        for family in TEAM_TOTAL_FAMILIES:
            gates.append(
                DataQualityGate(
                    family=family,
                    reason=(
                        f"a team in this game has {thin} observed scoring game(s), below the "
                        f"{MIN_GAMES_FOR_TEAM_TOTAL} needed for a defensible team-score "
                        "distribution"
                    ),
                    measured=f"min_games_observed={thin}",
                )
            )
    return tuple(gates)


def data_quality_for(context: GameContext) -> DataQuality:
    """The `data_quality` block a handicap template ships pre-filled.

    The handicapper may LOWER any of it and may add gates of their own. They
    may not raise the ceiling: the ceiling is a statement about what was
    fetched, and no amount of football knowledge makes an unfetched injury
    report present."""
    ceiling, reasons = confidence_ceiling(context)
    return DataQuality(
        confidence_ceiling=ceiling,
        coverage=context.quality_map(),
        gates=gates_for(context),
        missing_domains=context.missing_domains,
        notes=reasons,
    )


# --------------------------------------------------------- materiality


#: Domains whose CHANGE invalidates a handicap rather than merely updating a
#: packet. These are the facts a handicapper reasons FROM: a different starting
#: quarterback or a 25mph wind is a different game, and a handicap formed
#: before it is not a handicap of the game that will be played.
MATERIAL_DOMAINS = ("availability", "environment", "identity")

#: Domains whose change is informative but does not, on its own, force a
#: re-handicap. A tenth of a point of yards-per-play is not a new game.
INFORMATIVE_DOMAINS = tuple(d for d in CONTEXT_DOMAINS if d not in MATERIAL_DOMAINS)


def material_fingerprint(context: GameContext) -> str:
    """A hash over the MATERIAL domains' values only.

    Deliberately narrow. Hashing the whole context would make every refreshed
    yards-per-play a reason to re-handicap sixty games, and an invalidation
    that fires on everything is one nobody reads.
    """
    payload = {
        domain: context.fields[domain].values
        for domain in MATERIAL_DOMAINS
        if domain in context.fields
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def context_fingerprint(context: GameContext) -> str:
    """A hash over every domain's values, for change detection generally."""
    payload = {
        domain: one.values for domain, one in sorted(context.fields.items())
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def changed_domains(before: GameContext, after: GameContext) -> tuple[str, ...]:
    return tuple(
        sorted(
            domain
            for domain in CONTEXT_DOMAINS
            if (before.fields.get(domain) or ContextField(domain)).values
            != (after.fields.get(domain) or ContextField(domain)).values
        )
    )


# ---------------------------------------------------------- derivation


def rest_days(kickoff: datetime | None, last_game: datetime | None) -> int | None:
    """Whole days between a team's previous kickoff and this one.

    Arithmetic on two published timestamps, which is why it belongs here. If
    either is absent the answer is None -- never a default of 7, which is the
    most common value and therefore the most convincing wrong answer."""
    if kickoff is None or last_game is None:
        return None
    delta = kickoff - last_game
    if delta < timedelta(0):
        return None
    return int(delta.total_seconds() // 86400)


def per_game(total: float | None, games: int | None) -> float | None:
    if total is None or not games:
        return None
    return round(float(total) / float(games), 3)
