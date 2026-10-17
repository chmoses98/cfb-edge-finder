"""Independent regression tests for the CFB defensive ratings (cfb/defense_ratings.py).

Every test builds a synthetic league with a KNOWN truth (seeded, small noise) so the claim being checked is
"the rating recovers / does not mislead", not "the code agrees with itself". A final group checks equivalence with
the frozen walk-forward ridge (pf_core.pit.rolling_ridge_ratings via cfb.ratings), so the shadow rating IS the
validated rating, and the published-file schema.
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from cfb import defense_ratings as dr  # noqa: E402
from cfb import ratings  # noqa: E402

BASE = 28.0


def _row(gid, season, week, a, b, off, de, site, rng, noise, div=("fbs", "fbs"), exact=False):
    """Two team-game rows (offence view) for one game a (home, site=+1) vs b."""
    ko = pd.Timestamp(f"{season}-09-01", tz="UTC") + pd.Timedelta(days=7 * week)
    out = []
    for t, o, s, dt, do in ((a, b, site, div[0], div[1]), (b, a, -site, div[1], div[0])):
        mu = BASE + off[t] + de[o] + 1.5 * s
        out.append({"game_id": gid, "season": season, "week": week, "block": season * 100 + week, "kickoff": ko,
                    "team_id": t, "opp_id": o, "team_div": dt, "opp_div": do, "site": s, "post": False,
                    "pts": mu if exact else mu + rng.normal(0, noise)})
    return out


def _league(n=40, seed=0, off_sd=4.0, def_sd=4.0):
    rng = np.random.default_rng(seed)
    ids = [f"t{i}" for i in range(n)]
    off = dict(zip(ids, rng.normal(0, off_sd, n)))
    de = dict(zip(ids, rng.normal(0, def_sd, n)))
    return ids, off, de, rng


def _cfg(**kw):
    base = dict(alpha=1.0, decay=0.2, history=1, n_draws=1500)
    base.update(kw)
    return dr.DefenseConfig(**base)


def _fit(T, season=2020, block=None, **kw):
    block = block or int(T[T.season == season]["block"].max()) + 1
    return dr.fit(T, season, block, _cfg(**kw))


# ----------------------------------------------------------------------------- (a) schedule adjustment
def test_defence_that_only_faced_weak_offences_is_not_ranked_elite():
    """X (true defence = average) played five games against the five worst offences; E (true defence = elite) played
    strong offences. Raw points allowed rank X above E; the schedule-adjusted rating must not."""
    ids, off, de, rng = _league(40, seed=1, off_sd=2.0, def_sd=2.0)
    weak = [f"t{i}" for i in range(1, 11)]
    strong = [f"t{i}" for i in range(11, 21)]
    for t in weak:
        off[t] = -9.0
    for t in strong:
        off[t] = 6.0
    de["X"], de["E"], off["X"], off["E"] = 0.0, -7.0, 0.0, 0.0
    ids = ids + ["X", "E"]
    rows, gid = [], 0
    for w in range(1, 6):
        gid += 1
        rows += _row(gid, 2020, w, "X", weak[w - 1], off, de, 1 if w % 2 else -1, rng, 3.0)
        gid += 1
        rows += _row(gid, 2020, w, "E", strong[w - 1], off, de, 1 if w % 2 else -1, rng, 3.0)
        rest = [t for t in ids if t not in ("X", "E", weak[w - 1], strong[w - 1])]
        rng.shuffle(rest)
        for a, b in zip(rest[::2], rest[1::2]):
            gid += 1
            rows += _row(gid, 2020, w, a, b, off, de, 1, rng, 3.0)
    T = pd.DataFrame(rows)
    # the schedule-blind comparator really is fooled
    raw = T.groupby("opp_id")["pts"].mean().rank()
    assert raw["X"] < raw["E"], "construction check: raw points allowed should flatter X"
    f = _fit(T, history=0, alpha=0.5)
    tab = dr.rank_table(f).set_index("team_id")
    assert tab.loc["E", "rank_point"] < tab.loc["X", "rank_point"]
    # X is not elite: nowhere near the top 10% of 42 defences, and its rating is close to its true value
    assert tab.loc["X", "rank_point"] > 8
    assert abs(f.frame().set_index("team_id").loc["X", "def_rating"] - 0.0) < 3.0
    raw_gap = T[T.opp_id == "X"]["pts"].mean() - BASE
    assert abs(f.frame().set_index("team_id").loc["X", "def_rating"]) < abs(raw_gap)


# ----------------------------------------------------------------------------- (b) disconnected schedule graph
def _two_cluster_games(weeks, bridge_week=None, seed=3, n=40):
    ids, off, de, rng = _league(n, seed=seed)
    A, B = ids[: n // 2], ids[n // 2:]
    rows, gid = [], 0
    for w in range(1, weeks + 1):
        for grp in (A, B):
            perm = list(rng.permutation(grp))
            for a, b in zip(perm[::2], perm[1::2]):
                gid += 1
                rows += _row(gid, 2020, w, a, b, off, de, 1, rng, 3.0)
    if bridge_week:
        perm_a, perm_b = list(rng.permutation(A)), list(rng.permutation(B))
        for a, b in zip(perm_a, perm_b):
            gid += 1
            rows += _row(gid, 2020, bridge_week, a, b, off, de, 1, rng, 3.0)
    return pd.DataFrame(rows), ids


def test_components_are_counted_and_ranks_suppressed_while_disconnected():
    T, ids = _two_cluster_games(weeks=3)
    # cluster games are random pairings each week, so within-cluster connectivity is probable but never complete across
    comp = dr.schedule_components(T, ids)
    assert comp["n_components"] >= 2
    f = _fit(T, history=0)
    assert f.components["n_components"] == comp["n_components"]
    tab = dr.rank_table(f)
    assert f.median_fbs_games == 3 < f.config.min_median_games
    assert (tab["rank_status"] == dr.RANK_SUPPRESSED).all()
    assert tab["rank"].isna().all()
    # the rating and its uncertainty band stay visible
    assert tab["rank_point"].notna().all() and (tab["rank_hi"] >= tab["rank_lo"]).all()
    assert (tab["rank_hi"] - tab["rank_lo"]).mean() > 8
    assert not dr.league_summary(f)["ranks_published"]


def test_one_game_per_team_has_one_component_per_game_and_fcs_does_not_bridge():
    rng = np.random.default_rng(0)
    ids, off, de, _ = _league(10, seed=4)
    rows = []
    for k in range(0, 8, 2):                            # four FBS-vs-FBS games: t0-t1, t2-t3, t4-t5, t6-t7
        rows += _row(k, 2020, 1, ids[k], ids[k + 1], off, de, 1, rng, 3.0)
    # t8 and t9 each play the SAME FCS team: the pooled FCS node must not connect them
    for t in ("t8", "t9"):
        rows += _row(100 + int(t[1]), 2020, 1, t, "fcs1", {**off, "fcs1": -9}, de | {"fcs1": 0}, 1, rng, 3.0, div=("fbs", "fcs"))
    T = pd.DataFrame(rows)
    comp = dr.schedule_components(T, ids)
    assert comp["n_components"] == 6                    # 4 FBS pairs + t8 + t9 (isolated in the FBS graph)
    assert comp["largest"] == 2


def test_ranks_published_once_connected_and_flagged_provisional_before_that():
    T, ids = _two_cluster_games(weeks=3, bridge_week=4)
    f = _fit(T, history=0)
    assert f.components["n_components"] == 1
    tab = dr.rank_table(f)
    assert tab["rank"].notna().all()
    assert set(tab["rank_status"]) <= {dr.RANK_OK, dr.RANK_PROVISIONAL}
    assert dr.league_summary(f)["ranks_published"]
    # many weeks inside the clusters, no bridge: the median-games rule lifts the league suppression, but the
    # teams outside the main component stay withheld and the rest are provisional (never OK)
    T8, ids8 = _two_cluster_games(weeks=8)
    f8 = _fit(T8, history=0)
    t8 = dr.rank_table(f8)
    if f8.components["n_components"] > 1:
        assert f8.median_fbs_games >= f8.config.min_median_games
        assert (t8.loc[~t8["in_main_component"], "rank_status"] == dr.RANK_SUPPRESSED).all()
        assert (t8.loc[t8["in_main_component"], "rank_status"] == dr.RANK_PROVISIONAL).all()


# ----------------------------------------------------------------------------- (c) FCS pooling
def test_fcs_opponent_does_not_flatter_a_defence_when_pooled():
    ids, off, de, rng = _league(40, seed=5, off_sd=2.0, def_sd=2.0)
    de["D1"], de["D2"], off["D1"], off["D2"] = 0.0, 0.0, 0.0, 0.0
    ids = ids + ["D1", "D2"]
    fcs = [f"f{i}" for i in range(8)]
    for t in fcs:
        off[t], de[t] = -10.0, 8.0
    rows, gid = [], 0
    for w in range(1, 7):
        rest = [t for t in ids if t not in ("D1",)] if w == 1 else list(ids)
        rng.shuffle(rest)
        if w == 1:
            gid += 1
            rows += _row(gid, 2020, w, "D1", fcs[0], off, de, 1, rng, 0.3, div=("fbs", "fcs"))
        for a, b in zip(rest[::2], rest[1::2]):
            gid += 1
            rows += _row(gid, 2020, w, a, b, off, de, 1, rng, 0.3)
        if w <= 3:                                    # every other FCS team plays one FBS team early (pooled rating is well identified)
            others = [t for t in rest if t not in ("D1", "D2")]
            gid += 1
            rows += _row(gid, 2020, w, others[0], fcs[w], off, de, 1, rng, 0.3, div=("fbs", "fcs"))
            gid += 1
            rows += _row(gid, 2020, w, others[1], fcs[w + 3], off, de, 1, rng, 0.3, div=("fbs", "fcs"))
    T = pd.DataFrame(rows)
    gaps = {}
    for mode in ("pool", "individual"):
        f = _fit(T, history=0, alpha=4.0, fcs_mode=mode)
        r = f.frame().set_index("team_id")["def_rating"]
        gaps[mode] = r["D2"] - r["D1"]               # > 0 means D1 (the FCS-opponent defence) looks better than its twin
    # an individually-rated FCS team with one game is shrunk toward average, so the FCS blow-out credits D1
    assert gaps["individual"] > 0.5
    assert abs(gaps["pool"]) < 0.5 * gaps["individual"]
    f = _fit(T, history=0, alpha=4.0, fcs_mode="pool")
    tab = dr.rank_table(f).set_index("team_id")
    assert bool(tab.loc["D1", "fcs_pooled"]) and not bool(tab.loc["D2", "fcs_pooled"])
    assert tab.loc["D1", "fcs_games_current"] == 1


# ----------------------------------------------------------------------------- (d) conference offsets
def _conf_league(seed=6):
    rng = np.random.default_rng(seed)
    A = [f"a{i}" for i in range(16)]
    B = [f"b{i}" for i in range(16)] + ["Z"]
    off = {t: rng.normal(0, 2.0) for t in A + B}
    de = {t: rng.normal(0, 2.0) + (0.0 if t[0] == "a" else 8.0) for t in A + B}      # conference B allows +8
    de["Z"] = 8.0
    conf = {t: ("A" if t[0] == "a" else "B") for t in A + B}
    rows, gid = [], 0
    for w in range(1, 7):
        for grp in (A, [t for t in B if t != "Z"]):
            perm = list(rng.permutation(grp))
            for a, b in zip(perm[::2], perm[1::2]):
                gid += 1
                rows += _row(gid, 2020, w, a, b, off, de, 1, rng, 3.0)
        if w <= 2:                                    # non-conference games identify the conference levels
            pa, pb = list(rng.permutation(A))[:8], list(rng.permutation([t for t in B if t != "Z"]))[:8]
            for a, b in zip(pa, pb):
                gid += 1
                rows += _row(gid, 2020, w, a, b, off, de, 1, rng, 3.0)
    gid += 1
    rows += _row(gid, 2020, 6, "Z", "a0", off, de, 1, rng, 0.0, exact=True)         # Z: ONE noiseless game, vs an A team
    return pd.DataFrame(rows), conf, de


def test_conference_offsets_shrink_a_thin_defence_toward_its_conference():
    T, conf, de = _conf_league()
    league = np.mean([v for t, v in de.items() if t != "Z"])
    truth_Z = de["Z"] - league                          # Z's true level relative to the league mean
    f0 = _fit(T, history=0, alpha=3.0)
    f1 = dr.fit(T, 2020, int(T["block"].max()) + 1, _cfg(history=0, alpha=3.0, conf_alpha=0.5, n_draws=500), conf_of=conf)
    z0 = f0.frame().set_index("team_id").loc["Z"]
    z1 = f1.frame().set_index("team_id").loc["Z"]
    assert z0["games_current"] == 1
    # without offsets Z is pulled toward the FBS mean (0); with them toward its (weak) conference
    assert abs(z1["def_rating"] - truth_Z) < abs(z0["def_rating"] - truth_Z) - 0.5
    assert z1["def_rating"] > z0["def_rating"] + 0.5      # i.e. rated as a worse defence than the unconditional shrinkage says
    # full-fit sanity: conference offsets do not wreck well-observed teams
    r0 = f0.frame().set_index("team_id")["def_rating"]
    r1 = f1.frame().set_index("team_id")["def_rating"]
    common = [t for t in r0.index if t not in ("Z", dr.POOL)]
    assert np.corrcoef(r0[common], r1[common])[0, 1] > 0.9


# ----------------------------------------------------------------------------- (e) uncertainty
def _season_games(weeks, season, seed, ids, off, de):
    rng = np.random.default_rng(seed)
    rows, gid = [], season * 1000
    for w in range(1, weeks + 1):
        perm = list(rng.permutation(ids))
        for a, b in zip(perm[::2], perm[1::2]):
            gid += 1
            rows += _row(gid, season, w, a, b, off, de, 1, rng, 8.0)
    return rows


def test_uncertainty_shrinks_with_current_games_and_grows_with_carry_share():
    ids, off, de, _ = _league(40, seed=7)
    rows = _season_games(12, 2019, 11, ids, off, de) + _season_games(12, 2020, 12, ids, off, de)
    T = pd.DataFrame(rows)
    se, carry, eff = {}, {}, {}
    for w in (1, 3, 6, 12):
        f = dr.fit(T, 2020, 2020 * 100 + w + 1, _cfg(history=1, decay=0.2, alpha=1.0, n_draws=200))
        fr = f.frame().set_index("team_id")
        se[w], carry[w], eff[w] = fr["se_def"].mean(), fr["carry_share"].mean(), fr["eff_games"].mean()
    assert se[1] > se[3] > se[6] > se[12]                # more games -> tighter
    assert carry[1] > carry[3] > carry[6] > carry[12]    # and less of the information is carried
    assert eff[1] < eff[3] < eff[6] < eff[12]
    # the same total number of games, but more of them carried (down-weighted): wider
    T_cur = pd.DataFrame(_season_games(6, 2020, 12, ids, off, de))
    T_all_cur = T_cur
    a = dr.fit(T_all_cur, 2020, 2020 * 100 + 7, _cfg(history=1, alpha=1.0, n_draws=200)).frame()          # 6 current games, no prior
    a = a[a["team_id"] != dr.POOL]
    prior = pd.DataFrame(_season_games(4, 2019, 11, ids, off, de))
    two = pd.concat([prior, pd.DataFrame(_season_games(2, 2020, 12, ids, off, de))])
    b = dr.fit(two, 2020, 2020 * 100 + 3, _cfg(history=1, alpha=1.0, n_draws=200)).frame()                # 2 current + 4 prior
    b = b[b["team_id"] != dr.POOL]
    assert a["games_used"].mean() == b["games_used"].mean() == 6
    assert b["carry_share"].mean() > a["carry_share"].mean()
    assert b["se_def"].mean() > a["se_def"].mean()


def test_posterior_intervals_and_rank_bands_are_calibrated_when_the_model_holds():
    """Draw truths from the ridge prior (so the model is exactly right), simulate seasons, and check the 80% rating
    interval and the 80% rank band cover the truth about 80% of the time (a mis-scaled SE or rank band fails this)."""
    alpha, sigma = 4.0, 8.0
    tau = sigma / np.sqrt(alpha)
    cov_rating, cov_rank = [], []
    for rep in range(12):
        rng = np.random.default_rng(100 + rep)
        ids = [f"t{i}" for i in range(40)]
        off = dict(zip(ids, rng.normal(0, tau, 40)))
        de = dict(zip(ids, rng.normal(0, tau, 40)))
        T = pd.DataFrame(_season_games(4, 2020, 200 + rep, ids, off, de))
        T["pts"] = BASE + 0 * T["pts"]  # placeholder, regenerated below with the exact generative model
        rows, gid = [], 0
        rng2 = np.random.default_rng(300 + rep)
        for w in range(1, 5):
            perm = list(rng2.permutation(ids))
            for a, b in zip(perm[::2], perm[1::2]):
                gid += 1
                rows += _row(gid, 2020, w, a, b, off, de, 1, rng2, sigma)
        T = pd.DataFrame(rows)
        f = dr.fit(T, 2020, 202010, dr.DefenseConfig(alpha=alpha, decay=0.2, history=0, n_draws=1500, min_median_games=1))
        fr = f.frame().set_index("team_id")
        z = (fr.loc[ids, "def_rating"] - pd.Series(de)[ids]) / fr.loc[ids, "se_def"]
        cov_rating += list(np.abs(z) < 1.2816)
        tab = dr.rank_table(f).set_index("team_id")
        true_rank = pd.Series(de)[ids].rank(method="first")
        cov_rank += list(((true_rank >= tab.loc[ids, "rank_lo"]) & (true_rank <= tab.loc[ids, "rank_hi"])).to_numpy())
    assert 0.72 <= np.mean(cov_rating) <= 0.88, np.mean(cov_rating)
    assert 0.70 <= np.mean(cov_rank) <= 0.92, np.mean(cov_rank)


# ----------------------------------------------------------------------------- equivalence with the frozen ridge
def test_rating_equals_the_frozen_walkforward_ridge():
    ids, off, de, _ = _league(24, seed=8)
    T = pd.DataFrame(_season_games(8, 2019, 21, ids, off, de) + _season_games(8, 2020, 22, ids, off, de))
    R = ratings.season_ratings(T, "pts", 2020, alpha=0.5, decay=0.2, history=3, fcs_mode="pool")
    for blk in (202001, 202004, 202008):
        f = dr.fit(T, 2020, blk, dr.DefenseConfig(alpha=0.5, decay=0.2, history=3))
        got = f.frame().set_index("team_id")
        ref = R[R["block"] == blk].set_index("team")
        both = [t for t in ref.index if t in got.index]
        np.testing.assert_allclose(got.loc[both, "def_rating"], ref.loc[both, "def_rating"], atol=1e-8)
        np.testing.assert_allclose(got.loc[both, "off_rating"], ref.loc[both, "off_rating"], atol=1e-8)
        assert abs(f.mu - ref["intercept"].iloc[0]) < 1e-8


def test_fit_is_point_in_time():
    ids, off, de, _ = _league(24, seed=9)
    T = pd.DataFrame(_season_games(8, 2020, 23, ids, off, de))
    T2 = T.copy()
    T2.loc[T2["week"] >= 5, "pts"] += 40
    a = dr.fit(T, 2020, 202005, _cfg(history=0)).frame()
    b = dr.fit(T2, 2020, 202005, _cfg(history=0)).frame()
    np.testing.assert_allclose(a["def_rating"], b["def_rating"])
    np.testing.assert_allclose(a["se_def"], b["se_def"])


# ----------------------------------------------------------------------------- published file
def test_shadow_file_schema_and_script_engine_side_by_side():
    from cfb import defense_shadow as sh
    ids, off, de, _ = _league(30, seed=10)
    T = pd.DataFrame(_season_games(5, 2020, 31, ids, off, de))
    T["team"] = T["team_id"]
    f = _fit(T, history=0)
    tab = dr.rank_table(f, names={t: f"Team {t}" for t in ids})
    se_ranks = {t: {"rank": i + 1, "universe": 30, "adjusted": 20.0 + i, "games": 5, "stable": False,
                    "fbs_components": 4} for i, t in enumerate(ids)}
    doc = sh.build_document(tab, dr.league_summary(f), se_ranks, generated_utc="2026-10-17T12:00:00Z",
                            data_asof_utc="2026-10-17T04:00:00Z", season=2020, config=f.config, code_sha="abc123")
    assert doc["schema"] == "sift.defense_ratings.v1" and doc["authority"] == "research_only" and doc["role"] == "shadow"
    assert len(doc["teams"]) == 30
    need = {"team_id", "team", "rating", "rank", "rank_lo", "rank_hi", "rank_status", "rank_reasons", "se", "games_played",
            "effective_games", "carry_share", "connected_to_main", "component_size", "fcs_pooled", "script_engine"}
    for row in doc["teams"]:
        assert need <= set(row)
        assert set(row["script_engine"]) >= {"rank", "universe", "rating", "games", "stable"}
        assert row["rank_lo"] <= row["rank_hi"]
    assert doc["league"]["fbs_schedule_components"] >= 1
    json.dumps(doc)                                    # serialisable
