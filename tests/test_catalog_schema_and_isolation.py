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

from cfb_edge_finder.catalog.artifacts import build_catalog, build_flat_index
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
            make_market(
                f"KXNCAAFSPREAD-{game}-UGA{i}",
                title=f"Georgia wins by over {i}.5 points",
                floor_strike=i + 0.5,
            )
            for i in (3, 4)
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
    catalog = build_catalog(MarketDiscovery(_fake()).run(as_of=NOW), include_markets=True)
    keys = _all_keys(catalog)
    for forbidden in FORBIDDEN_ARTIFACT_KEYS:
        assert forbidden not in keys, f"catalog exposes a model-shaped key: {forbidden!r}"
    assert catalog["capture"]["contains_model_projections"] is False


def test_catalog_schema_top_level_shape():
    catalog = build_catalog(MarketDiscovery(_fake()).run(as_of=NOW), include_markets=True)
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
    catalog = build_catalog(MarketDiscovery(_fake()).run(as_of=NOW), include_markets=True)
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
    catalog = build_catalog(MarketDiscovery(_fake()).run(as_of=NOW), include_markets=True)
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
    catalog = build_catalog(MarketDiscovery(_fake()).run(as_of=NOW), include_markets=True)
    coverage = catalog["games"][0]["completeness"]["multivariate_market_coverage"]
    assert coverage["combo_eligible_event_tickers"] == ["KXNCAAFGAME-26SEP19UGAARK"]
    assert coverage["combo_markets_enumerable_per_game"] is False
    assert "dynamically instantiated" in coverage["note"].lower()


def test_published_artifacts_round_trip_as_json(tmp_path: Path):
    """The PUBLISHED layout: a slate index plus one detail file per game."""
    from cfb_edge_finder.catalog.artifacts import write_catalog_artifacts

    run = MarketDiscovery(_fake()).run(as_of=NOW)
    artifacts = write_catalog_artifacts(run, tmp_path)

    index = json.loads((tmp_path / "cfb_market_catalog.json").read_text())
    assert index["totals"]["markets"] == 3
    assert artifacts.game_files_written == 1

    entry = index["games"][0]
    assert "markets" not in entry, "the index must not inline contracts"
    assert entry["markets_file"] == "games/26SEP19UGAARK.json"
    assert entry["market_count"] == 3

    detail = json.loads((tmp_path / entry["markets_file"]).read_text())
    assert len(detail["markets"]) == 3
    assert detail["game_key"] == entry["game_key"]

    # Deterministic bytes, and a re-publish over an unchanged slate
    # rewrites nothing at all.
    first = (tmp_path / "cfb_market_catalog.json").read_bytes()
    again = write_catalog_artifacts(MarketDiscovery(_fake()).run(as_of=NOW), tmp_path)
    assert (tmp_path / "cfb_market_catalog.json").read_bytes() == first
    assert again.game_files_written == 0
    assert again.game_files_unchanged == 1


def test_the_index_is_smaller_than_inlining_everything():
    """The reason for the split: a live capture inlined into one file
    measured 52 MB -- neither ingestible nor commitable every 30 minutes."""
    from cfb_edge_finder.catalog.artifacts import _encode

    run = MarketDiscovery(_fake()).run(as_of=NOW)
    assert len(_encode(build_catalog(run, include_markets=False))) < len(
        _encode(build_catalog(run, include_markets=True))
    )


def test_a_game_that_leaves_the_slate_has_its_detail_file_pruned(tmp_path: Path):
    """A stale file would keep advertising a finished game's menu as
    current, and `games/` would grow without bound."""
    from cfb_edge_finder.catalog.artifacts import write_catalog_artifacts

    run = MarketDiscovery(_fake()).run(as_of=NOW)
    write_catalog_artifacts(run, tmp_path)
    orphan = tmp_path / "games" / "26SEP01GONEGAME.json"
    orphan.write_text("{}")

    result = write_catalog_artifacts(run, tmp_path)
    assert result.game_files_pruned == 1
    assert not orphan.exists()


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
    catalog = build_catalog(run, include_markets=True)
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
        "fee_is_taker_side_only",
        "maker_fee_applies",
        "quote_age_seconds",
        "seconds_until_close",
        "seconds_until_occurrence",
    }


