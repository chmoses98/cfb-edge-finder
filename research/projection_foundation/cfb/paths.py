"""Cache locations and idempotent fetchers. Raw data never enters git (see ../.gitignore)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PF_ROOT = Path(__file__).resolve().parents[1]          # research/projection_foundation
REPO_ROOT = PF_ROOT.parents[1]
CACHE = Path(os.environ.get("PF_CFB_CACHE", PF_ROOT / ".cache"))
RD = CACHE / "rd"                                       # sparse clone of research-data
CFBD = RD / "data" / "research_cache" / "v2"
PRESEASON = RD / "data" / "research_cache" / "preseason"
V2_RESEARCH = RD / "data" / "research" / "v2"
SDV = CACHE / "sdv"
BUILT = CACHE / "built"
RESULTS = PF_ROOT / "results"
FOOTBALL_2026 = REPO_ROOT / "data" / "football" / "2026"
REPAIR = REPO_ROOT / "data" / "scripting" / "validation" / "base_repair"

REPO_URL = os.environ.get("PF_CFB_REPO_URL", "https://github.com/chmoses98/cfb-edge-finder")
SDV_URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/{tag}/{name}"
SEASONS = list(range(2014, 2026))   # CFBD cache seasons
CURRENT = 2026


def _run(cmd, cwd=None):
    subprocess.run(cmd, cwd=cwd, check=True, stdout=subprocess.DEVNULL)


def fetch_research_data(force: bool = False) -> None:
    """Sparse, blobless clone of the research-data branch (CFBD cache 2014-2025, preseason cache,
    archived v2 research predictions). ~110 MB."""
    if CFBD.exists() and not force:
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    if not RD.exists():
        _run(["git", "clone", "--filter=blob:none", "--no-checkout", "--depth=1", "--branch",
              "research-data", REPO_URL, str(RD)])
    _run(["git", "sparse-checkout", "set", "--no-cone", "data/research_cache/v2/*",
          "data/research_cache/preseason/*", "data/research/v2/*"], cwd=RD)
    _run(["git", "checkout", "research-data"], cwd=RD)


def _download(url: str, dest: Path, tries: int = 4) -> bool:
    for i in range(tries):
        try:
            tmp = dest.with_suffix(dest.suffix + ".part")
            with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
                f.write(r.read())
            tmp.replace(dest)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"download failed ({i + 1}/{tries}) {url}: {e}", file=sys.stderr)
            time.sleep(2 ** (i + 1))
    return False


def fetch_sdv(seasons=None, refresh_current: bool = False) -> None:
    """ESPN player box + betting releases (sportsdataverse-data). Current season refetched on demand."""
    seasons = seasons or list(range(2014, CURRENT + 1))
    for tag, stem, sub in (("espn_cfb_player_box", "player_box", "player_box"),
                           ("espn_cfb_betting", "betting", "betting")):
        d = SDV / sub
        d.mkdir(parents=True, exist_ok=True)
        for s in seasons:
            dest = d / f"{stem}_{s}.parquet"
            if dest.exists() and not (refresh_current and s == CURRENT):
                continue
            _download(SDV_URL.format(tag=tag, name=f"{stem}_{s}.parquet"), dest)


if __name__ == "__main__":
    fetch_research_data()
    fetch_sdv(refresh_current="--refresh" in sys.argv)
