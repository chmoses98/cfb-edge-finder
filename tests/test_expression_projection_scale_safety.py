"""Mission sections 11-13, 26, 27: projection reuse, correlation-aware
counts, scale, and the safety boundary.
"""

from __future__ import annotations

import ast
import inspect
import json
import resource
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cfb_edge_finder.expression import corpus, economics, exposure, grouping, ladders, taxonomy
from cfb_edge_finder.expression.corpus import load_contract_snapshots
from cfb_edge_finder.expression.grouping import build_universe
from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache, GameProjectionRequest

EXPRESSION_MODULES = (taxonomy, economics, ladders, grouping, corpus, exposure)


# --- Projection reuse (sections 12, 13) ----------------------------------


def _harness_games():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from scan_harness import make_games

    return make_games(4)[0]


def _history():
    from scan_harness import make_history_lines

    return make_history_lines(_harness_games())


HISTORY_WEEKS = 6
"""`make_history_lines` writes 2025 weeks 1-6, so an as_of must sit
strictly after that window or there is no leakage-safe history to fit."""


def _request(game_index=0, week=HISTORY_WEEKS + 1, **over):
    game = _harness_games()[game_index]
    base = dict(
        game_id=game.game_id, home_id=game.home_team_id, away_id=game.away_team_id,
        home_classification="fbs", away_classification="fbs", is_neutral_site=False,
        as_of_season=2025, as_of_week=week, n_simulations=200, seed=0,
    )
    base.update(over)
    return GameProjectionRequest(**base)


def test_one_projection_serves_every_contract_of_a_game():
    """The football model must run once per game, not once per ticker."""
    cache = GameProjectionCache(_history())
    request = _request()
    projections = [cache.get_or_build(request) for _ in range(40)]
    assert cache.projection_builds == 1, f"model ran {cache.projection_builds} times for one game"
    assert cache.projection_cache_hits == 39
    assert len({p.projection_snapshot_id for p in projections}) == 1


def test_all_contracts_of_one_game_share_a_projection_snapshot_id():
    """Section 12: winner, spread ladder and total ladder must derive
    from the SAME simulated distribution, or their probabilities are not
    mutually consistent and no ladder ordering can be trusted."""
    cache = GameProjectionCache(_history())
    request = _request()
    winner = cache.get_or_build(request)
    spread_rung = cache.get_or_build(request)
    total_rung = cache.get_or_build(request)
    assert winner.projection_snapshot_id == spread_rung.projection_snapshot_id == total_rung.projection_snapshot_id


def test_snapshot_id_is_deterministic_not_object_identity():
    """A uuid or id() would differ between runs and make the consistency
    check vacuous."""
    cache_a = GameProjectionCache(_history())
    cache_b = GameProjectionCache(_history())
    a = cache_a.get_or_build(_request())
    b = cache_b.get_or_build(_request())
    assert a.projection_snapshot_id == b.projection_snapshot_id
    assert a is not b


def test_different_games_get_different_snapshot_ids():
    cache = GameProjectionCache(_history())
    a = cache.get_or_build(_request(game_index=0))
    b = cache.get_or_build(_request(game_index=1))
    assert a.projection_snapshot_id != b.projection_snapshot_id
    assert cache.projection_builds == 2


def test_changed_as_of_invalidates_the_snapshot():
    cache = GameProjectionCache(_history())
    a = cache.get_or_build(_request(week=HISTORY_WEEKS + 1))
    b = cache.get_or_build(_request(week=HISTORY_WEEKS + 2))
    assert a.projection_snapshot_id != b.projection_snapshot_id


def test_ratings_are_fitted_once_per_as_of_across_games():
    cache = GameProjectionCache(_history())
    cache.get_or_build(_request(game_index=0))
    cache.get_or_build(_request(game_index=1))
    assert cache.ratings_fits == 1, "shared upstream fitting repeated per game"


# --- Correlation-aware counts (section 11) -------------------------------


