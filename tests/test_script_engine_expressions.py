"""Script/market compatibility, survival and labels -- and the vocabulary they may never use."""

from __future__ import annotations

import json

import pytest
from execution_fakes import CAPTURED_AT, catalog_dir, market, standard_markets
from script_engine_fakes import availability, identity, synthetic_season

from cfb_edge_finder.execution.disposition import DispositionConfig
from cfb_edge_finder.execution.slate import build_packet, load_catalog
from cfb_edge_finder.scripting.expressions import (
    AGGRESSIVE,
    BEST_EXPRESSION,
    CONTRADICTED_LABEL,
    LABELS,
    MARKET_DISAGREEMENT,
    MULTI_SCRIPT,
    NARROW_SCRIPT,
    SCORING_BAND_UNCALIBRATED,
    SCRIPT_ALIGNED,
    SCRIPT_DEPENDENT,
    annotate,
)
from cfb_edge_finder.scripting.football import FootballPacket, build_content
from cfb_edge_finder.scripting.freeze import freeze
from cfb_edge_finder.scripting.market_map import (
    ACTIVE,
    CONTRADICTED,
    NEUTRAL,
    PARTIAL,
    QUARANTINED,
    RESEARCH_UNCALIBRATED,
    SUPPORTED,
    UNMAPPABLE,
    classify,
    classify_against,
    map_game,
)
from cfb_edge_finder.scripting.publish import sift_payload
from cfb_edge_finder.scripting.scripts import ARCHETYPE_DEFINITION, CALIBRATED, UNCALIBRATED_DESCRIPTIVE

CAPTURED = CAPTURED_AT


def _ladder_markets() -> list[dict]:
    """The standard fixture plus a home spread ladder out to an extreme rung."""
    rows = standard_markets()
    for line, ask in ((3.5, 0.52), (10.5, 0.31), (24.5, 0.07)):
        rows.append(
            market(
                f"KXNCAAFSPREAD-26SEP19LSUMISS-MISSX{int(line)}",
                family="game_spread",
                title=f"Ole Miss wins by over {line} points",
                yes_sub_title=f"Ole Miss wins by over {line} points",
                floor_strike=line,
                is_alternate_line=True,
                yes_ask=ask,
                no_ask=round(1.02 - ask, 2),
            )
        )
    return rows


@pytest.fixture(scope="module")
def mapped(tmp_path_factory):
    # Football: home team F00 strongly better. Catalog title "LSU at Ole Miss": Ole Miss is HOME.
    packet = FootballPacket(
        identity=identity(),
        game_key="26SEP19LSUMISS",
        rows=tuple(synthetic_season()),
        availability=availability(),
        identity_check={"status": "PASS", "orientation_swapped": False},
        freshness={"status": "FRESH"},
    )
    content = build_content(packet)
    envelope = freeze(content, generated_at="2026-10-23T12:00:00Z")
    directory = catalog_dir(tmp_path_factory.mktemp("cat"), _ladder_markets())
    index, details = load_catalog(directory)
    entry = index["games"][0]
    market_packet = build_packet(
        entry, details[entry["game_key"]], DispositionConfig(as_of=CAPTURED, captured_at=CAPTURED), set(), "UTC"
    )
    mm = map_game(envelope, market_packet["contracts"], orientation_swapped=False, mapped_at="2026-10-23T12:00:00Z")
    annotate(envelope["content"], mm)
    return envelope, mm, market_packet


def _expr(mm, ticker, side="YES"):
    return next(e for e in mm["expressions"] if e["ticker"] == ticker and e["side"] == side)


def _status(expr, role):
    return next(c["status"] for c in expr["compatibility"] if c["role"] == role)


def test_classification_is_interval_logic_on_the_script_band():
    assert classify(1, float("inf"), [7, 24]) == (SUPPORTED, 1.0)
    assert classify(4, float("inf"), [7, 24]) == (SUPPORTED, 1.0)
    status, coverage = classify(11, float("inf"), [7, 24])
    assert status == PARTIAL and coverage == pytest.approx(14 / 18, abs=1e-3)
    assert classify(25, float("inf"), [7, 24]) == (CONTRADICTED, 0.0)
    assert classify(1, float("inf"), None) == (NEUTRAL, None)


