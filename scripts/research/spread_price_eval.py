#!/usr/bin/env python3
"""Research only: did the settled CFB full-game spread/total bets pay more than a point-in-time model says they were
worth, and what edge does a taker need to clear fee + half-spread at each price?

    python3 scripts/research/spread_price_eval.py --wagers app/latest/wagers.json \
        --team-games data/football/2026/team_games.jsonl --schedule data/football/2026/schedule.json \
        [--quotes 'data/live/execution/games/*/quotes.json'] [--json out.json]

NOT wired into pricing, recommendation or execution. Promotes nothing. Output is aggregate only (no ticker, order id,
fill id or team name is printed).

Protocol (fixed in code before any result was read):
  A. MODEL VS PRICE.  For each settled full-game spread / total bet, using only games that had FINISHED
     (kickoff + 4h) before the bet's placed_at, fit a ridge team off/def rating (FBS teams individually, every non-FBS
     team pooled as one "FCS" team, unpenalised intercept + home field). Margin / total distribution = the rating mean
     with a scale taken from out-of-sample residuals of earlier games; the margin additionally gets a discrete
     key-number shape factor (observed / expected frequency of each |margin| in earlier games, shrunk toward 1).
     Compared with the PRICE PAID (an ask, so it contains the half-spread): Brier and log-loss of P(bet side wins).
     That is ACCURACY only; profitability is reported separately. Bets placed at/after the game's scheduled kickoff are
     excluded (their price contains live game state this model does not have). The ridge penalty is the only tuned
     value and uses games of weeks 4-6 only.
  B. PRICE.  ROI and wins-above-price by entry-price band and side, fees counted ONCE (profit_loss is already net of
     fees; gross = profit_loss + fees). Minimum edge over the mid to clear fee + half-spread from the fee formula
     0.07 * P * (1 - P) per contract and captured quotes. A rule fixed before looking ("exclude YES-side game spreads
     priced above X") has X derived on the first chronological half and is judged on the second.
  C. TEAM-TOTAL NO.  Re-test on bets placed after the earlier sample; reports how many exist.

Kalshi strike semantics (contract rules and the recorded settlements): ticker number N settles YES iff the value is
>= N ("more than N - 0.5"), so no push is possible. The derived result is checked against the recorded settlement for
every bet; disagreeing joins are dropped and counted.

Everything is point in time and game-clustered (one game is one outcome). Cells are looked at after the fact: an
interval excluding zero is a LEAD, never a validated edge. There is no historical bid/ask at any bet's placement and no
true closing price in this data: all spread quantities are from LIVE captures of other games (proxy) and say so.
"""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import glob
import json
import math
import os
import random
import re
import sys
from collections import defaultdict

os.environ.setdefault("OMP_NUM_THREADS", "1")  # tiny solves: BLAS threading only adds contention
import numpy as np  # noqa: E402

MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
# Kalshi game-key team codes that are not an ESPN abbreviation / name prefix. Each was checked against the schedule
# for the game date (exactly one game that day has both teams); the settlement check in load_bets guards the rest.
ALIAS = {"MIZZ": "MIZ", "MTU": "MTSU", "MOSU": "MOST", "BSU": "BOIS", "ULL": "UL", "UST": "STMN", "TXAM": "TA&M"}
FINISH_HOURS = 4.0  # a game counts as finished this long after kickoff
PRIOR_SD = {
    "margin": 17.5,
    "total": 16.0,
}  # a-priori residual scale used until earlier out-of-sample residuals accumulate
PRIOR_N = 50
SHAPE_LAMBDA = 8.0  # shrinkage of the key-number shape factor toward 1 (pseudo-games)
MIN_TRAIN_GAMES = 150
DEV_WEEKS = (4, 5, 6)
LAMBDA_GRID = (1.0, 3.0, 10.0, 30.0, 100.0)
NO_RATING_LAMBDA = 1e7


# --------------------------------------------------------------------------------------------------------------------
# ticker / join
# --------------------------------------------------------------------------------------------------------------------
FAMILY_PREFIXES = (  # longest / most specific first
    ("KXNCAAFTEAMTOTAL", "TEAM_TOTAL"),
    ("KXNCAAF1QSPREAD", "1Q_SPREAD"),
    ("KXNCAAF1HSPREAD", "1H_SPREAD"),
    ("KXNCAAF1QTOTAL", "1Q_TOTAL"),
    ("KXNCAAF1HTOTAL", "1H_TOTAL"),
    ("KXNCAAFSPREAD", "GAME_SPREAD"),
    ("KXNCAAFTOTAL", "GAME_TOTAL"),
    ("KXNCAAFGAME", "GAME_ML"),
    ("KXMVECROSSCATEGORY", "COMBO"),
)
FULL_GAME = ("GAME_SPREAD", "GAME_TOTAL", "TEAM_TOTAL", "GAME_ML")


def family(ticker: str) -> str:
    for prefix, name in FAMILY_PREFIXES:
        if ticker.startswith(prefix):
            return name
    return "OTHER"


def parse_ticker(ticker: str) -> dict | None:
    """KXNCAAFSPREAD-26SEP11RUTGBC-RUTG7 -> family, date, concatenated team codes, tail team code, strike N."""
    parts = ticker.split("-")
    if len(parts) != 3:
        return None
    m = re.match(r"(\d\d)([A-Z]{3})(\d\d)([A-Z0-9]+)$", parts[1])
    if not m or m.group(2) not in MONTHS:
        return None
    date = dt.date(2000 + int(m.group(1)), MONTHS[m.group(2)], int(m.group(3)))
    tail = re.match(r"([A-Z&]*?)(\d+(?:\.\d+)?)$", parts[2])
    if not tail:
        return None
    return {
        "family": family(ticker),
        "date": date,
        "codes": m.group(4),
        "team": tail.group(1) or None,
        "strike": float(tail.group(2)),
    }


@functools.cache
def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def compat(code: str, comp: dict) -> int:
    """2 = exact abbreviation / alias / name, 1 = prefix or initial-subsequence of the location, 0 = no."""
    abbr = comp["abbreviation"].upper()
    if code == abbr or ALIAS.get(code) == abbr:
        return 2
    names = {_norm(n) for n in comp.get("names", [])} | {_norm(comp.get("location", ""))}
    if code in names:
        return 2
    loc = _norm(comp.get("location", ""))
    if len(code) >= 2 and loc.startswith(code):
        return 1
    if len(code) >= 2 and loc and code[0] == loc[0]:
        it = iter(loc)
        if all(ch in it for ch in code):
            return 1
    return 0


def index_schedule(events: list[dict]) -> dict:
    by = defaultdict(list)
    for e in events:
        if len(e.get("competitors", [])) != 2:
            continue
        d = dt.date.fromisoformat(e["date"][:10])
        for off in (0, -1):  # the UTC date can be the day after the local kickoff date
            by[d + dt.timedelta(days=off)].append(e)
    return by


