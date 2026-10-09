"""Football Signal Discovery Lab, Wave 2A (CFB): the retrospective replay uses the frozen Wave-2 rules unchanged,
never reads a post-kickoff quote or an outcome before the reveal stage, and never writes the prospective store.

Offline: synthetic ladders, the committed replay artifacts and the working-tree football log.
"""

from __future__ import annotations

import copy
import gzip
import importlib.util
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from cfb_edge_finder.control_prospective import capture as cap
from cfb_edge_finder.signal_discovery import wave2 as W
from cfb_edge_finder.signal_discovery.wave2a import history as H
from cfb_edge_finder.signal_discovery.wave2a import replay as R

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "scripting" / "validation" / "signal_discovery_wave2a"
KEY = "26SEP19AAABBB"
KICKOFF = "2026-09-19T20:00:00Z"
CODES = {"status": "RESOLVED", "codes": {"AAA": "1", "BBB": "2"}}


def at(minutes_before: float, kickoff: str = KICKOFF) -> str:
    return cap.iso(cap.parse_utc(kickoff) - timedelta(minutes=minutes_before))


def research_rows(minutes_before: float, run: str = "r1", shift: float = 0.0, partial: bool = False) -> list[dict]:
    """A research-corpus capture: both asks, no bids. AAA favoured by ~7 (+shift)."""
    out = []
    t = at(minutes_before)
    for code, strike, ya, na in (
        ("AAA", 3.5, 0.67, 0.35),
        ("AAA", 6.5, 0.54, 0.48),
        ("AAA", 7.5, 0.48, 0.54),
        ("AAA", 10.5, 0.39, 0.63),
        ("BBB", 3.5, 0.13, 0.89),
    ):
        ya = round(min(max(ya + shift, 0.02), 0.97), 2)
        na = round(min(max(na - shift, 0.02), 0.97), 2)
        out.append(
            {
                "run_id": run,
                "captured_at": t,
                "kalshi_event_ticker": f"KXNCAAFSPREAD-{KEY}",
                "kalshi_market_ticker": f"KXNCAAFSPREAD-{KEY}-{code}{int(strike + 0.5)}",
                "threshold": None if partial and code == "BBB" else strike,
                "executable_yes_price": ya,
                "executable_no_price": na,
                "market_status": "active",
            }
        )
    return out


def obs(stream: str = W.PROS_001, side: str = "home", z_value: float = 3.0) -> dict:
    game = {
        "game_key": KEY,
        "kickoff_utc": KICKOFF,
        "game_id": "999",
        "teams": {"home": {"team_id": "1", "name": "Aaa"}, "away": {"team_id": "2", "name": "Bbb"}},
    }
    if stream == W.PROS_001:
        x = W.DSC001_MEAN + z_value * W.DSC001_SD
        feature = {W.DSC001_FEATURE: x, "eligibility": None}
        return W.observation_001(game=game, feature=feature, reason=None, now=at(200), code_sha=None)
    row = {"claims": {"control": {"tier": f"{side.upper()}_CONTROL_STRONG", "side": side, "strength": "STRONG"}}}
    return W.observation_002(game=game, ledger_row=row, now=at(200), code_sha=None)


def entry_for(o: dict, *captures: list[dict]) -> dict:
    rows = [r for c in captures for r in c]
    attempts, quotes = R.research_attempts(rows, game_key=KEY, kickoff=KICKOFF)
    return R.replay_entry(o, attempts=attempts, quotes=quotes, codes=CODES, first_capture=at(10_000), code_sha=None)


