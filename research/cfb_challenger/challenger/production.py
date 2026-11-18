"""Frozen production model (P0 = `0.5.0-early-season-talent-prior` as deployed) on identical games.

2014-2025: the committed Wave-2D/2E production replay (`p0_games_2014_2025.jsonl.gz`: live_eh/live_ea,
C.2 correction, talent delta, live_sigma -- the live object, built through production entry points).
2026: the archived P0 rows (`base_repair/games_2026.jsonl.gz`, weeks 1-6) extended to all 2026 games with the
same production object (`GameProjectionCache` on the 2022-2025 CFBD lines + 2026 talent; P0 never ingests
season-S rows, so its forecast for a game is fixed pre-season). Win probability is exactly the deployed pricer:
P(margin > 0) of a normal with the 0.5 continuity correction.
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

REPO = Path(__file__).resolve().parents[3]
P0_HIST = REPO / "data/scripting/validation/rush_projection/p0_games_2014_2025.jsonl.gz"
P0_2026 = REPO / "data/scripting/validation/base_repair/games_2026.jsonl.gz"


def win_prob(margin, sigma):
    return norm.sf((0.5 - np.asarray(margin, float)) / np.asarray(sigma, float))


def load_hist() -> pd.DataFrame:
    rows = []
    for x in gzip.open(P0_HIST):
        r = json.loads(x)
        if "live_eh" not in r or "live_sigma" not in r:
            continue
        raw = r["live_eh"] - r["live_ea"]
        c2 = 0.0 if (r["c2_identity"] or not r["fbs_vs_fbs"]) else r["c2_a"] * raw + r["c2_b"] - raw
        m = raw + c2 + r["talent_delta"]
        rows.append({"game_id": str(r["game_id"]), "prod_margin": m, "prod_total": r["live_eh"] + r["live_ea"],
                     "prod_sigma": r["live_sigma"], "p_home_pts": r["home_points"], "p_away_pts": r["away_points"]})
    return pd.DataFrame(rows)


def build_2026(game_rows: pd.DataFrame, cfbd_root: Path, preseason_parent: Path) -> pd.DataFrame:
    """game_rows: game_id, home slug id, away slug id, neutral, week (FBS-vs-FBS). Returns P0 forecasts."""
    sys.path.insert(0, str(REPO / "src"))
    sys.path.insert(0, str(REPO / "scripts"))
    from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache, GameProjectionRequest
    from cfb_edge_finder.modeling.corpus import build_team_game_lines
    import research_scan_and_capture as rsc

    cap = datetime(2026, 9, 3, tzinfo=UTC)
    prior = []
    for s in (2022, 2023, 2024, 2025):
        games = json.load(gzip.open(cfbd_root / str(s) / "games.json.gz"))
        adv = json.load(gzip.open(cfbd_root / str(s) / "advanced_regular.json.gz"))
        pf = cfbd_root / str(s) / "advanced_postseason.json.gz"
        if pf.exists():
            adv += json.load(gzip.open(pf))
        prior.extend(build_team_game_lines(games, adv, captured_at=cap)[0])
    talent, _ = rsc._load_talent_by_team(preseason_parent, 2026)  # noqa: SLF001
    cache = GameProjectionCache(prior, talent_by_team=talent)
    out = []
    for r in game_rows.itertuples():
        req = GameProjectionRequest(game_id=r.game_id, home_id=r.home_slug, away_id=r.away_slug,
                                    home_classification="fbs", away_classification="fbs",
                                    is_neutral_site=bool(r.neutral), as_of_season=2026, as_of_week=int(r.week),
                                    n_simulations=6000, seed=0)
        try:
            p = cache.get_or_build(req).projection
        except Exception:  # noqa: BLE001
            continue
        d = p.to_game_distribution()
        sig = float(np.sqrt(d.home_sd**2 + d.away_sd**2 - 2 * d.correlation * d.home_sd * d.away_sd))
        out.append({"game_id": r.game_id, "prod_margin": p.expected_margin, "prod_total": p.expected_total,
                    "prod_sigma": sig})
    return pd.DataFrame(out)


def load_archived_2026() -> pd.DataFrame:
    rows = [json.loads(x) for x in gzip.open(P0_2026)]
    return pd.DataFrame([{"game_id": str(r["game_id"]), "home_slug": r["home_id"], "away_slug": r["away_id"],
                          "neutral": r["neutral_site"], "week": r["week"], "prod_margin": r["p0_margin"],
                          "prod_total": r["p0_total"], "prod_sigma": r["p0_sigma"], "p_home_pts": r["home_points"],
                          "p_away_pts": r["away_points"]} for r in rows])


def espn_to_slug(games: pd.DataFrame) -> dict[str, str]:
    """ESPN team id -> production slug, matched on game id + final score (orientation-safe)."""
    srcs = [json.loads(x) for x in gzip.open(P0_HIST)] + [json.loads(x) for x in gzip.open(P0_2026)]
    g = games.set_index("game_id")
    votes: dict[str, dict[str, int]] = {}
    for r in srcs:
        gid = str(r["game_id"])
        if gid not in g.index:
            continue
        e = g.loc[gid]
        if (e.home_pts, e.away_pts) == (r["home_points"], r["away_points"]):
            pairs = ((e.home, r["home_id"]), (e.away, r["away_id"]))
        elif (e.home_pts, e.away_pts) == (r["away_points"], r["home_points"]):
            pairs = ((e.home, r["away_id"]), (e.away, r["home_id"]))
        else:
            continue
        for eid, slug in pairs:
            votes.setdefault(eid, {}).setdefault(slug, 0)
            votes[eid][slug] += 1
    return {k: max(v, key=v.get) for k, v in votes.items()}


def load_b1_2025() -> pd.DataFrame:
    """Reference only: production stack WITH current-season ingestion (Wave-2E 'B1'), 2025 replay rows."""
    rows = []
    for x in gzip.open(P0_HIST):
        r = json.loads(x)
        if r["season"] != 2025 or "in_eh" not in r or "in_sigma" not in r:
            continue
        raw = r["in_eh"] - r["in_ea"]
        c2 = 0.0 if (r["c2_identity"] or not r["fbs_vs_fbs"]) else r["c2_a"] * raw + r["c2_b"] - raw
        rows.append({"game_id": str(r["game_id"]), "b1_margin": raw + c2 + r["talent_delta"],
                     "b1_total": r["in_eh"] + r["in_ea"], "b1_sigma": r["in_sigma"],
                     "b1_home_pts": r["home_points"], "b1_away_pts": r["away_points"]})
    return pd.DataFrame(rows)
