"""Target-game outcomes. RESEARCH ONLY; read only after the feature table is frozen.

Final points come from the CFBD `games` record (`homePoints`/`awayPoints`),
cross-checked against the two box-score rows' `points_for`; quarter scores
from `homeLineScores`/`awayLineScores` (only when exactly four or more
quarters are listed; first half = Q1 + Q2); plays from the box rows.
A disagreement between the game record and the box rows is OUTCOME_MISMATCH
and the game is excluded from every outcome test.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from cfb_edge_finder.scripting.gamelog import TeamGame


def _load(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def load_outcomes(directory: Path, rows: list[TeamGame]) -> dict[str, dict[str, Any]]:
    box: dict[str, dict[str, TeamGame]] = {}
    for r in rows:
        box.setdefault(r.game_id, {})[r.team_id] = r
    out: dict[str, dict[str, Any]] = {}
    for g in _load(Path(directory) / "games.json.gz"):
        if not g.get("completed"):
            continue
        gid = str(g["id"])
        hp, ap = g.get("homePoints"), g.get("awayPoints")
        if hp is None or ap is None:
            continue
        sides = box.get(gid) or {}
        h, a = sides.get(str(g.get("homeId"))), sides.get(str(g.get("awayId")))
        status = "OK"
        if h is not None and a is not None and (h.points_for != hp or a.points_for != ap):
            status = "OUTCOME_MISMATCH"
        hl, al = g.get("homeLineScores") or [], g.get("awayLineScores") or []
        quarters = len(hl) >= 4 and len(al) >= 4 and all(x is not None for x in hl[:4] + al[:4])
        out[gid] = {
            "status": status,
            "home_points": float(hp),
            "away_points": float(ap),
            "home_margin": float(hp - ap),
            "total_points": float(hp + ap),
            "overtime": len(hl) > 4,
            "home_1h": float(hl[0] + hl[1]) if quarters else None,
            "away_1h": float(al[0] + al[1]) if quarters else None,
            "home_1q": float(hl[0]) if quarters else None,
            "away_1q": float(al[0]) if quarters else None,
            "home_plays": h.box.get("plays") if h else None,
            "away_plays": a.box.get("plays") if a else None,
            "home_ypp": (h.box.get("total_yards") / h.box["plays"])
            if h and h.box.get("plays") and h.box.get("total_yards") is not None
            else None,
            "away_ypp": (a.box.get("total_yards") / a.box["plays"])
            if a and a.box.get("plays") and a.box.get("total_yards") is not None
            else None,
        }
    return out
