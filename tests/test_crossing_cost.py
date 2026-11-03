import importlib.util
import json
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "crossing_cost", pathlib.Path(__file__).resolve().parents[1] / "scripts/research/crossing_cost.py"
)
cc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cc)


def _quote(**kw):
    base = {
        "status": "active",
        "book_state": "two_sided",
        "family": "game_spread",
        "yes_ask": 0.52,
        "yes_bid": 0.48,
        "buy_yes_fee": 0.017,
        "no_ask": 0.52,
        "no_bid": 0.48,
        "buy_no_fee": 0.017,
    }
    return {**base, **kw}


def _file(tmp_path, quotes):
    p = tmp_path / "quotes.json"
    p.write_text(json.dumps({"quotes": quotes}))
    return str(p)


def test_cost_is_half_spread_plus_taker_fee(tmp_path):
    rows = list(cc.sides(_file(tmp_path, [_quote()]), 0.3, 0.7))
    assert len(rows) == 2
    s = cc.summarise(rows)["game_spread"]
    assert round(s["mean_cost_c"], 6) == round(100 * (0.02 + 0.017), 6)


def test_inactive_one_sided_and_out_of_band_quotes_are_skipped(tmp_path):
    qs = [
        _quote(status="closed"),
        _quote(book_state="one_sided"),
        _quote(yes_ask=0.95, no_ask=0.95),
        _quote(buy_yes_fee=None, buy_no_fee=None),
    ]
    assert list(cc.sides(_file(tmp_path, qs), 0.3, 0.7)) == []


def test_crossed_book_is_not_counted(tmp_path):
    assert list(cc.sides(_file(tmp_path, [_quote(yes_bid=0.60, no_bid=0.60)]), 0.3, 0.7)) == []


def test_render_orders_cheapest_family_first(tmp_path):
    qs = [_quote(), _quote(family="first_half_total", yes_ask=0.60, yes_bid=0.20, no_ask=0.60, no_bid=0.20)]
    text = cc.render(cc.summarise([r for r in cc.sides(_file(tmp_path, qs), 0.3, 0.7)]))
    assert text.index("game_spread") < text.index("first_half_total")
