"""Mission section 27: calibration measurement, plus the market
comparison and cluster-aware uncertainty it depends on.
"""

from __future__ import annotations

import pytest

from cfb_edge_finder.analytics.calibration_report import (
    MARKET_PRICE_CAVEAT,
    brier_score,
    build_calibration_report,
    compare_model_to_market,
    log_loss,
)
from cfb_edge_finder.analytics.uncertainty import (
    cluster_bootstrap_mean,
    cluster_bootstrap_rate,
    sample_confidence,
)


def _calibrated(n_per_bin=100):
    """A perfectly calibrated forecaster: at probability p, exactly p of
    the outcomes are True."""
    preds, outs = [], []
    for p in (0.1, 0.3, 0.5, 0.7, 0.9):
        hits = round(p * n_per_bin)
        preds += [p] * n_per_bin
        outs += [True] * hits + [False] * (n_per_bin - hits)
    return preds, outs


def test_perfect_calibration_has_near_zero_error():
    preds, outs = _calibrated()
    r = build_calibration_report(label="perfect", predictions=preds, outcomes=outs)
    assert r.expected_calibration_error == pytest.approx(0.0, abs=1e-9)
    assert r.max_calibration_error == pytest.approx(0.0, abs=1e-9)
    for b in r.bins:
        if b.count:
            assert b.observed_rate == pytest.approx(b.mean_predicted, abs=1e-9)


def test_overconfident_model_is_detected():
    """Says 0.9, only 0.5 happen -> observed below predicted (negative
    calibration error) in that bin."""
    preds = [0.9] * 100
    outs = [True] * 50 + [False] * 50
    r = build_calibration_report(label="overconfident", predictions=preds, outcomes=outs)
    bin_09 = next(b for b in r.bins if b.count)
    assert bin_09.calibration_error == pytest.approx(-0.4)
    assert r.expected_calibration_error == pytest.approx(0.4)


def test_underconfident_model_is_detected():
    preds = [0.2] * 100
    outs = [True] * 60 + [False] * 40
    r = build_calibration_report(label="underconfident", predictions=preds, outcomes=outs)
    b = next(x for x in r.bins if x.count)
    assert b.calibration_error == pytest.approx(0.4), "under-confidence should be positive error"


def test_empty_bins_are_retained_with_null_stats():
    """Dropping an empty bin would hide that a probability range was
    never predicted at all."""
    r = build_calibration_report(label="sparse", predictions=[0.05, 0.06], outcomes=[True, False])
    empty = [b for b in r.bins if b.count == 0]
    assert len(empty) == len(r.bins) - 1
    for b in empty:
        assert b.observed_rate is None and b.mean_predicted is None and b.calibration_error is None


def test_empty_bins_do_not_dilute_ece():
    """ECE is weighted by observations, so nine empty bins must not drag
    a 0.4 error down toward 0.04."""
    r = build_calibration_report(label="x", predictions=[0.9] * 10, outcomes=[True] * 5 + [False] * 5)
    assert r.expected_calibration_error == pytest.approx(0.4)


def test_low_sample_bin_is_still_reported():
    r = build_calibration_report(label="tiny", predictions=[0.5], outcomes=[True])
    b = next(x for x in r.bins if x.count)
    assert b.count == 1 and b.observed_rate == 1.0
    assert sample_confidence(1).label == "LOW_SAMPLE"


def test_empty_input_returns_none_not_zero():
    r = build_calibration_report(label="none", predictions=[], outcomes=[])
    assert r.n == 0 and r.brier is None and r.log_loss is None and r.expected_calibration_error is None


def test_prediction_of_exactly_one_lands_in_the_final_bin():
    r = build_calibration_report(label="edge", predictions=[1.0, 0.0], outcomes=[True, False])
    assert sum(b.count for b in r.bins) == 2, "boundary predictions were dropped"


def test_log_loss_is_clipped_at_the_boundaries():
    """A confident-and-wrong boundary prediction must not be infinite."""
    ll = log_loss([1.0], [False])
    assert ll is not None and ll < float("inf") and ll > 30


def test_log_loss_and_brier_reward_correctness():
    assert brier_score([1.0], [True]) == pytest.approx(0.0)
    assert brier_score([0.0], [True]) == pytest.approx(1.0)
    assert log_loss([0.99], [True]) < log_loss([0.5], [True])


