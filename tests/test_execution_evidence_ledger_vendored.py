"""The vendored execution-evidence ledger (copied verbatim from chmoses98/kalshi-bet-router).

Pure-Python and self-contained: it pins the vendored file's digest and exercises the properties this repository
relies on -- digests only, append-only, idempotent, never rewritten.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "accounting" / "execution_evidence_ledger.py"
PINNED_SHA256 = "72a6697e37f456cff07bfef3bb17ec06ea7b4ab534527ab531cc503700431ebe"

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
    row = {
        "schema_version": L.SCHEMA_VERSION,
        "kind": "EXECUTION",
        "producer": "router",
        "source_bet_key": KEY,
        "order_id_digest": D1,
        "fill_id_digests": [D2],
        "fill_count": 1,
        "first_fill_ts": 1_790_000_000,
        "last_fill_ts": 1_790_000_001,
        "execution_action": "BUY",
        "all_taker": True,
        "flags": [],
    }
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
    link = {
        "schema_version": L.SCHEMA_VERSION,
        "kind": "LINK",
        "producer": "router",
        "source_bet_key": KEY,
        "status": "MISSING",
        "link_basis": {"match": "exact"},
    }
    assert L.validate_row(link)
    assert L.validate_row({**link, "producer": "edge_finder"}) == []


# Cross-repository agreement on how order / fill / recommendation_ref / model_run_ref digests are derived. The same
# four vectors are asserted in kalshi-bet-router (tests/test_execution_evidence.py::GOLDEN_DIGESTS) and in every
# destination, so a repository that derives a ref differently fails its own CI.
_GOLDEN_DIGESTS = (
    ("order", "ORDER-X", "70b17cbeb58152c522d8be2a9a30ac34"),
    ("fill", "FILL-X", "b34311e543285f452dd37dc618573dcb"),
    ("recommendation", "rec_0001", "34284d398be9830fd5ca1d76f8a673f1"),
    ("model_run", "run_0001", "6e039e855b3d236b125624c33b87427b"),
)


def test_digest_formula_matches_the_cross_repository_golden_vectors():
    for kind, value, expected in _GOLDEN_DIGESTS:
        material = f"kalshi-router.execution-evidence.v1|{kind}|{value}".encode()
        assert hashlib.sha256(material).hexdigest()[:32] == expected
