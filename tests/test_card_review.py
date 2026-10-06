"""The card review: core expressions, incremental exposure, cash paths, and the
absence of any sizing, rejection or cap.

*** WHAT THESE TESTS PIN ***
A final card may hold several correlated bets. What it may not do is hold them
without saying so. Every rung beside a core is incremental exposure that must
be justified; a more aggressive rung is a tail extension, not a second edge; a
rung that pays where the core does not is correlated but independently
cashable; and nothing here decides, rejects, ranks, truncates or sizes.

Every classification below is derived from contract TERMS. No fixture carries a
result, and none could change an answer.
"""

from __future__ import annotations

import ast
import inspect
import json

import pytest

from cfb_edge_finder.decisions import build_decision_record, validate_decision_record
from cfb_edge_finder.execution import CANDIDATE_SCHEMA_VERSION
from cfb_edge_finder.execution import card_review as card_review_module
from cfb_edge_finder.execution.candidates import build_expression, reduce_candidates
from cfb_edge_finder.execution.card_review import (
    CARD_REVIEW_VERSION,
    CardDecision,
    CardRole,
    CashPathRelation,
    build_card_review,
    core_fields,
    relate,
    thesis_index,
    win_set,
)
from cfb_edge_finder.execution.evaluator import CANDIDATE_STATUSES, evaluate_game
from cfb_edge_finder.execution.report import build_candidate_artifact, build_reduction_ledger
from tests.execution_fakes import GAME_KEY
from tests.test_decision_records import candidates, minimal_record, prepared, private_store
from tests.test_execution_candidates import handicap, packet

TRIPLE_QUOTE = chr(34) * 3


def row(ticker, *, game="G", kind="spread", team="home", side="yes", line=None, edge=0.05,
        period="full_game", comparator="greater", cap=None, fee=0.01):
    return {
        "ticker": ticker,
        "game_key": game,
        "kind": kind,
        "period": period,
        "team": team,
        "best_side": side,
        "line": line,
        "comparator": comparator,
        "cap": cap,
        "net_edge": edge,
        "fee": fee,
        "status": "robust_positive_ev",
        "sensitivity": {"robustness": "robust_positive_ev", "net_edge_low": edge},
    }


# A Saturday-shaped slate. Team names are deliberately absent: the vocabulary is
# generic, and nothing in the code knows any team. The away favourite carries a
# moneyline and two margin rungs; the away underdog carries a moneyline and
# "home does NOT win by more than 9.5" -- the same underdog with the points.
def nested_favourite(core="ml"):
    edges = {"ml": (0.07, 0.05, 0.04), "spread": (0.05, 0.07, 0.04)}[core]
    return [
        row("FAV-ML", game="FAVGAME", kind="moneyline", team="away", edge=edges[0]),
        row("FAV-8", game="FAVGAME", kind="spread", team="away", line=7.5, edge=edges[1]),
        row("FAV-21", game="FAVGAME", kind="spread", team="away", line=20.5, edge=edges[2]),
    ]


def underdog_pair():
    return [
        row("DOG-ML", game="DOGGAME", kind="moneyline", team="away", edge=0.07),
        row("FAV-NOT-10", game="DOGGAME", kind="spread", team="home", side="no", line=9.5, edge=0.05),
    ]


def saturday():
    return nested_favourite() + underdog_pair() + [
        # A half ladder: two rungs of the first-half margin.
        row("H1-11", game="HALFGAME", period="first_half", line=10.5, edge=0.06),
        row("H1-21", game="HALFGAME", period="first_half", line=20.5, edge=0.03),
        # Two rungs of one full-game ladder.
        row("F-14", game="LADDER", line=13.5, edge=0.06),
        row("F-18", game="LADDER", line=17.5, edge=0.04),
    ]


def alternatives_of(reduction, core):
    return {a["ticker"]: a for a in reduction.alternatives.get(core, [])}


# ------------------------------------------------- TEST A: nested ladder


