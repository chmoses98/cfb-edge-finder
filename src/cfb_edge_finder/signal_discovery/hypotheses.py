"""Pre-registered CFB hypotheses (Stage B, set 1). RESEARCH ONLY.

Every hypothesis is DATA: an id, a football interpretation, a population
rule, a signal side, the outcome it is expected to move and the market it
should logically express. `evaluate.py` interprets these specs; it has no
per-hypothesis code path that could be tuned after a result is seen.

Set 1 was written and committed BEFORE any CFBD line was joined to any
feature row or outcome (protocol section 4). Thresholds below are fixed
round numbers chosen from football logic and the production finding scale
(|edge| 1.0 = MODERATE, 2.0 = STRONG), never from an outcome.

Kinds
-----
SIDE          a population of games and a signal side; tests the side's
              win rate / margin (football), ATS residual and ML residual
              (market), and -110 / provider-odds economics.
TOTAL         a population and a direction (+1 over / -1 under); tests the
              total points vs the season mean (football) and total residual
              vs the closing total (market).
SLOPE_SIDE    a continuous home-perspective feature; slope of home margin
              (football) and of home ATS residual (market) per feature SD,
              optionally with control features (partial slope).
SLOPE_TOTAL   a continuous game feature; slope of total points (football)
              and total residual (market) per SD.
SLOPE_TEAMPTS slope of home points minus derived implied home team total.
FOOTBALL_ONLY a SIDE spec evaluated on a football outcome with no historical
              market (1H margin).
UNAVAILABLE   cannot be tested with the historical source; reported, never
              imputed.
"""

from __future__ import annotations

from typing import Any

HIGH_PACE = 0.5
LOW_PACE = -0.5
SKEPTICAL_SPREAD = 3.0  # control side favoured by < 3 points, or an underdog
CONFIDENT_SPREAD = 10.0  # control side favoured by >= 10 points
WEAK_UNIT = -0.5  # a unit at least half an FBS SD below average
EDGE_ALIGNED = 1.0  # production MODERATE edge scale

