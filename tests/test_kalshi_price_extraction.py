"""price_extraction.py tests, including against the genuine live fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from cfb_edge_finder.kalshi.price_extraction import extract_market_price

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kalshi"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


def test_extracts_real_live_spread_fixture_prices():
    market = _load_fixture("spread_market_suu5.json")
    extracted = extract_market_price(market)
    assert extracted.yes_bid == 0.07
    assert extracted.yes_ask == 0.35
    assert extracted.no_bid == 0.65
    assert extracted.no_ask == 0.93
    assert extracted.executable_yes_price == 0.35
    assert extracted.executable_no_price == 0.93
    assert extracted.midpoint == (0.07 + 0.35) / 2.0
    assert extracted.has_quoted_market is True
    assert extracted.has_any_volume is False  # real evidence: fresh market, zero volume


def test_missing_fields_are_none_not_fabricated_zero():
    extracted = extract_market_price({"ticker": "X"})
    assert extracted.yes_bid is None
    assert extracted.yes_ask is None
    assert extracted.executable_yes_price is None
    assert extracted.midpoint is None
    assert extracted.has_quoted_market is False
    assert extracted.has_any_volume is False


def test_unparseable_dollar_string_is_none():
    extracted = extract_market_price({"yes_ask_dollars": "not-a-number"})
    assert extracted.yes_ask is None


def test_executable_price_is_never_the_midpoint():
    market = {
        "yes_bid_dollars": "0.10",
        "yes_ask_dollars": "0.50",
        "no_bid_dollars": "0.50",
        "no_ask_dollars": "0.90",
    }
    extracted = extract_market_price(market)
    assert extracted.executable_yes_price == 0.50
    assert extracted.midpoint == 0.30
    assert extracted.executable_yes_price != extracted.midpoint


def test_has_any_volume_true_when_volume_present():
    extracted = extract_market_price({"volume_fp": "42.00"})
    assert extracted.has_any_volume is True
