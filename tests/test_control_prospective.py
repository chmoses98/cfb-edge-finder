"""Prospective CONTROL research: protocol, capture, tracking, promotion gate, research-signals contract, V2 settlement.

Everything here runs offline on the committed data and synthetic rows; the conductor's Kalshi read is injected.
"""

from __future__ import annotations

import copy
import gzip
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from cfb_edge_finder.control_prospective import (
    CAPTURE_OK,
    CAPTURE_PENDING,
    CAPTURE_SYSTEM_FAILURE,
    DISAGREEMENT_BELOW_CENTS,
    EDGE_CONFIRMED,
    H1_ID,
    H1_WORDING,
    H2_ID,
    H2_WORDING,
    MARKET_NOT_OFFERED,
    ORIENTATION_FAILURE,
    PRICE_1_00,
    PROTOCOL_SHA256,
    QUOTE_NOT_EXECUTABLE,
    REVIEW_REQUIRED,
    VALUE_WATCH,
    ProtocolMismatch,
    load_protocol,
    sha256,
    verify_protocol,
)
from cfb_edge_finder.control_prospective import capture as cap
from cfb_edge_finder.control_prospective import signals as sig
from cfb_edge_finder.control_prospective import tracking as trk
from cfb_edge_finder.scripting.gamelog import TeamGame
from cfb_edge_finder.scripting.realized_v2 import build_report_v2, key_of, settle_rows

ROOT = Path(__file__).resolve().parent.parent
KICKOFF = "2026-10-10T20:00:00Z"


def _conductor():
    spec = importlib.util.spec_from_file_location(
        "cfb_research_conductor", ROOT / "scripts" / "cfb_research_conductor.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cfb_research_conductor"] = mod
    spec.loader.exec_module(mod)
    return mod


def at(minutes_before: float, kickoff: str = KICKOFF) -> str:
    from datetime import timedelta

    return cap.iso(cap.parse_utc(kickoff) - timedelta(minutes=minutes_before))


GAME = cap.SlateGame(
    game_key="26OCT10AAABBB", kickoff_utc=KICKOFF, kickoff_source="espn_schedule", title="Away U at Home St"
)


def mk(ticker_code: str, yes_ask, no_ask=None, status="active", sub=None) -> dict:
    return {
        "market_ticker": f"KXNCAAFGAME-26OCT10AAABBB-{ticker_code}",
        "event_ticker": "KXNCAAFGAME-26OCT10AAABBB",
        "team_code": ticker_code,
        "yes_sub_title": sub or ticker_code,
        "status": status,
        "yes_bid": None,
        "yes_ask": yes_ask,
        "no_bid": None,
        "no_ask": no_ask,
    }


# --------------------------------------------------------------------------- protocol / H1 / H2


def test_protocol_is_registered_and_pinned():
    protocol = verify_protocol(load_protocol(ROOT))
    assert sha256(protocol) == PROTOCOL_SHA256 == "b35c75ef64447ba6087dc83d1741e42e9d2570528f4fee5518154724a84c8021"
    assert protocol["status"] == "PRE-REGISTERED"


def test_hypothesis_wording_is_frozen_exactly():
    protocol = load_protocol(ROOT)
    assert protocol["hypotheses"]["H1"]["wording"] == H1_WORDING
    assert H1_WORDING == (
        "MODERATE CONTROL, home and away pooled, purchased at the frozen PRIMARY_60_180 executable game-winner ask "
        "at all available prices, has fee-adjusted ROI > 0."
    )
    assert protocol["hypotheses"]["H2"]["wording"] == H2_WORDING
    assert H2_WORDING == "STRONG CONTROL priced below 85¢ underperforms its fee-adjusted break-even rate."
    assert protocol["hypotheses"]["H2"]["entry_ask_strictly_below_cents"] == DISAGREEMENT_BELOW_CENTS == 85
    assert protocol["entry"]["price_cap"].startswith("none")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["hypotheses"]["H1"].__setitem__("wording", "MODERATE CONTROL has ROI > 0."),
        lambda p: p["hypotheses"]["H2"].__setitem__("entry_ask_strictly_below_cents", 80),
        lambda p: p["capture"].__setitem__("slots_minutes_before_kickoff", [150, 90]),
        lambda p: p["review_checkpoints"].__setitem__("30", "PRIMARY_REVIEW"),
    ],
)
def test_any_protocol_edit_is_refused(mutate):
    protocol = copy.deepcopy(load_protocol(ROOT))
    mutate(protocol)
    with pytest.raises(ProtocolMismatch):
        verify_protocol(protocol)


def test_protocol_forbids_staking_bankroll_and_recommendations():
    forbidden = " ".join(load_protocol(ROOT)["forbidden"]).lower()
    for word in ("stake", "bankroll", "kelly", "recommendation", "bet-up-to"):
        assert word in forbidden


# --------------------------------------------------------------------------- capture


