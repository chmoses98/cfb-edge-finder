"""Append-only archive files: exclusive-create writes, content hashes, and a verifier.

Files are named by capture time + run id, so two runs never target the same path; `open(..., 'xb')` makes an accidental
overwrite a hard error. The only mutable file is `state/latest.json` (a change-detection cache that can be rebuilt from rows)."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_new(path: Path, data: bytes) -> None:
    """Create `path` exclusively. Raises FileExistsError rather than overwrite."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as fh:
        fh.write(data)


def write_new_gz(path: Path, data: bytes) -> str:
    """gzip `data` deterministically (mtime=0) into a new file; returns sha256 of the UNCOMPRESSED bytes."""
    write_new(path, gzip.compress(data, mtime=0))
    return sha256(data)


def read_gz(path: Path) -> bytes:
    return gzip.decompress(path.read_bytes())


def jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical(r) + b"\n" for r in rows)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
