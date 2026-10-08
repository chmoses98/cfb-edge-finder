"""2026 CONTROL market-pricing study: leakage, orientation, pricing, fees, CLV, buckets, safety, determinism."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import re
from pathlib import Path

import pytest
from script_engine_fakes import synthetic_season

from cfb_edge_finder.archetype_research.replay import LeakageError, history_for
from cfb_edge_finder.control_market import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    POP_PROSPECTIVE,
    POP_REPLAY,
    PRICE_BUCKETS,
    PROTOCOL_SHA256,
    RESEARCH_UNIT_CONTRACTS,
    SERIES,
    TIERS,
    ProtocolMismatch,
    assert_output_path,
    load_protocol,
    sha256,
    verify_protocol,
)
from cfb_edge_finder.control_market import economics as E
from cfb_edge_finder.control_market import football as F
from cfb_edge_finder.control_market import quotes as Q
from cfb_edge_finder.control_market import report as R
from cfb_edge_finder.control_market import study as S
from cfb_edge_finder.scripting.calibration import load_calibration
from cfb_edge_finder.scripting.football import LeagueFitCache
from cfb_edge_finder.scripting.gamelog import FINAL_AFTER, parse_utc

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "cfb_edge_finder" / "control_market"
KICKOFF = "2026-09-26T20:00:00Z"


# --------------------------------------------------------------------------- fixtures / helpers


def quote(
    minutes_before: float,
    yes=0.70,
    no=0.31,
    source=Q.SOURCE_CATALOG,
    ticker="KXNCAAFGAME-26SEP26AWYHOM-HOM",
    kickoff=KICKOFF,
    **kw,
) -> Q.Quote:
    t = parse_utc(kickoff).timestamp() - minutes_before * 60
    from datetime import UTC, datetime

    captured = datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Q.Quote(
        source=source,
        captured_at=captured,
        event_ticker=ticker.rsplit("-", 1)[0],
        market_ticker=ticker,
        yes_ask=yes,
        no_ask=no,
        status=kw.pop("status", "active"),
        **kw,
    )


SCHEDULE = [
    {
        "id": "G1",
        "date": KICKOFF,
        "competitors": [
            {"home_away": "home", "team_id": "10", "abbreviation": "HOM", "names": ["Home Team"], "location": "Home"},
            {"home_away": "away", "team_id": "20", "abbreviation": "AWY", "names": ["Away Team"], "location": "Away"},
        ],
    }
]


def keys(name: str) -> set[str]:
    return {re.sub(r"[^a-z]", "", name.lower())}


def football_record(side: str, strength: str = "STRONG", population: str = POP_REPLAY) -> dict:
    team_id, opp_id = ("10", "20") if side == "home" else ("20", "10")
    return {
        "population": population,
        "football_provenance": "test",
        "game_id": "G1",
        "kickoff_utc": KICKOFF,
        "week": 4,
        "home": "Home Team",
        "away": "Away Team",
        "data_confidence": "LOW",
        "prior_games": [3, 3],
        "fcs_involved": False,
        "replay_eligibility": None,
        "football_record_hash": "h",
        "control": {
            "side": side,
            "strength": strength,
            "tier": f"{side.upper()}_CONTROL_{strength}",
            "team_id": team_id,
            "team": "x",
            "opponent_id": opp_id,
            "opponent": "y",
        },
    }


def two_market_quotes(home_yes=0.80, home_no=0.22, away_yes=0.21, away_no=0.81, minutes=100.0):
    return [
        quote(
            minutes,
            yes=home_yes,
            no=home_no,
            ticker="KXNCAAFGAME-26SEP26AWYHOM-HOM",
            team_name="Home Team",
            event_title="Away Team at Home Team",
            kickoff_hint=KICKOFF,
        ),
        quote(
            minutes,
            yes=away_yes,
            no=away_no,
            ticker="KXNCAAFGAME-26SEP26AWYHOM-AWY",
            team_name="Away Team",
            event_title="Away Team at Home Team",
            kickoff_hint=KICKOFF,
        ),
    ]


def build(rec: dict, quotes: list[Q.Quote]) -> dict:
    orientation = Q.orient_markets(quotes, SCHEDULE, keys)
    by_ticker: dict[str, list[Q.Quote]] = {}
    for q in quotes:
        by_ticker.setdefault(q.market_ticker, []).append(q)
    return S.freeze_row(rec, by_ticker, orientation)


@pytest.fixture(scope="module")
def season():
    rows = synthetic_season()
    meta = {}
    for r in rows:
        meta.setdefault(r.game_id, {"neutral_site": False, "conference_game": None})
        meta[r.game_id]["home_id" if r.site == "home" else "away_id"] = r.team_id
    targets, _ = F.replay_targets(rows, meta, kickoff_before="2030-01-01T00:00:00Z")
    return rows, targets


# --------------------------------------------------------------------------- protocol


def test_protocol_is_the_registered_one():
    protocol = verify_protocol(load_protocol(ROOT))
    assert sha256(protocol) == PROTOCOL_SHA256
    assert protocol["status"] == "PRE-REGISTERED"


def test_a_modified_protocol_is_refused():
    protocol = load_protocol(ROOT)
    protocol["price_buckets_cents"][0] = [1, 50]
    with pytest.raises(ProtocolMismatch):
        verify_protocol(protocol)


def test_price_buckets_are_fixed_and_cover_every_tradeable_cent():
    assert PRICE_BUCKETS == ((1, 49), (50, 59), (60, 69), (70, 79), (80, 84), (85, 89), (90, 94), (95, 99))
    covered = [c for lo, hi in PRICE_BUCKETS for c in range(lo, hi + 1)]
    assert covered == list(range(1, 100))
    assert R.price_bucket(0.84) == "80-84" and R.price_bucket(0.85) == "85-89" and R.price_bucket(0.49) == "1-49"
    assert R.price_bucket(None) is None


def test_bootstrap_settings_match_protocol():
    assert (BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED) == (10_000, 20261008)


# --------------------------------------------------------------------------- football: leakage & populations


def test_control_reconstruction_uses_only_pregame_football(season):
    rows, targets = season
    target = next(t for t in targets if t.week == 5)
    cal = load_calibration(ROOT)
    first = F.replay_game(rows, target, LeagueFitCache(), cal)
    cutoff = parse_utc(first["football_data_cutoff"])
    doctored = [
        dataclasses.replace(r, points_for=99.0, points_against=0.0) if r.kickoff() + FINAL_AFTER >= cutoff else r
        for r in rows
    ]
    again = F.replay_game(doctored, target, LeagueFitCache(), cal)
    assert again["football_record_hash"] == first["football_record_hash"]
    assert again["control"] == first["control"]


def test_target_or_future_football_cannot_enter_history(season):
    rows, targets = season
    target = targets[3]
    impostor = dataclasses.replace(target, kickoff_utc="2026-12-31T19:00:00Z")
    with pytest.raises(LeakageError):
        history_for(rows, impostor)


def test_replay_rows_are_labelled_retrospective(season):
    rows, targets = season
    rec = F.replay_game(rows, targets[-1], LeagueFitCache(), load_calibration(ROOT))
    assert rec["population"] == POP_REPLAY
    assert rec["football_provenance"] == "RETROSPECTIVE_REPLAY_AT_PRODUCTION_CUTOFF"
    assert "control_won" not in json.dumps(rec) and "points" not in json.dumps(rec)


def test_replay_targets_stop_at_the_v2_ledger_start(season):
    rows, _ = season
    meta = {}
    for r in rows:
        meta.setdefault(r.game_id, {})["home_id" if r.site == "home" else "away_id"] = r.team_id
    targets, skipped = F.replay_targets(rows, meta, kickoff_before="2026-09-20T00:00:00Z")
    assert all(t.kickoff_utc < "2026-09-20T00:00:00Z" for t in targets)
    assert skipped["kickoff_not_before_v2_ledger"] > 0


def test_prospective_v2_rows_are_copied_not_recomputed():
    row = {
        "kind": "FINAL_PREGAME",
        "methodology_version": "cfb-script-engine/2.0.0",
        "game_key": "K1",
        "event_id": "401",
        "kickoff_utc": KICKOFF,
        "teams": {"home": {"team_id": "10", "name": "H"}, "away": {"team_id": "20", "name": "A"}},
        # deliberately a tier no football record could produce from these teams' data: copied verbatim
        "claims": {"control": {"side": "away", "strength": "MODERATE", "tier": "AWAY_CONTROL_MODERATE"}},
        "football_artifact_hash": "fa",
        "claims_artifact_hash": "ca",
    }
    pub = {**row, "kind": "PUBLICATION", "game_key": "K2"}
    out = F.prospective_rows([row, pub], {"K1", "K2"})
    assert len(out) == 1
    assert out[0]["population"] == POP_PROSPECTIVE
    assert out[0]["control"]["tier"] == "AWAY_CONTROL_MODERATE" and out[0]["control"]["team_id"] == "20"
    assert F.prospective_rows([row], set()) == []  # not completed -> not in the study
    src = inspect.getsource(F.prospective_rows)
    assert "derive_claims" not in src and "build_content" not in src


def test_outcome_is_read_only_at_reveal():
    for fn in (S.freeze_row, F.replay_game, F.replay_targets):
        src = inspect.getsource(fn)
        assert "points_for" not in src and "outcome" not in src.replace("outcome-blind", "").replace(
            "No outcome", ""
        ).replace("no outcome", "")
    params = inspect.signature(S.freeze_row).parameters
    assert set(params) == {"football", "quotes_by_ticker", "orientation", "abbreviations"}


# --------------------------------------------------------------------------- orientation & side pricing


@pytest.mark.parametrize(
    "side,contract_side,expected_ticker",
    [
        ("home", "yes", "KXNCAAFGAME-26SEP26AWYHOM-HOM"),
        ("home", "no", "KXNCAAFGAME-26SEP26AWYHOM-AWY"),
        ("away", "yes", "KXNCAAFGAME-26SEP26AWYHOM-AWY"),
        ("away", "no", "KXNCAAFGAME-26SEP26AWYHOM-HOM"),
    ],
)
def test_control_side_orientation(side, contract_side, expected_ticker):
    """HOME/AWAY CONTROL represented by YES (own market) and by NO (opponent's market)."""
    row = build(football_record(side), two_market_quotes())
    block = row["primary"] if contract_side == "yes" else row["secondary"]
    assert block["ticker"] == expected_ticker and block["side"] == contract_side
    expected_price = {
        ("home", "yes"): 0.80,  # HOM yes ask
        ("home", "no"): 0.81,  # AWY no ask
        ("away", "yes"): 0.21,  # AWY yes ask
        ("away", "no"): 0.22,  # HOM no ask
    }[(side, contract_side)]
    assert block["entry"]["price"] == expected_price


@pytest.mark.parametrize("side", ["home", "away"])
@pytest.mark.parametrize("control_won", [True, False])
def test_settlement_orientation(side, control_won):
    row = build(football_record(side), two_market_quotes())
    out = S.reveal_row(
        row, {"status": "SETTLED", "control_won": control_won, "control_margin": 7 if control_won else -7}, {}
    )
    assert out["primary"]["economics"]["settlement_value"] == (1.0 if control_won else 0.0)
    assert out["secondary"]["economics"]["settlement_value"] == (1.0 if control_won else 0.0)


def test_yes_and_no_prices_are_independent_never_complements():
    q = quote(100, yes=0.74, no=0.93)
    assert Q.side_ask(q, "yes") == 0.74 and Q.side_ask(q, "no") == 0.93
    missing_no = quote(100, yes=0.74, no=None)
    assert Q.side_ask(missing_no, "no") is None
    assert not Q.is_valid(missing_no, "no", KICKOFF)
    for module in (Q, S, E):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
                if isinstance(node.left, ast.Constant) and node.left.value == 1:
                    assert "yes" not in ast.unparse(node.right).lower(), ast.unparse(node)


def test_conflicting_orientation_is_excluded():
    qs = two_market_quotes()
    # both markets claim the same team -> conflict for the whole event
    qs[1] = dataclasses.replace(qs[1], team_name="Home Team")
    row = build(football_record("home"), qs)
    assert row["primary"]["exclusion"] in ("ORIENTATION_CONFLICT",)


def test_game_without_any_game_winner_market():
    row = build(football_record("home"), [])
    assert row["primary"]["exclusion"] == "NO_GAME_WINNER_MARKET"
    assert row["primary"]["entry"]["price"] is None


def test_vs_slugs_give_no_team_names():
    assert Q.slug_teams("cfb-2026-wk03-virginia-vs-west-virginia") is None
    assert Q.slug_teams("cfb-2026-wk01-fresno-state-at-usc") == {"away": "fresno state", "home": "usc"}


def test_moneyline_only():
    assert SERIES == "KXNCAAFGAME"
    spread = {
        "markets": [
            {
                "market_ticker": "KXNCAAFSPREAD-26SEP26AWYHOM-HOM7",
                "series_ticker": "KXNCAAFSPREAD",
                "yes_ask": 0.5,
                "no_ask": 0.51,
            }
        ]
    }
    assert Q.quotes_from_catalog_game(spread, KICKOFF, "c") == []
    total_obs = {"observation": {"kalshi_market_ticker": "KXNCAAFTOTAL-26SEP26AWYHOM-50", "executable_yes_price": 0.5}}
    assert Q.quotes_from_observation_row(total_obs) is None


# --------------------------------------------------------------------------- entry window


def test_entry_is_the_last_valid_quote_inside_60_180():
    qs = [quote(200, yes=0.60), quote(170, yes=0.61), quote(90, yes=0.62), quote(61, yes=0.63), quote(59, yes=0.99)]
    assert Q.select_entry(qs, "yes", KICKOFF).yes_ask == 0.63


def test_window_edges_are_inclusive():
    assert Q.select_entry([quote(180, yes=0.5)], "yes", KICKOFF).yes_ask == 0.5
    assert Q.select_entry([quote(60, yes=0.5)], "yes", KICKOFF).yes_ask == 0.5
    assert Q.select_entry([quote(180.02, yes=0.5)], "yes", KICKOFF) is None
    assert Q.select_entry([quote(59.98, yes=0.5)], "yes", KICKOFF) is None


def test_no_quote_outside_the_window_is_ever_used():
    qs = [quote(400, yes=0.5), quote(30, yes=0.6), quote(5, yes=0.7)]
    assert Q.select_entry(qs, "yes", KICKOFF) is None


def test_no_post_kickoff_quote_is_accepted():
    after = quote(-5, yes=0.5)
    assert not Q.is_valid(after, "yes", KICKOFF)
    assert Q.select_checkpoint([after], "yes", KICKOFF, "CLOSING") is None
    assert Q.select_checkpoint([after], "yes", KICKOFF, "EARLY_OPEN") is None


def test_missing_quote_remains_missing():
    row = build(football_record("home"), two_market_quotes(minutes=30))
    assert row["primary"]["entry"]["price"] is None
    assert row["primary"]["exclusion"] == "ENTRY_QUOTE_UNAVAILABLE"
    assert row["primary"]["fee"] is None
    out = S.reveal_row(row, {"status": "SETTLED", "control_won": True, "control_margin": 10}, {})
    assert out["primary"]["economics"] == {"available": False, "reason": "ENTRY_QUOTE_UNAVAILABLE"}


def test_unexecutable_books_are_not_valid():
    assert not Q.is_valid(quote(100, yes=1.0), "yes", KICKOFF)  # no offer below $1
    assert not Q.is_valid(quote(100, yes=0.0), "yes", KICKOFF)
    assert not Q.is_valid(quote(100, yes=0.505), "yes", KICKOFF)  # not a whole cent
    assert not Q.is_valid(quote(100, yes=0.5, sentinel=True), "yes", KICKOFF)
    assert not Q.is_valid(quote(100, yes=0.5, status="closed"), "yes", KICKOFF)


def test_tie_on_capture_time_prefers_the_research_observation():
    a = quote(100, yes=0.50, source=Q.SOURCE_CATALOG)
    b = quote(100, yes=0.51, source=Q.SOURCE_OBSERVATION)
    assert Q.select_entry([a, b], "yes", KICKOFF).source == Q.SOURCE_OBSERVATION


# --------------------------------------------------------------------------- fees & economics


@pytest.mark.parametrize("price,fee", [(0.50, 0.02), (0.53, 0.02), (0.90, 0.01), (0.96, 0.01), (0.99, 0.01)])
def test_fee_uses_the_captured_entry_price(price, fee):
    assert E.entry_fee(price) == fee


def test_missing_fee_remains_unavailable():
    assert E.entry_fee(None) is None and E.entry_fee(0.505) is None and E.entry_fee(1.0) is None
    econ = E.one_contract(0.6, None, True)
    assert econ == {"available": False, "reason": "FEE_UNAVAILABLE"}


def test_one_contract_economics():
    win = E.one_contract(0.80, 0.02, True)
    assert win["fee_adjusted_pnl"] == pytest.approx(0.18) and win["gross_pnl"] == pytest.approx(0.20)
    assert win["outlay"] == pytest.approx(0.82) and win["contracts"] == 1
    loss = E.one_contract(0.80, 0.02, False)
    assert loss["fee_adjusted_pnl"] == pytest.approx(-0.82)


def test_settlement_mismatch_is_excluded():
    row = build(football_record("home"), two_market_quotes())
    ticker = row["primary"]["ticker"]
    out = S.reveal_row(row, {"status": "SETTLED", "control_won": True, "control_margin": 3}, {ticker: ["no"]})
    assert out["primary"]["economics"]["available"] is False
    assert out["primary"]["exclusion"] == "SETTLEMENT_MISMATCH"


def test_frozen_row_tampering_is_refused():
    row = build(football_record("home"), two_market_quotes())
    row["primary"]["entry"]["price"] = 0.01
    with pytest.raises(S.FrozenRowMismatch):
        S.reveal_row(row, {"status": "SETTLED", "control_won": True, "control_margin": 3}, {})


# --------------------------------------------------------------------------- CLV


def test_clv_is_side_aware_and_uses_the_same_side_close():
    qs = two_market_quotes(minutes=100) + [
        quote(
            20,
            yes=0.85,
            no=0.17,
            ticker="KXNCAAFGAME-26SEP26AWYHOM-HOM",
            team_name="Home Team",
            event_title="Away Team at Home Team",
            kickoff_hint=KICKOFF,
        ),
        quote(
            20,
            yes=0.18,
            no=0.84,
            ticker="KXNCAAFGAME-26SEP26AWYHOM-AWY",
            team_name="Away Team",
            event_title="Away Team at Home Team",
            kickoff_hint=KICKOFF,
        ),
    ]
    row = S.reveal_row(
        build(football_record("home"), qs), {"status": "SETTLED", "control_won": True, "control_margin": 5}, {}
    )
    assert row["primary"]["clv"]["clv"] == pytest.approx(0.05)  # HOM yes 0.80 -> 0.85
    assert row["secondary"]["clv"]["clv"] == pytest.approx(0.03)  # AWY no 0.81 -> 0.84
    assert row["primary"]["clv"]["direction"] == "FAVORABLE"


def test_missing_close_is_not_zero_filled():
    row = S.reveal_row(
        build(football_record("home"), two_market_quotes()),
        {"status": "SETTLED", "control_won": True, "control_margin": 5},
        {},
    )
    assert row["primary"]["clv"] == {"available": False, "reason": "CLOSE_MISSING"}
    summary = R.clv_summary([row])
    assert summary["clv_n"] == 0 and summary["missing"] == 1 and "mean_clv" not in summary


def test_flat_clv_is_flat_not_missing():
    c = E.clv(0.7, 0.7)
    assert c["available"] and c["clv"] == 0 and c["direction"] == "FLAT"


# --------------------------------------------------------------------------- report


def _synthetic_rows(n=40, seed=3):
    import random

    rnd = random.Random(seed)
    rows = []
    for i in range(n):
        tier = TIERS[i % 4]
        side = "home" if tier.startswith("HOME") else "away"
        price = rnd.choice([0.55, 0.66, 0.75, 0.82, 0.88, 0.93, 0.97])
        rec = {**football_record(side, tier.rsplit("_", 1)[1]), "game_id": f"G{i}"}
        sched = [dict(SCHEDULE[0], id=f"G{i}")]
        qs = two_market_quotes(home_yes=price, away_yes=price) + [
            quote(
                10,
                yes=price + 0.01,
                ticker="KXNCAAFGAME-26SEP26AWYHOM-HOM",
                team_name="Home Team",
                event_title="Away Team at Home Team",
                kickoff_hint=KICKOFF,
            ),
        ]
        orientation = Q.orient_markets(qs, sched, keys)
        by: dict = {}
        for q in qs:
            by.setdefault(q.market_ticker, []).append(q)
        frozen = S.freeze_row(rec, by, orientation)
        won = rnd.random() < price
        rows.append(
            S.reveal_row(frozen, {"status": "SETTLED", "control_won": won, "control_margin": 7 if won else -3}, {})
        )
    return rows


def test_report_is_deterministic_and_in_fixed_order():
    rows = _synthetic_rows()
    a = R.build_report(rows, (POP_REPLAY, POP_PROSPECTIVE))
    b = R.build_report(list(reversed(rows)), (POP_REPLAY, POP_PROSPECTIVE))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert list(a["POOLED"]["groups"]) == [*TIERS, "ALL_MODERATE", "ALL_STRONG", "ALL_CONTROL"]
    assert list(a["POOLED"]["groups"]["ALL_CONTROL"]["price_buckets"]) == list(R.BUCKET_LABELS)
    assert a[POP_PROSPECTIVE]["overall_verdict"] == "INSUFFICIENT 2026 MARKET DATA"


def test_inclusion_never_depends_on_the_result():
    rows = _synthetic_rows()
    flipped = []
    for r in rows:
        f = json.loads(json.dumps(r))
        won = not f["settlement"]["control_won"]
        frozen = {k: v for k, v in f.items() if k not in ("settlement", "source_code_sha", "protocol_sha256")}
        for name in ("primary", "secondary"):
            for key in ("settlement", "economics", "clv"):
                frozen[name].pop(key, None)
            frozen[name]["exclusion"] = (
                r[name]["exclusion"]
                if r[name]["exclusion"] in ("ENTRY_QUOTE_UNAVAILABLE", "FEE_UNAVAILABLE", "NO_GAME_WINNER_MARKET")
                else None
            )
        flipped.append(
            S.reveal_row(frozen, {"status": "SETTLED", "control_won": won, "control_margin": 1 if won else -1}, {})
        )
    base = R.coverage(rows)
    other = R.coverage(flipped)
    assert base["primary_economics_n"] == other["primary_economics_n"]
    assert base["eligible_control_games"] == other["eligible_control_games"]


def test_verdict_rules():
    econ = {"n": 60, "roi_on_outlay": 0.05, "roi_on_outlay_bootstrap": {"ci95": [0.01, 0.09]}}
    assert R.verdict(econ, {"clv_n": 30, "mean_clv": 0.01})["verdict"] == "EVIDENCE_OF_UNDERPRICING"
    assert R.verdict(econ, {"clv_n": 30, "mean_clv": -0.01})["verdict"] == "POSSIBLE_UNDERPRICING"
    assert R.verdict({**econ, "n": 19}, {"clv_n": 30, "mean_clv": 0.01})["verdict"] == "INSUFFICIENT_DATA"
    neg = {"n": 60, "roi_on_outlay": -0.05, "roi_on_outlay_bootstrap": {"ci95": [-0.09, -0.01]}}
    assert R.verdict(neg, {})["verdict"] == "OVERPRICED"
    flat = {"n": 60, "roi_on_outlay": -0.01, "roi_on_outlay_bootstrap": {"ci95": [-0.09, 0.05]}}
    assert R.verdict(flat, {})["verdict"] == "APPROXIMATELY_EFFICIENT"
    assert R.overall_verdict({t: "INSUFFICIENT_DATA" for t in TIERS}) == "INSUFFICIENT 2026 MARKET DATA"
    mixed = dict(
        zip(TIERS, ["OVERPRICED", "APPROXIMATELY_EFFICIENT", "INSUFFICIENT_DATA", "INSUFFICIENT_DATA"], strict=True)
    )
    assert R.overall_verdict(mixed) == "2026 CONTROL RESULTS ARE MIXED BY TIER / PRICE"


def test_bootstrap_is_withheld_below_five_and_seeded():
    assert R.bootstrap({"x": [1, 0, 1]}, R._mean_stat, 3)["ci95"] is None
    a = R.bootstrap({"x": [1, 0, 1, 1, 0, 1]}, R._mean_stat, 6)
    b = R.bootstrap({"x": [1, 0, 1, 1, 0, 1]}, R._mean_stat, 6)
    assert a == b and a["seed"] == BOOTSTRAP_SEED


def test_no_recommendation_language_in_conclusion_fields():
    report = R.build_report(_synthetic_rows(), (POP_REPLAY, POP_PROSPECTIVE))
    conclusions = [report["POOLED"]["overall_verdict"]]
    conclusions += [report["POOLED"]["groups"][t]["verdict"]["verdict"] for t in TIERS]
    conclusions += list(R.VERDICTS if hasattr(R, "VERDICTS") else []) + [
        "2026 CONTROL SHOWS MARKET UNDERPRICING",
        "2026 CONTROL FOOTBALL SIGNAL HOLDS BUT MARKET IS APPROXIMATELY EFFICIENT",
        "2026 CONTROL SIDE IS OVERPRICED BY THE MARKET",
        "2026 CONTROL RESULTS ARE MIXED BY TIER / PRICE",
        "INSUFFICIENT 2026 MARKET DATA",
    ]
    assert all(R.conclusion_language_ok(c) for c in conclusions)
    for bad in ("bet the home side", "a strong play", "hammer it", "stake 2 units", "this is +EV"):
        assert not R.conclusion_language_ok(bad)


# --------------------------------------------------------------------------- safety


def test_no_staking_or_bankroll_parameters():
    assert RESEARCH_UNIT_CONTRACTS == 1
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text())
        names = {n.arg for n in ast.walk(tree) if isinstance(n, ast.arg)}
        names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        for banned in ("bankroll", "kelly", "stake", "units", "unit_size", "bet_size"):
            assert not any(banned in name.lower() for name in names), (path.name, banned)


def test_research_code_cannot_write_recommendations_or_production_paths():
    for bad in (
        "data/scripting/live/x.json",
        "data/scripting/ledger/2026/a.jsonl",
        "data/live/games/x.json",
        "app/latest/recommendations.json",
        "data/football/2026/x.json",
    ):
        with pytest.raises(PermissionError):
            assert_output_path(bad)
    assert_output_path("data/scripting/validation/control_market_2026/control_market_report.json")
    for path in PACKAGE.glob("*.py"):
        text = path.read_text()
        assert "recommendations.json" not in text and "wagers" not in text
        assert "import requests" not in text and "kalshi_client" not in text


def test_rerun_is_deterministic():
    a = _synthetic_rows(seed=9)
    b = _synthetic_rows(seed=9)
    assert json.dumps(R.build_report(a, (POP_REPLAY,)), sort_keys=True) == json.dumps(
        R.build_report(b, (POP_REPLAY,)), sort_keys=True
    )
