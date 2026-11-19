"""Frozen parameters and inputs, verified against PROVENANCE.json on every load (fail closed on any hash mismatch)."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

FROZEN_DIR = Path(__file__).resolve().parent / "frozen"


class FrozenAssetError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def provenance() -> dict:
    return json.loads((FROZEN_DIR / "PROVENANCE.json").read_text())


def _verified(name: str) -> bytes:
    rec = provenance()["files"][name]
    path = FROZEN_DIR / name
    if not path.exists():
        raise FrozenAssetError(f"missing frozen asset {name}")
    got = _sha256(path)
    if got != rec["sha256"]:
        raise FrozenAssetError(f"frozen asset {name} sha256 {got} != recorded {rec['sha256']}")
    return path.read_bytes()


def verify_all() -> dict:
    """Re-hash EVERY frozen asset from disk now (uncached; ``load`` is cached per process) against PROVENANCE.json.
    Raises FrozenAssetError on any missing or mismatched file. Returns {name: sha256}."""
    rec = json.loads((FROZEN_DIR / "PROVENANCE.json").read_text())
    if rec != provenance():
        raise FrozenAssetError("PROVENANCE.json changed since it was loaded")
    if not rec.get("files"):
        raise FrozenAssetError("PROVENANCE.json lists no frozen assets")
    return {name: hashlib.sha256(_verified(name)).hexdigest() for name in rec["files"]}


@dataclass(frozen=True)
class Frozen:
    p307: dict                 # research/cfb_challenger/frozen_params.json (#307), verbatim
    p15: dict                  # research/cfb_challenger/phase15/frozen_p15.json (#308), verbatim
    nb: dict                   # segment -> (mu_train, y_train) for the empirical period PMFs
    prior_rows: list           # compact team-game rows of the seasons before the frozen season
    preseason: dict            # team_id -> (talent_z, ret_z) for the frozen season
    season: int


@lru_cache(maxsize=1)
def load() -> Frozen:
    prov = provenance()
    p307 = json.loads(_verified("frozen_params_p307.json"))
    p15 = json.loads(_verified("frozen_p15.json"))
    with np.load(io.BytesIO(_verified("period_nb.npz"))) as z:
        nb = {k[:-3]: (z[k], z[k[:-3] + "_y"].astype(np.int64)) for k in z.files if k.endswith("_mu")}
    rows = [json.loads(x) for x in gzip.decompress(_verified("prior_team_games.jsonl.gz")).splitlines() if x]
    pre_doc = json.loads(_verified("preseason.json"))
    season = int(prov["season"])
    if pre_doc["season"] != season:
        raise FrozenAssetError("preseason table season does not match PROVENANCE season")
    pre = {t: (float(v[0]), float(v[1])) for t, v in pre_doc["teams"].items()}
    return Frozen(p307=p307, p15=p15, nb=nb, prior_rows=rows, preseason=pre, season=season)