SET1: list[dict[str, Any]] = [
    # ------------------------------------------------------------- CONTROL
    {
        "id": "CFB-SIG-001",
        "name": "CONTROL (any tier) -> winner / moneyline",
        "family": "CONTROL",
        "kind": "SIDE",
        "population": {"rule": "control", "strength": None},
        "side": "control",
        "primary_market": "ML",
        "markets": ["ML", "ATS"],
        "interpretation": (
            "A team that owns the opponent-adjusted sustained-efficiency matchup moves the ball more reliably per "
            "snap than its opponent allows; over ~70 snaps that should convert into winning more often."
        ),
    },
    {
        "id": "CFB-SIG-002",
        "name": "MODERATE CONTROL -> closing spread (ATS)",
        "family": "CONTROL",
        "kind": "SIDE",
        "population": {"rule": "control", "strength": "MODERATE"},
        "side": "control",
        "primary_market": "ATS",
        "markets": ["ATS", "ML"],
        "interpretation": (
            "A moderate efficiency edge is a smaller, less visible advantage; if the market anchors on records, "
            "rankings or raw scoring it could under-rate it. Required study (prompt section 11)."
        ),
    },
    {
        "id": "CFB-SIG-003",
        "name": "STRONG CONTROL -> closing spread (ATS)",
        "family": "CONTROL",
        "kind": "SIDE",
        "population": {"rule": "control", "strength": "STRONG"},
        "side": "control",
        "primary_market": "ATS",
        "markets": ["ATS", "ML"],
        "interpretation": (
            "A strong efficiency edge is the most visible kind; it should be well priced. Tests whether the market "
            "fully prices the separation."
        ),
    },
    {
        "id": "CFB-SIG-004",
        "name": "CONTROL x PACE (control-side margin and ATS vs possession environment)",
        "family": "CONTROL_INTERACTION",
        "kind": "SLOPE_SIDE",
        "population": {"rule": "control", "strength": None},
        "side": "control",
        "feature": "possession_environment",
        "feature_orientation": "game",
        "expected_sign": 1,
        "primary_market": "ATS",
        "markets": ["ATS"],
        "interpretation": (
            "A per-play efficiency edge compounds over more snaps: more possessions should mean more separation "
            "for the CONTROL side. Spreads may scale with talent gaps but not with expected snap count."
        ),
    },
    {
        "id": "CFB-SIG-005",
        "name": "CONTROL x DEFENSIVE SUPPRESSION (opponent passing offense outclassed) -> winner",
        "family": "CONTROL_INTERACTION",
        "kind": "SIDE",
        "population": {"rule": "control_opp_pass_suppressed", "strength": None, "threshold": -EDGE_ALIGNED},
        "side": "control",
        "primary_market": "ML",
        "markets": ["ML", "ATS"],
        "interpretation": (
            "Trailing teams come back through the air. When the CONTROL team's pass defense outclasses the "
            "opponent's pass offense (opponent passing direction edge <= -1.0), the weaker side has few fast "
            "comeback paths, so the CONTROL side should convert its edge into wins more reliably."
        ),
    },
    # ------------------------------------------------------------- CLOSENESS
    {
        "id": "CFB-SIG-006",
        "name": "CLOSENESS -> market underdog ATS",
        "family": "CLOSENESS",
        "kind": "SIDE",
        "population": {"rule": "closeness"},
        "side": "market_underdog",
        "primary_market": "ATS",
        "markets": ["ATS", "ML"],
        "interpretation": (
            "When neither offense owns the efficiency matchup (EVEN_MATCHUP resolves), the game should be closer "
            "than an average game; if the spread still reflects a talent/brand gap, the underdog should cover."
        ),
    },
    {
        "id": "CFB-SIG-007",
        "name": "EVEN MATCHUP x LOW PACE -> market underdog ATS",
        "family": "CLOSENESS",
        "kind": "SIDE",
        "population": {"rule": "closeness_low_pace", "threshold": LOW_PACE},
        "side": "market_underdog",
        "primary_market": "ATS",
        "markets": ["ATS"],
        "interpretation": (
            "Fewer possessions shrink the variance of the margin; an even matchup with few snaps favours the points."
        ),
    },
    # ------------------------------------------------------------- PACE / TOTALS
    {
        "id": "CFB-SIG-008",
        "name": "PACE claim HIGH -> over",
        "family": "PACE",
        "kind": "TOTAL",
        "population": {"rule": "pace_claim", "level": "HIGH"},
        "direction": 1,
        "primary_market": "TOTAL",
        "markets": ["TOTAL"],
        "interpretation": "More snaps -> more scoring opportunities, beyond what the scoring baseline already counts.",
    },
    {
        "id": "CFB-SIG-009",
        "name": "PACE claim LOW -> under",
        "family": "PACE",
        "kind": "TOTAL",
        "population": {"rule": "pace_claim", "level": "LOW"},
        "direction": -1,
        "primary_market": "TOTAL",
        "markets": ["TOTAL"],
        "interpretation": "Fewer snaps -> fewer possessions -> fewer points.",
    },
    {
        "id": "CFB-SIG-010",
        "name": "Possession environment (continuous) -> total residual",
        "family": "PACE",
        "kind": "SLOPE_TOTAL",
        "population": {"rule": "all"},
        "feature": "possession_environment",
        "expected_sign": 1,
        "primary_market": "TOTAL",
        "markets": ["TOTAL"],
        "interpretation": "As 008/009 on the continuous production pace measure.",
    },
    {
        "id": "CFB-SIG-011",
        "name": "DEFENSIVE SUPPRESSION claim -> under",
        "family": "DEFENSIVE_SUPPRESSION",
        "kind": "TOTAL",
        "population": {"rule": "defensive_suppression"},
        "direction": -1,
        "primary_market": "TOTAL",
        "markets": ["TOTAL"],
        "interpretation": "Both defenses out-rate the offenses they face; drives stall on both sides.",
    },
    {
        "id": "CFB-SIG-012",
        "name": "SCORING ENVIRONMENT ELEVATED -> over",
        "family": "SCORING_ENVIRONMENT",
        "kind": "TOTAL",
        "population": {"rule": "scoring_env", "level": "ELEVATED"},
        "direction": 1,
        "primary_market": "TOTAL",
        "markets": ["TOTAL"],
        "interpretation": (
            "Both offenses efficient / high-scoring environment beyond the baseline (V2: incremental on holdout)."
        ),
    },
    {
        "id": "CFB-SIG-013",
        "name": "SCORING ENVIRONMENT SUPPRESSED -> under",
        "family": "SCORING_ENVIRONMENT",
        "kind": "TOTAL",
        "population": {"rule": "scoring_env", "level": "SUPPRESSED"},
        "direction": -1,
        "primary_market": "TOTAL",
        "markets": ["TOTAL"],
        "interpretation": "Low-scoring environment (V2: NOT incremental over the baseline on holdout).",
    },
    # ------------------------------------------------------------- UNIT MATCHUPS
    {
        "id": "CFB-SIG-014",
        "name": "Passing matchup net, beyond sustained efficiency -> margin / ATS",
        "family": "PASSING",
        "kind": "SLOPE_SIDE",
        "population": {"rule": "all"},
        "feature": "net.passing",
        "controls": ["net.sustained_efficiency"],
        "expected_sign": 1,
        "primary_market": "ATS",
        "markets": ["ATS"],
        "interpretation": (
            "An aerial mismatch (pass offense vs pass defense) may separate games beyond generic efficiency."
        ),
    },
    {
        "id": "CFB-SIG-015",
        "name": "Rushing matchup net, beyond sustained efficiency -> margin / ATS",
        "family": "RUSHING",
        "kind": "SLOPE_SIDE",
        "population": {"rule": "all"},
        "feature": "net.rushing",
        "controls": ["net.sustained_efficiency"],
        "expected_sign": 1,
        "primary_market": "ATS",
        "markets": ["ATS"],
        "interpretation": "A ground mismatch controls clock and down-and-distance beyond generic efficiency.",
    },
    {
        "id": "CFB-SIG-016",
        "name": "RUSH EDGE x CONTROL (same-side rushing net >= 1) -> ATS",
        "family": "CONTROL_INTERACTION",
        "kind": "SIDE",
        "population": {"rule": "control_rush_aligned", "threshold": EDGE_ALIGNED},
        "side": "control",
        "primary_market": "ATS",
        "markets": ["ATS", "ML"],
        "interpretation": (
            "A team that owns efficiency AND the run game can shorten the game while ahead and finish drives."
        ),
    },
    {
        "id": "CFB-SIG-017",
        "name": "DISRUPTION x WEAK PROTECTION -> disruption side ATS",
        "family": "DISRUPTION",
        "kind": "SIDE",
        "population": {"rule": "disruption_weak_protection", "threshold": WEAK_UNIT},
        "side": "disruption",
        "primary_market": "ATS",
        "markets": ["ATS", "ML"],
        "interpretation": (
            "A repeatable sack/TFL edge matters most against an offense that already allows sacks (opponent offense "
            "sack-rate quality <= -0.5 SD): drive-killing negative plays."
        ),
    },
    {
        "id": "CFB-SIG-018",
        "name": "EXPLOSIVE advantage",
        "family": "EXPLOSIVENESS",
        "kind": "UNAVAILABLE",
        "reason": (
            "CFBD 2014-2025 caches carry no explosive-play counts (production explosive metrics are None on every "
            "historical row; net.explosiveness is null). Only the 2026 ESPN log has them (weeks 1-6). Not tested; "
            "not imputed from CFBD PPA 'explosiveness', which is a different quantity."
        ),
        "primary_market": None,
        "markets": [],
        "interpretation": "Explosive plays create points without sustained drives.",
    },
    {
        "id": "CFB-SIG-019",
        "name": "FINISHING edge without efficiency edge -> ATS",
        "family": "FINISHING",
        "kind": "SIDE",
        "population": {"rule": "finishing_without_efficiency"},
        "side": "finishing",
        "primary_market": "ATS",
        "markets": ["ATS", "ML"],
        "interpretation": (
            "A team that converts scoring opportunities better but does not own efficiency: finishing is noisier "
            "and partly luck, so the market may over-weight points-based reputations (expected sign NEGATIVE for the "
            "finishing side vs the spread is equally plausible; test is two-sided, reported direction +)."
        ),
    },
    # ------------------------------------------------------------- CORE CONTINUOUS / DISAGREEMENT
    {
        "id": "CFB-SIG-020",
        "name": "Sustained-efficiency net (continuous) -> margin / ATS",
        "family": "SUSTAINED_EFFICIENCY",
        "kind": "SLOPE_SIDE",
        "population": {"rule": "all"},
        "feature": "net.sustained_efficiency",
        "expected_sign": 1,
        "primary_market": "ATS",
        "markets": ["ATS"],
        "interpretation": (
            "The CONTROL quantity itself, continuous: does it carry margin information the closing spread lacks?"
        ),
    },
    {
        "id": "CFB-SIG-021",
        "name": "Scoring-baseline margin vs closing spread disagreement -> ATS",
        "family": "MARKET_DISAGREEMENT",
        "kind": "SLOPE_SIDE",
        "population": {"rule": "all"},
        "feature": "gap.margin",
        "expected_sign": 1,
        "primary_market": "ATS",
        "markets": ["ATS"],
        "interpretation": (
            "gap.margin = opponent-adjusted baseline home margin + closing home spread (positive = football likes "
            "home more than the market). If the football baseline holds information, the residual follows the gap."
        ),
    },
    {
        "id": "CFB-SIG-022",
        "name": "Scoring-baseline total vs closing total disagreement -> total residual",
        "family": "MARKET_DISAGREEMENT",
        "kind": "SLOPE_TOTAL",
        "population": {"rule": "all"},
        "feature": "gap.total",
        "expected_sign": 1,
        "primary_market": "TOTAL",
        "markets": ["TOTAL"],
        "interpretation": "gap.total = baseline total - closing total.",
    },
    {
        "id": "CFB-SIG-023",
        "name": "STRONG CONTROL, market skeptical (control side favoured < 3 or underdog)",
        "family": "MARKET_DISAGREEMENT",
        "kind": "SIDE",
        "population": {"rule": "control_market_skeptical", "strength": "STRONG", "threshold": SKEPTICAL_SPREAD},
        "side": "control",
        "primary_market": "ATS",
        "markets": ["ATS", "ML"],
        "interpretation": "Football strong / market skeptical: who is right?",
    },
    {
        "id": "CFB-SIG-024",
        "name": "MODERATE CONTROL, market skeptical (control side favoured < 3 or underdog)",
        "family": "MARKET_DISAGREEMENT",
        "kind": "SIDE",
        "population": {"rule": "control_market_skeptical", "strength": "MODERATE", "threshold": SKEPTICAL_SPREAD},
        "side": "control",
        "primary_market": "ATS",
        "markets": ["ATS", "ML"],
        "interpretation": "Football moderate / market skeptical: who is right?",
    },
    {
        "id": "CFB-SIG-025",
        "name": "Home offense scoring baseline vs DERIVED implied home team total",
        "family": "TEAM_TOTAL",
        "kind": "SLOPE_TEAMPTS",
        "population": {"rule": "all"},
        "feature": "gap.home_team_total",
        "expected_sign": 1,
        "primary_market": "TEAM_TOTAL_DERIVED",
        "markets": ["TEAM_TOTAL_DERIVED"],
        "interpretation": (
            "No historical team-total line exists in CFBD; implied home team total = (total - home spread) / 2. "
            "gap = baseline home points - implied. DERIVED, not a traded line."
        ),
    },
    {
        "id": "CFB-SIG-026",
        "name": "CONTROL -> first-half margin (football only)",
        "family": "CONTROL",
        "kind": "FOOTBALL_ONLY",
        "population": {"rule": "control", "strength": None},
        "side": "control",
        "football_outcome": "margin_1h",
        "primary_market": None,
        "markets": [],
        "interpretation": (
            "Does the efficiency edge already show by halftime? No historical 1H line exists in the cache."
        ),
    },
    {
        "id": "CFB-SIG-027",
        "name": "TREND / FORM (improving or declining opponent-adjusted efficiency)",
        "family": "TREND",
        "kind": "UNAVAILABLE",
        "reason": (
            "Not built in Wave 1. A leakage-free form signal needs a recency-weighted opponent adjustment, which "
            "would be a materially new adjustment method (prompt section 7 forbids introducing one without separate "
            "validation). Arbitrary last-3-game raw trends are excluded by the prompt. QB change is also unavailable "
            "(CFBD carries no primary passer)."
        ),
        "primary_market": None,
        "markets": [],
        "interpretation": "Form and QB changes should move a team's true level mid-season.",
    },
    # ------------------------------------------------------------- BASELINES (section 41)
    {
        "id": "CFB-BASE-001",
        "name": "BASELINE: home team ATS",
        "family": "BASELINE",
        "kind": "SIDE",
        "population": {"rule": "not_neutral"},
        "side": "home",
        "primary_market": "ATS",
        "markets": ["ATS", "ML"],
        "interpretation": "Is home field priced? (reference, not a signal)",
    },
    {
        "id": "CFB-BASE-002",
        "name": "BASELINE: market favourite ATS",
        "family": "BASELINE",
        "kind": "SIDE",
        "population": {"rule": "all"},
        "side": "market_favorite",
        "primary_market": "ATS",
        "markets": ["ATS", "ML"],
        "interpretation": "Favourite-longshot reference.",
    },
    {
        "id": "CFB-BASE-003",
        "name": "BASELINE: RAW yards/play net (unadjusted) -> margin / ATS",
        "family": "BASELINE",
        "kind": "SLOPE_SIDE",
        "population": {"rule": "all"},
        "feature": "raw.ypp_net",
        "expected_sign": 1,
        "primary_market": "ATS",
        "markets": ["ATS"],
        "interpretation": (
            "Raw efficiency comparator for CFB-SIG-020 (raw stats cannot establish a signal; reference only)."
        ),
    },
]

