"""Football Signal Discovery Lab, Wave 2 (CFB): pre-registration, ladder center, checkpoint, contracts, append-only
ledger, verdicts and conductor isolation.

Offline: synthetic rows plus the committed live data of one real game (26OCT10LSUUK). Kalshi reads are injected.
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
from cfb_edge_finder.scripting.gamelog import read_jsonl
from cfb_edge_finder.signal_discovery import wave2 as W
from cfb_edge_finder.signal_discovery import wave2_cycle as C

ROOT = Path(__file__).resolve().parent.parent
KEY = "26OCT10LSUUK"
EVENT = "401856715"
KICKOFF = "2026-10-10T23:00:00Z"


def at(minutes_before: float, kickoff: str = KICKOFF) -> str:
    return cap.iso(cap.parse_utc(kickoff) - timedelta(minutes=minutes_before))


def rung(
    code: str, strike: float, yb: float, ya: float, nb: float | None = None, na: float | None = None, key: str = KEY
):
    n = int(strike + 0.5)
    return {
        "ticker": f"KXNCAAFSPREAD-{key}-{code}{n}",
        "event_ticker": f"KXNCAAFSPREAD-{key}",
        "floor_strike": strike,
        "strike_type": "greater",
        "yes_sub_title": f"{code} wins by over {strike} points",
        "status": "active",
        "yes_bid_dollars": f"{yb:.4f}",
        "yes_ask_dollars": f"{ya:.4f}",
        "no_bid_dollars": None if nb is None else f"{nb:.4f}",
        "no_ask_dollars": None if na is None else f"{na:.4f}",
    }


def ladder(shift: float = 0.0) -> list[dict]:
    """LSU favoured by ~7 (+shift); both teams' ladders; NO asks are deliberately NOT 1 - YES bid."""
    out = []
    for s, mid in ((3.5, 0.66), (6.5, 0.53), (7.5, 0.47), (10.5, 0.38), (13.5, 0.30)):
        m = min(max(mid + shift, 0.02), 0.97)
        out.append(
            rung("LSU", s, round(m - 0.01, 2), round(m + 0.01, 2), round(1 - m - 0.02, 2), round(1 - m + 0.03, 2))
        )
    for s, mid in ((3.5, 0.12), (6.5, 0.07)):
        out.append(
            rung(
                "UK", s, round(mid - 0.01, 2), round(mid + 0.01, 2), round(1 - mid - 0.02, 2), round(1 - mid + 0.03, 2)
            )
        )
    return out


def norm(rows):
    return W.spread_markets_for(rows, KEY)


CODES = {"status": "RESOLVED", "codes": {"LSU": "99", "UK": "96"}}


# --------------------------------------------------------------------------- pre-registration


def test_candidates_are_registered_and_pinned():
    doc = W.load_candidates(ROOT)
    assert W.sha256(doc) == W.CANDIDATES_SHA256
    protocol = (ROOT / "docs" / "research" / "FOOTBALL_SIGNAL_DISCOVERY_WAVE2_PROTOCOL.md").read_text()
    assert "Status: **PRE-REGISTERED**" in protocol and W.CANDIDATES_SHA256 in protocol
    assert doc["streams"][W.PROS_001]["standardisation"] == {
        "mean": 0.13804024626610417,
        "sd": 1.4218539928759784,
        "source": doc["streams"][W.PROS_001]["standardisation"]["source"],
    }
    assert "EDGE_CONFIRMED" not in doc["verdict_states"] and doc["never_emitted_by_code"] == ["EDGE_CONFIRMED"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["streams"]["CFB-PROS-001"]["standardisation"].__setitem__("sd", 1.0),
        lambda d: d["streams"]["CFB-PROS-002"].__setitem__("frozen_threshold_points", 3.5),
        lambda d: d["population"].__setitem__("kickoff_on_or_after_utc", "2026-09-01T00:00:00Z"),
        lambda d: d.__setitem__("status", "DRAFT"),
    ],
)
def test_any_candidate_edit_is_refused(tmp_path, mutate):
    doc = json.loads((ROOT / W.CANDIDATES_PATH).read_text())
    mutate(doc)
    (tmp_path / W.CANDIDATES_PATH).parent.mkdir(parents=True)
    (tmp_path / W.CANDIDATES_PATH).write_text(json.dumps(doc))
    with pytest.raises(W.CandidatesMismatch):
        W.load_candidates(tmp_path)