def test_due_slots_inside_the_primary_window_only():
    assert cap.due_slots(KICKOFF, at(200), set()) == []
    assert cap.due_slots(KICKOFF, at(181), set()) == []
    assert cap.due_slots(KICKOFF, at(180), set()) == []  # 180 > every primary slot
    assert cap.due_slots(KICKOFF, at(165), set()) == ["T165"]
    assert cap.due_slots(KICKOFF, at(119), {"T165"}) == ["T120"]
    assert cap.due_slots(KICKOFF, at(60), {"T165", "T120", "T90", "T70"}) == []
    assert cap.due_slots(KICKOFF, at(59), {"T165", "T120", "T90", "T70"}) == []
    assert cap.due_slots(KICKOFF, at(30), {"T165", "T120", "T90", "T70"}) == ["T30"]


def test_missed_slots_are_caught_up_while_the_window_is_open():
    # the conductor was down from T-170 to T-85: one catch-up attempt covers every overdue slot
    assert cap.due_slots(KICKOFF, at(85), set()) == ["T165", "T120", "T90"]
    # after the window closes nothing primary is attempted any more
    assert cap.due_slots(KICKOFF, at(55), set()) == []


def test_no_capture_at_or_after_kickoff():
    assert cap.due_slots(KICKOFF, KICKOFF, set()) == []
    assert cap.due_slots(KICKOFF, at(-5), set()) == []
    with pytest.raises(ValueError):
        cap.capture(GAME, ["T30"], KICKOFF, [mk("AAA", 0.5)])


def test_failed_read_is_never_market_absence():
    attempt, quotes = cap.capture(GAME, ["T165"], at(165), None, request_error="ConnectionError: reset")
    assert attempt["outcome"] == cap.ATTEMPT_FAILED and quotes == []
    attempt2, _ = cap.capture(GAME, ["T120"], at(120), [])
    assert attempt2["outcome"] == cap.ATTEMPT_NO_MARKET
    after = at(30)
    failed_only = cap.side_status(
        kickoff=KICKOFF, now=after, market_ticker=None, orientation_status=None, quotes=[], attempts=[attempt]
    )
    assert failed_only["status"] == CAPTURE_SYSTEM_FAILURE
    absent = cap.side_status(
        kickoff=KICKOFF, now=after, market_ticker=None, orientation_status=None, quotes=[], attempts=[attempt, attempt2]
    )
    assert absent["status"] == MARKET_NOT_OFFERED
    nothing = cap.side_status(
        kickoff=KICKOFF, now=after, market_ticker=None, orientation_status=None, quotes=[], attempts=[]
    )
    assert nothing["status"] == CAPTURE_SYSTEM_FAILURE


def test_pending_until_the_window_closes():
    status = cap.side_status(
        kickoff=KICKOFF, now=at(200), market_ticker=None, orientation_status=None, quotes=[], attempts=[]
    )
    assert status["status"] == CAPTURE_PENDING
    assert status["window_opens_at"] == at(180) and status["window_closes_at"] == at(60)


def test_entry_is_the_last_valid_yes_ask_in_the_window():
    rows = []
    for minutes, ask in ((190, 0.70), (165, 0.72), (120, 0.74), (70, 0.75), (40, 0.80)):
        _, q = cap.capture(GAME, [f"T{minutes}"], at(minutes), [mk("AAA", ask, 0.30), mk("BBB", 0.31, 0.71)])
        rows += q
    entry = cap.primary_entry(rows, "KXNCAAFGAME-26OCT10AAABBB-AAA", KICKOFF)
    assert entry["yes_ask"] == 0.75 and entry["minutes_before"] == 70.0
    status = cap.side_status(
        kickoff=KICKOFF,
        now=at(30),
        market_ticker="KXNCAAFGAME-26OCT10AAABBB-AAA",
        orientation_status="RESOLVED",
        quotes=rows,
        attempts=[],
    )
    assert status["status"] == CAPTURE_OK and status["entry"]["price"] == 0.75


def test_a_one_dollar_ask_is_price_1_00_never_a_price():
    _, rows = cap.capture(GAME, ["T120"], at(120), [mk("AAA", 1.0, 0.01), mk("BBB", 0.01, 1.0)])
    attempt = {"in_primary_window": True, "outcome": cap.ATTEMPT_OK}
    status = cap.side_status(
        kickoff=KICKOFF,
        now=at(30),
        market_ticker="KXNCAAFGAME-26OCT10AAABBB-AAA",
        orientation_status="RESOLVED",
        quotes=rows,
        attempts=[attempt],
    )
    assert status["status"] == PRICE_1_00 and status["entry"] is None
    view = cap.current_price(rows[0])
    assert view["status"] == PRICE_1_00 and view["yes_ask"] is None


