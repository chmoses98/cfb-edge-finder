"""Mission sections 13, 20, 21, 28, 30, 31, 32: dataset provenance and
health, scale, and the safety boundary.
"""

from __future__ import annotations

import ast
import inspect
import json
import time
from pathlib import Path

import pytest

from cfb_edge_finder.analytics import calibration_report, dataset, metrics, report, slices, uncertainty
from cfb_edge_finder.analytics.dataset import build_dataset
from cfb_edge_finder.analytics.report import INSUFFICIENT_DATA_MESSAGE, build_report, render_markdown

NOW = "2026-09-20T12:00:00+00:00"


def _obs(key, *, family="moneyline", label="T_24H", model_p=0.6, yes=0.55, no=0.50,
         capture_mode="PROSPECTIVE", pricing="model_priced", game="g1", ticker="KXNCAAFGAME-A-B"):
    return {
        "observation_key": key,
        "capture_mode": capture_mode,
        "season": 2026,
        "observation": {
            "game_id": game, "kalshi_market_ticker": ticker, "family": family,
            "model_probability": model_p, "executable_yes_price": yes, "executable_no_price": no,
            "market_midpoint": 0.52, "pricing_status": pricing, "captured_at": NOW,
            "snapshot_timing": {"label": label, "hours_before_kickoff": 24.0},
        },
    }


def _attr(key, *, state="SETTLED_YES", event_true=True, family="moneyline", label="T_24H",
          game="g1", ticker="KXNCAAFGAME-A-B", closing_status="CLOSING_CAPTURED",
          closing_yes=0.62, closing_no=0.40):
    return {
        "attribution_key": f"{key}|attribution_v1",
        "observation_key": key,
        "game_id": game, "kalshi_market_ticker": ticker, "family": family,
        "timing_label": label, "season": 2026, "state": state, "event_true": event_true,
        "captured_at": NOW, "settled_at": NOW, "model_version": "m1",
        "fee_status": "VERIFIED_CURRENT", "fee_schedule_version": "kalshi_fee_schedule_2026_07_07_taker",
        "closing": {
            "closing_captured": closing_status == "CLOSING_CAPTURED",
            "closing_status": closing_status,
            "closing_yes_price": closing_yes, "closing_no_price": closing_no,
        },
        "yes_economics": {"research_unit_pnl": 0.45, "fee_adjusted_research_unit_pnl": 0.43, "estimated_fee": 0.02},
        "no_economics": {"research_unit_pnl": -0.50, "fee_adjusted_research_unit_pnl": -0.52, "estimated_fee": 0.02},
    }


def _write(tmp_path: Path, observations, attributions) -> tuple[Path, Path]:
    o = tmp_path / "obs.jsonl"
    a = tmp_path / "attr.jsonl"
    o.write_text("".join(json.dumps(x) + "\n" for x in observations), encoding="utf-8")
    a.write_text("".join(json.dumps(x) + "\n" for x in attributions), encoding="utf-8")
    return o, a


# --- Happy path -----------------------------------------------------------


def test_settled_supported_rows_are_joined(tmp_path):
    o, a = _write(tmp_path, [_obs("k1")], [_attr("k1")])
    ds = build_dataset(o, a)
    assert ds.settled_supported_n == 1
    row = ds.rows[0]
    assert row.observation_key == "k1" and row.game_id == "g1"
    assert row.gaps.yes_probability_gap == pytest.approx(0.05)
    assert row.yes_clv.available and row.yes_clv.raw_price_movement == pytest.approx(0.07)
    assert ds.ledger_load_count == 2, "ledgers must be read exactly once each"


# --- Provenance enforcement (section 21) ---------------------------------


def test_non_prospective_rows_are_excluded_and_counted(tmp_path):
    o, a = _write(
        tmp_path,
        [_obs("k1"), _obs("k2", capture_mode="RETROSPECTIVE_FIXTURE")],
        [_attr("k1"), _attr("k2")],
    )
    ds = build_dataset(o, a)
    assert ds.settled_supported_n == 1
    assert ds.health.rejected_non_prospective == 1
    assert all(r.observation_key != "k2" for r in ds.rows)


def test_report_warns_about_excluded_retrospective_rows(tmp_path):
    o, a = _write(tmp_path, [_obs("k1", capture_mode="BACKTEST")], [_attr("k1")])
    rep = build_report(build_dataset(o, a))
    assert any("non-prospective" in w for w in rep.warnings)


# --- Unsupported populations (section 13) --------------------------------


def test_unsupported_family_is_partitioned_not_mixed(tmp_path):
    o, a = _write(
        tmp_path,
        [_obs("k1"), _obs("k2", family="exotic")],
        [_attr("k1"), _attr("k2", family="exotic")],
    )
    ds = build_dataset(o, a)
    assert ds.settled_supported_n == 1
    assert len(ds.diagnostic_rows) == 1
    assert ds.health.unsupported_leaked_into_primary == 0


# --- Health conditions (section 30) --------------------------------------


