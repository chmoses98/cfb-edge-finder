"""Multi-season, strictly-separated key-number calibration of the discrete score model (2026-11-02).

*** WHY THIS EXISTS ***
PR #249 fitted the key-number lifts on 208 games of ONE season and tested on the next 139. The aggregate gap closed
but accuracy did not improve, and the repository warned that a margin-of-3 excess "may simply be this season's
lumpiness". This module re-asks the question with three seasons of final scores that the repository already holds
(``data/scripting/validation/records_{2024,2025,2026}.jsonl.gz``) and with a split that cannot leak:

    TRAIN      season 2024                       every parameter is fitted here and nowhere else
    VALIDATE   season 2025                       candidate SELECTION happens here (one pre-declared metric)
    TEST       2026 kickoffs before 2026-10-17   first look for the selected candidate
    FINAL      2026-10-17 .. 2026-11-01          the block PR #249 reported on. See the disclosure below.

*** DISCLOSURE (the FINAL block is not pristine) ***
PR #249 already evaluated models on the FINAL block, and the author knew the margin-of-3 gap before designing the
candidates here. What IS true: no parameter of any system below was fitted on, or selected with, TEST or FINAL
(the 2026 data in PR #249 were only ever used to fit the *old* v1/v2 lifts, which are refit from 2024 here). So
TEST and FINAL are out-of-sample for this procedure; FINAL is reported as a continuity check and is never the
basis of the verdict. The verdict needs VALIDATE and TEST both.

*** THE ACCEPTANCE RULE (declared here, before any held-out number is computed) ***
A calibrated candidate is ``PROMOTABLE`` iff ALL of the following hold on BOTH the VALIDATE and the TEST block:
  A. predicted all-key-number mass (margins 3, 7, 10, 14) within 1.96 binomial SE of the observed rate;
  B. its absolute gap to observed is smaller than the uncalibrated discrete model's gap;
  C. paired per-game MARGIN log-score gain over the uncalibrated discrete model has a 95% bootstrap CI whose lower
     bound is > 0, and total / team-score gains have CI upper bounds >= 0 (not significantly worse);
  D. event Brier (strikes around the pregame centre) is no worse than the uncalibrated model's by more than 0.001;
  E. it also beats the repository's Normal baseline on margin log score with a CI lower bound > 0.
``PROMOTABLE`` is a statement about evidence on these blocks only. Nothing here promotes anything: promotion is an
owner decision and needs, in addition, market-price evidence that does not exist in this repository.

*** WHAT IS NOT USED ***
Ratings are shrunk within-season team points for/against (``naive_means``). No havoc metric, no opponent-adjusted
efficiency (these records carry no play counts), no market price. This tests the SHAPE of the score distribution.
"""

from __future__ import annotations

import gzip
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from cfb_edge_finder.projections.discrete_scoring import ShapeParams, build_distribution
from cfb_edge_finder.research.discrete_scoring_eval import (
    KEY_MARGINS,
    MIN_PRIOR_GAMES,
    NormalSystem,
    _bootstrap,
    _disc_logp,
    _ece,
    _strikes,
    fit_calibration,
    fit_calibration_per_key,
    fit_normal,
    fit_shape,
    naive_means,
)

PROTOCOL_VERSION = "cfb_multiseason_calibration/1.0.0"

TRAIN_SEASON = 2024
VALIDATE_SEASON = 2025
TEST_SEASON = 2026
FINAL_START = "2026-10-17"
FINAL_END = "2026-11-01"

#: Pre-declared thresholds (see module docstring). Changing one after seeing results is a protocol violation.
KEY_MASS_SE_MULTIPLE = 1.96
BRIER_TOLERANCE = 0.001
MAX_LIFT_MARGIN = 28
LIFT_PRIOR_COUNT = 20.0
LIFT_BOUNDS = (0.25, 4.0)
LIFT_ROUNDS = 3

CANDIDATES = ("discrete_v1", "discrete_v2", "discrete_emp")
REFERENCE = "discrete_base"


# ----------------------------------------------------------------------------------------------- loading
def load_games(root: Path, seasons: tuple[int, ...] = (2024, 2025, 2026)) -> list[dict[str, Any]]:
    """FBS-vs-FBS games with final scores from ``records_<season>.jsonl.gz`` under ``root`` (validation dir)."""
    games: list[dict[str, Any]] = []
    for season in seasons:
        path = Path(root) / f"records_{season}.jsonl.gz"
        if not path.exists():
            continue
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                if r.get("divisions") != ["fbs", "fbs"] or r.get("fcs_involved"):
                    continue
                if r.get("actual_home") is None or r.get("actual_away") is None:
                    continue
                kickoff = str(r["kickoff_utc"])
                games.append(
                    {
                        "game_id": str(r["game_id"]),
                        "season": int(r["season"]),
                        "kickoff": kickoff,
                        "date": kickoff[:10],
                        "home": str(r["home"]),
                        "away": str(r["away"]),
                        "neutral": bool(r.get("neutral_site")),
                        "home_pts": int(r["actual_home"]),
                        "away_pts": int(r["actual_away"]),
                    }
                )
    games.sort(key=lambda g: (g["kickoff"], g["game_id"]))
    return games