def test_unexecutable_and_orientation_failures_are_named():
    attempt = {"in_primary_window": True, "outcome": cap.ATTEMPT_OK}
    _, rows = cap.capture(GAME, ["T120"], at(120), [mk("AAA", None, None, status="paused")])
    s = cap.side_status(
        kickoff=KICKOFF,
        now=at(30),
        market_ticker="KXNCAAFGAME-26OCT10AAABBB-AAA",
        orientation_status="RESOLVED",
        quotes=rows,
        attempts=[attempt],
    )
    assert s["status"] == QUOTE_NOT_EXECUTABLE
    s2 = cap.side_status(
        kickoff=KICKOFF,
        now=at(30),
        market_ticker=None,
        orientation_status="ORIENTATION_UNRESOLVED",
        quotes=rows,
        attempts=[attempt],
    )
    assert s2["status"] == ORIENTATION_FAILURE


def test_side_prices_come_from_their_own_fields():
    raw = {
        "ticker": "KXNCAAFGAME-26OCT10AAABBB-AAA",
        "yes_ask_dollars": "0.7400",
        "no_ask_dollars": "0.3100",
        "yes_bid_dollars": "0.7200",
        "no_bid_dollars": "0.2600",
        "status": "active",
    }
    m = cap.normalize_market(raw)
    assert (m["yes_ask"], m["no_ask"], m["yes_bid"], m["no_bid"]) == (0.74, 0.31, 0.72, 0.26)
    assert m["yes_ask"] + m["no_ask"] != 1.0  # the two asks are never complements
    legacy = cap.normalize_market({"ticker": "KXNCAAFGAME-X-AAA", "yes_ask": 74, "no_ask": 31})
    assert (legacy["yes_ask"], legacy["no_ask"]) == (0.74, 0.31)
    missing = cap.normalize_market({"ticker": "KXNCAAFGAME-X-AAA", "yes_ask_dollars": "0.6100"})
    assert missing["no_ask"] is None  # never imputed as 1 - yes


# --------------------------------------------------------------------------- tracking / population


def ledger_row(
    game_key="26OCT10AAABBB",
    kind="FINAL_PREGAME",
    recorded=None,
    tier="HOME_CONTROL_MODERATE",
    kickoff=KICKOFF,
    event_id="999",
    **claims,
):
    side, _, strength = tier.partition("_CONTROL_")
    return {
        "schema_version": "cfb_script_ledger/2.0.0",
        "methodology_version": "cfb-script-engine/2.0.0",
        "activation": "SHADOW",
        "kind": kind,
        "game_key": game_key,
        "season": 2026,
        "event_id": event_id,
        "kickoff_utc": kickoff,
        "recorded_at": recorded or at(100, kickoff),
        "claims_artifact_hash": claims.pop("hash", "h" * 64),
        "football_artifact_hash": "f" * 64,
        "teams": {"home": {"name": "Home St", "team_id": "1"}, "away": {"name": "Away U", "team_id": "2"}},
        "status": "CLAIMS_PUBLISHED",
        "claims": {
            "control": None if tier is None else {"side": side.lower(), "strength": strength, "tier": tier},
            "closeness": claims.get("closeness", False),
            "pace": claims.get("pace"),
            "scoring_environment": claims.get("scoring_environment"),
            "defensive_suppression": claims.get("defensive_suppression", False),
            "disruption": claims.get("disruption", []),
            "explosive_upset": [],
        },
    }


def test_population_reads_the_last_final_pregame_row_and_never_a_publication():
    early = ledger_row(recorded=at(170), hash="a" * 64)
    late = ledger_row(recorded=at(80), hash="b" * 64, tier="HOME_CONTROL_STRONG")
    pub = ledger_row(kind="PUBLICATION", recorded=at(2000), hash="c" * 64)
    after = ledger_row(recorded=at(-10), hash="d" * 64)
    other_method = {**ledger_row(hash="e" * 64), "methodology_version": "cfb-script-engine/1.3.0"}
    pop = trk.population([early, pub, late, after, other_method])
    assert list(pop) == ["26OCT10AAABBB"]
    assert pop["26OCT10AAABBB"]["claims_artifact_hash"] == "b" * 64  # the last one before kickoff, copied
    before_registration = ledger_row(kickoff="2026-10-07T23:00:00Z", recorded="2026-10-07T21:00:00Z")
    assert trk.population([before_registration]) == {}


def test_missing_final_pregame_is_a_counted_pipeline_miss():
    pub = ledger_row(kind="PUBLICATION", recorded=at(2000))
    assert trk.missing_final_pregame([pub], at(-60)) == [
        {"game_key": "26OCT10AAABBB", "kickoff_utc": KICKOFF, "event_id": "999"}
    ]
    assert trk.missing_final_pregame([pub], at(30)) == []  # not kicked off yet


def team(team_id, pts_for, pts_against, site, game_id="999", plays=70.0, sacks=2.0, tfl=5.0):
    return TeamGame(
        game_id=game_id,
        season=2026,
        week=6,
        kickoff_utc=KICKOFF,
        team_id=team_id,
        team=f"T{team_id}",
        opponent_id="2" if team_id == "1" else "1",
        opponent="x",
        team_division="fbs",
        opponent_division="fbs",
        site=site,
        points_for=pts_for,
        points_against=pts_against,
        box={"plays": plays, "total_yards": 400.0, "def_sacks": sacks, "def_tfl": tfl, "turnovers": 1.0},
        pbp=None,
    )


