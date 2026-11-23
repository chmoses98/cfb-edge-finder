import datetime as dt
import importlib.util
import json
import math
import pathlib

import numpy as np

_SPEC = importlib.util.spec_from_file_location(
    "spread_price_eval", pathlib.Path(__file__).resolve().parents[1] / "scripts/research/spread_price_eval.py"
)
sp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sp)

UTC = dt.UTC


def _bet(
    price=0.5, won=True, side="YES", family="GAME_SPREAD", game="g", placed=None, stake=None, fee=0.0, contracts=10.0
):
    stake = stake if stake is not None else price * contracts + fee
    pl = (contracts - stake) if won else -stake
    return {
        "family": family,
        "side": side,
        "price": price,
        "won": won,
        "game": game,
        "stake": stake,
        "fee": fee,
        "contracts": contracts,
        "pl": pl,
        "placed": placed or dt.datetime(2026, 10, 1, tzinfo=UTC),
    }


def _game(i, home, away, hp, ap, day, fbs2=True, hfa=1.0, week=5):
    return {
        "gid": str(i),
        "week": week,
        "ko": dt.datetime(2026, 9, 1, 18, tzinfo=UTC) + dt.timedelta(days=day),
        "home": home,
        "away": away,
        "home_id": home,
        "away_id": away,
        "hfa": hfa,
        "hp": float(hp),
        "ap": float(ap),
        "fbs2": fbs2,
    }


# ---- ticker / semantics / fees -------------------------------------------------------------------------------------
def test_parse_ticker_and_family():
    t = sp.parse_ticker("KXNCAAFSPREAD-26SEP11RUTGBC-RUTG7")
    assert t["family"] == "GAME_SPREAD" and t["strike"] == 7.0 and t["team"] == "RUTG" and t["codes"] == "RUTGBC"
    assert t["date"] == dt.date(2026, 9, 11)
    tot = sp.parse_ticker("KXNCAAFTOTAL-26SEP11RUTGBC-55")
    assert tot["family"] == "GAME_TOTAL" and tot["team"] is None and tot["strike"] == 55.0
    assert sp.parse_ticker("KXMVECROSSCATEGORY-S2026ABC") is None
    assert sp.family("KXNCAAF1HSPREAD-26SEP11AABB-AA3") == "1H_SPREAD"


def test_yes_wins_includes_the_strike_so_no_push():
    assert sp.yes_wins(7, 7) and sp.yes_wins(8, 7) and not sp.yes_wins(6, 7)


def test_fee_formula_matches_documented_schedule():
    assert math.isclose(sp.fee_per_contract(0.5), 0.0175)
    assert math.isclose(0.07 * 54.8 * 0.53 * 0.47, 0.9556, abs_tol=1e-4)  # a real receipt fee
    assert sp.fee_order(0.5, 10) == 0.18  # 17.5c rounds UP to 18c
    assert sp.fee_order(0.5, 4) == 0.07  # 7.0c exactly, no spurious extra cent from float error
    assert math.isclose(sp.fee_per_contract(0.1), sp.fee_per_contract(0.9))
    assert sp.fee_per_contract(0.1) < sp.fee_per_contract(0.5)


def test_fees_counted_once_gross_minus_net_is_fees():
    rows = [_bet(0.5, True, fee=0.2), _bet(0.4, False, fee=0.1, game="h")]
    net, gross = sp.roi(rows), sp.gross_roi(rows)
    assert math.isclose(gross - net, sum(r["fee"] for r in rows) / sum(r["stake"] for r in rows))


# ---- join ----------------------------------------------------------------------------------------------------------
def _comp(abbr, loc, tid, score="0"):
    return {"abbreviation": abbr, "location": loc, "names": [loc, abbr], "team_id": tid, "score": score}


def test_find_game_unique_alias_and_ambiguity():
    ev = [
        {
            "id": "1",
            "date": "2026-10-10T19:00Z",
            "completed": True,
            "competitors": [_comp("MIZ", "Missouri", "m"), _comp("TA&M", "Texas A&M", "t")],
        },
        {
            "id": "2",
            "date": "2026-10-10T23:00Z",
            "completed": True,
            "competitors": [_comp("OU", "Oklahoma", "o"), _comp("TEX", "Texas", "x")],
        },
    ]
    by = sp.index_schedule(ev)
    e, a, b = sp.find_game(by, dt.date(2026, 10, 10), "TXAMMIZZ")  # aliases for both teams
    assert e["id"] == "1" and {a["team_id"], b["team_id"]} == {"m", "t"}
    assert sp.find_game(by, dt.date(2026, 10, 10), "ZZZYYY") is None
    # same codes matching two different games -> refuse rather than guess
    ev2 = ev + [
        {
            "id": "3",
            "date": "2026-10-10T20:00Z",
            "completed": True,
            "competitors": [_comp("MIZ", "Missouri", "m2"), _comp("TA&M", "Texas A&M", "t2")],
        }
    ]
    assert sp.find_game(sp.index_schedule(ev2), dt.date(2026, 10, 10), "TXAMMIZZ") is None


