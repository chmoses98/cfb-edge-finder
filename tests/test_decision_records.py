"""The private decision record: produced by `candidates`, refused anywhere public,
readable by the postmortem, and attributable to a wager without guessing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cfb_edge_finder.accounting import decision_attribution as da
from cfb_edge_finder.decisions import (
    PRIVATE_MARKER,
    DecisionStore,
    DecisionStoreUnavailable,
    validate_decision_record,
)
from cfb_edge_finder.execution.cli import (
    EXIT_DECISION_RECORD_NOT_PERSISTED,
    EXIT_NO_DECISION_STORE,
    main,
)
from tests.execution_fakes import NOW
from tests.test_execution_workflow import filled_handicaps, two_game_catalog

ROOT = Path(__file__).resolve().parents[1]
POSTMORTEM = ROOT / "scripts" / "cfb_postmortem.py"


def private_store(tmp_path: Path, name: str = "private") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / PRIVATE_MARKER).write_text("")
    return root


def prepared(tmp_path: Path):
    """A slate prepared, evaluated and ready for `candidates`."""
    catalog = two_game_catalog(tmp_path)
    out = tmp_path / "exec"
    assert (
        main(
            [
                "prepare-live",
                "--catalog-dir",
                str(catalog),
                "--out-dir",
                str(out),
                "--as-of",
                NOW.isoformat(),
                "--date",
                "all",
            ]
        )
        == 0
    )
    manifest = json.loads((out / "shard_manifest.json").read_text())
    shard_name = manifest["shards"][0]["shard"]
    shard = json.loads((out / "shards" / f"{shard_name}.json").read_text())
    handicap_file = tmp_path / "handicaps.json"
    handicap_file.write_text(json.dumps(filled_handicaps(shard["games"])))
    assert (
        main(
            [
                "evaluate",
                "--shard",
                shard_name,
                "--out-dir",
                str(out),
                "--handicaps",
                str(handicap_file),
                "--min-edge",
                "0.0",
            ]
        )
        == 0
    )
    return out, shard_name


def candidates(out, shard_name, *extra):
    return main(["candidates", "--shard", shard_name, "--out-dir", str(out), "--min-edge", "0.0", *extra])


# ---------------------------------------------------------------- the store


def test_an_unconfigured_store_fails_closed_before_anything_is_written(tmp_path, monkeypatch):
    monkeypatch.delenv("CFB_DECISION_STORE", raising=False)
    out, shard_name = prepared(tmp_path)
    assert candidates(out, shard_name) == EXIT_NO_DECISION_STORE
    assert not (out / "candidates").exists(), "a shortlist was written with no decision record"


def test_running_without_a_record_has_to_be_said_out_loud(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CFB_DECISION_STORE", raising=False)
    out, shard_name = prepared(tmp_path)
    assert candidates(out, shard_name, "--no-decision-record") == 0
    assert (out / "candidates").exists()
    assert "NO DECISION RECORD" in capsys.readouterr().out


def test_a_store_without_the_private_marker_is_refused(tmp_path):
    root = tmp_path / "unmarked"
    root.mkdir()
    with pytest.raises(DecisionStoreUnavailable) as excinfo:
        DecisionStore.resolve(str(root))
    assert PRIVATE_MARKER in str(excinfo.value)


def test_a_missing_directory_is_refused(tmp_path):
    with pytest.raises(DecisionStoreUnavailable):
        DecisionStore.resolve(str(tmp_path / "nowhere"))


def test_a_store_inside_this_public_repository_is_refused(tmp_path):
    inside = ROOT / "data" / "decisions-test-store"
    inside.mkdir(parents=True, exist_ok=True)
    (inside / PRIVATE_MARKER).write_text("")
    try:
        with pytest.raises(DecisionStoreUnavailable) as excinfo:
            DecisionStore.resolve(str(inside), repo_root=ROOT)
        assert "PUBLIC" in str(excinfo.value)
    finally:
        (inside / PRIVATE_MARKER).unlink()
        inside.rmdir()


def test_a_checkout_of_a_public_repository_is_refused_even_with_the_marker(tmp_path):
    root = private_store(tmp_path)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", "https://github.com/chmoses98/kalshi-bet-router.git"],
        check=True,
    )
    with pytest.raises(DecisionStoreUnavailable) as excinfo:
        DecisionStore.resolve(str(root))
    assert "PUBLIC repository" in str(excinfo.value)


def test_the_environment_variable_configures_the_store(tmp_path, monkeypatch):
    root = private_store(tmp_path)
    monkeypatch.setenv("CFB_DECISION_STORE", str(root))
    assert DecisionStore.resolve(None).root == root.resolve()


# ------------------------------------------------------------- the producer


def test_a_candidates_run_persists_one_record_of_what_it_decided(tmp_path, monkeypatch, capsys):
    root = private_store(tmp_path)
    monkeypatch.setenv("CFB_DECISION_STORE", str(root))
    out, shard_name = prepared(tmp_path)
    assert candidates(out, shard_name) == 0
    written = [p for p in root.rglob("*.json")]
    assert len(written) == 1
    record = json.loads(written[0].read_text())
    assert validate_decision_record(record) == []
    artifact = json.loads((out / "candidates" / f"{shard_name}.candidates.json").read_text())

    # The record says what the artifact said, at the moment it said it.
    assert record["artifact_generated_at"] == artifact["generated_at"]
    assert [c["market_ticker"] for c in record["candidates"]] == [c["market"] for c in artifact["candidates"]]
    assert all(c["selected"] is True for c in record["candidates"])
    first, first_artifact = record["candidates"][0], artifact["candidates"][0]
    assert first["observed_price"] == first_artifact["kalshi_executable_price"]
    assert first["bet_up_to"] == first_artifact["bet_up_to_price"]
    assert first["tier"] == first_artifact["robustness"]
    assert first["recommendation_id"] == first_artifact["recommendation_id"]
    assert first["recommended_stake"] is None, "nothing sizes a bet; a stake would be invented"
    assert record["bankroll_context"] is None
    # The theses are the handicapper's own words.
    game = record["games"][first["game_key"]]
    assert game["thesis"] == "a test thesis"
    assert game["opposing_case"] == "a test counterargument"
    assert game["handicap_payload"]["period_distributions"]
    # Alternatives that were evaluated and not chosen are distinguishable.
    assert all(e["selected"] is False for e in record["evaluated_not_selected"])
    assert record["reduction"]["removed"] == len(record["evaluated_not_selected"])
    assert "decision record:" in capsys.readouterr().out


def test_the_record_id_is_stable_for_the_same_decision_and_differs_for_a_new_one(tmp_path, monkeypatch):
    root = private_store(tmp_path)
    monkeypatch.setenv("CFB_DECISION_STORE", str(root))
    out, shard_name = prepared(tmp_path)
    assert candidates(out, shard_name) == 0
    first = [json.loads(p.read_text()) for p in root.rglob("*.json")][0]
    identity = {k: first[k] for k in ("slate_date", "batch", "shard", "artifact_generated_at")}
    assert identity["shard"] == shard_name
    # A second run generates a new artifact (new generated_at) -> a new record, not an overwrite.
    assert candidates(out, shard_name) == 0
    records = sorted(root.rglob("*.json"))
    assert len(records) == 2
    ids = {json.loads(p.read_text())["record_id"] for p in records}
    assert len(ids) == 2


def test_a_failed_write_after_the_artifact_is_loud_and_non_zero(tmp_path, monkeypatch):
    root = private_store(tmp_path)
    monkeypatch.setenv("CFB_DECISION_STORE", str(root))
    out, shard_name = prepared(tmp_path)

    def broken_write(self, record, **_kwargs):
        raise DecisionStoreUnavailable("disk full")

    monkeypatch.setattr(DecisionStore, "write", broken_write)
    assert candidates(out, shard_name) == EXIT_DECISION_RECORD_NOT_PERSISTED
    assert (out / "candidates" / f"{shard_name}.candidates.json").exists()
    assert list(root.rglob("*.json")) == []


def test_the_write_is_atomic_and_never_overwrites_a_different_record(tmp_path):
    root = private_store(tmp_path)
    store = DecisionStore(root)
    record = minimal_record()
    path = store.write(record)
    assert path.exists() and not list(root.rglob(".tmp-*"))
    assert store.write(record) == path  # same record: a no-op, one file
    assert len(list(root.rglob("*.json"))) == 1
    # A different decision lands in a different file; nothing is overwritten.
    other = {**record, "record_id": "different-record-id"}
    other_path = store.write(other)
    assert other_path != path and path.exists() and other_path.exists()
    assert json.loads(path.read_text())["record_id"] == "r1"
    # ...and a file whose content is not the record its name claims is refused
    # rather than replaced.
    path.write_text(json.dumps({**record, "record_id": "tampered"}))
    with pytest.raises(DecisionStoreUnavailable):
        store.write(record)


def test_git_sync_failure_is_reported_not_swallowed(tmp_path, monkeypatch):
    root = private_store(tmp_path)
    store = DecisionStore(root)
    with pytest.raises(DecisionStoreUnavailable) as excinfo:
        store.write(minimal_record(), git_sync=True)
    assert "NOT yet durable" in str(excinfo.value)


def test_an_unsupported_schema_version_is_not_read_as_a_record(tmp_path):
    root = private_store(tmp_path)
    (root / "old.json").write_text(json.dumps({**minimal_record(), "schema_version": "cfb_decision_record/0.9.0"}))
    store = DecisionStore(root)
    assert list(store.records()) == []
    assert any("not supported" in p for p in store.problems())


def test_the_public_repository_holds_no_decision_record():
    """No file under the repository carries the decision-record schema tag."""
    hits = []
    for path in ROOT.rglob("*.json"):
        if ".git" in path.parts or "node_modules" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        record_markers = (
            '"schema_version": "cfb_decision_record/',
            '"producer": "cfb_edge_finder.execution candidates"',
        )
        if any(marker in text for marker in record_markers):
            hits.append(str(path))
    assert hits == []


# ------------------------------------------------------------ attribution


def minimal_record(**overrides):
    record = {
        "schema_version": "cfb_decision_record/1.0.0",
        "record_id": "r1",
        "created_at": "2026-09-26T13:40:00+00:00",
        "producer": "cfb_edge_finder.execution candidates",
        "slate_date": "2026-09-26",
        "batch": "early_b1",
        "shard": "early",
        "kickoff_window": "early",
        "bankroll_context": None,
        "games": {
            "26SEP26UCLAMD": {
                "game": "UCLA at Maryland",
                "kickoff": "2026-09-26T17:30:00Z",
                "thesis": "both offences, weak secondaries",
                "opposing_case": "wind",
                "effective_confidence": "medium",
                "data_quality_ceiling": "high",
                "packet_hash": "p1",
            }
        },
        "candidates": [
            {
                "recommendation_id": "rec-1",
                "game_key": "26SEP26UCLAMD",
                "market_ticker": "KXNCAAFTOTAL-26SEP26UCLAMD-57",
                "side": "YES",
                "observed_price": 0.50,
                "observed_at": "2026-09-26T13:39:00Z",
                "fair_probability": 0.58,
                "edge": 0.06,
                "bet_up_to": 0.53,
                "confidence": "medium",
                "tier": "robust_positive_ev",
                "recommended_stake": None,
                "selected": True,
            }
        ],
        "evaluated_not_selected": [
            {
                "market_ticker": "KXNCAAFTOTAL-26SEP26UCLAMD-55",
                "game_key": "26SEP26UCLAMD",
                "side": "YES",
                "edge": 0.04,
                "observed_price": 0.52,
                "removal_reason": "dominated_duplicate",
                "lost_to": "KXNCAAFTOTAL-26SEP26UCLAMD-57",
                "selected": False,
            }
        ],
        "source": {},
    }
    record.update(overrides)
    return record


def wager(**overrides):
    row = {
        "source_bet_key": "kalshi:v1:ucla",
        "market_ticker": "KXNCAAFTOTAL-26SEP26UCLAMD-57",
        "side": "YES",
        "game_date": "2026-09-26",
        "executed_at": "2026-09-26T15:45:04Z",
        "execution_price": 0.49,
        "stake": 31.9975,
    }
    row.update(overrides)
    return row


def test_an_exact_match_exposes_the_decision_beside_the_execution():
    result = da.attribute([wager()], [minimal_record()])
    assert result.counts == {"matched": 1, "unmatched": 0, "ambiguous": 0}
    entry = result.entries[0]
    assert entry["thesis"] == "both offences, weak secondaries"
    assert entry["opposing_case"] == "wind"
    assert entry["bet_up_to"] == 0.53
    assert entry["observed_price"] == 0.50
    assert entry["execution_price"] == 0.49
    assert entry["price_delta"] == pytest.approx(-0.01)
    assert entry["inside_bet_up_to"] is True
    assert entry["tier"] == "robust_positive_ev" and entry["confidence"] == "medium"
    assert entry["recommended_stake"] is None and entry["actual_stake"] == 31.9975
    assert [a["market_ticker"] for a in entry["alternatives"]] == ["KXNCAAFTOTAL-26SEP26UCLAMD-55"]
    assert entry["alternatives"][0]["selected"] is False
    assert result.matches.counts == {"recommended_and_executed": 1}


def test_a_fill_above_the_bet_up_to_is_matched_and_named():
    result = da.attribute([wager(execution_price=0.55)], [minimal_record()])
    assert result.entries[0]["state"] == "matched"
    assert result.entries[0]["inside_bet_up_to"] is False
    assert result.matches.counts == {"executed_above_bet_up_to": 1}


def test_a_wager_with_no_record_is_unmatched_with_the_reason():
    result = da.attribute([wager(market_ticker="KXNCAAFSPREAD-26SEP26VANAUB-AUB8")], [minimal_record()])
    assert result.counts["unmatched"] == 1
    assert "no decision record" in result.entries[0]["reason"]
    assert result.matches.counts == {"executed_not_recommended": 1, "recommended_not_executed": 1}


def test_a_record_created_after_the_order_cannot_have_informed_it():
    late = minimal_record(record_id="late", created_at="2026-09-26T16:00:00+00:00")
    result = da.attribute([wager()], [late])
    assert result.counts["unmatched"] == 1
    assert "newer than the order" in result.entries[0]["reason"]


def test_a_record_created_after_kickoff_is_rejected_as_a_pre_game_decision():
    post = minimal_record(record_id="post", created_at="2026-09-26T17:45:00+00:00")
    result = da.attribute([wager(executed_at="2026-09-26T17:50:00Z")], [post])
    assert result.counts["unmatched"] == 1
    assert result.rejected_post_kickoff == 1
    assert "after kickoff" in result.entries[0]["reason"]


def test_two_runs_of_the_same_game_resolve_to_the_nearest_earlier_run():
    early = minimal_record(record_id="early", created_at="2026-09-26T11:00:00+00:00")
    later = minimal_record(record_id="later", created_at="2026-09-26T14:30:00+00:00")
    result = da.attribute([wager()], [early, later])
    assert result.counts["matched"] == 1
    assert result.entries[0]["record_id"] == "later"
    assert result.entries[0]["runs_considered"] == 2


def test_two_runs_at_the_same_instant_are_ambiguous_not_guessed():
    a = minimal_record(record_id="a")
    b = minimal_record(record_id="b")
    result = da.attribute([wager()], [a, b])
    assert result.counts["ambiguous"] == 1
    assert sorted(result.entries[0]["record_ids"]) == ["a", "b"]
    assert result.matches.counts["ambiguous_match"] == 1
    # Neither candidate is consumed by an ambiguity: both stay visible as
    # decisions nobody is credited with acting on.
    assert result.matches.counts["recommended_not_executed"] == 2


def test_the_same_ticker_twice_in_one_record_is_ambiguous():
    record = minimal_record()
    record["candidates"].append({**record["candidates"][0], "recommendation_id": "rec-2", "bet_up_to": 0.60})
    result = da.attribute([wager()], [record])
    assert result.counts["ambiguous"] == 1


def test_a_different_side_of_the_same_market_is_not_the_same_decision():
    result = da.attribute([wager(side="NO")], [minimal_record()])
    assert result.counts["unmatched"] == 1


def test_null_optional_fields_are_carried_as_null_not_filled():
    record = minimal_record()
    for name in ("bet_up_to", "fair_probability", "edge", "tier", "confidence", "observed_price"):
        record["candidates"][0][name] = None
    record["games"]["26SEP26UCLAMD"]["thesis"] = None
    result = da.attribute([wager()], [record])
    entry = result.entries[0]
    assert entry["state"] == "matched"
    assert entry["bet_up_to"] is None and entry["inside_bet_up_to"] is None
    assert entry["price_delta"] is None and entry["thesis"] is None and entry["tier"] is None


def test_the_wager_rows_are_never_written():
    rows = [wager()]
    before = json.dumps(rows, sort_keys=True)
    da.attribute(rows, [minimal_record()])
    assert json.dumps(rows, sort_keys=True) == before


def test_an_old_schema_version_is_not_attributed_from():
    root_records = [{**minimal_record(), "schema_version": "cfb_decision_record/0.1.0"}]
    # The store filters unsupported versions before they reach attribution;
    # attribution itself only ever sees supported records. Prove the store does.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / PRIVATE_MARKER).write_text("")
        (root / "x.json").write_text(json.dumps(root_records[0]))
        assert list(DecisionStore(root).records()) == []


def test_the_postmortem_reads_the_private_store_and_moves_no_money(tmp_path):
    from cfb_edge_finder.accounting import store as ledger_store

    base = tmp_path / "ledger"
    ledger_store.append_wagers(
        base,
        2026,
        [
            {
                **wager(),
                "wager_id": "routed-x",
                "schema_version": "cfb_accounted_wager.v1",
                "import_batch_id": "kalshi-router-v1",
                "entry_method": "IMPORTED_RECEIPT",
                "contracts": 63.05,
                "fees_paid": 1.103,
                "fees_are_estimated": False,
                "venue": "kalshi",
                "season": 2026,
            }
        ],
    )
    ledger_store.append_settlements(
        base,
        2026,
        [
            {
                "settlement_id": "stl-x",
                "schema_version": "cfb_wager_settlement.v1",
                "source_bet_key": "kalshi:v1:ucla",
                "market_ticker": "KXNCAAFTOTAL-26SEP26UCLAMD-57",
                "side": "YES",
                "settlement_status": "SETTLED",
                "settled_at": "2026-09-26T20:59:16Z",
                "result": "WON",
                "gross_return": 63.05,
                "net_profit_loss": 29.9495,
                "refusals": [],
                "venue": "kalshi",
            }
        ],
    )
    root = private_store(tmp_path)
    DecisionStore(root).write(minimal_record())

    bare = subprocess.run(
        [
            sys.executable,
            str(POSTMORTEM),
            "--base-dir",
            str(base),
            "--season",
            "2026",
            "--json",
            str(tmp_path / "bare.json"),
        ],
        capture_output=True,
        text=True,
    )
    cut = subprocess.run(
        [
            sys.executable,
            str(POSTMORTEM),
            "--base-dir",
            str(base),
            "--season",
            "2026",
            "--decisions",
            str(root),
            "--json",
            str(tmp_path / "cut.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert bare.returncode == 0 and cut.returncode == 0, (bare.stderr, cut.stderr)
    bare_doc = json.loads((tmp_path / "bare.json").read_text())
    cut_doc = json.loads((tmp_path / "cut.json").read_text())
    assert bare_doc["overall"] == cut_doc["overall"], "attribution moved the money"
    assert cut_doc["decision_attribution"]["counts"] == {"matched": 1, "unmatched": 0, "ambiguous": 0}
    assert cut_doc["decision_attribution"]["wagers"][0]["thesis"] == "both offences, weak secondaries"
    assert {row["label"] for row in cut_doc["by_robustness_tier"]} == {"robust_positive_ev"}
    assert "decision-record attribution" in cut.stdout
    assert "matched / unmatched / ambiguous: 1 / 0 / 0" in cut.stdout


def test_the_postmortem_refuses_an_unusable_decision_store(tmp_path):
    base = tmp_path / "ledger"
    from cfb_edge_finder.accounting import store as ledger_store

    ledger_store.append_wagers(
        base,
        2026,
        [
            {
                **wager(),
                "wager_id": "routed-x",
                "schema_version": "cfb_accounted_wager.v1",
                "import_batch_id": "kalshi-router-v1",
                "entry_method": "IMPORTED_RECEIPT",
                "contracts": 63.05,
                "fees_paid": 1.103,
                "fees_are_estimated": False,
                "venue": "kalshi",
                "season": 2026,
            }
        ],
    )
    result = subprocess.run(
        [
            sys.executable,
            str(POSTMORTEM),
            "--base-dir",
            str(base),
            "--season",
            "2026",
            "--decisions",
            str(tmp_path / "missing"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "decision store" in result.stderr


# ------------------------------------------------- decision-store init / check
#
# The owner's one-time setup is ONE verified command, and "is the store
# configured on THIS machine?" has an answer that needs no slate.


def test_decision_store_init_creates_a_marked_store_outside_the_repository(tmp_path, capsys):
    target = tmp_path / "private-cfb-decisions"
    assert main(["decision-store", "init", str(target)]) == 0
    assert (target / PRIVATE_MARKER).is_file()
    out = capsys.readouterr().out
    assert f"decision store: {target.resolve()}" in out
    assert "records:  0" in out
    assert f"export CFB_DECISION_STORE={target.resolve()}" in out


def test_decision_store_init_is_idempotent(tmp_path):
    target = tmp_path / "private-cfb-decisions"
    assert main(["decision-store", "init", str(target)]) == 0
    marker_bytes = (target / PRIVATE_MARKER).read_bytes()
    assert main(["decision-store", "init", str(target)]) == 0
    assert (target / PRIVATE_MARKER).read_bytes() == marker_bytes


def test_decision_store_init_refuses_a_path_inside_the_repository_and_creates_nothing(capsys):
    inside = ROOT / "data" / "a-private-store-attempt"
    assert not inside.exists()
    try:
        assert main(["decision-store", "init", str(inside)]) == EXIT_NO_DECISION_STORE
        assert not inside.exists(), "a refused init must leave no directory behind"
        assert "PUBLIC" in capsys.readouterr().err
    finally:
        if inside.exists():
            (inside / PRIVATE_MARKER).unlink(missing_ok=True)
            inside.rmdir()


def test_decision_store_init_refuses_a_checkout_of_a_public_repository(tmp_path, capsys):
    clone = tmp_path / "clone"
    clone.mkdir()
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    subprocess.run(
        ["git", "-C", str(clone), "remote", "add", "origin", "https://github.com/chmoses98/kalshi-bet-router.git"],
        check=True,
    )
    target = clone / "decisions"
    assert main(["decision-store", "init", str(target)]) == EXIT_NO_DECISION_STORE
    assert not target.exists()
    assert "PUBLIC" in capsys.readouterr().err


def test_decision_store_check_fails_closed_when_nothing_is_configured(monkeypatch, capsys):
    monkeypatch.delenv("CFB_DECISION_STORE", raising=False)
    assert main(["decision-store", "check"]) == EXIT_NO_DECISION_STORE
    assert "no private decision store is configured" in capsys.readouterr().err


def test_decision_store_check_reads_the_configured_store(tmp_path, monkeypatch, capsys):
    root = private_store(tmp_path)
    monkeypatch.setenv("CFB_DECISION_STORE", str(root))
    assert main(["decision-store", "check"]) == 0
    out = capsys.readouterr().out
    assert f"decision store: {root.resolve()}" in out
    assert "CFB_DECISION_STORE points here in this shell" in out


def test_decision_store_check_counts_the_records_a_real_run_wrote(tmp_path, monkeypatch, capsys):
    root = private_store(tmp_path)
    monkeypatch.setenv("CFB_DECISION_STORE", str(root))
    out, shard_name = prepared(tmp_path)
    assert candidates(out, shard_name) == 0
    assert main(["decision-store", "check"]) == 0
    text = capsys.readouterr().out
    assert "records:  1" in text
    assert "problems: 0" in text


def test_decision_store_check_refuses_a_store_without_a_marker(tmp_path, monkeypatch, capsys):
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.setenv("CFB_DECISION_STORE", str(bare))
    assert main(["decision-store", "check"]) == EXIT_NO_DECISION_STORE
    assert PRIVATE_MARKER in capsys.readouterr().err