@pytest.mark.parametrize("core", ["ml", "spread"])
def test_a_nested_margin_ladder_has_one_core_and_every_other_rung_is_incremental(core):
    reduction = reduce_candidates(nested_favourite(core))
    survivors = [e.ticker for e in reduction.survivors]
    expected_core = "FAV-ML" if core == "ml" else "FAV-8"
    assert survivors == [expected_core], "ML, -8 and -21 are one view; one core survives"

    alternatives = alternatives_of(reduction, expected_core)
    assert set(alternatives) == {"FAV-ML", "FAV-8", "FAV-21"} - {expected_core}, (
        "the other rungs stay visible as named alternatives"
    )
    for alternative in alternatives.values():
        assert alternative["card_role"] == CardRole.INCREMENTAL_EXPRESSION_CANDIDATE.value
        assert alternative["requires_incremental_justification"] is True

    # The aggressive rung is a TAIL EXTENSION whichever rung is core: it can
    # only pay when the core already has.
    deep = alternatives["FAV-21"]["cash_path"]
    assert deep["relation"] == CashPathRelation.NESTED_TAIL_EXTENSION.value
    assert deep["tail_extension"] is True
    assert deep["cashes_when_core_fails"] is False
    assert alternatives["FAV-21"]["justification_standard"] == "heightened"

    review = build_card_review(reduction)
    thesis = review["games"]["FAVGAME"]["theses"]["side:away"]
    assert thesis["core_expressions"] == [expected_core]
    assert "FAV-21" in thesis["related_incremental_candidates"]["nested_tail_extension"]
    assert thesis["deepest_tail_extension_by_core"][expected_core] == "FAV-21"
    game = review["games"]["FAVGAME"]
    assert game["has_nested_tail_extension"] is True
    assert game["core_candidate_count"] == 1, "three tickers, one opinion: not three ideas"


def test_a_with_the_moneyline_as_core_both_margin_rungs_are_deeper_tails():
    reduction = reduce_candidates(nested_favourite("ml"))
    tails = build_card_review(reduction)["games"]["FAVGAME"]["theses"]["side:away"][
        "nested_tail_extensions"
    ]
    assert [(t["ticker"], t["extension_points"]) for t in tails] == [("FAV-8", 7), ("FAV-21", 20)]


def test_a_a_less_aggressive_rung_beside_a_spread_core_has_an_independent_cash_path():
    """With -8 as the core, the moneyline pays on a 1-7 point win that -8 does
    not: correlated, not nested."""
    reduction = reduce_candidates(nested_favourite("spread"))
    moneyline = alternatives_of(reduction, "FAV-8")["FAV-ML"]["cash_path"]
    assert moneyline["relation"] == CashPathRelation.CORRELATED_INDEPENDENT_CASH_PATH.value
    assert moneyline["cashes_when_core_fails"] is True
    assert moneyline["tail_extension"] is False


# ------------------------------------- TEST B: independent cash path


def test_b_an_underdog_moneyline_and_its_points_are_correlated_not_redundant():
    reduction = reduce_candidates(underdog_pair())
    assert [e.ticker for e in reduction.survivors] == ["DOG-ML"]
    points = alternatives_of(reduction, "DOG-ML")["FAV-NOT-10"]
    assert points["cash_path"]["relation"] == CashPathRelation.CORRELATED_INDEPENDENT_CASH_PATH.value
    assert points["cash_path"]["cashes_when_core_fails"] is True
    assert points["cash_path"]["wins_when"] == "full game home margin <= 9"

    # Shared thesis, correlated...
    dog_ml, points_row = underdog_pair()
    assert build_expression(dog_ml).thesis_key == build_expression(points_row).thesis_key
    full = relate(points_row, dog_ml)
    assert full["both_can_cash"] is True
    # ...with a real independent cash path: a close loss pays the points only.
    assert full["cashes_when_reference_fails"] is True
    assert full["this_wins_when"]["at_most"] == 9
    assert full["reference_wins_when"]["at_most"] == -1
    assert full["both_lose_when"] == "full game home margin >= 10"

    # Eligible for justification, NOT rejected.
    assert points["requires_incremental_justification"] is True
    assert points["justification_standard"] == "standard"
    review = build_card_review(reduction)
    assert review["games"]["DOGGAME"]["independent_cash_path_candidates"] == ["FAV-NOT-10"]
    assert not _decisions_made(review), "correlation is never a decision the repository takes"


