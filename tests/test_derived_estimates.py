"""Research-only derived estimates (research_packet/derived_estimates.py) and their audit integration.

Properties: no probability is invented (every number is a count from the reference class plus a stated interval); weak
or tail evidence is SHAPE_ONLY with the ceiling withheld; missing evidence is named, not rejected silently; matchup
uncertainty only WIDENS; derived estimates are never analyst evaluation or candidates; analysts supersede them; nothing
touches models or execution.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from cfb_edge_finder.research_packet import derived_estimates as de
from cfb_edge_finder.research_packet import history as hist
from cfb_edge_finder.research_packet import market_audit as ma

REPO = Path(__file__).resolve().parents[1]
GAME = "26NOV07AAABBB"


def make_history(n=400, seed=3):
    rnd = random.Random(seed)
    rows = []
    for i in range(n):
        quarters_h = [rnd.randint(0, 14) for _ in range(4)]
        quarters_a = [rnd.randint(0, 14) for _ in range(4)]
        rows.append(
            {
                "game_id": str(i),
                "kickoff_utc": f"2025-10-{1 + i % 28:02d}T{i % 24:02d}:00:00Z",
                "season": 2025,
                "pred_home": 30.0 + rnd.uniform(-1, 1),
                "pred_away": 20.0 + rnd.uniform(-1, 1),
                "pred_total": 50.0 + rnd.uniform(-1, 1),
                "actual_home": float(sum(quarters_h)),
                "actual_away": float(sum(quarters_a)),
                "home_periods": quarters_h,
                "away_periods": quarters_a,
            }
        )
    return rows


def packet(qb_home=None, inj_home=None):
    qb = {
        "confirmed_starter": None,
        "projected_starter": "A",
        "changed_in_last_3_games": False,
        "leader_differs_from_last_game": False,
        "game_log": [{"passer": "A", "attempts": 30}] * 4,
    }
    side = {"status": "OBSERVED", "named_entries": 0, "qb_flagged_uncertain": False}
    return {
        "football": {
            "baseline": {
                "home_points": 30.0,
                "away_points": 20.0,
                "home_margin": 10.0,
                "total_points": 50.0,
                "uncertainty_points": 6.0,
            }
        },
        "inputs": {"football_data_cutoff": "2026-01-01T00:00:00Z"},
        "qb": {"home": qb_home or qb, "away": dict(qb)},
        "availability": {"espn_core_injury_listing": {"home": inj_home or side, "away": dict(side)}},
    }


def contract(ticker, family, kind, period, line=None, side=None, outcome=None, ask=0.5):
    blk = {"ask": ask, "ask_size": 100.0, "exec": True, "all_in": ask + 0.01, "contracts_1u": 100, "order_fee_1u": 1.0}
    return {
        "ticker": ticker,
        "family": family,
        "kind": kind,
        "period": period,
        "line": line,
        "side": side,
        "outcome": outcome,
        "yes": blk,
        "no": dict(blk),
        "yes_means": "x",
        "book": "two_sided",
        "upd": "t",
    }


GAME_ROW = {"game_key": GAME, "football_history": "PRESENT", "kickoff": "2026-11-07T17:00:00Z", "flag_codes": []}
SKILL = {
    "keys": {
        "margin|game": {"label": "SUPPORTED"},
        "total|game": {"label": "SUPPORTED"},
        "team|game": {"label": "SUPPORTED"},
        "total|h1": {"label": "SHAPE_ONLY"},
    }
}
SD = {"margin|game": 12.0, "total|game": 14.0, "team|game": 9.0, "total|h1": 8.0}


def derive(contracts, pk=None, game_row=None, skill=None, hg=None, codes=None):
    return de.derive_game(
        game_row or GAME_ROW,
        contracts,
        pk if pk is not None else packet(),
        hg or make_history(),
        skill or SKILL,
        codes or {"home": "BBB", "away": "AAA"},
        SD,
    )


def by_side(entries, ticker, side):
    return next(e for e in entries if e["ticker"] == ticker and e["side"] == side)


# ------------------------------------------------------------------------------ unit behaviour


def test_wilson_interval_is_a_valid_widening_interval():
    lo, hi = de.wilson(30, 100)
    assert 0.0 < lo < 0.30 < hi < 1.0
    assert de.wilson(0, 0) == (0.0, 1.0)
    assert de.wilson(100, 100)[1] == pytest.approx(1.0) and de.wilson(100, 100)[0] < 1.0


def test_contract_quantity_and_special_parsing():
    assert de.contract_quantity(contract("T", "game_spread", "margin_over", "full_game", 3.5, "away"))["side"] == "away"
    assert de.contract_quantity(contract("T", "x", "win", "h1", outcome="tie")) == {
        "type": "win",
        "period": "h1",
        "outcome": "tie",
    }
    assert de.contract_quantity(contract("T", "x", "see_title", "full_game")) is None
    codes = {"home": "BBB", "away": "AAA"}
    wm = {"ticker": "KXNCAAFWINMARGIN-G-AAA7TO14", "family": "winning_margin"}
    assert de._special(wm, codes) == {
        "special": "winning_margin",
        "side": "away",
        "lo": 7,
        "hi": 14,
        "skill": "margin|game",
    }
    plus = de._special({"ticker": "KXNCAAFWINMARGIN-G-BBB15PLUS", "family": "winning_margin"}, codes)
    assert (plus["side"], plus["lo"], plus["hi"]) == ("home", 15, None)
    assert de._special({"ticker": "KXNCAAFOT-G-1", "family": "overtime"}, codes)["special"] == "overtime"
    j = de._special({"ticker": "KXNCAAF1HFT-G-TIEAAA", "family": "unknown"}, codes)
    assert (j["h1"], j["game"]) == ("tie", "away")
    assert de._special({"ticker": "KXNCAAF1HFT-G-ZZZAAA", "family": "unknown"}, codes) is None


def test_qb_assessment_widens_by_matchup_and_never_rejects():
    low = de.qb_assessment(packet(), "home")
    assert low["level"] == "LOW" and low["multiplier"] == 1.25 and "QB_NOT_CONFIRMED" in low["reasons"]
    shaky = packet(
        qb_home={
            "confirmed_starter": None,
            "projected_starter": "B",
            "changed_in_last_3_games": True,
            "leader_differs_from_last_game": True,
            "game_log": [{"passer": "A", "attempts": 30}, {"passer": "B", "attempts": 10}] * 2,
        }
    )
    hi = de.qb_assessment(shaky, "home")
    assert hi["multiplier"] > low["multiplier"] and hi["level"] in ("ELEVATED", "HIGH")
    assert de.qb_assessment({}, "home")["multiplier"] <= de.POLICY["cap"]
    absent = de.qb_assessment(packet(inj_home={"status": "OBSERVED", "named_entries": 2}), "home")
    assert "LISTED_ABSENCES" in absent["reasons"] and absent["multiplier"] > low["multiplier"]


def test_supported_near_center_is_derived_with_range_and_complementary_no():
    c = [contract("KXNCAAFTOTAL-G-50", "game_total", "total_over", "full_game", 49.5)]
    entries, info = derive(c)
    yes, no = by_side(entries, c[0]["ticker"], "YES"), by_side(entries, c[0]["ticker"], "NO")
    assert yes["status"] == no["status"] == de.DERIVED_ESTIMATE and yes["source"] == de.SOURCE
    py, pn = yes["probability"], no["probability"]
    assert 0 < py["low"] <= py["point"] <= py["high"] < 1
    assert pn["low"] == pytest.approx(1 - py["high"], abs=1e-4) and pn["high"] == pytest.approx(1 - py["low"], abs=1e-4)
    assert info["reference_class"]["n"] >= hist.MIN_COMPARABLES and info["qb_assessment"]["home"]["level"] == "LOW"
    chosen, _ = hist.reference_class(hist.eligible_reference(make_history(), "2026-01-01T00:00:00Z", None), 10.0, 50.0)
    n_over = sum(g["actual_home"] + g["actual_away"] > 49.5 for g in chosen)
    assert py["point"] == pytest.approx(n_over / len(chosen), abs=1e-4)  # a count, not an invention


def test_tail_rung_and_untested_periods_are_shape_only_with_ceiling_withheld():
    tail = contract("KXNCAAFTOTAL-G-20", "game_total", "total_over", "full_game", 19.5)
    weak = contract("KXNCAAF1HTOTAL-G-25", "first_half_total", "total_over", "h1", 24.5)
    entries, _ = derive([tail, weak])
    t = by_side(entries, tail["ticker"], "NO")
    assert t["status"] == de.SHAPE_ONLY_REFERENCE and t["skill_label"] == "SUPPORTED_BUT_OUTSIDE_TESTED_LINE_RANGE"
    assert "extrapolation" in t["bet_up_to_withheld"]
    w = by_side(entries, weak["ticker"], "YES")
    assert w["status"] == de.SHAPE_ONLY_REFERENCE and w["skill_label"] == "SHAPE_ONLY" and w["probability"]


def test_missing_evidence_is_named_and_never_dropped():
    props = [
        contract("KXNCAAFTEAMRECTD-G-A1", "team_stat_prop", "see_title", "full_game"),
        contract("KXNCAAFFIRSTTDTEAM-G-A", "touchdown_scorer", "see_title", "full_game"),
    ]
    entries, _ = derive(props)
    assert len(entries) == 4 and {e["status"] for e in entries} == {de.INSUFFICIENT_EVIDENCE}
    assert all(e["missing_evidence"] and "probability" not in e for e in entries)
    c = [contract("KXNCAAFTOTAL-G-50", "game_total", "total_over", "full_game", 49.5)]
    absent, _ = derive(c, game_row={**GAME_ROW, "football_history": "ABSENT"}, pk=None)
    assert {e["status"] for e in absent} == {de.INSUFFICIENT_EVIDENCE} and "absent" in absent[0]["reason"]
    thin, _ = derive(c, hg=make_history(n=20))
    assert {e["status"] for e in thin} == {de.INSUFFICIENT_EVIDENCE}


def test_uncertainty_only_widens():
    c = [
        contract(f"KXNCAAFSPREAD-G-{k}", "game_spread", "margin_over", "full_game", k + 0.5, "home") for k in (0, 6, 12)
    ]
    base, _ = derive(c)
    shaky = packet(
        qb_home={
            "confirmed_starter": None,
            "projected_starter": None,
            "changed_in_last_3_games": True,
            "leader_differs_from_last_game": True,
            "game_log": [],
        },
        inj_home={"status": "OBSERVED", "named_entries": 3, "qb_flagged_uncertain": True},
    )
    wide, info = derive(c, pk=shaky)
    assert info["qb_assessment"]["home"]["level"] == "HIGH"
    for a, b in zip(base, wide, strict=True):
        assert (
            b["probability"]["low"] <= a["probability"]["low"] and b["probability"]["high"] >= a["probability"]["high"]
        )
        assert a["probability"]["point"] == b["probability"]["point"]


def test_ladders_are_monotone_in_the_line():
    c = [contract(f"KXNCAAFTOTAL-G-{k}", "game_total", "total_over", "full_game", k + 0.5) for k in range(44, 58, 2)]
    entries, _ = derive(c)
    pts = [by_side(entries, x["ticker"], "YES")["probability"] for x in c]
    assert all(
        a["point"] >= b["point"] and a["low"] >= b["low"] and a["high"] >= b["high"]
        for a, b in zip(pts, pts[1:], strict=False)
    )


# ------------------------------------------------------------------------------ audit integration


def audit_side(**kw):
    s = {
        "game_key": GAME,
        "ticker": "KXNCAAFTOTAL-G-50",
        "side": "YES",
        "status": ma.NOT_EVALUATED,
        "all_in": 0.41,
        "reason_code": None,
        "reason": None,
        "probability": None,
        "margin_low_vs_all_in": None,
        "bet_up_to": None,
        "thesis": None,
        "missing_evidence": None,
        "evaluation_source": None,
        "price_screen": None,
        "skill_label": None,
        "reference_n": None,
        "bet_up_to_withheld": None,
        "derived_prior": None,
        "family": "game_total",
        "kickoff": "t",
        "evidence_gaps": [],
    }
    return s | kw


def derived_entry(status, **kw):
    e = {
        "game_key": GAME,
        "ticker": "KXNCAAFTOTAL-G-50",
        "side": "YES",
        "source": de.SOURCE,
        "status": status,
        "reason": "historical reference class n=100",
    }
    return e | kw


def test_apply_derived_ceiling_from_lower_bound_and_shape_only_withheld():
    s = audit_side()
    assert (
        ma.apply_derived(
            [s], [derived_entry(de.DERIVED_ESTIMATE, probability={"low": 0.55, "point": 0.6, "high": 0.7})]
        )
        == []
    )
    assert s["status"] == ma.DERIVED_ESTIMATE and s["evaluation_source"] == ma.SRC_DERIVED
    assert s["price_screen"] == "LOWER_BOUND_CLEARS_BREAKEVEN" and s["bet_up_to"] is not None and s["bet_up_to"] >= 0.5
    assert s["probability"]["tier"] == "HISTORICAL_ESTIMATE" and s["probability"]["calibrated"] is False
    s2 = audit_side()
    ma.apply_derived(
        [s2],
        [
            derived_entry(
                de.SHAPE_ONLY_REFERENCE, probability={"low": 0.3, "high": 0.6}, bet_up_to_withheld="SHAPE_ONLY"
            )
        ],
    )
    assert s2["status"] == ma.SHAPE_ONLY_REFERENCE and s2["bet_up_to"] is None and s2["bet_up_to_withheld"]


def test_generated_entries_cannot_claim_candidate_or_analyst_source():
    s = audit_side()
    d = ma.apply_derived([s], [derived_entry(ma.EVALUATED_CANDIDATE, probability={"low": 0.9, "high": 0.95})])
    assert [x.code for x in d] == ["BAD_DERIVED_STATUS"] and s["status"] == ma.NOT_EVALUATED
    d = ma.apply_derived(
        [s], [derived_entry(de.DERIVED_ESTIMATE, source="ANALYST", probability={"low": 0.5, "high": 0.6})]
    )
    assert [x.code for x in d] == ["BAD_DERIVED_STATUS"]
    d = ma.apply_derived([s], [derived_entry(de.INSUFFICIENT_EVIDENCE, probability={"low": 0.5, "high": 0.6})])
    assert [x.code for x in d] == ["BAD_INSUFFICIENT_EVIDENCE"]


def test_derived_never_reaches_complete_and_analyst_supersedes_it():
    s = audit_side()
    ma.apply_derived([s], [derived_entry(de.DERIVED_ESTIMATE, probability={"low": 0.55, "point": 0.6, "high": 0.7})])
    assert ma.classify([s], [])["verdict"] == ma.VERDICT_GAPS and not ma.classify([s], [])["evaluation_complete"]
    ma.apply_evaluations(
        [s],
        [
            {
                "game_key": GAME,
                "ticker": s["ticker"],
                "side": "YES",
                "status": ma.EVALUATED_PASS,
                "reason": "analyst reads the matchup as slower than the baseline",
                "probability": {"low": 0.3, "point": 0.35, "high": 0.4},
            }
        ],
    )
    assert s["evaluation_source"] == ma.SRC_ANALYST and s["derived_prior"]["status"] == ma.DERIVED_ESTIMATE
    assert s["price_screen"] is None and s["probability"]["tier"] == "ANALYST_JUDGMENT"
    assert ma.classify([s], [])["verdict"] == ma.VERDICT_COMPLETE
    # an analyst insufficient-evidence entry must not inherit the generated probability or ceiling
    s3 = audit_side()
    ma.apply_derived([s3], [derived_entry(de.DERIVED_ESTIMATE, probability={"low": 0.55, "high": 0.7})])
    ma.apply_evaluations(
        [s3],
        [
            {
                "game_key": GAME,
                "ticker": s3["ticker"],
                "side": "YES",
                "status": ma.INSUFFICIENT_EVIDENCE,
                "reason": "QB situation unresolved",
                "missing_evidence": ["confirmed QB"],
            }
        ],
    )
    assert s3["probability"] is None and s3["bet_up_to"] is None


def test_verify_report_rejects_derived_posing_as_complete():
    base = {
        "verdict": {"verdict": ma.VERDICT_COMPLETE, "evaluation_complete": True, "structural_defects": 0},
        "totals": {
            "sides_recorded": 2,
            "sides_expected": 2,
            "contracts_listed": 1,
            "derived_estimate_sides": 1,
            "shape_only_reference_sides": 0,
            "analyst_evaluated_sides": 1,
            "status_counts": {ma.DERIVED_ESTIMATE: 1, ma.EVALUATED_PASS: 1},
        },
        "coverage_by_family": {"f": {"sides": 2}},
    }
    assert any("stand in for analyst" in p for p in ma.verify_report(base))


# ------------------------------------------------------------------------------ the real slate


def _live_slate():
    """The committed slate with the most contracts: live data rolls forward as games finish, so never hard-code a date."""
    best = None
    for path in sorted((REPO / "data/research_packets/slates").glob("*.json")):
        rows = json.loads(path.read_text())["games"]
        n = sum(g.get("contract_count", 0) for g in rows)
        if rows and (best is None or n > best[1]):
            best = (path.stem, n)
    if best is None:
        pytest.skip("no committed slate")
    return best[0]


@pytest.fixture(scope="module")
def real():
    inv = ma.load_inventory(REPO, _live_slate(), None)
    sides = ma.build_sides(inv)
    games = hist.load_games(REPO / "data/research_packets/reference/history_games.jsonl.gz")
    skill = json.loads((REPO / "data/research_packets/reference/derived_skill.json").read_text())
    doc, ctx = de.generate(REPO, inv, games, skill)
    defects = ma.apply_derived(sides, doc["entries"])
    return inv, sides, doc, ctx, defects


def test_real_slate_every_priceable_side_has_an_honest_status(real):
    inv, sides, doc, ctx, defects = real
    assert defects == [] and len(doc["entries"]) == len(sides)
    assert not [s for s in sides if s["status"] == ma.NOT_EVALUATED]
    assert {s["status"] for s in sides} <= {
        ma.UNPRICEABLE,
        ma.DERIVED_ESTIMATE,
        ma.SHAPE_ONLY_REFERENCE,
        ma.INSUFFICIENT_EVIDENCE,
    }
    assert not [s for s in sides if s["evaluation_source"] == ma.SRC_ANALYST]
    assert ma.classify(sides, [])["verdict"] == ma.VERDICT_GAPS  # never COMPLETE on generated estimates
    for s in sides:
        if s["status"] == ma.DERIVED_ESTIMATE:
            assert s["probability"] and s["probability"]["low"] <= s["probability"]["high"]
            assert s["bet_up_to"] is None or s["bet_up_to"] >= 0.01
        if s["status"] == ma.SHAPE_ONLY_REFERENCE:
            assert s["bet_up_to"] is None and s["bet_up_to_withheld"]
        if s["status"] == ma.INSUFFICIENT_EVIDENCE:
            assert s["missing_evidence"] and s["probability"] is None


def test_real_slate_point_estimates_equal_the_packets_own_reference_frequency(real):
    inv, sides, *_ = real
    ref = {(g, c["ticker"]): c["ref_freq"] for g, cs in inv.contracts.items() for c in cs if c.get("ref_freq")}
    checked = 0
    for s in sides:
        if s["side"] == "YES" and s["status"] in (ma.DERIVED_ESTIMATE, ma.SHAPE_ONLY_REFERENCE):
            r = ref.get((s["game_key"], s["ticker"]))
            if r is not None:
                assert s["probability"]["point"] == pytest.approx(min(0.995, max(0.005, r["yes"])), abs=0.0021)
                assert s["reference_n"] == r["n"]
                checked += 1
    assert checked >= 1


def test_real_slate_yes_ladders_are_monotone(real):
    inv, sides, *_ = real
    prob = {(s["game_key"], s["ticker"]): s["probability"] for s in sides if s["side"] == "YES" and s["probability"]}
    checked = 0
    for gk in inv.contracts:
        import json as _j

        doc = _j.loads((REPO / f"data/research_packets/games/{gk}/markets.json").read_text())
        for lad in doc["ladders"].values():
            pts = [prob.get((gk, t)) for t in lad["tickers"]]
            if all(pts) and len(pts) > 1 and lad["family"].endswith(("spread", "total", "team_total")):
                for a, b in zip(pts, pts[1:], strict=False):
                    assert a["point"] >= b["point"] - 1e-9, (gk, lad["family"])
                    checked += 1
    assert checked >= 1


def test_real_slate_families_report_every_side_including_props(real):
    inv, sides, *_ = real
    rep = ma.build_report(inv, sides, [])
    for fam_name, f in rep["coverage_by_family"].items():
        assert f["not_evaluated"] == 0, fam_name
    cov = rep["coverage_by_family"]
    if "winning_margin" in cov:  # not tested for skill: shape only whenever a reference class exists
        assert cov["winning_margin"]["derived_estimate"] == 0
    if "touchdown_scorer" in cov:  # no drive-order data anywhere in the repository
        assert cov["touchdown_scorer"]["derived_estimate"] == cov["touchdown_scorer"]["shape_only_reference"] == 0
    if "first_half_total" in cov:  # untested period: shape only
        assert cov["first_half_total"]["derived_estimate"] == 0
    leads = rep["derived_leads_for_analyst_review"]
    assert leads["count"] == sum(leads["by_family"].values())
    assert all(r["bet_up_to"] is not None and r["margin_low_vs_all_in"] > 0 for r in leads["rows"])
    assert ma.verify_report(rep) == []


def test_skill_table_labels_follow_the_stated_rule():
    sk = json.loads((REPO / "data/research_packets/reference/derived_skill.json").read_text())
    assert sk["schema_version"] == de.SKILL_SCHEMA and "no market lines" in sk["not_an_edge_claim"]
    for k, v in sk["keys"].items():
        assert v["label"] == (de.SUPPORTED if v["ci95"][0] > 0 else de.SHAPE_ONLY), k


def test_committed_derived_report_is_self_consistent_and_honest():
    rep = json.loads((REPO / "docs/reports/CFB_MARKET_AUDIT_2026-11-07_early_derived.json").read_text())
    t = rep["totals"]
    assert (t["games"], t["contracts_listed"], t["sides_expected"], t["sides_recorded"]) == (42, 3992, 7984, 7984)
    assert t["analyst_evaluated_sides"] == 0 and t["not_evaluated_sides"] == 0
    assert sum(t["status_counts"].values()) == 7984 and rep["verdict"]["evaluation_complete"] is False
    assert ma.verify_report(rep) == [] and rep["defects"] == []


def test_generator_imports_no_execution_model_or_decision_code():
    src = Path(de.__file__).read_text()
    for forbidden in ("execution", "decision", "modeling", "kalshi_client", "place_order"):
        assert f"cfb_edge_finder.{forbidden}" not in src
