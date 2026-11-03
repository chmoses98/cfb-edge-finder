#!/usr/bin/env python3
"""Research only: how are CFB first-quarter / first-half scores distributed, and does a walk-forward rating help?

    python3 scripts/research/period_scoring_eval.py --team-games team_games.jsonl [--backfill period_scores.jsonl] \
        [--test-weeks 7 8 9] [--dev-weeks 5 6]

NOT wired into any pricing, recommendation or execution path. It promotes nothing. Output is aggregate only.

Protocol (fixed before the held-out weeks were scored):
  * FBS-vs-FBS games only (FCS buy games distort the home-field term).
  * Every statistic for a test week uses only games from strictly earlier weeks (point in time).
  * Dev weeks choose the ridge penalty only; the held-out weeks are never used to tune.
  * Intervals are 95% bootstrap over games (one game is one outcome).

Two questions:
  1. SHAPE: is a smoothed *discrete* empirical pmf a better description of period totals/margins than a Normal?
     (period scores are lumpy: 0, 3, 7, 10, 14 ... so a continuous curve misprices the key numbers.)
  2. RATING: does a ridge team-offence/defence rating predict 1H/Q1 totals and margins better than the league average?
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict

import numpy as np

PERIODS = {"q": 1, "h": 2}  # number of leading periods summed


def load_games(team_games: list[dict], backfill: list[dict] | None = None) -> list[dict]:
    """Join to one record per game with leading-period points for home/away. Rejects rows whose periods do not sum to
    the final score or whose team ids do not match, rather than guessing."""
    by_game: dict[str, list[dict]] = defaultdict(list)
    for r in team_games:
        by_game[r["game_id"]].append(r)
    bf = {r["game_id"]: r for r in (backfill or [])}
    out = []
    for gid, rows in by_game.items():
        if len(rows) != 2:
            continue
        a, b = rows
        if a.get("period_points") and b.get("period_points"):
            pp = {a["team_id"]: a["period_points"], b["team_id"]: b["period_points"]}
        elif gid in bf:
            r = bf[gid]
            pp = {r["home_team_id"]: r["home_periods"], r["away_team_id"]: r["away_periods"]}
        else:
            continue
        if set(pp) != {a["team_id"], b["team_id"]}:
            continue
        if any(abs(sum(pp[x["team_id"]]) - x["points_for"]) > 1e-9 for x in (a, b)):
            continue
        home = a if a["site"] == "home" else b if b["site"] == "home" else a  # neutral: fixed arbitrary orientation
        away = b if home is a else a

        def lead(t, n, pp=pp):
            return sum(pp[t["team_id"]][:n])

        out.append(
            {
                "gid": gid,
                "week": a["week"],
                "site": home["site"],
                "home": home["team_id"],
                "away": away["team_id"],
                "fbs": a.get("team_division") == "fbs" and b.get("team_division") == "fbs",
                "q": (lead(home, 1), lead(away, 1)),
                "h": (lead(home, 2), lead(away, 2)),
                "f": (home["points_for"], away["points_for"]),
            }
        )
    out.sort(key=lambda g: (g["week"], g["gid"]))
    return out


def value(g: dict, per: str, kind: str) -> int:
    h, a = g[per]
    return int(h + a) if kind == "total" else int(h - a)


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def empirical_pmf(train: np.ndarray, lo: int, hi: int, alpha: float = 0.5) -> np.ndarray:
    sup = np.arange(lo, hi + 1)
    cnt = np.array([np.sum(train == k) for k in sup], float) + alpha
    return cnt / cnt.sum()


def normal_pmf(train: np.ndarray, lo: int, hi: int, kind: str) -> np.ndarray:
    sup = np.arange(lo, hi + 1)
    mu, sd = float(train.mean()), float(train.std()) or 1.0
    p = np.array([_ncdf((k + 0.5 - mu) / sd) - _ncdf((k - 0.5 - mu) / sd) for k in sup])
    if kind == "total":
        p = np.where(sup < 0, 0.0, p)  # a total cannot be negative
    p = np.maximum(p, 1e-6)
    return p / p.sum()


def shape_logscores(games: list[dict], per: str, kind: str, test_weeks, alpha: float = 0.5):
    """Per-game negative log-likelihood of the realised value under (discrete empirical, Normal), trained strictly
    before each test week. Returns two arrays (empirical, normal)."""
    fbs = [g for g in games if g["fbs"]]
    emp, nor = [], []
    for w in test_weeks:
        tr = np.array([value(g, per, kind) for g in fbs if g["week"] < w])
        te = [value(g, per, kind) for g in fbs if g["week"] == w]
        if len(tr) < 50 or not te:
            continue
        lo, hi = int(tr.min()) - 10, int(tr.max()) + 10
        ep, npm = empirical_pmf(tr, lo, hi, alpha), normal_pmf(tr, lo, hi, kind)
        for a in te:
            i = min(max(a, lo), hi) - lo
            emp.append(-math.log(ep[i]))
            nor.append(-math.log(npm[i]))
    return np.array(emp), np.array(nor)


def boot_ci(d: np.ndarray, reps: int = 4000, seed: int = 3) -> tuple[float, float]:
    rnd = random.Random(seed)
    n = len(d)
    o = sorted(float(np.mean(d[[rnd.randrange(n) for _ in range(n)]])) for _ in range(reps))
    return o[int(0.025 * reps)], o[int(0.975 * reps) - 1]


def over_probabilities(games: list[dict], per: str, strikes) -> list[dict]:
    """Empirical P(total > k) vs Normal at half-point strikes, using all FBS games given (descriptive, in-sample)."""
    tr = np.array([value(g, per, "total") for g in games if g["fbs"]])
    mu, sd = float(tr.mean()), float(tr.std()) or 1.0
    return [
        {"strike": k, "empirical": float(np.mean(tr > k)), "normal": 1 - _ncdf((k - mu) / sd), "n": int(len(tr))}
        for k in strikes
    ]


# ---------------------------------------------------------------- ratings
def _design(rows, ti, T):
    X = np.zeros((len(rows), 2 * T + 2))
    for i, (t, o, hf, _y) in enumerate(rows):
        X[i, ti[t]] = 1
        X[i, T + ti[o]] = 1
        X[i, 2 * T] = hf
        X[i, 2 * T + 1] = 1
    return X


def _fit(X, y, lam, T):
    pen = np.ones(X.shape[1]) * lam
    pen[2 * T :] = 1e-6  # never shrink home field or intercept
    return np.linalg.solve(X.T @ X + np.diag(pen), X.T @ y)


def _obs(g, per):
    hf = 1.0 if g["site"] == "home" else 0.0
    return [(g["home"], g["away"], hf, g[per][0]), (g["away"], g["home"], -hf, g[per][1])]


def rating_errors(games: list[dict], per: str, kind: str, lam: float, test_weeks):
    """Squared errors (rating model, league-average baseline) per held-out FBS game; ratings use earlier weeks only."""
    fbs = [g for g in games if g["fbs"]]
    teams = sorted({g["home"] for g in games} | {g["away"] for g in games})
    ti = {t: i for i, t in enumerate(teams)}
    T = len(teams)
    se_m, se_b = [], []
    for w in test_weeks:
        tr = [o for g in fbs if g["week"] < w for o in _obs(g, per)]
        if len(tr) < 200:
            continue
        y = np.array([o[3] for o in tr], float)
        beta = _fit(_design(tr, ti, T), y, lam, T)
        mu = float(y.mean())
        for g in fbs:
            if g["week"] != w:
                continue
            hf = 1.0 if g["site"] == "home" else 0.0

            def side(t, o, h, beta=beta):
                x = np.zeros(2 * T + 2)
                x[ti[t]] = 1
                x[T + ti[o]] = 1
                x[2 * T] = h
                x[2 * T + 1] = 1
                return float(x @ beta)

            ph, pa = side(g["home"], g["away"], hf), side(g["away"], g["home"], -hf)
            h, a = g[per]
            if kind == "total":
                se_m.append((h + a - (ph + pa)) ** 2)
                se_b.append((h + a - 2 * mu) ** 2)
            else:
                se_m.append((h - a - (ph - pa)) ** 2)
                se_b.append((h - a) ** 2)  # league-average baseline for a margin is 0 (home field is in the model only)
    return np.array(se_m), np.array(se_b)


def choose_lambda(games, per, kind, dev_weeks, grid=(1, 3, 10, 30, 100, 300)) -> float:
    best, best_mse = grid[0], float("inf")
    for lam in grid:
        m, _ = rating_errors(games, per, kind, lam, dev_weeks)
        if len(m) and m.mean() < best_mse:
            best, best_mse = lam, float(m.mean())
    return best


def run(games, dev_weeks, test_weeks) -> dict:
    res: dict = {"shape": {}, "rating": {}, "n_heldout": 0}
    res["n_heldout"] = sum(1 for g in games if g["fbs"] and g["week"] in set(test_weeks))
    for per in ("q", "h"):
        for kind in ("total", "margin"):
            e, n = shape_logscores(games, per, kind, test_weeks)
            if len(e):
                d = n - e  # positive => discrete empirical better
                lo, hi = boot_ci(d)
                res["shape"][f"{per}|{kind}"] = {"nats_per_game": float(d.mean()), "ci": [lo, hi], "n": int(len(d))}
            lam = choose_lambda(games, per, kind, dev_weeks)
            m, b = rating_errors(games, per, kind, lam, test_weeks)
            if len(m):
                d = b - m
                lo, hi = boot_ci(d)
                res["rating"][f"{per}|{kind}"] = {
                    "lambda": lam,
                    "mse_reduction_pct": float(100 * d.mean() / b.mean()),
                    "ci_pct": [float(100 * lo / b.mean()), float(100 * hi / b.mean())],
                    "n": int(len(d)),
                }
    return res


def render(res: dict) -> str:
    L = [
        f"Held-out FBS-vs-FBS games: {res['n_heldout']}",
        "",
        "SHAPE: discrete empirical minus Normal (nats/game, + = empirical better)",
    ]
    for k, v in res["shape"].items():
        L.append(f"  {k:10s} {v['nats_per_game']:+.3f}  CI [{v['ci'][0]:+.3f}, {v['ci'][1]:+.3f}]  n={v['n']}")
    L += ["", "RATING vs league average: MSE reduction % (+ = ratings better)"]
    for k, v in res["rating"].items():
        L.append(
            f"  {k:10s} {v['mse_reduction_pct']:+.1f}%  CI [{v['ci_pct'][0]:+.1f}, {v['ci_pct'][1]:+.1f}]"
            f"  lambda={v['lambda']}  n={v['n']}"
        )
    return "\n".join(L) + "\n"


def _jsonl(path):
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team-games", required=True)
    ap.add_argument("--backfill")
    ap.add_argument("--dev-weeks", type=int, nargs="+", default=[5, 6])
    ap.add_argument("--test-weeks", type=int, nargs="+", default=[7, 8, 9])
    a = ap.parse_args(argv)
    games = load_games(_jsonl(a.team_games), _jsonl(a.backfill) if a.backfill else None)
    if not games:
        print("no usable games", file=sys.stderr)
        return 1
    print(render(run(games, a.dev_weeks, a.test_weeks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