def schedule():
    return [
        {
            "id": "999",
            "date": KICKOFF,
            "competitors": [
                {
                    "team_id": "1",
                    "abbreviation": "BBB",
                    "home_away": "home",
                    "names": ["Home St"],
                    "name_keys": ["home st"],
                },
                {
                    "team_id": "2",
                    "abbreviation": "AAA",
                    "home_away": "away",
                    "names": ["Away U"],
                    "name_keys": ["away u"],
                },
            ],
        }
    ]


def keys(name: str) -> set[str]:
    return {name.lower()} if name else set()


def captured(home_ask=0.74):
    rows, attempts = [], []
    for minutes in (165, 120, 90, 70):
        a, q = cap.capture(
            GAME,
            [f"T{minutes}"],
            at(minutes),
            [mk("AAA", 1 - home_ask + 0.02, home_ask, sub="Away U"), mk("BBB", home_ask, 0.29, sub="Home St")],
        )
        rows += q
        attempts.append(a)
    return rows, attempts


def test_settlement_is_deterministic_idempotent_and_copies_the_frozen_tier():
    quotes, attempts = captured(0.74)
    ledger = [ledger_row(recorded=at(100))]
    teams = [team("1", 31.0, 17.0, "home"), team("2", 17.0, 31.0, "away")]
    kwargs = dict(quotes=quotes, attempts=attempts, schedule=schedule(), keys=keys, team_rows=teams, now=at(-300))
    first = trk.settle(ledger, [], **kwargs)
    again = trk.settle(ledger, [], **kwargs)
    assert first == again and len(first) == 1
    row = first[0]
    assert row["control"]["tier"] == "HOME_CONTROL_MODERATE"
    assert row["capture"]["status"] == CAPTURE_OK and row["capture"]["entry"]["price"] == 0.74
    assert row["settlement"]["control_won"] is True and row["settlement"]["control_margin"] == 14.0
    assert row["economics"]["entry_price"] == 0.74 and row["economics"]["fee"] > 0
    assert row["hypotheses"] == [H1_ID]
    assert trk.settle(ledger, first, **kwargs) == []  # append-only: never written twice


def test_not_settled_before_the_result_exists():
    quotes, attempts = captured()
    out = trk.settle(
        [ledger_row()], [], quotes=quotes, attempts=attempts, schedule=schedule(), keys=keys, team_rows=[], now=at(-300)
    )
    assert out == []


def test_strong_below_85_joins_h2_and_at_or_above_does_not():
    teams = [team("1", 10.0, 20.0, "home"), team("2", 20.0, 10.0, "away")]
    for ask, expected in ((0.84, [H2_ID]), (0.85, [])):
        quotes, attempts = captured(ask)
        rows = trk.settle(
            [ledger_row(tier="HOME_CONTROL_STRONG")],
            [],
            quotes=quotes,
            attempts=attempts,
            schedule=schedule(),
            keys=keys,
            team_rows=teams,
            now=at(-300),
        )
        assert rows[0]["hypotheses"] == expected
        assert rows[0]["settlement"]["control_won"] is False


def synthetic_settled(n, roi_sign=1, tier="HOME_CONTROL_MODERATE", weeks=4):
    out = []
    for i in range(n):
        won = roi_sign > 0 or i % 3 == 0
        price = 0.70
        fee = 0.02
        out.append(
            {
                "game_key": f"G{i}",
                "kickoff_utc": f"2026-10-{10 + i % weeks:02d}T20:00:00Z",
                "control": {"tier": tier if i % 2 == 0 else tier.replace("HOME", "AWAY")},
                "capture": {"status": CAPTURE_OK},
                "settlement": {"status": "SETTLED", "control_won": won},
                "economics": {
                    "available": True,
                    "entry_price": price,
                    "fee": fee,
                    "outlay": price + fee,
                    "settlement_value": 1.0 if won else 0.0,
                    "fee_adjusted_pnl": (1.0 if won else 0.0) - price - fee,
                },
                "hypotheses": [H1_ID] if "MODERATE" in tier else [],
            }
        )
    return out


def test_value_watch_never_auto_promotes_before_review():
    for n in (0, 10, 20, 29):
        assert trk.summarize(synthetic_settled(n), [])["moderate_status"]["status"] == VALUE_WATCH


def test_review_required_at_thirty_with_positive_roi_never_edge_confirmed():
    status = trk.summarize(synthetic_settled(30), [])["moderate_status"]
    assert status["status"] == REVIEW_REQUIRED and status["status"] != EDGE_CONFIRMED
    assert status["edge_criteria_met"] is False  # n < 50


def test_negative_roi_at_thirty_stays_value_watch_until_primary_review():
    assert trk.summarize(synthetic_settled(30, roi_sign=-1), [])["moderate_status"]["status"] == VALUE_WATCH
    assert trk.summarize(synthetic_settled(50, roi_sign=-1), [])["moderate_status"]["status"] == REVIEW_REQUIRED


