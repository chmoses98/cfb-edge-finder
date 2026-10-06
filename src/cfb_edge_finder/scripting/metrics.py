"""The metric registry: every number the engine may publish, defined once. PURE.

Every metric is written from the OFFENSE's point of view: what a team's
offense did in one game, as numerator / denominator counts. The DEFENSIVE
version of the same metric is what opposing offenses did against that
defense, which `adjust.py` estimates from the same regression. So one
definition yields two published unit metrics:

    off.<id>   "this offense, against an average defense"
    def.<id>   "this defense, against an average offense"   (value ALLOWED)

`offense_higher_is_better` gives the direction for the offense; a defense's
direction is always the opposite (allowing more yards per play is worse).

*** TIERS ***
CORE metrics need only box-score counts. ENHANCED metrics need a play log.
A row lacking the inputs for a metric contributes nothing to it -- it is
never imputed and never read as zero.

*** ADJUSTMENT ***
`adjust=False` marks a descriptive tendency (pass rate, explosive-yard
share) where "what would this team do against an average opponent" is not a
meaningful question for V1. Such metrics publish `adjusted: null` with the
reason, and are never presented as adjusted.

*** REGRESSION-PRONE ***
Turnovers swing on fumble bounces and tipped passes. `regression_prone`
metrics are published (they happened) but `findings.py` never lets one
carry a script on its own: repeatable disruption (sacks, tackles for loss,
passes defended) is kept separate from raw turnover margin.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cfb_edge_finder.scripting.gamelog import TeamGame

CORE = "CORE"
ENHANCED = "ENHANCED"

DIMENSIONS = (
    "scoring",
    "sustained_efficiency",
    "rushing",
    "passing",
    "explosiveness",
    "disruption",
    "finishing",
    "pace",
    "volatility",
)

Observation = tuple[float, float]
Extractor = Callable[[TeamGame, TeamGame | None], Observation | None]


@dataclass(frozen=True)
class MetricDef:
    metric_id: str
    name: str
    dimension: str
    tier: str
    unit: str
    offense_higher_is_better: bool
    extract: Extractor
    adjust: bool = True
    secondary: bool = False
    regression_prone: bool = False
    description: str = ""
    #: Weight of one game in the fit is its denominator relative to the mean
    #: denominator (plays, attempts, drives). Per-game counts weight 1.
    weight_by_denominator: bool = True

    def describe(self) -> dict[str, object]:
        return {
            "metric_id": self.metric_id,
            "name": self.name,
            "dimension": self.dimension,
            "tier": self.tier,
            "unit": self.unit,
            "offense_higher_is_better": self.offense_higher_is_better,
            "adjusted": self.adjust,
            "secondary_evidence": self.secondary,
            "regression_prone": self.regression_prone,
            "description": self.description,
        }


def _box(row: TeamGame | None, key: str) -> float | None:
    if row is None:
        return None
    return row.box.get(key)


def _pbp(row: TeamGame | None, key: str) -> float | None:
    if row is None or row.pbp is None:
        return None
    return row.pbp.get(key)


def _ratio(num: float | None, den: float | None, *, min_den: float = 1.0) -> Observation | None:
    if num is None or den is None or den < min_den:
        return None
    return float(num), float(den)


def _per_game(value: float | None) -> Observation | None:
    if value is None:
        return None
    return float(value), 1.0


def _plays(row: TeamGame) -> float | None:
    plays = _box(row, "plays")
    if plays is not None:
        return plays
    rush, passes = _box(row, "rush_att"), _box(row, "pass_att")
    if rush is None or passes is None:
        return None
    return rush + passes


def _dropbacks_box(row: TeamGame, opp: TeamGame | None) -> float | None:
    passes = _box(row, "pass_att")
    sacks = _box(opp, "def_sacks")
    if passes is None or sacks is None:
        return None
    return passes + sacks


def _havoc_allowed(row: TeamGame, opp: TeamGame | None) -> Observation | None:
    tfl, pd = _box(opp, "def_tfl"), _box(opp, "def_passes_defended")
    plays = _plays(row)
    if tfl is None or pd is None or plays is None:
        return None
    return _ratio(tfl + pd, plays, min_den=10)


def _neutral_pass_rate(row: TeamGame, opp: TeamGame | None) -> Observation | None:
    return _ratio(_pbp(row, "neutral_pass_plays"), _pbp(row, "neutral_plays"), min_den=10)


def _explosive_rate(row: TeamGame, opp: TeamGame | None) -> Observation | None:
    rush, passes = _pbp(row, "explosive_rush"), _pbp(row, "explosive_pass")
    if rush is None or passes is None:
        return None
    return _ratio(rush + passes, _pbp(row, "scrim_plays"), min_den=10)


METRICS: tuple[MetricDef, ...] = (
    # ------------------------------------------------------------ scoring
    MetricDef(
        "points_per_game",
        "Points per game",
        "scoring",
        CORE,
        "points",
        True,
        lambda r, o: _per_game(r.points_for),
        weight_by_denominator=False,
        description="Points scored, all phases (includes defensive and special-teams scores).",
    ),
    # ----------------------------------------------- sustained efficiency
    MetricDef(
        "yards_per_play",
        "Yards per play",
        "sustained_efficiency",
        CORE,
        "yards",
        True,
        lambda r, o: _ratio(_box(r, "total_yards"), _plays(r), min_den=10),
        description="Total offensive yards / (rush attempts + pass attempts). NCAA box: sacks count as rushes.",
    ),
    MetricDef(
        "first_down_rate",
        "First downs per play",
        "sustained_efficiency",
        CORE,
        "rate",
        True,
        lambda r, o: _ratio(_box(r, "first_downs"), _plays(r), min_den=10),
        description="First downs (all methods) / offensive plays. A box-score proxy for staying on schedule.",
    ),
    MetricDef(
        "third_down_rate",
        "Third-down conversion rate",
        "sustained_efficiency",
        CORE,
        "rate",
        True,
        lambda r, o: _ratio(_box(r, "third_conv"), _box(r, "third_att"), min_den=3),
        secondary=True,
        description="SECONDARY evidence: small samples, heavily dependent on distance-to-go.",
    ),
    MetricDef(
        "success_rate",
        "Success rate",
        "sustained_efficiency",
        ENHANCED,
        "rate",
        True,
        lambda r, o: _ratio(_pbp(r, "success_plays"), _pbp(r, "scrim_plays"), min_den=10),
        description=(
            "Share of non-garbage scrimmage plays gaining 50%/70%/100% of the distance on 1st/2nd/3rd-4th down."
        ),
    ),
    MetricDef(
        "early_down_success_rate",
        "Early-down success rate",
        "sustained_efficiency",
        ENHANCED,
        "rate",
        True,
        lambda r, o: _ratio(_pbp(r, "early_success"), _pbp(r, "early_plays"), min_den=10),
        description="Success rate on 1st and 2nd down only.",
    ),
    # ------------------------------------------------------------ rushing
    MetricDef(
        "yards_per_rush",
        "Yards per rush (box)",
        "rushing",
        CORE,
        "yards",
        True,
        lambda r, o: _ratio(_box(r, "rush_yards"), _box(r, "rush_att"), min_den=5),
        description="NCAA box-score rushing: sacks are counted as rushes with negative yardage.",
    ),
    MetricDef(
        "rush_success_rate",
        "Rush success rate",
        "rushing",
        ENHANCED,
        "rate",
        True,
        lambda r, o: _ratio(_pbp(r, "rush_success"), _pbp(r, "rush_plays"), min_den=5),
        description="Success rate on designed runs (sacks excluded).",
    ),
    MetricDef(
        "yards_per_designed_rush",
        "Yards per designed rush",
        "rushing",
        ENHANCED,
        "yards",
        True,
        lambda r, o: _ratio(_pbp(r, "rush_yards"), _pbp(r, "rush_plays"), min_den=5),
        description="Rushing yards per designed run, sacks removed (play log).",
    ),
    # ------------------------------------------------------------ passing
    MetricDef(
        "yards_per_pass_attempt",
        "Yards per pass attempt",
        "passing",
        CORE,
        "yards",
        True,
        lambda r, o: _ratio(_box(r, "pass_yards"), _box(r, "pass_att"), min_den=5),
        description="Passing yards / pass attempts (box). Not sack-adjusted.",
    ),
    MetricDef(
        "net_yards_per_dropback",
        "Net yards per dropback",
        "passing",
        ENHANCED,
        "yards",
        True,
        lambda r, o: _ratio(_pbp(r, "pass_yards"), _pbp(r, "pass_plays"), min_den=5),
        description="Sack-adjusted: passing yards minus sack yardage, per dropback (attempts + sacks).",
    ),
    MetricDef(
        "pass_success_rate",
        "Pass success rate",
        "passing",
        ENHANCED,
        "rate",
        True,
        lambda r, o: _ratio(_pbp(r, "pass_success"), _pbp(r, "pass_plays"), min_den=5),
        description="Success rate on dropbacks (sacks included as failures).",
    ),
    MetricDef(
        "interception_rate",
        "Interception rate",
        "passing",
        CORE,
        "rate",
        False,
        lambda r, o: _ratio(_box(r, "ints_thrown"), _box(r, "pass_att"), min_den=5),
        regression_prone=True,
        description="Interceptions thrown / pass attempts. Regression-prone; never carries a script alone.",
    ),
    # -------------------------------------------------------- explosiveness
    MetricDef(
        "explosive_play_rate",
        "Explosive-play rate",
        "explosiveness",
        ENHANCED,
        "rate",
        True,
        _explosive_rate,
        description="Share of scrimmage plays gaining 12+ (rush) or 16+ (pass) yards.",
    ),
    MetricDef(
        "explosive_rush_rate",
        "Explosive-rush rate",
        "explosiveness",
        ENHANCED,
        "rate",
        True,
        lambda r, o: _ratio(_pbp(r, "explosive_rush"), _pbp(r, "rush_plays"), min_den=5),
        description="Designed runs gaining 12+ yards.",
    ),
    MetricDef(
        "explosive_pass_rate",
        "Explosive-pass rate",
        "explosiveness",
        ENHANCED,
        "rate",
        True,
        lambda r, o: _ratio(_pbp(r, "explosive_pass"), _pbp(r, "pass_plays"), min_den=5),
        description="Dropbacks gaining 16+ yards.",
    ),
    # ----------------------------------------------------------- disruption
    MetricDef(
        "sack_rate_allowed",
        "Sack rate",
        "disruption",
        CORE,
        "rate",
        False,
        lambda r, o: _ratio(_box(o, "def_sacks"), _dropbacks_box(r, o), min_den=5),
        description="Sacks taken / (pass attempts + sacks), from both teams' box scores. Repeatable disruption.",
    ),
    MetricDef(
        "havoc_allowed",
        "Havoc rate (TFL + PD)",
        "disruption",
        CORE,
        "rate",
        False,
        _havoc_allowed,
        description=(
            "(Tackles for loss + passes defended by the opposing defense) / offensive plays. A havoc PROXY: "
            "turnovers are deliberately excluded so repeatable disruption stays separate from turnover luck."
        ),
    ),
    MetricDef(
        "turnovers_per_game",
        "Turnovers per game",
        "disruption",
        CORE,
        "count",
        False,
        lambda r, o: _per_game(_box(r, "turnovers")),
        weight_by_denominator=False,
        regression_prone=True,
        description="Giveaways per game. Regression-prone; reported, never a script's sole cause.",
    ),
    # ------------------------------------------------------------ finishing
    MetricDef(
        "points_per_opportunity",
        "Points per scoring opportunity",
        "finishing",
        ENHANCED,
        "points",
        True,
        lambda r, o: _ratio(_pbp(r, "opp_points"), _pbp(r, "scoring_opps"), min_den=2),
        description="Offensive points per drive that reached the opponent's 40 (TD=7, FG=3).",
    ),
    MetricDef(
        "points_per_drive",
        "Points per drive",
        "finishing",
        ENHANCED,
        "points",
        True,
        lambda r, o: _ratio(_pbp(r, "drive_points"), _pbp(r, "drives"), min_den=4),
        description="Offensive points (TD=7, FG=3) per offensive drive.",
    ),
    # ----------------------------------------------------------------- pace
    MetricDef(
        "plays_per_game",
        "Plays per game",
        "pace",
        CORE,
        "plays",
        True,
        lambda r, o: _per_game(_plays(r)),
        weight_by_denominator=False,
        description="Offensive plays per game. Direction is descriptive (more plays = more possessions).",
    ),
    MetricDef(
        "seconds_per_play",
        "Possession seconds per play",
        "pace",
        CORE,
        "seconds",
        False,
        lambda r, o: _ratio(_box(r, "possession_seconds"), _plays(r), min_den=10),
        description="Time of possession / offensive plays. Lower = faster. Direction is descriptive only.",
    ),
    MetricDef(
        "pass_rate",
        "Pass rate (all situations)",
        "pace",
        CORE,
        "rate",
        True,
        lambda r, o: _ratio(_box(r, "pass_att"), _plays(r), min_den=10),
        adjust=False,
        description="Pass attempts / plays. A tendency; game state drives it, so it is not opponent-adjusted.",
    ),
    MetricDef(
        "neutral_pass_rate",
        "Neutral-situation pass rate",
        "pace",
        ENHANCED,
        "rate",
        True,
        _neutral_pass_rate,
        adjust=False,
        description="Dropbacks / plays in quarters 1-3 with the score within 14. A tendency, not adjusted.",
    ),
    # ----------------------------------------------------------- volatility
    MetricDef(
        "explosive_yard_share",
        "Share of yards from explosive plays",
        "volatility",
        ENHANCED,
        "rate",
        True,
        lambda r, o: _ratio(_pbp(r, "explosive_yards"), _pbp(r, "scrim_yards"), min_den=50),
        adjust=False,
        description="How much of the offense's yardage arrived in explosive chunks. Dependence, not quality.",
    ),
)

BY_ID: dict[str, MetricDef] = {m.metric_id: m for m in METRICS}


def metric(metric_id: str) -> MetricDef:
    return BY_ID[metric_id]


def core_metric_ids() -> tuple[str, ...]:
    return tuple(m.metric_id for m in METRICS if m.tier == CORE)


def enhanced_metric_ids() -> tuple[str, ...]:
    return tuple(m.metric_id for m in METRICS if m.tier == ENHANCED)
