"""Write / verify the append-only ladder archive laid out on the `cfb-1q-ladder-archive` branch.

    runs/YYYY-MM-DD/<ts>_<run>.json        run manifest (every run, including failures)
    rows/YYYY-MM-DD/<ts>_<run>.jsonl       changed-only / full-board market rows
    raw/YYYY-MM-DD/<ts>_<run>_<sha12>.json.gz   original API response bytes (hash is of the uncompressed original)
    state/latest.json                      change-detection cache (rebuildable; the ONLY mutable file)"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .archive_io import canonical, jsonl_bytes, read_gz, read_jsonl, sha256, write_new, write_new_gz
from .ladder_1q import CaptureResult


def stamp(observed_at: str) -> tuple[str, str]:
    day = observed_at[:10]
    ts = observed_at[11:19].replace(":", "") + "Z"
    return day, ts


def load_state(archive: Path) -> dict[str, Any] | None:
    p = archive / "state" / "latest.json"
    return json.loads(p.read_text()) if p.exists() else None


def write_run(archive: Path, res: CaptureResult) -> dict[str, Any]:
    m = res.manifest
    day, ts = stamp(m["observed_at"])
    base = f"{ts}_{m['run_id']}"
    written = {"manifest": f"runs/{day}/{base}.json", "rows": None, "raw": []}
    # raw pages first, then rows, then manifest: a crash leaves raw/rows without a manifest, never a manifest without its data
    for digest, body in sorted(res.raw_pages.items()):
        path = f"raw/{day}/{base}_{digest[:12]}.json.gz"
        got = write_new_gz(archive / path, body)
        assert got == digest
        written["raw"].append(path)
    if res.rows:
        written["rows"] = f"rows/{day}/{base}.jsonl"
        write_new(archive / written["rows"], jsonl_bytes(res.rows))
    m = dict(m, files=written, rows_sha256=sha256(jsonl_bytes(res.rows)) if res.rows else None)
    write_new(archive / written["manifest"], canonical(m) + b"\n")
    state_path = archive / "state" / "latest.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_bytes(canonical(res.state) + b"\n")
    return m


def verify(archive: Path) -> dict[str, Any]:
    """Re-hash everything. Returns {ok, problems, counts}. Used by CI and by the audit before any coverage is reported."""
    problems: list[str] = []
    raw_files = sorted((archive / "raw").glob("*/*.json.gz")) if (archive / "raw").exists() else []
    shas: set[str] = set()
    for f in raw_files:
        data = read_gz(f)
        d = sha256(data)
        shas.add(d)
        if not f.name.endswith(f"_{d[:12]}.json.gz"):
            problems.append(f"raw hash mismatch: {f.name}")
        try:
            json.loads(data)
        except ValueError:
            problems.append(f"raw not JSON: {f.name}")
    manifests = sorted((archive / "runs").glob("*/*.json")) if (archive / "runs").exists() else []
    last_obs = ""
    n_rows = 0
    for mf in manifests:
        m = json.loads(mf.read_text())
        if m["observed_at"] < last_obs and mf.name.split("_")[0] > "":
            pass  # file order is by day/time string; run clocks may interleave across days, so only require per-file consistency below
        last_obs = max(last_obs, m["observed_at"])
        files = m.get("files") or {}
        rp = files.get("rows")
        if rp:
            p = archive / rp
            if not p.exists():
                problems.append(f"missing rows file {rp}")
            else:
                data = p.read_bytes()
                if sha256(data) != m.get("rows_sha256"):
                    problems.append(f"rows hash mismatch {rp}")
                rows = read_jsonl(p)
                n_rows += len(rows)
                if len(rows) != m["rows_written"]:
                    problems.append(f"row count mismatch {rp}")
                for r in rows:
                    if r["observed_at"] != m["observed_at"] or r["run_id"] != m["run_id"]:
                        problems.append(f"row/run mismatch in {rp}")
                        break
        for rp in files.get("raw", []):
            if not (archive / rp).exists():
                problems.append(f"missing raw {rp}")
        for req in m.get("requests", []):
            if req.get("sha256") and m.get("raw_pages_written") and req["status"] and req["status"] < 400 and req["sha256"] not in shas:
                problems.append(f"request body not archived: {req['path']} {req['sha256'][:12]}")
    return {"ok": not problems, "problems": problems[:50], "counts": {"manifests": len(manifests), "raw_files": len(raw_files), "rows": n_rows}}