def rate_games(games: list[dict[str, Any]]) -> list[tuple[dict[str, Any], tuple[float, float]]]:
    """Point-in-time means: for each game, shrunk team points for/against from STRICTLY EARLIER dates of the SAME
    season only. A game with fewer than ``MIN_PRIOR_GAMES`` priors for either team is not rated (never guessed)."""
    by_season: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for g in games:
        by_season[g["season"]].append(g)
    out: list[tuple[dict[str, Any], tuple[float, float]]] = []
    for season_games in by_season.values():
        for g in season_games:
            prior = [p for p in season_games if p["date"] < g["date"]]
            if not prior:
                continue
            counts: dict[str, int] = defaultdict(int)
            for p in prior:
                counts[p["home"]] += 1
                counts[p["away"]] += 1
            if counts[g["home"]] < MIN_PRIOR_GAMES or counts[g["away"]] < MIN_PRIOR_GAMES:
                continue
            mu = naive_means(prior, g)
            if mu is not None:
                out.append((g, mu))
    out.sort(key=lambda gm: (gm[0]["kickoff"], gm[0]["game_id"]))
    return out


def split_blocks(
    rated: list[tuple[dict[str, Any], tuple[float, float]]],
) -> dict[str, list[tuple[dict[str, Any], tuple[float, float]]]]:
    train = [x for x in rated if x[0]["season"] == TRAIN_SEASON]
    validate = [x for x in rated if x[0]["season"] == VALIDATE_SEASON]
    test = [x for x in rated if x[0]["season"] == TEST_SEASON and x[0]["date"] < FINAL_START]
    final = [x for x in rated if x[0]["season"] == TEST_SEASON and FINAL_START <= x[0]["date"] <= FINAL_END]
    return {"train": train, "validate": validate, "test": test, "final": final}


# ----------------------------------------------------------------------------------------------- fitting
def fit_empirical_lifts(
    train: list[tuple[dict[str, Any], tuple[float, float]]],
    base: ShapeParams,
    *,
    max_margin: int = MAX_LIFT_MARGIN,
    prior: float = LIFT_PRIOR_COUNT,
    rounds: int = LIFT_ROUNDS,
) -> tuple[tuple[int, float], ...]:
    """Per-margin lifts for margins 1..``max_margin`` from TRAIN games only.

    For each margin m: lift_m = (observed count + prior) / (model-expected count + prior), shrunk toward 1 by the
    additive ``prior``. The model renormalises after lifting, so the table is refined for ``rounds`` rounds
    (each round multiplies by the residual ratio of the CURRENT model). Bounded in ``LIFT_BOUNDS``.
    """
    lifts = np.ones(max_margin + 1)
    obs = np.zeros(max_margin + 1)
    for g, _ in train:
        m = abs(g["home_pts"] - g["away_pts"])
        if 1 <= m <= max_margin:
            obs[m] += 1
    for _ in range(rounds):
        shape = ShapeParams(
            base.fg_share,
            base.sigma_common,
            base.sigma_team,
            base.margin_shrink,
            base.key_lift,
            tuple((m, float(lifts[m])) for m in range(1, max_margin + 1)),
        )
        pred = np.zeros(max_margin + 1)
        for _g, (mh, ma) in train:
            d = build_distribution(mh, ma, shape)
            for m in range(1, max_margin + 1):
                pred[m] += d.margin_prob(m) + d.margin_prob(-m)
        ratio = (obs + prior) / (pred + prior)
        lifts = np.clip(lifts * ratio, LIFT_BOUNDS[0], LIFT_BOUNDS[1])
    return tuple((m, float(lifts[m])) for m in range(1, max_margin + 1))


def fit_systems(train: list[tuple[dict[str, Any], tuple[float, float]]]) -> dict[str, Any]:
    """Every system, fitted on ``train`` and nothing else. The signature is the leakage guarantee."""
    cal = [g for g, _ in train]
    means = [mu for _, mu in train]
    normal = fit_normal(cal, means)
    base, base_ll = fit_shape(cal, means)
    v1, _ = fit_calibration(cal, means, base)
    v2, _ = fit_calibration_per_key(cal, means, base)
    emp_lifts = fit_empirical_lifts(train, base)
    emp = ShapeParams(base.fg_share, base.sigma_common, base.sigma_team, 1.0, 1.0, emp_lifts)
    return {
        "normal": normal,
        REFERENCE: base,
        "discrete_v1": v1,
        "discrete_v2": v2,
        "discrete_emp": emp,
        "_train_loglik_per_game_base": base_ll,
    }