def find_game(by: dict, date: dt.date, codes: str):
    """Unique (event, comp_x, comp_y) whose two teams match the concatenated codes, else None (never guesses)."""
    hits = []
    for e in by.get(date, []):
        a, b = e["competitors"]
        for x, y in ((a, b), (b, a)):
            for i in range(1, len(codes)):
                sx, sy = compat(codes[:i], x), compat(codes[i:], y)
                if sx and sy:
                    hits.append((sx + sy, e, x, y))
    if not hits:
        return None
    best = max(h[0] for h in hits)
    top = {h[1]["id"]: h for h in hits if h[0] == best}
    return next(iter(top.values()))[1:] if len(top) == 1 else None


def yes_wins(value: float, strike: float) -> bool:
    """Kalshi: ticker number N settles YES iff value >= N (rules text: 'more than N - 0.5')."""
    return value >= strike


def load_bets(wagers_path: str, events: list[dict]) -> tuple[list[dict], dict]:
    """Settled full-game bets joined to a final score. Returns (bets, diagnostics)."""
    items = json.load(open(wagers_path))["items"]
    by = index_schedule(events)
    diag = {"settled": 0, "period_combo_other": 0, "no_game": 0, "settlement_disagrees": 0, "joined": 0}
    out = []
    for w in items:
        if w.get("settlement_status") != "SETTLED":
            continue
        diag["settled"] += 1
        pt = parse_ticker(w["kalshi_ticker"])
        if pt is None or pt["family"] not in FULL_GAME:
            diag["period_combo_other"] += 1
            continue
        g = find_game(by, pt["date"], pt["codes"])
        if g is None or not g[0].get("completed"):
            diag["no_game"] += 1
            continue
        e, x, y = g
        try:
            pts = {"x": float(x["score"]), "y": float(y["score"])}
        except (TypeError, ValueError):
            diag["no_game"] += 1
            continue
        comps = {"x": x, "y": y}
        tm = None
        if pt["team"]:
            c = {k: compat(pt["team"], v) for k, v in comps.items()}
            if c["x"] == c["y"]:
                diag["no_game"] += 1
                continue
            tm = "x" if c["x"] > c["y"] else "y"
        opp = None if tm is None else ("y" if tm == "x" else "x")
        strike = pt["strike"]
        if pt["family"] == "GAME_TOTAL":
            value = pts["x"] + pts["y"]
        elif tm is None:
            diag["no_game"] += 1
            continue
        elif pt["family"] == "TEAM_TOTAL":
            value = pts[tm]
        else:  # GAME_SPREAD, or GAME_ML ("team wins" == margin >= 1)
            value = pts[tm] - pts[opp]
            if pt["family"] == "GAME_ML":
                strike = 1.0
        side = w["selection"]
        yes = yes_wins(value, strike)
        won = yes if side == "YES" else not yes
        if won != (w["profit_loss"] > 0):
            diag["settlement_disagrees"] += 1
            continue
        diag["joined"] += 1
        out.append(
            {
                "family": pt["family"],
                "side": side,
                "strike": strike,
                "price": w["average_price"],
                "contracts": w["contracts"],
                "stake": w["stake"],
                "fee": w["fees"],
                "pl": w["profit_loss"],
                "won": won,
                "placed": _ts(w["placed_at"]),
                "kickoff": _ts(e["date"]),
                "game": e["id"],
                "team_id": comps[tm]["team_id"] if tm else None,
                "value": value,
            }
        )
    return out, diag


def _ts(s: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=dt.UTC)


# --------------------------------------------------------------------------------------------------------------------
# point-in-time rating
# --------------------------------------------------------------------------------------------------------------------
def build_games(team_rows: list[dict]) -> list[dict]:
    """One record per game from team_games rows (two rows per game). Non-FBS teams collapse to team 'FCS' for rating
    purposes; real ids are kept in home_id / away_id. Games with no FBS team are dropped."""
    by = defaultdict(list)
    for r in team_rows:
        by[r["game_id"]].append(r)
    games = []
    for gid, rows in by.items():
        if len(rows) != 2:
            continue
        a, b = rows
        if a["site"] != "home" and b["site"] == "home":
            a, b = b, a
        fa, fb = a["team_division"] == "fbs", b["team_division"] == "fbs"
        if not (fa or fb):
            continue
        games.append(
            {
                "gid": gid,
                "week": a["week"],
                "ko": _ts(a["kickoff_utc"]),
                "home": a["team_id"] if fa else "FCS",
                "away": b["team_id"] if fb else "FCS",
                "home_id": a["team_id"],
                "away_id": b["team_id"],
                "hfa": 1.0 if "home" in (a["site"], b["site"]) else 0.0,
                "hp": float(a["points_for"]),
                "ap": float(b["points_for"]),
                "fbs2": fa and fb,
            }
        )
    games.sort(key=lambda g: (g["ko"], g["gid"]))
    return games


def fit_ridge(games: list[dict], lam: float) -> dict:
    """points_home = mu + off[h] + def[a] + hfa/2 ; points_away = mu + off[a] + def[h] - hfa/2.
    mu and hfa unpenalised, off/def ridge."""
    teams = sorted({t for g in games for t in (g["home"], g["away"])})
    ix = {t: i for i, t in enumerate(teams)}
    T = len(teams)
    n = len(games)
    X = np.zeros((2 * n, 2 + 2 * T))
    y = np.zeros(2 * n)
    if n:
        h = np.array([ix[g["home"]] for g in games])
        a = np.array([ix[g["away"]] for g in games])
        hfa = np.array([g["hfa"] for g in games])
        r = np.arange(n)
        X[2 * r, 0] = X[2 * r + 1, 0] = 1.0
        X[2 * r, 1], X[2 * r + 1, 1] = 0.5 * hfa, -0.5 * hfa
        X[2 * r, 2 + h] = 1.0
        X[2 * r, 2 + T + a] = 1.0
        X[2 * r + 1, 2 + a] = 1.0
        X[2 * r + 1, 2 + T + h] = 1.0
        y[2 * r] = [g["hp"] for g in games]
        y[2 * r + 1] = [g["ap"] for g in games]
    P = np.eye(2 + 2 * T) * lam
    P[0, 0] = P[1, 1] = 0.0
    beta = np.linalg.solve(X.T @ X + P + 1e-9 * np.eye(2 + 2 * T), X.T @ y)
    return {"ix": ix, "T": T, "beta": beta, "n": len(games)}


