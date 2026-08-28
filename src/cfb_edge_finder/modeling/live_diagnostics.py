"""Pregame structural health checks on live projections and pricing.

*** DIFFERENT FROM diagnostics.py ***

`diagnostics.py` grades the model against SETTLED outcomes -- it needs
results, and this repository has none. These checks need no outcomes at
all: they assert properties that must hold at pricing time whatever the
games eventually do. A probability of 1.7, a spread ladder that prices
-14.5 above -3.5, or a neutral-site game carrying home-field advantage is
broken now, and waiting for a final score to discover that would waste the
one Week 1 we get.

*** THESE DIAGNOSTICS TUNE NOTHING ***

Every function reports. None adjusts a coefficient, a probability, or a
threshold. A failing check is a defect to investigate, never an input to
a correction -- a diagnostic that silently repaired what it measured
would destroy the very signal it exists to provide.

*** LARGE DISAGREEMENT IS NOT AN ANOMALY ***

There is deliberately no check for "the model disagrees with the market
by a lot." In Week 1 the point estimate carries no current-season
information at all (see docs/WEEK1_FOOTBALL_INPUT_AUDIT.md), so
disagreement is expected and is the research subject. Flagging it as a
fault would train the reader to dismiss exactly the observations worth
studying.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from cfb_edge_finder.expression.grouping import ContractSnapshot
from cfb_edge_finder.expression.ladders import check_model_tie_mass
from cfb_edge_finder.schemas.common import MarketFamily, Side


class DiagnosticSeverity(StrEnum):
    BLOCKER = "BLOCKER"
    """Pricing built on this is not usable for research."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    INFO = "INFO"
    """Recorded context, not a fault. Week 1's zero-carryover state is
    the main one: important to see, wrong to alarm about."""


@dataclass(frozen=True)
class ModelFinding:
    check_id: str
    severity: DiagnosticSeverity
    detail: str
    game_id: str | None = None
    market_ticker: str | None = None


@dataclass
class ModelHealthReport:
    findings: list[ModelFinding] = field(default_factory=list)
    contracts_checked: int = 0
    games_checked: int = 0

    def by_severity(self, severity: DiagnosticSeverity) -> list[ModelFinding]:
        return [f for f in self.findings if f.severity is severity]

    @property
    def blockers(self) -> list[ModelFinding]:
        return self.by_severity(DiagnosticSeverity.BLOCKER)

    @property
    def is_healthy(self) -> bool:
        """No BLOCKER and no HIGH. INFO findings are expected in Week 1."""
        return not self.blockers and not self.by_severity(DiagnosticSeverity.HIGH)

    def counts(self) -> dict[str, int]:
        return {s.value: len(self.by_severity(s)) for s in DiagnosticSeverity}


def check_probability_valid(probability: float | None, *, context: str) -> list[ModelFinding]:
    """Finite and inside [0, 1]. 0 and 1 exactly are flagged separately:
    they are arithmetically legal but assert certainty about a football
    game, which no honest model does."""
    findings: list[ModelFinding] = []
    if probability is None:
        return findings
    if not math.isfinite(probability):
        findings.append(
            ModelFinding("probability_finite", DiagnosticSeverity.BLOCKER,
                         f"{context}: probability is {probability!r}, not finite")
        )
        return findings
    if not 0.0 <= probability <= 1.0:
        findings.append(
            ModelFinding("probability_in_unit_interval", DiagnosticSeverity.BLOCKER,
                         f"{context}: probability {probability} outside [0, 1]")
        )
        return findings
    if probability in (0.0, 1.0):
        findings.append(
            ModelFinding("probability_degenerate", DiagnosticSeverity.HIGH,
                         f"{context}: probability is exactly {probability} -- "
                         f"the model is asserting certainty about a football game")
        )
    return findings


def check_ladder_monotonic(
    snapshots: list[ContractSnapshot], *, family: MarketFamily
) -> list[ModelFinding]:
    """A harder threshold must not be MORE likely than an easier one.

    For spreads on one team, a higher threshold is harder to cover, so
    model probability must be non-increasing in the threshold. For totals,
    a higher line is harder to exceed, so the same holds. A violation is a
    genuine internal inconsistency: the same simulated distribution
    produced both numbers, so they cannot legitimately disagree about
    which event is more likely."""
    findings: list[ModelFinding] = []
    groups: dict[tuple[str, str], list[ContractSnapshot]] = {}
    for snap in snapshots:
        sem = snap.semantics
        if sem.family is not family or sem.threshold is None or snap.model_probability is None:
            continue
        team = sem.team.value if sem.team is not None else "-"
        groups.setdefault((sem.game_id, team), []).append(snap)

    for (game_id, team), rungs in sorted(groups.items()):
        ordered = sorted(rungs, key=lambda s: s.semantics.threshold)
        for lower, higher in zip(ordered, ordered[1:], strict=False):
            if lower.semantics.threshold == higher.semantics.threshold:
                continue
            if higher.model_probability > lower.model_probability + 1e-9:
                findings.append(
                    ModelFinding(
                        f"{family.value}_ladder_monotonic",
                        DiagnosticSeverity.BLOCKER,
                        f"{game_id} {team}: threshold {higher.semantics.threshold} has "
                        f"probability {higher.model_probability:.4f} > threshold "
                        f"{lower.semantics.threshold}'s {lower.model_probability:.4f}. "
                        f"A harder event cannot be more likely.",
                        game_id=game_id,
                        market_ticker=higher.semantics.market_ticker,
                    )
                )
    return findings