def test_wave1_rule_is_recovered_exactly_not_retuned():
    report = json.loads((ROOT / "data/scripting/validation/signal_discovery_wave1/evaluation_report.json").read_text())
    market = report["results"]["CFB-DSC-001"]["market"]
    assert (market["feature_mean"], market["feature_sd"]) == (W.DSC001_MEAN, W.DSC001_SD)
    set2 = json.loads((ROOT / "data/scripting/validation/signal_discovery_wave1/hypotheses_set2.json").read_text())
    spec = next(h for h in set2["hypotheses"] if h["id"] == "CFB-DSC-001")
    assert spec["feature"] == W.DSC001_FEATURE and spec["expected_sign"] == 1
    assert W.Z_THRESHOLD == 1.0 and W.SKEPTIC_BELOW_POINTS == 3.0


# --------------------------------------------------------------------------- ladder and market center


def test_spread_code_strips_the_strike_integer_only():
    assert W.spread_code("KXNCAAFSPREAD-26OCT09FSULOU-LOU4", 3.5) == "LOU"
    assert W.spread_code("KXNCAAFSPREAD-26OCT09FSULOU-FSU15", 14.5) == "FSU"
    assert W.spread_code("KXNCAAFSPREAD-26OCT09FSULOU-LOU4", 4.5) is None  # strike disagrees with the ticker
    assert W.spread_code("KXNCAAFSPREAD-X-12", 11.5) is None  # no team code left


def _wave1_reference(markets, team_code):
    """The Wave-1 script's arithmetic, restated independently."""
    pts = []
    for m in markets:
        yb, ya = m["yes_bid"], m["yes_ask"]
        if not (0 < yb <= ya < 1):
            continue
        mid = (ya + yb) / 2
        st = m["floor_strike"]
        pts.append((st, mid) if m["team_code"] == team_code else (-st, 1 - mid))
    pts.sort()
    for (s1, p1), (s2, p2) in zip(pts, pts[1:], strict=False):
        if p1 >= 0.5 >= p2 and p1 != p2:
            return s1 + (p1 - 0.5) / (p1 - p2) * (s2 - s1)
    return None


def test_implied_margin_is_the_wave1_algorithm_from_both_perspectives():
    rows = norm(ladder())
    lsu, bracket = W.implied_margin(W.ladder_points(rows, "99", CODES["codes"]))
    uk, _ = W.implied_margin(W.ladder_points(rows, "96", CODES["codes"]))
    assert lsu == pytest.approx(_wave1_reference(rows, "LSU")) and uk == pytest.approx(_wave1_reference(rows, "UK"))
    assert 6.5 < lsu < 7.5 and bracket == ["KXNCAAFSPREAD-26OCT10LSUUK-LSU7", "KXNCAAFSPREAD-26OCT10LSUUK-LSU8"]
    assert uk == pytest.approx(-lsu)  # orientation flips the sign, nothing else


def test_missing_center_stays_missing():
    rows = norm([rung("LSU", 3.5, 0.30, 0.32), rung("LSU", 6.5, 0.20, 0.22)])  # never crosses 0.5
    assert W.implied_margin(W.ladder_points(rows, "99", CODES["codes"])) == (None, None)
    bad = norm([rung("LSU", 3.5, 0.60, 0.50)])  # bid > ask: not a valid mid
    assert W.ladder_points(bad, "99", CODES["codes"]) == []


