"""Loaders for the V2 research cache (data/research_cache/v2 on the
research-data branch). Read-only; nothing here calls CFBD.

Team key: the CFBD school name, which is consistent across every CFBD
endpoint in the cache. `registry_slug()` maps a CFBD name to the
production team registry id at the boundary (bare "Miami" is Miami (FL)
in CFBD; the registry refuses the bare alias, so it is mapped
explicitly).
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from cfb_edge_finder.ingestion.team_matching import resolve_team_id_for_game

CFBD = "cfbd"
EXPLICIT_SLUGS = {"Miami": "miami-fl"}


def read_json_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


class V2Cache:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.manifest = json.loads((self.root / "manifest.json").read_text())

    @property
    def seasons(self) -> list[int]:
        return sorted(int(s) for s in self.manifest["endpoints"] if s.isdigit())

    def has(self, season: int, name: str) -> bool:
        return (self.root / str(season) / f"{name}.json.gz").exists()

    def load(self, season: int, name: str):
        path = self.root / str(season) / f"{name}.json.gz"
        if not path.exists():
            return []
        return read_json_gz(path)

    def venues(self) -> list[dict]:
        p = self.root / "venues.json.gz"
        return read_json_gz(p) if p.exists() else []


def registry_slug(name: str | None, classification: str | None) -> str | None:
    """CFBD school name -> production registry id (FBS) or None."""
    if not name:
        return None
    if name in EXPLICIT_SLUGS:
        return EXPLICIT_SLUGS[name]
    try:
        return resolve_team_id_for_game(str(name), CFBD, classification)
    except Exception:
        return None