#: Pre-registered walk-forward models (section 28). Fixed feature lists; no selection.
WALK_FORWARD = {
    "WF-ATS": {
        "target": "home_ats_resid",
        "features": [
            "net.sustained_efficiency",
            "net.passing",
            "net.rushing",
            "net.disruption",
            "net.finishing",
            "net.scoring",
            "gap.margin",
        ],
        "first_test_season": 2017,
        "pick_threshold_points": 2.0,
    },
    "WF-TOTAL": {
        "target": "total_resid",
        "features": ["gap.total", "possession_environment", "tempo_environment", "def_quality_sum", "off_quality_sum"],
        "first_test_season": 2017,
        "pick_threshold_points": 2.0,
    },
    "WF-ML": {
        "target": "home_win",
        "market_feature": "logit.ml_home_novig",
        "features": ["net.sustained_efficiency", "baseline.home_margin"],
        "first_test_season": 2022,
        "train_first_season": 2021,
    },
}

#: Pre-registered status rule (protocol section 7). Applied mechanically by evaluate.py.
STATUS_RULE = {
    "q_football": 0.05,
    "q_market": 0.05,
    "q_market_watch": 0.10,
    "min_n_market": 100,
    "min_seasons_same_sign_share": 0.6,
    "efficient_ci_halfwidth_cover": 0.05,
    "blocks": {"A": [2014, 2015, 2016, 2017, 2018, 2019], "B": [2020, 2021, 2022, 2023, 2024, 2025]},
}