def test_no_is_never_one_minus_yes():
    rows = norm(ladder())
    pts = W.ladder_points(rows, "96", CODES["codes"])  # UK perspective: LSU rungs become NO contracts
    nat = W.natural_rung(pts)
    assert not nat["own_rung"]
    contract = W.contract_for(nat)
    raw = next(r for r in rows if r["market_ticker"] == nat["ticker"])
    assert contract["contract_side"] == "no" and contract["ask"] == raw["no_ask"]
    assert contract["ask"] != pytest.approx(1 - raw["yes_bid"])  # the captured NO ask, not a complement
    no_ask_missing = norm([{**rung("LSU", 6.5, 0.52, 0.54), "no_ask_dollars": None}, rung("LSU", 7.5, 0.46, 0.48)])
    c2 = W.contract_for(W.natural_rung(W.ladder_points(no_ask_missing, "96", CODES["codes"])))
    assert c2["status"] == W.ENTRY_UNAVAILABLE and c2["reason"] == "QUOTE_NOT_EXECUTABLE" and c2["fee"] is None


def test_natural_rung_tie_break_is_deterministic():
    a = {"s": -3.5, "p": 0.55, "ticker": "B", "own_rung": True}
    b = {"s": 3.5, "p": 0.45, "ticker": "A", "own_rung": True}
    c = {"s": 2.5, "p": 0.45, "ticker": "Z", "own_rung": True}
    assert W.natural_rung([a, b, c])["ticker"] == "Z"  # equal |p-0.5|: smaller |s| wins
    assert W.natural_rung([a, b])["ticker"] == "A"  # equal |s| too: ticker order


def test_fee_is_computed_or_unavailable_never_zero():
    assert W.spread_fee(0.50) == 0.02 and W.spread_fee(0.07) == 0.01
    assert W.spread_fee(None) is None and W.spread_fee(1.0) is None and W.spread_fee(0.505) is None
    one_dollar = {
        "own_rung": True,
        "yes_ask": 1.0,
        "no_ask": 0.2,
        "ticker": "T",
        "floor_strike": 3.5,
        "s": 3.5,
        "p": 0.5,
    }
    assert W.contract_for(one_dollar)["status"] == W.ENTRY_UNAVAILABLE


def test_contract_payouts_on_half_point_strikes():
    yes = {"contract_side": "yes", "floor_strike": 6.5}
    no = {"contract_side": "no", "floor_strike": 6.5}  # NO on "opponent wins by over 6.5"
    assert W.contract_pays(yes, 7) and not W.contract_pays(yes, 6)
    assert W.contract_pays(no, -6) and not W.contract_pays(no, -7) and W.contract_pays(no, 3)


# --------------------------------------------------------------------------- checkpoint


def _attempt(minutes, outcome=cap.ATTEMPT_OK, markets=None, kickoff=KICKOFF):
    game = cap.SlateGame(game_key=KEY, kickoff_utc=kickoff, kickoff_source="espn_schedule")
    slots = [cap.slot_label(int(minutes))]
    a, rows = cap.capture(
        game,
        slots,
        at(minutes, kickoff),
        None if outcome == cap.ATTEMPT_FAILED else (markets or []),
        request_error="boom" if outcome == cap.ATTEMPT_FAILED else None,
    )
    return a, rows


def test_checkpoint_is_the_last_valid_capture_inside_the_window_only():
    a1, q1 = _attempt(165, markets=norm(ladder(0.0)))
    a2, q2 = _attempt(70, markets=norm(ladder(0.10)))
    a3, q3 = _attempt(30, markets=norm(ladder(0.30)))  # descriptive closing slot: never an entry
    cp = W.checkpoint(
        kickoff=KICKOFF, now=at(10), attempts=[a1, a2, a3], quotes=q1 + q2 + q3, codes_status=CODES, team_id="99"
    )
    assert cp["status"] == W.ELIGIBLE and cp["captured_at"] == at(70)
    flat, _ = _attempt(70, markets=norm([rung("LSU", 3.5, 0.30, 0.32), rung("LSU", 6.5, 0.20, 0.22)]))
    cp2 = W.checkpoint(
        kickoff=KICKOFF, now=at(10), attempts=[a1, flat, a3], quotes=q1 + q3, codes_status=CODES, team_id="99"
    )
    assert cp2["captured_at"] == at(165)  # the latest VALID in-window capture, never the 30-minute one


