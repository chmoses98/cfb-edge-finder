"""Empirical threshold DISCOVERY -- structurally incapable of approval.

*** WHAT THIS PRODUCES ***

`DraftResearchFinding` objects. A finding says "in this prespecified
slice, the fee-adjusted research P/L looked like this, with this
cluster-aware interval, on this many independent games." That is the
beginning of an argument, not a rule.

*** WHAT IT CANNOT PRODUCE ***

It cannot emit a `ShadowThresholdArtifact`, cannot write an
`ApprovalState`, and cannot rank slices by profitability. Those are not
policy choices enforced by review -- they are absent capabilities. There
is no code path from this module to an approved artifact, and a test
plants an absurdly profitable synthetic sample to prove that even a
100%-ROI result produces a draft finding and nothing else.

*** WHY IT REFUSES TO RANK BY ROI ***

Sorting candidate slices by return and reporting the top one is how a
random dataset produces a strategy. With enough (family x timing x gap x
price) rectangles, some rectangle always looks excellent, and its
apparent edge is a selection artifact that no amount of subsequent
honesty removes. So slices are prespecified by the protocol, evaluated
exhaustively, and reported in a FIXED order with the number examined
stated alongside -- a nominal result from one of forty slices is reported
as one of forty.

*** THE SLICES ARE THE PROTOCOL'S, NOT THIS MODULE'S ***

Family, timing label and model version come from
`research/protocol.py`'s MANDATORY_PARTITIONS. Gap and price bands are
DESCRIPTIVE buckets that already exist for reporting. Nothing here
invents a cut point, and nothing here searches for one.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from cfb_edge_finder.research.protocol import (
    CLUSTER_UNIT,
    PROTOCOL_VERSION,
    document_hash,
)

DISCOVERY_VERSION = "threshold_discovery_v1"

DRAFT_RESEARCH_FINDING = "DRAFT_RESEARCH_FINDING"
"""The ONLY status this module can emit. There is deliberately no
constant here for any approved or validated state."""

BLOCKED_ON_SAMPLE = "EMPIRICAL_THRESHOLD_RESEARCH_BLOCKED_ON_SAMPLE"


class DiscoveryRefusal(StrEnum):
    """Why a slice yielded no finding. Every one is a refusal to
    speculate, not an error."""

    NO_SETTLED_OBSERVATIONS = "NO_SETTLED_OBSERVATIONS"
    BELOW_DECLARED_MINIMUM_GAMES = "BELOW_DECLARED_MINIMUM_GAMES"
    NO_DECLARED_MINIMUM = "NO_DECLARED_MINIMUM"
    """A human must state the minimum sample they will accept. Inferring
    it from the data it will then be applied to is circular."""
    SINGLE_GAME_CLUSTER = "SINGLE_GAME_CLUSTER"
    """One game is one outcome. No interval is computable from it, and a
    point estimate without an interval is not reportable."""
    MIXED_MODEL_VERSIONS = "MIXED_MODEL_VERSIONS"
    NOT_PROSPECTIVE = "NOT_PROSPECTIVE"


@dataclass(frozen=True)
class SettledResearchObservation:
    """One settled, prospective, supported observation.

    Deliberately narrow: everything needed to evaluate a slice and
    nothing that could be used to search for one."""

    game_id: str
    market_ticker: str
    family: str
    timing_label: str
    model_version: str
    side: str
    executable_price: float
    fee_adjusted_break_even: float
    model_probability: float
    settled_yes: bool
    capture_mode: str = "PROSPECTIVE"
    closing_price: float | None = None

    @property
    def signed_gap(self) -> float:
        """Model probability minus the fee-adjusted break-even. Positive
        means the model thinks this side is underpriced after fees."""
        return self.model_probability - self.fee_adjusted_break_even

    @property
    def research_unit_pl(self) -> float:
        """Fee-adjusted profit on ONE contract, in dollars.

        A research unit, explicitly not a stake: one contract, always,
        regardless of confidence. Sizing is a separate and disconnected
        question (`cfb_edge_finder.sizing`)."""
        cost = self.fee_adjusted_break_even
        return (1.0 - cost) if self.settled_yes else -cost


@dataclass(frozen=True)
class SliceKey:
    family: str
    timing_label: str
    model_version: str

    def as_dict(self) -> dict:
        return {
            "family": self.family,
            "timing_label": self.timing_label,
            "model_version": self.model_version,
        }


@dataclass(frozen=True)
class DraftResearchFinding:
    """A descriptive result for one prespecified slice.

    `status` is fixed at DRAFT_RESEARCH_FINDING. There is no field that
    could carry an approval, and no method that could set one."""

    slice_key: SliceKey
    status: str
    observations: int
    distinct_contracts: int
    distinct_games: int
    mean_research_unit_pl: float
    cluster_se: float | None
    cluster_ci_low: float | None
    cluster_ci_high: float | None
    mean_signed_gap: float
    settle_rate: float
    slices_examined: int
    """How many slices were evaluated in the run that produced this. A
    nominal result from one of forty is reported as one of forty."""
    protocol_version: str = PROTOCOL_VERSION
    protocol_document_sha256: str = ""
    discovery_version: str = DISCOVERY_VERSION

    @property
    def interval_excludes_zero(self) -> bool:
        """Descriptive only. Emphatically NOT a promotion criterion --
        with enough slices, some interval always excludes zero."""
        if self.cluster_ci_low is None or self.cluster_ci_high is None:
            return False
        return self.cluster_ci_low > 0 or self.cluster_ci_high < 0


@dataclass
class DiscoveryReport:
    findings: list[DraftResearchFinding] = field(default_factory=list)
    refusals: dict[str, str] = field(default_factory=dict)
    status: str = BLOCKED_ON_SAMPLE
    slices_examined: int = 0
    total_settled_observations: int = 0
    total_settled_games: int = 0
    discovery_game_ids: tuple[str, ...] = ()
    """Recorded so holdout validation can mechanically exclude them. A
    rule may never be validated on a game used to discover it."""

    def to_payload(self) -> dict:
        return {
            "discovery_version": DISCOVERY_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "protocol_document_sha256": document_hash(),
            "status": self.status,
            "slices_examined": self.slices_examined,
            "total_settled_observations": self.total_settled_observations,
            "total_settled_games": self.total_settled_games,
            "discovery_game_ids": list(self.discovery_game_ids),
            "refusals": dict(sorted(self.refusals.items())),
            "findings": [
                {
                    "slice": f.slice_key.as_dict(),
                    "status": f.status,
                    "observations": f.observations,
                    "distinct_contracts": f.distinct_contracts,
                    "distinct_games": f.distinct_games,
                    "mean_research_unit_pl": f.mean_research_unit_pl,
                    "cluster_ci_low": f.cluster_ci_low,
                    "cluster_ci_high": f.cluster_ci_high,
                    "mean_signed_gap": f.mean_signed_gap,
                    "settle_rate": f.settle_rate,
                    "slices_examined": f.slices_examined,
                }
                for f in self.findings
            ],
        }


def _cluster_statistics(
    observations: list[SettledResearchObservation],
) -> tuple[float, float | None, float | None, float | None]:
    """Mean research-unit P/L with a game-clustered standard error.

    The cluster mean is taken per game FIRST, then averaged. Treating
    twenty contracts on one game as twenty observations would shrink the
    interval by roughly sqrt(20) on evidence that is really one football
    outcome -- see the protocol's section 3."""
    by_game: dict[str, list[float]] = defaultdict(list)
    for obs in observations:
        by_game[getattr(obs, CLUSTER_UNIT)].append(obs.research_unit_pl)
    game_means = [statistics.fmean(v) for v in by_game.values()]
    overall = statistics.fmean(game_means)
    if len(game_means) < 2:
        return overall, None, None, None
    se = statistics.stdev(game_means) / math.sqrt(len(game_means))
    # 1.96 is the normal approximation, stated rather than hidden. With
    # few clusters this is optimistic; the cluster count is reported
    # alongside so a reader can discount it.
    return overall, se, overall - 1.96 * se, overall + 1.96 * se