def test_home_control_supports_the_moneyline_and_a_moderate_spread_not_the_extreme_rung(mapped):
    envelope, mm, _ = mapped
    assert envelope["content"]["game_scripts"]["scripts"][0]["archetype"] == "HOME_CONTROL"
    ml = _expr(mm, "KXNCAAFGAME-26SEP19LSUMISS-MISS")
    moderate = _expr(mm, "KXNCAAFSPREAD-26SEP19LSUMISS-MISSX3")
    stretch = _expr(mm, "KXNCAAFSPREAD-26SEP19LSUMISS-MISSX10")
    extreme = _expr(mm, "KXNCAAFSPREAD-26SEP19LSUMISS-MISSX24")
    assert _status(ml, "PRIMARY") == SUPPORTED
    assert _status(moderate, "PRIMARY") == SUPPORTED
    assert _status(stretch, "PRIMARY") == PARTIAL
    assert _status(extreme, "PRIMARY") == CONTRADICTED
    # -24.5 is not treated like the moneyline.
    assert extreme["script_survival"]["supported"] < ml["script_survival"]["supported"]


def test_the_no_side_is_the_exact_complement(mapped):
    _, mm, _ = mapped
    yes = _expr(mm, "KXNCAAFSPREAD-26SEP19LSUMISS-MISSX24", "YES")
    no = _expr(mm, "KXNCAAFSPREAD-26SEP19LSUMISS-MISSX24", "NO")
    flip = {SUPPORTED: CONTRADICTED, CONTRADICTED: SUPPORTED, PARTIAL: PARTIAL, NEUTRAL: NEUTRAL}
    for a, b in zip(yes["compatibility"], no["compatibility"], strict=True):
        assert flip[a["status"]] == b["status"]


def test_an_uncalibrated_scoring_band_is_research_not_support(mapped):
    """The trailing team's team-total under sits inside the control script's
    points band -- but that band is drawn around the uncalibrated scoring
    baseline, so the relation is kept as research and never counted."""
    envelope, mm, _ = mapped
    primary = envelope["content"]["game_scripts"]["scripts"][0]
    assert primary["outcome_shape"]["band_authority"]["home_margin"] == ARCHETYPE_DEFINITION
    assert primary["outcome_shape"]["band_authority"]["away_points"] == UNCALIBRATED_DESCRIPTIVE
    away_tt = [e for e in mm["expressions"] if e["kind"] == "team_total" and e["team"] == "away" and e["side"] == "NO"]
    assert away_tt
    research = [c for e in away_tt for c in e["compatibility"] if c["role"] == "PRIMARY"]
    assert {c["status"] for c in research} == {RESEARCH_UNCALIBRATED}
    assert any(c["research"]["band_relation"] in (SUPPORTED, PARTIAL) for c in research)
    for e in away_tt:
        assert e["market_authority"] == QUARANTINED
        assert e["labels"] == [SCORING_BAND_UNCALIBRATED]
        assert e["script_survival"]["supported"] == 0 and e["script_survival"]["weighted_score"] == 0


def test_no_scoring_contract_can_earn_a_positive_script_label(mapped):
    _, mm, _ = mapped
    scoring = [e for e in mm["expressions"] if e["kind"] in ("total", "team_total") and e["unmappable_reason"] is None]
    assert len(scoring) >= 6
    positive = {MULTI_SCRIPT, BEST_EXPRESSION, SCRIPT_ALIGNED, SCRIPT_DEPENDENT, NARROW_SCRIPT, AGGRESSIVE}
    for e in scoring:
        assert not positive & set(e["labels"]), e["expression_id"]
        assert all(c["status"] in (RESEARCH_UNCALIBRATED, NEUTRAL) for c in e["compatibility"])
    survivors = {s["expression_id"] for s in mm["survivors"]}
    assert not survivors & {e["expression_id"] for e in scoring}
    assert not any(t["thesis"].startswith(("total:", "team_total:")) for t in mm["theses"])
    # Margin markets keep their full authority.
    assert any(MULTI_SCRIPT in e["labels"] or BEST_EXPRESSION in e["labels"] for e in mm["expressions"])
    margin = [e for e in mm["expressions"] if e["kind"] in ("moneyline", "spread") and e["unmappable_reason"] is None]
    assert margin and all(e["market_authority"] == ACTIVE for e in margin)


