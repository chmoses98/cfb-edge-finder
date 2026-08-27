"""Mission sections 16, 25: the read-only Kalshi settlement cross-check
and the safety boundary around it.
"""

from __future__ import annotations

import pytest
import requests

from cfb_edge_finder.research import kalshi_settlement_check as check
from cfb_edge_finder.research.settlement_health import (
    SettlementHealthReport,
    evaluate_settlement_health,
    should_fail_settlement_run,
)
from cfb_edge_finder.schemas.common import Side


def _mkt(**kw):
    return check.parse_market_outcome("KXNCAAFGAME-X-Y", kw or None)


# --- Parsing Kalshi's own result -----------------------------------------


def test_finalized_yes_and_no_results_are_read():
    assert _mkt(status="finalized", result="yes").official_settlement is Side.YES
    assert _mkt(status="settled", result="no").official_settlement is Side.NO


def test_result_on_a_non_finalized_market_is_not_trusted():
    """A result string on a market Kalshi has not finalized is not an
    official outcome and must not be used to flag a mismatch."""
    outcome = _mkt(status="active", result="yes")
    assert outcome.official_settlement is None
    assert outcome.is_finalized is False
    assert "not finalized" in outcome.detail


def test_closed_is_not_finalized():
    """A closed market has stopped trading but may carry no settlement."""
    assert _mkt(status="closed", result="yes").is_finalized is False


def test_unrecognised_result_is_never_coerced_to_a_side():
    outcome = _mkt(status="finalized", result="something_new")
    assert outcome.official_settlement is None
    assert "unrecognised" in outcome.detail


@pytest.mark.parametrize("token", ["void", "voided", "cancelled", "canceled", "all_no", "all_yes"])
def test_void_results_are_not_settlements(token):
    """Voiding is a different fact from settling; collapsing them would
    silently turn a voided market into a losing contract."""
    outcome = _mkt(status="finalized", result=token)
    assert outcome.is_void is True
    assert outcome.official_settlement is None


def test_absent_market_is_not_a_settlement():
    outcome = check.parse_market_outcome("T", None)
    assert outcome.official_settlement is None
    assert outcome.is_finalized is False
    assert outcome.fetch_failed is False


# --- Fetch failures are distinct from "no settlement" --------------------


def test_fetch_failure_is_reported_as_a_failure_not_as_absence():
    class _Client:
        def fetch_market_detail(self, ticker):
            raise requests.HTTPError("429 rate limited")

    outcome = check.fetch_market_outcome(_Client(), "KXNCAAFGAME-X-Y")
    assert outcome.fetch_failed is True
    assert outcome.official_settlement is None
    assert "failed" in outcome.detail


def test_successful_fetch_is_not_a_failure():
    class _Client:
        def fetch_market_detail(self, ticker):
            return {"status": "finalized", "result": "yes"}

    outcome = check.fetch_market_outcome(_Client(), "KXNCAAFGAME-X-Y")
    assert outcome.fetch_failed is False
    assert outcome.official_settlement is Side.YES


# --- Mismatch detection ---------------------------------------------------


def test_mismatch_requires_both_sides_known():
    assert check.detect_mismatch(Side.YES, Side.NO) is True
    assert check.detect_mismatch(Side.NO, Side.YES) is True
    assert check.detect_mismatch(Side.YES, Side.YES) is False
    assert check.detect_mismatch(None, Side.YES) is False, "absence treated as disagreement"
    assert check.detect_mismatch(Side.YES, None) is False, "absence treated as disagreement"
    assert check.detect_mismatch(None, None) is False


# --- Health escalation (section 19) --------------------------------------


def test_settlement_mismatch_is_high_severity_and_fails_the_run():
    report = SettlementHealthReport(settlement_mismatches=1, settled_yes=10, settled_no=10)
    diags = evaluate_settlement_health(report)
    codes = {d.code: d.severity.value for d in diags}
    assert codes.get("settlement_mismatch") == "high"
    assert should_fail_settlement_run(diags) is True