def check_winner_complementarity(snapshots: list[ContractSnapshot]) -> list[ModelFinding]:
    """Deviation of the two moneyline sides from summing to 1.

    Delegates the arithmetic to `expression/ladders.check_model_tie_mass`,
    which already owns this diagnostic, rather than restating it with a
    second tolerance that could disagree.

    *** WHY A SHORTFALL IS INFO AND AN EXCESS IS HIGH ***

    Settlement partitions every final score into exactly one winner, so
    the true probabilities sum to 1. The model prices each side as
    "this team scores strictly more", which leaves simulated mass on an
    exact 0 margin unassigned. That can only make the sum LESS than 1 --
    it is the documented tie-mass artifact, reported and not corrected.

    A sum GREATER than 1 cannot be explained that way: no amount of tie
    mass adds probability. That means the two sides were not priced from
    one distribution, which is a real defect.

    An earlier draft of this check flagged any deviation beyond an
    invented 0.02 tolerance as HIGH, and duly reported 33 games whose
    ~2% shortfall is exactly the documented artifact. The tolerance was
    fiction; the direction of the deviation is the signal."""
    findings: list[ModelFinding] = []
    by_game: dict[str, dict[str, float]] = {}
    for snap in snapshots:
        sem = snap.semantics
        if sem.family is not MarketFamily.MONEYLINE or snap.model_probability is None:
            continue
        if sem.team in (Side.HOME, Side.AWAY):
            by_game.setdefault(sem.game_id, {})[sem.team.value] = snap.model_probability

    for game_id, sides in sorted(by_game.items()):
        if len(sides) != 2:
            continue
        finding = check_model_tie_mass(
            game_id=game_id,
            home_model_probability=sides[Side.HOME.value],
            away_model_probability=sides[Side.AWAY.value],
        )
        if finding is None:
            continue
        # `magnitude` is 1 - total: positive means a shortfall (tie mass),
        # negative means the sides sum to more than 1.
        if finding.magnitude < 0:
            findings.append(
                ModelFinding(
                    "winner_probability_excess",
                    DiagnosticSeverity.HIGH,
                    f"{game_id}: moneyline sides sum to "
                    f"{sides[Side.HOME.value] + sides[Side.AWAY.value]:.4f}, above 1. Tie mass "
                    f"cannot add probability, so the two sides were not priced from one "
                    f"distribution.",
                    game_id=game_id,
                )
            )
        else:
            findings.append(
                ModelFinding(
                    "winner_tie_mass",
                    DiagnosticSeverity.INFO,
                    finding.detail,
                    game_id=game_id,
                )
            )
    return findings


def check_unsupported_population_unpriced(snapshots: list[ContractSnapshot]) -> list[ModelFinding]:
    """A contract outside the supported population must carry no model
    probability.

    Pricing an unsupported game would put a number where the research has
    no basis for one, and that number would then flow into gap
    statistics as if it meant something."""
    findings: list[ModelFinding] = []
    for snap in snapshots:
        unsupported = snap.pricing_status is not None and snap.pricing_status != "model_priced"
        if unsupported and snap.model_probability is not None:
            findings.append(
                ModelFinding(
                    "unsupported_population_priced",
                    DiagnosticSeverity.BLOCKER,
                    f"pricing_status={snap.pricing_status!r} but model_probability="
                    f"{snap.model_probability} is set",
                    game_id=snap.semantics.game_id,
                    market_ticker=snap.semantics.market_ticker,
                )
            )
    return findings


def check_model_provenance(snapshots: list[ContractSnapshot]) -> list[ModelFinding]:
    """Every priced contract must name the model that priced it.

    Without it a candidate cannot be matched to threshold evidence
    gathered under a specific model version, and `None` on that axis is
    treated as a mismatch rather than a wildcard -- so a missing version
    silently removes the contract from every future rule."""
    findings: list[ModelFinding] = []
    for snap in snapshots:
        if snap.model_probability is not None and not snap.model_version:
            findings.append(
                ModelFinding(
                    "model_provenance_missing",
                    DiagnosticSeverity.HIGH,
                    "contract is priced but carries no model_version",
                    game_id=snap.semantics.game_id,
                    market_ticker=snap.semantics.market_ticker,
                )
            )
    return findings