def test_checkpoint_missingness_is_named():
    ok, q = _attempt(120, markets=norm(ladder()))
    assert (
        W.checkpoint(
            kickoff=KICKOFF,
            now=at(100),
            attempts=[ok],
            quotes=q,
            codes_status={"status": "ORIENTATION_UNRESOLVED", "codes": {}},
            team_id="99",
        )["status"]
        == W.PENDING
    )
    failed, _ = _attempt(120, outcome=cap.ATTEMPT_FAILED)
    assert (
        W.checkpoint(kickoff=KICKOFF, now=at(50), attempts=[failed], quotes=[], codes_status=CODES, team_id="99")[
            "status"
        ]
        == W.SYSTEM_FAILURE
    )
    empty, _ = _attempt(120, markets=[])
    assert (
        W.checkpoint(kickoff=KICKOFF, now=at(50), attempts=[empty], quotes=[], codes_status=CODES, team_id="99")[
            "status"
        ]
        == W.MARKET_NOT_OFFERED
    )
    unresolved = {"status": "ORIENTATION_CONFLICT", "codes": {}}
    assert (
        W.checkpoint(kickoff=KICKOFF, now=at(50), attempts=[ok], quotes=q, codes_status=unresolved, team_id="99")[
            "status"
        ]
        == W.ORIENTATION_FAILURE
    )
    flat, fq = _attempt(120, markets=norm([rung("LSU", 3.5, 0.30, 0.32)]))
    assert (
        W.checkpoint(kickoff=KICKOFF, now=at(50), attempts=[flat], quotes=fq, codes_status=CODES, team_id="99")[
            "status"
        ]
        == W.ENTRY_UNAVAILABLE
    )


# --------------------------------------------------------------------------- frozen rows


GAME = {
    "game_key": KEY,
    "kickoff_utc": KICKOFF,
    "game_id": EVENT,
    "teams": {"home": {"team_id": "96", "name": "Kentucky"}, "away": {"team_id": "99", "name": "LSU"}},
}


def _feature(x):
    return {"eligibility": None, W.DSC001_FEATURE: x, "football_data_cutoff": "2026-10-10T08:00:00Z"}


@pytest.mark.parametrize(
    ("z", "status", "side"),
    [(1.0, W.PENDING, "home"), (-1.0, W.PENDING, "away"), (0.999, W.EXCLUDED_PROTOCOL, None), (2.5, W.PENDING, "home")],
)
def test_pros001_qualification_and_side(z, status, side):
    row = W.observation_001(
        game=GAME, feature=_feature(W.DSC001_MEAN + z * W.DSC001_SD), reason=None, now=at(190), code_sha="x"
    )
    assert row["status"] == status and row.get("side") == side
    if side:
        assert row["side_team"] == GAME["teams"][side]


def test_pros001_ineligible_features_are_excluded_with_a_reason():
    row = W.observation_001(
        game=GAME,
        feature={"eligibility": "NO_PRIOR_HISTORY_HOME", W.DSC001_FEATURE: None},
        reason=None,
        now=at(190),
        code_sha=None,
    )
    assert row["status"] == W.EXCLUDED_PROTOCOL and row["reason"] == "FEATURE_NO_PRIOR_HISTORY_HOME"
    assert (
        W.observation_001(game=GAME, feature=None, reason="NO_ESPN_IDENTITY", now=at(50), code_sha=None)["status"]
        == W.IDENTITY_FAILURE
    )


def _cp(implied):
    pts = W.ladder_points(norm(ladder()), "99", CODES["codes"])
    return {
        "status": W.ELIGIBLE,
        "reason": None,
        "captured_at": at(70),
        "minutes_before": 70.0,
        "market_implied_margin": implied,
        "bracket": [],
        "rungs_used": [],
        "points": pts,
    }