def predict(state: dict, home: str, away: str, hfa: float) -> tuple[float, float]:
    """(mean margin home-away, mean total). A team never seen in training gets the ridge prior (rating 0)."""
    b, ix, T = state["beta"], state["ix"], state["T"]

    def eff(team, part):
        i = ix.get(team)
        return 0.0 if i is None else b[2 + i + (T if part == "d" else 0)]

    ph = b[0] + eff(home, "o") + eff(away, "d") + 0.5 * b[1] * hfa
    pa = b[0] + eff(away, "o") + eff(home, "d") - 0.5 * b[1] * hfa
    return ph - pa, ph + pa


class Walk:
    """Walk-forward predictions. state(cutoff) uses only games FINISHED (kickoff + FINISH_HOURS) at or before cutoff."""

    def __init__(self, games: list[dict], lam: float):
        self.games = games  # sorted by kickoff
        self.lam = lam
        self._cache: dict[int, dict] = {}
        self._fin = [g["ko"] + dt.timedelta(hours=FINISH_HOURS) for g in games]  # non-decreasing
        self._oos: list[dict] | None = None
        self.shape_cache: dict[int, dict[int, float]] = {}

    def n_before(self, cutoff: dt.datetime) -> int:
        lo, hi = 0, len(self._fin)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._fin[mid] <= cutoff:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def state(self, cutoff: dt.datetime) -> dict:
        k = self.n_before(cutoff)
        if k not in self._cache:
            self._cache[k] = {**fit_ridge(self.games[:k], self.lam), "k": k}
        return self._cache[k]

    def oos(self) -> list[dict]:
        """Each game predicted from games finished before ITS kickoff: the out-of-sample residual pool."""
        if self._oos is None:
            rows = []
            for g in self.games:
                st = self.state(g["ko"])
                pm, pt = predict(st, g["home"], g["away"], g["hfa"]) if st["k"] else (0.0, 56.0)
                rows.append({**g, "pm": pm, "pt": pt, "n_train": st["k"]})
            self._oos = rows
        return self._oos

    def pool_before(self, cutoff: dt.datetime) -> list[dict]:
        k = self.n_before(cutoff)
        return self.oos()[:k]


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def pooled_sd(resid: list[float], kind: str) -> float:
    s2 = PRIOR_SD[kind] ** 2
    return math.sqrt((PRIOR_N * s2 + sum(r * r for r in resid)) / (PRIOR_N + len(resid)))


def margin_pmf(mu: float, sd: float, shape: dict[int, float] | None, span: int = 80) -> dict[int, float]:
    """Discretised Normal x key-number shape factor (symmetric in |m|), renormalised."""
    pm = {}
    for m in range(-span, span + 1):
        p = norm_cdf((m + 0.5 - mu) / sd) - norm_cdf((m - 0.5 - mu) / sd)
        pm[m] = p * (shape.get(abs(m), 1.0) if shape else 1.0)
    z = sum(pm.values())
    return {m: p / z for m, p in pm.items()}


def _ncdf_vec(x: np.ndarray) -> np.ndarray:
    """Standard normal CDF (Abramowitz-Stegun 7.1.26 erf, |error| < 1.5e-7)."""
    z = np.abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    erf = 1.0 - poly * np.exp(-z * z)
    return 0.5 * (1.0 + np.sign(x) * erf)


def shape_factors(pool: list[dict], sd: float, lam: float = SHAPE_LAMBDA, kmax: int = 40) -> dict[int, float]:
    """w[k] = (observed + lam) / (expected under the Normal + lam) over earlier games' |margin| == k."""
    ks = np.arange(kmax + 1)
    obs = np.zeros(kmax + 1)
    pm = np.array([g["pm"] for g in pool]) if pool else np.zeros(0)
    for g in pool:
        m = abs(round(g["hp"] - g["ap"]))
        if m <= kmax:
            obs[m] += 1.0
    exp = np.zeros(kmax + 1)
    if len(pm):
        up = _ncdf_vec((ks[None, :] + 0.5 - pm[:, None]) / sd) - _ncdf_vec((ks[None, :] - 0.5 - pm[:, None]) / sd)
        dn = _ncdf_vec((-ks[None, :] + 0.5 - pm[:, None]) / sd) - _ncdf_vec((-ks[None, :] - 0.5 - pm[:, None]) / sd)
        exp = up.sum(axis=0) + np.where(ks > 0, dn.sum(axis=0), 0.0)
    return {int(k): float((obs[k] + lam) / (exp[k] + lam)) for k in ks}


def model_p_yes(bet: dict, g: dict, walk: Walk, discrete: bool) -> tuple[float, float] | None:
    """(P(YES), model mean for the market's quantity) for one full-game spread/total bet, information strictly before
    bet['placed']."""
    st = walk.state(bet["placed"])
    if st["k"] < MIN_TRAIN_GAMES:
        return None
    pm, pt = predict(st, g["home"], g["away"], g["hfa"])
    pool = walk.pool_before(bet["placed"])
    n = bet["strike"]
    key = len(pool)
    if bet["family"] == "GAME_TOTAL":
        sd = pooled_sd([x["hp"] + x["ap"] - x["pt"] for x in pool], "total")
        return 1.0 - norm_cdf((n - 0.5 - pt) / sd), pt
    sd = pooled_sd([x["hp"] - x["ap"] - x["pm"] for x in pool], "margin")
    mu_team = -pm if bet["team_id"] == g["away_id"] else pm
    if discrete:
        if key not in walk.shape_cache:
            walk.shape_cache[key] = shape_factors(pool, sd)
        pmf = margin_pmf(mu_team, sd, walk.shape_cache[key])
        return sum(p for m, p in pmf.items() if m >= n), mu_team
    return 1.0 - norm_cdf((n - 0.5 - mu_team) / sd), mu_team


# --------------------------------------------------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------------------------------------------------
def cluster_boot(rows: list[dict], stat, reps: int = 3000, seed: int = 7) -> tuple[float, float]:
    """95% interval of stat(rows), resampling GAMES (rows sharing 'game' move together)."""
    by = defaultdict(list)
    for r in rows:
        by[r["game"]].append(r)
    gs = list(by.values())
    if len(gs) < 2:
        return (float("nan"), float("nan"))
    rnd = random.Random(seed)
    out = sorted(stat([x for g in (rnd.choice(gs) for _ in gs) for x in g]) for _ in range(reps))
    return out[int(0.025 * reps)], out[int(0.975 * reps) - 1]


def roi(rows: list[dict]) -> float:
    s = sum(r["stake"] for r in rows)
    return sum(r["pl"] for r in rows) / s if s else 0.0


def gross_roi(rows: list[dict]) -> float:
    """ROI before fees: profit_loss is net of fees, so add them back exactly once."""
    s = sum(r["stake"] for r in rows)
    return sum(r["pl"] + r["fee"] for r in rows) / s if s else 0.0