def test_load_bets_derives_result_checks_settlement_and_drops_disagreement(tmp_path):
    ev = [
        {
            "id": "1",
            "date": "2026-10-10T19:00Z",
            "completed": True,
            "competitors": [_comp("AAA", "Aaa", "a", "24"), _comp("BBB", "Bbb", "b", "17")],
        }
    ]

    def w(ticker, sel, pl):
        return {
            "kalshi_ticker": ticker,
            "selection": sel,
            "settlement_status": "SETTLED",
            "average_price": 0.5,
            "contracts": 10.0,
            "stake": 5.0,
            "fees": 0.1,
            "profit_loss": pl,
            "placed_at": "2026-10-10T15:00:00Z",
            "source_ids": {},
        }

    items = [
        w("KXNCAAFSPREAD-26OCT10AAABBB-AAA7", "YES", 5.0),  # margin 7 >= 7 -> YES wins (tie at the strike is a win)
        w("KXNCAAFSPREAD-26OCT10AAABBB-AAA8", "YES", -5.0),  # margin 7 < 8 -> loses
        w("KXNCAAFTOTAL-26OCT10AAABBB-41", "NO", 5.0),  # total 41 >= 41 -> YES wins -> NO loses ... but recorded as win
        w("KXNCAAFTOTAL-26OCT10AAABBB-42", "NO", 5.0),  # total 41 < 42 -> NO wins
    ]
    path = tmp_path / "w.json"
    path.write_text(json.dumps({"items": items}))
    bets, diag = sp.load_bets(str(path), ev)
    assert diag["settlement_disagrees"] == 1 and diag["joined"] == 3
    assert {(b["family"], b["strike"]) for b in bets} == {
        ("GAME_SPREAD", 7.0),
        ("GAME_SPREAD", 8.0),
        ("GAME_TOTAL", 42.0),
    }


# ---- rating, point in time -----------------------------------------------------------------------------------------
def test_ridge_recovers_a_noise_free_rating_structure():
    rng = np.random.default_rng(0)
    off = {t: float(v) for t, v in zip("ABCDEF", rng.normal(0, 5, 6), strict=True)}
    dfn = {t: float(v) for t, v in zip("ABCDEF", rng.normal(0, 5, 6), strict=True)}
    games, i = [], 0
    for _rep in range(6):
        for h in "ABCDEF":
            for a in "ABCDEF":
                if h != a:
                    hp = 28 + off[h] + dfn[a] + 1.5
                    ap = 28 + off[a] + dfn[h] - 1.5
                    games.append(_game(i, h, a, hp, ap, i % 30))
                    i += 1
    st = sp.fit_ridge(games, 1e-6)
    pm, pt = sp.predict(st, "A", "B", 1.0)
    assert math.isclose(pm, (28 + off["A"] + dfn["B"] + 1.5) - (28 + off["B"] + dfn["A"] - 1.5), abs_tol=0.05)
    assert math.isclose(pt, 56 + off["A"] + dfn["B"] + off["B"] + dfn["A"], abs_tol=0.05)
    assert sp.predict(st, "ZZ", "A", 1.0)[0] != 0.0  # unseen team falls back to prior but still predicts


def test_walk_uses_only_finished_games_and_ignores_the_future():
    g = [_game(i, "A", "B", 30, 20, i) for i in range(10)]
    w = sp.Walk(g, 10.0)
    ko = g[3]["ko"]
    assert w.n_before(ko + dt.timedelta(hours=3, minutes=59)) == 3  # game 3 not finished yet
    assert w.n_before(ko + dt.timedelta(hours=4)) == 4
    s1 = w.state(ko + dt.timedelta(hours=5))
    g2 = [dict(x) for x in g]
    for x in g2[6:]:
        x["hp"] = 100.0  # change only LATER games
    s2 = sp.Walk(g2, 10.0).state(ko + dt.timedelta(hours=5))
    assert np.allclose(s1["beta"], s2["beta"])


def test_out_of_sample_prediction_for_a_game_does_not_use_that_game():
    g = [_game(i, "A", "B", 30, 20, i) for i in range(6)]
    base = sp.Walk(g, 5.0).oos()[4]["pm"]
    g2 = [dict(x) for x in g]
    g2[4]["hp"] = 90.0
    assert math.isclose(sp.Walk(g2, 5.0).oos()[4]["pm"], base)


def test_margin_pmf_is_a_distribution_and_shape_lifts_observed_key_numbers():
    pmf = sp.margin_pmf(3.0, 14.0, None)
    assert math.isclose(sum(pmf.values()), 1.0)
    pool = [{"hp": 27.0, "ap": 20.0, "pm": 0.0} for _ in range(60)]  # every game lands on |margin| == 7, never 0
    w = sp.shape_factors(pool, 14.0)
    assert w[7] > 1.5 and w[0] < 1.0 and all(v > 0 for v in w.values())
    shaped = sp.margin_pmf(0.0, 14.0, w)
    assert math.isclose(sum(shaped.values()), 1.0) and shaped[7] > pmf[7] and shaped[0] < pmf[0]


