"""Game-handicap pricing wired into the market audit (research_packet/handicap_pricing.py).

Properties: one handicap prices every expressible side coherently (fee-inclusive, conservative lower bound); an analyst
handicap is analyst judgment (overridable, never an approval); a seeded draft is never an analyst handicap; what the
closed-form pricer cannot express carries a specific gap and no invented probability; nothing touches execution.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cfb_edge_finder.execution.evaluator import fair_from_distribution
from cfb_edge_finder.execution.handicap import PeriodDistribution
from cfb_edge_finder.research_packet import derived_estimates as de
from cfb_edge_finder.research_packet import handicap_pricing as hp
from cfb_edge_finder.research_packet import market_audit as ma

REPO = Path(__file__).resolve().parents[1]
GAME = "26NOV07AAABBB"
CODES = {"home": "BBB", "away": "AAA"}

REGION = {
    "margin_points": 3.0,
    "total_points": 4.0,
    "sd_scale_low": 0.9,
    "sd_scale_high": 1.1,
    "correlation_delta": 0.05,
}


def dist(region=True, home=30.0, away=20.0):
    p = {"home_mean": home, "away_mean": away, "home_sd": 10.0, "away_sd": 9.0, "correlation": 0.1}
    if region:
        p["uncertainty"] = REGION
    return PeriodDistribution.parse("full_game", p)


def side(ticker, kind, period="full_game", line=None, cs=None, family="game_total", outcome=None, which="YES", ask=0.5):
    return {
        "game_key": GAME,
        "ticker": ticker,
        "side": which,
        "kind": kind,
        "period": period,
        "line": line,
        "contract_side": cs,
        "contract_outcome": outcome,
        "family": family,
        "status": ma.NOT_EVALUATED,
        "all_in": ask + 0.01,
        "ask": ask,
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
        "handicap_check": None,
        "kickoff": "t",
        "evidence_gaps": [],
    }


def sem(kind, **kw):
    return {"kind": kind, "period": "full_game", "team": kw.pop("team", "none"), "comparator": "greater", **kw}


# ------------------------------------------------------------------------------ contract semantics


def test_semantics_cover_the_pricer_families_and_name_gaps_for_the_rest():
    s, why = hp.semantics_for(side("T", "margin_over", line=3.5, cs="away", family="game_spread"), CODES)
    assert why is None and s["kind"] == "spread" and s["team"] == "away" and s["line"] == 3.5
    assert hp.semantics_for(side("T", "win", cs="home"), CODES)[0]["kind"] == "moneyline"
    assert hp.semantics_for(side("T", "team_points_over", line=20.5, cs="home"), CODES)[0]["kind"] == "team_total"
    assert hp.semantics_for(side("T", "total_over", period="h1", line=24.5), CODES)[0]["period"] == "first_half"
    wm = hp.semantics_for(side("KXNCAAFWINMARGIN-G-AAA7TO14", "see_title", family="winning_margin"), CODES)[0]
    assert (wm["kind"], wm["team"], wm["line"], wm["cap"]) == ("margin_band", "away", 7.0, 14.0)
    plus = hp.semantics_for(side("KXNCAAFWINMARGIN-G-BBB15PLUS", "see_title", family="winning_margin"), CODES)[0]
    assert plus["cap"] is None and plus["line"] == 15.0
    gaps = {
        "tie": side("T", "win", outcome="tie", cs=None),
        "ot": side("KXNCAAFOT-G-1", "see_title", family="overtime"),
        "joint": side("KXNCAAF1HFT-G-TIEAAA", "see_title", family="unknown"),
        "prop": side("KXNCAAFTEAMRECTD-G-A1", "see_title", family="team_stat_prop"),
    }
    reasons = {k: hp.semantics_for(v, CODES) for k, v in gaps.items()}
    assert all(r[0] is None and len(r[1]) > 20 for r in reasons.values())
    assert "tie" in reasons["tie"][1] and "overtime" in reasons["ot"][1] and "correlation" in reasons["joint"][1]


# ------------------------------------------------------------------------------ pricing


def test_range_matches_the_existing_pricer_and_widens_only_with_a_stated_region():
    d = dist()
    s = sem("total", line=49.5)
    lo, pt, hi = hp.yes_probability_range(s, d)
    assert lo < pt < hi
    assert pt == pytest.approx(fair_from_distribution(s, d).value)  # exactly the existing pricer's base case
    point_only = hp.yes_probability_range(s, dist(region=False))
    assert point_only[0] == point_only[1] == point_only[2]
    assert point_only[1] == pytest.approx(pt)


def test_one_handicap_prices_every_family_coherently():
    d = dist()
    totals = [hp.yes_probability_range(sem("total", line=x), d) for x in (40.5, 45.5, 50.5, 55.5)]
    assert all(a[1] > b[1] and a[0] > b[0] and a[2] > b[2] for a, b in zip(totals, totals[1:], strict=False))
    ml_home = hp.yes_probability_range(sem("moneyline", team="home"), d)[1]
    ml_away = hp.yes_probability_range(sem("moneyline", team="away"), d)[1]
    assert ml_home > 0.5 > ml_away
    assert 0.9 < ml_home + ml_away <= 1.0  # the shortfall is the (unpriced) tie mass
    spreads = [hp.yes_probability_range(sem("spread", team="home", line=x), d)[1] for x in (-3.5, 2.5, 9.5, 16.5)]
    assert spreads == sorted(spreads, reverse=True)
    band = hp.yes_probability_range(
        sem("margin_band", team="home", line=7.0, comparator="greater_or_equal", cap=14.0), d
    )
    assert 0 < band[1] < 0.5


def test_candidate_only_when_conservative_lower_bound_clears_the_fee_inclusive_price():
    cheap = side("KXNCAAFTOTAL-G-40", "total_over", line=40.5, ask=0.50)  # model P(over 40.5) is far above 0.51
    dear = side("KXNCAAFTOTAL-G-60", "total_over", line=60.5, ask=0.50)  # model P(over 60.5) is far below
    entries, info = hp.price_sides(
        [cheap, dear], {GAME: CODES}, {GAME: {"full_game": dist()}}, source=hp.SRC_ANALYST_HANDICAP
    )
    by = {e["ticker"]: e for e in entries}
    assert by[cheap["ticker"]]["status"] == ma.EVALUATED_CANDIDATE and by[dear["ticker"]]["status"] == ma.EVALUATED_PASS
    assert by[dear["ticker"]]["reason_code"] == "HANDICAP_LOWER_BOUND_BELOW_BREAKEVEN"
    assert info["priced_sides"] == 2 and info["gaps"] == {}
    # the same handicap with NO stated region is a point estimate: it may pass, never a candidate, no ceiling
    e2, _ = hp.price_sides(
        [cheap], {GAME: CODES}, {GAME: {"full_game": dist(region=False)}}, source=hp.SRC_ANALYST_HANDICAP
    )
    assert e2[0]["status"] == ma.EVALUATED_PASS and e2[0]["reason_code"] == "POINT_ESTIMATE_NO_UNCERTAINTY"


def test_unsupported_sides_are_reported_not_priced():
    sides = [side("KXNCAAFOT-G-1", "see_title", family="overtime"), side("T-h1", "total_over", period="h1", line=24.5)]
    entries, info = hp.price_sides(sides, {GAME: CODES}, {GAME: {"full_game": dist()}}, source=hp.SRC_ANALYST_HANDICAP)
    assert entries == []
    reasons = info["gaps"][GAME]
    assert any("overtime" in r for r in reasons) and any("no score distribution for first_half" in r for r in reasons)


# ------------------------------------------------------------------------------ audit integration


def test_analyst_handicap_sides_are_analyst_evaluations_with_ceiling_and_overridable():
    s = side("KXNCAAFTOTAL-G-40", "total_over", line=40.5, ask=0.50)
    entries, _ = hp.price_sides([s], {GAME: CODES}, {GAME: {"full_game": dist()}}, source=hp.SRC_ANALYST_HANDICAP)
    assert ma.apply_evaluations([s], entries) == []
    assert s["status"] == ma.EVALUATED_CANDIDATE and s["evaluation_source"] == ma.SRC_ANALYST_HANDICAP
    assert s["probability"]["tier"] == "ANALYST_JUDGMENT" and s["probability"]["calibrated"] is False
    assert s["bet_up_to"] is not None and s["bet_up_to"] >= 0.5  # fee-inclusive, from the LOWER bound
    assert ma.classify([s], [])["verdict"] == ma.VERDICT_COMPLETE  # analyst-owned, with a stated range
    ma.apply_evaluations(
        [s],
        [
            {
                "game_key": GAME,
                "ticker": s["ticker"],
                "side": "YES",
                "status": ma.EVALUATED_PASS,
                "reason": "late weather read overrides the handicap",
                "source": "ANALYST",
                "probability": {"low": 0.30, "point": 0.4, "high": 0.5},
            }
        ],
    )
    assert s["evaluation_source"] == ma.SRC_ANALYST and s["status"] == ma.EVALUATED_PASS


def test_point_estimate_earns_no_ceiling_and_cannot_be_a_manual_candidate():
    s = side("KXNCAAFTOTAL-G-40", "total_over", line=40.5)
    entries, _ = hp.price_sides(
        [s], {GAME: CODES}, {GAME: {"full_game": dist(region=False)}}, source=hp.SRC_ANALYST_HANDICAP
    )
    ma.apply_evaluations([s], entries)
    assert s["status"] == ma.EVALUATED_PASS and s["bet_up_to"] is None
    assert s["bet_up_to_withheld"] == "POINT_ESTIMATE_NO_UNCERTAINTY"
    s2 = side("T2", "total_over", line=40.5)
    d = ma.apply_evaluations(
        [s2],
        [
            {
                "game_key": GAME,
                "ticker": "T2",
                "side": "YES",
                "status": ma.EVALUATED_CANDIDATE,
                "reason": "a point estimate is not a range",
                "probability": {"low": 0.8, "high": 0.8},
            }
        ],
    )
    assert [x.code for x in d] == ["CANDIDATE_POINT_ESTIMATE"]


def test_handicap_file_validation_refuses_drafts_unsigned_and_unevidenced():
    good_payload = {
        "thesis": "BBB controls tempo",
        "opposing_case": "AAA's QB returns",
        "period_distributions": {"full_game": dist().as_dict()},
    }
    base = {
        "game_key": GAME,
        "analyst": "A. Analyst",
        "handicapped_at": "2026-11-07T12:00:00Z",
        "evidence": ["ESPN depth chart 2026-11-07T11:50Z"],
        "payload": good_payload,
    }

    def run(h):
        return hp.parse_handicaps({"schema_version": hp.HANDICAPS_SCHEMA, "handicaps": [h]})

    dists, meta, rej = run(base)
    assert GAME in dists and meta[GAME]["analyst"] == "A. Analyst" and rej == []
    cases = {
        "HANDICAP_IS_DRAFT": {**base, "draft": True},
        "HANDICAP_UNSIGNED": {**base, "analyst": ""},
        "HANDICAP_WITHOUT_EVIDENCE": {**base, "evidence": []},
        "HANDICAP_WITHOUT_THESIS": {**base, "payload": {**good_payload, "opposing_case": ""}},
        "HANDICAP_INVALID": {
            **base,
            "payload": {**good_payload, "period_distributions": {"full_game": {"home_mean": 1}}},
        },
    }
    for code, h in cases.items():
        d2, _m, r2 = run(h)
        assert d2 == {} and [x["code"] for x in r2] == [code], code
    d3, _m, r3 = hp.parse_handicaps({"schema_version": hp.HANDICAPS_SCHEMA, "handicaps": [base, base]})
    assert d3 == {} and {x["code"] for x in r3} == {"DUPLICATE_HANDICAP"}
    assert hp.parse_handicaps({"schema_version": "x"})[2][0]["code"] == "HANDICAPS_SCHEMA"


# ------------------------------------------------------------------------------ historical seed


def history(n=300):
    import random

    rnd = random.Random(5)
    rows = []
    for i in range(n):
        qh = [rnd.randint(0, 14) for _ in range(4)]
        qa = [rnd.randint(0, 10) for _ in range(4)]
        rows.append(
            {
                "game_id": str(i),
                "kickoff_utc": f"2025-10-{1 + i % 28:02d}T00:00:00Z",
                "home_periods": qh,
                "away_periods": qa,
                "actual_home": float(sum(qh)),
                "actual_away": float(sum(qa)),
            }
        )
    return rows


QB = {"home": {"multiplier": 1.25}, "away": {"multiplier": 1.75}}


def test_seed_is_a_data_derived_draft_that_cannot_pass_as_an_analyst_handicap():
    rows = history()
    seed = hp.seed_handicap(rows, QB, 6.0)
    assert set(seed) == set(hp.PERIOD_NAME.values())
    g = seed["full_game"]
    assert g["home_mean"] == pytest.approx(sum(r["actual_home"] for r in rows) / len(rows), abs=0.001)
    assert (
        g["uncertainty"]["margin_points"] > 0
        and g["uncertainty"]["sd_scale_low"] < 1 < g["uncertainty"]["sd_scale_high"]
    )
    assert (
        seed["first_quarter"]["uncertainty"]["margin_points"] < g["uncertainty"]["margin_points"]
    )  # scaled by period share
    assert hp.seed_handicap(rows[:20], QB, 6.0) is None  # too thin: nothing is seeded
    # wider matchup uncertainty can only widen the region
    narrow = hp.seed_handicap(rows, {"home": {"multiplier": 1.0}, "away": {"multiplier": 1.0}}, 6.0)
    assert narrow["full_game"]["uncertainty"]["margin_points"] < g["uncertainty"]["margin_points"]
    dists = hp.seeded_distributions(seed)
    assert all(isinstance(v, PeriodDistribution) for v in dists.values())
    _d, _m, rej = hp.parse_handicaps(
        {
            "schema_version": hp.HANDICAPS_SCHEMA,
            "handicaps": [
                {
                    "game_key": GAME,
                    "draft": True,
                    "analyst": "x",
                    "handicapped_at": "t",
                    "evidence": ["e"],
                    "payload": {"period_distributions": seed, "thesis": "t", "opposing_case": "o"},
                }
            ],
        }
    )
    assert rej[0]["code"] == "HANDICAP_IS_DRAFT"


def test_seeded_cross_check_never_changes_a_status():
    s = side("KXNCAAFTOTAL-G-50", "total_over", line=49.5, ask=0.40)
    ma.apply_derived(
        [s],
        [
            {
                "game_key": GAME,
                "ticker": s["ticker"],
                "side": "YES",
                "source": de.SOURCE,
                "status": de.DERIVED_ESTIMATE,
                "reason": "historical reference class n=100",
                "probability": {"low": 0.50, "point": 0.58, "high": 0.66},
            }
        ],
    )
    before = (s["status"], s["evaluation_source"], s["bet_up_to"])
    check = [
        {
            "game_key": GAME,
            "ticker": s["ticker"],
            "side": "YES",
            "probability": {"low": 0.48, "point": 0.57, "high": 0.65},
        }
    ]
    ma.apply_handicap_checks([s], check, hp.SRC_SEED)
    assert (s["status"], s["evaluation_source"], s["bet_up_to"]) == before
    hc = s["handicap_check"]
    assert hc["agrees_with_own_estimate"] and hc["price_screen_agrees"] and hc["point_gap"] == pytest.approx(0.01)
    assert hc["corroborated_bet_up_to"] == min(hc["bet_up_to"], s["bet_up_to"])
    far = [{**check[0], "probability": {"low": 0.10, "point": 0.15, "high": 0.20}}]
    ma.apply_handicap_checks([s], far, hp.SRC_SEED)
    assert (
        s["handicap_check"]["agrees_with_own_estimate"] is False
        and s["handicap_check"]["corroborated_bet_up_to"] is None
    )


# ------------------------------------------------------------------------------ the live slate and the CLI


def _live_slate():
    best = None
    for path in sorted((REPO / "data/research_packets/slates").glob("*.json")):
        rows = json.loads(path.read_text())["games"]
        n = sum(g.get("contract_count", 0) for g in rows)
        if rows and (best is None or n > best[1]):
            best = (path.stem, n)
    if best is None:
        pytest.skip("no committed slate")
    return best[0]


def _cli():
    import importlib.util

    spec = importlib.util.spec_from_file_location("audit_cli2", REPO / "scripts/audit_cfb_market_evaluation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_prices_a_signed_analyst_handicap_and_cross_checks_with_seeds(tmp_path):
    slate = _live_slate()
    inv = ma.load_inventory(REPO, slate, None)
    key = next(g["game_key"] for g in inv.games)
    handicap = {
        "schema_version": hp.HANDICAPS_SCHEMA,
        "handicaps": [
            {
                "game_key": key,
                "analyst": "Test Analyst",
                "handicapped_at": "2026-11-07T12:00:00Z",
                "evidence": ["synthetic test evidence"],
                "payload": {
                    "thesis": "synthetic thesis",
                    "opposing_case": "synthetic opposing case",
                    "period_distributions": {
                        "full_game": dist().as_dict(),
                        "first_half": {
                            "home_mean": 14.0,
                            "away_mean": 10.0,
                            "home_sd": 7.0,
                            "away_sd": 6.5,
                            "correlation": 0.1,
                            "uncertainty": REGION,
                        },
                    },
                },
            }
        ],
    }
    hfile = tmp_path / "h.json"
    hfile.write_text(json.dumps(handicap))
    out = tmp_path / "r.json"
    seeds = tmp_path / "seeds.json"
    rc = _cli().main(
        [
            "--slate-date",
            slate,
            "--derive",
            "--seed-check",
            "--handicaps",
            str(hfile),
            "--json",
            str(out),
            "--seed-handicaps-out",
            str(seeds),
            "--md",
            str(tmp_path / "r.md"),
        ]
    )
    assert rc == 0
    rep = json.loads(out.read_text())
    hi = rep["handicap_integration"]
    assert list(hi["analyst_handicap_games"]) == [key] and hi["analyst_handicap_priced_sides"] > 0
    assert hi["analyst_handicap_rejections"] == [] and hi["seeded_cross_check_sides"] >= 0
    assert rep["totals"]["analyst_evaluated_sides"] == hi["analyst_handicap_priced_sides"]
    assert (
        ma.verify_report(rep) == [] and rep["verdict"]["evaluation_complete"] is False
    )  # the other games are unevaluated or generated
    # unsupported sides in the handicapped game keep their generated status and are named, not priced
    assert hi["analyst_handicap_unpriced_by_reason"]
    seed_doc = json.loads(seeds.read_text())
    assert seed_doc["schema_version"] == "cfb_game_handicap_seeds/1.0.0" and all(x["draft"] for x in seed_doc["seeds"])
    assert "family_opportunity_comparison" in rep and "direction_group_comparison" in rep


def test_seed_check_matches_a_run_without_it_status_for_status(tmp_path):
    slate = _live_slate()
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    cli = _cli()
    assert cli.main(["--slate-date", slate, "--derive", "--json", str(a)]) == 0
    assert cli.main(["--slate-date", slate, "--derive", "--seed-check", "--json", str(b)]) == 0
    ra, rb = json.loads(a.read_text()), json.loads(b.read_text())
    assert ra["totals"]["status_counts"] == rb["totals"]["status_counts"]
    assert ra["derived_leads_for_analyst_review"]["count"] == rb["derived_leads_for_analyst_review"]["count"]


def test_committed_integrated_report_is_self_consistent_and_honest():
    rep = json.loads((REPO / "docs/reports/CFB_MARKET_AUDIT_2026-11-07_early_integrated.json").read_text())
    t = rep["totals"]
    assert (t["games"], t["contracts_listed"], t["sides_expected"], t["sides_recorded"]) == (42, 3992, 7984, 7984)
    assert t["not_evaluated_sides"] == 0 and sum(t["status_counts"].values()) == 7984
    assert t["analyst_evaluated_sides"] == 0 and rep["verdict"]["evaluation_complete"] is False
    assert ma.verify_report(rep) == [] and rep["defects"] == []
    hi = rep["handicap_integration"]
    assert hi["analyst_handicap_priced_sides"] == 0 and hi["seeded_cross_check_sides"] > 0
    assert rep["family_opportunity_comparison"]["by_family"]


def test_handicap_pricing_imports_only_pure_pricing_and_no_order_or_approval_code():
    src = (REPO / "src/cfb_edge_finder/research_packet/handicap_pricing.py").read_text()
    imported = set(re.findall(r"^from (cfb_edge_finder[\w.]*) import", src, re.M))
    assert imported <= {
        "cfb_edge_finder.execution.evaluator",
        "cfb_edge_finder.execution.handicap",
        "cfb_edge_finder.execution.uncertainty",
        "cfb_edge_finder.research_packet",
    }
    for forbidden in ("place_order", "approve", "qualif", "kalshi_client", "decision", "sift"):
        assert forbidden not in " ".join(imported).lower()
