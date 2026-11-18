"""Walk-forward opponent-adjusted ratings (numpy only), a line-for-line port of research #307/#308.

Ratings for a game on local date d are a weighted ridge fit on team-games of the two prior seasons (weights
w_prev2, w_prev) and of the current season dated strictly before d. `training_rows` additionally drops any row
that kicked off at/after the forecast time and asserts the pregame invariant.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import numpy as np

METRICS = ("pts", "ppd", "sr", "ypp", "plays", "drives")
PERIODS = ("q1", "q2", "q3", "q4", "h1", "h2")
PMETRICS = tuple(f"pp_{p}" for p in PERIODS)
FCS = "FCS"
LOCAL_OFFSET = timedelta(hours=6)          # research: US local game day = UTC - 6 h

COMPACT_KEYS = ("game_id", "season", "date", "kickoff_utc", "team_id", "opponent_id", "site", "team_division",
                "opponent_division", "points_for", "points_against", "box_plays", "box_total_yards",
                "pbp_success_plays", "pbp_scrim_plays", "pbp_drives", "pbp_drive_points",
                "pp_q1", "pp_q2", "pp_q3", "pp_q4")


class PregameViolation(AssertionError):
    pass


def parse_utc(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(UTC)


def local_date(kickoff_utc: str) -> str:
    return (parse_utc(kickoff_utc) - LOCAL_OFFSET).date().isoformat()


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def compact_from_log(raw: dict) -> dict:
    """One `cfb_team_game/1.0.0` row (data/football/<season>/team_games.jsonl) -> compact rating row.

    Period points are kept only when there are >= 4 periods summing to the final score (research `valid` rule;
    `load_log_rows` also requires both sides to report the same number of periods)."""
    box, pbp = raw.get("box") or {}, raw.get("pbp") or {}
    out = {"game_id": str(raw["game_id"]), "season": int(raw["season"]), "date": local_date(raw["kickoff_utc"]),
           "kickoff_utc": str(raw["kickoff_utc"]), "team_id": str(raw["team_id"]),
           "opponent_id": str(raw["opponent_id"]), "site": raw.get("site"),
           "team_division": raw.get("team_division"), "opponent_division": raw.get("opponent_division"),
           "points_for": _num(raw.get("points_for")), "points_against": _num(raw.get("points_against")),
           "box_plays": _num(box.get("plays")), "box_total_yards": _num(box.get("total_yards")),
           "pbp_success_plays": _num(pbp.get("success_plays")), "pbp_scrim_plays": _num(pbp.get("scrim_plays")),
           "pbp_drives": _num(pbp.get("drives")), "pbp_drive_points": _num(pbp.get("drive_points"))}
    pp = [_num(x) for x in (raw.get("period_points") or [])]
    ok = (len(pp) >= 4 and all(v is not None for v in pp) and out["points_for"] is not None
          and abs(sum(pp) - out["points_for"]) < 1e-9)
    for q in range(4):
        out[f"pp_q{q + 1}"] = pp[q] if ok else None
    out["_n_periods"] = len(pp)
    return out


def load_log_rows(raw_rows: Iterable[dict]) -> list[dict]:
    """Compact rows from the repo ESPN game log; de-duplicated on (game_id, team_id), last observation wins."""
    by_key = {}
    for r in raw_rows:
        c = compact_from_log(r)
        by_key[(c["game_id"], c["team_id"])] = c
    for (g, _t), c in by_key.items():
        o = by_key.get((g, c["opponent_id"]))
        if o is None or o["_n_periods"] != c["_n_periods"] or o["pp_q1"] is None:
            for q in range(4):
                c[f"pp_q{q + 1}"] = None
    rows = []
    for c in by_key.values():
        c.pop("_n_periods", None)
        rows.append(c)
    return rows


def training_rows(rows: list[dict], season: int, game_date: str, now_utc: datetime) -> list[dict]:
    """Rows a forecast for `season`/`game_date` may use: seasons S-2, S-1 and season-S rows dated before
    `game_date`, never a row whose kickoff is at/after `now_utc`. Raises PregameViolation if that is not so."""
    base = [r for r in rows if r["season"] in (season - 2, season - 1)]
    cur = [r for r in rows if r["season"] == season and r["date"] < game_date]
    out = [r for r in base + cur if parse_utc(r["kickoff_utc"]) < now_utc]
    for r in out:
        if not (r["date"] < game_date and parse_utc(r["kickoff_utc"]) < now_utc):
            raise PregameViolation(f"team-game {r['game_id']}/{r['team_id']} is not strictly before the forecast")
    return out


def _metric_arrays(rows: list[dict]) -> dict[str, np.ndarray]:
    def col(k):
        return np.array([np.nan if r.get(k) is None else float(r[k]) for r in rows], float)

    plays, yards = col("box_plays"), col("box_total_yards")
    scrim, succ = col("pbp_scrim_plays"), col("pbp_success_plays")
    drives, dpts = col("pbp_drives"), col("pbp_drive_points")
    with np.errstate(invalid="ignore", divide="ignore"):
        m = {"pts": col("points_for"), "plays": plays,
             "ypp": yards / np.where(plays > 0, plays, np.nan),
             "sr": succ / np.where(scrim > 0, scrim, np.nan),
             "drives": drives, "ppd": dpts / np.where(drives > 0, drives, np.nan)}
    q = {f"q{i}": col(f"pp_q{i}") for i in range(1, 5)}
    for p in ("q1", "q2", "q3", "q4"):
        m[f"pp_{p}"] = q[p]
    m["pp_h1"] = q["q1"] + q["q2"]
    m["pp_h2"] = q["q3"] + q["q4"]
    return m


def _spd_solve(A: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cholesky solve with level-1/2 numpy ops only: multi-threaded LAPACK is pathologically slow on these ~280-dim
    systems on small runners (1 s per solve with 4 OpenBLAS threads vs 1 ms single-threaded)."""
    n = A.shape[0]
    L = np.zeros_like(A)
    for j in range(n):
        v = A[j:, j] - L[j:, :j] @ L[j, :j]
        L[j, j] = np.sqrt(v[0])
        L[j + 1:, j] = v[1:] / L[j, j]
    y = np.zeros(n)
    for i in range(n):
        y[i] = (b[i] - L[i, :i] @ y[:i]) / L[i, i]
    x = np.zeros(n)
    for i in range(n - 1, -1, -1):
        x[i] = (y[i] - L[i + 1:, i] @ x[i + 1:]) / L[i, i]
    return x