def test_edge_criteria_flag_still_requires_human_review():
    status = trk.summarize(synthetic_settled(60), [])["moderate_status"]
    assert status["status"] == REVIEW_REQUIRED and status["edge_criteria_met"] is True
    reviewed = trk.summarize(
        synthetic_settled(60), [{"signal": "moderate_control", "status": EDGE_CONFIRMED, "reviewed_by": "owner"}]
    )["moderate_status"]
    assert reviewed["status"] == EDGE_CONFIRMED and reviewed["source"] == "REVIEWED_DECISION"


def test_checkpoints_follow_the_frozen_ladder():
    assert trk.checkpoint(9) == {"reached": None, "reached_at_n": None, "next_at_n": 10, "next": "EARLY_READ_ONLY"}
    assert trk.checkpoint(20)["reached"] == "INTERIM_RESEARCH_READ"
    assert trk.checkpoint(55)["reached"] == "PRIMARY_REVIEW" and trk.checkpoint(55)["next"] is None


# --------------------------------------------------------------------------- research signals contract


@pytest.fixture(scope="module")
def contract():
    return sig.build_signals(
        root=ROOT,
        now="2026-10-08T06:00:00Z",
        games=[],
        summary=trk.summarize([], []),
        population_counts={"by_tier": {}},
        sources={},
    )


def test_contract_schema_and_study_provenance(contract):
    assert contract["schema"] == "cfb_research_signals/1.0.0"
    study = contract["study"]
    assert study["pr"] == "chmoses98/cfb-edge-finder#105"
    assert study["protocol_sha256"] == "61aa278d86582532556d0174abc2e1c5f883ebaa882285fe603d1ee2af23e1db"
    assert study["rows_sha256"] == "3956ea6a50c04cd6234f172ace6b6f95a28a01d85969ce7c8ffd76f16cdf8208"
    assert study["results_commit"].startswith("b7df12c3")
    assert contract["protocol"]["sha256"] == PROTOCOL_SHA256


def test_moderate_is_value_watch_with_the_exact_initial_sample(contract):
    m = contract["signals"]["moderate_control"]
    assert m["status"] == VALUE_WATCH and m["label"] == "Value Watch"
    basis = m["research_basis"]
    report = json.loads((ROOT / sig.STUDY_REPORT).read_text())["results"]["POOLED"]["groups"]["ALL_MODERATE"][
        "economics"
    ]
    assert (
        (basis["retrospective_priced_n"], basis["wins"], basis["losses"])
        == (report["n"], report["wins"], report["losses"])
        == (6, 6, 0)
    )
    assert basis["fee_adjusted_roi"] == round(report["roi_on_outlay"], 4) == 0.3245
    assert basis["market_verdict"] == "INSUFFICIENT_DATA"
    assert "6–0" in m["explanation"] and m["small_sample"] == "Initial priced n = 6"


def test_strong_is_never_value(contract):
    s = contract["signals"]["strong_control"]
    assert s["status"] != VALUE_WATCH and s["status"] == "NO_EDGE"
    assert s["research_basis"]["market_verdict"] == "APPROXIMATELY_EFFICIENT"
    assert s["research_basis"]["fee_adjusted_roi"] == -0.0845
    text = json.dumps({k: v for k, v in s.items() if k != "research_basis"}).lower()
    for word in ("value", "cheap", "+ev", "mispriced"):
        assert word not in text


def test_disagreement_basis_is_computed_from_the_frozen_rows(contract):
    d = contract["signals"]["market_disagreement"]
    assert d["rule"]["below_cents"] == 85
    assert (d["research_basis"]["retrospective_priced_n"], d["research_basis"]["wins"]) == (17, 8)
    assert d["status"] == "INSUFFICIENT_DATA"


def test_no_prospective_numbers_are_faked(contract):
    p = contract["signals"]["moderate_control"]["prospective"]
    assert p["settled_n"] == 0 and p["fee_adjusted_roi"] is None and p["statement"] == "No prospective settlements yet."


def test_prospective_numbers_are_live_from_settlements():
    doc = sig.build_signals(
        root=ROOT,
        now="2026-10-20T06:00:00Z",
        games=[],
        summary=trk.summarize(synthetic_settled(12), []),
        population_counts={"by_tier": {"HOME_CONTROL_MODERATE": 7, "AWAY_CONTROL_MODERATE": 6}},
        sources={},
    )
    p = doc["signals"]["moderate_control"]["prospective"]
    assert p["n"] == 13 and p["settled_n"] == 12 and p["fee_adjusted_roi"] is not None
    assert p["checkpoint"]["reached"] == "EARLY_READ_ONLY"


BANNED = re.compile(r"\b(bet|bets|lock|hammer|fade|wager|kelly|units?|\+ev|best bet|bet up to)\b", re.IGNORECASE)


