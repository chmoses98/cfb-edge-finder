import importlib.util
import json
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "wager_loss_structure", pathlib.Path(__file__).resolve().parents[1] / "scripts/research/wager_loss_structure.py"
)
wls = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(wls)


def _row(
    ticker="KXNCAAFSPREAD-26SEP11AAABBB-AAA7",
    stake=100.0,
    fee=3.0,
    pl=-100.0,
    price=0.5,
    game="g1",
    placed="2026-09-11T00:00:00Z",
    side="YES",
):
    return {
        "kalshi_ticker": ticker,
        "selection": side,
        "average_price": price,
        "contracts": stake / price,
        "stake": stake,
        "fees": fee,
        "profit_loss": pl,
        "settlement_status": "SETTLED",
        "placed_at": placed,
        "source_ids": {"kalshi_game_key": game},
    }


def _load(tmp_path, items):
    p = tmp_path / "w.json"
    p.write_text(json.dumps({"items": items}))
    return wls.load(str(p))


def test_family_prefixes_are_specific_before_generic():
    assert wls.family("KXNCAAFTEAMTOTAL-X") == "TEAM_TOTAL"
    assert wls.family("KXNCAAF1HSPREAD-X") == "1H_SPREAD"
    assert wls.family("KXNCAAFSPREAD-X") == "GAME_SPREAD"
    assert wls.family("KXMVECROSSCATEGORY-X") == "COMBO"
    assert wls.family("KXSOMETHING") == "OTHER"


def test_unsettled_wagers_are_excluded(tmp_path):
    bad = _row()
    bad["settlement_status"] = "PENDING"
    assert len(_load(tmp_path, [_row(), bad])) == 1


def test_per_bet_cap_scales_a_6_8U_bet_to_5U_and_leaves_small_bets_alone(tmp_path):
    rows = _load(tmp_path, [_row(stake=512.0, pl=-512.0, game="a"), _row(stake=50.0, pl=20.0, game="b")])
    capped = dict(wls.scenarios(rows, 75.0))["cap every bet at 5U"]
    assert round(capped[0]["stake"], 6) == 375.0 and round(capped[0]["pl"], 6) == -375.0
    assert capped[1]["stake"] == 50.0


def test_before_fees_adds_back_exactly_the_fees(tmp_path):
    rows = _load(tmp_path, [_row(fee=2.0, pl=-10.0, game="a"), _row(fee=1.0, pl=5.0, game="b")])
    gross = dict(wls.scenarios(rows, 75.0))["actual, BEFORE fees (isolates the fee drag)"]
    assert sum(r["pl"] for r in gross) == sum(r["pl"] for r in rows) + 3.0


def test_game_budget_never_exceeds_5U_and_keeps_proportions(tmp_path):
    rows = _load(
        tmp_path, [_row(stake=300.0, pl=-300.0), _row(stake=300.0, pl=100.0, ticker="KXNCAAFTOTAL-26SEP11AAABBB-55")]
    )
    budget = dict(wls.scenarios(rows, 75.0))["cap total outlay per game at 5U"]
    assert round(sum(r["stake"] for r in budget), 6) == 375.0
    assert round(budget[0]["stake"], 6) == round(budget[1]["stake"], 6)


def test_combos_are_dropped_by_the_structural_scenario(tmp_path):
    rows = _load(tmp_path, [_row(), _row(ticker="KXMVECROSSCATEGORY-X", game="c")])
    structural = dict(wls.scenarios(rows, 75.0))["5U per bet + 5U per game + no combos"]
    assert len(structural) == 1


def test_cluster_interval_is_deterministic_and_ordered(tmp_path):
    items = [_row(pl=-100.0 if i % 2 else 120.0, game=f"g{i}") for i in range(20)]
    rows = _load(tmp_path, items)
    a, b = wls.cluster_ci(rows), wls.cluster_ci(rows)
    assert a == b and a[0] <= wls.roi(rows) <= a[1]


def test_output_contains_no_tickers_or_identifiers(tmp_path):
    rows = _load(tmp_path, [_row(game="26SEP11AAABBB")])
    text = wls.render(rows, 75.0)
    assert "KXNCAAF" not in text and "26SEP11" not in text


def test_time_split_is_chronological_and_covers_every_bet(tmp_path):
    items = [_row(game=f"g{i}", placed=f"2026-09-{10 + i:02d}T00:00:00Z") for i in range(10)]
    first, second = wls.time_split(_load(tmp_path, items))
    assert len(first[1]) + len(second[1]) == 10
    assert max(r["placed"] for r in first[1]) <= min(r["placed"] for r in second[1])


def test_size_return_correlation_detects_a_planted_relationship(tmp_path):
    items = [_row(stake=10.0 + 10 * i, pl=(10.0 + 10 * i) * (0.01 * i), game=f"g{i}") for i in range(30)]
    rho, p = wls.stake_size_rank_correlation(_load(tmp_path, items))
    assert rho > 0.99 and p < 0.01


def test_size_return_correlation_is_null_for_unrelated_data(tmp_path):
    items = [_row(stake=10.0 + (i * 7) % 13, pl=(-1) ** i * 5.0, game=f"g{i}") for i in range(40)]
    rho, p = wls.stake_size_rank_correlation(_load(tmp_path, items))
    assert abs(rho) < 0.5 and p > 0.01
