"""Mission sections 9, 20, 21: model-version enforcement, no betting
language in the closed vocabularies this milestone introduces, and
futures/single-game series isolation."""

from __future__ import annotations

import re

from cfb_edge_finder.kalshi.cfb_coverage_reason import KalshiCfbCoverageReason
from cfb_edge_finder.kalshi.game_mapping import CORE_V1_MARKET_FAMILIES
from cfb_edge_finder.kalshi.research_ledger import ResearchReadiness

FORBIDDEN_TOKENS = frozenset({"bet", "play", "wager", "stake", "tier"})
FORBIDDEN_PHRASES = ("strong buy", "bet up to")


def _no_forbidden_words(value: str) -> bool:
    """Whole-token check (splits on any non-alphanumeric run), so
    'playoff'/'played' never false-positive against 'play' -- mirrors
    how these values are actually built (snake_case slugs)."""
    tokens = set(re.split(r"[^a-z0-9]+", value.lower()))
    lowered = value.lower()
    return not (tokens & FORBIDDEN_TOKENS) and not any(phrase in lowered for phrase in FORBIDDEN_PHRASES)


# --- model version enforcement (mission section 9) --------------------------


def test_capture_script_model_version_matches_build_cfb_baseline_exactly():
    import importlib.util
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root / "src"))

    baseline_spec = importlib.util.spec_from_file_location(
        "build_cfb_baseline_for_test", repo_root / "scripts" / "build_cfb_baseline.py"
    )
    baseline_module = importlib.util.module_from_spec(baseline_spec)
    baseline_spec.loader.exec_module(baseline_module)

    capture_spec = importlib.util.spec_from_file_location(
        "capture_kalshi_cfb_snapshot_for_test", repo_root / "scripts" / "capture_kalshi_cfb_snapshot.py"
    )
    capture_module = importlib.util.module_from_spec(capture_spec)
    capture_spec.loader.exec_module(capture_module)

    assert capture_module.MODEL_VERSION == baseline_module.MODEL_VERSION
    assert capture_module.MODEL_VERSION == "0.4.0-milestone-c2-live-margin-correction"


# --- no betting language in the Milestone D closed vocabularies -------------


def test_coverage_reason_values_have_no_betting_language():
    for reason in KalshiCfbCoverageReason:
        assert _no_forbidden_words(reason.value), reason.value


def test_research_readiness_values_have_no_betting_language():
    for readiness in ResearchReadiness:
        assert _no_forbidden_words(readiness.value), readiness.value


def test_pricing_status_and_parse_status_literals_have_no_betting_language():
    # These are free-form `str` fields by design (not a closed StrEnum --
    # see schemas/kalshi_observation.py), but every literal value this
    # codebase actually assigns to them is enumerated here so a future
    # addition is caught by this test too.
    literals = [
        "model_priced",
        "not_priced",
        "unsupported_population",
        "unsupported_family",
        "futures_separate_engine",
        "confirmed_live",
        "unconfirmed",
        "unresolved",
        "not_applicable",
    ]
    for value in literals:
        assert _no_forbidden_words(value), value


# --- futures isolation (mission section 21) ---------------------------------


def test_futures_series_never_overlap_core_v1_series():
    import importlib.util
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "capture_kalshi_cfb_snapshot_for_futures_test", repo_root / "scripts" / "capture_kalshi_cfb_snapshot.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    core_v1_series = set(module.CORE_V1_SERIES_TO_FAMILY)
    futures_series = set(module.FUTURES_SERIES_TICKERS)
    assert core_v1_series.isdisjoint(futures_series)


def test_core_v1_market_families_never_include_team_total_or_alt_lines():
    from cfb_edge_finder.schemas.common import MarketFamily

    assert CORE_V1_MARKET_FAMILIES == {MarketFamily.MONEYLINE, MarketFamily.SPREAD, MarketFamily.TOTAL}