def _strings(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("never",):
                continue
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v, path)
    elif isinstance(node, str):
        yield path, node


def test_contract_text_carries_no_betting_language(contract):
    offenders = [(p, s) for p, s in _strings(contract) if BANNED.search(s)]
    assert offenders == []


def test_frozen_rows_tampering_is_refused(tmp_path):
    study = tmp_path / sig.STUDY_DIR
    study.mkdir(parents=True)
    (tmp_path / sig.STUDY_REPORT).write_bytes((ROOT / sig.STUDY_REPORT).read_bytes())
    with gzip.open(ROOT / sig.STUDY_ROWS, "rb") as fh:
        payload = fh.read().replace(b'"control_won":true', b'"control_won":false', 1)
    with gzip.open(tmp_path / sig.STUDY_ROWS, "wb") as fh:
        fh.write(payload)
    with pytest.raises(sig.StudyMismatch):
        sig.load_study(tmp_path)


def test_card_line_and_quick_read_are_compact_and_claim_driven():
    cc = {
        "control": {"side": "home", "strength": "STRONG", "tier": "HOME_CONTROL_STRONG", "team": "Georgia"},
        "closeness": False,
        "pace": "LOW",
        "scoring": None,
        "defensive_suppression": True,
        "disruption": [],
    }
    assert sig.card_line(cc, "CLAIMS_PUBLISHED") == "Georgia controls · slower pace · defensive suppression"
    assert (
        sig.quick_read(cc, "CLAIMS_PUBLISHED")
        == "Georgia controls this matchup. Expect a slower game with defensive resistance."
    )
    assert sig.edges(cc)[0] == "Georgia owns the sustained-efficiency matchup"
    assert sig.signal_of(cc) == "STRONG_CONTROL"
    mod = {
        **cc,
        "control": {**cc["control"], "strength": "MODERATE", "tier": "HOME_CONTROL_MODERATE", "team": "Toledo"},
    }
    assert sig.signal_of(mod) == "VALUE_WATCH" and sig.card_line(mod, "CLAIMS_PUBLISHED").startswith(
        "Toledo control edge"
    )
    assert sig.card_line(cc, "NO_SUPPORTED_CLAIM") == "No clear SIFT read"


def test_market_disagreement_needs_strong_and_an_executable_price_below_85():
    assert sig.disagreement("STRONG_CONTROL", {"status": "EXECUTABLE", "yes_ask": 0.84}) is True
    assert sig.disagreement("STRONG_CONTROL", {"status": "EXECUTABLE", "yes_ask": 0.85}) is False
    assert sig.disagreement("STRONG_CONTROL", {"status": PRICE_1_00, "yes_ask": None}) is None
    assert sig.disagreement("VALUE_WATCH", {"status": "EXECUTABLE", "yes_ask": 0.40}) is False


# --------------------------------------------------------------------------- the conductor cycle (offline)


@pytest.fixture()
def main_dir(tmp_path):
    """A minimal main checkout: one catalog game with V2 claims, its frozen V2 artifact and event id."""
    root = tmp_path / "main"
    (root / "data" / "live" / "games").mkdir(parents=True)
    (root / "data" / "football" / "2026").mkdir(parents=True)
    (root / "data" / "scripting" / "live" / "sift").mkdir(parents=True)
    (root / "data" / "scripting" / "live" / "frozen_v2").mkdir(parents=True)
    (root / "app" / "latest").mkdir(parents=True)
    src = ROOT / "data" / "scripting" / "live"
    key = "26OCT10LSUUK"
    for sub in ("sift", "frozen_v2"):
        (root / "data" / "scripting" / "live" / sub / f"{key}.json.gz").write_bytes(
            (src / sub / f"{key}.json.gz").read_bytes()
        )
    catalog = json.loads((ROOT / "data" / "live" / "cfb_market_catalog.json").read_text())
    game = next(g for g in catalog["games"] if g["game_key"] == key)
    (root / "data" / "live" / "cfb_market_catalog.json").write_text(
        json.dumps({"capture": catalog["capture"], "games": [game]})
    )
    detail = ROOT / "data" / "live" / game["markets_file"]
    (root / "data" / "live" / game["markets_file"]).parent.mkdir(parents=True, exist_ok=True)
    (root / "data" / "live" / game["markets_file"]).write_bytes(detail.read_bytes())
    sched = json.loads((ROOT / "data" / "football" / "2026" / "schedule.json").read_text())
    sched["events"] = [e for e in sched["events"] if e["id"] == "401856715"]
    (root / "data" / "football" / "2026" / "schedule.json").write_text(json.dumps(sched))
    (root / "data" / "football" / "2026" / "team_games.jsonl").write_text("")
    events = json.loads((ROOT / "app" / "latest" / "events.json").read_text())
    events["items"] = [e for e in events["items"] if e["source_ids"].get("kalshi_game_key") == key]
    (root / "app" / "latest" / "events.json").write_text(json.dumps(events))
    return root