def test_a_band_missing_its_authority_is_treated_as_uncalibrated():
    from cfb_edge_finder.execution.card_review import WinSet

    ws = WinSet(variable="full_game:total_points", low=40, high=float("inf"))
    shape = {"bands": {"total_points": [50, 70], "home_margin": [7, 24]}}
    assert classify_against(ws, shape)["status"] == RESEARCH_UNCALIBRATED
    calibrated = {**shape, "band_authority": {"total_points": CALIBRATED}}
    assert classify_against(ws, calibrated) == {"status": SUPPORTED, "coverage": 1.0}


def test_every_eligible_contract_is_accounted_for(mapped):
    _, mm, market_packet = mapped
    cov = mm["coverage"]
    assert cov["contracts_eligible"] == len(market_packet["contracts"])
    assert cov["expressions_evaluated"] == 2 * cov["contracts_eligible"]
    assert cov["expressions_mapped"] + cov["expressions_unmappable"] == cov["expressions_evaluated"]
    assert cov["balanced"]
    for e in mm["expressions"]:
        if e["unmappable_reason"]:
            assert all(c["status"] == UNMAPPABLE for c in e["compatibility"])
    first_half = [e for e in mm["expressions"] if e["period"] == "first_half"]
    assert first_half and all("full-game" in e["unmappable_reason"] for e in first_half)


def test_labels_never_claim_a_price_edge(mapped):
    _, mm, _ = mapped
    blob = json.dumps(mm).lower()
    for banned in ("+ev", "positive_ev", "bet_up_to", "high_probability_expression", 'edge"'):
        assert banned not in blob
    assert "HIGH_PROBABILITY_EXPRESSION" not in LABELS
    for e in mm["expressions"]:
        assert set(e.get("labels") or []) <= set(LABELS)
        assert e["pricing"]["source"] is None
        assert e["pricing"]["fair_probability"] is None and e["pricing"]["expected_value"] is None


def test_one_best_expression_per_thesis_and_aggressive_rungs(mapped):
    _, mm, _ = mapped
    best = [e for e in mm["expressions"] if BEST_EXPRESSION in e["labels"]]
    theses = [e["thesis"] for e in best]
    assert len(theses) == len(set(theses))
    home_side = next(e for e in best if e["thesis"] == "side:home")
    assert MULTI_SCRIPT in home_side["labels"]
    stretch = _expr(mm, "KXNCAAFSPREAD-26SEP19LSUMISS-MISSX10")
    assert AGGRESSIVE in stretch["labels"] or CONTRADICTED_LABEL in stretch["labels"]
    assert CONTRADICTED_LABEL in _expr(mm, "KXNCAAFSPREAD-26SEP19LSUMISS-MISSX24")["labels"]


def test_the_ladder_states_what_each_rung_additionally_requires(mapped):
    _, mm, _ = mapped
    block = next(t for t in mm["theses"] if t["thesis"] == "side:home")
    rungs = {r["expression_id"]: r for r in block["ladder"]}
    core = next(r for r in block["ladder"] if r["is_core"])
    assert core["expression_id"] == block["core_expression"]
    extreme = rungs.get("KXNCAAFSPREAD-26SEP19LSUMISS-MISSX24:YES")
    assert extreme is not None
    assert extreme["relation_to_core"] in ("nested_tail_extension", "correlated_independent_cash_path")
    assert extreme["scripts_lost_vs_core"]


def test_market_disagreement_flags_without_touching_the_artifact(tmp_path):
    packet = FootballPacket(
        identity=identity(),
        game_key="26SEP19LSUMISS",
        rows=tuple(synthetic_season()),
        availability=availability(),
        identity_check={"status": "PASS", "orientation_swapped": False},
        freshness={"status": "FRESH"},
    )
    envelope = freeze(build_content(packet), generated_at="2026-10-23T12:00:00Z")
    before = json.dumps(envelope["content"], sort_keys=True)
    rows = standard_markets()
    for m in rows:
        if m["market_ticker"].endswith("-MISS"):
            m.update(yes_ask=0.22, yes_bid=0.21, no_ask=0.79, no_bid=0.78)
    directory = catalog_dir(tmp_path, rows)
    index, details = load_catalog(directory)
    entry = index["games"][0]
    mp = build_packet(
        entry, details[entry["game_key"]], DispositionConfig(as_of=CAPTURED, captured_at=CAPTURED), set(), "UTC"
    )
    mm = map_game(envelope, mp["contracts"], orientation_swapped=False, mapped_at="2026-10-23T12:00:00Z")
    annotate(envelope["content"], mm)
    assert mm["market_disagreement"] is not None
    assert any(MARKET_DISAGREEMENT in e["labels"] for e in mm["expressions"])
    assert json.dumps(envelope["content"], sort_keys=True) == before