def wins_above_price(rows: list[dict]) -> tuple[float, float, int]:
    """(wins - sum(price), z vs the fair-price null, n). The price is an ask, so the null is slightly pessimistic."""
    e = sum(r["price"] for r in rows)
    v = sum(r["price"] * (1 - r["price"]) for r in rows)
    w = sum(1.0 for r in rows if r["won"])
    return w - e, ((w - e) / math.sqrt(v) if v else 0.0), len(rows)


def brier(p: float, y: bool) -> float:
    return (p - (1.0 if y else 0.0)) ** 2


def logloss(p: float, y: bool) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -math.log(p if y else 1 - p)


def fee_per_contract(price: float, rate: float = 0.07) -> float:
    """Taker fee per contract before cent rounding: 0.07 * P * (1 - P)."""
    return rate * price * (1 - price)


def fee_order(price: float, contracts: int, rate: float = 0.07) -> float:
    """Exchange fee for one order, rounded UP to the cent: ceil(0.07 * C * P * (1 - P))."""
    return math.ceil(round(rate * contracts * price * (1 - price) * 100, 9)) / 100


# --------------------------------------------------------------------------------------------------------------------
# part A
# --------------------------------------------------------------------------------------------------------------------
def choose_lambda(games: list[dict]) -> tuple[float, dict]:
    """Ridge penalty by walk-forward squared error (margin + total): dev-week FBS-vs-FBS games with enough history."""
    res = {}
    for lam in LAMBDA_GRID:
        errs = [
            (g["hp"] - g["ap"] - g["pm"]) ** 2 + (g["hp"] + g["ap"] - g["pt"]) ** 2
            for g in Walk(games, lam).oos()
            if g["week"] in DEV_WEEKS and g["n_train"] >= MIN_TRAIN_GAMES and g["fbs2"]
        ]
        res[lam] = float(np.mean(errs)) if errs else float("inf")
    return min(res, key=res.get), res


def score_bets(bets: list[dict], games: list[dict], lam: float) -> tuple[list[dict], dict]:
    by_id = {g["gid"]: g for g in games}
    walk, base = Walk(games, lam), Walk(games, NO_RATING_LAMBDA)
    rows, drop = [], defaultdict(int)
    for b in bets:
        if b["family"] not in ("GAME_SPREAD", "GAME_TOTAL"):
            continue
        if b["placed"] >= b["kickoff"]:
            drop["placed_at_or_after_kickoff"] += 1
            continue
        g = by_id.get(b["game"])
        if g is None:
            drop["game_not_in_team_games"] += 1
            continue
        if b["family"] == "GAME_SPREAD" and b["team_id"] not in (g["home_id"], g["away_id"]):
            drop["team_not_in_game"] += 1
            continue
        rn = model_p_yes(b, g, walk, discrete=False)
        rd = model_p_yes(b, g, walk, discrete=True)
        r0 = model_p_yes(b, g, base, discrete=False)
        if rn is None or rd is None or r0 is None:
            drop["too_little_history"] += 1
            continue
        pn, pd_, p0 = rn[0], rd[0], r0[0]

        def side(p, s=b["side"]):
            return p if s == "YES" else 1 - p

        rows.append(
            {
                **b,
                "week": g["week"],
                "fbs2": g["fbs2"],
                "p_price": b["price"],
                "p_fee": b["stake"] / b["contracts"],
                "p_normal": side(pn),
                "p_discrete": side(pd_),
                "p_norating": side(p0),
                "mu": rd[1],
            }
        )
    return rows, dict(drop)


def compare(rows: list[dict], name: str) -> dict:
    out = {"name": name, "n": len(rows), "games": len({r["game"] for r in rows})}
    if not rows:
        return out
    for metric, f in (("brier", brier), ("logloss", logloss)):
        for k in ("price", "fee", "normal", "discrete", "norating"):
            out[f"{metric}_{k}"] = float(np.mean([f(r[f"p_{k}"], r["won"]) for r in rows]))
        for k in ("normal", "discrete", "norating"):

            def stat(s, k=k, f=f):
                return float(np.mean([f(r["p_price"], r["won"]) - f(r[f"p_{k}"], r["won"]) for r in s]))

            out[f"{metric}_gain_{k}_vs_price"] = (stat(rows), *cluster_boot(rows, stat))
    out["mean_p_price"] = float(np.mean([r["p_price"] for r in rows]))
    out["mean_p_discrete"] = float(np.mean([r["p_discrete"] for r in rows]))
    out["hit_rate"] = float(np.mean([r["won"] for r in rows]))
    return out


def calibration(rows: list[dict], key: str, edges=(0.0, 0.35, 0.5, 0.65, 1.0001)) -> list[dict]:
    out = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        rs = [r for r in rows if lo <= r[key] < hi]
        if rs:
            out.append(
                {
                    "bin": f"{lo:.2f}-{min(hi, 1):.2f}",
                    "n": len(rs),
                    "mean_p": float(np.mean([r[key] for r in rs])),
                    "hit": float(np.mean([r["won"] for r in rs])),
                }
            )
    return out


def edge_split(rows: list[dict]) -> list[dict]:
    """PROFITABILITY, kept apart from accuracy: ROI where the discrete model said the bet cleared its own
    fee-inclusive breakeven vs where it did not. Small n: a lead at best."""
    out = []
    for name, sel in (
        ("model edge over fee-inclusive breakeven > 0", lambda r: r["p_discrete"] - r["p_fee"] > 0),
        ("model edge <= 0 (model disagrees)", lambda r: r["p_discrete"] - r["p_fee"] <= 0),
    ):
        rs = [r for r in rows if sel(r)]
        if rs:
            out.append({"name": name, "n": len(rs), "roi": roi(rs), "ci": cluster_boot(rs, roi)})
    return out


def spread_descriptives(rows: list[dict]) -> list[dict]:
    """Descriptive cuts of scored GAME_SPREAD bets: ROI and wins above price. Looked at after the fact: leads only."""
    out = []
    sp = [r for r in rows if r["family"] == "GAME_SPREAD"]

    def cell(name, rs):
        if len(rs) >= 8:
            wa, z, n = wins_above_price(rs)
            out.append(
                {"cut": name, "n": n, "roi": roi(rs), "ci": cluster_boot(rs, roi), "wins_above_price": wa, "z": z}
            )

    cell("all scored game spreads", sp)
    for lo, hi, nm in ((0, 7, "strike under 7"), (7, 14, "strike 7-13"), (14, 1000, "strike 14+")):
        cell(nm, [r for r in sp if lo <= r["strike"] < hi])
    for side in ("YES", "NO"):
        cell(f"side {side}", [r for r in sp if r["side"] == side])
    cell("model prob > price paid (model agrees)", [r for r in sp if r["p_discrete"] > r["p_price"]])
    cell("model prob <= price paid (model disagrees)", [r for r in sp if r["p_discrete"] <= r["p_price"]])
    hi = [r for r in sp if r["side"] == "YES" and r["price"] > 0.5]
    if len(hi) >= 8:
        cell("YES side priced above 50c", hi)
        out[-1].update(
            {
                "mean_price": float(np.mean([r["p_price"] for r in hi])),
                "mean_model": float(np.mean([r["p_discrete"] for r in hi])),
                "hit": float(np.mean([r["won"] for r in hi])),
                "brier_price": float(np.mean([brier(r["p_price"], r["won"]) for r in hi])),
                "brier_model": float(np.mean([brier(r["p_discrete"], r["won"]) for r in hi])),
            }
        )
    cell("bet team is the model favourite", [r for r in sp if r["mu"] > 0])
    cell("bet team is the model underdog", [r for r in sp if r["mu"] <= 0])
    cell("FBS vs FBS", [r for r in sp if r["fbs2"]])
    cell("FBS vs FCS", [r for r in sp if not r["fbs2"]])
    return out