def live_markets(lsu=0.76, uk=0.25):
    return [
        {
            "ticker": "KXNCAAFGAME-26OCT10LSUUK-LSU",
            "event_ticker": "KXNCAAFGAME-26OCT10LSUUK",
            "yes_sub_title": "LSU",
            "status": "active",
            "yes_ask_dollars": f"{lsu:.4f}",
            "no_ask_dollars": f"{1 - lsu + 0.01:.4f}",
        },
        {
            "ticker": "KXNCAAFGAME-26OCT10LSUUK-UK",
            "event_ticker": "KXNCAAFGAME-26OCT10LSUUK",
            "yes_sub_title": "Kentucky",
            "status": "active",
            "yes_ask_dollars": f"{uk:.4f}",
            "no_ask_dollars": f"{1 - uk + 0.01:.4f}",
        },
    ]


def test_conductor_cycle_captures_publishes_and_is_idempotent(main_dir, tmp_path):
    conductor = _conductor()
    store, ledger = tmp_path / "store", tmp_path / "ledger"
    (ledger / "2026").mkdir(parents=True)
    kickoff = "2026-10-10T23:00:00Z"
    now = at(120, kickoff)
    kwargs = dict(main_dir=main_dir, ledger_dir=ledger, store_dir=store, fetch=lambda: live_markets(), fetch_event=None)
    first = conductor.run_cycle(now=now, **kwargs)
    assert first["attempts"] == 1 and first["quotes"] == 2 and first["read_error"] is None
    again = conductor.run_cycle(now=now, **kwargs)
    assert again["attempts"] == 0  # same moment: nothing new
    doc = json.loads((store / "signals" / "cfb_research_signals.json").read_text())
    game = doc["games"][0]
    assert game["signal"] == "VALUE_WATCH" and game["claims"]["control"]["team"] == "LSU"
    assert game["market"]["price"] == {**game["market"]["price"], "status": "EXECUTABLE", "yes_ask": 0.76}
    assert game["market"]["team_code"] == "LSU" and game["event_id"].startswith("evt_")
    assert game["capture"]["status"] == CAPTURE_OK and game["capture"]["entry"]["price"] == 0.76
    assert game["historical"]["n"] == 404 and game["historical"]["central_50"] == [-4.0, 18.0]


def test_conductor_logs_a_failed_read_per_due_game(main_dir, tmp_path):
    conductor = _conductor()
    store, ledger = tmp_path / "store", tmp_path / "ledger"
    (ledger / "2026").mkdir(parents=True)

    def boom():
        raise ConnectionError("reset by peer")

    out = conductor.run_cycle(
        main_dir=main_dir, ledger_dir=ledger, store_dir=store, now=at(150, "2026-10-10T23:00:00Z"), fetch=boom
    )
    assert out["attempts"] == 1 and out["read_error"].startswith("ConnectionError")
    attempts = [json.loads(line) for line in (store / "capture" / "2026" / "attempts.jsonl").read_text().splitlines()]
    assert attempts[0]["outcome"] == cap.ATTEMPT_FAILED  # never "no market"
    doc = json.loads((store / "signals" / "cfb_research_signals.json").read_text())
    assert doc["sources"]["market_read"]["ok"] is False
    assert doc["games"][0]["market"]["price"]["source"] == "KALSHI_CATALOG"  # honest fallback, labelled


def test_watchdog_dispatches_for_a_game_without_final_pregame(main_dir, tmp_path):
    conductor = _conductor()
    store, ledger = tmp_path / "store", tmp_path / "ledger"
    (ledger / "2026").mkdir(parents=True)
    calls = []
    out = conductor.run_cycle(
        main_dir=main_dir,
        ledger_dir=ledger,
        store_dir=store,
        now=at(150, "2026-10-10T23:00:00Z"),
        fetch=lambda: live_markets(),
        dispatch=lambda: calls.append(1),
        busy=lambda: False,
    )
    assert out["watchdog"]["final_pregame"] == ["26OCT10LSUUK"] and calls == [1]
    conductor.run_cycle(
        main_dir=main_dir,
        ledger_dir=ledger,
        store_dir=store,
        now=at(140, "2026-10-10T23:00:00Z"),
        fetch=lambda: live_markets(),
        dispatch=lambda: calls.append(1),
        busy=lambda: False,
    )
    assert calls == [1]  # debounced
    row = ledger_row(
        game_key="26OCT10LSUUK",
        kickoff="2026-10-10T23:00:00Z",
        recorded=at(160, "2026-10-10T23:00:00Z"),
        event_id="401856715",
        tier="AWAY_CONTROL_MODERATE",
    )
    (ledger / "2026" / "publications_v2.jsonl").write_text(json.dumps(row) + "\n")
    out = conductor.run_cycle(
        main_dir=main_dir,
        ledger_dir=ledger,
        store_dir=store,
        now=at(80, "2026-10-10T23:00:00Z"),
        fetch=lambda: live_markets(),
        dispatch=lambda: calls.append(1),
        busy=lambda: False,
    )
    assert out["watchdog"]["final_pregame"] == []