@pytest.mark.parametrize(("implied", "status"), [(2.99, W.ELIGIBLE), (3.0, W.EXCLUDED_PROTOCOL), (-4.0, W.ELIGIBLE)])
def test_pros002_threshold_is_strictly_below_three(implied, status):
    ledger_row = {
        "claims": {"control": {"tier": "AWAY_CONTROL_STRONG", "side": "away", "strength": "STRONG"}},
        "recorded_at": at(150),
    }
    obs = W.observation_002(game=GAME, ledger_row=ledger_row, now=at(140), code_sha=None)
    assert obs["status"] == W.PENDING and obs["side"] == "away"
    assert W.entry_row(obs=obs, cp=_cp(implied), now=at(55), code_sha=None)["status"] == status


# --------------------------------------------------------------------------- verdicts


def _settled(n, residual, stream=W.PROS_001):
    return [
        {
            "signal_id": stream,
            "status": W.SETTLED,
            "residual": residual,
            "ats_like": "WIN" if residual > 0 else "LOSS",
            "outright_win": True,
            "side": "home",
            "economics": {"available": False},
        }
        for _ in range(n)
    ]


def test_no_settled_sample_is_never_zero():
    s = W.stream_summary(W.PROS_001, [])
    assert s == {"n": 0, "display": "NO SETTLED SAMPLE", "verdict": W.PROSPECTIVE_TRACKING}


def test_reads_and_falsification_follow_the_registration():
    assert W.stream_summary(W.PROS_001, _settled(49, 1.0))["verdict"] == W.PROSPECTIVE_TRACKING
    assert W.stream_summary(W.PROS_001, _settled(50, 1.0))["verdict"] == W.EARLY_READ
    assert W.stream_summary(W.PROS_001, _settled(200, -1.0))["verdict"] == W.INTERIM  # never rejected before 400
    assert W.stream_summary(W.PROS_001, _settled(400, -0.1))["verdict"] == W.REJECTED
    assert W.stream_summary(W.PROS_001, _settled(400, 0.5))["verdict"] == W.REVIEW_REQUIRED
    assert W.stream_summary(W.PROS_002, _settled(100, -1.0, W.PROS_002))["verdict"] == W.REJECTED  # 0% < 52%
    assert W.stream_summary(W.PROS_002, _settled(100, 1.0, W.PROS_002))["verdict"] == W.REVIEW_REQUIRED
    assert "EDGE_CONFIRMED" not in W.VERDICT_STATES


# --------------------------------------------------------------------------- the cycle (append-only, no backfill)


def _schedule():
    sched = json.loads((ROOT / "data" / "football" / "2026" / "schedule.json").read_text())
    conductor = _conductor()
    return conductor.with_name_keys([copy.deepcopy(e) for e in sched["events"] if e["id"] == EVENT]), conductor


def _conductor():
    spec = importlib.util.spec_from_file_location(
        "cfb_research_conductor", ROOT / "scripts" / "cfb_research_conductor.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cfb_research_conductor"] = mod
    spec.loader.exec_module(mod)
    return mod


def _frozen(key):
    path = ROOT / "data" / "scripting" / "live" / "frozen_v2" / f"{key}.json.gz"
    return json.loads(gzip.open(path).read()) if path.exists() else None


def _winner_quotes(now):
    game = cap.SlateGame(game_key=KEY, kickoff_utc=KICKOFF, kickoff_source="espn_schedule", title="LSU at Kentucky")
    raw = [
        {
            "ticker": f"KXNCAAFGAME-{KEY}-LSU",
            "event_ticker": f"KXNCAAFGAME-{KEY}",
            "yes_sub_title": "LSU",
            "status": "active",
            "yes_ask_dollars": "0.7600",
            "no_ask_dollars": "0.2600",
        },
        {
            "ticker": f"KXNCAAFGAME-{KEY}-UK",
            "event_ticker": f"KXNCAAFGAME-{KEY}",
            "yes_sub_title": "Kentucky",
            "status": "active",
            "yes_ask_dollars": "0.2500",
            "no_ask_dollars": "0.7700",
        },
    ]
    _, rows = cap.capture(game, ["T120"], now, [cap.normalize_market(m) for m in raw])
    return rows


