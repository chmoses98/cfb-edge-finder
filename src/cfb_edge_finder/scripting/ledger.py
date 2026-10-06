"""The prospective ledger: what was published, frozen BEFORE kickoff. PURE I/O helpers.

Append-only. A row is written:
  PUBLICATION    the first time a game's artifact hash is published
  FINAL_PREGAME  once per game and artifact hash, the last build inside the
                 final-pregame window (default: within 3 hours of kickoff)
Each row carries the frozen scripts (role, archetype, bands), the finding
codes, every MAPPED expression's compatibility string, labels and survival,
and the executable entry price at that moment. The full frozen envelope is
stored once, content-addressed by its hash.

Nothing is ever rewritten. A row recorded after kickoff is refused
(`LedgerOrderError`), so outcomes cannot be backfilled into "predictions",
and a script cannot be edited after the fact: its hash is in the row.
"""

from __future__ import annotations

import gzip
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from cfb_edge_finder.scripting import LEDGER_SCHEMA_VERSION
from cfb_edge_finder.scripting.freeze import canonical_bytes, verify
from cfb_edge_finder.scripting.gamelog import parse_utc
from cfb_edge_finder.scripting.market_map import COMPAT_CODES

PUBLICATION = "PUBLICATION"
FINAL_PREGAME = "FINAL_PREGAME"
FINAL_WINDOW = timedelta(hours=3)


class LedgerOrderError(RuntimeError):
    """A ledger row was offered at or after kickoff."""


def _season_dir(root: Path, season: int) -> Path:
    return root / str(season)


def read_rows(root: Path, season: int) -> list[dict[str, Any]]:
    path = _season_dir(root, season) / "publications.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def store_artifact(root: Path, season: int, envelope: dict[str, Any]) -> Path:
    if not verify(envelope):
        raise ValueError("refusing to store an artifact whose content does not match its hash")
    path = _season_dir(root, season) / "artifacts" / f"{envelope['artifact_hash']}.json.gz"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.GzipFile(path, "wb", mtime=0) as fh:
            fh.write(canonical_bytes(envelope))
    return path


def load_artifact(root: Path, season: int, artifact_hash: str) -> dict[str, Any] | None:
    path = _season_dir(root, season) / "artifacts" / f"{artifact_hash}.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def checkpoint_for(kickoff: str, as_of: str) -> str | None:
    """PUBLICATION always; FINAL_PREGAME also when inside the final window."""
    start, now = parse_utc(kickoff), parse_utc(as_of)
    if now >= start:
        return None
    return FINAL_PREGAME if start - now <= FINAL_WINDOW else PUBLICATION


def row_for(
    *,
    game_key: str,
    season: int,
    envelope: dict[str, Any],
    market_map: dict[str, Any] | None,
    checkpoint: str,
    recorded_at: str,
    prices_captured_at: str | None,
) -> dict[str, Any]:
    content = envelope["content"]
    if parse_utc(recorded_at) >= parse_utc(content["kickoff_utc"]):
        raise LedgerOrderError(f"{game_key}: a ledger row must be recorded before kickoff")
    scripts = content["game_scripts"]["scripts"]
    expressions = []
    for e in (market_map or {}).get("expressions") or []:
        if e.get("unmappable_reason") is not None:
            continue
        price = e.get("price") or {}
        expressions.append(
            {
                "id": e["expression_id"],
                "kind": e["kind"],
                "thesis": e["thesis"],
                "wins_when": e.get("wins_when"),
                "compat": "".join(COMPAT_CODES[c["status"]] for c in e["compatibility"]),
                "labels": e.get("labels") or [],
                "supported": e["script_survival"]["supported"],
                "contradicted": e["script_survival"]["contradicted"],
                "weighted_score": e["script_survival"]["weighted_score"],
                "entry": price.get("entry"),
                "cost_per_contract": price.get("cost_per_contract"),
            }
        )
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "kind": checkpoint,
        "game_key": game_key,
        "season": season,
        "event_id": content["event_id"],
        "kickoff_utc": content["kickoff_utc"],
        "recorded_at": recorded_at,
        "artifact_hash": envelope["artifact_hash"],
        "artifact_generated_at": envelope["generated_at"],
        "data_confidence": content["data_confidence"],
        "teams": content["teams"],
        "scoring_baseline": content["matchup_profile"]["scoring_baseline"],
        "status": content["game_scripts"]["status"],
        "scripts": [
            {
                "script_id": s["script_id"],
                "rank": s["rank"],
                "role": s["role"],
                "archetype": s["archetype"],
                "lead_side": s["lead_side"],
                "outcome_shape": s["outcome_shape"],
                "required_findings": s["required_findings"],
            }
            for s in scripts
        ],
        "findings": [
            {"code": f["code"], "side": f["side"], "category": f["category"]} for f in content["matchup_findings"]
        ],
        "market_map_artifact_hash": (market_map or {}).get("artifact_hash"),
        "prices_captured_at": prices_captured_at,
        "expressions": expressions,
    }


def append(root: Path, season: int, row: dict[str, Any]) -> bool:
    """Append unless a row with the same (game, hash, kind) exists. Returns True if written."""
    existing = {(r["game_key"], r["artifact_hash"], r["kind"]) for r in read_rows(root, season)}
    key = (row["game_key"], row["artifact_hash"], row["kind"])
    if key in existing:
        return False
    path = _season_dir(root, season) / "publications.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    return True