# ----------------------------------------------------------------------------------------------- scoring
def score_block(block: list[tuple[dict[str, Any], tuple[float, float]]], systems: dict[str, Any]) -> dict[str, Any]:
    names = ["normal", REFERENCE, *CANDIDATES]
    per_game: dict[str, dict[str, list[float]]] = {n: defaultdict(list) for n in names}
    events: dict[str, tuple[list[float], list[int]]] = {n: ([], []) for n in names}
    key_pred: dict[str, list[float]] = {n: [] for n in names}
    key_obs: list[int] = []
    for g, (mh, ma) in block:
        h, a = g["home_pts"], g["away_pts"]
        key_obs.append(int(abs(h - a) in KEY_MARGINS))
        mstrikes, tstrikes = _strikes(mh - ma), _strikes(mh + ma)
        hstrikes, astrikes = _strikes(mh), _strikes(ma)
        for name in names:
            ev_p, ev_o = events[name]
            if name == "normal":
                ns: NormalSystem = systems["normal"]
                per_game[name]["margin"].append(ns.logp_margin(mh, ma, h - a))
                per_game[name]["total"].append(ns.logp_total(mh, ma, h + a))
                per_game[name]["team"].append(0.5 * (ns.logp_team(mh, h) + ns.logp_team(ma, a)))
                for s in mstrikes:
                    ev_p.append(ns.p_margin_gt(mh, ma, s))
                    ev_o.append(int(h - a > s))  # noqa: E702
                for s in tstrikes:
                    ev_p.append(ns.p_total_gt(mh, ma, s))
                    ev_o.append(int(h + a > s))  # noqa: E702
                for s in hstrikes:
                    ev_p.append(ns.p_team_gt(mh, s))
                    ev_o.append(int(h > s))  # noqa: E702
                for s in astrikes:
                    ev_p.append(ns.p_team_gt(ma, s))
                    ev_o.append(int(a > s))  # noqa: E702
                key_pred[name].append(ns.key_mass(mh, ma))
            else:
                d = build_distribution(mh, ma, systems[name])
                for k, v in _disc_logp(d, g).items():
                    per_game[name][k].append(v)
                for s in mstrikes:
                    ev_p.append(d.p_margin_gt(s))
                    ev_o.append(int(h - a > s))  # noqa: E702
                for s in tstrikes:
                    ev_p.append(d.p_total_gt(s))
                    ev_o.append(int(h + a > s))  # noqa: E702
                for s in hstrikes:
                    ev_p.append(d.p_team_gt(True, s))
                    ev_o.append(int(h > s))  # noqa: E702
                for s in astrikes:
                    ev_p.append(d.p_team_gt(False, s))
                    ev_o.append(int(a > s))  # noqa: E702
                key_pred[name].append(sum(d.margin_prob(sg * k) for k in KEY_MARGINS for sg in (1, -1)))
    return {
        "names": names,
        "per_game": per_game,
        "events": events,
        "key_pred": key_pred,
        "key_obs": key_obs,
        "n": len(block),
    }


def summarize_block(scored: dict[str, Any]) -> dict[str, Any]:
    n = scored["n"]
    obs_rate = float(np.mean(scored["key_obs"])) if n else float("nan")
    out: dict[str, Any] = {"games": n, "observed_key_mass": obs_rate, "systems": {}, "paired": {}}
    for name in scored["names"]:
        p, o = scored["events"][name]
        parr, oarr = np.asarray(p), np.asarray(o)
        out["systems"][name] = {
            "margin_logscore": float(np.mean(scored["per_game"][name]["margin"])),
            "total_logscore": float(np.mean(scored["per_game"][name]["total"])),
            "team_logscore": float(np.mean(scored["per_game"][name]["team"])),
            "event_brier": float(np.mean((parr - oarr) ** 2)),
            "event_ece": _ece(list(parr), list(oarr)),
            "key_mass_predicted": float(np.mean(scored["key_pred"][name])),
        }

    def paired(a: str, b: str) -> dict[str, Any]:
        res = {}
        for k in ("margin", "total", "team"):
            diff = [x - y for x, y in zip(scored["per_game"][a][k], scored["per_game"][b][k], strict=True)]
            mean, lo, hi = _bootstrap(diff)
            res[k] = {"mean": mean, "ci95": [lo, hi]}
        return res

    for c in CANDIDATES:
        out["paired"][c] = {"vs_" + REFERENCE: paired(c, REFERENCE), "vs_normal": paired(c, "normal")}
    out["paired"][REFERENCE] = {"vs_normal": paired(REFERENCE, "normal")}
    return out