# --------------------------------------------------------------------------------------------------------------------
# part B
# --------------------------------------------------------------------------------------------------------------------
PRICE_BANDS = ((0.0, 0.30), (0.30, 0.50), (0.50, 0.70), (0.70, 1.01))


def band_table(rows: list[dict]) -> list[dict]:
    out = []
    for side in ("YES", "NO", "ALL"):
        for lo, hi in PRICE_BANDS:
            rs = [r for r in rows if lo <= r["price"] < hi and (side == "ALL" or r["side"] == side)]
            if len(rs) < 5:
                continue
            wa, z, n = wins_above_price(rs)
            out.append(
                {
                    "side": side,
                    "band": f"{lo:.2f}-{min(hi, 1):.2f}",
                    "n": n,
                    "stake": sum(r["stake"] for r in rs),
                    "roi_net": roi(rs),
                    "ci": cluster_boot(rs, roi),
                    "roi_gross": gross_roi(rs),
                    "fee_pct": sum(r["fee"] for r in rs) / sum(r["stake"] for r in rs),
                    "wins_above_price": wa,
                    "z": z,
                }
            )
    return out


def quote_half_spreads(quote_glob: str) -> dict[str, list[tuple[float, float]]]:
    """(ask, half_spread) per active two-sided contract side in captured quotes, by family. These are LIVE captures of
    upcoming games, not the book at any historical fill."""
    out = defaultdict(list)
    for f in glob.glob(quote_glob):
        for m in json.load(open(f)).get("quotes", []):
            if m.get("status") != "active" or m.get("book_state") != "two_sided":
                continue
            for side in ("yes", "no"):
                ask, bid = m.get(f"{side}_ask"), m.get(f"{side}_bid")
                if ask is not None and bid is not None and bid <= ask:
                    out[m.get("family")].append((ask, (ask - bid) / 2))
    return out


def min_edge_table(quotes: dict, fam_key: str) -> list[dict]:
    """Edge over the MID a taker needs at each ask band to break even: fee 0.07*P*(1-P) + captured half-spread."""
    out = []
    for lo, hi in PRICE_BANDS:
        sel = [(a, h) for a, h in quotes.get(fam_key, []) if lo <= a < hi]
        mid = (max(lo, 0.05) + min(hi, 0.95)) / 2
        row = {
            "band": f"{lo:.2f}-{min(hi, 1):.2f}",
            "n_quotes": len(sel),
            "fee_c_at_band_mid": 100 * fee_per_contract(mid),
        }
        if sel:
            hs = [h for _, h in sel]
            row["half_spread_median_c"] = 100 * float(np.median(hs))
            row["min_edge_median_c"] = 100 * (fee_per_contract(mid) + float(np.median(hs)))
        out.append(row)
    return out


def _is_yes_spread(r: dict) -> bool:
    return r["family"] == "GAME_SPREAD" and r["side"] == "YES"


def chronological_rule_test(bets: list[dict], grid=(0.20, 0.30, 0.40, 0.50, 0.60), min_excl: int = 10) -> dict:
    """Rule fixed before looking: exclude YES-side GAME_SPREAD bets priced above X. X is derived on the first half (by
    placed_at) as the grid value whose EXCLUDED set has the lowest ROI (>= min_excl bets), then judged on the second."""
    rows = sorted(bets, key=lambda r: r["placed"])
    half = len(rows) // 2
    a, b = rows[:half], rows[half:]
    res = {"n_first": len(a), "n_second": len(b), "derive": []}
    best = None
    for x in grid:
        ex = [r for r in a if _is_yes_spread(r) and r["price"] > x]
        if len(ex) < min_excl:
            continue
        res["derive"].append({"x": x, "n_excluded": len(ex), "roi_excluded": roi(ex)})
        if best is None or roi(ex) < best[1]:
            best = (x, roi(ex))
    if best is None:
        return {**res, "x": None}
    x = best[0]
    drop = lambda r: _is_yes_spread(r) and r["price"] > x  # noqa: E731
    ex, kept = [r for r in b if drop(r)], [r for r in b if not drop(r)]
    gain = lambda s: roi([r for r in s if not drop(r)]) - roi(s)  # noqa: E731
    res.update(
        {
            "x": x,
            "second_excluded": {
                "n": len(ex),
                "roi": roi(ex) if ex else None,
                "ci": cluster_boot(ex, roi) if ex else None,
            },
            "second_kept": {"n": len(kept), "roi": roi(kept), "ci": cluster_boot(kept, roi)},
            "second_all_roi": roi(b),
            "pnl_avoided": -sum(r["pl"] for r in ex),
            "roi_gain_points": gain(b),
            "roi_gain_ci": cluster_boot(b, gain),
            "p_boot_gain_le0": boot_p_le_zero(b, gain),
        }
    )
    return res


