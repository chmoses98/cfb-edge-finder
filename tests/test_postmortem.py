"""The postmortem, and the line between accounting and analysis.

*** THE TWO ASSERTIONS EVERYTHING ELSE SUPPORTS ***
1. `test_every_monetary_figure_is_identical_with_and_without_recommendations`.
   Recommendations add CUTS. If one of them could move a total, the postmortem
   would be describing the analysis rather than the money.
2. `test_the_matcher_returns_the_wager_rows_untouched`. A matcher that
   "corrected" an execution price to the recommended one would produce a
   ledger in which every bet filled exactly where the analysis said it should,
   and every number computed from it would be a description of the analysis.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cfb_edge_finder.accounting import postmortem as pm
from cfb_edge_finder.accounting.recommendation_link import (
    DEFAULT_MATCH_WINDOW_SECONDS,
    MatchState,
    Recommendation,
    match_executions,
    recommendations_from_artifact,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/cfb_postmortem.py"


def wager(key, *, ticker="KXNCAAFSPREAD-26SEP19LSUMISS-LSU10", side="YES",
          price=0.53, stake=10.6, fees=0.14, executed="2026-09-19T15:30:00Z"):
    return {
        "source_bet_key": key,
        "wager_id": f"routed-{key}",
        "import_batch_id": "kalshi-router-v1",
        "entry_method": "IMPORTED_RECEIPT",
        "game_date": "2026-09-19",
        "market_ticker": ticker,
        "side": side,
        "executed_at": executed,
        "contracts": 20.0,
        "execution_price": price,
        "stake": stake,
        "fees_paid": fees,
        "fees_are_estimated": False,
        "venue": "kalshi",
        "season": 2026,
    }


def settlement(key, *, result="WON", gross=20.0, net=9.26):
    return {
        "source_bet_key": key,
        "settlement_id": f"stl-{key}",
        "market_ticker": "KXNCAAFSPREAD-26SEP19LSUMISS-LSU10",
        "side": "YES",
        "settlement_status": "SETTLED",
        "settled_at": "2026-09-20T02:00:00Z",
        "result": result,
        "gross_return": gross,
        "net_profit_loss": net,
        "refusals": [],
        "venue": "kalshi",
    }


def recommendation(key="r1", *, ticker="KXNCAAFSPREAD-26SEP19LSUMISS-LSU10",
                   side="YES", entry=0.53, bet_up_to=0.57,
                   quoted="2026-09-19T12:00:00Z", robustness="robust_positive_ev",
                   confidence="medium", ceiling="high", edge=0.06, stake=None):
    return Recommendation(
        recommendation_id=key,
        game_key="26SEP19LSUMISS",
        ticker=ticker,
        side=side,
        quote_timestamp=quoted,
        quoted_entry=entry,
        bet_up_to=bet_up_to,
        fair_probability=0.61,
        fee_adjusted_edge=edge,
        robustness=robustness,
        confidence=confidence,
        data_quality_ceiling=ceiling,
        correlation_group="full_game:margin / home",
        recommended_stake=stake,
    )


# ======================================================== the hard line


def test_every_monetary_figure_is_identical_with_and_without_recommendations():
    """*** THE ONE THAT MATTERS ***

    Recommendations add cuts. A recommendation that could move a total would
    make the postmortem a description of the analysis rather than of the
    money."""
    wagers = [wager("a"), wager("b", ticker="KXNCAAFTOTAL-26SEP19LSUMISS-51")]
    settlements = [settlement("a"), settlement("b", result="LOST", gross=0.0, net=-10.74)]
    recs = [recommendation("r1"), recommendation("r2", ticker="KXNCAAFTOTAL-26SEP19LSUMISS-51")]

    bare = pm.build(wagers, settlements, 2026)
    attributed = pm.build(
        wagers,
        settlements,
        2026,
        matches=match_executions(wagers, recs),
        recommendations=recs,
    )

    for field in (
        "wagers", "settled", "won", "lost", "staked", "fees_paid",
        "gross_return", "net_profit_loss", "roi",
    ):
        assert bare.overall.as_dict()[field] == attributed.overall.as_dict()[field], field

    assert bare.as_dict()["by_market_family"] == attributed.as_dict()["by_market_family"]
    assert bare.as_dict()["by_period"] == attributed.as_dict()["by_period"]
    assert bare.as_dict()["by_game"] == attributed.as_dict()["by_game"]

    # ...and the cuts that DID change are only the attribution ones.
    assert bare.as_dict()["by_robustness_tier"] != attributed.as_dict()["by_robustness_tier"]


def test_the_matcher_returns_the_wager_rows_untouched():
    """A matcher that adjusted an execution price would make every bet look
    like it filled where the analysis said."""
    wagers = [wager("a", price=0.61)]
    before = copy.deepcopy(wagers)
    result = match_executions(wagers, [recommendation("r1", entry=0.53, bet_up_to=0.55)])
    assert wagers == before, "the matcher mutated the execution evidence"
    assert result.matches[0].executed_price == 0.61
    assert result.matches[0].recommended_price == 0.53


def test_a_match_record_carries_no_money_of_its_own():
    """It points at a wager; the money is read from the ledger row."""
    result = match_executions([wager("a")], [recommendation("r1")])
    match = result.matches[0]
    assert match.source_bet_key == "a"
    for banned in ("net_profit_loss", "gross_return", "fees_paid", "contracts"):
        assert banned not in match.as_dict()


# =========================================================== matching


def test_a_recommendation_that_came_first_at_the_right_price_matches():
    result = match_executions([wager("a")], [recommendation("r1")])
    assert result.counts == {MatchState.RECOMMENDED_AND_EXECUTED.value: 1}


def test_a_recommendation_published_after_the_bet_cannot_have_informed_it():
    """Otherwise every bet is attributed to whatever was published later."""
    result = match_executions(
        [wager("a", executed="2026-09-19T11:00:00Z")],
        [recommendation("r1", quoted="2026-09-19T12:00:00Z")],
    )
    assert result.counts == {
        MatchState.EXECUTED_NOT_RECOMMENDED.value: 1,
        MatchState.RECOMMENDED_NOT_EXECUTED.value: 1,
    }


def test_a_recommendation_far_outside_the_window_does_not_match():
    result = match_executions(
        [wager("a", executed="2026-09-25T15:30:00Z")],
        [recommendation("r1", quoted="2026-09-19T12:00:00Z")],
        window_seconds=DEFAULT_MATCH_WINDOW_SECONDS,
    )
    assert MatchState.EXECUTED_NOT_RECOMMENDED.value in result.counts


def test_a_different_side_of_the_same_market_is_not_the_same_bet():
    result = match_executions([wager("a", side="NO")], [recommendation("r1", side="YES")])
    assert MatchState.EXECUTED_NOT_RECOMMENDED.value in result.counts


def test_a_fill_above_the_bet_up_to_price_is_named_as_such():
    result = match_executions(
        [wager("a", price=0.62)], [recommendation("r1", entry=0.53, bet_up_to=0.57)]
    )
    assert result.counts == {MatchState.EXECUTED_ABOVE_BET_UP_TO.value: 1}
    assert "said to stop at" in result.matches[0].reason


def test_a_fill_outside_the_price_tolerance_is_named_too():
    result = match_executions(
        [wager("a", price=0.44)],
        [recommendation("r1", entry=0.53, bet_up_to=0.99)],
        price_tolerance=0.02,
    )
    assert result.counts == {MatchState.EXECUTED_ABOVE_BET_UP_TO.value: 1}
    assert "tolerance" in result.matches[0].reason


def test_a_stake_far_from_the_recommended_one_is_a_sizing_difference():
    result = match_executions(
        [wager("a", stake=100.0)], [recommendation("r1", stake=10.0)]
    )
    assert result.counts == {MatchState.EXECUTED_DIFFERENT_SIZE.value: 1}


def test_two_eligible_recommendations_are_ambiguous_and_stay_unresolved():
    """Picking the nearer one by time would attribute a bet to an analysis
    that may not have produced it."""
    result = match_executions(
        [wager("a")],
        [recommendation("r1"), recommendation("r2", quoted="2026-09-19T13:00:00Z")],
    )
    assert result.counts[MatchState.AMBIGUOUS_MATCH.value] == 1
    # Neither recommendation is consumed by an ambiguity.
    assert result.counts[MatchState.RECOMMENDED_NOT_EXECUTED.value] == 2


def test_a_recommendation_nobody_acted_on_is_reported():
    """The half a wager ledger cannot see."""
    result = match_executions([], [recommendation("r1")])
    assert result.counts == {MatchState.RECOMMENDED_NOT_EXECUTED.value: 1}


def test_recommendations_read_from_an_artifact_get_deterministic_ids():
    artifact = {
        "batch": "early_b1",
        "generated_at": "2026-09-19T12:00:00Z",
        "games": {
            "26SEP19LSUMISS": {
                "handicap_confidence_effective": "medium",
                "factual_data_quality": {"confidence_ceiling": "high"},
            }
        },
        "candidates": [
            {
                "game_key": "26SEP19LSUMISS",
                "market": "KXNCAAFSPREAD-26SEP19LSUMISS-LSU10",
                "side": "YES",
                "kalshi_executable_price": 0.53,
                "bet_up_to_price": 0.57,
                "fair_probability": 0.61,
                "fee_adjusted_edge": 0.06,
                "robustness": "robust_positive_ev",
                "correlation_group": "full_game:margin / home",
                "stake_placeholder": None,
            }
        ],
    }
    first = recommendations_from_artifact(artifact)
    second = recommendations_from_artifact(artifact)
    assert [r.recommendation_id for r in first] == [r.recommendation_id for r in second]
    assert first[0].confidence == "medium"
    assert first[0].data_quality_ceiling == "high"
    assert first[0].robustness == "robust_positive_ev"


# ========================================================= the report


def test_an_unsettled_wager_is_never_a_loss():
    report = pm.build([wager("a"), wager("b")], [settlement("a")], 2026)
    assert report.overall.settled == 1
    assert report.overall.lost == 0
    assert not report.is_final


def test_no_headline_total_is_stated_while_any_wager_is_unestablished():
    report = pm.build([wager("a"), wager("b")], [settlement("a")], 2026)
    document = report.as_dict()
    assert document["overall"]["net_profit_loss"] is None
    assert document["overall"]["roi"] is None
    assert "PARTIAL" in document["completeness_note"]
    assert "PARTIAL" in pm.render(report)


def test_a_complete_report_states_its_totals_and_its_denominators():
    wagers = [wager("a"), wager("b")]
    settlements = [settlement("a"), settlement("b", result="LOST", gross=0.0, net=-10.74)]
    report = pm.build(wagers, settlements, 2026, unit=10.0)
    document = report.as_dict()
    assert report.is_final
    assert document["overall"]["net_profit_loss"] == pytest.approx(9.26 - 10.74, abs=1e-6)
    assert document["overall"]["roi"] == pytest.approx(
        (9.26 - 10.74) / (10.6 * 2), abs=1e-6
    )
    assert document["unit_result"] == pytest.approx((9.26 - 10.74) / 10.0, abs=1e-6)
    assert "divided by" in document["denominators"]["roi"]


def test_no_unit_result_is_stated_without_an_explicit_unit():
    """A unit inferred from the average stake is a number that never existed,
    and it would be derived from the very bets it is meant to measure."""
    report = pm.build([wager("a")], [settlement("a")], 2026)
    assert report.unit_result is None
    assert "no unit was supplied" in pm.render(report)


def test_the_market_family_and_period_cuts_come_from_the_series_ticker():
    wagers = [
        wager("a", ticker="KXNCAAFSPREAD-26SEP19LSUMISS-LSU10"),
        wager("b", ticker="KXNCAAF1HTOTAL-26SEP19LSUMISS-24"),
    ]
    report = pm.build(wagers, [settlement("a"), settlement("b")], 2026)
    families = {row["label"] for row in report.as_dict()["by_market_family"]}
    assert "game_spread" in families
    assert "first_half_total" in families
    periods = {row["label"] for row in report.as_dict()["by_period"]}
    assert {"full_game", "first_half"} <= periods


def test_an_unreadable_ticker_is_unknown_rather_than_guessed():
    assert pm.market_family_of("SOMETHING-ELSE-ENTIRELY") == ("unknown", "unknown")
    assert pm.market_family_of(None) == ("unknown", "unknown")
    assert pm.game_key_of("no-dashes") == "unknown"


def test_a_thin_cut_says_it_is_thin_rather_than_reading_like_a_finding():
    report = pm.build([wager("a")], [settlement("a")], 2026)
    row = report.as_dict()["by_market_family"][0]
    assert "too few for this cut to mean anything" in row["reading"]


def test_correlated_same_game_positions_are_grouped():
    wagers = [
        wager("a", ticker="KXNCAAFSPREAD-26SEP19LSUMISS-LSU10"),
        wager("b", ticker="KXNCAAFSPREAD-26SEP19LSUMISS-LSU65"),
        wager("c", ticker="KXNCAAFSPREAD-26SEP19OTHERGAME-AAA1"),
    ]
    report = pm.build(wagers, [], 2026)
    groups = report.as_dict()["correlated_exposure"]
    assert len(groups) == 1
    assert groups[0]["contract_count"] == 2
    assert "26SEP19LSUMISS" in groups[0]["group"]


def test_a_losing_robust_bet_is_not_called_a_handicap_error():
    """The categorisation rule that matters most: an outcome is not evidence
    about a decision."""
    wagers = [wager(f"w{i}") for i in range(4)]
    settlements = [settlement(f"w{i}", result="LOST", gross=0.0, net=-10.74) for i in range(4)]
    recs = [recommendation(f"r{i}") for i in range(4)]
    # Each recommendation must match a distinct wager, so give them distinct
    # markets rather than four identical ones (which would be ambiguous).
    wagers = [
        wager(f"w{i}", ticker=f"KXNCAAFSPREAD-26SEP19LSUMISS-LSU{i}") for i in range(4)
    ]
    settlements = [
        settlement(f"w{i}", result="LOST", gross=0.0, net=-10.74) for i in range(4)
    ]
    recs = [
        recommendation(f"r{i}", ticker=f"KXNCAAFSPREAD-26SEP19LSUMISS-LSU{i}")
        for i in range(4)
    ]
    report = pm.build(
        wagers, settlements, 2026,
        matches=match_executions(wagers, recs), recommendations=recs,
    )
    categories = report.as_dict()["issue_categories"]
    # Four losing robust bets is below the tier-reading bar, so not even a
    # prompt is raised -- and a prompt is all that would ever be raised.
    assert categories["HANDICAP_ERROR_PROMPTS"] == []
    assert categories["EXECUTION_ERROR"] == []
    rendered = pm.render(report)
    assert "NORMAL VARIANCE" in rendered
    assert "A losing bet is not evidence of a mistake" in rendered


def test_a_losing_tier_raises_a_prompt_and_says_it_is_not_a_verdict():
    wagers = [
        wager(f"w{i}", ticker=f"KXNCAAFSPREAD-26SEP19LSUMISS-LSU{i}") for i in range(10)
    ]
    settlements = [
        settlement(f"w{i}", result="LOST", gross=0.0, net=-10.74) for i in range(10)
    ]
    recs = [
        recommendation(f"r{i}", ticker=f"KXNCAAFSPREAD-26SEP19LSUMISS-LSU{i}")
        for i in range(10)
    ]
    report = pm.build(
        wagers, settlements, 2026,
        matches=match_executions(wagers, recs), recommendations=recs,
    )
    prompts = report.as_dict()["issue_categories"]["HANDICAP_ERROR_PROMPTS"]
    assert prompts
    assert "PROMPT, NOT A VERDICT" in prompts[0]["category"]
    assert "expected behaviour of a bet that was never certain" in prompts[0]["note"]


def test_an_execution_above_the_ceiling_is_an_execution_error_without_the_outcome():
    wagers = [wager("a", price=0.70)]
    recs = [recommendation("r1", entry=0.53, bet_up_to=0.57)]
    # NO settlement at all: the categorisation must not need one.
    report = pm.build(
        wagers, [], 2026, matches=match_executions(wagers, recs), recommendations=recs
    )
    issues = report.as_dict()["issue_categories"]["EXECUTION_ERROR"]
    assert len(issues) == 1
    assert "visible WITHOUT the outcome" in issues[0]["note"]


def test_a_bet_on_a_thin_data_game_is_a_process_note_not_a_verdict():
    wagers = [wager("a")]
    recs = [recommendation("r1", ceiling="insufficient")]
    report = pm.build(
        wagers, [], 2026, matches=match_executions(wagers, recs), recommendations=recs
    )
    issues = report.as_dict()["issue_categories"]["DATA_PROCESS_ERROR"]
    assert len(issues) == 1
    assert "not a claim that the bet was wrong" in issues[0]["note"]


def test_price_against_the_recommendation_is_reported_both_ways():
    wagers = [
        wager("a", price=0.55, ticker="KXNCAAFSPREAD-26SEP19LSUMISS-LSU1"),
        wager("b", price=0.52, ticker="KXNCAAFSPREAD-26SEP19LSUMISS-LSU2"),
    ]
    recs = [
        recommendation("r1", ticker="KXNCAAFSPREAD-26SEP19LSUMISS-LSU1", entry=0.53),
        recommendation("r2", ticker="KXNCAAFSPREAD-26SEP19LSUMISS-LSU2", entry=0.53),
    ]
    report = pm.build(
        wagers, [], 2026, matches=match_executions(wagers, recs), recommendations=recs
    )
    price = report.as_dict()["price_vs_recommended"]
    assert price["matched_bets"] == 2
    assert price["mean_price_delta"] == pytest.approx(0.005, abs=1e-6)
    assert "paid MORE" in price["note"]


def test_the_report_carries_its_own_disclaimer_as_data():
    report = pm.build([wager("a")], [settlement("a")], 2026)
    assert "ACCOUNTING ONLY" in report.as_dict()["not_model_evidence"]
    assert "ACCOUNTING ONLY" in pm.render(report)


# ===================================================== the CLI, for real


def ledger_dir(tmp_path, wagers, settlements):
    (tmp_path / "wagers").mkdir(parents=True)
    (tmp_path / "settlements").mkdir(parents=True)
    (tmp_path / "wagers" / "2026.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in wagers)
    )
    (tmp_path / "settlements" / "2026.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in settlements)
    )
    return tmp_path


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
    )


def test_the_cli_reports_from_the_ledger_alone(tmp_path):
    base = ledger_dir(tmp_path, [wager("a"), wager("b")], [settlement("a"), settlement("b")])
    result = run_cli("--base-dir", str(base), "--season", "2026", "--unit", "10")
    assert result.returncode == 0, result.stderr
    assert "FINAL" in result.stdout
    assert "wagers:      2" in result.stdout
    assert "no candidate artifacts supplied" in result.stdout


def test_the_cli_refuses_an_unreadable_ledger_line(tmp_path):
    base = ledger_dir(tmp_path, [wager("a")], [])
    (base / "wagers" / "2026.jsonl").write_text('{"source_bet_key": "a"}\n{not json\n')
    result = run_cli("--base-dir", str(base), "--season", "2026")
    assert result.returncode == 2
    assert "could not be read" in result.stderr


def test_the_cli_refuses_a_missing_ledger(tmp_path):
    result = run_cli("--base-dir", str(tmp_path), "--season", "2026")
    assert result.returncode == 2


def test_the_cli_writes_the_same_report_as_json(tmp_path):
    base = ledger_dir(tmp_path, [wager("a")], [settlement("a")])
    out = tmp_path / "report.json"
    result = run_cli(
        "--base-dir", str(base), "--season", "2026", "--json", str(out)
    )
    assert result.returncode == 0
    document = json.loads(out.read_text())
    assert document["season"] == 2026
    assert document["is_final"] is True
    assert document["overall"]["wagers"] == 1


def test_the_cli_adds_cuts_from_candidate_artifacts_without_moving_money(tmp_path):
    base = ledger_dir(tmp_path, [wager("a")], [settlement("a")])
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / "early_b1.candidates.json").write_text(
        json.dumps(
            {
                "batch": "early_b1",
                "generated_at": "2026-09-19T12:00:00Z",
                "games": {
                    "26SEP19LSUMISS": {
                        "handicap_confidence_effective": "high",
                        "factual_data_quality": {"confidence_ceiling": "high"},
                    }
                },
                "candidates": [
                    {
                        "game_key": "26SEP19LSUMISS",
                        "market": "KXNCAAFSPREAD-26SEP19LSUMISS-LSU10",
                        "side": "YES",
                        "kalshi_executable_price": 0.53,
                        "bet_up_to_price": 0.57,
                        "fair_probability": 0.61,
                        "fee_adjusted_edge": 0.06,
                        "robustness": "robust_positive_ev",
                        "correlation_group": "full_game:margin / home",
                        "stake_placeholder": None,
                    }
                ],
            }
        )
    )
    bare_json = tmp_path / "bare.json"
    cut_json = tmp_path / "cut.json"
    run_cli("--base-dir", str(base), "--season", "2026", "--json", str(bare_json))
    run_cli(
        "--base-dir", str(base), "--season", "2026",
        "--candidates", str(candidates), "--json", str(cut_json),
    )
    bare = json.loads(bare_json.read_text())
    cut = json.loads(cut_json.read_text())

    assert bare["overall"] == cut["overall"], "an attribution cut moved the money"
    assert cut["recommendation_matching"] == {"recommended_and_executed": 1}
    assert {row["label"] for row in cut["by_robustness_tier"]} == {"robust_positive_ev"}
    assert {row["label"] for row in bare["by_robustness_tier"]} == {"not_recommended"}
