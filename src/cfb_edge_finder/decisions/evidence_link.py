"""Decision-side evidence: recommendation / model-run references (DIGESTS ONLY) and the exact-unique-match link.

WHERE THE ATTRIBUTION GAP IS CLOSED. The router can never assert a recommendation link (the contract forbids
recommendation provenance on routed rows) -- the repository that owns the decision records writes it. This module
turns a RUN CFB candidate artifact into *decision candidates* (what was recommended, when, from which model snapshot)
and links them to the router's EXECUTION evidence rows with the vendored ledger's ``link_recommendation``:

  * exact market + side, created BEFORE the first fill, within ``LINK_WINDOW_SECONDS`` of it;
  * zero matches -> MISSING; one distinct recommendation -> LINKED; two or more -> AMBIGUOUS (never "nearest").

PRIVACY. The repository is public. Recommendation and model-run ids are hashed with a domain-separated SHA-256
(32 hex characters); the raw ids stay in the private decision store. A decision candidate carries the market, side and
a timestamp -- the same facts the published candidate artifact already shows.

``LINK_WINDOW_SECONDS`` is an explicit, owner-adjustable policy value, not a hidden default: a recommendation older
than this before the first fill is not linked to it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

DOMAIN_RECOMMENDATION = "cfb:recommendation:v1"
DOMAIN_MODEL_RUN = "cfb:model_run:v1"
LINK_WINDOW_SECONDS = 6 * 3600

_LEDGER_PATH = Path(__file__).resolve().parents[3] / "scripts" / "accounting" / "execution_evidence_ledger.py"


def ledger_module():
    """The vendored, hash-pinned evidence ledger (scripts/accounting/execution_evidence_ledger.py)."""
    name = "execution_evidence_ledger_vendored"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _LEDGER_PATH)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def ref(domain: str, raw_id: str) -> str:
    """Domain-separated opaque reference for a private id: 32 hex characters."""
    return hashlib.sha256(f"{domain}:{raw_id}".encode()).hexdigest()[:32]


def _epoch(ts: datetime | str) -> float:
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return ts.timestamp()


def decision_candidates(
    artifact: dict[str, Any],
    *,
    decision_time: datetime,
    model_as_of_by_game: dict[str, str],
    model_run_id: str | None = None,
    key: str = "candidates",
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Digest-only decision candidates for the linker, plus counts of what could not be built (explicit gaps).

    A candidate lacking a recommendation id, market, side or a stated model-as-of time is SKIPPED and counted
    (``missing_*``): a link needs all of them, and none is invented.
    """
    from cfb_edge_finder.expression.ticker_ontology import parse_ticker

    out: list[dict[str, Any]] = []
    gaps = {"missing_recommendation_id": 0, "missing_market_or_side": 0, "missing_model_as_of": 0}
    created = _epoch(decision_time)
    for cand in artifact.get(key) or []:
        rid, market, side = cand.get("recommendation_id"), cand.get("market"), cand.get("side")
        if not rid:
            gaps["missing_recommendation_id"] += 1
            continue
        if not market or not side:
            gaps["missing_market_or_side"] += 1
            continue
        info = parse_ticker(str(market))
        as_of = model_as_of_by_game.get(str(cand.get("game_key"))) or model_as_of_by_game.get(info.game_key or "")
        if not as_of:
            gaps["missing_model_as_of"] += 1
            continue
        out.append(
            {
                "market_ticker": str(market),
                "side": str(side).upper(),
                "created_at_ts": created,
                "recommendation_ref": ref(DOMAIN_RECOMMENDATION, str(rid)),
                "model_run_ref": ref(DOMAIN_MODEL_RUN, model_run_id) if model_run_id else None,
                "model_as_of": as_of,
            }
        )
    return out, gaps


def write_decision_candidates(path: Path, rows: list[dict[str, Any]]) -> int:
    """Append-only, idempotent: a row already present (same key) is not appended again. Returns rows written."""
    path = Path(path)
    seen: set[tuple[Any, ...]] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                seen.add((r["recommendation_ref"], r["market_ticker"], r["side"], r["created_at_ts"]))
    fresh = [
        r for r in rows if (r["recommendation_ref"], r["market_ticker"], r["side"], r["created_at_ts"]) not in seen
    ]
    if fresh:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for r in fresh:
                fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
    return len(fresh)


def read_decision_candidates(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def link_executions(
    executions: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    recorded_at: str,
    window_seconds: float = LINK_WINDOW_SECONDS,
) -> list[dict[str, Any]]:
    """One LINK row per EXECUTION row (the router's evidence). Exact unique match or MISSING / AMBIGUOUS.

    Each item of ``executions`` is ``{"execution": <EXECUTION evidence row>, "market_ticker": ..., "side": ...}``:
    the evidence row is keyed by ``source_bet_key`` and has no market of its own, so the caller supplies the
    wager's market and side (from the wager ledger). ``recorded_at`` is required -- no clock is read here.
    Every produced row is checked with the ledger's own ``validate_row``; one that fails is a bug and raises.
    """
    ledger = ledger_module()
    out = []
    for item in executions:
        row = ledger.link_recommendation(
            item["execution"],
            candidates,
            window_seconds=window_seconds,
            market_ticker=item["market_ticker"],
            side=str(item["side"]).upper(),
        )
        row["recorded_at"] = recorded_at
        problems = ledger.validate_row(row)
        if problems:
            raise ValueError(f"invalid LINK row: {problems}")
        out.append(row)
    return out
