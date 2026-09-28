"""Where decision records live, and the guard that keeps them out of public git.

*** THE STORE IS A DIRECTORY THE OPERATOR NAMES, AND NOTHING ELSE ***
`CFB_DECISION_STORE` (or `--decision-store`) names a directory. It is expected
to be a checkout of a PRIVATE repository, or a private drive, or any place the
operator is willing to hold the prices and sizes of their own decisions. Nothing
in this repository chooses a default, because every default this repository
could choose is inside a public checkout.

*** FAIL CLOSED, LOUDLY ***
A store that is not configured, does not exist, is not marked private, or is
inside this repository's own working tree is REFUSED before any record is
written -- and the refusal is an error a workflow cannot miss, not a warning it
can scroll past. There is no fallback to a public path and no fallback to
"skip it quietly": an operator who wants to run without a record has to say so
with `--no-decision-record`, so the absence of a record is a decision on the
record rather than an accident.

The privacy marker is a file the operator creates ONCE in the store root:

    .private-decision-store

Its presence says a person looked at this directory and chose it for this
purpose. Its absence is a refusal.

*** ATOMIC ***
A record is written to a temporary file in the same directory, flushed, synced,
and renamed into place. A reader never sees a half-written record, and a run
that dies mid-write leaves nothing that looks like a record.

*** OPTIONAL GIT SYNC ***
With `CFB_DECISION_STORE_GIT_SYNC=1` and a store that is a git working tree,
the new record is committed and pushed. A push that fails is an error: the
record exists locally, the run says so, and it says the record is NOT yet
durable. Without the variable the store is a directory and durability is the
operator's arrangement.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from cfb_edge_finder.decisions.record import (
    SUPPORTED_SCHEMA_VERSIONS,
    validate_decision_record,
)

ENV_STORE = "CFB_DECISION_STORE"
ENV_GIT_SYNC = "CFB_DECISION_STORE_GIT_SYNC"
PRIVATE_MARKER = ".private-decision-store"

#: Repository names whose working trees are PUBLIC. A store inside a checkout of
#: one of these is refused whatever the marker says.
PUBLIC_REPOSITORY_MARKERS = ("cfb-edge-finder", "kalshi-bet-router")


class DecisionStoreUnavailable(Exception):
    """No private store can be used. The caller must fail closed."""


def _git_toplevel(path: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def _git_remote(path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            capture_output=True, text=True, check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


class DecisionStore:
    """A private directory of decision records. Resolve it with `resolve`."""

    def __init__(self, root: Path):
        self.root = Path(root)

    # ------------------------------------------------------------ resolve

    @classmethod
    def resolve(
        cls,
        explicit: str | None,
        *,
        env: dict[str, str] | None = None,
        repo_root: Path | None = None,
    ) -> DecisionStore:
        """The store the operator configured, checked, or a refusal.

        `explicit` is the CLI flag; the environment variable is the fallback.
        `repo_root` is this repository's working tree, so a store inside it is
        refused. Every failure raises `DecisionStoreUnavailable` with the
        reason a person needs to fix it.
        """
        environment = os.environ if env is None else env
        configured = (explicit or environment.get(ENV_STORE) or "").strip()
        if not configured:
            raise DecisionStoreUnavailable(
                f"no private decision store is configured. Set {ENV_STORE} (or pass "
                "--decision-store) to a directory OUTSIDE this repository -- a private "
                f"repository checkout -- containing a {PRIVATE_MARKER} marker file. To run "
                "without a decision record, say so with --no-decision-record."
            )
        root = Path(configured).expanduser()
        if not root.is_dir():
            raise DecisionStoreUnavailable(
                f"the decision store {root} does not exist or is not a directory; nothing "
                "was written and no decision record exists for this run"
            )
        root = root.resolve()
        if not (root / PRIVATE_MARKER).is_file():
            raise DecisionStoreUnavailable(
                f"the decision store {root} carries no {PRIVATE_MARKER} marker. Create that "
                "file there ONLY if the directory is private (a private repository checkout or "
                "a private drive); it is the operator's statement that the store may hold the "
                "prices and sizes of their decisions"
            )
        store_top = _git_toplevel(root)
        repo_top = _git_toplevel(repo_root) if repo_root is not None else None
        if store_top is not None and repo_top is not None and store_top == repo_top:
            raise DecisionStoreUnavailable(
                f"the decision store {root} is inside this repository's working tree "
                f"({repo_top}), which is PUBLIC; refusing to write a decision record there"
            )
        remote = _git_remote(root).lower() if store_top is not None else ""
        if any(f"/{name}" in remote or remote.endswith(name) or remote.endswith(name + ".git")
               for name in PUBLIC_REPOSITORY_MARKERS):
            raise DecisionStoreUnavailable(
                f"the decision store {root} is a checkout of a PUBLIC repository ({remote}); "
                "refusing to write a decision record there"
            )
        return cls(root)

    # -------------------------------------------------------------- write

    def path_for(self, record: dict[str, Any]) -> Path:
        slate = str(record.get("slate_date") or "undated")
        batch = str(record.get("batch") or record.get("shard") or "run")
        stamp = str(record.get("created_at") or "").replace(":", "").replace("-", "")[:15]
        return self.root / slate / f"{stamp}-{batch}-{str(record.get('record_id'))[:12]}.json"

    def write(self, record: dict[str, Any], *, git_sync: bool | None = None) -> Path:
        """Write one record atomically. Returns the path. Raises on any failure.

        `git_sync` defaults to the environment variable. A failed commit or push
        raises AFTER the file exists locally, and the message says both."""
        problems = validate_decision_record(record)
        if problems:
            raise DecisionStoreUnavailable(
                "refusing to write an invalid decision record: " + "; ".join(problems)
            )
        target = self.path_for(record)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # The same record id at the same instant is the same run. Nothing to
            # do, and nothing to overwrite: a decision record is evidence.
            existing = json.loads(target.read_text(encoding="utf-8"))
            if existing.get("record_id") == record.get("record_id"):
                return target
            raise DecisionStoreUnavailable(
                f"a different decision record already exists at {target}; refusing to overwrite "
                "decision-time evidence"
            )
        encoded = json.dumps(record, indent=1, sort_keys=True, default=str) + "\n"
        fd, temp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        sync = (os.environ.get(ENV_GIT_SYNC, "").strip().lower() in ("1", "true", "yes")
                if git_sync is None else git_sync)
        if sync:
            self._git_sync(target)
        return target

    def _git_sync(self, target: Path) -> None:
        if _git_toplevel(self.root) is None:
            raise DecisionStoreUnavailable(
                f"{ENV_GIT_SYNC} is set but the decision store {self.root} is not a git working "
                f"tree; the record was written to {target} and is NOT yet durable"
            )
        relative = target.relative_to(_git_toplevel(self.root))  # type: ignore[arg-type]
        for argv in (
            ["git", "-C", str(self.root), "add", "--", str(relative)],
            ["git", "-C", str(self.root), "commit", "-q", "-m",
             f"decision record {target.stem}", "--", str(relative)],
            ["git", "-C", str(self.root), "push", "-q"],
        ):
            result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=300)
            if result.returncode != 0:
                raise DecisionStoreUnavailable(
                    f"git {argv[3]} failed in the decision store ({result.stderr.strip()[:200]}); "
                    f"the record was written to {target} and is NOT yet durable"
                )

    # --------------------------------------------------------------- read

    def records(self) -> Iterator[dict[str, Any]]:
        """Every readable record under the root, oldest created first.

        A file that is not a record of a supported schema version is skipped
        WITH the reason available through `problems()`; it is never guessed
        at."""
        found: list[tuple[str, dict[str, Any]]] = []
        for path in sorted(self.root.rglob("*.json")):
            if path.name.startswith(".tmp-"):
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            if record.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
                continue
            if validate_decision_record(record):
                continue
            record = dict(record)
            record["_path"] = str(path)
            found.append((str(record.get("created_at") or ""), record))
        found.sort(key=lambda pair: pair[0])
        for _created, record in found:
            yield record

    def problems(self) -> list[str]:
        """Files under the root that are not readable records, with the reason."""
        out: list[str] = []
        for path in sorted(self.root.rglob("*.json")):
            if path.name.startswith(".tmp-"):
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                out.append(f"{path}: unreadable ({exc})")
                continue
            if not isinstance(record, dict):
                out.append(f"{path}: not an object")
                continue
            if record.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
                out.append(f"{path}: schema_version {record.get('schema_version')!r} is not supported")
                continue
            issues = validate_decision_record(record)
            if issues:
                out.append(f"{path}: {'; '.join(issues)}")
        return out
