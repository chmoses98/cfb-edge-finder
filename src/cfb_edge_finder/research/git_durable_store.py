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


DURABLE_STORE_PATHS: tuple[str, ...] = ("data/research",)
"""The only paths this module ever stages/commits on the durable-store
branch -- see the module-level bug note in commit_and_push_with_retry's
docstring for why this must never be a bare `git add -A`."""


def ensure_branch_checked_out(repo_dir: Path, branch: str, remote: str = "origin") -> None:
    """Fetches `branch` and checks it out, creating a fresh ORPHAN branch
    (no shared history with main -- keeps bot commits fully out of main's
    line of history) the first time it does not exist remotely yet.

    *** WHY THE ORPHAN PATH DOES NOT `rm` THE WORKING TREE ***
    `git checkout --orphan` detaches the index from history but leaves
    every file from the PREVIOUS branch (main -- this runs in the same
    checkout `actions/checkout@v4` already populated) sitting on disk,
    merely unstaged by the `git rm --cached` below. This is intentional
    and safe now that `commit_and_push_with_retry` only ever stages
    `DURABLE_STORE_PATHS` (never a bare `git add -A`) -- see that
    function's docstring for the real incident this fixes: an earlier
    version staged everything, and because `data/research/` is
    (correctly, on main/feature branches) `.gitignore`d, `git add -A`
    silently skipped the actual data and committed main's entire stray
    source tree instead. Wiping the working tree here would risk deleting
    files a caller's already-imported-but-not-yet-executed deferred
    import still expects to find on disk; scoping the ADD instead avoids
    that risk entirely while fixing the real bug."""
    fetch = _run(["git", "fetch", remote, branch], repo_dir)
    if fetch.returncode == 0:
        checkout = _run(["git", "checkout", "-B", branch, f"{remote}/{branch}"], repo_dir)
        if checkout.returncode != 0:
            raise GitDurableStoreError(f"checkout of {branch!r} failed: {checkout.stderr}")
        return

    # *** A FAILED FETCH IS NOT AN ABSENT BRANCH ***
    # `git fetch` exits non-zero for a missing branch AND for a network
    # blip, an auth expiry, or a GitHub outage. Treating every non-zero
    # as "first run, start fresh" meant a transient failure silently
    # continued against an EMPTY corpus: every already-captured label
    # looked due again, so the run re-priced the whole slate and reported
    # (observed live) 1,337 captures due where the true answer was 0.
    #
    # That failed safe -- the orphan has no shared history, so the
    # non-forced push below is rejected as non-fast-forward and
    # _reset_to_remote_tip recovers the real corpus (verified against a
    # real remote: the existing rows survive untouched). But it burns a
    # full scan and, worse, reports wildly wrong telemetry during exactly
    # the incident an operator is trying to read. So ask the remote
    # directly what exists, and fail loudly when we cannot tell.
    listing = _run(["git", "ls-remote", "--heads", remote, branch], repo_dir)
    if listing.returncode != 0:
        raise GitDurableStoreError(
            f"cannot reach {remote!r} to determine whether {branch!r} exists "
            f"(fetch: {fetch.stderr.strip()!r}; ls-remote: {listing.stderr.strip()!r}) -- "
            f"refusing to start a fresh orphan branch, which would scan against an empty corpus"
        )
    if listing.stdout.strip():
        raise GitDurableStoreError(
            f"{branch!r} exists on {remote!r} but could not be fetched: {fetch.stderr.strip()!r} -- "
            f"refusing to continue against an empty corpus"
        )

    # Genuinely absent on the remote -- this is the first run.
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
    duplicates and skipped.

    *** WHY THE ADD/EMPTY-CHECK SEQUENCE IS SCOPED TO DURABLE_STORE_PATHS, NEVER `-A` ***
    A live preseason dress rehearsal caught two related bugs here, both
    rooted in the same fact: this repo's own `.gitignore` correctly
    excludes `data/research/` on `main`/feature branches (a safety net
    against an accidental commit of local rehearsal output -- see
    .gitignore's own comment), but `ensure_branch_checked_out`'s orphan
    path runs in the SAME working directory `actions/checkout@v4` already
    populated with `main`'s full source tree (only unstaged via
    `git rm --cached`, never deleted from disk):

    1. A bare `git add -A` re-staged every one of main's non-ignored
       stray files (src/, tests/, docs/, scripts/, ...) while silently
       SKIPPING the one directory that actually needed to be tracked --
       `data/research/` itself, still `.gitignore`d. Two separate live
       GitHub Actions runs an hour apart each committed a stray copy of
       main's source tree and ZERO real observations.
    2. Even after scoping `git add` to `DURABLE_STORE_PATHS` with `-f`
       (overriding the ignore rule deliberately), a plain
       `git status --porcelain` on an ignored path still reports nothing
       by default (ignored paths don't show as untracked) -- so the
       "anything to commit" check returned empty and the loop skipped
       committing/pushing entirely, even though real new data existed on
       disk.

    Fixed by staging FIRST (`git add -f`, idempotent whether or not
    anything actually changed) and then checking `git diff --cached
    --quiet` -- gitignore-agnostic once a path is staged -- to decide
    whether there is genuinely anything new to commit."""
    last_result: AppendResult | None = None
    for attempt in range(1, max_retries + 1):
        result = apply_fn(repo_dir)
        last_result = result

        existing_paths = [p for p in DURABLE_STORE_PATHS if (repo_dir / p).exists()]
        if not existing_paths:
            # apply_fn wrote nothing at all this attempt (a genuine no-op
            # -- distinct from "wrote rows that happened to already
            # exist," which still creates the file) -- `git add` on a
            # path that doesn't exist on disk errors, so short-circuit
            # rather than treat that as a failure.
            return PushResult(attempts=attempt, append_result=result)

        add = _run(["git", "add", "-f", "--", *existing_paths], repo_dir)
        if add.returncode != 0:
            raise GitDurableStoreError(f"git add failed: {add.stderr}")

        staged_diff = _run(["git", "diff", "--cached", "--quiet"], repo_dir)
        if staged_diff.returncode == 0:
            # Nothing staged -- everything was already present (e.g. the
            # other run wrote the same logical rows), so there is
            # genuinely nothing new to commit this attempt.
            return PushResult(attempts=attempt, append_result=result)

        conflict_check = _run(["git", "diff", "--name-only", "--diff-filter=U"], repo_dir)
        if conflict_check.stdout.strip():
            raise GitDurableStoreError(
                f"unexpected unmerged paths before commit: {conflict_check.stdout!r} -- refusing to commit"
            )
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