def _decisions_made(review):
    """Every value anywhere in the block that is a CardDecision, outside the
    vocabulary that defines them."""
    verdicts = {d.value for d in CardDecision}
    found = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if path == "" and key == "decisions":
                    continue
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for value in node:
                walk(value, path)
        elif node in verdicts:
            found.append((path, node))

    walk(review, "")
    return found


# ------------------------------------------------- TEST C / D: theses


def test_c_a_side_and_a_game_total_are_two_theses_in_one_game_exposure():
    reduction = reduce_candidates(
        [
            row("SIDE", line=3.5),
            row("TOTAL", kind="total", team="none", line=50.5),
        ]
    )
    game = build_card_review(reduction)["games"]["G"]
    assert game["thesis_groups"] == ["side:home", "total:over"]
    assert game["multiple_thesis_groups"] is True
    assert game["core_expressions"] == ["SIDE", "TOTAL"]
    assert game["core_candidate_count"] == 2
    assert game["exposure_after_sizing"]["total_game_exposure"] is None
    assert "summed" in game["exposure_warning"]


def test_d_a_team_total_and_the_side_stay_separate_theses_but_one_game():
    reduction = reduce_candidates(
        [
            row("SIDE", line=6.5),
            row("TEAM-TOTAL", kind="team_total", team="home", line=27.5),
        ]
    )
    game = build_card_review(reduction)["games"]["G"]
    assert set(game["theses"]) == {"side:home", "team_scoring:home:over"}
    assert game["core_candidate_count"] == 2
    assert sorted(game["core_expressions"]) == ["SIDE", "TEAM-TOTAL"]


def test_two_views_in_one_thesis_are_two_cores_and_funding_both_is_incremental():
    """A first-half and a full-game margin on the same side: the same thesis
    by a different mechanism. Each is the core of its own view, and only one
    carries the thesis without a justification."""
    reduction = reduce_candidates(
        [row("FG", line=6.5), row("H1", period="first_half", line=3.5)]
    )
    thesis = build_card_review(reduction)["games"]["G"]["theses"]["side:home"]
    assert thesis["multiple_core_expressions"] is True
    assert thesis["core_expression_relations"][0]["relation"] == (
        CashPathRelation.SAME_THESIS_DIFFERENT_MECHANISM.value
    )

    _reduction, cores = core_fields_of([row("FG", line=6.5), row("H1", period="first_half", line=3.5)])
    for candidate in cores:
        assert candidate["card_role"] == CardRole.CORE_EXPRESSION.value
        assert candidate["thesis_core_expression_count"] == 2
        assert candidate["funding_beside_thesis_peers_requires_incremental_justification"] is True


def core_fields_of(rows):
    """The card fields a candidate record carries, without building a packet."""
    reduction = reduce_candidates(rows)
    index = thesis_index(list(reduction.survivors))
    return reduction, [core_fields(e, index) for e in reduction.survivors]


def test_opposing_positions_in_one_game_are_named_not_resolved():
    reduction = reduce_candidates(
        [
            row("HOME-ML", kind="moneyline", team="home", edge=0.05),
            # "home does NOT win by more than 9.5": the away side with points.
            row("AWAY-PLUS", line=9.5, side="no", edge=0.05),
            row("HOME-4", line=3.5, edge=0.01, game="G2"),
            row("AWAY-ML", kind="moneyline", team="away", game="G2"),
        ]
    )
    review = build_card_review(reduction)
    middle = review["games"]["G"]["opposing_positions"]
    assert len(middle) == 1 and middle[0]["relation"] == CashPathRelation.OPPOSING_MIDDLE.value
    assert middle[0]["both_can_cash"] is True
    exclusive = review["games"]["G2"]["opposing_positions"]
    assert exclusive[0]["relation"] == CashPathRelation.MUTUALLY_EXCLUSIVE.value
    assert review["slate"]["games_with_opposing_positions"] == ["G", "G2"]
    assert not _decisions_made(review)