def test_script_engine_cron_reaches_every_day():
    text = (ROOT / ".github" / "workflows" / "script-engine.yml").read_text()
    assert 'cron: "41 */2 * * *"' in text and "settle-v2" in text and "report-v2" in text
    conductor = (ROOT / ".github" / "workflows" / "cfb-research-conductor.yml").read_text()
    assert "gh workflow run cfb-research-conductor.yml" in conductor and "HEAD:$STORE_BRANCH" in conductor


# --------------------------------------------------------------------------- V2 settlement


def claims_artifact(c50=(8, 35), c80=(1, 48)):
    return {
        "content": {
            "claims": {
                "control": {
                    "historical_range": {
                        "median": 23,
                        "central_50": list(c50),
                        "central_80": list(c80),
                        "calibration_sha256": "626c",
                    }
                }
            }
        }
    }


def v1_artifact(plays_mu=66.0, total=50.0):
    return {
        "content": {
            "matchup_profile": {
                "adjustment": {
                    "league_baselines": {
                        "plays_per_game": {"mu": plays_mu},
                        "yards_per_play": {"mu": 5.6},
                        "success_rate": {"mu": 0.42},
                    }
                },
                "scoring_baseline": {"total_points": total},
            }
        }
    }


def test_v2_settlement_control_coverage_closeness_and_idempotence():
    row = ledger_row(
        tier="HOME_CONTROL_STRONG",
        closeness=True,
        pace="HIGH",
        scoring_environment={"level": "ELEVATED", "strengthened": True},
        defensive_suppression=True,
        disruption=[{"side": "home", "strength": "STRONG", "aligned_with_control": True}],
    )
    teams = [team("1", 27.0, 21.0, "home", plays=75.0, sacks=4.0, tfl=9.0), team("2", 21.0, 27.0, "away", plays=70.0)]
    done: set = set()
    rows = settle_rows([row], done, teams, lambda h: claims_artifact(), lambda h: v1_artifact())
    assert (
        len(rows) == 1 and settle_rows([row], done, teams, lambda h: claims_artifact(), lambda h: v1_artifact()) == []
    )
    r = rows[0]
    assert key_of(r) == key_of(row) and r["claims_artifact_hash"] == row["claims_artifact_hash"]
    c = r["control"]
    assert c["control_margin"] == 6 and c["won"] is True
    assert c["range"]["in_central_50"] is False and c["range"]["in_central_80"] is True
    assert r["closeness"] == {"abs_margin": 6, "within_3": False, "within_7": True, "within_one_score_8": True}
    assert r["pace"]["direction_held"] is True and r["pace"]["realized_total_plays"] == 145.0
    assert r["scoring_environment"]["realized_vs_baseline"] == "BELOW"
    assert r["disruption"][0]["mechanism_held"] is True
    report = build_report_v2([row], rows)
    assert report["control"]["HOME_CONTROL_STRONG"]["central_80_coverage"] == 1.0


def test_v2_settlement_makes_missing_inputs_explicit():
    row = ledger_row(tier="AWAY_CONTROL_MODERATE", pace="LOW", defensive_suppression=True)
    teams = [team("1", 20.0, 24.0, "home"), team("2", 24.0, 20.0, "away")]
    r = settle_rows([row], set(), teams, lambda h: None, lambda h: None)[0]
    assert r["control"]["won"] is True and r["control"]["range"]["status"] == "UNAVAILABLE"
    assert r["pace"]["status"] == "UNAVAILABLE" and r["defensive_suppression"]["status"] == "UNAVAILABLE"


def test_v2_settlement_never_reads_a_publication_row_or_an_unfinished_game():
    pub = ledger_row(kind="PUBLICATION")
    assert (
        settle_rows(
            [pub], set(), [team("1", 1.0, 0.0, "home"), team("2", 0.0, 1.0, "away")], lambda h: None, lambda h: None
        )
        == []
    )
    assert settle_rows([ledger_row()], set(), [], lambda h: None, lambda h: None) == []


def test_v2_environment_rule_matches_the_research_rule():
    from cfb_edge_finder.archetype_research.taxonomy import environment_labels as research_rule
    from cfb_edge_finder.scripting.realized import realized_features
    from cfb_edge_finder.scripting.realized_v2 import environment_labels as production_rule

    for plays, total, ypp in ((80.0, 70.0, 6.5), (60.0, 30.0, 4.0), (70.0, 45.0, 5.0)):
        home = team("1", total / 2 + 3, total / 2 - 3, "home", plays=plays)
        away = team("2", total / 2 - 3, total / 2 + 3, "away", plays=plays)
        home.box["total_yards"] = away.box["total_yards"] = ypp * plays
        f = realized_features(home, away)
        pre = {"league_norms": {"plays_per_game": 66.0, "yards_per_play": 5.6}, "baseline": {"total_points": 52.0}}
        assert production_rule(f, pre) == research_rule(f, pre)
