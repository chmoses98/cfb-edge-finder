"""Candidate reduction, the audit ledger, exposure, and the absence of a cap.

*** WHAT A REDUCTION MAY AND MAY NOT DO ***
It may say "this contract is the same opinion as that one, at a worse price".
It may not say "that is enough bets for one file". The first is a deterministic
economic comparison with a named winner; the second is a cap, and a cap is how
a scan that claimed to be exhaustive quietly stops being one.
"""

from __future__ import annotations

import inspect

import pytest

from cfb_edge_finder.execution import candidates as candidates_module
from cfb_edge_finder.execution.candidates import (
    EDGE_TIE_BAND,
    ReductionReason,
    build_expression,
    exposure_groups,
    reduce_candidates,
)
from cfb_edge_finder.execution.evaluator import CANDIDATE_STATUSES, evaluate_game
from cfb_edge_finder.execution.handicap import parse_handicap
from cfb_edge_finder.execution.report import (
    build_candidate_artifact,
    build_reduction_ledger,
)
from cfb_edge_finder.execution.slate import build_slate
from tests.execution_fakes import GAME_KEY, catalog_dir
from tests.test_execution_disposition import config

TRIPLE_QUOTE = chr(34) * 3
"""Built rather than written, so this file's own source scan does not trip on it."""


def row(ticker, *, kind="spread", team="home", side="yes", edge=0.05, line=3.5,
        fee=0.01, robustness="robust_positive_ev", worst=None):
    return {
        "ticker": ticker,
        "game_key": "G",
        "kind": kind,
        "period": "full_game",
        "team": team,
        "best_side": side,
        "line": line,
        "net_edge": edge,
        "fee": fee,
        "status": robustness,
        "sensitivity": {
            "robustness": robustness,
            "net_edge_low": edge if worst is None else worst,
        },
    }


# ------------------------------------------------------------ grouping


def test_two_spellings_of_one_view_reduce_to_one_candidate():
    """YES on 'home by more than 6.5' and NO on 'away by more than 5.5' are
    the same event quoted from opposite ends."""
    reduction = reduce_candidates(
        [
            row("HOME-YES", team="home", side="yes", line=6.5, edge=0.05),
            row("AWAY-NO", team="away", side="no", line=5.5, edge=0.09),
        ]
    )
    assert [e.ticker for e in reduction.survivors] == ["AWAY-NO"]
    assert reduction.removed[0]["ticker"] == "HOME-YES"
    assert reduction.removed[0]["lost_to"] == "AWAY-NO"


def test_a_total_and_a_spread_are_different_views_and_both_survive():
    reduction = reduce_candidates(
        [row("SPREAD", kind="spread"), row("TOTAL", kind="total", team="none")]
    )
    assert {e.ticker for e in reduction.survivors} == {"SPREAD", "TOTAL"}
    assert reduction.removed == []


# ------------------------------------------------------- the tie-breaks


def test_the_larger_fee_adjusted_edge_wins_outright():
    reduction = reduce_candidates([row("A", edge=0.05), row("B", edge=0.09, line=6.5)])
    assert [e.ticker for e in reduction.survivors] == ["B"]
    assert reduction.removed[0]["reason"] == ReductionReason.DOMINATED_DUPLICATE.value


def test_inside_the_tie_band_the_cheaper_entry_fee_wins():
    reduction = reduce_candidates(
        [
            row("EXPENSIVE", edge=0.0500, fee=0.020, line=3.5),
            row("CHEAP", edge=0.0500 + EDGE_TIE_BAND / 2, fee=0.008, line=6.5),
        ]
    )
    assert [e.ticker for e in reduction.survivors] == ["CHEAP"]
    assert reduction.removed[0]["reason"] == ReductionReason.FEE_DOMINATED.value


def test_inside_the_tie_band_the_more_robust_expression_wins():
    """An extra 0.001 of base edge is not worth a bet that stops working when
    the margin estimate moves a point."""
    reduction = reduce_candidates(
        [
            row("FRAGILE", edge=0.0510, robustness="sensitive_positive_ev", line=3.5),
            row("ROBUST", edge=0.0500, robustness="robust_positive_ev", line=6.5),
        ]
    )
    assert [e.ticker for e in reduction.survivors] == ["ROBUST"]
    assert reduction.removed[0]["reason"] == ReductionReason.WORSE_ROBUSTNESS.value


def test_the_last_tie_break_is_the_worst_case_edge():
    reduction = reduce_candidates(
        [
            row("NARROW", edge=0.05, worst=0.001, line=3.5),
            row("WIDE", edge=0.05, worst=0.030, line=6.5),
        ]
    )
    assert [e.ticker for e in reduction.survivors] == ["WIDE"]
    assert reduction.removed[0]["reason"] == ReductionReason.INFERIOR_EXPRESSION.value


def test_the_reduction_is_order_independent():
    """A shortlist that depends on which order the ledger happened to be read
    in is a shortlist nobody can reproduce."""
    rows = [
        row("A", edge=0.05, line=3.5),
        row("B", edge=0.09, line=6.5),
        row("C", edge=0.07, line=9.5),
    ]
    forward = [e.ticker for e in reduce_candidates(rows).survivors]
    backward = [e.ticker for e in reduce_candidates(list(reversed(rows))).survivors]
    assert forward == backward