# ------------------------------------------------- win sets, from terms


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"kind": "spread", "line": 7.5}, ("full_game:home_margin", 8, None)),
        ({"kind": "spread", "line": 7}, ("full_game:home_margin", 8, None)),
        ({"kind": "spread", "line": 7, "comparator": "greater_or_equal"}, ("full_game:home_margin", 7, None)),
        ({"kind": "spread", "line": 7.5, "side": "no"}, ("full_game:home_margin", None, 7)),
        ({"kind": "spread", "team": "away", "line": 7.5}, ("full_game:home_margin", None, -8)),
        ({"kind": "moneyline", "team": "away"}, ("full_game:home_margin", None, -1)),
        ({"kind": "total", "team": "none", "line": 50.5, "side": "no"}, ("full_game:total_points", None, 50)),
        ({"kind": "team_total", "team": "away", "line": 24.5}, ("full_game:away_points", 25, None)),
        ({"kind": "margin_band", "line": 7.5, "cap": 14}, ("full_game:home_margin", 8, 14)),
    ],
)
def test_win_sets_follow_the_contract_terms(kwargs, expected):
    wins = win_set(row("T", **kwargs)).as_dict()
    assert (wins["variable"], wins["at_least"], wins["at_most"]) == expected


def test_what_is_not_one_interval_is_undetermined_rather_than_guessed():
    band_no = row("BAND", kind="margin_band", line=7.5, cap=14, side="no")
    band_without_cap_key = {k: v for k, v in row("OLD", kind="margin_band", line=7.5).items() if k != "cap"}
    explicit = row("PROP", kind="binary_event")
    for r in (band_no, band_without_cap_key, explicit):
        assert win_set(r) is None
        assert relate(r, row("CORE", line=3.5))["relation"] == CashPathRelation.UNDETERMINED.value


def test_a_ledger_row_without_a_comparator_reads_as_the_evaluator_reads_it():
    old = {k: v for k, v in row("OLD", line=7.5).items() if k not in ("comparator", "cap")}
    assert win_set(old).as_dict()["at_least"] == 8


# ------------------------------------------------- TEST E: no sizing