def fixed_rule_test(bets: list[dict], lo_price: float = 0.30) -> dict:
    """Second fixed rule, nothing tuned: exclude any bet priced below `lo_price` (fees are the largest share there)."""
    rows = sorted(bets, key=lambda r: r["placed"])
    b = rows[len(rows) // 2 :]
    ex, kept = [r for r in b if r["price"] < lo_price], [r for r in b if r["price"] >= lo_price]
    gain = lambda s: roi([r for r in s if r["price"] >= lo_price]) - roi(s)  # noqa: E731
    return {
        "second_excluded": {"n": len(ex), "roi": roi(ex) if ex else None, "ci": cluster_boot(ex, roi) if ex else None},
        "second_kept": {"n": len(kept), "roi": roi(kept), "ci": cluster_boot(kept, roi)},
        "roi_gain_points": gain(b),
        "roi_gain_ci": cluster_boot(b, gain),
    }


# --------------------------------------------------------------------------------------------------------------------
# part C
# --------------------------------------------------------------------------------------------------------------------
def team_total_no(bets: list[dict], after: dt.datetime) -> dict:
    tt = [r for r in bets if r["family"] == "TEAM_TOTAL" and r["side"] == "NO"]
    sub = [r for r in tt if r["placed"] > after]
    out = {"n_all": len(tt), "n_after_earlier_sample": len(sub)}
    for name, rs in (("all", tt), ("after", sub)):
        if len(rs) >= 3:
            wa, z, n = wins_above_price(rs)
            out[name] = {"n": n, "wins_above_price": wa, "z": z, "roi": roi(rs), "ci": cluster_boot(rs, roi)}
    if len(tt) >= 3:
        wa, _, n = wins_above_price(tt)
        edge = wa / n
        sd = math.sqrt(sum(r["price"] * (1 - r["price"]) for r in tt) / n)
        # bets for 80% power at the Bonferroni(8) two-sided alpha (z=2.73) if the observed per-bet edge were real
        weeks = max((max(r["placed"] for r in tt) - min(r["placed"] for r in tt)).days / 7, 1.0)
        out["power"] = {
            "edge_per_bet": edge,
            "bets_needed_80pct": math.ceil(((2.73 + 0.84) * sd / edge) ** 2) if edge > 0 else None,
            "bets_per_week": n / weeks,
        }
    return out


# --------------------------------------------------------------------------------------------------------------------
# extra tests (accuracy, game level; timing; incremental value over price)
# --------------------------------------------------------------------------------------------------------------------
def boot_dist(rows: list[dict], stat, reps: int = 3000, seed: int = 11) -> list[float]:
    by = defaultdict(list)
    for r in rows:
        by[r["game"]].append(r)
    gs = list(by.values())
    if len(gs) < 2:
        return []
    rnd = random.Random(seed)
    return sorted(stat([x for g in (rnd.choice(gs) for _ in gs) for x in g]) for _ in range(reps))


def boot_p_le_zero(rows: list[dict], stat) -> float:
    """One-sided bootstrap p that stat <= 0 (share of game-resampled replicates at or below zero)."""
    d = boot_dist(rows, stat)
    return sum(1 for x in d if x <= 0) / len(d) if d else float("nan")


def incremental_slope(rows: list[dict]) -> dict:
    """Does the model add anything to the price? OLS slope of (outcome - price) on (model - price), through the origin.
    0 = the price already contains what the model knows; 1 = the model is right and the price is wrong."""

    def slope(s):
        x = np.array([r["p_discrete"] - r["p_price"] for r in s])
        y = np.array([(1.0 if r["won"] else 0.0) - r["p_price"] for r in s])
        return float((x * y).sum() / (x * x).sum()) if (x * x).sum() else 0.0

    return {"n": len(rows), "slope": slope(rows), "ci": cluster_boot(rows, slope)}


def timing_table(bets: list[dict]) -> list[dict]:
    """ROI by when the bet was placed relative to scheduled kickoff (full-game spread / total / team total).
    In-play bets pay a price that contains the live game state."""
    cuts = (
        ("placed after kickoff", None, 0.0),
        ("0-3h before", 0.0, 3.0),
        ("3-24h before", 3.0, 24.0),
        ("over 24h before", 24.0, 1e9),
    )
    out = []
    pool = [r for r in bets if r["family"] in ("GAME_SPREAD", "GAME_TOTAL", "TEAM_TOTAL")]
    for name, lo, hi in cuts:

        def hrs(r):
            return (r["kickoff"] - r["placed"]).total_seconds() / 3600.0

        rs = [r for r in pool if (hrs(r) < 0 if lo is None else lo <= hrs(r) < hi and hrs(r) >= 0)]
        if len(rs) >= 5:
            wa, z, n = wins_above_price(rs)
            out.append(
                {"timing": name, "n": n, "roi": roi(rs), "ci": cluster_boot(rs, roi), "wins_above_price": wa, "z": z}
            )
    return out


def _team_history(team_rows: list[dict]) -> dict[str, list[dict]]:
    h = defaultdict(list)
    for r in team_rows:
        pb, bx = r.get("pbp") or {}, r.get("box") or {}
        scrim = pb.get("scrim_plays") or 0
        if not scrim or bx.get("plays") is None:
            continue
        h[r["team_id"]].append(
            {
                "fin": _ts(r["kickoff_utc"]) + dt.timedelta(hours=FINISH_HOURS),
                "plays": bx["plays"],
                "sr": pb["success_plays"] / scrim,
                "xr": ((pb.get("explosive_pass") or 0) + (pb.get("explosive_rush") or 0)) / scrim,
                "pf": r["points_for"],
                "pa": r["points_against"],
            }
        )
    for v in h.values():
        v.sort(key=lambda x: x["fin"])
    return h


def game_level_eval(games: list[dict], team_rows: list[dict], lam: float) -> dict:
    """Walk-forward, FBS-vs-FBS games with >= MIN_TRAIN_GAMES of history, split into dev weeks and held-out weeks:
    (1) squared error of margin / total: league-mean+home-field vs raw points-per-game (no opponent adjustment) vs
        ridge (opponent adjusted); (2) held-out log-likelihood of the integer margin: Normal vs Normal x key-number
        shape; (3) whether pace / success rate / explosive rate (season to date, point in time) explain the ridge
        residual. Intervals are game bootstraps."""
    walk, base = Walk(games, lam), Walk(games, NO_RATING_LAMBDA)
    ro, bo = walk.oos(), base.oos()
    hist = _team_history(team_rows)
    out = {"se": {}, "ll": {}, "cov": {}}
    rows = []
    for g, r, b in zip(games, ro, bo, strict=True):
        if not g["fbs2"] or r["n_train"] < MIN_TRAIN_GAMES or g["week"] < DEV_WEEKS[0]:
            continue
        cut = g["ko"]
        prior = {t: [x for x in hist.get(t, []) if x["fin"] <= cut] for t in (g["home"], g["away"])}
        pool = walk.pool_before(cut)
        sd_m = pooled_sd([x["hp"] - x["ap"] - x["pm"] for x in pool], "margin")
        sd_b = pooled_sd([x["hp"] - x["ap"] - bo[i]["pm"] for i, x in enumerate(pool)], "margin")
        key = len(pool)
        if key not in walk.shape_cache:
            walk.shape_cache[key] = shape_factors(pool, sd_m)
        m = round(g["hp"] - g["ap"])
        pn = margin_pmf(r["pm"], sd_m, None)
        pdc = margin_pmf(r["pm"], sd_m, walk.shape_cache[key])
        pb = margin_pmf(b["pm"], sd_b, None)
        hh, aa = prior[g["home"]], prior[g["away"]]
        raw = None
        if hh and aa and pool:
            lg = float(np.mean([x["hp"] + x["ap"] for x in pool])) / 2
            ph = np.mean([x["pf"] for x in hh]) + np.mean([x["pa"] for x in aa]) - lg
            pa = np.mean([x["pf"] for x in aa]) + np.mean([x["pa"] for x in hh]) - lg
            raw = (ph - pa + b["pm"], ph + pa)
        row = {
            "game": g["gid"],
            "week": g["week"],
            "dm": g["hp"] - g["ap"],
            "dt": g["hp"] + g["ap"],
            "ridge": (r["pm"], r["pt"]),
            "base": (b["pm"], b["pt"]),
            "raw": raw,
            "ll_n": math.log(max(pn.get(m, 0.0), 1e-9)),
            "ll_d": math.log(max(pdc.get(m, 0.0), 1e-9)),
            "ll_b": math.log(max(pb.get(m, 0.0), 1e-9)),
        }
        if len(hh) >= 2 and len(aa) >= 2:
            mean = lambda xs, k: float(np.mean([x[k] for x in xs]))  # noqa: E731
            row["cov"] = {
                "pace_sum": mean(hh, "plays") + mean(aa, "plays"),
                "sr_diff": mean(hh, "sr") - mean(aa, "sr"),
                "sr_sum": mean(hh, "sr") + mean(aa, "sr"),
                "xr_diff": mean(hh, "xr") - mean(aa, "xr"),
            }
        rows.append(row)
    for label, rs in (
        ("dev", [x for x in rows if x["week"] in DEV_WEEKS]),
        ("heldout", [x for x in rows if x["week"] not in DEV_WEEKS]),
    ):
        if not rs:
            continue
        rr = [x for x in rs if x["raw"] is not None]
        se = {"n": len(rs), "n_raw": len(rr)}
        for tgt, ix in (("margin", 0), ("total", 1)):
            key_ = "dm" if tgt == "margin" else "dt"

            def mse(rows_, k, ix=ix, key_=key_):
                return float(np.mean([(x[key_] - x[k][ix]) ** 2 for x in rows_]))

            def gain(a, b, ix=ix, key_=key_):
                return lambda s: float(np.mean([(x[key_] - x[b][ix]) ** 2 - (x[key_] - x[a][ix]) ** 2 for x in s]))

            se[tgt] = {
                "mse_base": mse(rs, "base"),
                "mse_ridge": mse(rs, "ridge"),
                "mse_raw": mse(rr, "raw") if rr else None,
                "ridge_vs_base_pct": 100 * gain("ridge", "base")(rs) / mse(rs, "base"),
                "ridge_vs_base_ci": cluster_boot(rs, gain("ridge", "base")),
                "ridge_vs_raw_gain": gain("ridge", "raw")(rr) if rr else None,
                "ridge_vs_raw_ci": cluster_boot(rr, gain("ridge", "raw")) if rr else None,
            }
        out["se"][label] = se
        for nm, a_, b_ in (
            ("discrete_minus_normal", "ll_d", "ll_n"),
            ("normal_ratings_minus_normal_norating", "ll_n", "ll_b"),
        ):
            f = lambda s, a_=a_, b_=b_: float(np.mean([x[a_] - x[b_] for x in s]))  # noqa: E731
            out["ll"].setdefault(label, {})[nm] = (f(rs), *cluster_boot(rs, f), len(rs))
    held = [x for x in rows if x["week"] not in DEV_WEEKS and "cov" in x]
    alls = [x for x in rows if "cov" in x]
    for name, tgt, resid in (
        ("pace_sum vs total residual", "pace_sum", "t"),
        ("sr_sum vs total residual", "sr_sum", "t"),
        ("sr_diff vs margin residual", "sr_diff", "m"),
        ("xr_diff vs margin residual", "xr_diff", "m"),
    ):

        def corr(s, tgt=tgt, resid=resid):
            x = np.array([q["cov"][tgt] for q in s])
            y = np.array([(q["dt"] - q["ridge"][1]) if resid == "t" else (q["dm"] - q["ridge"][0]) for q in s])
            return float(np.corrcoef(x, y)[0, 1]) if len(s) > 3 and x.std() > 0 and y.std() > 0 else 0.0

        for lab, rs in (("all_weeks_4plus", alls), ("heldout_7plus", held)):
            out["cov"][f"{name} [{lab}]"] = {"n": len(rs), "r": corr(rs), "ci": cluster_boot(rs, corr)}
    return out


def fee_by_price(contracts: int = 20) -> list[dict]:
    """Fee arithmetic at representative prices: per contract (unrounded), per order rounded up to the cent, share of
    the price paid, and the win rate that breaks even AT THE PRICE (price + fee), before any half-spread."""
    out = []
    for p in (0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90):
        f = fee_per_contract(p)
        out.append(
            {
                "price": p,
                "fee_c": 100 * f,
                "fee_c_rounded_order": 100 * fee_order(p, contracts) / contracts,
                "fee_pct_of_price": f / p,
                "breakeven_win_rate": p + f,
                "maker_fee_c": 100 * fee_per_contract(p, 0.0175),
            }
        )
    return out


def shape_effect(rows: list[dict]) -> dict:
    """How far the key-number shape moves the spread probability: mean |p_discrete - p_normal| in cents."""
    sp = [r for r in rows if r["family"] == "GAME_SPREAD"]
    d = [abs(r["p_discrete"] - r["p_normal"]) for r in sp]
    return {
        "n": len(sp),
        "mean_abs_c": 100 * float(np.mean(d)) if d else None,
        "max_abs_c": 100 * max(d) if d else None,
    }


def fee_formula_check(quote_glob: str) -> dict:
    """Largest gap between the exchange's own quoted taker fee per contract and 0.07 * ask * (1 - ask)."""
    worst, n = 0.0, 0
    for f in glob.glob(quote_glob):
        for m in json.load(open(f)).get("quotes", []):
            for side in ("yes", "no"):
                ask, fee = m.get(f"{side}_ask"), m.get(f"buy_{side}_fee")
                if ask is not None and fee is not None:
                    worst = max(worst, abs(fee - fee_per_contract(ask)))
                    n += 1
    return {"n": n, "max_abs_gap_dollars": worst}


# --------------------------------------------------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------------------------------------------------
def all_settled(wagers: str) -> list[dict]:
    """Every settled non-combo wager. ROI needs only price / stake / fees / profit_loss, so no join is required."""
    rows = []
    for w in json.load(open(wagers))["items"]:
        t = w["kalshi_ticker"]
        if w.get("settlement_status") != "SETTLED" or family(t) == "COMBO":
            continue
        rows.append(
            {
                "family": family(t),
                "side": w["selection"],
                "price": w["average_price"],
                "stake": w["stake"],
                "fee": w["fees"],
                "pl": w["profit_loss"],
                "won": w["profit_loss"] > 0,
                "game": (w.get("source_ids") or {}).get("kalshi_game_key") or t,
                "placed": _ts(w["placed_at"]),
            }
        )
    return rows


def run(wagers: str, team_games: str, schedule: str, quotes: str | None, after: str) -> dict:
    events = json.load(open(schedule))["events"]
    bets, diag = load_bets(wagers, events)
    games = build_games([json.loads(line) for line in open(team_games) if line.strip()])
    lam, curve = choose_lambda(games)
    scored, drop = score_bets(bets, games, lam)
    fbs = [r for r in scored if r["fbs2"]]
    res = {"diag": diag, "lambda": lam, "lambda_curve": curve, "n_scored": len(scored), "dropped": drop}
    res["A"] = {
        "fbs": compare(fbs, "FBS-vs-FBS spread+total"),
        "spread": compare([r for r in fbs if r["family"] == "GAME_SPREAD"], "FBS spread"),
        "total": compare([r for r in fbs if r["family"] == "GAME_TOTAL"], "FBS total"),
        "dev": compare([r for r in fbs if r["week"] in DEV_WEEKS], "dev weeks 4-6"),
        "heldout": compare([r for r in fbs if r["week"] not in DEV_WEEKS], "held-out weeks 7+"),
        "cal_price": calibration(fbs, "p_price"),
        "cal_discrete": calibration(fbs, "p_discrete"),
        "edge_split": edge_split(fbs),
        "descriptives": spread_descriptives(scored),
    }
    res["A"]["shape_effect"] = shape_effect(fbs)
    res["A"]["incremental_all"] = incremental_slope(fbs)
    res["A"]["incremental_spread"] = incremental_slope([r for r in fbs if r["family"] == "GAME_SPREAD"])
    res["A"]["game_level"] = game_level_eval(
        games, [json.loads(line) for line in open(team_games) if line.strip()], lam
    )
    settled = all_settled(wagers)
    res["B"] = {
        "timing": timing_table(bets),
        "fee_by_price": fee_by_price(),
        "bands_all": band_table(settled),
        "bands_game_spread": band_table([r for r in settled if r["family"] == "GAME_SPREAD"]),
        "rule_yes_spread": chronological_rule_test(settled),
        "rule_under30": fixed_rule_test(settled),
    }
    if quotes:
        res["B"]["fee_formula_check"] = fee_formula_check(quotes)
        q = quote_half_spreads(quotes)
        res["B"]["min_edge_spread"] = min_edge_table(q, "game_spread")
        res["B"]["min_edge_total"] = min_edge_table(q, "game_total")
    res["C"] = team_total_no(bets, _ts(after))
    return res


def _ci(ci):
    return f"[{ci[0]:+.1%}, {ci[1]:+.1%}]" if ci and not math.isnan(ci[0]) else "[n/a]"


def render(res: dict) -> str:
    d = res["diag"]
    L = [
        f"settled {d['settled']}; period/combo/other {d['period_combo_other']}; no game {d['no_game']}; "
        f"settlement disagrees {d['settlement_disagrees']}; joined {d['joined']}",
        f"ridge penalty {res['lambda']} (dev weeks {DEV_WEEKS}; curve {res['lambda_curve']}); "
        f"scored {res['n_scored']}; "
        f"dropped {res['dropped']}",
    ]
    A = res["A"]
    for k in ("fbs", "spread", "total", "dev", "heldout"):
        c = A[k]
        if not c["n"]:
            continue
        L.append(
            f"A[{c['name']}] n={c['n']} games={c['games']} hit={c['hit_rate']:.3f} price {c['mean_p_price']:.3f} "
            f"model {c['mean_p_discrete']:.3f}"
        )
        for m in ("brier", "logloss"):
            g = c[f"{m}_gain_discrete_vs_price"]
            L.append(
                f"   {m}: price {c[m + '_price']:.4f} fee-incl {c[m + '_fee']:.4f} norating {c[m + '_norating']:.4f} "
                f"normal {c[m + '_normal']:.4f} discrete {c[m + '_discrete']:.4f}; gain(discrete vs price) "
                f"{g[0]:+.4f} [{g[1]:+.4f}, {g[2]:+.4f}]; gain(normal) {c[m + '_gain_normal_vs_price'][0]:+.4f} "
                f"gain(norating) {c[m + '_gain_norating_vs_price'][0]:+.4f}"
            )
    L += ["calibration price: " + str(A["cal_price"]), "calibration model: " + str(A["cal_discrete"])]
    L += [f"  {e['name']}: n={e['n']} roi {e['roi']:+.1%} {_ci(e['ci'])}" for e in A["edge_split"]]
    L += [
        f"  {e['cut']}: n={e['n']} roi {e['roi']:+.1%} {_ci(e['ci'])} wap={e['wins_above_price']:+.1f} z={e['z']:+.2f}"
        for e in A["descriptives"]
    ]
    L.append(
        "incremental value of model over price (0 = none, 1 = model right): "
        + json.dumps({k: A[k] for k in ("incremental_all", "incremental_spread")})
    )
    L.append("game level: " + json.dumps(A["game_level"], default=str))
    B = res["B"]
    L.append("shape effect: " + json.dumps(A["shape_effect"]))
    L.append("fee by price: " + json.dumps(res["B"]["fee_by_price"]))
    L.append("timing: " + json.dumps(B["timing"], default=str))
    L.append("fee formula check: " + json.dumps(B.get("fee_formula_check")))
    for key in ("bands_all", "bands_game_spread"):
        L.append(key)
        L += [
            f"  {r['side']:3s} {r['band']} n={r['n']} roi {r['roi_net']:+.1%} {_ci(r['ci'])} "
            f"gross {r['roi_gross']:+.1%} "
            f"fees {r['fee_pct']:.1%} wap {r['wins_above_price']:+.1f} z={r['z']:+.2f}"
            for r in B[key]
        ]
    for key in ("min_edge_spread", "min_edge_total"):
        if key in B:
            L.append(key + ": " + json.dumps(B[key], default=str))
    L += [
        "rule YES-spread: " + json.dumps(B["rule_yes_spread"], default=str),
        "rule <30c: " + json.dumps(B["rule_under30"], default=str),
        "C: " + json.dumps(res["C"], default=str),
    ]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wagers", default="app/latest/wagers.json")
    ap.add_argument("--team-games", default="data/football/2026/team_games.jsonl")
    ap.add_argument("--schedule", default="data/football/2026/schedule.json")
    ap.add_argument("--quotes", default="data/live/execution/games/*/quotes.json")
    ap.add_argument("--after", default="2026-10-31T23:59:59Z", help="end of the earlier team-total sample (placed_at)")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    res = run(a.wagers, a.team_games, a.schedule, a.quotes, a.after)
    print(render(res))
    if a.json:
        json.dump(res, open(a.json, "w"), indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