def _final_rows(tmp_path, lsu, uk):
    base = json.loads((ROOT / "data" / "football" / "2026" / "team_games.jsonl").read_text().splitlines()[0])
    rows = []
    for tid, team, opp, pf, pa, site in (
        ("99", "LSU", "96", lsu, uk, "away"),
        ("96", "Kentucky", "99", uk, lsu, "home"),
    ):
        rows.append(
            {
                **base,
                "game_id": EVENT,
                "team_id": tid,
                "team": team,
                "opponent_id": opp,
                "points_for": float(pf),
                "points_against": float(pa),
                "kickoff_utc": KICKOFF,
                "site": site,
                "week": 7,
            }
        )
    path = tmp_path / "tg.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return list(read_jsonl(path))


def _run(store, now, monkeypatch, *, team_rows=(), fetch=None, z=1.6, winner=None, ledger_v2=()):
    schedule, conductor = _schedule()
    monkeypatch.setattr(C, "feature_for", lambda *a, **k: _feature(W.DSC001_MEAN + z * W.DSC001_SD))
    game = cap.SlateGame(
        game_key=KEY, kickoff_utc=KICKOFF, kickoff_source="espn_schedule", title="LSU at Kentucky", espn_event_id=EVENT
    )
    return C.run(
        store_dir=store,
        root=ROOT,
        season=2026,
        now=now,
        games=[game],
        schedule=schedule,
        team_rows=list(team_rows),
        frozen=_frozen,
        ledger_v2=list(ledger_v2),
        winner_quotes=winner or [],
        keys=conductor.market_keys,
        fetch_spread=fetch,
        code_sha="test",
    )


def _ledger(store):
    return [json.loads(x) for x in (store / "wave2" / "2026" / "ledger.jsonl").read_text().splitlines()]


def test_full_cycle_observe_enter_settle_is_append_only_and_idempotent(tmp_path, monkeypatch):
    store = tmp_path / "store"
    winner = _winner_quotes(at(120))
    fetch = lambda key: ladder()  # noqa: E731
    _run(store, at(195), monkeypatch, fetch=fetch)  # observation frozen before the window
    assert [(r["record"], r["status"], r["side"]) for r in _ledger(store)] == [("OBSERVATION", "PENDING", "home")]
    for m in (165, 120, 90, 70):
        _run(store, at(m), monkeypatch, fetch=fetch, winner=winner)
    attempts = [json.loads(x) for x in (store / "wave2" / "2026" / "spread_attempts.jsonl").read_text().splitlines()]
    assert [a["slots"] for a in attempts] == [["T165"], ["T120"], ["T90"], ["T70"]] and {
        a["outcome"] for a in attempts
    } == {"OK"}
    _run(store, at(55), monkeypatch, fetch=fetch, winner=winner)
    entry = _ledger(store)[-1]
    assert entry["record"] == "ENTRY" and entry["status"] == W.ELIGIBLE and entry["captured_at"] == at(70)
    assert entry["market_implied_margin"] == pytest.approx(
        -_wave1_reference(norm(ladder()), "LSU"), abs=1e-3
    )  # Kentucky (home) view
    frozen_before = (store / "wave2" / "2026" / "ledger.jsonl").read_text()
    final = _final_rows(tmp_path, lsu=24, uk=21)
    _run(
        store,
        cap.iso(cap.parse_utc(KICKOFF) + timedelta(hours=5)),
        monkeypatch,
        fetch=fetch,
        winner=winner,
        team_rows=final,
    )
    text = (store / "wave2" / "2026" / "ledger.jsonl").read_text()
    assert text.startswith(frozen_before)  # earlier rows untouched
    st = _ledger(store)[-1]
    assert st["record"] == "SETTLEMENT" and st["actual_margin"] == -3.0
    assert st["residual"] == pytest.approx(-3.0 - entry["market_implied_margin"], abs=1e-3)
    assert st["ats_like"] == ("WIN" if st["residual"] > 0 else "LOSS") and st["outright_win"] is False
    _run(
        store,
        cap.iso(cap.parse_utc(KICKOFF) + timedelta(hours=6)),
        monkeypatch,
        fetch=fetch,
        winner=winner,
        team_rows=final,
    )
    assert (store / "wave2" / "2026" / "ledger.jsonl").read_text() == text  # idempotent
    report = json.loads((store / "wave2" / "reports" / "2026" / "wave2_status.json").read_text())
    assert report["streams"][W.PROS_001]["summary"]["n"] == 1
    assert report["streams"][W.PROS_002]["summary"]["display"] == "NO SETTLED SAMPLE"


