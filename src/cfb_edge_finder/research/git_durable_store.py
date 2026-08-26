"""Milestone E, Part A: the git-level half of durable persistence --
surviving TWO workflow runs writing to the `research-data` branch at
close to the same time.

Defense in depth, two independent layers:
  1. The workflow YAML sets `concurrency: group: research-data-write`, so
     GitHub Actions itself normally serializes writers -- this is the
     primary protection and makes layer 2 rare in practice.
  2. `commit_and_push_with_retry` below is the fallback for the cases
     concurrency groups don't cover (a manual `workflow_dispatch` run
     overlapping a scheduled one, a retried job attempt): before every
     push attempt it resets the local branch to the FRESHLY FETCHED
     remote tip and re-runs the caller's `apply_fn` (which internally
     calls research.persistence's append-with-dedup against that fresh
     content) before committing again. Because dedup is always
     recomputed from the just-fetched file content, and every write is a
     pure end-of-file append, this converges without ever invoking `git
     merge` on the data files -- there is no line-level conflict to
     resolve, only a possible extra retry loop.

Mirrors docs/STORAGE_STRATEGY.md's carried-forward discipline: never
trust an automated git operation's exit code for a safety-critical
commit -- every step below checks its own `returncode` explicitly rather
than assuming success.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cfb_edge_finder.research.persistence import AppendResult


class GitDurableStoreError(RuntimeError):
    pass


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)


def ensure_branch_checked_out(repo_dir: Path, branch: str, remote: str = "origin") -> None:
    """Fetches `branch` and checks it out, creating a fresh ORPHAN branch
    (no shared history with main -- keeps bot commits fully out of main's
    line of history) the first time it does not exist remotely yet."""
    fetch = _run(["git", "fetch", remote, branch], repo_dir)
    if fetch.returncode == 0:
        checkout = _run(["git", "checkout", "-B", branch, f"{remote}/{branch}"], repo_dir)
        if checkout.returncode != 0:
            raise GitDurableStoreError(f"checkout of {branch!r} failed: {checkout.stderr}")
        return

    # Remote branch does not exist yet -- create it as a fresh orphan.
    orphan = _run(["git", "checkout", "--orphan", branch], repo_dir)
    if orphan.returncode != 0:
        raise GitDurableStoreError(f"orphan checkout of {branch!r} failed: {orphan.stderr}")
    _run(["git", "rm", "-rf", "--cached", "."], repo_dir)


def _reset_to_remote_tip(repo_dir: Path, branch: str, remote: str = "origin") -> None:
    fetch = _run(["git", "fetch", remote, branch], repo_dir)
    if fetch.returncode != 0:
        raise GitDurableStoreError(f"fetch of {branch!r} failed: {fetch.stderr}")
    reset = _run(["git", "reset", "--hard", f"{remote}/{branch}"], repo_dir)
    if reset.returncode != 0:
        raise GitDurableStoreError(f"reset to {remote}/{branch} failed: {reset.stderr}")


@dataclass(frozen=True)
class PushResult:
    attempts: int
    append_result: AppendResult


def commit_and_push_with_retry(
    repo_dir: Path,
    branch: str,
    apply_fn: Callable[[Path], AppendResult],
    commit_message: str,
    *,
    remote: str = "origin",
    max_retries: int = 5,
) -> PushResult:
    """`apply_fn(repo_dir)` must perform the actual file writes (via
    research.persistence's append_* helpers) against the CURRENT on-disk
    state of `repo_dir` and return the AppendResult. Called once per
    attempt -- on a rejected push, the branch is hard-reset to the fresh
    remote tip (discarding only the LOCAL commit just made, never
    anything already pushed by the other run) and `apply_fn` runs again
    against that updated content, so genuinely-new rows are re-appended
    while rows the other run already wrote are correctly re-detected as
    duplicates and skipped."""
    last_result: AppendResult | None = None
    for attempt in range(1, max_retries + 1):
        result = apply_fn(repo_dir)
        last_result = result

        status = _run(["git", "status", "--porcelain"], repo_dir)
        if not status.stdout.strip():
            # Nothing new to commit this attempt (everything was already
            # present, e.g. the other run wrote the same logical rows).
            return PushResult(attempts=attempt, append_result=result)

        conflict_check = _run(["git", "diff", "--name-only", "--diff-filter=U"], repo_dir)
        if conflict_check.stdout.strip():
            raise GitDurableStoreError(
                f"unexpected unmerged paths before commit: {conflict_check.stdout!r} -- refusing to commit"
            )

        add = _run(["git", "add", "-A"], repo_dir)
        if add.returncode != 0:
            raise GitDurableStoreError(f"git add failed: {add.stderr}")
        commit = _run(["git", "commit", "-m", commit_message], repo_dir)
        if commit.returncode != 0:
            raise GitDurableStoreError(f"git commit failed: {commit.stderr}")

        push = _run(["git", "push", remote, f"HEAD:{branch}"], repo_dir)
        if push.returncode == 0:
            return PushResult(attempts=attempt, append_result=result)

        # Rejected -- almost certainly a non-fast-forward race with
        # another writer. Reset to the fresh remote tip and retry;
        # apply_fn will recompute dedup fresh against the merged state.
        if attempt == max_retries:
            raise GitDurableStoreError(
                f"push to {branch!r} failed after {max_retries} attempts: {push.stderr}"
            )
        _reset_to_remote_tip(repo_dir, branch, remote)

    assert last_result is not None  # max_retries >= 1 guarantees at least one iteration
    return PushResult(attempts=max_retries, append_result=last_result)
