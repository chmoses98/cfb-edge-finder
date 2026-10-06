"""Retrospective scoring-baseline validation: leakage-safe, football-only, faithful to production."""

from __future__ import annotations

import dataclasses

import pytest
from script_engine_fakes import synthetic_season

from cfb_edge_finder.scripting.baseline_eval import (
    apply_correction,
    block_stats,
    defense_diagnostics,
    error_stats,
    evaluate,
    fit_correction,
    gate_checks,
    segment_stats,
    targets_from_rows,
)


def _meta(rows):
    meta = {}
    for r in rows:
        if r.site == "home":
            meta.setdefault(r.game_id, {})["home_id"] = r.team_id
        elif r.site == "away":
            meta.setdefault(r.game_id, {})["away_id"] = r.team_id
        meta.setdefault(r.game_id, {}).update({"neutral_site": False, "conference_game": None})
    return {k: v for k, v in meta.items() if "home_id" in v and "away_id" in v}


@pytest.fixture(scope="module")
def season():
    rows = synthetic_season()
    targets, _ = targets_from_rows(rows, _meta(rows))
    return rows, targets


def test_every_target_is_rebuilt_from_strictly_earlier_games(season):
    rows, targets = season
    late = targets[-40:]
    records = evaluate(rows, late)
    assert records and all(r["cutoff_check"] for r in records)
    assert all(r["pred_total"] is not None for r in records)


def test_a_target_cannot_see_its_own_or_any_later_result(season):
    """Rewriting every game from the target's kickoff onward -- the target
    itself included -- changes nothing about the target's baseline."""
    rows, targets = season
    target = targets[len(targets) // 2]
    before_rows = evaluate(rows, [target])[0]
    doctored = [
        dataclasses.replace(r, points_for=(r.points_for or 0) + 40, points_against=0.0)
        if r.kickoff_utc >= target.kickoff_utc
        else r
        for r in rows
    ]
    after = evaluate(doctored, [target])[0]
    for key in ("pred_home", "pred_away", "pred_total", "mu", "home_o", "home_d", "confidence_evidence"):
        assert after[key] == before_rows[key], key


def test_statistics_and_segments_are_reported(season):
    rows, targets = season
    records = evaluate(rows, targets[len(targets) // 2 :])
    stats = block_stats(records)
    for key in ("n", "bias", "bias_ci95", "mae", "rmse", "median_abs_error", "abs_error_p80", "abs_error_p90"):
        assert key in stats["total"]
    assert {"bias", "mae"} <= set(stats["home_points"]) and {"bias", "mae"} <= set(stats["away_points"])
    segs = segment_stats(records)
    for name in ("prior_games_min", "confidence_evidence", "predicted_total", "both_defenses_strong", "fcs_involved"):
        assert name in segs
    assert defense_diagnostics(records)["n_team_games"] == 2 * stats["total"]["n"]


def test_error_statistics_are_exact():
    recs = [{"pred_total": p, "actual_total": a} for p, a in ((50, 40), (40, 50), (60, 60), (70, 50))]
    st = error_stats(recs)
    assert st["n"] == 4 and st["bias"] == pytest.approx(5.0) and st["mae"] == pytest.approx(10.0)
    assert st["rmse"] == pytest.approx((100 + 100 + 0 + 400) ** 0.5 / 2, abs=1e-3)


def test_a_correction_is_fitted_on_training_records_only(season):
    rows, targets = season
    half = len(targets) // 2
    train, test = evaluate(rows, targets[:half]), evaluate(rows, targets[half:])
    model = fit_correction("linear_total", train)
    assert model["n_train"] == sum(1 for r in train if r["pred_total"] is not None)
    # Scoring the test block never reads its results: altering them leaves the corrected predictions alone.
    corrected = apply_correction(model, test)
    blind = apply_correction(model, [dict(r, actual_total=999.0, actual_home=0.0) for r in test])
    assert [r["pred_total"] for r in blind] == [r["pred_total"] for r in corrected]
    for method in ("linear_total", "components"):
        assert fit_correction(method, train)["n_train"] > 0
    gate = gate_checks(corrected, test)
    assert set(gate["checks"]) == {
        "n",
        "total_bias",
        "home_bias",
        "away_bias",
        "mae_vs_raw",
        "mae_vs_naive",
        "segments",
    }
