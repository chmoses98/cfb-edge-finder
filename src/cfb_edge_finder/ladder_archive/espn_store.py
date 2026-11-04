"""Append-only layout for the raw ESPN summary archive (`cfb-espn-raw-archive` branch).

    raw/YYYY-MM-DD/<ts>_<run>_<event>_<sha12>.json.gz   ORIGINAL response bytes (sha256 of the uncompressed original in the name)
    index/YYYY-MM-DD/<ts>_<run>.jsonl                    one extraction row per fetch attempt (failures included)
    runs/YYYY-MM-DD/<ts>_<run>.json                      run manifest: candidates, skipped (with reason), fetched, failed
There is no mutable state file: "already archived" is derived from the index."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .archive_io import canonical, jsonl_bytes, read_gz, read_jsonl, sha256, write_new, write_new_gz

MAX_FAILED_ATTEMPTS = 4


def history(archive: Path) -> dict[str, dict[str, Any]]:
    """event_id -> {archived_ok, completed_ok, failed_attempts}."""
    h: dict[str, dict[str, Any]] = {}
    for mf in sorted((archive / "runs").glob("*/*.json")) if (archive / "runs").exists() else []:
        m = json.loads(mf.read_text())
        ip = (m.get("files") or {}).get("index")
        if ip:
            if not (archive / ip).exists():
                problems.append(f"manifest points at missing index {ip}")
            elif sha256((archive / ip).read_bytes()) != m.get("index_sha256"):
                problems.append(f"index hash mismatch {ip}")
    for f in sorted((archive / "index").glob("*/*.jsonl")) if (archive / "index").exists() else []:
        for r in read_jsonl(f):
            e = h.setdefault(r["event_id"], {"archived_ok": False, "completed_ok": False, "failed_attempts": 0, "incomplete_attempts": 0})
            if r.get("fetch_ok"):
                e["archived_ok"] = True
                done = bool((r.get("final_state") or {}).get("completed"))
                e["completed_ok"] = e["completed_ok"] or done
                e["incomplete_attempts"] += 0 if done else 1
            else:
                e["failed_attempts"] += 1
    return h


def should_fetch(hist: dict[str, Any] | None) -> tuple[bool, str]:
    if hist is None:
        return True, "NEW"
    if hist["completed_ok"]:
        return False, "ALREADY_ARCHIVED_COMPLETE"
    if hist["failed_attempts"] >= MAX_FAILED_ATTEMPTS and not hist["archived_ok"]:
        return False, "GAVE_UP_AFTER_FAILURES"
    if hist.get("incomplete_attempts", 0) >= MAX_FAILED_ATTEMPTS:
        return False, "GAVE_UP_PAYLOAD_NEVER_COMPLETED"
    return True, "RETRY_NOT_COMPLETE_OR_FAILED"


def write_run(archive: Path, manifest: dict[str, Any], rows: list[dict[str, Any]], bodies: dict[str, bytes]) -> dict[str, Any]:
    observed = manifest["finished_at"]
    day, ts = observed[:10], observed[11:19].replace(":", "") + "Z"
    base = f"{ts}_{manifest['run_id']}"
    files = {"raw": [], "index": None, "manifest": f"runs/{day}/{base}.json"}
    for r in rows:
        if r.get("fetch_ok"):
            body = bodies[r["event_id"]]
            path = f"raw/{day}/{base}_{r['event_id']}_{r['sha256'][:12]}.json.gz"
            assert write_new_gz(archive / path, body) == r["sha256"]
            r["raw_file"] = path
            files["raw"].append(path)
    if rows:
        files["index"] = f"index/{day}/{base}.jsonl"
        write_new(archive / files["index"], jsonl_bytes(rows))
    m = dict(manifest, files=files, index_sha256=sha256(jsonl_bytes(rows)) if rows else None)
    write_new(archive / files["manifest"], canonical(m) + b"\n")
    return m


def verify(archive: Path) -> dict[str, Any]:
    problems: list[str] = []
    n_raw = n_idx = 0
    for f in sorted((archive / "raw").glob("*/*.json.gz")) if (archive / "raw").exists() else []:
        n_raw += 1
        data = read_gz(f)
        if not f.name.endswith(f"_{sha256(data)[:12]}.json.gz"):
            problems.append(f"raw hash mismatch {f.name}")
        try:
            json.loads(data)
        except ValueError:
            problems.append(f"raw not JSON {f.name}")
    for mf in sorted((archive / "runs").glob("*/*.json")) if (archive / "runs").exists() else []:
        m = json.loads(mf.read_text())
        ip = (m.get("files") or {}).get("index")
        if ip:
            if not (archive / ip).exists():
                problems.append(f"manifest points at missing index {ip}")
            elif sha256((archive / ip).read_bytes()) != m.get("index_sha256"):
                problems.append(f"index hash mismatch {ip}")
    for f in sorted((archive / "index").glob("*/*.jsonl")) if (archive / "index").exists() else []:
        for r in read_jsonl(f):
            n_idx += 1
            if r.get("fetch_ok"):
                p = archive / r["raw_file"]
                if not p.exists() or sha256(read_gz(p)) != r["sha256"]:
                    problems.append(f"index row does not match raw: {r['event_id']}")
            if r.get("pregame_evidence") is not False:
                problems.append(f"row labelled pregame: {r['event_id']}")
    return {"ok": not problems, "problems": problems[:50], "counts": {"raw_files": n_raw, "index_rows": n_idx}}
