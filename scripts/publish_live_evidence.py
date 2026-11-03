#!/usr/bin/env python3
"""Fold a ``collect_live_context.py`` run into the COMMITTED availability evidence RUN CFB can read.

RUN CFB reads committed files and has no outbound access of its own. The collector (ESPN core injuries,
keyless) runs in CI; this script copies what it actually observed -- source id, the time the source was
read, the per-team injury lists -- into ``data/evidence/availability/<game_key>.json`` and writes a counts-only
``availability_index.json``.

It invents nothing: a game the collector did not reach gets no record (NEVER_COLLECTED); a game it tried and
could not read gets an UNAVAILABLE record carrying the attempt time and reason; a failed attempt never
replaces an older real observation. The exit code is 0 for any provider problem (fail soft), 2 for bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfb_edge_finder.execution.evidence import publish_availability_evidence, write_index  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-dir", default="data/live")
    parser.add_argument("--context-dir", default="data/live/context")
    parser.add_argument("--evidence-dir", default="data/evidence/availability")
    args = parser.parse_args(argv)

    catalog = Path(args.catalog_dir) / "cfb_market_catalog.json"
    run = Path(args.context_dir) / "collection_run.json"
    if not catalog.exists() or not run.exists():
        print(f"need {catalog} and {run} (run collect_live_context.py first)", file=sys.stderr)
        return 2
    index = json.loads(catalog.read_text(encoding="utf-8"))
    run_at = str(json.loads(run.read_text(encoding="utf-8")).get("as_of") or "")
    if not run_at:
        print("collection_run.json carries no as_of; refusing to date the evidence", file=sys.stderr)
        return 2
    games = index.get("games") or []
    keys = {str(g.get("game_key")) for g in games}
    kickoffs = {str(g.get("game_key")): g.get("kickoff") for g in games}
    counts = publish_availability_evidence(
        context_dir=Path(args.context_dir),
        catalog_game_keys=keys,
        evidence_dir=Path(args.evidence_dir),
        run_at=run_at,
        kickoffs=kickoffs,
    )
    doc = write_index(Path(args.evidence_dir), keys, run_at)
    print(f"availability evidence run_at={run_at}: {counts}")
    print(
        f"  games with a record: {doc['games_with_a_record']} / {doc['catalog_games']}; "
        f"never collected: {doc['games_without_a_record_never_collected']}; by quality: {doc['by_record_quality']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