def test_duplicate_observation_keys_are_fatal(tmp_path):
    o, a = _write(tmp_path, [_obs("k1"), _obs("k1")], [_attr("k1")])
    ds = build_dataset(o, a)
    assert ds.health.duplicate_observation_keys == 1 and ds.health.has_fatal


def test_duplicate_attribution_keys_are_fatal(tmp_path):
    o, a = _write(tmp_path, [_obs("k1")], [_attr("k1"), _attr("k1")])
    ds = build_dataset(o, a)
    assert ds.health.duplicate_attribution_keys == 1 and ds.health.has_fatal


def test_settlement_mismatch_is_fatal_and_excluded(tmp_path):
    o, a = _write(tmp_path, [_obs("k1")], [_attr("k1", state="SETTLEMENT_MISMATCH")])
    ds = build_dataset(o, a)
    assert ds.health.settlement_mismatches == 1 and ds.health.has_fatal
    assert ds.settled_supported_n == 0, "a disputed contract entered the analysis"


def test_impossible_probability_is_fatal(tmp_path):
    o, a = _write(tmp_path, [_obs("k1", model_p=1.5)], [_attr("k1")])
    ds = build_dataset(o, a)
    assert ds.health.impossible_probabilities == 1 and ds.health.has_fatal


def test_malformed_close_link_is_flagged(tmp_path):
    bad = _attr("k1")
    bad["closing"]["closing_yes_price"] = None
    o, a = _write(tmp_path, [_obs("k1")], [bad])
    ds = build_dataset(o, a)
    assert ds.health.malformed_close_links == 1


def test_malformed_json_lines_are_counted(tmp_path):
    o = tmp_path / "obs.jsonl"
    a = tmp_path / "attr.jsonl"
    o.write_text(json.dumps(_obs("k1")) + "\n{broken\n", encoding="utf-8")
    a.write_text(json.dumps(_attr("k1")) + "\n", encoding="utf-8")
    ds = build_dataset(o, a)
    assert ds.health.malformed_rows == 1 and ds.settled_supported_n == 1


def test_unsettled_states_do_not_enter_the_analysis(tmp_path):
    o, a = _write(tmp_path, [_obs("k1")], [_attr("k1", state="GAME_NOT_FINAL")])
    assert build_dataset(o, a).settled_supported_n == 0


def test_report_flags_fatal_conditions(tmp_path):
    o, a = _write(tmp_path, [_obs("k1"), _obs("k1")], [_attr("k1")])
    rep = build_report(build_dataset(o, a))
    assert any("FATAL" in w for w in rep.warnings)


# --- Missing close excluded from CLV, not zeroed (section 6) -------------


def test_missing_close_is_excluded_from_clv_aggregates(tmp_path):
    obs = [_obs(f"k{i}", game=f"g{i}", ticker=f"T{i}") for i in range(6)]
    attrs = [_attr(f"k{i}", game=f"g{i}", ticker=f"T{i}") for i in range(3)]
    attrs += [
        _attr(f"k{i}", game=f"g{i}", ticker=f"T{i}",
              closing_status="CLOSING_MISSING_MARKET_CLOSED", closing_yes=None, closing_no=None)
        for i in range(3, 6)
    ]
    ds = build_dataset(*_write(tmp_path, obs, attrs))
    assert ds.settled_supported_n == 6 and ds.closing_available_n == 3
    summary = slices.summarize_slice("all", "test", ds.rows, side="yes")
    assert summary.n == 6
    assert summary.clv_n == 3, "missing closes leaked into the CLV sample"
    assert summary.mean_clv == pytest.approx(0.07), "a missing close was averaged in as zero"


# --- Empty corpus (section 29) -------------------------------------------


def test_empty_corpus_reports_insufficient_data_without_failing(tmp_path):
    o, a = _write(tmp_path, [], [])
    rep = build_report(build_dataset(o, a))
    assert rep.sufficient_data is False
    assert rep.message == INSUFFICIENT_DATA_MESSAGE
    md = render_markdown(rep)
    assert INSUFFICIENT_DATA_MESSAGE in md
    assert "fabricat" in md.lower(), "the report should state that nothing was fabricated"


def test_report_renders_with_real_data(tmp_path):
    obs = [_obs(f"k{i}", game=f"g{i % 7}", ticker=f"T{i}") for i in range(30)]
    attrs = [_attr(f"k{i}", game=f"g{i % 7}", ticker=f"T{i}", event_true=(i % 2 == 0)) for i in range(30)]
    rep = build_report(build_dataset(*_write(tmp_path, obs, attrs)))
    assert rep.sufficient_data is True and rep.families
    fam = rep.families[0]
    assert fam.comparison is not None and fam.overall.analysis_status == slices.CORE
    assert all(s.analysis_status == slices.EXPLORATORY for s in fam.timing)
    md = render_markdown(rep)
    assert "Research-only" in md and "EXPLORATORY" in " ".join(rep.warnings + [md])


