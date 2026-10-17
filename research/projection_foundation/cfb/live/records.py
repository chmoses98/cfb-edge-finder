"""Forecast-archive records (sift.forecast_archive.v1 inputs) and content-based de-duplication."""
from __future__ import annotations

import numpy as np
import pandas as pd

SPORT = "CFB"
CHALLENGER_ID = "pf-cfb"
INCUMBENT_ID = "script-engine-scoring-baseline"
TEAM_ENTITY_TOTAL, TEAM_ENTITY_MARGIN = "TOTAL", "MARGIN"


def trim_pmf(lo: int, probs, eps: float = 1e-9) -> tuple[int, list[float]]:
    """Drop leading/trailing mass below eps (the lattice is much wider than the support) and renormalise."""
    p = np.asarray(probs, float)
    nz = np.where(p > eps)[0]
    if len(nz) == 0:
        return int(lo), p.tolist()
    a, b = nz[0], nz[-1] + 1
    q = p[a:b]
    return int(lo) + int(a), (q / q.sum()).tolist()


def forecast_record(*, family: str, game_id: str, start_utc: str, entity_id: str, entity_name: str, team: str,
                    dist_lo: int, probs, gate_status: str, participation: str | None, readiness: str, now_utc: str,
                    asof_utc: str, version: str, mean: float | None = None, role: str = "challenger",
                    model_id: str | None = None, extra: dict | None = None) -> dict:
    lo, pr = trim_pmf(dist_lo, probs) if probs is not None and len(probs) else (0, [])
    if mean is None:
        mean = float(np.dot(np.arange(lo, lo + len(pr)), pr))
    rec = {"sport": SPORT, "family": family, "model_id": model_id or (CHALLENGER_ID if role == "challenger" else INCUMBENT_ID),
           "model_role": role, "model_version": version, "game_id": str(game_id), "game_start_utc": start_utc,
           "entity_id": str(entity_id), "entity_name": entity_name, "team": team, "generated_utc": now_utc,
           "data_asof_utc": asof_utc, "mean": float(mean), "lo": lo, "probs": pr, "gate_status": gate_status,
           "participation": participation, "readiness": readiness}
    if extra:
        rec.update(extra)
    return rec


def signature(r: dict) -> tuple:
    """What makes a forecast 'the same' for de-duplication: the number and the conditions it was issued under."""
    return (r["model_id"], r["game_id"], r["entity_id"], r["family"], round(float(r["mean"]), 4),
            r.get("participation"), r.get("gate_status"), r.get("qb_flag"))


def drop_unchanged(records: list[dict], archived: pd.DataFrame) -> tuple[list[dict], int]:
    """Skip records whose latest archived pregame version carries the same signature (re-runs on unchanged inputs
    must not grow the archive)."""
    if archived is None or archived.empty:
        return records, 0
    a = archived.copy()
    a = a.sort_values("generated_utc").groupby(["model_id", "game_id", "entity_id", "family"], as_index=False).tail(1)
    sig = {(r.model_id, r.game_id, r.entity_id, r.family):
           (r.model_id, r.game_id, r.entity_id, r.family, round(float(r.mean), 4), getattr(r, "participation", None),
            getattr(r, "gate_status", None), getattr(r, "qb_flag", None) if "qb_flag" in a.columns else None)
           for r in a.itertuples()}
    keep, dropped = [], 0
    for r in records:
        prev = sig.get((r["model_id"], r["game_id"], r["entity_id"], r["family"]))
        cur = signature(r)
        if prev is not None and _norm(prev) == _norm(cur):
            dropped += 1
        else:
            keep.append(r)
    return keep, dropped


def _norm(t):
    return tuple(None if (isinstance(x, float) and np.isnan(x)) else x for x in t)