def _snapshot_rows(n_games, rungs_per_ladder=8):
    """A realistic universe: each game gets a moneyline pair, two spread
    ladders and one total ladder."""
    rows = []
    for g in range(n_games):
        game = f"game{g:05d}"
        event = f"KXNCAAFGAME-EV{g:05d}"
        for team, price in (("home", 0.60), ("away", 0.44)):
            rows.append({
                "observation_key": f"{event}-{team}",
                "observation": {
                    "game_id": game, "kalshi_market_ticker": f"{event}-{team.upper()}",
                    "family": "moneyline", "team": team, "side": None, "threshold": None,
                    "semantic_operator": None, "model_probability": price,
                    "executable_yes_price": price, "executable_no_price": 1.0 - price + 0.10,
                    "market_midpoint": price, "pricing_status": "model_priced",
                    "parse_status": "confirmed_live", "captured_at": f"2026-09-0{1+(g%9)}T00:00:00Z",
                    "market_status": "active", "fee_status": "VERIFIED_CURRENT",
                    "fee_schedule_version": "v1", "model_version": {"model_version": "m1"},
                    "snapshot_timing": {"label": "T_24H", "hours_before_kickoff": 24.0},
                },
            })
        for team in ("home", "away"):
            for i in range(rungs_per_ladder):
                threshold = 1.5 + 2 * i
                rows.append({
                    "observation_key": f"KXNCAAFSPREAD-EV{g:05d}-{team}{i}",
                    "observation": {
                        "game_id": game, "kalshi_market_ticker": f"KXNCAAFSPREAD-EV{g:05d}-{team.upper()}{i}",
                        "family": "spread", "team": team, "side": None, "threshold": threshold,
                        "semantic_operator": ">", "model_probability": max(0.05, 0.80 - 0.05 * i),
                        "executable_yes_price": max(0.05, 0.78 - 0.05 * i),
                        "executable_no_price": min(0.95, 0.30 + 0.05 * i),
                        "market_midpoint": 0.5, "pricing_status": "model_priced",
                        "parse_status": "confirmed_live", "captured_at": "2026-09-01T00:00:00Z",
                        "market_status": "active", "fee_status": "VERIFIED_CURRENT",
                        "fee_schedule_version": "v1", "model_version": {"model_version": "m1"},
                        "snapshot_timing": {"label": "T_24H", "hours_before_kickoff": 24.0},
                    },
                })
        for i in range(rungs_per_ladder):
            threshold = 40.5 + 3 * i
            rows.append({
                "observation_key": f"KXNCAAFTOTAL-EV{g:05d}-{i}",
                "observation": {
                    "game_id": game, "kalshi_market_ticker": f"KXNCAAFTOTAL-EV{g:05d}-{i}",
                    "family": "total", "team": None, "side": "over", "threshold": threshold,
                    "semantic_operator": ">", "model_probability": max(0.05, 0.85 - 0.06 * i),
                    "executable_yes_price": max(0.05, 0.83 - 0.06 * i),
                    "executable_no_price": min(0.95, 0.25 + 0.06 * i),
                    "market_midpoint": 0.5, "pricing_status": "model_priced",
                    "parse_status": "confirmed_live", "captured_at": "2026-09-01T00:00:00Z",
                    "market_status": "active", "fee_status": "VERIFIED_CURRENT",
                    "fee_schedule_version": "v1", "model_version": {"model_version": "m1"},
                    "snapshot_timing": {"label": "T_24H", "hours_before_kickoff": 24.0},
                },
            })
    return rows


def _write(tmp_path: Path, rows) -> Path:
    path = tmp_path / "obs.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_counts_expose_that_contracts_are_not_independent(tmp_path):
    """Section 11: 20 ladder rungs from one game are not 20 theses."""
    loaded = load_contract_snapshots(_write(tmp_path, _snapshot_rows(10)))
    universe = build_universe(loaded.snapshots)
    assert universe.game_group_count == 10
    assert universe.contract_count > universe.game_group_count * 10
    assert universe.dimension_group_count == 20, "moneyline+spread share MARGIN; totals separate -> 2 per game"
    assert universe.equivalence_group_count > universe.dimension_group_count


def test_moneyline_pair_forms_a_multi_expression_equivalence_group(tmp_path):
    loaded = load_contract_snapshots(_write(tmp_path, _snapshot_rows(1)))
    universe = build_universe(loaded.snapshots)
    multi = universe.multi_expression_groups
    assert multi, "the moneyline pair did not produce a shared-event group"
    for group in multi:
        assert group.expression_count >= 2
        assert group.lowest_break_even_expression is not None


def test_one_snapshot_per_ticker_is_selected(tmp_path):
    """Comparing an EARLY_OPEN ask against a T_30 ask would manufacture
    dominance out of the passage of time."""
    rows = _snapshot_rows(1)
    stale = json.loads(json.dumps(rows[0]))
    stale["observation"]["captured_at"] = "2026-08-01T00:00:00Z"
    stale["observation"]["executable_yes_price"] = 0.10
    stale["observation"]["snapshot_timing"]["label"] = "EARLY_OPEN"
    loaded = load_contract_snapshots(_write(tmp_path, [stale, *rows]))
    assert loaded.snapshots_collapsed == 1
    kept = [s for s in loaded.snapshots if s.semantics.market_ticker == rows[0]["observation"]["kalshi_market_ticker"]]
    assert kept[0].executable_yes_price == rows[0]["observation"]["executable_yes_price"]