def _executable_strings(module):
    tree = ast.parse(inspect.getsource(module))
    docstrings = set()
    for node in ast.walk(tree):
        for statement in getattr(node, "body", []) if isinstance(getattr(node, "body", None), list) else []:
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
                docstrings.add(id(statement.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def test_e_the_card_review_code_imports_nothing_that_sizes_or_orders():
    tree = ast.parse(inspect.getsource(card_review_module))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported <= {"__future__", "math", "dataclasses", "enum", "typing", "cfb_edge_finder.execution.candidates"}
    for text in _executable_strings(card_review_module):
        lowered = text.lower()
        for banned in ("kelly", "bankroll", "$", "units", "place_order", "create_order", "recommended_stake"):
            assert banned not in lowered, f"card review code says {banned!r}: {text!r}"


def test_e_the_card_review_block_recommends_no_amount_and_computes_no_exposure():
    review = build_card_review(reduce_candidates(saturday()))

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                # The one key allowed to name sizing is the declaration that
                # the repository does none (asserted False below).
                if key == "repository_sizes_positions":
                    continue
                for banned in ("stake", "unit", "kelly", "bankroll", "order", "size_", "_size"):
                    assert banned not in key.lower(), f"{path}.{key}"
                if key == "exposure_after_sizing":
                    assert {k: v for k, v in value.items() if k != "supplied_by"} == dict.fromkeys(
                        (k for k in value if k != "supplied_by"), None
                    ), f"{path}.{key} carries a figure"
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for value in node:
                walk(value, path)
        else:
            # Integers only: counts, step numbers, outcome bounds. A float is
            # the shape of a fraction or an amount, and nothing here has one.
            assert not isinstance(node, float), f"{path} = {node!r}"

    walk(review, "card_review")
    assert review["sizing_authority"] == "operator"
    assert review["repository_sizes_positions"] is False
    assert review["slate"]["exposure_after_sizing"]["total_card_risk"] is None
    assert "$" not in json.dumps(review)


# ------------------------------------------------- TEST F: no top-N


def test_f_a_long_ladder_is_fully_classified_however_many_rungs_are_shown_inline():
    rows = [row(f"R{i:02d}", line=float(i) + 0.5, edge=0.02 + i * 0.001) for i in range(40)]
    reduction = reduce_candidates(rows)
    review = build_card_review(reduction)
    thesis = review["games"]["G"]["theses"]["side:home"]
    listed = {t for tickers in thesis["related_incremental_candidates"].values() for t in tickers}
    assert thesis["related_incremental_candidate_count"] == 39 == len(listed)
    assert listed | set(thesis["core_expressions"]) == {r["ticker"] for r in rows}


def test_f_display_truncation_changes_nothing_in_the_card_review(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap(), min_net_edge=0.02)
    common = ([evaluation], {GAME_KEY: game}, {GAME_KEY: handicap()})
    full = build_candidate_artifact("early", *common, min_net_edge=0.02)
    capped = build_candidate_artifact("early", *common, min_net_edge=0.02, top_n=1, max_inline_alternatives=1)
    assert len(capped["candidates"]) == 1
    assert capped["card_review"] == full["card_review"]
    game_block = full["card_review"]["games"][GAME_KEY]
    assert game_block["core_candidate_count"] == full["reduction"]["surviving_candidates"]


def test_f_the_card_review_module_contains_no_slice():
    chunks = inspect.getsource(card_review_module).split(TRIPLE_QUOTE)
    executable = "".join(chunks[::2])
    for banned in ("[:", "top_n", "[:N]", "limit", "max_"):
        assert banned not in executable, f"the card review contains {banned}"


# ------------------------------------------------- TEST G: order independence


def test_g_reversed_rows_produce_the_same_card_review():
    rows = saturday()
    forward = reduce_candidates(rows)
    backward = reduce_candidates(list(reversed(rows)))
    assert build_card_review(forward) == build_card_review(backward)
    assert forward.alternatives == backward.alternatives


# ------------------------------------------------- TEST H: reconciliation


def test_h_reduction_still_reconciles_and_every_removal_carries_its_card_facts():
    rows = saturday()
    reduction = reduce_candidates(rows)
    assert len(reduction.survivors) + len(reduction.removed) == len(rows)
    assert {e.ticker for e in reduction.survivors} | {r["ticker"] for r in reduction.removed} == {
        r["ticker"] for r in rows
    }
    for removal in reduction.removed:
        assert removal["requires_incremental_justification"] is True
        assert removal["card_role"] in (
            CardRole.INCREMENTAL_EXPRESSION_CANDIDATE.value,
            CardRole.DUPLICATE_LISTING.value,
        )
        assert removal["cash_path_relation"] in {r.value for r in CashPathRelation}


def test_h_an_exact_duplicate_is_a_duplicate_listing_with_the_same_outcomes():
    reduction = reduce_candidates([row("A", line=6.5, edge=0.05), row("B", line=6.5, edge=0.04)])
    (removal,) = reduction.removed
    assert removal["card_role"] == CardRole.DUPLICATE_LISTING.value
    assert removal["cash_path_relation"] == CashPathRelation.EQUIVALENT_OUTCOME_SET.value


# ------------------------------------------------- the artifact contract


def test_the_artifact_states_the_contract_machine_readably(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap(), min_net_edge=0.02)
    artifact = build_candidate_artifact(
        "early", [evaluation], {GAME_KEY: game}, {GAME_KEY: handicap()}, min_net_edge=0.02
    )
    assert artifact["schema_version"] == CANDIDATE_SCHEMA_VERSION == "cfb_candidate_artifact/1.1.0"
    review = artifact["card_review"]
    assert review["contract_version"] == CARD_REVIEW_VERSION
    assert review["required_before_final_card"] is True
    assert review["sizing_authority"] == "operator"
    assert {rule["id"] for rule in review["rules"]} == {
        "one_core_per_funded_thesis",
        "additional_expression_needs_justification",
        "alternate_rungs_are_not_independent",
        "review_game_exposure_after_sizing",
        "review_thesis_exposure_after_sizing",
        "state_common_failure_mode",
        "independent_cash_path_may_survive",
        "correlation_is_not_a_rejection_rule",
        "slate_concentration_review",
    }
    assert [step["step"] for step in review["final_review_checklist"]] == list(range(1, 12))
    assert set(review["incremental_justification_template"]) == {
        "shared_thesis", "shared_failure_mode", "independent_cash_path", "incremental_edge_case",
        "why_not_redundant", "tail_extension", "concentration_effect", "decision",
    }
    assert all(v is None for v in review["incremental_justification_template"].values())
    assert set(review["decisions"]) == {d.value for d in CardDecision}
    assert "Correlation is not a veto. It is an exposure fact." in review["principles"]
    assert any("BEFORE RETURNING A BET CARD" in line for line in artifact["how_to_read"])

    for candidate in artifact["candidates"]:
        assert candidate["card_role"] == CardRole.CORE_EXPRESSION.value
        assert candidate["stake_placeholder"] is None
        for alternative in candidate["related_alternatives"]:
            assert alternative["card_role"] == CardRole.INCREMENTAL_EXPRESSION_CANDIDATE.value
            assert alternative["requires_incremental_justification"] is True


# ------------------------------------------------- TEST I: decision record


def test_i_the_card_review_survives_into_the_decision_record_without_a_stake(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap(), min_net_edge=0.02)
    artifact = build_candidate_artifact(
        "early", [evaluation], {GAME_KEY: game}, {GAME_KEY: handicap()}, min_net_edge=0.02
    )
    ledger = build_reduction_ledger(
        "early", "early", reduce_candidates([r for r in evaluation.rows if r["status"] in CANDIDATE_STATUSES])
    )
    record = build_decision_record(
        artifact=artifact,
        reduction_ledger=ledger,
        handicaps={GAME_KEY: handicap().as_dict()},
        packets={GAME_KEY: game},
        slate={"slate_date": "2026-09-19"},
        batch_entry=None,
        source_files={},
    )
    assert validate_decision_record(record) == []
    assert record["card_review"] == artifact["card_review"]
    assert record["candidate_schema_version"] == "cfb_candidate_artifact/1.1.0"
    assert record["bankroll_context"] is None
    for candidate in record["candidates"]:
        assert candidate["card_role"] == CardRole.CORE_EXPRESSION.value
        assert candidate["thesis_group"]
        assert candidate["recommended_stake"] is None
        for alternative in candidate["related_alternatives"]:
            assert alternative["requires_incremental_justification"] is True
            assert alternative["cash_path_relation"]
    assert record["evaluated_not_selected"], "the fixture has removed rungs"
    for entry in record["evaluated_not_selected"]:
        assert entry["requires_incremental_justification"] is True
        assert entry["cash_path_relation"] in {r.value for r in CashPathRelation}
    encoded = json.dumps(record)
    for banned in ('"stake"', '"units"', '"kelly', '"bankroll"'):
        assert banned not in encoded


def test_i_a_candidates_run_writes_the_card_review_into_the_private_record(tmp_path, monkeypatch, capsys):
    """End to end through the CLI: what the operator was handed is what the
    postmortem will read, and still no stake."""
    root = private_store(tmp_path)
    monkeypatch.setenv("CFB_DECISION_STORE", str(root))
    out, shard_name = prepared(tmp_path)
    assert candidates(out, shard_name) == 0
    assert "CARD REVIEW required before a final card" in capsys.readouterr().out
    (written,) = list(root.rglob("*.json"))
    record = json.loads(written.read_text())
    artifact = json.loads((out / "candidates" / f"{shard_name}.candidates.json").read_text())
    assert validate_decision_record(record) == []
    assert record["schema_version"] == "cfb_decision_record/1.0.0"
    assert record["card_review"] == artifact["card_review"]
    assert record["card_review"]["required_before_final_card"] is True
    assert [c["card_role"] for c in record["candidates"]] == [c["card_role"] for c in artifact["candidates"]]
    assert all(e["requires_incremental_justification"] is True for e in record["evaluated_not_selected"])
    assert all(c["recommended_stake"] is None for c in record["candidates"])
    assert record["bankroll_context"] is None


def test_i_a_record_without_a_card_review_is_still_a_valid_1_0_0_record():
    record = minimal_record()
    assert "card_review" not in record
    assert validate_decision_record(record) == []
    assert validate_decision_record({**record, "card_review": None}) == []


def test_i_a_record_whose_card_review_carries_a_figure_is_refused():
    review = build_card_review(reduce_candidates(saturday()))
    record = minimal_record(card_review=review)
    assert validate_decision_record(record) == []
    review["games"]["FAVGAME"]["exposure_after_sizing"]["total_game_exposure"] = 160.0
    problems = validate_decision_record(record)
    assert any("total_game_exposure" in p for p in problems)
    assert validate_decision_record(minimal_record(card_review={**review, "sizing_authority": "repository"}))


def test_i_a_record_from_a_1_0_0_artifact_has_a_null_card_review(tmp_path):
    game = packet(tmp_path)
    evaluation = evaluate_game(game, handicap(), min_net_edge=0.02)
    artifact = build_candidate_artifact(
        "early", [evaluation], {GAME_KEY: game}, {GAME_KEY: handicap()}, min_net_edge=0.02
    )
    legacy = {k: v for k, v in artifact.items() if k != "card_review"} | {
        "schema_version": "cfb_candidate_artifact/1.0.0"
    }
    for candidate in legacy["candidates"]:
        for key in [k for k in candidate if k.startswith("card_") or k.startswith("thesis_")]:
            del candidate[key]
    record = build_decision_record(
        artifact=legacy, reduction_ledger=None, handicaps={}, packets={GAME_KEY: game},
        slate={}, batch_entry=None, source_files={},
    )
    assert validate_decision_record(record) == []
    assert record["card_review"] is None
    assert all(c["card_role"] is None for c in record["candidates"])


# ------------------------------------------------- the Saturday fixture


def test_the_saturday_fixture_makes_the_distinction_from_terms_alone():
    """The postmortem's lesson, as the artifact now states it. No outcome is
    in the fixture, so none can have chosen these labels."""
    reduction = reduce_candidates(saturday())
    review = build_card_review(reduction)

    favourite = review["games"]["FAVGAME"]["theses"]["side:away"]
    assert favourite["core_expressions"] == ["FAV-ML"]
    assert favourite["related_incremental_candidates"] == {"nested_tail_extension": ["FAV-21", "FAV-8"]}
    assert favourite["deepest_tail_extension_by_core"] == {"FAV-ML": "FAV-21"}
    assert favourite["core_loses_when"]["FAV-ML"] == "full game home margin >= 0"

    underdog = review["games"]["DOGGAME"]["theses"]["side:away"]
    assert underdog["related_incremental_candidates"] == {"correlated_independent_cash_path": ["FAV-NOT-10"]}

    for game in ("HALFGAME", "LADDER"):
        assert review["games"][game]["has_nested_tail_extension"] is True
    assert review["slate"]["nested_tail_extension_candidates"] == 4
    assert review["slate"]["games_with_candidates"] == 4
    assert review["slate"]["review_status"] == "pending_final_review"
