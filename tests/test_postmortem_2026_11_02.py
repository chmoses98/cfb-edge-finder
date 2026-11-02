"""Regression tests for the 2026-11-02 postmortem fixes (issue #248)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from cfb_edge_finder.decision.exposure_aggregation import Position, aggregate_exposure
from cfb_edge_finder.decision.pit_guards import Guard, PitInputs, PitPolicy, PriceProvenance, evaluate_pit_guards
from cfb_edge_finder.expression.fair_value import (
    CalibrationStatus,
    ReliabilityBin,
    assess_edge,
    bet_up_to,
    calibrated_probability,
    fee_inclusive_break_even,
    order_fee_dollars,
)
from cfb_edge_finder.expression.ticker_ontology import Family, Period, parse_ticker
from cfb_edge_finder.postmortem.contract_reconciliation import append_records, build_reconciliation, check_economics
from cfb_edge_finder.postmortem.loss_attribution import classify_loss, family_performance
from cfb_edge_finder.projections.discrete_scoring import (
    GameRow,
    MissingPeriodCalibration,
    ShapeParams,
    build_distribution,
    build_period_distribution,
    expected_points,
    fit_ratings,
)

ROOT = Path(__file__).resolve().parents[1]
SHAPE = ShapeParams(0.2, 0.1, 0.1)


# ---------------------------------------------------------------- ontology
@pytest.mark.parametrize(
    "ticker,fam,per,team,opp,strike",
    [
        ("KXNCAAFSPREAD-26OCT22JMUAPP-JMU8", Family.SPREAD, Period.GAME, "JMU", "APP", 7.5),
        ("KXNCAAFSPREAD-26OCT22ECUMEM-MEM8", Family.SPREAD, Period.GAME, "MEM", "ECU", 7.5),
        ("KXNCAAFTEAMTOTAL-26OCT22LIBKENN-KENN24", Family.TEAM_TOTAL, Period.GAME, "KENN", "LIB", 23.5),
        ("KXNCAAF1QSPREAD-26OCT24RUTGNW-NW7", Family.SPREAD, Period.FIRST_QUARTER, "NW", "RUTG", 6.5),
        ("KXNCAAF1HSPREAD-26OCT31NDNAVY-ND15", Family.SPREAD, Period.FIRST_HALF, "ND", "NAVY", 14.5),
    ],
)
def test_ticker_decoding(ticker, fam, per, team, opp, strike):
    i = parse_ticker(ticker)
    assert (i.family, i.period, i.team, i.opponent, i.strike, i.decoded) == (fam, per, team, opp, strike, True)


def test_total_and_combo_and_unknown():
    t = parse_ticker("KXNCAAF1QTOTAL-26OCT24BAYKU-11")
    assert (t.family, t.period, t.strike, t.team) == (Family.TOTAL, Period.FIRST_QUARTER, 10.5, None)
    c = parse_ticker("KXMVECROSSCATEGORY-S2026ADIRNR6EOGLEJODE-HSELTECMA")
    assert c.family is Family.COMBO_UNDECODED and not c.decoded and c.game_key is None
    assert parse_ticker("KXOTHER-1-2").family is Family.UNKNOWN


# ---------------------------------------------------------------- fees / fair value
def test_per_order_fee_rounds_once_per_order_not_per_contract():
    one = order_fee_dollars(0.50, 1, "KXNCAAFSPREAD")
    big = order_fee_dollars(0.50, 150, "KXNCAAFSPREAD")
    assert one == 0.02  # ceil(1.75c)
    assert big == 2.63  # ceil(262.5c)
    assert fee_inclusive_break_even(0.50, 150, "KXNCAAFSPREAD") == pytest.approx(0.50 + 2.63 / 150)
    assert fee_inclusive_break_even(0.50, 150, "KXNCAAFSPREAD") < fee_inclusive_break_even(0.50, 1, "KXNCAAFSPREAD")


def test_unpriceable_fee_is_none_never_zero():
    assert order_fee_dollars(1.0, 10, "X") is None
    assert order_fee_dollars(0.0, 10, "X") is None
    assert order_fee_dollars(0.5, 0, "X") is None
    assert fee_inclusive_break_even(1.0, 10, "X") is None


def test_ledger_fees_reproduce_exactly():
    wagers = json.loads((ROOT / "app/latest/wagers.json").read_text())["items"]
    assert wagers
    for w in wagers:
        fee = order_fee_dollars(w["average_price"], w["contracts"], w["kalshi_ticker"].split("-")[0])
        assert fee == pytest.approx(w["fees"], abs=0.011), w["kalshi_ticker"]


BINS = [ReliabilityBin(0.4, 0.5, 200, 90), ReliabilityBin(0.5, 0.6, 200, 118), ReliabilityBin(0.6, 0.7, 10, 8)]


def test_calibration_refuses_without_evidence():
    assert calibrated_probability(None, BINS).status is CalibrationStatus.UNCALIBRATED_NO_RAW_PROBABILITY
    assert calibrated_probability(0.55, None).status is CalibrationStatus.UNCALIBRATED_NO_RELIABILITY_TABLE
    sparse = calibrated_probability(0.65, BINS)
    assert sparse.status is CalibrationStatus.UNCALIBRATED_SPARSE_BIN and sparse.point is None
    assert bet_up_to(sparse, 100, "KXNCAAFSPREAD") is None
    assert bet_up_to(None, 100, "KXNCAAFSPREAD") is None


def test_calibration_interval_and_bet_up_to_use_lower_bound():
    est = calibrated_probability(0.55, BINS)
    assert est.usable and est.lower < est.point < est.upper
    cap = bet_up_to(est, 100, "KXNCAAFSPREAD")
    assert cap is not None
    assert est.lower - fee_inclusive_break_even(cap, 100, "KXNCAAFSPREAD") >= 0
    nxt = round(cap + 0.01, 2)
    assert est.lower - fee_inclusive_break_even(nxt, 100, "KXNCAAFSPREAD") < 0
    assert cap < est.point  # strictly more conservative than the point estimate


def test_assess_edge_has_no_edge_without_calibration():
    a = assess_edge(0.5, 100, "KXNCAAFSPREAD", calibrated_probability(0.55, None))
    assert a.lower_bound_margin is None and a.point_margin is None and a.bet_up_to is None
    assert a.within_bet_up_to is None and a.break_even is not None


def test_no_price_qualifies_returns_none():
    weak = calibrated_probability(0.45, BINS)  # hit rate 0.45 -> lower bound ~0.40
    assert bet_up_to(weak, 100, "KXNCAAFSPREAD", required_margin=0.5) is None


# ---------------------------------------------------------------- PIT guards
T = datetime(2026, 11, 7, 18, 0, tzinfo=UTC)


def _good(**kw):
    base = dict(
        decision_time=T,
        kickoff=T + timedelta(hours=2),
        projection_as_of=T - timedelta(hours=1),
        availability_observed_at=T - timedelta(minutes=30),
        qb_confirmed=True,
        quote_captured_at=T - timedelta(seconds=30),
        price_provenance=PriceProvenance.EXECUTABLE_ASK,
    )
    base.update(kw)
    return PitInputs(**base)


def test_all_good_is_actionable():
    assert evaluate_pit_guards(_good()).actionable


@pytest.mark.parametrize(
    "kw,guard",
    [
        (dict(projection_as_of=None), Guard.MISSING_PROJECTION_AS_OF),
        (dict(projection_as_of=T - timedelta(hours=13)), Guard.PROJECTION_STALE),
        (dict(projection_as_of=T + timedelta(minutes=1)), Guard.PROJECTION_FROM_FUTURE),
        (dict(availability_observed_at=None), Guard.MISSING_AVAILABILITY),
        (dict(availability_observed_at=T - timedelta(hours=4)), Guard.AVAILABILITY_STALE),
        (dict(availability_observed_at=T + timedelta(seconds=1)), Guard.AVAILABILITY_FROM_FUTURE),
        (dict(qb_confirmed=None), Guard.QB_UNCONFIRMED),
        (dict(qb_confirmed=False), Guard.QB_UNCONFIRMED),
        (dict(quote_captured_at=None), Guard.MISSING_QUOTE_TIME),
        (dict(quote_captured_at=T - timedelta(minutes=6)), Guard.QUOTE_STALE),
        (dict(quote_captured_at=T + timedelta(seconds=5)), Guard.QUOTE_FROM_FUTURE),
        (dict(price_provenance=PriceProvenance.CAPTURED_MID), Guard.NON_EXECUTABLE_PRICE),
        (dict(price_provenance=PriceProvenance.UNKNOWN), Guard.NON_EXECUTABLE_PRICE),
        (dict(kickoff=None), Guard.MISSING_KICKOFF),
        (dict(kickoff=T + timedelta(minutes=5)), Guard.AFTER_KICKOFF_CUTOFF),
        (dict(kickoff=T - timedelta(minutes=1)), Guard.AFTER_KICKOFF_CUTOFF),
    ],
)
def test_each_guard_blocks(kw, guard):
    res = evaluate_pit_guards(_good(**kw))
    assert not res.actionable and guard in res.blocking


def test_policy_override_and_naive_datetime_rejected():
    ok = evaluate_pit_guards(
        _good(quote_captured_at=T - timedelta(minutes=6)), PitPolicy(max_quote_age=timedelta(minutes=10))
    )
    assert ok.actionable
    with pytest.raises(ValueError):
        evaluate_pit_guards(_good(decision_time=datetime(2026, 11, 7, 18, 0)))


# ---------------------------------------------------------------- exposure
def test_exposure_aggregates_ladder_across_expressions_and_flags_without_capping():
    pos = [
        Position("KXNCAAFSPREAD-26NOV07AAABBB-AAA7", "YES", 150.0),
        Position("KXNCAAFSPREAD-26NOV07AAABBB-AAA14", "YES", 150.0),
        Position("KXNCAAF1HSPREAD-26NOV07AAABBB-AAA3", "YES", 100.0),
        Position("KXNCAAFSPREAD-26NOV07AAABBB-BBB7", "NO", 50.0),  # NO on BBB leans AAA too
        Position("KXNCAAFTEAMTOTAL-26NOV07AAABBB-AAA28", "YES", 50.0),
        Position("KXNCAAFTOTAL-26NOV07AAABBB-55", "NO", 100.0),
    ]
    rep = aggregate_exposure(pos, unit_dollars=100.0)
    g = rep.games["26NOV07AAABBB"]
    assert g.gross_units == pytest.approx(6.0)
    assert g.cluster_units["margin:AAA"] == pytest.approx(4.5)
    assert g.view_units["dominates:AAA"] == pytest.approx(5.0)
    assert "26NOV07AAABBB:GAME_GROSS_OVER_5U" in rep.flags
    assert "26NOV07AAABBB:CORRELATED_VIEW_OVER_5U" not in rep.flags  # 5.0 is not > 5
    assert g.positions == 6  # nothing removed


def test_exposure_hedges_count_as_gross_and_days_flag():
    pos = [Position(f"KXNCAAFGAME-26NOV07G{i}AAAB{i}-G{i}AAA", "YES", 400.0) for i in range(4)]
    rep = aggregate_exposure(pos, unit_dollars=100.0)
    assert rep.daily_gross_units["2026-11-07"] == pytest.approx(16.0)
    assert "2026-11-07:DAY_GROSS_OVER_15U" in rep.flags


def test_exposure_combo_reported_separately_and_unit_required():
    rep = aggregate_exposure([Position("KXMVECROSSCATEGORY-S1-X", "YES", 70.0, "2026-10-26")], unit_dollars=70.0)
    assert rep.games == {} and rep.undecoded_combo_units == pytest.approx(1.0)
    assert "UNDECODED_COMBO_EXPOSURE_PRESENT" in rep.flags
    with pytest.raises(ValueError):
        aggregate_exposure([], unit_dollars=0)


# ---------------------------------------------------------------- reconciliation
def _ledger():
    w = json.loads((ROOT / "app/latest/wagers.json").read_text())["items"]
    s = {x["wager_id"]: x for x in json.loads((ROOT / "app/latest/settlements.json").read_text())["items"]}
    return w, s


def test_issue_fixture_reproduces_from_canonical_ledger():
    w, s = _ledger()
    win = [x for x in w if "2026-10-22" <= x["placed_at"][:10] <= "2026-11-01"]
    recs = [build_reconciliation(x, s.get(x["wager_id"])) for x in win]
    assert len(recs) == 90
    assert round(sum(r["execution"]["stake"] for r in recs), 2) == 9013.40
    assert round(sum(r["settlement"]["net_pnl"] for r in recs), 2) == -1848.05
    assert round(sum(r["execution"]["fees_paid"] for r in recs), 2) == 282.07
    assert all(r["economics_issues"] == [] for r in recs)
    assert all(r["execution"]["fee_matches_schedule"] for r in recs)


def test_missing_evidence_is_flagged_not_inferred():
    w, s = _ledger()
    r = build_reconciliation(w[0], s.get(w[0]["wager_id"]))
    for flag in (
        "MISSING_RECOMMENDATION_ID",
        "MISSING_MODEL_RUN_ID",
        "MISSING_ORDER_ID",
        "MISSING_FILL_IDS",
        "MISSING_CLOSE_QUOTE",
        "MISSING_INJURY_SNAPSHOT",
        "MISSING_MODEL_INPUTS_AS_OF",
    ):
        assert flag in r["missing"]
    assert r["attribution"]["recommendation_id"] is None and r["close"]["clv_mid_vs_fill"] is None


def test_close_quote_gives_clv_for_each_side_and_combo_stays_undecoded():
    w, s = _ledger()
    yes = next(x for x in w if x["selection"] == "YES" and x["kalshi_ticker"].startswith("KXNCAAFSPREAD"))
    no = next(x for x in w if x["selection"] == "NO" and x["kalshi_ticker"].startswith("KXNCAAFSPREAD"))
    q = {"yes_bid": 0.50, "yes_ask": 0.54}
    ry = build_reconciliation(yes, s.get(yes["wager_id"]), close_quote=q)
    rn = build_reconciliation(no, s.get(no["wager_id"]), close_quote=q)
    assert ry["close"]["close_price_for_side_mid"] == pytest.approx(0.52)
    assert rn["close"]["close_price_for_side_mid"] == pytest.approx(0.48)
    assert ry["close"]["clv_mid_vs_fill"] == pytest.approx(0.52 - yes["average_price"])
    combo = next(x for x in w if x["kalshi_ticker"].startswith("KXMVECROSSCATEGORY"))
    rc = build_reconciliation(combo, s.get(combo["wager_id"]))
    assert "UNDECODED_COMBO_LEGS" in rc["missing"] and rc["ontology"]["family"] == "COMBO_UNDECODED"
    assert rc["combo_legs"] is None


def test_reconciliation_does_not_mutate_inputs_and_is_deterministic():
    w, s = _ledger()
    before = json.dumps([w[0], s.get(w[0]["wager_id"])], sort_keys=True)
    a = build_reconciliation(w[0], s.get(w[0]["wager_id"]))
    b = build_reconciliation(w[0], s.get(w[0]["wager_id"]))
    assert a["record_id"] == b["record_id"]
    assert json.dumps([w[0], s.get(w[0]["wager_id"])], sort_keys=True) == before


def test_economics_identity_violation_detected():
    w, s = _ledger()
    bad = dict(w[0], stake=w[0]["stake"] + 5)
    assert "STAKE_NE_COST_PLUS_FEES" in check_economics(bad, s.get(w[0]["wager_id"]))
    bad_s = dict(s[w[0]["wager_id"]], net_pnl=s[w[0]["wager_id"]]["net_pnl"] - 1)  # i.e. subtracting fees twice
    assert "NET_NE_GROSS_MINUS_STAKE" in check_economics(w[0], bad_s)


def test_append_only_store(tmp_path):
    w, s = _ledger()
    recs = [build_reconciliation(x, s.get(x["wager_id"])) for x in w[:5]]
    p = tmp_path / "rec.jsonl"
    assert append_records(p, recs) == 5
    first = p.read_text()
    assert append_records(p, recs) == 0
    assert p.read_text() == first
    assert append_records(p, [build_reconciliation(w[5], s.get(w[5]["wager_id"]))]) == 1
    assert p.read_text().startswith(first)


# ---------------------------------------------------------------- loss attribution
def _lost_record(close=None):
    w, s = _ledger()
    x = next(v for v in w if v["kalshi_ticker"].startswith("KXNCAAFSPREAD") and s[v["wager_id"]]["result"] == "LOST")
    return build_reconciliation(x, s[x["wager_id"]], close_quote=close), x


def _close_in_our_favour(side):
    return {"yes_bid": 0.97, "yes_ask": 0.99} if side == "YES" else {"yes_bid": 0.01, "yes_ask": 0.03}


def _close_against_us(side):
    return _close_in_our_favour("NO" if side == "YES" else "YES")


def test_loss_without_evidence_is_insufficient_not_guessed():
    r, _ = _lost_record()
    v = classify_loss(r)
    assert v["cause"] == "INSUFFICIENT_EVIDENCE"
    assert {"CALIBRATED_PREGAME_PROBABILITY", "CLOSE_QUOTE"} <= set(v["missing"])


def test_negative_clv_without_pregame_probability_does_not_pick_handicap_or_pricing():
    r, x = _lost_record(_close_against_us(_lost_record()[1]["selection"]))
    v = classify_loss(r)
    assert v["cause"] == "INSUFFICIENT_EVIDENCE"
    assert "HANDICAP_VS_PRICING_NOT_SEPARABLE_WITHOUT_PREGAME_PROBABILITY" in v["missing"]


def test_variance_requires_close_at_least_as_good_as_fill_after_fees():
    r, x = _lost_record(_close_in_our_favour(_lost_record()[1]["selection"]))
    assert classify_loss(r)["cause"] == "VARIANCE"


def test_pricing_handicap_expression_buckets():
    r, x = _lost_record(_close_against_us(_lost_record()[1]["selection"]))
    cheap = calibrated_probability(0.55, [ReliabilityBin(0.5, 0.6, 400, 220)])
    weak = calibrated_probability(0.45, [ReliabilityBin(0.4, 0.5, 400, 180)])
    # estimate far above fill, close against us -> handicap
    hi = classify_loss(r, estimate=cheap)
    assert hi["cause"] in ("BAD_HANDICAP", "BAD_PRICING")
    # estimate below break-even -> price never acceptable -> pricing
    assert classify_loss(r, estimate=weak)["cause"] == "BAD_PRICING"
    assert classify_loss(r, cheaper_equivalent_all_in=0.01)["cause"] == "BAD_EXPRESSION"


def test_combo_loss_always_insufficient():
    w, s = _ledger()
    c = next(v for v in w if v["kalshi_ticker"].startswith("KXMVECROSSCATEGORY"))
    assert classify_loss(build_reconciliation(c, s.get(c["wager_id"])))["cause"] == "INSUFFICIENT_EVIDENCE"


def test_family_performance_reports_ci_and_no_false_confidence_on_tiny_family():
    w, s = _ledger()
    win = [x for x in w if "2026-10-22" <= x["placed_at"][:10] <= "2026-11-01"]
    recs = [build_reconciliation(x, s.get(x["wager_id"])) for x in win]
    perf = family_performance(recs)
    tt = perf["GAME_TEAM_TOTAL"]
    assert tt["n_bets"] == 28 and tt["net"] == pytest.approx(581.42, abs=0.01)
    assert tt["roi_ci95_game_cluster_bootstrap"][0] < 0  # a 28-bet hot sample does NOT establish profit
    assert not tt["ci_excludes_zero"]
    assert perf["GAME_SPREAD"]["net"] == pytest.approx(-1706.30, abs=0.01)
    assert perf["1H_TOTAL"]["ci_reliable"] is False and perf["1H_TOTAL"]["ci_excludes_zero"] is False


def test_postmortem_cannot_reach_the_ledger_write_path():
    src = ROOT / "src/cfb_edge_finder"
    writers = ["store", "wager", "settlement", "economics", "import_routed_wagers", "import_settlements"]
    for name in writers:
        text = (src / "accounting" / f"{name}.py").read_text()
        assert "postmortem" not in text.replace("accounting.postmortem", ""), name
    for f in (src / "postmortem").glob("*.py"):
        text = f.read_text()
        for fn in ("append_wagers", "append_settlements", "append_amendments"):
            assert fn not in text, f"{f.name} names ledger writer {fn}"
    for pkg in ("accounting",):
        for f in (src / pkg).glob("*.py"):
            assert "cfb_edge_finder.postmortem" not in f.read_text(), f.name


# ---------------------------------------------------------------- discrete scoring
def test_distribution_is_exact_pmf_with_key_number_lumps():
    d = build_distribution(28.0, 24.0, SHAPE)
    assert d.joint.sum() == pytest.approx(1.0)
    assert d.margin_prob(0) < 1e-6  # no final ties: overtime resolved
    assert d.margin_prob(3) > d.margin_prob(2) and d.margin_prob(3) > d.margin_prob(4)
    assert d.margin_prob(7) > d.margin_prob(6) and d.margin_prob(7) > d.margin_prob(8)
    # exact push mass between adjacent half-point and integer strikes
    assert d.p_margin_gt(2.5) - d.p_margin_gt(3.5) == pytest.approx(d.margin_prob(3))
    assert d.p_margin_gt(3.0) == pytest.approx(d.p_margin_gt(3.5))
    assert d.p_margin_gt(3.5) + d.p_margin_lt(3.5) == pytest.approx(1.0)
    assert d.p_margin_gt(3.0) + d.p_margin_lt(3.0) + d.margin_prob(3) == pytest.approx(1.0)
    assert d.p_total_gt(50.5) + d.p_total_lt(50.5) == pytest.approx(1.0)
    assert d.p_team_gt(True, 27.5) + d.p_team_lt(True, 27.5) == pytest.approx(1.0)
    mean_total = sum(i * p for i, p in enumerate(d._total))
    assert mean_total == pytest.approx(52.0, abs=0.6)


def test_symmetry_and_monotonicity():
    d = build_distribution(25.0, 25.0, SHAPE)
    assert d.p_margin_gt(0.5) == pytest.approx(0.5, abs=1e-6)
    hi = build_distribution(35.0, 25.0, SHAPE)
    assert hi.p_margin_gt(0.5) > d.p_margin_gt(0.5)
    assert hi.p_total_gt(50.5) > d.p_total_gt(50.5)


def test_period_distribution_refuses_without_evidenced_share():
    with pytest.raises(MissingPeriodCalibration):
        build_period_distribution(28, 24, SHAPE, period_share=None)
    with pytest.raises(MissingPeriodCalibration):
        build_period_distribution(28, 24, SHAPE, period_share=1.5)
    d = build_period_distribution(28, 24, SHAPE, period_share=0.5)
    assert d.joint.sum() == pytest.approx(1.0)


def _rows():
    rng = np.random.default_rng(0)
    teams = [str(i) for i in range(8)]
    strength = {t: 0.8 + 0.1 * i for i, t in enumerate(teams)}
    rows = []
    for _ in range(6):
        for i, a in enumerate(teams):
            b = teams[(i + 1 + _) % 8]
            if a == b:
                continue
            rows.append(GameRow(a, b, "home", 65.0, float(round(65 * 0.4 * strength[a] / strength[b]))))
            rows.append(GameRow(b, a, "away", 65.0, float(round(65 * 0.4 * strength[b] / strength[a]))))
    return rows, rng


def test_ratings_are_opponent_adjusted_and_unrated_teams_unpriced():
    rows, _ = _rows()
    r = fit_ratings(rows)
    assert r.off["7"] > r.off["0"]
    assert r.defense["7"] < r.defense["0"]
    assert expected_points(r, "7", "0", "neutral") > expected_points(r, "0", "7", "neutral")
    assert expected_points(r, "7", "NOT_RATED", "neutral") is None
    assert expected_points(r, "7", "0", "home") > expected_points(r, "7", "0", "away")


def test_schedule_strength_credits_hard_schedule():
    rows = [
        GameRow("A", "weak", "neutral", 65, 40),
        GameRow("weak", "A", "neutral", 65, 10),
        GameRow("B", "strong", "neutral", 65, 40),
        GameRow("strong", "B", "neutral", 65, 10),
    ]
    rows += [
        GameRow("strong", "x", "neutral", 65, 45),
        GameRow("x", "strong", "neutral", 65, 5),
        GameRow("weak", "y", "neutral", 65, 5),
        GameRow("y", "weak", "neutral", 65, 45),
    ]
    r = fit_ratings(rows)
    assert r.schedule_strength["B"] < r.schedule_strength["A"]  # B faced the better defence
    assert r.off["B"] > r.off["A"]  # same 40 points, harder opponent -> higher offence rating


def test_eval_harness_runs_and_is_point_in_time():
    from cfb_edge_finder.research.discrete_scoring_eval import load_games, run_evaluation

    games = load_games(ROOT / "data/football/2026/team_games.jsonl")
    assert games and all(g["home"] != g["away"] for g in games)
    tiny = run_evaluation(games[:40])
    assert tiny["status"] == "INSUFFICIENT_DATA"  # refuses to report on too little data
    report = json.loads((ROOT / "docs/reports/discrete_scoring_eval_2026-11-02.json").read_text())
    assert report["status"] == "OK"
    p = report["protocol"]
    assert p["calibration_window"][1] <= p["heldout_window"][0]
    assert p["calibration_games"] >= 30 and p["heldout_games"] >= 30
    assert set(report["unsupported"]) >= {"epa", "special_teams", "period_1q_1h", "market_prices"}
    assert "havoc" not in json.dumps(report).lower()
