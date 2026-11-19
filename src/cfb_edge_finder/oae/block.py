"""Build the additive ``oae_challenger`` block of the CFB projection artifact (never touches existing fields)."""
from __future__ import annotations

import math
from datetime import datetime

from . import ARTIFACT_KEY, FLAG, MODEL_VERSION, authority, unavailable_block
from . import forecast as fc
from . import frozen as fz
from .ratings import PregameViolation, local_date, parse_utc, rate_date

# Families rated RELIABLE on the 2025-26 holdout (research/cfb_challenger/phase15/RESULTS_P15.md, results_p15.json).
FG_FAMILIES = ("winner", "spread")
PERIOD_FAMILIES = {"q1": ("spread", "winner"), "q2": ("total",), "q3": ("spread",), "q4": ("spread", "total"),
                   "h1": ("total",)}
EXCLUDED = {
    "fg_total": "PARTIAL: total probabilities over-projected (holdout bias +0.059 toward the over); not emitted",
    "fg_team_total": "PARTIAL: same scoring over-projection (bias +0.046); not emitted",
    "q1_total": "ABSTAIN: bias +0.02-0.03 toward the over", "q3_total": "ABSTAIN: bias +0.03 toward the over",
    "h1_spread": "no gain over the naive split", "h1_winner": "no gain over the naive split",
    "h2_all": "USABLE only; not emitted", "ties": "Q2/Q3/2H tie calibration slope 0.64 or worse; not emitted",
}
FG_SPREAD_HALF = 14          # research strike window around the model mean (Kalshi KXNCAAFSPREAD)
FG_ABSTAIN = (0.10, 0.90)    # FG spread probabilities outside this band are flagged abstain (alt-line miscalibration)
Q_SPREADS = [k + 0.5 for k in range(0, 15)]
Q_TOTALS = [k + 0.5 for k in range(0, 31)]
H1_TOTAL_HALF = 10

# Contract basis of every emitted family, identical to the #308 validation (ESPN linescores; Kalshi CFB rules).
SETTLEMENT_BASIS = {
    "fg": "full game, final score INCLUDING overtime; winner is 2-way (no ties); p_home_win + p_away_win = 1",
    **{f"q{i}": f"regulation quarter {i} points only; overtime EXCLUDED" for i in range(1, 5)},
    "h1": "regulation Q1 + Q2 points only",
}
PERIOD_WINNER_BASIS = ("3-way (home / away / tie): a tied period settles both team contracts NO; tie probability "
                       "is not emitted, so p_home_win + p_away_win = 1 - P(tie)")
def _r(x: float, nd: int = 4) -> float:
    return round(float(x), nd)


def _half_points(centre: float, half: float) -> list[float]:
    lo, hi = math.floor(centre - half), math.ceil(centre + half)
    return [k + 0.5 for k in range(lo, hi) if abs(k + 0.5 - centre) <= half]


def forecast_game(R, gp, F: fz.Frozen, home, away, neutral) -> dict:
    from .ratings import game_features
    f = game_features(R, gp, F.preseason, home, away, True, True, neutral)
    fg = fc.full_game(f, F.p15["fg"]["layer"], F.p15["fg"]["sigma"])
    J = fc.joint_from_margin_total(fg["margin"], fg["sd_m"], fg["total"], fg["sd_t"], fg["rho"], 200)
    ladder = []
    for k in _half_points(fg["margin"], FG_SPREAD_HALF):
        p = fc.p_margin_gt(J, k)
        ladder.append({"k": k, "p_home_by_more": _r(p), "abstain": not (FG_ABSTAIN[0] <= p <= FG_ABSTAIN[1])})
    out = {"fg": {"expected_margin": _r(fg["margin"]), "spread_home": _r(-fg["margin"]), "sigma_margin": _r(fg["sd_m"]),
                  "p_home_win": _r(fg["p_home_win"]), "p_away_win": _r(1 - fg["p_home_win"]),
                  "spread_ladder": ladder},
           "periods": {}, "games_played": {"home": int(f["n_h"]), "away": int(f["n_a"])}}
    for seg, fams in PERIOD_FAMILIES.items():
        fp = F.p15["periods"][seg]
        mh, ma = fc.period_means(f, fg, seg, fp["coef"])
        Jp = fc.period_joint(_nb(F, seg), mh, ma, fp["tie_scale"])
        d = {}
        if "spread" in fams or "winner" in fams:
            d["expected_margin"] = _r(mh - ma)
        if "total" in fams:
            d["expected_total"] = _r(mh + ma)
        if "winner" in fams:
            d["p_home_win"], d["p_away_win"] = _r(fc.p_margin_gt(Jp, 0.5)), _r(fc.p_away_margin_gt(Jp, 0.5))
        if "spread" in fams:
            d["spread_ladder"] = [{"k": k, "p_home_by_more": _r(fc.p_margin_gt(Jp, k)),
                                   "p_away_by_more": _r(fc.p_away_margin_gt(Jp, k))} for k in Q_SPREADS]
        if "total" in fams:
            ks = _half_points(mh + ma, H1_TOTAL_HALF) if seg == "h1" else Q_TOTALS
            d["total_ladder"] = [{"k": k, "p_over": _r(fc.p_total_gt(Jp, k))} for k in ks]
        out["periods"][seg] = d
    return out