# --------------------------------------------------------------------------------------------------------------------
# SET 2 -- written AFTER the Stage A screen (block A = 2014-2019 lines + outcomes only) and BEFORE any block-B
# (2020-2025) line was joined to these features. Decisive evaluation: block B only. Thresholds come from block-A
# feature distributions (never from block-A outcomes) or are the sign split.
# --------------------------------------------------------------------------------------------------------------------

SET2: list[dict[str, Any]] = [
    {
        "id": "CFB-DSC-001",
        "name": "Rush-defense quality edge -> home ATS residual",
        "family": "RUSHING",
        "kind": "SLOPE_SIDE",
        "population": {"rule": "all"},
        "feature": "defdiff.rush_success_rate",
        "expected_sign": 1,
        "primary_market": "ATS",
        "markets": ["ATS"],
        "screen_evidence": "block A: +0.83 pts/SD, z +3.45, whole-screen q 0.002, 6/6 seasons",
        "interpretation": (
            "The team whose run defense is better (opponent-adjusted rush success allowed) may be under-rated: run "
            "stopping is less visible than points/passing yards, yet it forces long down-and-distance."
        ),
    },
    {
        "id": "CFB-DSC-002",
        "name": "Production rushing net (unconditional) -> home ATS residual",
        "family": "RUSHING",
        "kind": "SLOPE_SIDE",
        "population": {"rule": "all"},
        "feature": "net.rushing",
        "expected_sign": 1,
        "primary_market": "ATS",
        "markets": ["ATS"],
        "screen_evidence": "block A: +0.67 pts/SD, z +2.75, q 0.019, 6/6 seasons",
        "interpretation": "Same theme on the production rushing dimension (offense rush efficiency vs run defense).",
    },
    {
        "id": "CFB-DSC-003",
        "name": "Pass-rate tendency difference -> home ATS residual (negative)",
        "family": "STYLE",
        "kind": "SLOPE_SIDE",
        "population": {"rule": "all"},
        "feature": "pass_rate_diff_raw",
        "expected_sign": -1,
        "primary_market": "ATS",
        "markets": ["ATS"],
        "screen_evidence": "block A: -0.72 pts/SD, z -2.93, q 0.011, 5/6 seasons",
        "interpretation": (
            "The more pass-heavy side may be over-rated (passing production is the most visible stat). Pass rate is a "
            "descriptive TENDENCY the production adjustment deliberately leaves raw (not a performance statistic)."
        ),
    },
    {
        "id": "CFB-DSC-004",
        "name": "Within STRONG CONTROL: control-side scoring-offense advantage -> control-side ATS (negative)",
        "family": "CONTROL_INTERACTION",
        "kind": "SLOPE_SIDE",
        "population": {"rule": "control", "strength": "STRONG"},
        "side": "control",
        "feature": "ctrl.offdiff_ppg",
        "expected_sign": -1,
        "primary_market": "ATS",
        "markets": ["ATS"],
        "screen_evidence": "block A within STRONG: -1.31 pts/SD, z -3.03, q 0.008, 6/6 seasons",
        "interpretation": (
            "VISIBILITY: when a STRONG efficiency edge is also visible as a big adjusted points-per-game advantage of "
            "the control team's offense, the market prices it fully or over-prices it; when the edge is in efficiency "
            "but not yet in scoring, it may be under-priced."
        ),
    },
    {
        "id": "CFB-DSC-005",
        "name": "STRONG CONTROL with low scoring visibility (control offense ppg-quality edge <= 1.5 SD) -> ATS",
        "family": "CONTROL_INTERACTION",
        "kind": "SIDE",
        "population": {
            "rule": "feature_range",
            "feature": "ctrl.offdiff_ppg",
            "hi": 1.5,
            "and": [{"rule": "control", "strength": "STRONG"}],
        },
        "side": "control",
        "primary_market": "ATS",
        "markets": ["ATS", "ML"],
        "screen_evidence": "threshold 1.5 = rounded block-A median (1.645) of the oriented feature within STRONG",
        "interpretation": "Binary form of CFB-DSC-004: the less-visible half of STRONG CONTROL.",
    },
    {
        "id": "CFB-DSC-006",
        "name": "Efficiency net minus scoring net -> home ATS residual",
        "family": "VISIBILITY",
        "kind": "SLOPE_SIDE",
        "population": {"rule": "all"},
        "feature": "eff_minus_scoring_net",
        "expected_sign": 1,
        "primary_market": "ATS",
        "markets": ["ATS"],
        "screen_evidence": "not screened directly; generalises the CFB-DSC-004 visibility idea to every game",
        "interpretation": (
            "Efficiency that has not (yet) shown up in points may be under-priced by a points-anchored market."
        ),
    },
    {
        "id": "CFB-DSC-007",
        "name": "Combined pass-rate tendency -> total residual (under)",
        "family": "STYLE",
        "kind": "SLOPE_TOTAL",
        "population": {"rule": "all"},
        "feature": "pass_rate_sum_raw",
        "expected_sign": -1,
        "primary_market": "TOTAL",
        "markets": ["TOTAL"],
        "screen_evidence": "block A: -0.56 pts/SD, z -2.18, q 0.082, 6/6 seasons",
        "interpretation": (
            "Totals may over-weight pass-heavy matchups (incompletions stop the clock but also end drives)."
        ),
    },
    {
        "id": "CFB-DSC-008",
        "name": "Within MODERATE CONTROL: success-rate matchup net -> control-side ATS",
        "family": "CONTROL_INTERACTION",
        "kind": "SLOPE_SIDE",
        "population": {"rule": "control", "strength": "MODERATE"},
        "side": "control",
        "feature": "ctrl.netq_success_rate",
        "expected_sign": 1,
        "primary_market": "ATS",
        "markets": ["ATS"],
        "screen_evidence": "block A within MODERATE: +1.22 pts/SD, z +2.44, q 0.043, 6/6 seasons",
        "interpretation": (
            "Among MODERATE edges, the ones carried by success rate (the most stable efficiency stat) may be"
            " under-priced."
        ),
    },
]
