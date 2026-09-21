"""Durable state, resume, and the end-to-end CLI workflow.

The property under test is the one the whole design exists for: a
handicapping session that dies halfway through must resume, and a
shortlist must be impossible until every game in the shard has closed its
books.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from cfb_edge_finder.execution.cli import main
from cfb_edge_finder.execution.evaluator import evaluate_game
from cfb_edge_finder.execution.report import ShardGateError, build_report, correlation_review
from cfb_edge_finder.execution.slate import build_slate
from cfb_edge_finder.execution.state import ExecutionState, StateStore, next_incomplete
from tests.execution_fakes import CAPTURED_AT, NOW, catalog_dir, standard_markets
from tests.test_execution_disposition import config
from tests.test_execution_evaluator import handicap

SECOND_GAME = "26SEP19AAABBB"


def two_game_catalog(tmp_path: Path) -> Path:
    return catalog_dir(
        tmp_path,
        kickoff="2026-09-19T16:00:00Z",
        extra_games=[
            (SECOND_GAME, "LSU at Ole Miss", "2026-09-19T16:30:00Z", standard_markets(SECOND_GAME))
        ],
    )


def filled_handicaps(packets) -> dict:
    payloads = []
    for packet in packets:
        payloads.append(
            {
                "schema_version": "cfb_handicap_payload/1.0.0",
                "game_key": packet["game_key"],
                "packet_hash": packet["packet_hash"],
                "teams": {"home": "Ole Miss", "away": "LSU"},
                "confidence": "test",
                "thesis": "a test thesis",
                "opposing_case": "a test counterargument",
                "period_distributions": {
                    "full_game": {
                        "home_mean": 30.0,
                        "away_mean": 20.0,
                        "home_sd": 10.5,
                        "away_sd": 10.0,
                        "correlation": 0.1,
                    }
                },
                "explicit_probabilities": {},
            }
        )
    return {"handicaps": payloads}


# ------------------------------------------------------------- state


def test_state_starts_pending_and_records_a_handicap(tmp_path):
    packets = build_slate(two_game_catalog(tmp_path), config(), slate_date=None).packets
    store = StateStore(tmp_path / "state")
    state = store.reconcile_with_packet(packets[0])
    assert state.state == ExecutionState.PENDING_HANDICAP.value

    state = store.record_handicap(packets[0], handicap(game_key=packets[0]["game_key"]))
    assert state.state == ExecutionState.HANDICAP_COMPLETE.value
    assert state.has_usable_handicap
    assert store.load(packets[0]["game_key"]).has_usable_handicap


def test_a_completed_evaluation_is_durable(tmp_path):
    packets = build_slate(two_game_catalog(tmp_path), config(), slate_date=None).packets
    store = StateStore(tmp_path / "state")
    game = packets[0]
    store.record_handicap(game, handicap(game_key=game["game_key"]))
    evaluation = evaluate_game(game, handicap(game_key=game["game_key"]))
    store.record_evaluation(game, evaluation)

    reloaded = StateStore(tmp_path / "state").load(game["game_key"])
    assert reloaded.state == ExecutionState.EVALUATION_COMPLETE.value
    assert reloaded.eligible_contracts == evaluation.eligible
    assert reloaded.evaluated_contracts == evaluation.evaluated
    assert reloaded.unpriceable_contracts == evaluation.unpriceable
    assert reloaded.unaccounted_contracts == 0
    assert reloaded.completed_at


def test_resume_skips_valid_completed_games(tmp_path):
    packets = build_slate(two_game_catalog(tmp_path), config(), slate_date=None).packets
    store = StateStore(tmp_path / "state")
    first, second = packets

    assert next_incomplete(packets, store)["game_key"] == first["game_key"]

    store.record_handicap(first, handicap(game_key=first["game_key"]))
    store.record_evaluation(first, evaluate_game(first, handicap(game_key=first["game_key"])))

    assert next_incomplete(packets, store)["game_key"] == second["game_key"]


def test_a_changed_quote_invalidates_a_completed_evaluation(tmp_path):
    """The handicap survives -- it is about the game. The evaluation does
    not: every price it was computed against has moved."""
    packets = build_slate(two_game_catalog(tmp_path), config(), slate_date=None).packets
    game = packets[0]
    store = StateStore(tmp_path / "state")
    store.record_handicap(game, handicap(game_key=game["game_key"]))
    store.record_evaluation(game, evaluate_game(game, handicap(game_key=game["game_key"])))

    moved = json.loads(json.dumps(game))
    moved["contracts"][0]["executable"]["yes"]["entry"] = 0.77
    moved["packet_hash"] = "a-different-hash"

    state = store.reconcile_with_packet(moved)
    assert state.state == ExecutionState.STALE_DUE_TO_INPUT_CHANGE.value
    assert state.evaluated_contracts is None
    assert state.completed_at is None
    assert state.has_usable_handicap
    assert state.handicap_status == "complete"
    assert "quotes moved" in state.invalidation_reason


def test_a_changed_contract_set_also_flags_the_handicap_for_review(tmp_path):
    packets = build_slate(two_game_catalog(tmp_path), config(), slate_date=None).packets
    game = packets[0]
    store = StateStore(tmp_path / "state")
    store.record_handicap(game, handicap(game_key=game["game_key"]))
    store.record_evaluation(game, evaluate_game(game, handicap(game_key=game["game_key"])))

    changed = json.loads(json.dumps(game))
    changed["contracts"] = changed["contracts"][:-1]
    changed["packet_hash"] = "another-hash"

    state = store.reconcile_with_packet(changed)
    assert state.state == ExecutionState.STALE_DUE_TO_INPUT_CHANGE.value
    assert state.handicap_status == "complete_needs_review"
    assert "eligible contract set changed" in state.invalidation_reason


def test_an_unchanged_packet_is_reused_exactly(tmp_path):
    packets = build_slate(two_game_catalog(tmp_path), config(), slate_date=None).packets
    game = packets[0]
    store = StateStore(tmp_path / "state")
    store.record_handicap(game, handicap(game_key=game["game_key"]))
    store.record_evaluation(game, evaluate_game(game, handicap(game_key=game["game_key"])))
    state = store.reconcile_with_packet(game)
    assert state.is_complete
    assert state.invalidation_reason is None


# ------------------------------------------------------------- report


def test_the_shortlist_cannot_be_generated_before_the_gate_passes(tmp_path):
    packets = build_slate(two_game_catalog(tmp_path), config(), slate_date=None).packets
    complete = evaluate_game(packets[0], handicap(game_key=packets[0]["game_key"]))
    partial = evaluate_game(packets[1], handicap(game_key=packets[1]["game_key"]))
    partial.rows.pop()
    from cfb_edge_finder.execution.evaluator import reconcile

    reconcile(packets[1], partial)

    with pytest.raises(ShardGateError) as excinfo:
        build_report(
            "early",
            [complete, partial],
            {p["game_key"]: p for p in packets},
            {},
            min_net_edge=0.02,
            games_discovered=2,
            contracts_discovered=34,
            mechanical_exclusions=0,
        )
    assert "INCOMPLETE" in str(excinfo.value)


def test_the_report_distinguishes_evaluated_from_selected(tmp_path):
    packets = build_slate(two_game_catalog(tmp_path), config(), slate_date=None).packets
    payloads = {p["game_key"]: handicap(game_key=p["game_key"]) for p in packets}
    evaluations = [evaluate_game(p, payloads[p["game_key"]], min_net_edge=0.0) for p in packets]
    report = build_report(
        "early",
        evaluations,
        {p["game_key"]: p for p in packets},
        payloads,
        min_net_edge=0.0,
        games_discovered=2,
        contracts_discovered=sum(p["counts"]["discovered"] for p in packets),
        mechanical_exclusions=0,
        top_n=3,
    )
    totals = report["totals"]
    assert totals["eligible_contracts"] == sum(e.eligible for e in evaluations)
    assert totals["positive_ev_contracts"] >= totals["passed_correlation_review"]
    assert totals["passed_correlation_review"] >= totals["final_bets_selected"]
    assert totals["final_bets_selected"] == len(report["final_bets"]) <= 3
    assert totals["unaccounted"] == 0
    assert "survivors of" in report["provenance"]


def test_every_final_bet_carries_the_fields_an_operator_needs(tmp_path):
    packets = build_slate(two_game_catalog(tmp_path), config(), slate_date=None).packets
    payloads = {
        p["game_key"]: handicap(
            game_key=p["game_key"], thesis="the thesis", opposing_case="the other side"
        )
        for p in packets
    }
    evaluations = [evaluate_game(p, payloads[p["game_key"]], min_net_edge=0.0) for p in packets]
    report = build_report(
        "early",
        evaluations,
        {p["game_key"]: p for p in packets},
        payloads,
        min_net_edge=0.0,
        games_discovered=2,
        contracts_discovered=sum(p["counts"]["discovered"] for p in packets),
        mechanical_exclusions=0,
    )
    assert report["final_bets"]
    for bet in report["final_bets"]:
        for key in (
            "game",
            "game_key",
            "market",
            "side",
            "kalshi_executable_price",
            "implied_probability",
            "fair_probability",
            "raw_edge",
            "fee_adjusted_edge",
            "stake_placeholder",
            "why_this_market_expresses_the_thesis",
        ):
            assert key in bet, key
        assert bet["stake_placeholder"] is None
        # The thesis and the opposing case are identical on every bet from one
        # game, so they are stated once in `games_context` and joined on
        # game_key -- compaction, not omission.
        game = report["games_context"][bet["game_key"]]
        assert game["thesis"] == "the thesis"
        assert game["strongest_opposing_case"] == "the other side"


def test_the_correlation_review_keeps_the_best_expression_of_one_view():
    rows = [
        {"ticker": "A", "game_key": "G", "kind": "spread", "period": "full_game",
         "team": "home", "best_side": "yes", "net_edge": 0.05},
        {"ticker": "B", "game_key": "G", "kind": "spread", "period": "full_game",
         "team": "away", "best_side": "no", "net_edge": 0.09},
        {"ticker": "C", "game_key": "G", "kind": "total", "period": "full_game",
         "team": "none", "best_side": "yes", "net_edge": 0.03},
    ]
    survivors, dropped = correlation_review(rows)
    assert {c.row["ticker"] for c in survivors} == {"B", "C"}
    assert [d["ticker"] for d in dropped] == ["A"]
    # The reason is now a deterministic REDUCTION REASON rather than a
    # sentence, and the pair it names is what makes the ledger auditable.
    assert dropped[0]["reason"] == "dominated_duplicate"
    assert dropped[0]["lost_to"] == "B"
    assert "same view" in dropped[0]["explanation"]


# ---------------------------------------------------------------- CLI


def run(args) -> int:
    return main(args)


def test_the_cli_walks_the_whole_workflow(tmp_path, capsys):
    catalog = two_game_catalog(tmp_path)
    out = tmp_path / "exec"

    assert run([
        "prepare-live",
        "--catalog-dir", str(catalog),
        "--out-dir", str(out),
        "--as-of", NOW.isoformat(),
        "--date", "all",
    ]) == 0
    slate = json.loads((out / "cfb_execution_slate.json").read_text())
    assert slate["reconciliation"]["balanced"] is True
    manifest = json.loads((out / "shard_manifest.json").read_text())
    assert manifest["reconciles"] is True

    shard_name = manifest["shards"][0]["shard"]
    shard = json.loads((out / "shards" / f"{shard_name}.json").read_text())

    # the gate is shut before any handicap exists
    assert run(["report", "--shard", shard_name, "--out-dir", str(out)]) == 3

    handicap_file = tmp_path / "handicaps.json"
    handicap_file.write_text(json.dumps(filled_handicaps(shard["games"])))

    assert run([
        "evaluate", "--shard", shard_name, "--out-dir", str(out),
        "--handicaps", str(handicap_file), "--min-edge", "0.0",
    ]) == 0
    ledger = json.loads((out / "ledgers" / f"{shard_name}.json").read_text())
    assert ledger["shard_complete"] is True
    assert ledger["totals"]["unaccounted_contracts"] == 0
    assert (
        ledger["totals"]["eligible_contracts"]
        == ledger["totals"]["evaluated_contracts"] + ledger["totals"]["unpriceable_contracts"]
    )
    assert len(ledger["contracts"]) == ledger["totals"]["eligible_contracts"]

    # a second run reuses the durable state rather than re-pricing
    assert run(["evaluate", "--shard", shard_name, "--out-dir", str(out), "--min-edge", "0.0"]) == 0
    reused = json.loads((out / "ledgers" / f"{shard_name}.json").read_text())
    assert len(reused["games_reused_from_state"]) == len(shard["games"])

    assert run([
        "report", "--shard", shard_name, "--out-dir", str(out), "--top", "2", "--min-edge", "0.0"
    ]) == 0
    report = json.loads((out / "reports" / f"{shard_name}.json").read_text())
    assert report["totals"]["unaccounted"] == 0
    assert len(report["final_bets"]) <= 2
    assert (out / "reports" / f"{shard_name}.txt").exists()


def test_the_cli_refuses_a_handicap_whose_teams_do_not_match(tmp_path):
    catalog = two_game_catalog(tmp_path)
    out = tmp_path / "exec"
    run([
        "prepare-live", "--catalog-dir", str(catalog), "--out-dir", str(out),
        "--as-of", NOW.isoformat(), "--date", "all",
    ])
    manifest = json.loads((out / "shard_manifest.json").read_text())
    shard_name = manifest["shards"][0]["shard"]
    shard = json.loads((out / "shards" / f"{shard_name}.json").read_text())

    flipped = filled_handicaps(shard["games"])
    flipped["handicaps"][0]["teams"] = {"home": "LSU", "away": "Ole Miss"}
    path = tmp_path / "flipped.json"
    path.write_text(json.dumps(flipped))

    assert run([
        "evaluate", "--shard", shard_name, "--out-dir", str(out), "--handicaps", str(path)
    ]) == 2


def test_the_cli_refuses_a_handicap_built_against_a_different_packet(tmp_path):
    catalog = two_game_catalog(tmp_path)
    out = tmp_path / "exec"
    run([
        "prepare-live", "--catalog-dir", str(catalog), "--out-dir", str(out),
        "--as-of", NOW.isoformat(), "--date", "all",
    ])
    manifest = json.loads((out / "shard_manifest.json").read_text())
    shard_name = manifest["shards"][0]["shard"]
    shard = json.loads((out / "shards" / f"{shard_name}.json").read_text())

    stale = filled_handicaps(shard["games"])
    stale["handicaps"][0]["packet_hash"] = "0" * 64
    path = tmp_path / "stale.json"
    path.write_text(json.dumps(stale))

    assert run([
        "evaluate", "--shard", shard_name, "--out-dir", str(out), "--handicaps", str(path)
    ]) == 2
    assert run([
        "evaluate", "--shard", shard_name, "--out-dir", str(out), "--handicaps", str(path),
        "--allow-hash-mismatch", "--min-edge", "0.0",
    ]) == 0


def test_evaluate_reports_pending_games_and_keeps_the_gate_shut(tmp_path):
    catalog = two_game_catalog(tmp_path)
    out = tmp_path / "exec"
    run([
        "prepare-live", "--catalog-dir", str(catalog), "--out-dir", str(out),
        "--as-of", NOW.isoformat(), "--date", "all",
    ])
    manifest = json.loads((out / "shard_manifest.json").read_text())
    shard_name = manifest["shards"][0]["shard"]
    shard = json.loads((out / "shards" / f"{shard_name}.json").read_text())

    partial = filled_handicaps(shard["games"][:1])
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(partial))

    assert run([
        "evaluate", "--shard", shard_name, "--out-dir", str(out), "--handicaps", str(path)
    ]) == 3
    ledger = json.loads((out / "ledgers" / f"{shard_name}.json").read_text())
    assert ledger["shard_complete"] is False
    assert ledger["games_pending_handicap"]
    assert run(["report", "--shard", shard_name, "--out-dir", str(out)]) == 3


def test_prepare_live_fails_closed_on_a_stale_capture(tmp_path, capsys):
    """Published with its diagnostics, and non-zero: a slate nobody can
    bet is not a success, and a scripted caller must not read it as one."""
    catalog = catalog_dir(tmp_path, captured_at=CAPTURED_AT - timedelta(hours=8))
    out = tmp_path / "exec"
    assert run([
        "prepare-live", "--catalog-dir", str(catalog), "--out-dir", str(out),
        "--as-of", NOW.isoformat(), "--date", "all",
    ]) == 4
    slate = json.loads((out / "cfb_execution_slate.json").read_text())
    assert slate["reconciliation"]["contracts_eligible"] == 0
    assert "stale_quote" in slate["reconciliation"]["exclusions_by_status"]
    assert "WARNING" in capsys.readouterr().err


def test_handicap_template_covers_every_game_in_the_shard(tmp_path):
    catalog = two_game_catalog(tmp_path)
    out = tmp_path / "exec"
    run([
        "prepare-live", "--catalog-dir", str(catalog), "--out-dir", str(out),
        "--as-of", NOW.isoformat(), "--date", "all",
    ])
    manifest = json.loads((out / "shard_manifest.json").read_text())
    shard_name = manifest["shards"][0]["shard"]
    assert run(["handicap-template", "--shard", shard_name, "--out-dir", str(out)]) == 0
    template = json.loads(
        (out / "templates" / f"{shard_name}.handicap_template.json").read_text()
    )
    shard = json.loads((out / "shards" / f"{shard_name}.json").read_text())
    assert {h["game_key"] for h in template["handicaps"]} == {
        g["game_key"] for g in shard["games"]
    }


def test_the_execution_package_places_no_orders():
    """A structural check, not a promise in a docstring.

    Docstrings are excluded deliberately: this package's own module
    docstring says it can never reach an order or portfolio endpoint, and
    a plain text scan flags that sentence as the very thing it promises
    not to do. Only executable code counts -- the same rule
    `tests/test_catalog_schema_and_isolation.py` uses."""
    import ast

    import cfb_edge_finder.execution as package

    root = Path(package.__file__).parent
    forbidden = ("place_order", "create_order", "portfolio", "/orders", "api_key", "private_key")
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                first = (node.body or [None])[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstrings.add(id(first.value))
            # A bare string statement after an assignment is the attribute-docstring
            # idiom this repository uses throughout.
            body = getattr(node, "body", None)
            for statement in body if isinstance(body, list) else []:
                if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
                    docstrings.add(id(statement.value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                for token in forbidden:
                    assert token not in node.value.lower(), f"{path.name}: {token}"
