"""Multi-season key-number calibration: split integrity, point-in-time ratings, the pre-declared rule, the lift fit."""

from __future__ import annotations

import copy
import gzip
import json

import numpy as np

from cfb_edge_finder.projections.discrete_scoring import ShapeParams, build_distribution
from cfb_edge_finder.research import multiseason_calibration as ms


def g(gid, season, date, home, away, hp, ap):
    return {
        "game_id": gid,
        "season": season,
        "kickoff": f"{date}T16:00:00Z",
        "date": date,
        "home": home,
        "away": away,
        "neutral": False,
        "home_pts": hp,
        "away_pts": ap,
    }


# ---- loading ---------------------------------------------------------------------------------------------
def test_loader_keeps_only_fbs_vs_fbs_with_final_scores(tmp_path):
    rows = [
        {
            "game_id": "1",
            "season": 2024,
            "kickoff_utc": "2024-09-01T16:00:00Z",
            "home": "A",
            "away": "B",
            "neutral_site": False,
            "divisions": ["fbs", "fbs"],
            "fcs_involved": False,
            "actual_home": 30.0,
            "actual_away": 27.0,
        },
        {
            "game_id": "2",
            "season": 2024,
            "kickoff_utc": "2024-09-01T16:00:00Z",
            "home": "A",
            "away": "FCS",
            "divisions": ["fbs", "fcs"],
            "fcs_involved": True,
            "actual_home": 50.0,
            "actual_away": 3.0,
        },
        {
            "game_id": "3",
            "season": 2024,
            "kickoff_utc": "2024-09-08T16:00:00Z",
            "home": "A",
            "away": "B",
            "divisions": ["fbs", "fbs"],
            "fcs_involved": False,
            "actual_home": None,
            "actual_away": None,
        },
    ]
    with gzip.open(tmp_path / "records_2024.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r) for r in rows))
    games = ms.load_games(tmp_path, seasons=(2024, 2025))
    assert [x["game_id"] for x in games] == ["1"] and games[0]["home_pts"] == 30


# ---- point-in-time ratings -------------------------------------------------------------------------------
def test_ratings_use_only_strictly_earlier_dates_of_the_same_season():
    games = [
        g("a", 2025, "2025-09-01", "A", "B", 30, 20),
        g("b", 2025, "2025-09-01", "C", "D", 10, 40),
        g("c", 2025, "2025-09-08", "A", "C", 24, 21),
        g("d", 2025, "2025-09-08", "B", "D", 17, 14),
        g("e", 2025, "2025-09-15", "A", "D", 28, 24),
        g("z", 2024, "2024-12-01", "A", "D", 99, 0),  # a PREVIOUS season: must never leak into 2025 ratings
    ]
    rated = {x[0]["game_id"]: x[1] for x in ms.rate_games(games)}
    # 'a','b' (first date) have no priors; 'c','d' have only one prior game each (< MIN_PRIOR_GAMES=2): not rated
    assert "a" not in rated and "b" not in rated and "c" not in rated
    assert "e" in rated, "two earlier games each for A and D within the season"
    # changing a same-day or later game must not move an earlier game's means; a previous-season game never matters
    changed = copy.deepcopy(games)
    changed[-1] = g("z", 2024, "2024-12-01", "A", "D", 0, 99)
    changed[4] = g("e", 2025, "2025-09-15", "A", "D", 3, 60)  # the target game's OWN score is not an input
    assert {x[0]["game_id"]: x[1] for x in ms.rate_games(changed)}["e"] == rated["e"]


def test_blocks_are_disjoint_and_ordered_in_time():
    rated = [
        (g(str(i), s, d, "A", "B", 20, 10), (24.0, 21.0))
        for i, (s, d) in enumerate(
            [
                (2024, "2024-10-05"),
                (2025, "2025-10-04"),
                (2026, "2026-10-10"),
                (2026, "2026-10-17"),
                (2026, "2026-11-01"),
                (2026, "2026-11-08"),
                (2023, "2023-10-07"),
            ]
        )
    ]
    blocks = ms.split_blocks(rated)
    ids = {k: {x[0]["game_id"] for x in v} for k, v in blocks.items()}
    assert ids["train"] == {"0"} and ids["validate"] == {"1"} and ids["test"] == {"2"} and ids["final"] == {"3", "4"}
    all_ids = [i for v in ids.values() for i in v]
    assert len(all_ids) == len(set(all_ids)), "no game is in two blocks"
    assert "5" not in all_ids and "6" not in all_ids  # after the window / other seasons: in no block
    last = lambda k: max(x[0]["kickoff"] for x in blocks[k])  # noqa: E731
    first = lambda k: min(x[0]["kickoff"] for x in blocks[k])  # noqa: E731
    assert last("train") < first("validate") < first("test") <= last("test") < first("final")


# ---- the pre-declared rule -------------------------------------------------------------------------------
def summary(
    *,
    gap_c=0.01,
    gap_r=0.08,
    n=300,
    obs=0.30,
    margin_lo=0.02,
    tot_hi=0.01,
    team_hi=0.01,
    brier_c=0.240,
    brier_r=0.241,
    normal_lo=0.05,
):
    cand = {"key_mass_predicted": obs + gap_c, "event_brier": brier_c, "margin_logscore": -4.0}
    ref = {"key_mass_predicted": obs - gap_r, "event_brier": brier_r, "margin_logscore": -4.1}
    return {
        "games": n,
        "observed_key_mass": obs,
        "systems": {"discrete_v1": cand, ms.REFERENCE: ref},
        "paired": {
            "discrete_v1": {
                "vs_" + ms.REFERENCE: {
                    "margin": {"mean": 0.05, "ci95": [margin_lo, 0.1]},
                    "total": {"mean": 0.0, "ci95": [-0.02, tot_hi]},
                    "team": {"mean": 0.0, "ci95": [-0.02, team_hi]},
                },
                "vs_normal": {"margin": {"mean": 0.1, "ci95": [normal_lo, 0.2]}},
            }
        },
    }


def test_all_five_criteria_pass_on_a_good_block_and_each_fails_independently():
    assert all(ms.criteria_for("discrete_v1", summary()).values())
    assert not ms.criteria_for("discrete_v1", summary(gap_c=0.20))["A_key_mass_within_1.96_se"]
    assert not ms.criteria_for("discrete_v1", summary(gap_c=0.09, gap_r=0.08))["B_gap_smaller_than_uncalibrated"]
    assert not ms.criteria_for("discrete_v1", summary(margin_lo=-0.01))[
        "C_margin_gain_ci_lower_gt_0_and_others_not_worse"
    ]
    assert not ms.criteria_for("discrete_v1", summary(tot_hi=-0.001))[
        "C_margin_gain_ci_lower_gt_0_and_others_not_worse"
    ]
    assert not ms.criteria_for("discrete_v1", summary(brier_c=0.243))["D_event_brier_not_worse"]
    assert not ms.criteria_for("discrete_v1", summary(normal_lo=-0.02))["E_beats_normal_on_margin_ci_lower_gt_0"]


def test_verdict_needs_validate_and_test_and_never_final():
    good, bad = summary(), summary(margin_lo=-0.05)
    final_bad = {**summary(margin_lo=-0.2), "games": 100}
    ok = ms.verdict({"validate": good, "test": good, "final": final_bad}, "discrete_v1")
    assert ok["verdict"] == "PROMOTABLE" and "NOT_PROMOTED" in ok["promotion"]  # FINAL is continuity only
    assert ms.verdict({"validate": good, "test": bad, "final": good}, "discrete_v1")["verdict"] == "NOT_PROMOTABLE"
    assert ms.verdict({"validate": bad, "test": good, "final": good}, "discrete_v1")["verdict"] == "NOT_PROMOTABLE"


def test_selection_uses_only_the_validate_block_and_breaks_ties_toward_the_simpler_candidate():
    s = {"systems": {c: {"margin_logscore": -4.0} for c in ms.CANDIDATES}}
    assert ms.select_candidate(s) == ms.CANDIDATES[0]
    s["systems"]["discrete_emp"]["margin_logscore"] = -3.9
    assert ms.select_candidate(s) == "discrete_emp"


# ---- the empirical lift fit recovers a planted excess ---------------------------------------------------
def test_empirical_lifts_recover_a_planted_margin_three_excess_and_leave_other_margins_alone():
    base = ShapeParams(0.15, 0.0, 0.0)
    planted = ShapeParams(0.15, 0.0, 0.0, 1.0, 1.0, ((3, 3.0),))
    d = build_distribution(28.0, 24.0, planted)
    flat = d.joint.ravel()
    rng = np.random.default_rng(7)
    idx = rng.choice(flat.size, size=600, p=flat / flat.sum())
    n = d.joint.shape[1]
    train = [
        (g(str(i), 2024, "2024-09-01", "A", "B", int(k // n), int(k % n)), (28.0, 24.0)) for i, k in enumerate(idx)
    ]
    lifts = dict(ms.fit_empirical_lifts(train, base))
    assert lifts[3] > 1.8, "the planted 3x excess at margin 3 must be recovered"
    others = [lifts[m] for m in range(11, 25)]
    assert all(0.4 < x < 1.6 for x in others), "margins with no planted excess stay near 1 (shrunk by the prior)"
    assert all(ms.LIFT_BOUNDS[0] <= v <= ms.LIFT_BOUNDS[1] for v in lifts.values())


# ---- amendments: parallel fit parity, checkpoint, empty FINAL block, 2026 source -----------------------------
def _tiny_train(n=12):
    rng = np.random.default_rng(3)
    out = []
    for i in range(n):
        h, a = int(rng.integers(10, 45)), int(rng.integers(7, 38))
        out.append(
            (g(str(i), 2024, "2024-09-01", "A", "B", h, a), (float(rng.integers(20, 33)), float(rng.integers(18, 30))))
        )
    return out


def test_parallel_shape_fit_matches_the_serial_fit_exactly():
    from cfb_edge_finder.research.discrete_scoring_eval import fit_shape

    train = _tiny_train()
    cal, means = [x[0] for x in train], [x[1] for x in train]
    serial, serial_ll = fit_shape(cal, means)
    par, par_ll = ms.fit_shape_parallel(cal, means, workers=2)
    assert par == serial and abs(par_ll - serial_ll) < 1e-12


def test_checkpoint_round_trip_preserves_every_fitted_parameter():
    from cfb_edge_finder.research.discrete_scoring_eval import NormalSystem

    systems = {
        "normal": NormalSystem(13.5, 17.0, 10.5),
        ms.REFERENCE: ShapeParams(0.15, 0.05, 0.1),
        "discrete_v1": ShapeParams(0.2, 0.0, 0.0, 0.8, 1.6),
        "discrete_v2": ShapeParams(0.2, 0.0, 0.0, 1.0, 1.0, ((3, 2.1), (7, 1.2), (10, 1.1), (14, 1.1))),
        "discrete_emp": ShapeParams(0.15, 0.05, 0.1, 1.0, 1.0, ((3, 1.9), (4, 0.8))),
    }
    back = ms.load_fits(json.loads(json.dumps(ms.dump_fits(systems))))
    for k in ("discrete_v1", "discrete_v2", "discrete_emp", ms.REFERENCE):
        assert back[k] == systems[k]
    assert (back["normal"].sd_margin, back["normal"].sd_total, back["normal"].sd_team) == (13.5, 17.0, 10.5)


def test_an_empty_final_block_yields_a_verdict_and_no_continuity_criteria():
    good = summary()
    v = ms.verdict({"validate": good, "test": good}, "discrete_v1")  # no 'final' key at all: the first run's crash
    assert v["verdict"] == "PROMOTABLE" and v["criteria_final_continuity_only"] is None
    v2 = ms.verdict({"validate": good, "test": good, "final": {**good, "games": 0}}, "discrete_v1")
    assert v2["criteria_final_continuity_only"] is None


def test_2026_games_can_come_from_team_games_with_ids_and_records_stay_names(tmp_path):
    tg = tmp_path / "team_games.jsonl"
    rows = []
    for gid, h, a, hp, ap in (("9", "T1", "T2", 31, 24), ("10", "T3", "T4", 20, 17)):
        for site, tid, pf, pa in (("home", h, hp, ap), ("away", a, ap, hp)):
            rows.append(
                {
                    "game_id": gid,
                    "kickoff_utc": "2026-10-17T16:00:00Z",
                    "team_id": tid,
                    "site": site,
                    "team_division": "fbs",
                    "points_for": pf,
                    "points_against": pa,
                    "pbp": {"scrim_plays": 60.0},
                }
            )
    tg.write_text("\n".join(json.dumps(r) for r in rows))
    games = ms.load_games(tmp_path, team_games_2026=tg)
    assert {x["home"] for x in games} == {"T1", "T3"} and all(x["season"] == 2026 for x in games)
    assert {x["game_id"] for x in games} == {"9", "10"}