def test_orientation_swap_translates_team_slots_by_identity(mapped):
    envelope, _, market_packet = mapped
    swapped = map_game(envelope, market_packet["contracts"], orientation_swapped=True, mapped_at="2026-10-23T12:00:00Z")
    straight = {
        e["expression_id"]: e["team"]
        for e in map_game(
            envelope, market_packet["contracts"], orientation_swapped=False, mapped_at="2026-10-23T12:00:00Z"
        )["expressions"]
    }
    for e in swapped["expressions"]:
        if straight[e["expression_id"]] in ("home", "away"):
            assert e["team"] != straight[e["expression_id"]]


def test_the_sift_payload_fits_the_event_budget_and_keeps_every_metric_field(mapped):
    envelope, mm, _ = mapped
    payload = sift_payload({"football": envelope, "market_map": mm, "prices_captured_at": CAPTURED.isoformat()})
    size = len(json.dumps(payload, separators=(",", ":")))
    assert size < 100_000, size
    cols = payload["matchup_profile"]["metric_columns"]
    for required in (
        "raw",
        "adjusted",
        "rank",
        "universe_size",
        "direction",
        "games",
        "source",
        "observed_at",
        "quality",
    ):
        assert required in cols
    team = payload["matchup_profile"]["teams"]["home"]
    assert team["season"] == 2026 and team["window"]["type"] == "season_to_date"
    assert all(len(row) == len(cols) for row in team["metrics"].values())
    gen = payload["script_generation"]
    assert gen["market_blind"] is True and gen["artifact_hash"] == envelope["artifact_hash"]
    assert "price" not in json.dumps(payload["script_market_map"]["expressions"])


def test_identical_support_prefers_the_cheaper_rung_never_less_support():
    from cfb_edge_finder.scripting.expressions import _sort_key

    def expr(eid, weighted, cost, at_least):
        return {
            "expression_id": eid,
            "script_survival": {"weighted_score": weighted},
            "price": {"cost_per_contract": cost},
            "wins_when": {"at_least": at_least, "at_most": None},
        }

    wide, tight, weaker = expr("ML", 1.7, 0.80, 1), expr("-6.5", 1.7, 0.55, 7), expr("-10.5", 1.2, 0.30, 11)
    ordered = sorted([wide, weaker, tight], key=_sort_key)
    assert [e["expression_id"] for e in ordered] == ["-6.5", "ML", "-10.5"]


def test_a_total_band_that_excludes_the_markets_coin_flip_line_is_flagged():
    from cfb_edge_finder.scripting.expressions import market_disagreement

    content = {
        "teams": {"home": {"name": "A"}, "away": {"name": "B"}},
        "game_scripts": {
            "scripts": [
                {
                    "role": "PRIMARY",
                    "archetype": "COMPETITIVE_SHOOTOUT",
                    "outcome_shape": {"winner_lean": "NONE", "bands": {"total_points": [73, 97]}},
                }
            ]
        },
    }
    totals = [
        {"kind": "total", "period": "full_game", "side": "YES", "line": line, "team": "none", "price": {"entry": ask}}
        for line, ask in ((51.5, 0.62), (55.5, 0.49), (60.5, 0.38))
    ]
    flag = market_disagreement(content, totals, False)
    assert flag is not None and flag["flags"][0]["kind"] == "TOTAL"
    assert flag["flags"][0]["market_median_total"] == 55.5 and flag["flags"][0]["thesis"] == "total:over"
    # An observation about an uncalibrated band, never evidence of value.
    assert flag["flags"][0]["evidence_of_value"] is False and "not evidence of value" in flag["note"]
    totals_inside = [dict(t, line=t["line"] + 20) for t in totals]
    assert market_disagreement(content, totals_inside, False) is None
