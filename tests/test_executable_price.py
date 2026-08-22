import pytest

from cfb_edge_finder.kalshi.executable_price import (
    UNVERIFIED_PLACEHOLDER_FEE_RATE,
    expected_fee_drag,
    net_executable_edge,
)

FEE_RATE = UNVERIFIED_PLACEHOLDER_FEE_RATE  # explicit, conscious choice -- see module docstring


def test_fee_rate_has_no_silent_default():
    # Both functions must require fee_rate explicitly -- no caller should
    # ever be able to get a fee-aware number without consciously supplying
    # a rate. This is a TypeError (missing required positional arg), not a
    # ValueError, which is exactly the point: it fails before any math runs.
    with pytest.raises(TypeError):
        expected_fee_drag(0.5)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        net_executable_edge(0.6, 0.55)  # type: ignore[call-arg]


def test_expected_fee_drag_bounded_and_zero_at_extremes():
    assert expected_fee_drag(0.0, FEE_RATE) == 0.0
    assert expected_fee_drag(1.0, FEE_RATE) == 0.0
    assert expected_fee_drag(0.5, FEE_RATE) > 0.0


def test_expected_fee_drag_rejects_out_of_bounds_price():
    with pytest.raises(ValueError):
        expected_fee_drag(1.5, FEE_RATE)


def test_net_executable_edge_reduces_raw_edge():
    fair = 0.60
    price = 0.55
    raw_edge = fair - price
    net = net_executable_edge(fair, price, FEE_RATE)
    assert net < raw_edge