def test_a_failed_spread_read_is_request_failed_never_no_market(tmp_path, monkeypatch):
    def boom(key):
        raise ConnectionError("reset")

    store = tmp_path / "s"
    _run(store, at(160), monkeypatch, fetch=boom)
    a = json.loads((store / "wave2" / "2026" / "spread_attempts.jsonl").read_text().splitlines()[0])
    assert a["outcome"] == cap.ATTEMPT_FAILED and a["error"].startswith("ConnectionError")
    _run(store, at(50), monkeypatch, fetch=boom)
    entry = [r for r in _ledger(store) if r["record"] == "ENTRY"][0]
    assert entry["status"] == W.SYSTEM_FAILURE  # our pipeline, not the market


def test_no_backfill_a_missed_observation_is_a_system_failure(tmp_path, monkeypatch):
    store = tmp_path / "s"
    _run(store, at(50), monkeypatch, fetch=lambda k: ladder())  # first look is after the window closed
    rows = _ledger(store)
    assert [(r["signal_id"], r["record"], r["status"]) for r in rows if r["signal_id"] == W.PROS_001] == [
        (W.PROS_001, "OBSERVATION", W.SYSTEM_FAILURE)
    ]
    assert not [r for r in rows if r["record"] == "ENTRY"]


def test_games_before_activation_are_never_in_the_population(tmp_path, monkeypatch):
    schedule, conductor = _schedule()
    early = "2026-10-09T23:00:00Z"
    game = cap.SlateGame(game_key=KEY, kickoff_utc=early, kickoff_source="espn_schedule")
    monkeypatch.setattr(C, "feature_for", lambda *a, **k: _feature(5.0))
    out = C.run(
        store_dir=tmp_path,
        root=ROOT,
        season=2026,
        now=at(120, early),
        games=[game],
        schedule=schedule,
        team_rows=[],
        frozen=_frozen,
        ledger_v2=[],
        winner_quotes=[],
        keys=conductor.market_keys,
        fetch_spread=lambda k: ladder(),
        code_sha=None,
    )
    assert out["spread_attempts"] == 0 and out["ledger_rows"] == 0


def test_pros002_observation_copies_the_final_pregame_row(tmp_path, monkeypatch):
    store = tmp_path / "s"
    fp = {
        "kind": "FINAL_PREGAME",
        "methodology_version": "cfb-script-engine/2.0.0",
        "game_key": KEY,
        "event_id": EVENT,
        "kickoff_utc": KICKOFF,
        "recorded_at": at(170),
        "claims_artifact_hash": "h",
        "teams": {"home": {"team_id": "96", "name": "Kentucky"}, "away": {"team_id": "99", "name": "LSU"}},
        "claims": {"control": {"tier": "AWAY_CONTROL_STRONG", "side": "away", "strength": "STRONG"}},
    }
    _run(store, at(160), monkeypatch, fetch=lambda k: ladder(), ledger_v2=[fp])
    obs = [r for r in _ledger(store) if r["signal_id"] == W.PROS_002]
    assert len(obs) == 1 and obs[0]["side"] == "away" and obs[0]["signal"]["claims_artifact_hash"] == "h"


