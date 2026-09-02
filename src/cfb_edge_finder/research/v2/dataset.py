"""Assemble the V2 game-level modeling table.

One row per completed FBS-involved game (plus the current season's
schedule with targets masked). Every feature column is computed from
information available BEFORE the game:

  * team state (state.py): opponent-adjusted efficiency/scoring strengths
    from games strictly before the game's (season, week);
  * preseason features (preseason.py): talent, recruiting, returning
    production, coaching change, prior-season strength/SP+, preseason poll
    -- each dated to season S-1 or earlier for season S;
  * situational: neutral, conference game, rest days, travel, altitude,
    kickoff, FCS opponent, postseason;
  * CFBD pregame Elo (as reported on the /games row -- timing-safe by
    construction, but treated as an EXTERNAL rating and evaluated
    separately);
  * market columns (EVALUATION ONLY, prefixed `mkt_`): consensus closing
    spread/total/moneyline from /lines. Never a model feature.

Targets: home_points, away_points, margin, total, home_won. For the
current season (2026) these are NaN by construction -- the builder
never reads 2026 outcomes.

Provenance: the output carries a `dataset_version`, the state config,
the cache manifest's fetched_at, and a content hash of the feature
columns so an experiment can pin exactly which table it ran on.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from cfb_edge_finder.ingestion.week_labels import UnclassifiablePostseasonError, derive_week_metadata
from cfb_edge_finder.modeling.leakage import postseason_week_rank
from cfb_edge_finder.research.v2.cache import V2Cache, registry_slug
from cfb_edge_finder.research.v2.preseason import build_preseason_table
from cfb_edge_finder.research.v2.state import FCS_ID, StateConfig, fit_state, side_rows
from cfb_edge_finder.research.v2.teamgames import build_team_game_table

DATASET_VERSION = "v2_dataset_v1"

STATE_METRICS = [
    "pts_for", "margin",
    "o_ppa", "o_sr", "o_expl", "o_pass_ppa", "o_rush_ppa", "o_pass_sr", "o_rush_sr", "o_sd_sr", "o_pd_sr",
    "o_line_yds", "o_plays", "o_ppa_ng", "o_sr_ng",
    "dr_pts_per_drive", "dr_ppo", "dr_to_rate", "dr_3out_rate", "dr_start_ytg", "dr_sec_per_play",
    "b_third_rate", "b_pen_yds", "b_havoc_def", "b_sack_rate_def", "b_takeaways",
]
"""Offense-side metrics per team-game. `pts_for`/`margin` give scoring
strengths; `b_havoc_def`, `b_sack_rate_def`, `b_takeaways` are DEFENSIVE
box-score outputs of the team (attached to its own row), so their
"offense" strength is really the team's defensive havoc and their
"defense" strength is the havoc its opponents generate against it."""


def _week_rank(raw: dict) -> int | None:
    st = raw.get("seasonType")
    if st == "postseason":
        try:
            meta = derive_week_metadata(season_type_raw=st, week_raw=raw.get("week"),
                                        postseason_descriptor=raw.get("notes") or raw.get("name"),
                                        playoff=raw.get("playoff"))
            return postseason_week_rank(meta.season_type, meta.cfp_round)
        except (UnclassifiablePostseasonError, ValueError):
            return 17
    return raw.get("week")


def games_frame(cache: V2Cache, seasons: list[int], *, current_season: int | None) -> pd.DataFrame:
    rows = []
    for s in seasons:
        for raw in cache.load(s, "games"):
            hc = (raw.get("homeClassification") or "").lower() or None
            ac = (raw.get("awayClassification") or "").lower() or None
            if hc != "fbs" and ac != "fbs":
                continue
            week = _week_rank(raw)
            if week is None:
                continue
            is_current = current_season is not None and s == current_season
            hp, ap = raw.get("homePoints"), raw.get("awayPoints")
            completed = bool(raw.get("completed")) and hp is not None and ap is not None
            if not is_current and not completed:
                continue
            start = raw.get("startDate")
            try:
                kickoff = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
            except ValueError:
                kickoff = None
            rows.append({
                "game_id": str(raw.get("id")), "season": s, "week": int(week),
                "season_type": raw.get("seasonType"), "kickoff": kickoff,
                "home": raw.get("homeTeam"), "away": raw.get("awayTeam"),
                "home_id_cfbd": raw.get("homeId"), "away_id_cfbd": raw.get("awayId"),
                "home_class": hc, "away_class": ac,
                "home_conf": raw.get("homeConference"), "away_conf": raw.get("awayConference"),
                "conference_game": bool(raw.get("conferenceGame")), "neutral": bool(raw.get("neutralSite")),
                "venue_id": raw.get("venueId"), "venue": raw.get("venue"),
                "home_pregame_elo": raw.get("homePregameElo"), "away_pregame_elo": raw.get("awayPregameElo"),
                # FIREWALL: current-season outcomes are never read
                "home_points": (None if is_current else int(hp)),
                "away_points": (None if is_current else int(ap)),
                "completed": (False if is_current else completed),
            })
    df = pd.DataFrame(rows)
    df["home_points"] = pd.to_numeric(df["home_points"], errors="coerce")
    df["away_points"] = pd.to_numeric(df["away_points"], errors="coerce")
    df["margin"] = df["home_points"] - df["away_points"]
    df["total"] = df["home_points"] + df["away_points"]
    df["home_won"] = np.where(df["margin"].isna(), np.nan, (df["margin"] > 0).astype(float))
    df["both_fbs"] = (df["home_class"] == "fbs") & (df["away_class"] == "fbs")
    df["postseason"] = df["season_type"] == "postseason"
    df["kickoff"] = pd.to_datetime(df["kickoff"], utc=True)
    df = df.sort_values(["season", "week", "kickoff", "game_id"]).reset_index(drop=True)
    return df


def market_frame(cache: V2Cache, seasons: list[int]) -> pd.DataFrame:
    """EVALUATION ONLY consensus closing lines per game."""
    rows = []
    for s in seasons:
        for r in cache.load(s, "lines_regular") + cache.load(s, "lines_postseason"):
            spreads, totals, hml, aml, opens = [], [], [], [], []
            for ln in r.get("lines") or []:
                if ln.get("spread") is not None:
                    spreads.append(float(ln["spread"]))
                if ln.get("overUnder") is not None:
                    totals.append(float(ln["overUnder"]))
                if ln.get("spreadOpen") is not None:
                    opens.append(float(ln["spreadOpen"]))
                if ln.get("homeMoneyline") is not None and ln.get("awayMoneyline") is not None:
                    hml.append(float(ln["homeMoneyline"]))
                    aml.append(float(ln["awayMoneyline"]))
            if not spreads and not totals:
                continue

            def implied(ml):
                return 100 / (ml + 100) if ml > 0 else -ml / (-ml + 100)

            p_home = None
            if hml:
                ph = np.median([implied(x) for x in hml])
                pa = np.median([implied(x) for x in aml])
                p_home = ph / (ph + pa)
            rows.append({
                "game_id": str(r.get("id")),
                # CFBD spread is the HOME line (negative = home favoured); model margin convention is home - away
                "mkt_spread_margin": -float(np.median(spreads)) if spreads else np.nan,
                "mkt_spread_open_margin": -float(np.median(opens)) if opens else np.nan,
                "mkt_total": float(np.median(totals)) if totals else np.nan,
                "mkt_p_home": p_home,
                "mkt_n_books": len(spreads),
            })
    return pd.DataFrame(rows).drop_duplicates("game_id") if rows else pd.DataFrame(columns=["game_id"])


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (lat1, lon1, lat2, lon2)):
        return float("nan")
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def team_locations(cache: V2Cache, seasons: list[int]) -> dict[tuple[int, str], dict]:
    out = {}
    for s in seasons:
        for t in cache.load(s, "teams_fbs"):
            loc = t.get("location") or {}
            out[(s, t.get("school"))] = {
                "lat": loc.get("latitude"), "lon": loc.get("longitude"),
                "elev": float(loc["elevation"]) if loc.get("elevation") not in (None, "") else None,
                "tz": loc.get("timezone"), "dome": loc.get("dome"), "venue_id": loc.get("id"),
                "conference": t.get("conference"),
            }
    return out


def venue_index(cache: V2Cache) -> dict[int, dict]:
    out = {}
    for v in cache.venues():
        out[v.get("id")] = {"lat": v.get("latitude"), "lon": v.get("longitude"),
                            "elev": float(v["elevation"]) if v.get("elevation") not in (None, "") else None,
                            "tz": v.get("timezone"), "dome": v.get("dome")}
    return out


def situational_features(games: pd.DataFrame, cache: V2Cache, seasons: list[int]) -> pd.DataFrame:
    locs = team_locations(cache, seasons)
    venues = venue_index(cache)
    g = games.copy()
    # rest days: days since each team's previous game (any classification), within season
    long = pd.concat([
        pd.DataFrame({"season": g.season, "team": g.home, "kickoff": g.kickoff, "game_id": g.game_id}),
        pd.DataFrame({"season": g.season, "team": g.away, "kickoff": g.kickoff, "game_id": g.game_id}),
    ]).sort_values(["team", "season", "kickoff"])
    long["prev_kick"] = long.groupby(["team", "season"])["kickoff"].shift(1)
    long["rest_days"] = (long["kickoff"] - long["prev_kick"]).dt.total_seconds() / 86400.0
    long["games_so_far"] = long.groupby(["team", "season"]).cumcount()
    rest = long.set_index(["game_id", "team"])[["rest_days", "games_so_far"]]
    out = pd.DataFrame(index=g.index)
    out["home_rest_days"] = [rest.at[(gid, t), "rest_days"] if (gid, t) in rest.index else np.nan
                             for gid, t in zip(g.game_id, g.home, strict=True)]
    out["away_rest_days"] = [rest.at[(gid, t), "rest_days"] if (gid, t) in rest.index else np.nan
                             for gid, t in zip(g.game_id, g.away, strict=True)]
    out["home_games_so_far"] = [rest.at[(gid, t), "games_so_far"] if (gid, t) in rest.index else 0
                                for gid, t in zip(g.game_id, g.home, strict=True)]
    out["away_games_so_far"] = [rest.at[(gid, t), "games_so_far"] if (gid, t) in rest.index else 0
                                for gid, t in zip(g.game_id, g.away, strict=True)]
    # venue geography
    dist_home, dist_away, elev, tzshift_away, dome = [], [], [], [], []
    for r in g.itertuples():
        hl = locs.get((r.season, r.home), {})
        al = locs.get((r.season, r.away), {})
        v = venues.get(r.venue_id) if r.venue_id is not None else None
        if v is None or v.get("lat") is None:
            v = hl if hl.get("lat") is not None else {}
        dist_home.append(_haversine_km(hl.get("lat"), hl.get("lon"), v.get("lat"), v.get("lon")) if hl else np.nan)
        dist_away.append(_haversine_km(al.get("lat"), al.get("lon"), v.get("lat"), v.get("lon")) if al else np.nan)
        elev.append(v.get("elev") if v.get("elev") is not None else np.nan)
        dome.append(1.0 if v.get("dome") else 0.0)
        tzshift_away.append(np.nan)
    out["home_travel_km"] = dist_home
    out["away_travel_km"] = dist_away
    out["venue_elev_m"] = elev
    out["venue_dome"] = dome
    out["kick_hour_utc"] = g.kickoff.dt.hour + g.kickoff.dt.minute / 60.0
    out["home_fcs_opp"] = (g.away_class != "fbs").astype(float)
    out["away_fcs_opp"] = (g.home_class != "fbs").astype(float)
    return out


@dataclass
class DatasetBuild:
    games: pd.DataFrame
    features: list[str]
    meta: dict = field(default_factory=dict)


def _state_feature_block(state, games_block: pd.DataFrame, metrics: list[str]) -> pd.DataFrame:
    """Home/away offense & defense strengths + games played for one as-of block."""
    hk = np.where(games_block.home_class == "fbs", games_block.home, FCS_ID)
    ak = np.where(games_block.away_class == "fbs", games_block.away, FCS_ID)
    off = state.offense.reindex(hk).fillna(0.0)
    off_a = state.offense.reindex(ak).fillna(0.0)
    de = state.defense.reindex(hk).fillna(0.0)
    de_a = state.defense.reindex(ak).fillna(0.0)
    cols = {}
    for m in metrics:
        cols[f"h_o_{m}"] = off[m].values
        cols[f"a_o_{m}"] = off_a[m].values
        cols[f"h_d_{m}"] = de[m].values
        cols[f"a_d_{m}"] = de_a[m].values
        cols[f"mu_{m}"] = state.mu[m]
        cols[f"hfa_{m}"] = state.hfa[m]
    cols["h_games_season"] = state.games_this_season.reindex(hk).fillna(0).values
    cols["a_games_season"] = state.games_this_season.reindex(ak).fillna(0).values
    cols["h_games_weighted"] = state.games_weighted.reindex(hk).fillna(0).values
    cols["a_games_weighted"] = state.games_weighted.reindex(ak).fillna(0).values
    return pd.DataFrame(cols, index=games_block.index)


def build_dataset(cache: V2Cache, *, seasons: list[int], current_season: int | None = None,
                  state_cfg: StateConfig | None = None, min_eval_season: int | None = None,
                  verbose: bool = True) -> DatasetBuild:
    state_cfg = state_cfg or StateConfig()
    hist_seasons = [s for s in seasons if current_season is None or s != current_season]
    all_seasons = list(seasons) + ([current_season] if current_season and current_season not in seasons else [])
    games = games_frame(cache, all_seasons, current_season=current_season)
    if verbose:
        print(f"games: {len(games)} rows, seasons {games.season.min()}-{games.season.max()}")
    tg = build_team_game_table(cache, hist_seasons)
    if verbose:
        print(f"team-game stats: {len(tg)} rows")
    # derived box metrics (own-row conventions: b_*_def are this team's DEFENSIVE outputs)
    if len(tg):
        tg["b_third_rate"] = tg["b_3d_conv"] / tg["b_3d_att"].replace(0, np.nan)
        tg["b_havoc_def"] = tg["b_tfl_def"].fillna(0) + tg["b_pd_def"].fillna(0) + tg["b_int_def"].fillna(0)
        tg["b_sack_rate_def"] = tg["b_sacks_def"]
        # takeaways = the opponent's turnovers committed in this game
        opp_to = tg[["game_id", "team", "b_turnovers"]].rename(
            columns={"team": "opponent", "b_turnovers": "b_takeaways"}
        )
        tg = tg.merge(opp_to, on=["game_id", "opponent"], how="left")

    long = side_rows(games[games.completed], tg, STATE_METRICS)
    # walk-forward state blocks
    feature_frames = []
    as_ofs = sorted({(s, w) for s, w in zip(games.season, games.week, strict=True)})
    first_eval = min_eval_season or (min(hist_seasons) + 1)
    for s, w in as_ofs:
        if s < first_eval:
            continue
        block = games[(games.season == s) & (games.week == w)]
        state = fit_state(long, STATE_METRICS, cutoff_season=s, cutoff_week=w, cfg=state_cfg)
        feature_frames.append(_state_feature_block(state, block, STATE_METRICS))
        if verbose and w in (1, 8):
            print(f"  state fitted as of {s} wk{w}: {len(state.teams)} teams, block {len(block)} games")
    state_feats = pd.concat(feature_frames) if feature_frames else pd.DataFrame(index=games.index)
    games = games.join(state_feats)
    games = games[games.season >= first_eval].copy()

    # preseason features
    pre = build_preseason_table(cache, games_all=games_frame(cache, all_seasons, current_season=current_season),
                                seasons=all_seasons, long_hist=long, state_cfg=state_cfg)
    pre_h = pre.add_prefix("h_pre_").rename(columns={"h_pre_season": "season", "h_pre_team": "home"})
    pre_a = pre.add_prefix("a_pre_").rename(columns={"a_pre_season": "season", "a_pre_team": "away"})
    games = games.merge(pre_h, on=["season", "home"], how="left").merge(pre_a, on=["season", "away"], how="left")

    sit = situational_features(games, cache, all_seasons)
    games = pd.concat([games, sit], axis=1)

    mkt = market_frame(cache, hist_seasons)
    games = games.merge(mkt, on="game_id", how="left")

    games["home_slug"] = [registry_slug(t, c) for t, c in zip(games.home, games.home_class, strict=True)]
    games["away_slug"] = [registry_slug(t, c) for t, c in zip(games.away, games.away_class, strict=True)]
    games["home_pregame_elo"] = pd.to_numeric(games["home_pregame_elo"], errors="coerce")
    games["away_pregame_elo"] = pd.to_numeric(games["away_pregame_elo"], errors="coerce")

    situational = (
        "neutral", "conference_game", "week", "postseason", "venue_elev_m", "venue_dome", "kick_hour_utc",
        "home_pregame_elo", "away_pregame_elo", "home_rest_days", "away_rest_days", "home_travel_km",
        "away_travel_km", "home_games_so_far", "away_games_so_far", "home_fcs_opp", "away_fcs_opp",
    )
    numeric = set(games.select_dtypes(include=["number", "bool"]).columns)
    feature_cols = [
        c for c in games.columns
        if (c.startswith(("h_", "a_", "mu_", "hfa_")) or c in situational) and c in numeric
    ]
    h = hashlib.sha256()
    h.update(pd.util.hash_pandas_object(games[feature_cols].astype(float).fillna(-999.0), index=False).values.tobytes())
    meta = {
        "dataset_version": DATASET_VERSION,
        "state_config": state_cfg.to_dict(),
        "state_metrics": STATE_METRICS,
        "seasons": all_seasons,
        "current_season": current_season,
        "cache_fetched_at": cache.manifest.get("fetched_at"),
        "n_games": int(len(games)),
        "n_features": len(feature_cols),
        "feature_hash": h.hexdigest()[:16],
        "built_at": datetime.utcnow().isoformat() + "Z",
    }
    return DatasetBuild(games=games.reset_index(drop=True), features=feature_cols, meta=meta)


def save_dataset(build: DatasetBuild, path) -> None:
    build.games.to_parquet(path, index=False)
    with open(str(path) + ".meta.json", "w") as fh:
        json.dump({**build.meta, "features": build.features}, fh, indent=2)