def fit_ratings(rows: list[dict], weights: np.ndarray, lam: float, metrics=METRICS + PMETRICS) -> dict:
    """Weighted ridge y = mu + hfa*hflag + O[off] - D[dfn] per metric (FCS pooled as one entity)."""
    off = [r["team_id"] if r["team_division"] == "fbs" else FCS for r in rows]
    dfn = [r["opponent_id"] if r["opponent_division"] == "fbs" else FCS for r in rows]
    teams = sorted(set(off) | set(dfn))
    ix = {t: i for i, t in enumerate(teams)}
    T, n, P = len(teams), len(rows), 2 * len(teams) + 2
    hflag = np.array([1.0 if r["site"] == "home" else (-1.0 if r["site"] == "away" else 0.0) for r in rows])
    # sparse design: each row is e_0 + hflag e_1 + e_(2+O[off]) - e_(2+T+D[dfn]); normal equations by bincount
    # (identical maths to the dense research solve, no multi-threaded BLAS on a 5000 x 280 product)
    idx = np.c_[np.zeros(n, int), np.ones(n, int), 2 + np.array([ix[t] for t in off], dtype=int),
                2 + T + np.array([ix[t] for t in dfn], dtype=int)]
    val = np.c_[np.ones(n), hflag, np.ones(n), -np.ones(n)]
    pair = (idx[:, :, None] * P + idx[:, None, :]).ravel()
    vv = (val[:, :, None] * val[:, None, :]).reshape(n, -1)
    pen = np.diag(np.r_[0.0, 0.0, np.full(2 * T, lam)]) + 1e-9 * np.eye(P)
    vals = _metric_arrays(rows)
    out = {}
    for m in metrics:
        y = vals[m]
        ok = np.isfinite(y)
        w = weights * ok
        G = np.bincount(pair, weights=(vv * w[:, None]).ravel(), minlength=P * P).reshape(P, P)
        rhs = np.bincount(idx.ravel(), weights=(val * (w * np.where(ok, y, 0.0))[:, None]).ravel(), minlength=P)
        beta = _spd_solve(G + pen, rhs)
        out[m] = {"mu": float(beta[0]), "hfa": float(beta[1]), "O": dict(zip(teams, beta[2:2 + T], strict=True)),
                  "D": dict(zip(teams, beta[2 + T:], strict=True))}
    return out


def rate_date(rows: list[dict], season: int, game_date: str, now_utc: datetime, params: dict) -> tuple[dict, dict, int]:
    """(ratings, games played per team this season before the date, number of training rows)."""
    tr = training_rows(rows, season, game_date, now_utc)
    w = np.array([1.0 if r["season"] == season else (params["w_prev"] if r["season"] == season - 1
                                                     else params["w_prev2"]) for r in tr], float)
    R = fit_ratings(tr, w, params["lam"])
    gp: dict[str, int] = {}
    for r in tr:
        if r["season"] == season:
            gp[r["team_id"]] = gp.get(r["team_id"], 0) + 1
    return R, gp, len(tr)


def game_features(R: dict, gp: dict, preseason: dict, home: str, away: str, home_fbs: bool, away_fbs: bool,
                  neutral: bool) -> dict:
    hk, ak = (home if home_fbs else FCS), (away if away_fbs else FCS)
    hf = 0.0 if neutral else 1.0
    f = {"hf": hf}
    for m, r in R.items():
        f[f"{m}_h"] = r["mu"] + r["hfa"] * hf + r["O"].get(hk, 0.0) - r["D"].get(ak, 0.0)
        f[f"{m}_a"] = r["mu"] - r["hfa"] * hf + r["O"].get(ak, 0.0) - r["D"].get(hk, 0.0)
    for side, team in (("h", home), ("a", away)):
        tz, rz = preseason.get(team, (0.0, 0.0))
        f[f"tal_{side}"], f[f"ret_{side}"], f[f"n_{side}"] = tz, rz, gp.get(team, 0)
    return f
