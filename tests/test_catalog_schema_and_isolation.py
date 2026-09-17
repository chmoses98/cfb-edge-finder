"""Schema validation, mechanics correctness, and model isolation.

The isolation tests are the structural expression of the pivot: the
catalog package must not be able to reach the projection model even by
accident, because the model is exactly what is being removed from the live
betting path.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cfb_edge_finder.catalog.artifacts import build_catalog, build_flat_index, write_json
from cfb_edge_finder.catalog.discovery import MarketDiscovery
from cfb_edge_finder.catalog.mechanics import (
    bid_ask_spread,
    implied_probability,
    kalshi_trading_fee,
    mid_price,
    quote_age_seconds,
)
from tests.catalog_fakes import FakeKalshi, make_event, make_market, make_milestone
from tests.test_catalog_discovery import CFB_SERIES, NOW

SRC = Path(__file__).resolve().parents[1] / "src" / "cfb_edge_finder"
CATALOG_DIR = SRC / "catalog"

FORBIDDEN_MODULES = (
    "cfb_edge_finder.modeling",
    "cfb_edge_finder.projections",
    "cfb_edge_finder.recommendation",
    "cfb_edge_finder.decision",
    "cfb_edge_finder.sizing",
    "cfb_edge_finder.analytics",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("module_path", sorted(CATALOG_DIR.glob("*.py")), ids=lambda p: p.name)
def test_catalog_package_never_imports_the_projection_model(module_path: Path):
    """No model, no projections, no recommendations, no sizing -- asserted,
    not merely intended. This is what keeps the retired model off the live
    path permanently rather than until someone adds a convenient import."""
    for imported in _imported_modules(module_path):
        for forbidden in FORBIDDEN_MODULES:
            assert not imported.startswith(forbidden), (
                f"{module_path.name} imports {imported!r}: the catalog must stay free of the "
                f"projection model (mission: remove model-generated probabilities from the LIVE path)"
            )


def test_catalog_builder_script_never_imports_the_projection_model():
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_kalshi_cfb_catalog.py"
    for imported in _imported_modules(script):
        for forbidden in FORBIDDEN_MODULES:
            assert not imported.startswith(forbidden), f"{script.name} imports {imported!r}"


def _code_without_docstrings(path: Path) -> str:
    """Source with every docstring removed.

    Prose matters here: these modules DOCUMENT that the retired path needed
    CFBD_API_KEY and why it no longer does, so a plain substring search over
    the file flags its own explanation. Only executable code counts."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body.pop(0)
    # Attribute docstrings sit as bare string Expr statements too.
    return ast.unparse(
        ast.fix_missing_locations(
            ast.Module(
                body=[
                    n
                    for n in tree.body
                    if not (
                        isinstance(n, ast.Expr)
                        and isinstance(n.value, ast.Constant)
                        and isinstance(n.value.value, str)
                    )
                ],
                type_ignores=[],
            )
        )
    )


def test_no_cfbd_dependency_anywhere_in_the_live_catalog_path():
    """The new live path must not require CFBD_API_KEY. Game identity comes
    from Kalshi's own milestones and event tickers."""
    for module_path in list(CATALOG_DIR.glob("*.py")) + [
        Path(__file__).resolve().parents[1] / "scripts" / "build_kalshi_cfb_catalog.py"
    ]:
        code = _code_without_docstrings(module_path)
        assert "CFBD_API_KEY" not in code, f"{module_path.name} uses CFBD_API_KEY in code"
        assert "cfbd" not in code.lower(), f"{module_path.name} references cfbd in code"
        for imported in _imported_modules(module_path):
            assert "cfbd" not in imported.lower(), f"{module_path.name} imports {imported!r}"


FORBIDDEN_ARTIFACT_KEYS = (
    "fair_probability",
    "fair_value",
    "model_probability",
    "expected_margin",
    "expected_score",
    "projected_margin",
    "projected_total",
    "model_edge",
    "edge",
    "kelly",
    "recommendation",
    "stake",
)


def _all_keys(value, found=None):
    found = found if found is not None else set()
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(k)
            _all_keys(v, found)
    elif isinstance(value, list):
        for item in value:
            _all_keys(item, found)
    return found


