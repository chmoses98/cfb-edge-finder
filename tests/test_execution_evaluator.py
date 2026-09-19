"""The exhaustive evaluator, the pricing arithmetic, and the completion gate."""

from __future__ import annotations

from statistics import NormalDist

import pytest

from cfb_edge_finder.execution.evaluator import (
    CompletionGateError,
    EvaluationStatus,
    _cutpoint,
    evaluate_game,
    fair_probability,
    ledger_document,
    reconcile,
    require_complete,
)
from cfb_edge_finder.execution.handicap import (
    HandicapPayload,
    HandicapValidationError,
    PeriodDistribution,
    parse_handicap,
    template_for_packet,
)
from cfb_edge_finder.execution.slate import build_slate
from tests.execution_fakes import GAME_KEY, catalog_dir
from tests.test_execution_disposition import config


def packet(tmp_path, markets=None):
    directory = catalog_dir(tmp_path, markets)
    return build_slate(directory, config(), slate_date=None).packets[0]


def handicap(**overrides) -> HandicapPayload:
    base = {
        "game_key": GAME_KEY,
        "packet_hash": None,
        "teams": {"home": "Ole Miss", "away": "LSU"},
        "period_distributions": {
            "full_game": PeriodDistribution(27.0, 24.0, 10.5, 10.0, 0.1),
        },
        "explicit_probabilities": {},
    }
    base.update(overrides)
    return HandicapPayload(**base)


# ------------------------------------------------------------ exhaustive


def test_the_evaluator_touches_every_eligible_contract(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap())
    assert len(evaluation.rows) == game["counts"]["eligible"]
    assert {r["ticker"] for r in evaluation.rows} == {c["ticker"] for c in game["contracts"]}


def test_the_evaluator_does_not_stop_when_it_finds_a_good_bet(tmp_path):
    """A handicap wildly out of line with the book produces positive EV on
    the very first contract. The loop must still reach the last one."""
    game = packet(tmp_path)
    lopsided = handicap(
        period_distributions={"full_game": PeriodDistribution(55.0, 3.0, 4.0, 4.0, 0.0)}
    )
    evaluation = evaluate_game(game, lopsided)
    assert evaluation.status_counts.get(EvaluationStatus.POSITIVE_EV.value, 0) > 0
    assert len(evaluation.rows) == game["counts"]["eligible"]
    assert evaluation.complete


def test_the_invariant_holds_and_is_reported(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap())
    assert evaluation.eligible == evaluation.evaluated + evaluation.unpriceable
    assert evaluation.unaccounted == 0
    assert evaluation.summary()["game_status"] == "COMPLETE"


def test_unpriceable_contracts_stay_visible_and_counted(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap())
    unpriceable = [
        r for r in evaluation.rows
        if r["status"] == EvaluationStatus.UNPRICEABLE_FROM_HANDICAP.value
    ]
    assert unpriceable, "the fixture deliberately contains ties, first-TD and double-result markets"
    assert len(unpriceable) == evaluation.unpriceable
    for row in unpriceable:
        assert row["reason"]
        assert row["ticker"]
        assert row["yes_means"]


def test_a_missing_period_distribution_makes_its_contracts_unpriceable_not_invented(tmp_path):
    game = packet(tmp_path)
    no_first_half = handicap(period_distributions={})
    evaluation = evaluate_game(game, no_first_half)
    assert evaluation.evaluated == 0
    assert evaluation.unpriceable == evaluation.eligible
    assert evaluation.complete
    reasons = {r["reason"] for r in evaluation.rows}
    assert any("no score distribution for period" in r for r in reasons)


def test_an_explicit_probability_prices_what_no_distribution_can(tmp_path):
    game = packet(tmp_path)
    tie = f"KXNCAAF1H-{GAME_KEY}-TIE"
    with_tie = handicap(explicit_probabilities={tie: 0.07})
    evaluation = evaluate_game(game, with_tie)
    row = next(r for r in evaluation.rows if r["ticker"] == tie)
    assert row["status"] in {s.value for s in EvaluationStatus}
    assert row["pricing_method"] == "explicit_probability"
    assert row["fair_probability_yes"] == 0.07


# --------------------------------------------------------- the arithmetic


def test_the_continuity_cut_respects_half_point_lines():
    """A blanket 0.5 correction on a strike that is already a half-integer
    shifts the whole ladder by a full point."""
    assert _cutpoint(16.5, "greater") == 16.5
    assert _cutpoint(4.0, "greater") == 4.5
    assert _cutpoint(1.0, "greater_or_equal") == 0.5