_NB_CACHE: dict = {}


def _nb(F: fz.Frozen, seg: str) -> fc.NeighbourPMF:
    if seg not in _NB_CACHE:
        mu, y = F.nb[seg]
        _NB_CACHE[seg] = fc.NeighbourPMF(mu, y)
    return _NB_CACHE[seg]


def build_block(games: list[dict], schedule: dict, log_rows: list[dict], now_utc: datetime,
                fbs_conference_ids) -> dict:
    """games: the artifact's existing `games` entries (game_id, home_id, away_id, start_utc).
    schedule: data/football/<season>/schedule.json. log_rows: compact rows of the current season's game log."""
    F = fz.load()
    prov = fz.provenance()
    season = int(schedule.get("season", 0))
    ev = {str(e["id"]): e for e in schedule.get("events", [])}
    auth = authority()
    block = {"model_version": MODEL_VERSION, **auth, "flag": FLAG, "available": True,
             "unavailable_reason": None,
             "note": ("Additive block; never overrides any production field. " +
                      ("Owner-promoted operating CFB game forecast; forecast only, no bet authority."
                       if auth["operating_forecast"] else "Not approved for use.")),
             "sign_convention": ("margin = home - away; spread_home = -expected_margin; "
                                 "ladder p = P(side wins by more than k)"),
             "settlement_basis": SETTLEMENT_BASIS, "period_winner_basis": PERIOD_WINNER_BASIS,
             "families": {"fg": list(FG_FAMILIES), **{k: list(v) for k, v in PERIOD_FAMILIES.items()}},
             "excluded": EXCLUDED, "provenance": {k: prov[k] for k in ("sources", "season")} |
             {"files": {k: v["sha256"] for k, v in prov["files"].items()}},
             "forecast_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "games": [], "skipped": []}
    if season != F.season:  # covers 2027+: frozen prior rows / preseason table exist for F.season only
        block["available"], block["unavailable_reason"] = False, f"season {season} not covered by frozen inputs"
        block["skipped"] = [{"game_id": str(g["game_id"]), "reason": f"no frozen inputs for season {season}"}
                            for g in games]
        return block
    rows = F.prior_rows + [r for r in log_rows if r["season"] == season]
    p = F.p307["params"]
    cache: dict = {}
    for g in games:
        gid = str(g["game_id"])
        e = ev.get(gid)
        start = parse_utc(g["start_utc"])
        if e is None:
            block["skipped"].append({"game_id": gid, "reason": "not in schedule"})
            continue
        side = {c["home_away"]: c for c in e["competitors"]}
        home, away = str(g["home_id"]), str(g["away_id"])
        if {str(side.get("home", {}).get("team_id")), str(side.get("away", {}).get("team_id"))} != {home, away}:
            block["skipped"].append({"game_id": gid, "reason": "schedule teams differ"})
            continue
        if not all(str(side[s].get("conference_id")) in fbs_conference_ids for s in ("home", "away")):
            block["skipped"].append({"game_id": gid, "reason": "not FBS vs FBS (outside validation)"})
            continue
        if start <= now_utc:
            block["skipped"].append({"game_id": gid, "reason": "already started"})
            continue
        d = local_date(g["start_utc"])
        if d not in cache:
            cache[d] = rate_date(rows, season, d, now_utc, p)
        R, gp, n_rows = cache[d]
        fcst = forecast_game(R, gp, F, home, away, bool(e.get("neutral_site")))
        if not start > now_utc:                       # forecast time strictly before kickoff (redundant guard)
            raise PregameViolation(f"game {gid} kickoff {g['start_utc']} is not after the forecast time")
        block["games"].append({"game_id": gid, "start_utc": g["start_utc"], "home_id": home, "away_id": away,
                               "neutral": bool(e.get("neutral_site")), "ratings_game_date": d,
                               "n_training_team_games": n_rows, **fcst})
    return block


__all__ = ["ARTIFACT_KEY", "build_block", "forecast_game", "unavailable_block"]