def _fake():
    game = "26SEP19UGAARK"
    events = {
        f"{s}-{game}": make_event(f"{s}-{game}") for s in ("KXNCAAFGAME", "KXNCAAFSPREAD")
    }
    markets = {
        f"KXNCAAFGAME-{game}": [make_market(f"KXNCAAFGAME-{game}-UGA", floor_strike=None)],
        f"KXNCAAFSPREAD-{game}": [
            make_market(f"KXNCAAFSPREAD-{game}-UGA{i}", floor_strike=i + 0.5) for i in (3, 4)
        ],
    }
    return FakeKalshi(
        milestones=[make_milestone(game, tuple(events))],
        events=events,
        markets_by_event=markets,
        series=CFB_SERIES,
        multivariate=[
            {
                "collection_ticker": "KXMVESPORTSMULTIGAMEEXTENDED-R",
                "series_ticker": "KXMVESPORTSMULTIGAMEEXTENDED",
                "associated_event_tickers": [f"KXNCAAFGAME-{game}", "KXNFLGAME-26SEP17DETBUF"],
                "size_min": 2,
                "is_single_market_per_event": False,
            }
        ],
    )


def test_artifact_contains_no_model_generated_field():
    catalog = build_catalog(MarketDiscovery(_fake()).run(as_of=NOW))
    keys = _all_keys(catalog)
    for forbidden in FORBIDDEN_ARTIFACT_KEYS:
        assert forbidden not in keys, f"catalog exposes a model-shaped key: {forbidden!r}"
    assert catalog["capture"]["contains_model_projections"] is False


def test_catalog_schema_top_level_shape():
    catalog = build_catalog(MarketDiscovery(_fake()).run(as_of=NOW))
    for key in ("schema_version", "capture", "discovery", "totals", "completeness", "games"):
        assert key in catalog, f"missing top-level key {key!r}"
    assert catalog["schema_version"].startswith("cfb_market_catalog/")

    totals = catalog["totals"]
    for key in ("physical_games", "events", "markets", "family_distribution", "unknown_family_markets"):
        assert key in totals

    # The two completeness axes must be reported SEPARATELY, per the
    # mission's explicit instruction not to claim universal completeness.
    completeness = catalog["completeness"]
    assert "native_game_markets_complete" in completeness
    assert "multivariate_market_coverage" in completeness
    assert completeness["multivariate_market_coverage"]["combo_markets_enumerable_per_game"] is False


def test_every_game_carries_full_diagnostics():
    catalog = build_catalog(MarketDiscovery(_fake()).run(as_of=NOW))
    required = {
        "related_event_tickers_reported",
        "events_fetched",
        "failed_event_tickers",
        "pagination_failed_event_tickers",
        "markets_discovered",
        "markets_open",
        "markets_unopened",
        "markets_paused",
        "markets_closed",
        "markets_classified",
        "markets_unknown",
        "api_failures",
        "native_game_markets_complete",
    }
    for game in catalog["games"]:
        assert required <= set(game["completeness"]), (
            f"game {game['game_key']} is missing diagnostics: "
            f"{sorted(required - set(game['completeness']))}"
        )


def test_every_market_carries_the_mandated_contract_fields():
    """The field list the mission requires be preserved where available."""
    catalog = build_catalog(MarketDiscovery(_fake()).run(as_of=NOW))
    required = {
        "market_ticker",
        "event_ticker",
        "series_ticker",
        "title",
        "yes_sub_title",
        "no_sub_title",
        "status",
        "market_type",
        "strike_type",
        "floor_strike",
        "cap_strike",
        "functional_strike",
        "custom_strike",
        "yes_bid",
        "yes_ask",
        "no_bid",
        "no_ask",
        "yes_bid_size",
        "yes_ask_size",
        "last_price",
        "volume",
        "volume_24h",
        "open_interest",
        "liquidity_dollars",
        "open_time",
        "close_time",
        "expected_expiration_time",
        "occurrence_datetime",
        "rules_primary",
        "rules_secondary",
        "fee_type",
        "fee_multiplier",
        "exchange_index",
        "settlement_sources",
        "family",
        "mechanics",
    }
    for game in catalog["games"]:
        for market in game["markets"]:
            missing = required - set(market)
            assert not missing, f"{market['market_ticker']} missing {sorted(missing)}"


def test_combo_eligibility_is_recorded_per_game():
    catalog = build_catalog(MarketDiscovery(_fake()).run(as_of=NOW))
    coverage = catalog["games"][0]["completeness"]["multivariate_market_coverage"]
    assert coverage["combo_eligible_event_tickers"] == ["KXNCAAFGAME-26SEP19UGAARK"]
    assert coverage["combo_markets_enumerable_per_game"] is False
    assert "dynamically instantiated" in coverage["note"].lower()


