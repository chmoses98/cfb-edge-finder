"""Milestone C.2: rich post-hoc segmentation of a completed walk-forward
backtest (mission section 3/8's diagnosis requirement).

*** WHY THIS IS SAFE, EVEN THOUGH IT'S NOT IN backtest.py ITSELF ***
Every function here is a PURE function of a list of already-computed
`backtest.GameOutcome` objects (each one already the output of a leakage-
checked walk-forward prediction -- see backtest.py) plus a static team
registry lookup (`teams.registry.get_team`, a fixed pregame-known fact,
never derived from in-season results). Nothing here re-fits a model,
re-touches CFBD data, or could introduce a NEW leakage path -- it only
classifies outcomes that already exist, for reporting.

*** WHY FAVORITE/UNDERDOG IS THE MODEL'S OWN CALL, NOT THE MARKET'S ***
`classify_favorite` uses `model_margin_mean` (the model's own raw
projection), never a market/Kalshi line -- there is no market-line input
anywhere in this codebase (mission's explicit no-market-leakage
instruction). This also means the classification is generated in a
walk-forward-consistent way: at prediction time, the model already "knew"
its own projected margin, so slicing metrics by that projection is not
lookahead, unlike slicing by the ACTUAL outcome (which is used here only
as an evaluation axis, alongside the projection, never as an input back
into projection).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cfb_edge_finder.modeling.backtest import BacktestMetrics, GameOutcome, compute_metrics
from cfb_edge_finder.teams.registry import get_team

LARGE_FAVORITE_THRESHOLD = 14.0
PICKEM_THRESHOLD = 3.0
"""Round, documented thresholds on |model_margin_mean| -- not fit, just a
reasonable split into "clear favorite" / "moderate" / "toss-up-like" for
diagnostic bucketing. Not used anywhere in the model itself."""


def classify_favorite(o: GameOutcome) -> str:
    if abs(o.model_margin_mean) < 1e-9:
        return "pickem_exact"
    return "home_favorite" if o.model_margin_mean > 0 else "home_underdog"


def classify_margin_magnitude(o: GameOutcome) -> str:
    m = abs(o.model_margin_mean)
    if m >= LARGE_FAVORITE_THRESHOLD:
        return "large_favorite"
    if m <= PICKEM_THRESHOLD:
        return "pickem_like"
    return "moderate_favorite"


def is_conference_game(o: GameOutcome) -> bool | None:
    """None when either side isn't in the static FBS registry (an FCS or
    unresolved opponent) -- "conference game" is only a meaningful concept
    for two FBS teams."""
    home = get_team(o.home_id)
    away = get_team(o.away_id)
    if home is None or away is None or home.conference is None or away.conference is None:
        return None
    return home.conference == away.conference


def _bin_label(value: float, edges: list[float]) -> str:
    for lo, hi in zip(edges, edges[1:], strict=False):
        if lo <= value < hi:
            return f"[{lo:g},{hi:g})"
    return f"[{edges[-1]:g},inf)" if value >= edges[-1] else f"(-inf,{edges[0]:g})"


PROJECTED_MARGIN_EDGES = [-100, -21, -14, -7, -3, 0, 3, 7, 14, 21, 100]
ACTUAL_MARGIN_EDGES = PROJECTED_MARGIN_EDGES
PROJECTED_TOTAL_EDGES = [0, 35, 42, 49, 56, 63, 70, 200]


def projected_margin_bin(o: GameOutcome) -> str:
    return _bin_label(o.model_margin_mean, PROJECTED_MARGIN_EDGES)


def actual_margin_bin(o: GameOutcome) -> str:
    return _bin_label(o.actual_home_points - o.actual_away_points, ACTUAL_MARGIN_EDGES)


def projected_total_bin(o: GameOutcome) -> str:
    return _bin_label(o.model_total_mean, PROJECTED_TOTAL_EDGES)


@dataclass(frozen=True)
class SegmentReport:
    label: str
    n: int
    metrics: BacktestMetrics


def segment_report(outcomes: list[GameOutcome], label: str, predicate) -> SegmentReport | None:
    subset = [o for o in outcomes if predicate(o)]
    if not subset:
        return None
    metrics = compute_metrics(subset, prob_attr="calibrated_prob_home_win")
    return SegmentReport(label=label, n=len(subset), metrics=metrics)


def full_diagnostic_report(outcomes: list[GameOutcome]) -> list[SegmentReport]:
    """Every segmentation mission section 3/8 asked for, computed from one
    already-completed backtest run. Returns a flat list (some entries may
    be absent -- e.g. a season with zero home-underdog games -- rather
    than crash on an empty subset)."""
    reports: list[SegmentReport] = []

    def add(label: str, predicate) -> None:
        r = segment_report(outcomes, label, predicate)
        if r is not None:
            reports.append(r)

    for season in sorted({o.season for o in outcomes}):
        add(f"season={season}", lambda o, s=season: o.season == s)

    add("weeks 2-3", lambda o: 2 <= o.week <= 3)
    add("weeks 4+", lambda o: o.week >= 4)

    add("neutral site", lambda o: o.is_neutral_site)
    add("home/away (non-neutral)", lambda o: not o.is_neutral_site)

    add("FBS-vs-FBS", lambda o: o.is_fbs_vs_fbs)
    add("FBS-vs-FCS", lambda o: not o.is_fbs_vs_fbs)

    add("conference game", lambda o: is_conference_game(o) is True)
    add("non-conference game", lambda o: is_conference_game(o) is False)

    add("home favorite", lambda o: classify_favorite(o) == "home_favorite")
    add("home underdog", lambda o: classify_favorite(o) == "home_underdog")

    add("large projected favorite (|margin|>=14)", lambda o: classify_margin_magnitude(o) == "large_favorite")
    add("moderate projected favorite (3<|margin|<14)", lambda o: classify_margin_magnitude(o) == "moderate_favorite")
    add("pick'em-like (|margin|<=3)", lambda o: classify_margin_magnitude(o) == "pickem_like")

    for edge_lo, edge_hi in zip(PROJECTED_MARGIN_EDGES, PROJECTED_MARGIN_EDGES[1:], strict=False):
        add(
            f"projected margin in [{edge_lo:g},{edge_hi:g})",
            lambda o, lo=edge_lo, hi=edge_hi: lo <= o.model_margin_mean < hi,
        )

    for edge_lo, edge_hi in zip(PROJECTED_TOTAL_EDGES, PROJECTED_TOTAL_EDGES[1:], strict=False):
        add(
            f"projected total in [{edge_lo:g},{edge_hi:g})",
            lambda o, lo=edge_lo, hi=edge_hi: lo <= o.model_total_mean < hi,
        )

    return reports


def print_diagnostic_report(outcomes: list[GameOutcome]) -> None:
    reports = full_diagnostic_report(outcomes)
    cols = f"{'Segment':<45} {'n':>6} {'WinLL':>8} {'MgnMAE':>8} {'MgnRMSE':>8} {'MgnBias':>9} {'TotMAE':>8}"
    print(f"\n{cols} {'TotRMSE':>8}")
    for r in reports:
        m = r.metrics
        print(
            f"{r.label:<45} {r.n:>6} {m.winner_log_loss:>8.4f} {m.margin_mae:>8.2f} {m.margin_rmse:>8.2f} "
            f"{m.margin_bias:>+9.2f} {m.total_mae:>8.2f} {m.total_rmse:>8.2f}"
        )


def source_of_margin_bias_summary(outcomes: list[GameOutcome]) -> dict:
    """A compact, structured summary specifically aimed at mission section
    3's "determine whether the bias is mostly X" question -- computed
    from real segment metrics, not asserted from theory. Returns raw
    numbers a caller (or this module's docstring/report) can reason about;
    does not itself assert a root cause."""
    all_metrics = compute_metrics(outcomes, prob_attr="calibrated_prob_home_win")
    fbs_fbs = [o for o in outcomes if o.is_fbs_vs_fbs]
    fbs_fcs = [o for o in outcomes if not o.is_fbs_vs_fbs]
    home_fav = [o for o in outcomes if classify_favorite(o) == "home_favorite"]
    home_dog = [o for o in outcomes if classify_favorite(o) == "home_underdog"]
    large_fav = [o for o in outcomes if classify_margin_magnitude(o) == "large_favorite"]
    pickem = [o for o in outcomes if classify_margin_magnitude(o) == "pickem_like"]
    early = [o for o in outcomes if o.week <= 3]
    later = [o for o in outcomes if o.week > 3]

    def bias(subset):
        if not subset:
            return None
        return float(
            np.mean([o.actual_home_points - o.actual_away_points - o.model_margin_mean for o in subset])
        )

    return {
        "overall_bias": all_metrics.margin_bias,
        "fbs_vs_fbs_bias": bias(fbs_fbs),
        "fbs_vs_fcs_bias": bias(fbs_fcs),
        "home_favorite_bias": bias(home_fav),
        "home_underdog_bias": bias(home_dog),
        "large_favorite_bias": bias(large_fav),
        "pickem_like_bias": bias(pickem),
        "early_season_bias": bias(early),
        "later_season_bias": bias(later),
    }