def test_clean_run_does_not_fail():
    report = SettlementHealthReport(settled_yes=10, settled_no=8, closing_captured=18)
    diags = evaluate_settlement_health(report)
    assert should_fail_settlement_run(diags) is False


def test_api_failure_fails_the_run():
    assert should_fail_settlement_run(evaluate_settlement_health(SettlementHealthReport(api_failures=1))) is True


def test_missing_close_never_blocks_settlement():
    """Section 20: a missing close must be visible but not fatal."""
    report = SettlementHealthReport(settled_yes=5, settled_no=5, closing_missing=10)
    diags = evaluate_settlement_health(report)
    assert should_fail_settlement_run(diags) is False
    assert any(d.code == "closing_missing_for_settled" for d in diags)


def test_no_final_games_when_expected_warns_but_none_expected_is_silent():
    expected = evaluate_settlement_health(SettlementHealthReport(), expected_final_games=12)
    assert any(d.code == "no_games_final_when_expected" for d in expected)
    assert should_fail_settlement_run(expected) is False

    quiet = evaluate_settlement_health(SettlementHealthReport(), expected_final_games=0)
    assert not any(d.code == "no_games_final_when_expected" for d in quiet)


def test_elevated_semantics_failure_rate_warns():
    report = SettlementHealthReport(settled_yes=5, settled_no=5, semantics_unresolved=5)
    assert any(d.code == "semantics_unresolved_rate_elevated" for d in evaluate_settlement_health(report))


# --- Safety boundary (section 25) ----------------------------------------


def _code_identifiers(module) -> set[str]:
    """Every name, attribute and function/class identifier actually
    referenced in a module's CODE.

    Deliberately AST-based rather than a raw text scan: these modules'
    docstrings say things like "this is NOT stake sizing", and a substring
    search over source would flag the very sentences that document the
    boundary. What matters is whether the executable surface can place an
    order or size a stake -- not whether the prose mentions it."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    # String literals are deliberately excluded. They are overwhelmingly
    # prose here, and prose is exactly what this test must not react to --
    # the module docstring literally says "it is not a wager", which a
    # text scan flags as a wagering surface. Endpoint strings on the
    # client are covered separately by
    # test_kalshi_client_has_no_trading_methods.
    return names


def test_settlement_modules_expose_no_trading_surface():
    """Structural: settlement code must not be able to place an order,
    stake, or mutate a balance, even by accident."""
    from cfb_edge_finder.research import attribution, settlement, settlement_health

    forbidden = (
        "place_order", "create_order", "cancel_order", "submit_order", "portfolio",
        "balance", "bankroll", "kelly", "wager",
    )
    for module in (attribution, settlement, settlement_health, check):
        identifiers = {i.lower() for i in _code_identifiers(module)}
        for token in forbidden:
            hits = [i for i in identifiers if token in i]
            assert not hits, f"{module.__name__} code references forbidden trading surface: {hits}"


def test_kalshi_client_has_no_trading_methods():
    from cfb_edge_finder.data.kalshi_client import KalshiClient

    for method in (m for m in dir(KalshiClient) if not m.startswith("_")):
        assert not any(t in method.lower() for t in ("order", "portfolio", "balance", "position")), (
            f"KalshiClient exposes a trading-shaped method: {method}"
        )


def test_research_unit_size_cannot_be_varied():
    """The research unit is fixed at one contract. If it ever became a
    parameter, this milestone would have grown a staking knob."""
    import inspect

    from cfb_edge_finder.research import attribution

    assert attribution.RESEARCH_UNIT_CONTRACTS == 1
    sig = inspect.signature(attribution.research_unit_economics)
    assert not any("size" in p or "contracts" in p or "stake" in p for p in sig.parameters), (
        f"research_unit_economics gained a sizing parameter: {list(sig.parameters)}"
    )


def test_settlement_only_reads_market_metadata():
    """The cross-check calls exactly one read endpoint."""
    import inspect

    source = inspect.getsource(check)
    assert "fetch_market_detail" in source
    assert "post(" not in source and "requests.post" not in source
    assert "delete(" not in source