def test_artifact_round_trips_as_json(tmp_path: Path):
    run = MarketDiscovery(_fake()).run(as_of=NOW)
    path = tmp_path / "cfb_market_catalog.json"
    write_json(path, build_catalog(run))
    reloaded = json.loads(path.read_text())
    assert reloaded["totals"]["markets"] == 3
    # Deterministic bytes for an unchanged capture.
    first = path.read_bytes()
    write_json(path, build_catalog(MarketDiscovery(_fake()).run(as_of=NOW)))
    assert path.read_bytes() == first


def test_flat_index_schema():
    flat = build_flat_index(MarketDiscovery(_fake()).run(as_of=NOW))
    assert flat["schema_version"].startswith("cfb_markets_flat/")
    assert flat["market_count"] == len(flat["markets"])
    tickers = [m["market_ticker"] for m in flat["markets"]]
    assert tickers == sorted(tickers), "flat index must be deterministically ordered"
    for row in flat["markets"]:
        assert row["game_key"] == "26SEP19UGAARK"


# --- mechanics ------------------------------------------------------------
def test_prices_are_parsed_from_decimal_dollar_strings():
    """Live Kalshi returns "0.1200", not 12. Treating the string as cents
    would report a 12c ask as $12.00 and every derived mechanic would be
    nonsense."""
    run = MarketDiscovery(_fake()).run(as_of=NOW)
    contract = run.games["26SEP19UGAARK"].contracts[0]
    assert contract.quote.yes_ask == pytest.approx(0.12)
    assert contract.quote.yes_bid == pytest.approx(0.01)
    assert contract.quote.yes_bid_size == pytest.approx(730.0)


def test_integer_cent_prices_are_also_accepted():
    """An older Kalshi representation (or a changed deployment) returns
    integer cents. Both spellings must land on the same dollar value."""
    from cfb_edge_finder.catalog.contract import build_contract

    market = {"ticker": "T", "event_ticker": "E", "yes_bid": 1, "yes_ask": 12, "status": "active"}
    contract = build_contract(
        market,
        game_key="g",
        series_ticker="KXNCAAFGAME",
        settlement_sources=[],
        fee_type="quadratic",
        fee_multiplier=1,
        captured_at=NOW,
    )
    assert contract.quote.yes_ask == pytest.approx(0.12)
    assert contract.quote.yes_bid == pytest.approx(0.01)


def test_mid_and_spread_require_two_sides():
    assert mid_price(0.10, 0.20) == pytest.approx(0.15)
    assert bid_ask_spread(0.10, 0.20) == pytest.approx(0.10)
    # A one-sided book has no mid; inventing one fabricates a price.
    assert mid_price(None, 0.20) is None
    assert mid_price(0.10, None) is None
    assert bid_ask_spread(None, None) is None


def test_implied_probability_is_the_price_restated():
    assert implied_probability(0.37) == pytest.approx(0.37)
    assert implied_probability(None) is None
    # Out-of-range means the input was not a dollar price. Returning None
    # beats publishing a confident 100%.
    assert implied_probability(12.0) is None
    assert implied_probability(-0.5) is None


def test_fee_is_quadratic_and_peaks_at_the_middle():
    at_mid = kalshi_trading_fee(0.50)
    at_edge = kalshi_trading_fee(0.02)
    assert at_mid > at_edge
    assert at_mid == pytest.approx(0.02)  # ceil(0.07 * 0.25 * 100c) = 2c
    assert kalshi_trading_fee(None) is None
    # A series with a different multiplier is honoured, not assumed.
    assert kalshi_trading_fee(0.50, fee_multiplier=2) > at_mid


def test_quote_age_is_reported():
    updated = datetime(2026, 9, 17, 21, 0, tzinfo=UTC)
    assert quote_age_seconds(updated, NOW) == pytest.approx(3600.0)
    assert quote_age_seconds(None, NOW) is None


def test_mechanics_block_has_no_game_opinion():
    run = MarketDiscovery(_fake()).run(as_of=NOW)
    catalog = build_catalog(run)
    mechanics = catalog["games"][0]["markets"][0]["mechanics"]
    assert set(mechanics) == {
        "yes_mid",
        "no_mid",
        "yes_bid_ask_spread",
        "yes_bid_ask_spread_cents",
        "implied_probability_yes_mid",
        "implied_probability_yes_ask",
        "implied_probability_yes_bid",
        "implied_probability_last",
        "two_sided_quote",
        "estimated_fee_per_contract_at_mid",
        "fee_formula",
        "quote_age_seconds",
        "seconds_until_close",
        "seconds_until_occurrence",
    }