def test_mismatched_lengths_raise():
    with pytest.raises(ValueError):
        build_calibration_report(label="x", predictions=[0.5], outcomes=[True, False])


# --- Market comparison (section 8) ---------------------------------------


def test_market_comparison_is_paired_and_carries_the_caveat():
    preds, outs = _calibrated(n_per_bin=20)
    market = [min(max(p + 0.05, 0.0), 1.0) for p in preds]
    c = compare_model_to_market(model_probabilities=preds, market_probabilities=market, outcomes=outs)
    assert c.n == len(outs)
    assert MARKET_PRICE_CAVEAT in c.caveats
    assert MARKET_PRICE_CAVEAT in c.market.caveats
    assert c.model.brier is not None and c.market.brier is not None
    # Model is perfectly calibrated here, market is shifted -> model better.
    assert c.brier_difference < 0, "negative difference should mean the model scored better"


def test_market_comparison_sign_is_not_assumed_favorable():
    """When the market is the better forecaster the difference flips."""
    outs = [True] * 50 + [False] * 50
    model = [0.9] * 100
    market = [0.5] * 100
    c = compare_model_to_market(model_probabilities=model, market_probabilities=market, outcomes=outs)
    assert c.brier_difference > 0, "model was worse; difference should be positive"


# --- Cluster-aware uncertainty (sections 16-18) --------------------------


def test_bootstrap_interval_brackets_the_point_estimate():
    values = [0.01 * i for i in range(60)]
    clusters = [f"game{i % 12}" for i in range(60)]
    e = cluster_bootstrap_mean(values, clusters)
    assert e.interval_available and e.lower <= e.point_estimate <= e.upper
    assert e.n_clusters == 12


def test_interval_withheld_below_the_cluster_floor():
    """300 rows from 2 games is not a large sample."""
    e = cluster_bootstrap_mean([0.5] * 300, ["g1"] * 150 + ["g2"] * 150)
    assert e.interval_available is False
    assert e.point_estimate == pytest.approx(0.5), "point estimate should still be reported"
    assert "cluster" in e.reason


def test_clustering_widens_the_interval_versus_pretending_independence():
    """The core reason clustering exists: 20 games x 15 correlated
    contracts must give a WIDER interval than 300 independent rows."""
    import random

    rng = random.Random(7)
    per_game = [rng.choice([-0.2, 0.2]) for _ in range(20)]
    clustered_vals, clustered_ids, iid_vals, iid_ids = [], [], [], []
    for g, effect in enumerate(per_game):
        for k in range(15):
            clustered_vals.append(effect)
            clustered_ids.append(f"game{g}")
            iid_vals.append(effect)
            iid_ids.append(f"row{g}_{k}")  # every row its own cluster

    clustered = cluster_bootstrap_mean(clustered_vals, clustered_ids, seed=1)
    pseudo_iid = cluster_bootstrap_mean(iid_vals, iid_ids, seed=1)
    assert clustered.interval_available and pseudo_iid.interval_available
    clustered_width = clustered.upper - clustered.lower
    iid_width = pseudo_iid.upper - pseudo_iid.lower
    assert clustered_width > iid_width, (
        f"game-clustered interval ({clustered_width:.4f}) was not wider than the "
        f"pretend-independent one ({iid_width:.4f})"
    )


def test_bootstrap_is_reproducible():
    v = [0.1 * i for i in range(50)]
    c = [f"g{i % 10}" for i in range(50)]
    a = cluster_bootstrap_mean(v, c, seed=42)
    b = cluster_bootstrap_mean(v, c, seed=42)
    assert (a.lower, a.upper) == (b.lower, b.upper), "report intervals are not reproducible"


def test_rate_bootstrap_on_booleans():
    e = cluster_bootstrap_rate([True] * 30 + [False] * 30, [f"g{i % 10}" for i in range(60)])
    assert e.point_estimate == pytest.approx(0.5) and e.interval_available


def test_sample_confidence_labels():
    assert sample_confidence(0).label == "NO_SAMPLE"
    assert sample_confidence(5, 5).label == "LOW_SAMPLE"
    assert sample_confidence(30, 10).label == "CAUTION"
    assert sample_confidence(500, 40).label == "OK"
    assert sample_confidence(500, 2).label == "LOW_SAMPLE", "many rows from few games is not a large sample"


def test_mismatched_cluster_lengths_raise():
    with pytest.raises(ValueError):
        cluster_bootstrap_mean([1.0, 2.0], ["g1"])