# =========================================================================
# UNIT SEMANTICS
#
# Kalshi spells money, counts and strikes with overlapping conventions, and
# getting one wrong corrupts data silently rather than raising. A single
# tolerant "parse a number" helper routed STRIKES through the cents path
# and published `floor_strike: 3.5` (a 3.5-point spread) as 0.035 --
# wrong on every spread and total in the catalog, and invisible unless you
# read a rendered artifact against the contract title it came from.
# =========================================================================


def _build(market: dict):
    from cfb_edge_finder.catalog.contract import build_contract

    return build_contract(
        market,
        game_key="26SEP19UGAARK",
        series_ticker="KXNCAAFSPREAD",
        settlement_sources=[],
        fee_type="quadratic",
        fee_multiplier=1,
        captured_at=NOW,
    )


def test_a_strike_is_points_and_is_never_unit_converted():
    contract = _build(
        {
            "ticker": "KXNCAAFSPREAD-26SEP19UGAARK-UGA3",
            "title": "Georgia wins by over 3.5 points",
            "floor_strike": 3.5,
            "cap_strike": 10.5,
            "status": "active",
        }
    )
    assert contract.semantics.floor_strike == pytest.approx(3.5), "a 3.5-point line became something else"
    assert contract.semantics.cap_strike == pytest.approx(10.5)


def test_a_large_total_strike_survives_intact():
    """A 48.5-point total published as 0.485 would look like a price and
    read as plausible, which is what makes this failure dangerous."""
    contract = _build({"ticker": "T", "floor_strike": 48.5, "status": "active"})
    assert contract.semantics.floor_strike == pytest.approx(48.5)


def test_quoted_sizes_are_contract_counts_in_both_spellings():
    fp = _build({"ticker": "A", "yes_bid_size_fp": "730.00", "yes_ask_size_fp": "10.00"})
    bare = _build({"ticker": "B", "yes_bid_size": 730, "yes_ask_size": 10})
    assert fp.quote.yes_bid_size == pytest.approx(730.0)
    assert bare.quote.yes_bid_size == pytest.approx(730.0), "a 730-contract bid was rescaled"
    assert fp.quote.yes_ask_size == bare.quote.yes_ask_size


def test_volume_and_open_interest_are_counts_in_both_spellings():
    fp = _build({"ticker": "A", "volume_fp": "1500.00", "open_interest_fp": "220.00"})
    bare = _build({"ticker": "B", "volume": 1500, "open_interest": 220})
    assert fp.liquidity.volume == pytest.approx(1500.0)
    assert bare.liquidity.volume == pytest.approx(1500.0)
    assert fp.liquidity.open_interest == bare.liquidity.open_interest == pytest.approx(220.0)


def test_money_fields_normalize_to_dollars_from_either_spelling():
    dollars = _build({"ticker": "A", "yes_ask_dollars": "0.1200", "liquidity_dollars": "12.5000"})
    cents = _build({"ticker": "B", "yes_ask": 12, "liquidity": 1250})
    assert dollars.quote.yes_ask == cents.quote.yes_ask == pytest.approx(0.12)
    assert dollars.liquidity.liquidity_dollars == cents.liquidity.liquidity_dollars == pytest.approx(12.5)


def test_a_strike_and_a_price_of_the_same_magnitude_do_not_collide():
    """The regression in one assertion: the same number means different
    things in the two fields, and both must survive."""
    contract = _build(
        {"ticker": "T", "floor_strike": 3.5, "yes_ask_dollars": "0.3500", "status": "active"}
    )
    assert contract.semantics.floor_strike == pytest.approx(3.5)
    assert contract.quote.yes_ask == pytest.approx(0.35)