def discover_threshold_candidates(
    observations: list[SettledResearchObservation],
    *,
    minimum_settled_games: int | None,
) -> DiscoveryReport:
    """Evaluate every prespecified slice. Rank nothing.

    `minimum_settled_games` must be supplied by a human. Passing None is
    legal and produces a refusal on every slice -- the honest outcome
    when nobody has yet declared what sample they would accept."""
    report = DiscoveryReport()

    prospective = [o for o in observations if o.capture_mode == "PROSPECTIVE"]
    if len(prospective) != len(observations):
        report.refusals[DiscoveryRefusal.NOT_PROSPECTIVE.value] = (
            f"{len(observations) - len(prospective)} non-prospective observation(s) excluded"
        )

    report.total_settled_observations = len(prospective)
    report.total_settled_games = len({o.game_id for o in prospective})
    report.discovery_game_ids = tuple(sorted({o.game_id for o in prospective}))

    if not prospective:
        report.refusals[DiscoveryRefusal.NO_SETTLED_OBSERVATIONS.value] = (
            "no settled prospective observations exist"
        )
        report.status = BLOCKED_ON_SAMPLE
        return report

    slices: dict[SliceKey, list[SettledResearchObservation]] = defaultdict(list)
    for obs in prospective:
        slices[SliceKey(obs.family, obs.timing_label, obs.model_version)].append(obs)

    # Sorted by IDENTIFIER, never by outcome. The order in which slices
    # are reported must carry no information about which did well.
    ordered = sorted(slices.items(), key=lambda kv: (kv[0].family, kv[0].timing_label, kv[0].model_version))
    report.slices_examined = len(ordered)

    for key, rows in ordered:
        label = f"{key.family}|{key.timing_label}|{key.model_version}"
        games = {o.game_id for o in rows}

        if minimum_settled_games is None:
            report.refusals[label] = DiscoveryRefusal.NO_DECLARED_MINIMUM.value
            continue
        if len(games) < minimum_settled_games:
            report.refusals[label] = (
                f"{DiscoveryRefusal.BELOW_DECLARED_MINIMUM_GAMES.value}: "
                f"{len(games)} < {minimum_settled_games}"
            )
            continue
        if len(games) < 2:
            report.refusals[label] = DiscoveryRefusal.SINGLE_GAME_CLUSTER.value
            continue

        mean, se, low, high = _cluster_statistics(rows)
        report.findings.append(
            DraftResearchFinding(
                slice_key=key,
                status=DRAFT_RESEARCH_FINDING,
                observations=len(rows),
                distinct_contracts=len({o.market_ticker for o in rows}),
                distinct_games=len(games),
                mean_research_unit_pl=mean,
                cluster_se=se,
                cluster_ci_low=low,
                cluster_ci_high=high,
                mean_signed_gap=statistics.fmean(o.signed_gap for o in rows),
                settle_rate=statistics.fmean(1.0 if o.settled_yes else 0.0 for o in rows),
                slices_examined=len(ordered),
                protocol_document_sha256=document_hash(),
            )
        )

    report.status = DRAFT_RESEARCH_FINDING if report.findings else BLOCKED_ON_SAMPLE
    return report