def test_a_total_rung_matches_the_normal_it_was_priced_from():
    distribution = PeriodDistribution(27.0, 24.0, 10.5, 10.0, 0.1)
    semantics = {
        "kind": "total",
        "period": "full_game",
        "requires": "period_distribution",
        "comparator": "greater",
        "line": 44.5,
        "team": "none",
    }
    result = fair_probability(semantics, "T", handicap(period_distributions={"full_game": distribution}))
    variance = 10.5**2 + 10.0**2 + 2 * 0.1 * 10.5 * 10.0
    expected = 1 - NormalDist(51.0, variance**0.5).cdf(44.5)
    assert result.value == pytest.approx(expected, abs=1e-9)


def test_an_away_spread_is_the_mirror_of_the_home_margin():
    distribution = PeriodDistribution(27.0, 24.0, 10.5, 10.0, 0.1)
    payload = handicap(period_distributions={"full_game": distribution})
    home = fair_probability(
        {"kind": "spread", "period": "full_game", "requires": "period_distribution",
         "comparator": "greater", "line": 6.5, "team": "home"},
        "H",
        payload,
    ).value
    away = fair_probability(
        {"kind": "spread", "period": "full_game", "requires": "period_distribution",
         "comparator": "greater", "line": 6.5, "team": "away"},
        "A",
        payload,
    ).value
    variance = 10.5**2 + 10.0**2 - 2 * 0.1 * 10.5 * 10.0
    margin = NormalDist(3.0, variance**0.5)
    reflected = NormalDist(-3.0, margin.stdev)
    assert home == pytest.approx(1 - margin.cdf(6.5), abs=1e-9)
    assert away == pytest.approx(1 - reflected.cdf(6.5), abs=1e-9)
    assert home > away


def test_a_deeper_rung_is_never_more_likely_than_a_shallower_one(tmp_path):
    """Ladder coherence: monotonicity is the cheapest proof that rungs did
    not get shuffled."""
    payload = handicap()
    probabilities = [
        fair_probability(
            {"kind": "spread", "period": "full_game", "requires": "period_distribution",
             "comparator": "greater", "line": line, "team": "home"},
            f"L{line}",
            payload,
        ).value
        for line in (2.5, 6.5, 9.5, 13.5)
    ]
    assert probabilities == sorted(probabilities, reverse=True)


def test_yes_and_no_are_priced_from_their_own_asks_and_own_fees(tmp_path):
    """Buying NO costs `no_ask`, not `1 - yes_ask` -- that complement is
    the NO *bid*, the wrong side of the spread."""
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap())
    row = next(r for r in evaluation.rows if r["ticker"] == f"KXNCAAFGAME-{GAME_KEY}-LSU")
    sides = {s["side"]: s for s in row["sides"]}
    assert sides["yes"]["executable_entry"] == 0.59
    assert sides["no"]["executable_entry"] == 0.42
    assert sides["yes"]["fair_probability"] + sides["no"]["fair_probability"] == pytest.approx(1.0)
    assert sides["yes"]["fee"] != sides["no"]["fee"]
    for side in sides.values():
        assert side["net_edge"] == pytest.approx(
            side["fair_probability"] - side["executable_entry"] - side["fee"], abs=1e-9
        )


def test_the_better_side_is_the_one_reported(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap())
    for row in evaluation.rows:
        if not row.get("sides"):
            continue
        assert row["net_edge"] == pytest.approx(max(s["net_edge"] for s in row["sides"]))
        assert row["best_side"] == max(row["sides"], key=lambda s: s["net_edge"])["side"]


def test_the_required_edge_separates_positive_from_merely_positive(tmp_path):
    game = packet(tmp_path)
    generous = evaluate_game(game, handicap(), min_net_edge=0.0)
    strict = evaluate_game(game, handicap(), min_net_edge=0.95)
    assert generous.status_counts.get(EvaluationStatus.POSITIVE_EV.value, 0) >= strict.status_counts.get(
        EvaluationStatus.POSITIVE_EV.value, 0
    )
    assert strict.status_counts.get(EvaluationStatus.BELOW_REQUIRED_EDGE.value, 0) > 0


