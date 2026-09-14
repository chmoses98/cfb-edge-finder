#!/usr/bin/env python3
"""Open, close, and inspect the research-data maintenance window.

    python scripts/research_maintenance_window.py --status
    python scripts/research_maintenance_window.py --open  --reason "shard cutover" --opened-by "<name>"
    python scripts/research_maintenance_window.py --close --reason "cutover complete"

*** WHAT THE WINDOW IS ***
One file on the `research-data` branch. While it exists, every durable
writer refuses to push (see research/maintenance.py for why it lives
there, why presence rather than content is the signal, and why an
unreadable state also refuses).

*** WHY THIS TOOL PUSHES WITH RAW GIT ***
It cannot use `git_durable_store.commit_and_push_with_retry`: that
function is precisely what the window blocks, so routing the CLOSE
through it would make the window impossible to leave. This is the one
deliberate, human-invoked path allowed to write to the branch while a
window is open -- which is also why it takes `--reason` and `--opened-by`
and records them, rather than being a silent toggle.

It is otherwise as small as it can be: fetch, add or remove exactly one
path in a detached worktree, commit, push. It never touches
`data/research/**` corpus files, so it cannot alter a single observation
even if it is run at the wrong moment.

*** THE WINDOW IS NOT THE ONLY LOCK ***
It stops writers running the MERGED code. It cannot stop a run that
started before the merge and is still in flight on the old code, and it
cannot stop a raw `git push` from a workflow that does not consult it.
The cutover runbook (docs/CUTOVER_SHARDING.md) therefore disables the
workflows as well and verifies the branch tip has not moved before
merging. Two independent locks, because either one alone has a gap.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.research import maintenance  # noqa: E402


def _run(args: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    if check and done.returncode != 0:
        raise SystemExit(f"ERROR: {' '.join(args)} failed:\n{done.stderr.strip()}")
    return done


def _apply(repo_dir: Path, branch: str, remote: str, *, payload: dict | None, message: str) -> str:
    """Add (payload) or remove (None) the flag on `branch`, in a scratch
    worktree so the caller's checkout is never disturbed."""
    _run(["git", "fetch", remote, branch], repo_dir)
    work = Path(tempfile.mkdtemp(prefix="research-maintenance-"))
    tree = work / "wt"
    try:
        _run(["git", "worktree", "add", "--detach", str(tree), f"{remote}/{branch}"], repo_dir)
        flag = tree / maintenance.MAINTENANCE_FLAG_PATH
        if payload is None:
            if not flag.is_file():
                print(f"(no window open on {branch!r}; nothing to close)")
                return ""
            _run(["git", "rm", "--", maintenance.MAINTENANCE_FLAG_PATH], tree)
        else:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            _run(["git", "add", "-f", "--", maintenance.MAINTENANCE_FLAG_PATH], tree)

        if _run(["git", "diff", "--cached", "--quiet"], tree, check=False).returncode == 0:
            print("(flag already in the requested state; nothing to push)")
            return ""

        # Nothing but the flag may ride along. The corpus is mid-cutover
        # and this tool has no business committing any part of it.
        staged = _run(["git", "diff", "--cached", "--name-only"], tree).stdout.split()
        if staged != [maintenance.MAINTENANCE_FLAG_PATH]:
            raise SystemExit(
                f"ERROR: refusing to push -- staged {staged}, expected only "
                f"{[maintenance.MAINTENANCE_FLAG_PATH]}"
            )

        _run(["git", "commit", "-m", message], tree)
        head = _run(["git", "rev-parse", "HEAD"], tree).stdout.strip()
        _run(["git", "push", remote, f"HEAD:{branch}"], tree)
        return head
    finally:
        _run(["git", "worktree", "remove", "--force", str(tree)], repo_dir, check=False)
        shutil.rmtree(work, ignore_errors=True)


def main_with_args(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true", help="report the window state and exit")
    mode.add_argument("--open", action="store_true", help="freeze durable writes")
    mode.add_argument("--close", action="store_true", help="reopen durable writes")
    parser.add_argument("--repo-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--branch", default="research-data")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--reason", default=None, help="why (required for --open)")
    parser.add_argument("--opened-by", default=None, help="who (required for --open)")
    parser.add_argument("--expected-reopen", default=None, help="free text, e.g. 'within 1h'")
    args = parser.parse_args(argv)

    if args.status:
        try:
            state = maintenance.read_window(args.repo_dir, args.branch, remote=args.remote)
        except maintenance.MaintenanceWindowUnknown as exc:
            print(f"UNKNOWN: {exc}", file=sys.stderr)
            # Unknown is not "closed". Exit 2 so a script that gates on
            # this cannot mistake it for a clean, writable branch.
            return 2
        print(state.describe())
        return 1 if state.active else 0

    if args.open:
        if not args.reason or not args.opened_by:
            print("ERROR: --open requires --reason and --opened-by", file=sys.stderr)
            return 2
        now = datetime.now(UTC)
        payload = maintenance.build_flag_payload(
            reason=args.reason,
            opened_by=args.opened_by,
            expected_reopen=args.expected_reopen,
            now=now,
        )
        head = _apply(
            args.repo_dir, args.branch, args.remote,
            payload=payload,
            message=f"maintenance window OPEN: {args.reason} (by {args.opened_by} at {now.isoformat()})",
        )
        print(f"\nOPENED maintenance window on {args.branch!r}" + (f" at {head}" if head else ""))
        print("Durable writes are now frozen. Workflows must ALSO be disabled -- see")
        print("docs/CUTOVER_SHARDING.md; this flag cannot stop an already-running job")
        print("on pre-merge code, nor a workflow that pushes with raw git.")
        return 0

    now = datetime.now(UTC)
    head = _apply(
        args.repo_dir, args.branch, args.remote,
        payload=None,
        message=f"maintenance window CLOSED: {args.reason or 'reopening writes'} at {now.isoformat()}",
    )
    print(f"\nCLOSED maintenance window on {args.branch!r}" + (f" at {head}" if head else ""))
    print("Durable writes are permitted again.")
    return 0


def main() -> int:
    return main_with_args()


if __name__ == "__main__":
    raise SystemExit(main())