def test_shape_factor_is_one_with_no_history_and_shrinks_toward_one():
    w = sp.shape_factors([], 14.0)
    assert all(math.isclose(v, 1.0) for v in w.values())


# ---- statistics ----------------------------------------------------------------------------------------------------
def test_cluster_boot_resamples_games_not_rows():
    rows = [{"game": "a", "x": 1}, {"game": "a", "x": 1}, {"game": "b", "x": 0}]
    seen = []

    def stat(s):
        seen.append(sum(1 for r in s if r["game"] == "a"))
        return float(len(s))

    sp.cluster_boot(rows, stat, reps=50)
    assert set(seen) <= {0, 2, 4}  # game "a" always moves as a pair of rows, never one at a time
    assert math.isnan(sp.cluster_boot([{"game": "a"}], lambda s: 0.0)[0])


def test_wins_above_price_zero_when_hit_rate_equals_price():
    rows = [_bet(0.5, True, game=str(i)) for i in range(5)] + [_bet(0.5, False, game=str(i + 9)) for i in range(5)]
    wa, z, n = sp.wins_above_price(rows)
    assert abs(wa) < 1e-12 and abs(z) < 1e-12 and n == 10


def test_brier_logloss_basic():
    assert math.isclose(sp.brier(0.8, True), 0.04)
    assert sp.logloss(0.5, True) == sp.logloss(0.5, False) == -math.log(0.5)
    assert math.isfinite(sp.logloss(0.0, True))  # clipped


def test_incremental_slope_is_zero_when_model_equals_price():
    rows = [{**_bet(0.5, i % 2 == 0, game=str(i)), "p_price": 0.5, "p_discrete": 0.5} for i in range(10)]
    assert sp.incremental_slope(rows)["slope"] == 0.0


# ---- rules ---------------------------------------------------------------------------------------------------------
def _series(n_first_lose, n_second_lose):
    rows, t0 = [], dt.datetime(2026, 9, 1, tzinfo=UTC)
    for i in range(80):
        late = i >= 40
        yes_hi = i % 2 == 0
        lose = (n_second_lose if late else n_first_lose) and yes_hi
        rows.append(
            _bet(
                0.6 if yes_hi else 0.4,
                won=not lose if yes_hi else (i % 3 != 0),
                side="YES" if yes_hi else "NO",
                game=str(i),
                placed=t0 + dt.timedelta(days=i),
                fee=0.1,
            )
        )
    return rows


def test_rule_threshold_is_derived_from_first_half_only():
    a = sp.chronological_rule_test(_series(True, True), grid=(0.5, 0.55), min_excl=5)
    b = sp.chronological_rule_test(_series(True, False), grid=(0.5, 0.55), min_excl=5)  # second half differs
    assert a["x"] == b["x"] and a["derive"] == b["derive"]  # x cannot depend on the second half
    assert a["second_excluded"]["roi"] < b["second_excluded"]["roi"]  # but the judged result does


def test_rule_returns_none_without_enough_first_half_evidence():
    r = sp.chronological_rule_test(
        [_bet(game=str(i), placed=dt.datetime(2026, 9, 1, tzinfo=UTC) + dt.timedelta(days=i)) for i in range(6)]
    )
    assert r["x"] is None


def test_team_total_no_reports_zero_after_cutoff():
    t0 = dt.datetime(2026, 10, 1, tzinfo=UTC)
    rows = [
        _bet(0.5, i % 3 != 0, side="NO", family="TEAM_TOTAL", game=str(i), placed=t0 + dt.timedelta(days=i))
        for i in range(12)
    ]
    out = sp.team_total_no(rows, dt.datetime(2026, 12, 1, tzinfo=UTC))
    assert out["n_all"] == 12 and out["n_after_earlier_sample"] == 0 and "after" not in out
    out2 = sp.team_total_no(rows, dt.datetime(2026, 10, 6, tzinfo=UTC))
    assert out2["n_after_earlier_sample"] == 6 and out2["after"]["n"] == 6


def test_band_table_splits_by_side_and_price():
    rows = [_bet(0.55, i % 2 == 0, side="YES", game=str(i)) for i in range(10)] + [
        _bet(0.35, True, side="NO", game=str(i + 20)) for i in range(6)
    ]
    t = sp.band_table(rows)
    keys = {(r["side"], r["band"]) for r in t}
    assert ("YES", "0.50-0.70") in keys and ("NO", "0.30-0.50") in keys and ("ALL", "0.50-0.70") in keys


def test_min_edge_adds_fee_and_half_spread():
    q = {"game_spread": [(0.55, 0.01)] * 5}
    row = [r for r in sp.min_edge_table(q, "game_spread") if r["band"] == "0.50-0.70"][0]
    assert math.isclose(row["min_edge_median_c"], 100 * (sp.fee_per_contract(0.6) + 0.01))
