"""The vendored execution-evidence ledger (copied verbatim from chmoses98/kalshi-bet-router).

Pure-Python and self-contained: it pins the vendored file's digest and exercises the properties this repository relies on --
digests only, append-only, idempotent, never rewritten.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "accounting" / "execution_evidence_ledger.py"
PINNED_SHA256 = "e9ea7f128d72be7e0f8c15f92c68ae7a7325cf06da78dde54506061472d51945"

KEY = "kalshi:v1:" + "ab" * 32
D1, D2 = "a" * 32, "b" * 32


def _load():
    spec = importlib.util.spec_from_file_location("execution_evidence_ledger_vendored", MODULE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


L = _load()


def _execution(**over):
    row = {"schema_version": L.SCHEMA_VERSION, "kind": "EXECUTION", "producer": "router", "source_bet_key": KEY,
           "order_id_digest": D1, "fill_id_digests": [D2], "fill_count": 1, "first_fill_ts": 1_790_000_000,
           "last_fill_ts": 1_790_000_001, "execution_action": "BUY", "all_taker": True, "flags": []}
    row.update(over)
    return row


def test_vendored_copy_is_byte_identical_to_the_router_source():
    assert hashlib.sha256(MODULE.read_bytes()).hexdigest() == PINNED_SHA256


def test_a_valid_row_is_accepted():
    assert L.validate_row(_execution()) == []


def test_raw_exchange_ids_are_refused_by_name_and_by_shape():
    assert L.validate_row(_execution(order_id="x"))
    assert L.validate_row(_execution(flags=["123e4567-e89b-12d3-a456-426614174000"]))


def test_import_is_append_only_and_idempotent(tmp_path):
    ledger = tmp_path / "execution_evidence.jsonl"
    assert L.import_rows(ledger, [_execution()]).written == 1
    before = ledger.read_bytes()
    again = L.import_rows(ledger, [_execution()])
    assert (again.written, again.duplicate) == (0, 1)
    assert ledger.read_bytes() == before


def test_a_conflicting_row_is_refused_and_the_ledger_is_unchanged(tmp_path):
    ledger = tmp_path / "execution_evidence.jsonl"
    L.import_rows(ledger, [_execution()])
    before = ledger.read_bytes()
    res = L.import_rows(ledger, [_execution(last_fill_ts=1_790_000_009)])
    assert res.refused == 1 and res.receipts[0]["status"] == "CONFLICT"
    assert ledger.read_bytes() == before


def test_the_router_cannot_write_a_recommendation_link():
    link = {"schema_version": L.SCHEMA_VERSION, "kind": "LINK", "producer": "router", "source_bet_key": KEY,
            "status": "MISSING", "link_basis": {"match": "exact"}}
    assert L.validate_row(link)
    assert L.validate_row({**link, "producer": "edge_finder"}) == []