def check_projection_reuse(snapshots: list[ContractSnapshot]) -> list[ModelFinding]:
    """All contracts of one game at one instant must come from ONE model
    version.

    Two versions inside a single game would mean two different simulated
    distributions priced sibling contracts, which silently breaks every
    equivalence and ladder relationship built on top of them."""
    findings: list[ModelFinding] = []
    by_game: dict[str, set[str]] = {}
    for snap in snapshots:
        if snap.model_probability is None or not snap.model_version:
            continue
        by_game.setdefault(snap.semantics.game_id, set()).add(snap.model_version)
    for game_id, versions in sorted(by_game.items()):
        if len(versions) > 1:
            findings.append(
                ModelFinding(
                    "projection_not_reused",
                    DiagnosticSeverity.BLOCKER,
                    f"{game_id} priced under {len(versions)} model versions in one snapshot set: "
                    f"{sorted(versions)}",
                    game_id=game_id,
                )
            )
    return findings


MIN_TRADEABLE_PRICE = 0.01
MAX_TRADEABLE_PRICE = 0.99
"""Kalshi's own tradeable range. A quote at exactly $1.00 cannot be
bought at a profit, so the pricing layer deliberately computes no fee for
it -- see kalshi/ladder_pricing.py."""


def check_fee_provenance(snapshots: list[ContractSnapshot]) -> list[ModelFinding]:
    """Fee-adjusted economics are meaningless on an unverified schedule --
    but only where a fee was owed in the first place.

    `fee_status` stays `unverified` in two entirely legitimate cases: the
    contract was never model-priced (no fee to verify), and the executable
    price sits outside [$0.01, $0.99] (unbuyable, so the pricing layer
    correctly refuses to invent a fee). Neither is a defect.

    An earlier draft of this check flagged both as HIGH and duly reported
    two contracts quoted at exactly $1.00 as fee-provenance failures. They
    were the system working. The real defect -- a priced, tradeable
    contract with no verified schedule -- is what remains HIGH here."""
    findings: list[ModelFinding] = []
    for snap in snapshots:
        if snap.model_probability is None:
            continue
        if snap.fee_status == "VERIFIED_CURRENT":
            continue
        price = snap.executable_yes_price
        untradeable = price is None or not (MIN_TRADEABLE_PRICE <= price <= MAX_TRADEABLE_PRICE)
        if untradeable:
            findings.append(
                ModelFinding(
                    "fee_absent_untradeable_quote",
                    DiagnosticSeverity.INFO,
                    f"priced contract quoted at {price} is outside the tradeable range, so no fee "
                    f"was computed. Correct refusal, not a provenance failure.",
                    game_id=snap.semantics.game_id,
                    market_ticker=snap.semantics.market_ticker,
                )
            )
            continue
        findings.append(
            ModelFinding(
                "fee_provenance_unverified",
                DiagnosticSeverity.HIGH,
                f"priced, tradeable contract at {price} carries fee_status={snap.fee_status!r}",
                game_id=snap.semantics.game_id,
                market_ticker=snap.semantics.market_ticker,
            )
        )
    return findings


def check_week1_carryover_disclosure(carryover_weights: dict[str, float]) -> list[ModelFinding]:
    """Records, loudly, when a projection carries NO current-season
    information.

    INFO, not a fault: at zero games played the weight is correctly zero
    and the point estimate is entirely prior-season. It is reported
    because a reader comparing that projection to a market which HAS seen
    2026 roster news needs to know which of the two is better informed.
    See docs/WEEK1_FOOTBALL_INPUT_AUDIT.md."""
    findings: list[ModelFinding] = []
    for game_id, weight in sorted(carryover_weights.items()):
        if weight <= 0.0:
            findings.append(
                ModelFinding(
                    "zero_current_season_information",
                    DiagnosticSeverity.INFO,
                    f"{game_id}: weight_on_current_season is {weight:.3f} -- the point estimate "
                    f"is entirely prior-season. Model-market disagreement here may reflect 2026 "
                    f"information the model cannot see, not edge.",
                    game_id=game_id,
                )
            )
    return findings


def run_model_health(snapshots: list[ContractSnapshot]) -> ModelHealthReport:
    """Every structural check that needs no settled outcome."""
    report = ModelHealthReport(
        contracts_checked=len(snapshots),
        games_checked=len({s.semantics.game_id for s in snapshots}),
    )
    for snap in snapshots:
        report.findings.extend(
            check_probability_valid(
                snap.model_probability, context=snap.semantics.market_ticker
            )
        )
    report.findings.extend(check_ladder_monotonic(snapshots, family=MarketFamily.SPREAD))
    report.findings.extend(check_ladder_monotonic(snapshots, family=MarketFamily.TOTAL))
    report.findings.extend(check_winner_complementarity(snapshots))
    report.findings.extend(check_unsupported_population_unpriced(snapshots))
    report.findings.extend(check_model_provenance(snapshots))
    report.findings.extend(check_projection_reuse(snapshots))
    report.findings.extend(check_fee_provenance(snapshots))
    return report