# --------------------------------------------------------------------------- isolation from production


def test_conductor_survives_a_wave2_failure(tmp_path, monkeypatch):
    conductor = _conductor()

    def boom(**kwargs):
        raise RuntimeError("wave2 exploded")

    monkeypatch.setattr(conductor.wave2_cycle, "run", boom)
    main = tmp_path / "main"
    for sub in (
        "data/live",
        "data/football/2026",
        "data/scripting/live/sift",
        "data/scripting/live/frozen_v2",
        "app/latest",
    ):
        (main / sub).mkdir(parents=True)
    (main / "data/live/cfb_market_catalog.json").write_text(json.dumps({"capture": {}, "games": []}))
    (main / "data/football/2026/schedule.json").write_text(json.dumps({"events": []}))
    (main / "app/latest/events.json").write_text(json.dumps({"items": []}))
    (tmp_path / "ledger" / "2026").mkdir(parents=True)
    out = conductor.run_cycle(
        main_dir=main, ledger_dir=tmp_path / "ledger", store_dir=tmp_path / "store", now=at(120), fetch=lambda: []
    )
    assert out["wave2"]["status"] == "SYSTEM_FAILURE" and "wave2 exploded" in out["wave2"]["error"]
    assert (tmp_path / "store" / "signals" / "cfb_research_signals.json").exists()  # SIFT's contract still published
    sift = (tmp_path / "store" / "signals" / "cfb_research_signals.json").read_text()
    assert "PROS-00" not in sift and "wave2" not in sift  # research signals never reach SIFT


def test_wave2_modules_import_no_production_decision_code():
    for mod in ("wave2", "wave2_cycle"):
        text = (ROOT / "src" / "cfb_edge_finder" / "signal_discovery" / f"{mod}.py").read_text()
        for banned in ("sizing", "recommend", "router", "bankroll", "stake", "betting_card", "sift_export"):
            assert f"import {banned}" not in text and f".{banned} import" not in text and f".{banned}." not in text


def test_status_report_carries_no_betting_language(tmp_path, monkeypatch):
    from cfb_edge_finder.control_prospective import BANNED_WORDS

    _run(tmp_path, at(160), monkeypatch, fetch=lambda k: ladder())
    text = (tmp_path / "wave2" / "reports" / "2026" / "wave2_status.json").read_text().lower()
    import re

    for word in BANNED_WORDS:
        assert not re.search(rf"(?<![a-z0-9_+]){re.escape(word)}(?![a-z0-9_])", text), word


def test_feature_reads_only_games_finished_before_the_cutoff(tmp_path):
    """Real 2026 log, real builder: the target's own result and anything after the 04:00 ET cutoff never move it."""
    from cfb_edge_finder.scripting.football import LeagueFitCache

    schedule, _ = _schedule()
    ident = C.identity(_frozen(KEY), schedule)
    rows = list(read_jsonl(ROOT / "data" / "football" / "2026" / "team_games.jsonl"))
    clean = C.feature_for(ident, KICKOFF, 2026, tuple(rows), schedule, LeagueFitCache())
    doctored = rows + _final_rows(tmp_path, lsu=70, uk=0)  # the target's own (fake) result
    later = [r for r in doctored if r.kickoff_utc >= "2026-10-10T08:00:00Z"]
    assert later  # non-vacuous: rows at/after the cutoff exist in the doctored log
    dirty = C.feature_for(ident, KICKOFF, 2026, tuple(doctored), schedule, LeagueFitCache())
    keep = (
        "eligibility",
        W.DSC001_FEATURE,
        "home_def_q.rush_success_rate",
        "away_def_q.rush_success_rate",
        "net.rushing",
    )
    assert {k: clean.get(k) for k in keep} == {k: dirty.get(k) for k in keep}
    assert clean["football_data_cutoff"] == "2026-10-10T08:00:00Z"
