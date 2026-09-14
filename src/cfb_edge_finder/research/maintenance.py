"""A fail-closed maintenance window for the `research-data` durable
branch: one flag file, checked by every writer immediately before it
pushes.

*** THE RACE THIS EXISTS TO CLOSE ***
The shard cutover has an ordering hazard that nothing in the write path
previously prevented. After the sharding change merges, readers accept a
legacy monolith AND shards, but every new write goes to a shard. So:

    merge  ->  a scheduled capture runs  ->  it creates the first shard
           ->  the migration now sees monolith + shards side by side
           ->  it refuses (correctly: blind migration into a populated
               shard set is how rows get duplicated)
           ->  the cutover is stuck, with the corpus split across two
               layouts and GH001 still blocking every push

`scripts/migrate_research_shards.py --allow-existing-shards` makes that
state RESOLVABLE rather than terminal. This module makes it
UNREACHABLE: while the window is open, no writer is allowed to push at
all, so the split layout never forms.

*** WHY NOT THE CONCURRENCY GROUP ***
`concurrency: group: research-data-write` serializes writers. It does not
stop one. A capture that starts after the merge and before the migration
holds the group legitimately, runs alone exactly as designed, and lands
its shard in the middle of the cutover. Serialization is not exclusion.

*** WHY THE FLAG LIVES ON research-data, NOT IN THE REPO ***
Every writer -- scheduled capture, conductor-dispatched capture,
settlement, attribution, weekly report, season report -- already fetches
this branch to read the corpus it is about to append to. A flag there is
therefore visible to all of them, on whatever runner, triggered by
whatever event, in whatever repository, with no new secret, no new API
call, and no dependence on a workflow file that a `workflow_dispatch`
could bypass. It is also the same object the push itself races against,
so checking it against the FRESHLY FETCHED tip is checking the exact
state the push will hit.

*** PRESENCE IS THE SIGNAL ***
The file existing means frozen. Not a field inside it -- the file's
existence. Content is informational only (who opened the window, when,
why, when they expect to reopen), so a truncated, half-written or
unparseable flag still freezes rather than silently thawing. Thaw is
`git rm` of one path: an operation with no partial-success state.

*** FAIL CLOSED ***
Three answers, not two: FROZEN, OPEN, and "cannot tell". A failed fetch,
an unreachable remote, an unreadable tree -- anything that leaves the
window's state UNKNOWN -- refuses the write. During a cutover the cost of
a refused write is one skipped ten-minute capture cycle; the cost of a
write that lands mid-migration is a corpus split across two layouts while
the only tool that can repair it has been told to stand down. Those are
not symmetric, so the ambiguous case takes the cheap side.

Read-only paths are deliberately untouched. `--no-push` diagnostics never
call into the push path, so every dry run, probe and readiness check
keeps working normally while the window is open -- which is exactly when
an operator most needs to look at the corpus.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MAINTENANCE_FLAG_PATH = "data/research/MAINTENANCE_WINDOW.json"
"""Inside `data/research/`, so `git_durable_store.DURABLE_STORE_PATHS`
already covers it and no staging rule has to learn about it."""


class MaintenanceWindowError(RuntimeError):
    pass


class MaintenanceWindowActive(MaintenanceWindowError):
    """The window is open: writes are frozen for a cutover. Retrying will
    not help until an operator closes it."""


class MaintenanceWindowUnknown(MaintenanceWindowError):
    """The window's state could not be determined. Treated as frozen --
    see the fail-closed note in the module docstring."""


@dataclass(frozen=True)
class MaintenanceState:
    active: bool
    reason: str | None = None
    opened_at: str | None = None
    opened_by: str | None = None
    expected_reopen: str | None = None
    unparseable: bool = False

    def describe(self) -> str:
        if not self.active:
            return "maintenance window: CLOSED (writes allowed)"
        parts = [f"reason={self.reason!r}" if self.reason else "reason=(unstated)"]
        if self.opened_by:
            parts.append(f"opened_by={self.opened_by}")
        if self.opened_at:
            parts.append(f"opened_at={self.opened_at}")
        if self.expected_reopen:
            parts.append(f"expected_reopen={self.expected_reopen}")
        if self.unparseable:
            parts.append("flag_unparseable=true (frozen anyway -- presence is the signal)")
        return "maintenance window: OPEN -- " + ", ".join(parts)


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)


def build_flag_payload(
    *,
    reason: str,
    opened_by: str,
    expected_reopen: str | None = None,
    now: datetime | None = None,
) -> dict:
    return {
        "active": True,
        "reason": reason,
        "opened_by": opened_by,
        "opened_at": (now or datetime.now(UTC)).isoformat(),
        "expected_reopen": expected_reopen,
        "note": (
            "While this file exists on the research-data branch, every durable "
            "writer refuses to push (research/maintenance.py). Remove it to "
            "reopen writes: scripts/research_maintenance_window.py --close."
        ),
    }


def read_window(
    repo_dir: Path,
    branch: str,
    *,
    remote: str = "origin",
    fetch: bool = True,
) -> MaintenanceState:
    """The window's state on the remote tip of `branch`.

    Raises MaintenanceWindowUnknown when the answer cannot be established
    -- never guesses "open for business"."""
    fetch_error = ""
    if fetch:
        fetched = _run(["git", "fetch", remote, branch], repo_dir)
        fetch_error = fetched.stderr.strip()

    # The remote-tracking ref is what the read below actually needs. It
    # can be missing either because the branch does not exist yet (the
    # very first run, before anything has ever been written) or because
    # the fetch failed -- and those two must not be conflated: the same
    # trap `ensure_branch_checked_out` documents, where a network blip
    # read as "no branch" and the run continued against an empty corpus.
    # So when the ref is absent, ask the remote directly.
    if _run(["git", "rev-parse", "--verify", "--quiet", f"{remote}/{branch}"], repo_dir).returncode != 0:
        listing = _run(["git", "ls-remote", "--heads", remote, branch], repo_dir)
        if listing.returncode != 0:
            raise MaintenanceWindowUnknown(
                f"cannot reach {remote!r} to read the maintenance window on {branch!r} "
                f"(fetch: {fetch_error!r}; ls-remote: {listing.stderr.strip()!r}) "
                "-- refusing the write rather than assuming writes are open"
            )
        if listing.stdout.strip():
            raise MaintenanceWindowUnknown(
                f"{branch!r} exists on {remote!r} but no local {remote}/{branch} ref could be "
                f"obtained (fetch: {fetch_error!r}) -- the maintenance window is unreadable, "
                "refusing the write"
            )
        # Genuinely no such branch yet: nothing is frozen because there
        # is nothing there to freeze.
        return MaintenanceState(active=False)

    listing = _run(
        ["git", "ls-tree", "-r", "--name-only", f"{remote}/{branch}", "--", MAINTENANCE_FLAG_PATH],
        repo_dir,
    )
    if listing.returncode != 0:
        raise MaintenanceWindowUnknown(
            f"cannot list {MAINTENANCE_FLAG_PATH} on {remote}/{branch}: "
            f"{listing.stderr.strip()!r} -- refusing the write"
        )
    if not listing.stdout.strip():
        return MaintenanceState(active=False)

    # The file is THERE. From here on the answer is "frozen" no matter
    # what the bytes say; only the human-readable detail is in doubt.
    shown = _run(["git", "show", f"{remote}/{branch}:{MAINTENANCE_FLAG_PATH}"], repo_dir)
    if shown.returncode != 0:
        return MaintenanceState(active=True, unparseable=True)
    try:
        payload = json.loads(shown.stdout)
    except json.JSONDecodeError:
        return MaintenanceState(active=True, unparseable=True)
    if not isinstance(payload, dict):
        return MaintenanceState(active=True, unparseable=True)
    return MaintenanceState(
        active=True,
        reason=payload.get("reason"),
        opened_at=payload.get("opened_at"),
        opened_by=payload.get("opened_by"),
        expected_reopen=payload.get("expected_reopen"),
    )


def assert_writes_allowed(
    repo_dir: Path,
    branch: str,
    *,
    remote: str = "origin",
    fetch: bool = True,
) -> MaintenanceState:
    """Gate for the durable write path. Returns the (closed) state, or
    raises -- MaintenanceWindowActive when a window is open,
    MaintenanceWindowUnknown when it cannot be read."""
    state = read_window(repo_dir, branch, remote=remote, fetch=fetch)
    if state.active:
        raise MaintenanceWindowActive(
            f"refusing to write to {branch!r}: {state.describe()}. "
            f"A cutover is in progress and writes are frozen deliberately; this run "
            f"captured nothing durable and should NOT be retried until the window closes. "
            f"Operator: scripts/research_maintenance_window.py --status."
        )
    return state