def test_malformed_rows_are_counted(tmp_path):
    path = tmp_path / "obs.jsonl"
    path.write_text(json.dumps(_snapshot_rows(1)[0]) + "\n{broken\n", encoding="utf-8")
    assert load_contract_snapshots(path).malformed_rows == 1


# --- Scale (section 26) ---------------------------------------------------


@pytest.mark.parametrize(
    "n_games,rungs,label", [(100, 24, "~5k"), (500, 24, "~25k")]
)
def test_grouping_scales_without_rescanning(tmp_path, n_games, rungs, label):
    rows = _snapshot_rows(n_games, rungs_per_ladder=rungs)
    path = _write(tmp_path, rows)

    started = time.perf_counter()
    loaded = load_contract_snapshots(path)
    universe = build_universe(loaded.snapshots)
    elapsed = time.perf_counter() - started

    assert loaded.ledger_load_count == 1, "ledger read more than once"
    assert universe.game_group_count == n_games
    # Deliberately enormous: trips only on a genuine algorithmic
    # regression (an O(n^2) rescan), never on CI noise.
    assert elapsed < 120.0, f"{label} ({len(rows)} contracts) took {elapsed:.1f}s"


def test_no_quadratic_rescan_in_grouping():
    """4x the contracts must not cost ~16x the time."""
    import tempfile

    timings = {}
    for n_games in (50, 200):
        with tempfile.TemporaryDirectory() as td:
            path = _write(Path(td), _snapshot_rows(n_games, rungs_per_ladder=24))
            started = time.perf_counter()
            build_universe(load_contract_snapshots(path).snapshots)
            timings[n_games] = max(time.perf_counter() - started, 1e-3)
    ratio = timings[200] / timings[50]
    assert ratio < 12.0, f"4x the data cost {ratio:.1f}x the time -- looks superlinear"


def test_memory_stays_proportional_to_contracts(tmp_path):
    path = _write(tmp_path, _snapshot_rows(500, rungs_per_ladder=24))
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    universe = build_universe(load_contract_snapshots(path).snapshots)
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert universe.contract_count > 10_000
    assert (after - before) / 1024 < 2000, "grouping used more than 2GB"


# --- Safety (section 27) --------------------------------------------------

FORBIDDEN = (
    "recommend", "select_bet", "best_bet", "qualify", "qualification", "stake", "staking",
    "allocate", "portfolio_optimize", "bankroll", "kelly", "wager", "place_order", "bet_size",
    "tier_a", "tier_b",
)


def _code_identifiers(module) -> set[str]:
    """AST-based, excluding string literals on purpose: these modules'
    docstrings say things like 'no bet recommendation appears here', and a
    raw text scan would flag the very sentences documenting the boundary."""
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


@pytest.mark.parametrize("module", EXPRESSION_MODULES, ids=lambda m: m.__name__)
def test_no_decision_shaped_surface(module):
    identifiers = {i.lower() for i in _code_identifiers(module)}
    for token in FORBIDDEN:
        hits = [i for i in identifiers if token in i]
        assert not hits, f"{module.__name__} exposes a decision-shaped surface: {hits}"


@pytest.mark.parametrize("module", EXPRESSION_MODULES, ids=lambda m: m.__name__)
def test_no_imports_of_future_decision_modules(module):
    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for name in imported:
        assert "betting" not in name and "qualification" not in name, f"{module.__name__} imports {name}"


def test_no_public_selection_functions():
    for module in EXPRESSION_MODULES:
        for name, obj in vars(module).items():
            if name.startswith("_") or not callable(obj):
                continue
            lowered = name.lower()
            for banned in ("best_", "select", "recommend", "choose", "rank", "optimal", "top_"):
                assert banned not in lowered, f"{module.__name__}.{name} looks like a selection function"


def test_lowest_break_even_is_a_property_not_a_selector():
    """`lowest_break_even_expression` reports which identical payout costs
    least -- arithmetic, not a suggestion. It must stay a read-only
    property with no arguments to tune."""
    prop = vars(grouping.EquivalenceGroup)["lowest_break_even_expression"]
    assert isinstance(prop, property)


def test_research_unit_is_fixed_at_one_contract():
    assert economics.RESEARCH_UNIT_CONTRACTS == 1
    sig = inspect.signature(economics.build_expression_economics)
    assert not any("size" in p or "contracts" in p or "stake" in p for p in sig.parameters)


def test_exposure_module_builds_no_card():
    """Section 20: exposure primitives only -- no set construction."""
    for name in vars(exposure):
        assert "card" not in name.lower()
        assert "portfolio" not in name.lower()