def load_script():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("wave2a_cfb", ROOT / "scripts" / "signal_lab_wave2a_cfb.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- frozen rules, unchanged


def test_constants_are_the_registered_wave2_constants():
    doc = W.load_candidates(ROOT)
    s1 = doc["streams"][W.PROS_001]["standardisation"]
    assert (s1["mean"], s1["sd"]) == (0.13804024626610417, 1.4218539928759784) == (W.DSC001_MEAN, W.DSC001_SD)
    assert W.Z_THRESHOLD == 1.0 and W.SKEPTIC_BELOW_POINTS == 3.0
    assert doc["streams"][W.PROS_002]["frozen_threshold_points"] == 3.0
    assert R.W is W and H.W is W  # the replay imports the frozen module itself, not a copy
    assert R.FREEZE_UTC == "2026-10-08T22:26:33Z" and W.ACTIVATION_UTC == "2026-10-10T00:00:00Z"


@pytest.mark.parametrize(("z", "selected"), [(1.0, True), (-1.0, True), (0.999999, False), (-0.999999, False)])
def test_abs_z_ge_1_rule_and_side(z, selected):
    o = obs(z_value=z)
    assert (o["status"] == W.PENDING) is selected
    if selected:
        assert o["side"] == ("home" if z > 0 else "away")
    else:
        assert R.replay_exclusion(W.PROS_001, o["status"], o["reason"], kickoff=KICKOFF, first_capture=at(9e3)) == (
            R.NOT_ELIGIBLE
        )


def test_history_applies_the_same_rule():
    joined = [
        {
            "game_id": str(i),
            "season": 2020,
            W.DSC001_FEATURE: W.DSC001_MEAN + z * W.DSC001_SD,
            "_eval": {"o.home_points": 20, "o.away_points": 10, "m.spread_home": -3.0, "home": "A", "away": "B"},
        }
        for i, z in enumerate((1.0, -1.0, 0.99, 2.5))
    ]
    rows = H.pros001_rows(joined)
    assert [r["side"] for r in rows] == ["home", "away", "home"]
    assert rows[0]["residual"] == 10 - 3 and rows[1]["residual"] == -10 - (-3)
    assert all(r["evidence_label"] == R.EVIDENCE_HISTORY and r["market_basis"] == H.SPORTSBOOK_CLOSE for r in rows)


@pytest.mark.parametrize(("spread_home", "eligible"), [(-3.0, False), (-2.99, True), (-2.5, True), (4.0, True)])
def test_history_pros002_strictly_below_3(spread_home, eligible):
    joined = [
        {
            "game_id": "1",
            "season": 2021,
            "_eval": {
                "control_strength": "STRONG",
                "control_side": "home",
                "o.home_points": 30,
                "o.away_points": 10,
                "m.spread_home": spread_home,
            },
        }
    ]
    assert H.pros002_rows(joined)[0]["eligible"] is eligible


def test_strong_control_rule_and_strict_threshold():
    # an entry whose implied margin is shifted to exactly 3.0 and to 2.99 (the frozen entry_row decides)
    o = obs(W.PROS_002, side="home")
    assert o["status"] == W.PENDING and o["side"] == "home"
    base = entry_for(o, research_rows(90))
    assert base["status"] in (W.ELIGIBLE, W.EXCLUDED_PROTOCOL)
    for value, expected in ((3.0, W.EXCLUDED_PROTOCOL), (2.99, W.ELIGIBLE), (3.01, W.EXCLUDED_PROTOCOL)):
        cp = {
            "status": W.ELIGIBLE,
            "reason": None,
            "captured_at": at(90),
            "minutes_before": 90.0,
            "attempt_id": "x",
            "market_implied_margin": value,
            "bracket": [],
            "rungs_used": [],
            "points": [],
        }
        e = W.entry_row(obs=o, cp=cp, now=KICKOFF, code_sha=None)
        assert e["status"] == expected
        if expected == W.EXCLUDED_PROTOCOL:
            assert R.replay_exclusion(W.PROS_002, e["status"], e["reason"], kickoff=KICKOFF, first_capture=at(9e3)) == (
                R.NOT_ELIGIBLE
            )
    moderate = {"claims": {"control": {"tier": "HOME_CONTROL_MODERATE", "side": "home", "strength": "MODERATE"}}}
    assert moderate["claims"]["control"]["tier"] not in W.STRONG_TIERS


# --------------------------------------------------------------------------- the window and the quotes


@pytest.mark.parametrize(("m", "inside"), [(180.0, True), (60.0, True), (180.1, False), (59.9, False)])
def test_primary_window_boundaries_are_inclusive(m, inside):
    attempts, _ = R.research_attempts(research_rows(m), game_key=KEY, kickoff=KICKOFF)
    assert attempts[0]["in_primary_window"] is inside


def test_catalog_window_is_conservative_about_the_build_duration():
    assert R.catalog_window(KICKOFF, at(178), 60) is True
    assert R.catalog_window(KICKOFF, at(179.5), 60) is False  # the fetch may have run before kickoff-180
    assert R.catalog_window(KICKOFF, at(60.5), 60) is False  # ... or after kickoff-60
    a, q = R.catalog_attempt([], game_key=KEY, kickoff=KICKOFF, captured_at=at(0.5), elapsed_seconds=60, commit="c")
    assert a is None and q == []  # a snapshot that could reach kickoff is never used


def test_last_valid_in_window_capture_wins_and_nothing_outside_is_used():
    early = research_rows(150, run="early", shift=0.0)
    late = research_rows(75, run="late", shift=0.10)  # AAA ~ +10: a different centre
    after_window = research_rows(30, run="close", shift=-0.3)  # inside 60 min: never the entry
    e = entry_for(obs(), early, late, after_window)
    assert e["status"] == W.ELIGIBLE
    assert e["captured_at"] == at(75) and e["capture_source"] == R.SOURCE_RESEARCH
    only_close = entry_for(obs(), after_window)
    assert only_close["status"] == W.SYSTEM_FAILURE
    assert only_close["replay_exclusion"] == R.NO_PRIMARY_WINDOW_CAPTURE


def test_post_kickoff_quotes_are_dropped_and_refused():
    rows = research_rows(-5)  # five minutes AFTER kickoff
    attempts, quotes = R.research_attempts(rows, game_key=KEY, kickoff=KICKOFF)
    assert attempts == [] and quotes == []
    bad = {
        "attempt_id": "x",
        "attempted_at": at(-1),
        "in_primary_window": True,
        "outcome": "OK",
        "minutes_before": -1,
        "source": "X",
    }
    with pytest.raises(R.ReplayIntegrityError):
        R.replay_entry(obs(), attempts=[bad], quotes=[], codes=CODES, first_capture=at(9e3), code_sha=None)


def test_market_center_is_the_wave2_function():
    rows = research_rows(90)
    attempts, quotes = R.research_attempts(rows, game_key=KEY, kickoff=KICKOFF)
    e = R.replay_entry(obs(), attempts=attempts, quotes=quotes, codes=CODES, first_capture=at(9e3), code_sha=None)
    value, bracket = W.implied_margin(W.ladder_points(quotes, "1", CODES["codes"]))
    assert e["market_implied_margin"] == round(value, 4) and e["bracket"] == bracket
    assert 6.5 < value < 7.5


def test_research_bids_are_the_book_identity_and_entry_prices_are_captured_asks():
    rows = research_rows(90)
    _, quotes = R.research_attempts(rows, game_key=KEY, kickoff=KICKOFF)
    for q, r in zip(quotes, sorted(rows, key=lambda r: r["kalshi_market_ticker"]), strict=True):
        assert q["yes_ask"] == r["executable_yes_price"] and q["no_ask"] == r["executable_no_price"]
        assert q["yes_bid"] == round(1 - r["executable_no_price"], 4)
        assert q["bid_provenance"] == R.BID_IDENTITY
    assert R.book_identity_holds([{"yes_bid": 0.4, "yes_ask": 0.42, "no_bid": 0.58, "no_ask": 0.6}]) == {"holds": 1}
    assert R.book_identity_holds([{"yes_bid": 0.4, "yes_ask": 0.42, "no_bid": 0.5, "no_ask": 0.6}]) == {"violated": 1}


def test_side_orientation_and_contract_side():
    # away side (BBB): its own rungs are bought YES; AAA rungs (the opponent's) are bought NO at the NO ask
    e = entry_for(obs(z_value=-2.0), research_rows(90))
    assert e["side"] == "away" and e["status"] == W.ELIGIBLE
    assert e["market_implied_margin"] < 0  # BBB is the underdog by ~7
    c = e["contract"]
    rung = next(
        q
        for q in R.research_attempts(research_rows(90), game_key=KEY, kickoff=KICKOFF)[1]
        if q["market_ticker"] == c["ticker"]
    )
    if rung["team_code"] == "AAA":
        assert c["contract_side"] == "no" and c["ask"] == rung["no_ask"]
    else:
        assert c["contract_side"] == "yes" and c["ask"] == rung["yes_ask"]
    assert c["ask"] != round(1 - rung["yes_ask"], 2) or rung["no_ask"] == round(1 - rung["yes_ask"], 2)


def test_partial_ladders_are_unusable_not_truncated():
    attempts, quotes = R.research_attempts(research_rows(90, partial=True), game_key=KEY, kickoff=KICKOFF)
    assert attempts[0]["outcome"] == R.ATTEMPT_PARTIAL and quotes == []
    e = entry_for(obs(), research_rows(90, partial=True))
    assert e["status"] != W.ELIGIBLE


# ------------------------------------------------------------------ missing stays missing; no prospective writes


def test_missing_close_stays_missing():
    e = entry_for(obs(), research_rows(90))
    s = R.reveal(
        e,
        {"status": "SETTLED", "control_points": 31, "opponent_points": 20, "control_margin": 11.0, "source": "t"},
        None,
        code_sha=None,
    )
    assert s["clv"] == {"closing_implied_margin": None, "closing_move_toward_side": None, "contract_clv": None}
    assert R.clv_summary([s])["missing_close"] == 1 and R.clv_summary([s])["mean_center_move_toward_side"] is None
    unsettled = R.reveal(e, {"status": "SETTLEMENT_UNAVAILABLE", "reason": "score missing"}, None, code_sha=None)
    assert unsettled["replay_exclusion"] == R.SETTLEMENT_UNAVAILABLE and "residual" not in unsettled


def test_no_settled_sample_is_never_zero():
    assert W.stream_summary(W.PROS_001, [])["display"] == W.NO_SETTLED_SAMPLE
    assert R.robustness([])["display"] == W.NO_SETTLED_SAMPLE
    assert R.classify({"n": 0}) == "INSUFFICIENT_REPLAY_DATA"


@pytest.mark.parametrize(
    "path", ["wave2/2026/ledger.jsonl", "wave2/reports/2026/wave2_status.json", "x/wave2/2026/spread_quotes.jsonl"]
)
def test_replay_cannot_write_the_prospective_store(path):
    with pytest.raises(R.ReplayIntegrityError):
        R.assert_not_prospective_store(path)
    R.assert_not_prospective_store("data/scripting/validation/signal_discovery_wave2a/replay_rows_2026.jsonl.gz")


def test_script_outputs_live_outside_the_prospective_store():
    mod = load_script()
    for p in (mod.CAPTURES, mod.MEMBERSHIP, mod.ROWS, mod.REPORT, mod.HISTORY):
        R.assert_not_prospective_store(str(p.relative_to(ROOT)))
        assert "signal_discovery_wave2a" in str(p)
    assert not any("research-signals" in str(p) for p in (mod.CAPTURES, mod.ROWS, mod.REPORT))


def test_replay_rows_are_labelled_and_never_prospective():
    with gzip.open(OUT / "replay_rows_2026.jsonl.gz", "rt") as fh:
        rows = [json.loads(x) for x in fh]
    assert rows and all(r["evidence_label"] == R.EVIDENCE_2026 for r in rows)
    assert all(R.in_replay_population(r["kickoff_utc"]) for r in rows)
    report = json.loads((OUT / "replay_report_2026.json").read_text())
    for s in report["streams"].values():
        assert s["prospective_status"] == W.PROSPECTIVE_TRACKING and s["prospective_n"] == W.NO_SETTLED_SAMPLE
        assert "verdict" not in s["summary"]  # the prospective read ladder is never applied to replay rows


def test_classification_rule():
    def summ(n, mean, ci, rate):
        return {"n": n, "residual": {"mean": mean, "ci95_bootstrap": ci}, "ats_like": {"rate": rate}}

    assert R.classify(summ(14, 5, [1, 9], 0.7)) == "INSUFFICIENT_REPLAY_DATA"
    assert R.classify(summ(30, 2, [0.1, 4], 0.55)) == "STRONGLY_SUPPORTIVE"
    assert R.classify(summ(30, 2, [-1, 4], 0.5)) == "SUPPORTIVE"
    assert R.classify(summ(30, -1, [-3, 1], 0.45)) == "UNSUPPORTIVE"
    assert R.classify(summ(30, 1, [-3, 4], 0.45)) == "MIXED"


# --------------------------------------------------------------------------- outcome-blind membership, determinism


def _membership_inputs(mod):
    from cfb_edge_finder.control_market.quotes import Quote
    from cfb_edge_finder.scripting.gamelog import TeamGame

    rows = [
        TeamGame.from_dict(json.loads(x))
        for x in (ROOT / "data/football/2026/team_games.jsonl").read_text().splitlines()
        if x.strip()
    ]
    events = json.loads((ROOT / "data/football/2026/schedule.json").read_text())["events"]
    caps = mod.read_gz_jsonl(mod.CAPTURES)
    winners = [Quote(**q) for q in mod.read_gz_jsonl(mod.CONTROL_QUOTES)]
    return rows, events, caps, winners


def test_membership_does_not_depend_on_the_target_outcome():
    mod = load_script()
    rows, events, caps, winners = _membership_inputs(mod)
    report = json.loads((OUT / "replay_report_2026.json").read_text())
    targets = {g["game_id"] for g in report["streams"][W.PROS_001]["games"][:3]}
    base = mod.membership(rows, events, caps, winners, {}, None, only_game_ids=targets)
    doctored = []
    for r in rows:
        if r.game_id in targets:
            d = r.as_dict()
            d["points_for"], d["points_against"] = 0.0, 99.0
            d["box"] = {k: (None if v is None else v * 3 + 7) for k, v in d["box"].items()}
            r = type(r).from_dict(d)
        doctored.append(r)
    again = mod.membership(doctored, events, caps, winners, {}, None, only_game_ids=targets)
    assert base["entries"] and mod.membership_digest(base) == mod.membership_digest(again)


def test_replay_is_deterministic():
    a = entry_for(obs(), research_rows(150, run="a"), research_rows(80, run="b", shift=0.05))
    b = entry_for(copy.deepcopy(obs()), research_rows(80, run="b", shift=0.05), research_rows(150, run="a"))
    assert a == b
    mod = load_script()
    rows = [{"b": 1, "a": [1, 2]}, {"z": None}]
    p = OUT / "_determinism_probe.jsonl.gz"
    try:
        d1 = mod.write_gz_jsonl(p, rows)
        b1 = p.read_bytes()
        d2 = mod.write_gz_jsonl(p, rows)
        assert d1 == d2 and b1 == p.read_bytes()
    finally:
        p.unlink(missing_ok=True)


def test_history_reproduces_wave1_exactly():
    hist = json.loads((OUT / "history_report.json").read_text())
    rep = hist[W.PROS_001]["reproduction_of_wave1_follow_rule"]
    assert rep["exact"] is True and rep["wave2a"]["n"] == 2713
    assert hist["evidence_label"] == R.EVIDENCE_HISTORY and hist["market_basis"] == H.SPORTSBOOK_CLOSE
