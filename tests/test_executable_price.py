import pytest

from cfb_edge_finder.kalshi.executable_price import expected_fee_drag, net_executable_edge


def test_expected_fee_drag_bounded_and_zero_at_extremes():
    assert expected_fee_drag(0.0) == 0.0
    assert expected_fee_drag(1.0) == 0.0
    assert expected_fee_drag(0.5) > 0.0


def test_expected_fee_drag_rejects_out_of_bounds_price():
    with pytest.raises(ValueError):
        expected_fee_drag(1.5)


def test_net_executable_edge_reduces_raw_edge():
    fair = 0.60
    price = 0.55
    raw_edge = fair - price
    net = net_executable_edge(fair, price)
    assert net < raw_edge