def test_a_low_confidence_family_is_marked_too_uncertain_not_dropped(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(
        game, handicap(low_confidence_families=("game_total",)), min_net_edge=0.0
    )
    flagged = [r for r in evaluation.rows if r["status"] == EvaluationStatus.TOO_UNCERTAIN.value]
    assert flagged
    assert all(r["family"] == "game_total" for r in flagged)
    assert evaluation.complete


def test_a_declared_unpriceable_family_is_counted_as_unpriceable(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap(declared_unpriceable_families=("game_spread",)))
    spreads = [r for r in evaluation.rows if r["family"] == "game_spread"]
    assert spreads
    assert all(
        r["status"] == EvaluationStatus.UNPRICEABLE_FROM_HANDICAP.value for r in spreads
    )
    assert evaluation.complete


# --------------------------------------------------------- the gate


def test_a_game_cannot_be_marked_complete_with_unaccounted_contracts(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap())
    assert evaluation.complete

    dropped = evaluation.rows.pop()
    evaluation.status_counts[dropped["status"]] -= 1
    reconcile(game, evaluation)

    assert evaluation.unaccounted == 1
    assert evaluation.missing_tickers == [dropped["ticker"]]
    assert evaluation.complete is False
    assert evaluation.summary()["game_status"] == "INCOMPLETE"

    with pytest.raises(CompletionGateError) as excinfo:
        require_complete(evaluation)
    assert dropped["ticker"] in str(excinfo.value)


def test_the_gate_names_the_exact_missing_contracts(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap())
    removed = [evaluation.rows.pop() for _ in range(3)]
    for row in removed:
        evaluation.status_counts[row["status"]] -= 1
    reconcile(game, evaluation)
    assert evaluation.unaccounted == 3
    assert sorted(evaluation.missing_tickers) == sorted(r["ticker"] for r in removed)


def test_a_duplicated_row_breaks_completion_even_though_the_count_balances(tmp_path):
    """The failure a headline count cannot see."""
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap())
    dropped = evaluation.rows.pop()
    evaluation.rows.append(dict(evaluation.rows[0]))
    reconcile(game, evaluation)
    assert evaluation.unaccounted == 0
    assert evaluation.duplicate_tickers
    assert evaluation.missing_tickers == [dropped["ticker"]]
    assert evaluation.complete is False


# ---------------------------------------------------------- the ledger


def test_the_ledger_carries_every_contract_not_only_winners(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap())
    ledger = ledger_document([evaluation], shard="early", min_net_edge=0.02, as_of="now")
    assert len(ledger["contracts"]) == game["counts"]["eligible"]
    assert ledger["totals"]["unaccounted_contracts"] == 0
    assert (
        ledger["totals"]["eligible_contracts"]
        == ledger["totals"]["evaluated_contracts"] + ledger["totals"]["unpriceable_contracts"]
    )


# -------------------------------------------------------- the payload


def test_the_payload_must_echo_the_teams(tmp_path):
    with pytest.raises(HandicapValidationError):
        parse_handicap({"game_key": GAME_KEY, "period_distributions": {}})


def test_a_negative_standard_deviation_is_rejected():
    with pytest.raises(HandicapValidationError):
        parse_handicap(
            {
                "game_key": GAME_KEY,
                "teams": {"home": "Ole Miss", "away": "LSU"},
                "period_distributions": {
                    "full_game": {
                        "home_mean": 27,
                        "away_mean": 24,
                        "home_sd": -1,
                        "away_sd": 10,
                        "correlation": 0.1,
                    }
                },
            }
        )


def test_an_out_of_range_explicit_probability_is_rejected():
    with pytest.raises(HandicapValidationError):
        parse_handicap(
            {
                "game_key": GAME_KEY,
                "teams": {"home": "Ole Miss", "away": "LSU"},
                "period_distributions": {},
                "explicit_probabilities": {"X": 1.4},
            }
        )


def test_an_unknown_period_is_rejected_rather_than_ignored():
    with pytest.raises(HandicapValidationError):
        parse_handicap(
            {
                "game_key": GAME_KEY,
                "teams": {"home": "Ole Miss", "away": "LSU"},
                "period_distributions": {"third_half": {}},
            }
        )


def test_an_untouched_template_parses_to_an_empty_handicap(tmp_path):
    """The reader who returns the form unfilled gets a game where every
    contract is explicitly unpriceable -- not a crash, and not a set of
    invented numbers."""
    game = packet(tmp_path)
    payload = parse_handicap(template_for_packet(game))
    assert payload.period_distributions == {}
    assert payload.explicit_probabilities == {}
    evaluation = evaluate_game(game, payload)
    assert evaluation.unpriceable == evaluation.eligible
    assert evaluation.complete


def test_the_template_lists_every_period_the_game_needs(tmp_path):
    game = packet(tmp_path)
    template = template_for_packet(game)
    needed = {
        c["semantics"]["period"]
        for c in game["contracts"]
        if c["semantics"]["requires"] == "period_distribution"
    }
    assert set(template["period_distributions"]) == needed
    explicit_needed = {
        c["ticker"]
        for c in game["contracts"]
        if c["semantics"]["requires"] == "explicit_probability"
    }
    assert set(template["explicit_probabilities"]) == explicit_needed
