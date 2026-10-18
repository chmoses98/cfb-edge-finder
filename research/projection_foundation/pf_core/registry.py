"""Experiment records: every walk-forward experiment writes one immutable JSON record.

Required fields mirror the Projection Foundation brief (Part V): model version, periods,
features, hyperparameters, provenance, independent observations, accuracy, calibration,
uncertainty, compute cost, comparisons and limitations.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any


def _git_sha(path: str = ".") -> str:
    try:
        return subprocess.check_output(["git", "-C", path, "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


@dataclass
class ExperimentRecord:
    experiment_id: str
    sport: str
    family: str                      # e.g. "receptions", "points", "pitcher_strikeouts"
    model: str                       # e.g. "B_ewma_shrunk"
    model_version: str
    question: str
    hypothesis: str
    split: str                       # "development" | "validation" | "holdout"
    train_period: str
    eval_period: str
    features: list[str]
    hyperparameters: dict[str, Any]
    data_provenance: list[str]
    n_obs: int
    n_independent: int               # independent units (player-games) used for inference
    n_clusters: int                  # games
    point: dict[str, Any] = field(default_factory=dict)
    distribution: dict[str, Any] = field(default_factory=dict)
    vs_baseline: dict[str, Any] = field(default_factory=dict)
    vs_incumbent: dict[str, Any] = field(default_factory=dict)
    compute_seconds: float = 0.0
    limitations: list[str] = field(default_factory=list)
    verdict: str = ""
    created_utc: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    code_sha: str = field(default_factory=_git_sha)
    python: str = field(default_factory=platform.python_version)

    def fingerprint(self) -> str:
        d = asdict(self)
        for k in ("created_utc", "compute_seconds", "code_sha", "python"):
            d.pop(k, None)
        return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]


def write_record(rec: ExperimentRecord, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{rec.experiment_id}.json")
    payload = asdict(rec)
    payload["fingerprint"] = rec.fingerprint()
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=_json_default)
    return path


def _json_default(o):
    try:
        import numpy as np
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except Exception:
        pass
    return str(o)


def md_table(rows: list[dict], cols: list[str], fmt: dict[str, str] | None = None) -> str:
    fmt = fmt or {}
    head = "| " + " | ".join(cols) + " |\n|" + "|".join(["---"] * len(cols)) + "|\n"
    body = []
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c, "")
            if isinstance(v, float):
                v = format(v, fmt.get(c, ".3f"))
            cells.append(str(v))
        body.append("| " + " | ".join(cells) + " |")
    return head + "\n".join(body) + "\n"