def test_a_loss_always_names_the_final_survivor_not_a_provisional_one():
    """A ledger entry pointing at a contract that is not in the artifact is
    worse than no entry: the reader goes looking for it."""
    rows = [
        row("LOW", edge=0.03, line=3.5),
        row("MID", edge=0.05, line=6.5),
        row("HIGH", edge=0.09, line=9.5),
    ]
    reduction = reduce_candidates(rows)
    survivors = {e.ticker for e in reduction.survivors}
    for entry in reduction.removed:
        assert entry["lost_to"] in survivors


# ---------------------------------------------------------- no top-N


def test_there_is_no_hidden_cap_in_the_reduction():
    """Fifty rungs of one ladder reduce to one survivor and forty-nine named
    losses -- never to a truncated list."""
    rows = [row(f"T{i:02d}", edge=0.02 + i * 0.001, line=float(i)) for i in range(50)]
    reduction = reduce_candidates(rows)
    assert len(reduction.survivors) == 1
    assert len(reduction.removed) == 49
    assert {e["ticker"] for e in reduction.removed} | {
        e.ticker for e in reduction.survivors
    } == {r["ticker"] for r in rows}


def test_the_reduction_module_contains_no_slice_of_a_survivor_list():
    """A structural check, because the behavioural one above can be satisfied
    by a cap that happens to be larger than the fixture.

    Scanned over the CODE only. The module's own prose says the words "top"
    and "[:N]" while promising their absence, and a scan that read the
    docstrings would fail on the promise rather than on the breach."""
    chunks = inspect.getsource(candidates_module).split(TRIPLE_QUOTE)
    executable = "".join(chunks[::2])
    for banned in ("[:top", "top_n", "[:N]", "[:limit", "[:max_"):
        assert banned not in executable, f"the reducer contains {banned}"


def test_fifty_distinct_views_all_survive():
    rows = [
        row(f"G{i:02d}", edge=0.05, line=3.5) | {"game_key": f"GAME{i:02d}"} for i in range(50)
    ]
    reduction = reduce_candidates(rows)
    assert len(reduction.survivors) == 50
    assert reduction.removed == []


# ---------------------------------------------------------- exposure


def test_correlated_same_game_positions_are_grouped_not_counted_as_diversified():
    """LSU -2.5 and LSU -6.5 are one opinion. So is LSU's team total over."""
    rows = [
        row("SPREAD", kind="spread", team="home", line=2.5, edge=0.05)
        | {"game_key": "LSUMISS"},
        row("ML", kind="moneyline", team="home", line=None, edge=0.04)
        | {"game_key": "LSUMISS"},
        row("TT", kind="team_total", team="home", line=27.5, edge=0.06)
        | {"game_key": "LSUMISS"},
    ]
    reduction = reduce_candidates(rows)
    survivors = {e.ticker for e in reduction.survivors}
    # A moneyline IS a rung of the same margin ladder, so the spread and the
    # moneyline reduce to one expression before exposure is even computed --
    # which is the correlation reduction doing its job, not a grouping failure.
    assert survivors == {"TT", "SPREAD"}
    groups = exposure_groups(list(reduction.survivors))
    assert groups["largest_game_group"] == 2, (
        "both surviving contracts are on one game and must be reported as one exposure, not "
        "as two independent bets"
    )
    theses = {g["group"].split(":", 1)[1] for g in groups["by_thesis"]}
    assert theses == {"side:home", "team_scoring:home:over"}, (
        "a spread and a team total are different theses on the same game; folding them "
        "together would hide a real difference, and separating the game group would hide a "
        "real correlation"
    )


def test_the_exposure_block_recommends_no_amount():
    rows = [row("A"), row("B", kind="total", team="none")]
    groups = exposure_groups(list(reduce_candidates(rows).survivors))
    encoded = str(groups).lower()
    for banned in ("stake", "units", "bankroll", "kelly", "$"):
        assert banned not in encoded


# ------------------------------------------------- through the artifact


def packet(tmp_path):
    return build_slate(catalog_dir(tmp_path), config(), slate_date=None).packets[0]


def handicap():
    return parse_handicap(
        {
            "schema_version": "cfb_handicap_payload/2.0.0",
            "game_key": GAME_KEY,
            "teams": {"home": "Ole Miss", "away": "LSU"},
            "thesis": "the fixture's thesis",
            "opposing_case": "the fixture's opposing case",
            "period_distributions": {
                "full_game": {
                    "home_mean": 30.0,
                    "away_mean": 21.0,
                    "home_sd": 10.5,
                    "away_sd": 10.0,
                    "correlation": 0.1,
                    "uncertainty": {"margin_points": 3.0, "total_points": 5.0},
                }
            },
        }
    )


