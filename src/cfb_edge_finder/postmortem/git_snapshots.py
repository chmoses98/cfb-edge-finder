"""A ``SnapshotSource`` over the git history of ``main``: every ``kalshi cfb catalog`` commit is a quote snapshot.

Works on a blobless / shallow clone: only the small status blob per commit and the one quote shard asked for are
fetched. Read-only: this module never writes to the repository it reads.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from cfb_edge_finder.postmortem.evidence_join import Snapshot, parse_utc

CATALOG_SUBJECT = "kalshi cfb catalog"
STATUS_PATH = "data/live/cfb_catalog_status.json"
GAMES_DIR = "data/live/execution/games"


class GitSnapshotSource:
    def __init__(self, repo: Path, *, since: str | None = None, until: str | None = None):
        self.repo = Path(repo)
        self.since, self.until = since, until
        self._snaps: list[Snapshot] | None = None

    def _git(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=False)
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
        return proc.stdout if proc.returncode == 0 else ""

    def snapshots(self) -> list[Snapshot]:
        if self._snaps is not None:
            return self._snaps
        args = ["log", f"--grep=^{CATALOG_SUBJECT}", "--format=%H"]
        if self.since:
            args.append(f"--since={self.since}")
        if self.until:
            args.append(f"--until={self.until}")
        commits = [c for c in self._git(*args, "HEAD").split() if c]
        out: list[Snapshot] = []
        for commit in commits:
            blob = self._git("show", f"{commit}:{STATUS_PATH}", check=False)
            try:
                captured = parse_utc(json.loads(blob).get("captured_at"))
            except ValueError:
                captured = None
            # A snapshot without a capture time cannot be placed on the clock: skipped, not guessed.
            if captured is not None:
                out.append(Snapshot(commit, captured))
        self._snaps = sorted(out, key=lambda s: s.captured_at)
        return self._snaps

    def game_keys(self, snapshot: Snapshot) -> set[str]:
        names = self._git("ls-tree", "--name-only", snapshot.ref, f"{GAMES_DIR}/", check=False)
        return {Path(n).name for n in names.split() if n}

    def quotes(self, snapshot: Snapshot, game_key: str) -> dict[str, Any] | None:
        base = f"{GAMES_DIR}/{game_key}"
        single = self._git("show", f"{snapshot.ref}:{base}/quotes.json", check=False)
        if single:
            return json.loads(single)
        listing = self._git("ls-tree", "--name-only", snapshot.ref, f"{base}/", check=False).split()
        parts = sorted(n for n in listing if Path(n).name.startswith("quotes.part"))
        if not parts:
            return None
        merged: dict[str, Any] = {}
        rows: list[Any] = []
        for name in parts:
            doc = json.loads(self._git("show", f"{snapshot.ref}:{name}"))
            merged = merged or {k: v for k, v in doc.items() if k != "quotes"}
            rows.extend(doc.get("quotes") or [])
        merged["quotes"] = rows
        return merged
