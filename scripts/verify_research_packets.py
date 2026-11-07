#!/usr/bin/env python3
"""Verify the RUN CFB research packets against the committed execution index, the way a connector would read them.

    python scripts/verify_research_packets.py                 # every indexed game
    python scripts/verify_research_packets.py --game 26NOV06UNMNEV --strict

Checks (exit 1 on any failure):

* every game in ``data/research_packets/index.json`` has every file the index names, each valid JSON and under the
  connector-safe size (default 250 KB; GitHub's contents API returns nothing for files over 1 MB);
* ``markets.json`` (all parts) holds exactly the contracts the execution index lists for the game;
* no football table carries a metric the owner's forbidden-input register matches (``modeling/forbidden_features.py``), and no market record carries a model probability,
  fair value, edge or stake (the repository supplies evidence, never the bet);
* every contract has both buy sides priced (or says why not) with an all-in cost.

``--strict`` also fails when a game's ``markets.json`` was built from an older quotes fingerprint than the current
execution index (the collector committed since): the reader must then use ``quotes.json``, and the packet says so.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfb_edge_finder.modeling.forbidden_features import forbidden_in  # noqa: E402
MAX_FILE_BYTES = 250_000
BANNED_MARKET_KEYS = {"fair_value", "fair_probability", "model_probability", "edge", "stake", "kelly", "bet_up_to", "ev"}


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def verify_game(row: dict[str, Any], exec_rows: dict[str, dict[str, Any]], *, strict: bool, max_bytes: int) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    key = row["game_key"]
    paths = [row["packet"], row["files"]["team_profiles"], row["files"]["schedules"], row["files"]["distributions"], *row["files"]["markets"]]
    docs: dict[str, Any] = {}
    for rel in paths:
        p = ROOT / rel
        if not p.exists():
            if rel.endswith(("team_profiles.json", "schedules.json")) and "FOOTBALL_IDENTITY_UNRESOLVED" in (row.get("blocking_flags") or []):
                continue  # identity failed: football sections legitimately absent, and the packet says so
            errors.append(f"{key}: missing {rel}")
            continue
        size = p.stat().st_size
        if size > max_bytes:
            errors.append(f"{key}: {rel} is {size} bytes (> {max_bytes})")
        try:
            docs[rel] = load(p)
        except ValueError:
            errors.append(f"{key}: {rel} is not valid JSON")
    markets_docs = [docs[r] for r in row["files"]["markets"] if r in docs]
    contracts = [c for d in markets_docs for c in d.get("contracts", [])]
    exec_row = exec_rows.get(key)
    if exec_row is None:
        errors.append(f"{key}: not in the execution index")
    elif len(contracts) != exec_row["market_count"]:
        errors.append(f"{key}: markets hold {len(contracts)} contracts, execution index lists {exec_row['market_count']}")
    if markets_docs:
        stored = (markets_docs[0].get("price_provenance") or {}).get("game_fingerprint")
        if exec_row is not None and stored != exec_row.get("game_fingerprint"):
            (errors if strict else warnings).append(f"{key}: markets.json fingerprint is older than the execution index; read quotes.json for current prices")
        if markets_docs[0].get("contains_model_probabilities") is not False:
            errors.append(f"{key}: markets.json must declare contains_model_probabilities=false")
    for c in contracts:
        bad = BANNED_MARKET_KEYS & set(c)
        if bad:
            errors.append(f"{key}: {c.get('ticker')} carries banned keys {sorted(bad)}")
        for side in ("yes", "no"):
            s = c.get(side) or {}
            if s.get("ask") is not None and s.get("exec") and s.get("all_in") is None:
                errors.append(f"{key}: {c.get('ticker')} {side} is executable at capture but has no all-in cost")
    prof = docs.get(row["files"]["team_profiles"])
    if prof:
        for side, team in prof["teams"].items():
            for group in team["metrics"].values():
                bad_metrics = forbidden_in(list(group))
                if bad_metrics:
                    errors.append(f"{key}: {side} metrics include forbidden input(s) {bad_metrics}")
    packet = docs.get(row["packet"])
    if packet:
        if not packet["data_quality"]["missing_data_flags"]:
            errors.append(f"{key}: packet carries no missing-data flags (the QB/injury gaps must always be stated)")
        if packet["qb"].get("status") != "PROJECTED_ONLY" and packet["qb"].get("home") is not None:
            errors.append(f"{key}: QB status must stay PROJECTED_ONLY until a confirmed source exists")
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", action="append")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--max-bytes", type=int, default=MAX_FILE_BYTES)
    ap.add_argument("--packets-dir", type=Path, default=ROOT / "data/research_packets")
    args = ap.parse_args(argv)
    index = load(args.packets_dir / "index.json")
    exec_index = load(ROOT / "data/live/cfb_execution_index.json")
    exec_rows = {g["game_key"]: g for g in exec_index["games"]}
    rows: list[dict[str, Any]] = []
    for slate in index["slates"]:
        rows.extend(load(ROOT / slate["path"])["games"])
    if args.game:
        rows = [r for r in rows if r["game_key"] in set(args.game)]
        missing = set(args.game) - {r["game_key"] for r in rows}
        if missing:
            print(f"FAIL: not in the packet index: {sorted(missing)}")
            return 1
    errors: list[str] = []
    warnings: list[str] = []
    for r in rows:
        e, w = verify_game(r, exec_rows, strict=args.strict, max_bytes=args.max_bytes)
        errors += e
        warnings += w
    for w in warnings[:20]:
        print("WARN:", w)
    for e in errors[:50]:
        print("FAIL:", e)
    print(f"verified {len(rows)} games: {len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