def test_the_artifact_and_its_ledger_account_for_every_candidate(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap(), min_net_edge=0.02)
    rows = [r for r in evaluation.rows if r["status"] in CANDIDATE_STATUSES]
    artifact = build_candidate_artifact(
        "early", [evaluation], {GAME_KEY: game}, {GAME_KEY: handicap()}, min_net_edge=0.02
    )
    ledger = build_reduction_ledger("early", None, reduce_candidates(rows))
    assert (
        artifact["reduction"]["surviving_candidates"] + ledger["removed"]
        == artifact["reduction"]["candidate_rows_before_reduction"]
        == len(rows)
    )
    assert artifact["reconciliation"]["unaccounted_contracts"] == 0


def test_the_artifact_points_at_its_own_audit_file(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap(), min_net_edge=0.02)
    artifact = build_candidate_artifact(
        "early",
        [evaluation],
        {GAME_KEY: game},
        {GAME_KEY: handicap()},
        min_net_edge=0.02,
        batch="early_b1",
    )
    assert artifact["reduction_ledger_file"] == "early_b1.reduction.json"


def test_every_candidate_carries_what_an_operator_needs_to_act(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap(), min_net_edge=0.02)
    artifact = build_candidate_artifact(
        "early", [evaluation], {GAME_KEY: game}, {GAME_KEY: handicap()}, min_net_edge=0.02
    )
    assert artifact["candidates"], "the fixture handicap is deliberately far from the book"
    for candidate in artifact["candidates"]:
        for key in (
            "game",
            "game_key",
            "market",
            "side",
            "side_means",
            "kalshi_executable_price",
            "fee",
            "fee_adjusted_breakeven",
            "fair_probability",
            "fee_adjusted_edge",
            "fee_adjusted_edge_range",
            "robustness",
            "robustness_reason",
            "bet_up_to_price",
            "market_disagreement_level",
            "correlation_group",
            "related_alternatives",
            "why_this_expression_survived",
            "why_this_market_expresses_the_thesis",
            "stake_placeholder",
        ):
            assert key in candidate, f"{candidate['market']} has no {key}"
        assert candidate["stake_placeholder"] is None

        # The per-game facts are hoisted, and every candidate can reach them.
        game = artifact["games"][candidate["game_key"]]
        for key in (
            "thesis",
            "strongest_opposing_case",
            "handicap_confidence",
            "factual_data_quality",
            "market_disagreement",
        ):
            assert key in game, f"games[{candidate['game_key']}] has no {key}"
        assert game["thesis"] == "the fixture's thesis"
        assert game["strongest_opposing_case"] == "the fixture's opposing case"


def test_the_artifact_does_not_carry_the_losing_contracts(tmp_path):
    """The latency problem restated: a final review must not be handed
    thousands of negative-EV rows again."""
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap(), min_net_edge=0.02)
    artifact = build_candidate_artifact(
        "early", [evaluation], {GAME_KEY: game}, {GAME_KEY: handicap()}, min_net_edge=0.02
    )
    shown = {c["market"] for c in artifact["candidates"]}
    priced_out_statuses = (
        "negative_ev",
        "below_required_edge",
        "zero_or_negligible_edge",
        "not_robust",
    )
    priced_out = {
        r["ticker"] for r in evaluation.rows if r["status"] in priced_out_statuses
    }
    assert priced_out, "the fixture must contain rows that were priced and did not qualify"
    assert not (shown & priced_out)
    # ...and they are still counted, in the reconciliation block.
    counts = artifact["reconciliation"]["status_counts"]
    assert sum(counts.get(status, 0) for status in priced_out_statuses) == len(priced_out)


def test_top_is_a_display_truncation_and_records_itself(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap(), min_net_edge=0.02)
    full = build_candidate_artifact(
        "early", [evaluation], {GAME_KEY: game}, {GAME_KEY: handicap()}, min_net_edge=0.02
    )
    capped = build_candidate_artifact(
        "early",
        [evaluation],
        {GAME_KEY: game},
        {GAME_KEY: handicap()},
        min_net_edge=0.02,
        top_n=1,
    )
    assert full["reduction"]["display_truncated_to"] is None
    assert capped["reduction"]["display_truncated_to"] == 1
    assert len(capped["candidates"]) == 1
    # The REDUCTION is identical; only what is printed changed.
    assert (
        capped["reduction"]["surviving_candidates"]
        == full["reduction"]["surviving_candidates"]
    )


def test_expression_keys_distinguish_a_rung_from_a_side():
    same_rung_other_side = build_expression(row("A", side="no"))
    same_side_other_rung = build_expression(row("B", side="yes", line=9.5))
    base = build_expression(row("C", side="yes", line=3.5))
    assert same_rung_other_side.expression_key != base.expression_key
    assert same_side_other_rung.expression_key != base.expression_key
    assert same_side_other_rung.view_key == base.view_key


@pytest.mark.parametrize("reason", list(ReductionReason))
def test_every_reason_has_a_human_explanation(reason):
    from cfb_edge_finder.execution.candidates import _EXPLANATIONS

    assert reason.value in _EXPLANATIONS
    assert "{winner}" in _EXPLANATIONS[reason.value]