# ----------------------------------------------------------------------------------------------- verdict
def criteria_for(candidate: str, summary: dict[str, Any]) -> dict[str, bool]:
    """A-E on ONE block (the pre-declared rule)."""
    n = summary["games"]
    obs = summary["observed_key_mass"]
    se = math.sqrt(max(obs * (1 - obs), 1e-12) / max(n, 1))
    cand, ref = summary["systems"][candidate], summary["systems"][REFERENCE]
    gap_c = abs(cand["key_mass_predicted"] - obs)
    gap_r = abs(ref["key_mass_predicted"] - obs)
    pr = summary["paired"][candidate]["vs_" + REFERENCE]
    pn = summary["paired"][candidate]["vs_normal"]
    return {
        "A_key_mass_within_1.96_se": bool(gap_c <= KEY_MASS_SE_MULTIPLE * se),
        "B_gap_smaller_than_uncalibrated": bool(gap_c < gap_r),
        "C_margin_gain_ci_lower_gt_0_and_others_not_worse": bool(
            pr["margin"]["ci95"][0] > 0 and pr["total"]["ci95"][1] >= 0 and pr["team"]["ci95"][1] >= 0
        ),
        "D_event_brier_not_worse": bool(cand["event_brier"] <= ref["event_brier"] + BRIER_TOLERANCE),
        "E_beats_normal_on_margin_ci_lower_gt_0": bool(pn["margin"]["ci95"][0] > 0),
    }


def select_candidate(validate_summary: dict[str, Any]) -> str:
    """Pre-declared selection: best VALIDATE margin log score; ties go to the simplest (list order)."""
    return max(
        CANDIDATES, key=lambda c: (round(validate_summary["systems"][c]["margin_logscore"], 9), -CANDIDATES.index(c))
    )


def verdict(blocks: dict[str, dict[str, Any]], selected: str) -> dict[str, Any]:
    per_block = {name: criteria_for(selected, blocks[name]) for name in ("validate", "test")}
    promotable = all(all(c.values()) for c in per_block.values())
    return {
        "selected_candidate": selected,
        "selection_rule": "best VALIDATE (2025) margin log score among the candidates fitted on TRAIN (2024)",
        "criteria_validate": per_block["validate"],
        "criteria_test": per_block["test"],
        "criteria_final_continuity_only": criteria_for(selected, blocks["final"]) if blocks["final"]["games"] else None,
        "verdict": "PROMOTABLE" if promotable else "NOT_PROMOTABLE",
        "promotion": "NOT_PROMOTED in either case: promotion is an owner decision and needs market-price evidence "
        "(none exists in this repository)",
    }


def run(root: Path) -> dict[str, Any]:
    games = load_games(root)
    rated = rate_games(games)
    parts = split_blocks(rated)
    if min(len(parts["train"]), len(parts["validate"]), len(parts["test"])) < 30:
        return {
            "status": "INSUFFICIENT_DATA",
            "protocol": PROTOCOL_VERSION,
            "counts": {k: len(v) for k, v in parts.items()},
        }
    systems = fit_systems(parts["train"])
    blocks = {
        name: summarize_block(score_block(parts[name], systems))
        for name in ("train", "validate", "test", "final")
        if parts[name]
    }
    selected = select_candidate(blocks["validate"])
    return {
        "status": "OK",
        "protocol": PROTOCOL_VERSION,
        "split": {
            "train": f"season {TRAIN_SEASON}",
            "validate": f"season {VALIDATE_SEASON}",
            "test": f"{TEST_SEASON} kickoffs before {FINAL_START}",
            "final": f"{FINAL_START}..{FINAL_END}",
        },
        "counts": {k: len(v) for k, v in parts.items()},
        "fitted": {
            "base": vars(systems[REFERENCE]) | {"key_lifts": list(systems[REFERENCE].key_lifts)},
            "v1": vars(systems["discrete_v1"]) | {"key_lifts": list(systems["discrete_v1"].key_lifts)},
            "v2": {**vars(systems["discrete_v2"]), "key_lifts": [list(x) for x in systems["discrete_v2"].key_lifts]},
            "emp_lifts": [list(x) for x in systems["discrete_emp"].key_lifts],
            "normal_sd": [systems["normal"].sd_margin, systems["normal"].sd_total, systems["normal"].sd_team],
        },
        "blocks": blocks,
        "verdict": verdict(blocks, selected),
        "disclosure": "FINAL block was seen in PR #249; it is a continuity check, never the basis of the verdict.",
    }


__all__ = [
    "CANDIDATES",
    "PROTOCOL_VERSION",
    "REFERENCE",
    "criteria_for",
    "fit_empirical_lifts",
    "fit_systems",
    "load_games",
    "rate_games",
    "run",
    "score_block",
    "select_candidate",
    "split_blocks",
    "summarize_block",
    "verdict",
]
