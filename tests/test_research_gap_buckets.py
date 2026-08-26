"""Mission section 17: gap-bucket boundaries."""

from __future__ import annotations

from cfb_edge_finder.research.gap_buckets import gap_bucket_for


def test_below_2_percent():
    assert gap_bucket_for(0.01) == "<2%"
    assert gap_bucket_for(-0.01) == "<2%"


def test_boundary_2_percent_goes_to_2_5_bucket():
    assert gap_bucket_for(0.02) == "2-5%"


def test_2_to_5_percent():
    assert gap_bucket_for(0.03) == "2-5%"


def test_boundary_5_percent():
    assert gap_bucket_for(0.05) == "5-8%"


def test_5_to_8_percent():
    assert gap_bucket_for(0.07) == "5-8%"


def test_boundary_8_percent():
    assert gap_bucket_for(0.08) == "8-12%"


def test_8_to_12_percent():
    assert gap_bucket_for(0.10) == "8-12%"


def test_boundary_12_percent_open_ended():
    assert gap_bucket_for(0.12) == "12%+"
    assert gap_bucket_for(0.50) == "12%+"


def test_magnitude_only_direction_agnostic():
    assert gap_bucket_for(0.03) == gap_bucket_for(-0.03)


def test_zero_gap():
    assert gap_bucket_for(0.0) == "<2%"