def test_published_strike_matches_the_contract_title():
    """End-to-end through the artifact, the way the bug was actually
    found: the number in `floor_strike` must match the number Kalshi wrote
    in the title."""
    import re

    catalog = build_catalog(MarketDiscovery(_fake()).run(as_of=NOW), include_markets=True)
    checked = 0
    for game in catalog["games"]:
        for market in game["markets"]:
            if market["floor_strike"] is None:
                continue
            # Skip the period token ("1H", "2Q") so the number matched is
            # the line, not the half.
            title = re.sub(r"\b[1-4][HQ]\b", "", market["title"] or "")
            found = re.search(r"(\d+(?:\.\d+)?)", title)
            if not found:
                continue
            assert market["floor_strike"] == pytest.approx(float(found.group(1))), (
                f"{market['market_ticker']}: floor_strike {market['floor_strike']} "
                f"disagrees with its own title {market['title']!r}"
            )
            checked += 1
    assert checked > 0, "no strike-bearing market was checked -- the fixture lost its ladders"


# =========================================================================
# FEE SCHEDULE
#
# The first production run on main found a fee_type this code did not
# anticipate -- `quadratic_with_maker_fees` -- on ~29% of live markets,
# including the KXNCAAFGAME moneylines. An exact match against "quadratic"
# published a null fee for all of them, leaving a consumer with no cost
# estimate on nearly a third of the menu.
# =========================================================================


def _mechanics_for_fee_type(fee_type):
    from cfb_edge_finder.catalog.contract import build_contract
    from cfb_edge_finder.catalog.mechanics import contract_mechanics

    contract = build_contract(
        {"ticker": "T", "event_ticker": "E", "status": "active",
         "yes_bid_dollars": "0.49", "yes_ask_dollars": "0.51"},
        game_key="g",
        series_ticker="KXNCAAFGAME",
        settlement_sources=[],
        fee_type=fee_type,
        fee_multiplier=1,
        captured_at=NOW,
    )
    return contract_mechanics(contract)


def test_fee_is_computed_for_every_quadratic_schedule_kalshi_uses():
    """Both live spellings. The taker fee is the same quadratic schedule
    under each; `_with_maker_fees` means the resting side pays too."""
    for fee_type in ("quadratic", "quadratic_with_maker_fees"):
        mechanics = _mechanics_for_fee_type(fee_type)
        assert mechanics["estimated_fee_per_contract_at_mid"] is not None, (
            f"fee_type={fee_type!r} published a null fee -- a consumer pricing a thesis gets no cost"
        )
        assert mechanics["fee_formula"] is not None


def test_maker_fee_is_flagged_so_a_taker_estimate_is_not_mistaken_for_total_cost():
    assert _mechanics_for_fee_type("quadratic_with_maker_fees")["maker_fee_applies"] is True
    assert _mechanics_for_fee_type("quadratic")["maker_fee_applies"] is False
    for fee_type in ("quadratic", "quadratic_with_maker_fees"):
        assert _mechanics_for_fee_type(fee_type)["fee_is_taker_side_only"] is True


def test_an_unrecognized_non_quadratic_schedule_still_refuses_to_guess():
    """The conservative default is kept for the case it was written for: a
    genuinely different schedule yields None, never a fabricated number."""
    mechanics = _mechanics_for_fee_type("per_contract_flat_rate")
    assert mechanics["estimated_fee_per_contract_at_mid"] is None
    assert mechanics["fee_formula"] is None
    assert mechanics["fee_is_taker_side_only"] is False


def test_a_missing_fee_type_still_computes_the_published_schedule():
    """Kalshi's documented default. An absent fee_type must not silently
    mean 'free'."""
    assert _mechanics_for_fee_type(None)["estimated_fee_per_contract_at_mid"] is not None
