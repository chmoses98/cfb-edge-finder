"""The sealed-holdout procedure: predict and freeze, then (only behind the gate) reveal. RESEARCH ONLY.

Stage 1 `predict` replays the production engine for every holdout game from
strictly earlier games (Wave 1 `study.run_targets` with outcomes NOT
revealed), attaches the V2 claims and writes a manifest of
(pregame_hash, v2_hash) per game. Nothing in stage 1 reads a target game's
own box score.

Stage 2 `reveal_and_score` refuses to run unless the gate holds:
  * the protocol document exists, is marked PRE-REGISTERED and names the
    SHA-256 of the frozen development parameters;
  * the frozen parameter file matches that hash (`fit.load_frozen`);
  * every prediction still matches its manifest hash, and the manifest
    matches the hash stage 1 recorded.
Only then are the targets' own rows read (Wave 1 `replay.reveal`, which
re-checks each pregame hash) and scored.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from cfb_edge_finder.archetype_research.replay import Target, reveal, schedule_targets, target_rows
from cfb_edge_finder.archetype_research.study import coverage, run_targets
from cfb_edge_finder.archetype_research.taxonomy import score_game
from cfb_edge_finder.archetype_research.v2 import HOLDOUT_SEASONS
from cfb_edge_finder.archetype_research.v2.claims import claims_hash
from cfb_edge_finder.archetype_research.v2.fit import attach_claims, load_frozen
from cfb_edge_finder.scripting.gamelog import TeamGame


class HoldoutGateError(RuntimeError):
    """The holdout may not be revealed: the protocol/freeze/prediction chain is broken."""


def manifest_of(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    entries = {r["pregame"]["game_id"]: [r["pregame_hash"], r["v2_hash"]] for r in records}
    body = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return {"games": entries, "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}


def predict_season(season_data: dict[str, Any]) -> dict[str, Any]:
    """Stage 1 for one season: frozen pregame records + V2 claims. No outcome is revealed."""
    rows, games = season_data["rows"], season_data["games"]
    if any(int(g.get("season") or 0) not in HOLDOUT_SEASONS for g in games):
        raise HoldoutGateError("predict_season is for the sealed holdout seasons only")
    targets, excluded = schedule_targets(games, rows)
    records = run_targets(rows, targets, reveal_outcomes=False)
    attach_claims(records)
    return {
        "records": records,
        "schedule_exclusions": excluded,
        "coverage": coverage(rows, games, records, excluded),
        "digests": season_data["digests"],
    }


def check_gate(protocol_path: Path, frozen_path: Path) -> dict[str, Any]:
    protocol = Path(protocol_path)
    if not protocol.exists():
        raise HoldoutGateError(f"protocol {protocol} does not exist")
    text = protocol.read_text(encoding="utf-8")
    frozen = load_frozen(frozen_path)
    if "PRE-REGISTERED" not in text:
        raise HoldoutGateError("protocol is not marked PRE-REGISTERED")
    if frozen["sha256"] not in text:
        raise HoldoutGateError("protocol does not name the frozen parameter hash")
    return frozen


def reveal_and_score(
    predictions: list[dict[str, Any]],
    manifest: dict[str, Any],
    rows: list[TeamGame],
    targets: dict[str, Target],
    *,
    protocol_path: Path,
    frozen_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Stage 2 for one season's predictions. Returns (revealed records, frozen parameters)."""
    frozen = check_gate(protocol_path, frozen_path)
    if manifest_of(predictions)["sha256"] != manifest["sha256"]:
        raise HoldoutGateError("predictions no longer match the frozen manifest")
    for r in predictions:
        if claims_hash(r["v2"]) != r["v2_hash"]:
            raise HoldoutGateError(f"V2 claims of {r['pregame']['game_id']} were altered after freezing")
    data = tuple(rows)
    out = []
    for r in predictions:
        rec = dict(r)
        rec["outcome"] = None
        if r["pregame"]["eligibility"] is None:
            frozen_pre = {"pregame": r["pregame"], "pregame_hash": r["pregame_hash"]}
            pre, home, away = reveal(frozen_pre, *target_rows(data, targets[r["pregame"]["game_id"]]))
            rec["outcome"] = score_game(pre, home, away)
        out.append(rec)
    return out, frozen