def test_totals_readiness_caveat_travels_with_totals(tmp_path):
    obs = [_obs(f"k{i}", family="total", game=f"g{i}", ticker=f"T{i}") for i in range(5)]
    attrs = [_attr(f"k{i}", family="total", game=f"g{i}", ticker=f"T{i}") for i in range(5)]
    rep = build_report(build_dataset(*_write(tmp_path, obs, attrs)))
    total = next(f for f in rep.families if f.family == "total")
    assert "WEAKER" in total.readiness
    assert any("readiness" in c.lower() for c in total.overall.caveats)


# --- Scale (section 28) ---------------------------------------------------


@pytest.mark.parametrize("n", [1_000, 10_000, 100_000])
def test_dataset_build_scales_linearly(tmp_path, n):
    """Must not become an O(observations x attributions) rescan."""
    obs = [_obs(f"k{i}", game=f"g{i % 200}", ticker=f"T{i}") for i in range(n)]
    attrs = [_attr(f"k{i}", game=f"g{i % 200}", ticker=f"T{i}", event_true=(i % 3 == 0)) for i in range(n)]
    o, a = _write(tmp_path, obs, attrs)

    started = time.perf_counter()
    ds = build_dataset(o, a)
    elapsed = time.perf_counter() - started

    assert ds.settled_supported_n == n
    assert ds.ledger_load_count == 2, "ledgers were read more than once"
    # Deliberately enormous: trips only on a genuine algorithmic
    # regression, never on CI noise.
    assert elapsed < 60.0, f"{n} rows took {elapsed:.1f}s"


def test_slice_aggregation_scales(tmp_path):
    obs = [_obs(f"k{i}", game=f"g{i % 300}", ticker=f"T{i}") for i in range(20_000)]
    attrs = [_attr(f"k{i}", game=f"g{i % 300}", ticker=f"T{i}", event_true=(i % 2 == 0)) for i in range(20_000)]
    ds = build_dataset(*_write(tmp_path, obs, attrs))
    started = time.perf_counter()
    rep = build_report(ds)
    elapsed = time.perf_counter() - started
    assert rep.sufficient_data and elapsed < 120.0, f"report build took {elapsed:.1f}s"


# --- Safety boundary (sections 31, 32) -----------------------------------

FORBIDDEN_TOKENS = (
    "recommend", "qualify", "qualification", "stake", "staking", "bankroll", "kelly",
    "wager", "place_order", "submit_order", "portfolio", "tier_a", "tier_b",
    "best_bet", "bet_size", "select_best", "optimal", "threshold_search",
)


def _code_identifiers(module) -> set[str]:
    """Names, attributes, functions and arguments actually referenced in
    a module's CODE.

    AST-based, and string literals are excluded on purpose: these modules'
    docstrings say things like "no bet recommendation appears below", and
    a raw text scan would flag the very sentences documenting the
    boundary -- training everyone to ignore the check."""
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
    return names


ANALYTICS_MODULES = (metrics, calibration_report, uncertainty, dataset, slices, report)


@pytest.mark.parametrize("module", ANALYTICS_MODULES, ids=lambda m: m.__name__)
def test_analytics_code_has_no_recommendation_surface(module):
    identifiers = {i.lower() for i in _code_identifiers(module)}
    for token in FORBIDDEN_TOKENS:
        hits = [i for i in identifiers if token in i]
        assert not hits, f"{module.__name__} exposes a decision-shaped surface: {hits}"


@pytest.mark.parametrize("module", ANALYTICS_MODULES, ids=lambda m: m.__name__)
def test_analytics_does_not_import_future_decision_modules(module):
    """Section 32: analytics must not import recommendation/staking."""
    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for name in imported:
        assert "betting" not in name, f"{module.__name__} imports {name}"
        assert "qualification" not in name, f"{module.__name__} imports {name}"


def test_no_function_returns_a_best_or_selected_slice():
    """Section 31: the analytics layer must be incapable of picking a
    cutoff. Every public entry point returns ALL slices in fixed order."""
    for module in ANALYTICS_MODULES:
        for name, obj in vars(module).items():
            if name.startswith("_") or not callable(obj):
                continue
            lowered = name.lower()
            for banned in ("best", "select", "recommend", "choose", "optimal", "rank", "top_"):
                assert banned not in lowered, f"{module.__name__}.{name} looks like a selection function"


def test_slices_are_returned_in_fixed_order_not_sorted_by_performance(tmp_path):
    obs = [_obs(f"k{i}", game=f"g{i % 9}", ticker=f"T{i}",
                yes=0.1 + 0.05 * (i % 9)) for i in range(45)]
    attrs = [_attr(f"k{i}", game=f"g{i % 9}", ticker=f"T{i}", event_true=(i % 4 == 0)) for i in range(45)]
    rep = build_report(build_dataset(*_write(tmp_path, obs, attrs)))
    fam = rep.families[0]
    assert [s.label for s in fam.price_buckets] == [b[0] for b in slices.PRICE_BUCKETS]
    assert [s.label for s in fam.timing] == list(slices.TIMING_ORDER)
    assert [s.label for s in fam.signed_gap_buckets] == [b[0] for b in slices.SIGNED_GAP_BUCKETS]
