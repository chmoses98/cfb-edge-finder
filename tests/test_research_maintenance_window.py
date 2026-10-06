"""The cutover freeze: no durable write may land between the sharding
merge and the migration.

*** THE RACE UNDER TEST ***
After the merge, readers accept a legacy monolith AND shards, but every
new write goes to a shard. So a scheduled capture that runs in the gap
creates the first shard, the monolith and the shards coexist, and the
migration -- which refuses to migrate blindly into a populated shard set
-- has nothing safe to do. The corpus is then split across two layouts
with GH001 still blocking every push.

`concurrency: group: research-data-write` does NOT prevent this. It
serializes writers; the capture in the gap holds the group entirely
legitimately and runs alone exactly as designed. Serialization is not
exclusion, so the freeze is a separate mechanism, asserted here against
the REAL push path rather than a mock of it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cfb_edge_finder.research import git_durable_store as store
from cfb_edge_finder.research import maintenance, persistence, shards

BRANCH = "research-data"


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    """A real bare remote and a real clone -- the freeze is a property of
    git state on the remote, so a fake cannot show it works."""
    bare = tmp_path / "remote.git"
    clone = tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "t")):
        subprocess.run(["git", "config", key, value], cwd=clone, check=True)
    (clone / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=clone, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "HEAD:main"], cwd=clone, check=True)
    return bare, clone


def _apply(key: str = "k1"):
    def apply_fn(repo_dir: Path) -> persistence.AppendResult:
        return persistence.append_sharded_json_rows(
            repo_dir / "data" / "research",
            shards.OBSERVATIONS_SUBDIR,
            2026,
            [{"observation_key": key, "observation": {"captured_at": "2026-09-12T10:00:00+00:00"}}],
            persistence.observation_key_of,
        )
    return apply_fn


def _open_window(clone: Path, *, reason: str = "shard cutover", body: str | None = None) -> None:
    """Open a window the way the CLI does: one file, pushed to the
    branch, nothing else touched."""
    work = clone.parent / "opener"
    subprocess.run(
        ["git", "clone", "-q", "--branch", BRANCH, str(clone.parent / "remote.git"), str(work)],
        check=True,
    )
    for key, value in (("user.email", "t@example.com"), ("user.name", "t")):
        subprocess.run(["git", "config", key, value], cwd=work, check=True)
    flag = work / maintenance.MAINTENANCE_FLAG_PATH
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(
        body
        if body is not None
        else json.dumps(maintenance.build_flag_payload(reason=reason, opened_by="ceo"), indent=2),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-f", "--", maintenance.MAINTENANCE_FLAG_PATH], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "open window"], cwd=work, check=True)
    subprocess.run(["git", "push", "-q", "origin", f"HEAD:{BRANCH}"], cwd=work, check=True)


def _close_window(clone: Path) -> None:
    work = clone.parent / "closer"
    subprocess.run(
        ["git", "clone", "-q", "--branch", BRANCH, str(clone.parent / "remote.git"), str(work)],
        check=True,
    )
    for key, value in (("user.email", "t@example.com"), ("user.name", "t")):
        subprocess.run(["git", "config", key, value], cwd=work, check=True)
    subprocess.run(["git", "rm", "-q", "--", maintenance.MAINTENANCE_FLAG_PATH], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "close window"], cwd=work, check=True)
    subprocess.run(["git", "push", "-q", "origin", f"HEAD:{BRANCH}"], cwd=work, check=True)


def _remote_rows(clone: Path) -> list[str]:
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", f"origin/{BRANCH}", "--", "data/research"],
        cwd=clone, capture_output=True, text=True, check=True,
    )
    return [p for p in listing.stdout.split() if p.endswith(".jsonl")]


# --- the freeze, against the real push path -------------------------------


def test_a_writer_cannot_push_while_the_window_is_open(tmp_path):
    """THE cutover guarantee: the sequence merge -> writer -> shard
    cannot happen, because the writer refuses."""
    _, clone = _repo(tmp_path)
    store.ensure_branch_checked_out(clone, BRANCH)
    store.commit_and_push_with_retry(clone, BRANCH, _apply("before"), "seed")
    before = _remote_rows(clone)
    assert before, "fixture should have written a shard before the freeze"

    _open_window(clone)

    with pytest.raises(maintenance.MaintenanceWindowActive) as exc:
        store.commit_and_push_with_retry(clone, BRANCH, _apply("during"), "must not land")

    assert "shard cutover" in str(exc.value)
    subprocess.run(["git", "fetch", "-q", "origin", BRANCH], cwd=clone, check=True)
    assert _remote_rows(clone) == before, "a write landed during the maintenance window"


def test_the_refusal_happens_before_any_work_is_done(tmp_path):
    """A frozen window must cost a capture run nothing -- no CFBD quota,
    no scan, no apply. So apply_fn is never called at all."""
    _, clone = _repo(tmp_path)
    store.ensure_branch_checked_out(clone, BRANCH)
    store.commit_and_push_with_retry(clone, BRANCH, _apply("seed"), "seed")
    _open_window(clone)

    calls: list[int] = []

    def counting_apply(repo_dir: Path) -> persistence.AppendResult:
        calls.append(1)
        return _apply("x")(repo_dir)

    with pytest.raises(maintenance.MaintenanceWindowActive):
        store.commit_and_push_with_retry(clone, BRANCH, counting_apply, "nope")
    assert calls == [], "apply_fn ran despite a frozen window"


def test_checkout_refuses_early_so_a_run_spends_nothing(tmp_path):
    """`ensure_branch_checked_out` is the first durable call a capture
    makes, ahead of the scan -- refusing there is what makes a frozen
    window free."""
    _, clone = _repo(tmp_path)
    store.ensure_branch_checked_out(clone, BRANCH)
    store.commit_and_push_with_retry(clone, BRANCH, _apply("seed"), "seed")
    _open_window(clone)

    with pytest.raises(maintenance.MaintenanceWindowActive):
        store.ensure_branch_checked_out(clone, BRANCH)


def test_closing_the_window_restores_writes(tmp_path):
    """The freeze must be fully reversible, or the cutover cannot end."""
    _, clone = _repo(tmp_path)
    store.ensure_branch_checked_out(clone, BRANCH)
    store.commit_and_push_with_retry(clone, BRANCH, _apply("seed"), "seed")
    _open_window(clone)
    with pytest.raises(maintenance.MaintenanceWindowActive):
        store.commit_and_push_with_retry(clone, BRANCH, _apply("blocked"), "blocked")

    _close_window(clone)

    store.ensure_branch_checked_out(clone, BRANCH)
    result = store.commit_and_push_with_retry(clone, BRANCH, _apply("after"), "after")
    assert result.append_result.written == 1
    subprocess.run(["git", "fetch", "-q", "origin", BRANCH], cwd=clone, check=True)
    body = subprocess.run(
        ["git", "show", f"origin/{BRANCH}:{_remote_rows(clone)[0]}"],
        cwd=clone, capture_output=True, text=True, check=True,
    ).stdout
    assert "after" in body


def test_read_only_paths_are_untouched_by_the_window(tmp_path):
    """`--no-push` diagnostics never enter the push path, so every dry
    run, probe and readiness check keeps working during a cutover --
    which is when an operator most needs to look at the corpus."""
    _, clone = _repo(tmp_path)
    store.ensure_branch_checked_out(clone, BRANCH)
    store.commit_and_push_with_retry(clone, BRANCH, _apply("seed"), "seed")
    _open_window(clone)

    base = clone / "data" / "research"
    sources = persistence.corpus_sources(base, shards.OBSERVATIONS_SUBDIR, 2026)
    assert sources, "the seeded shard should be on disk"
    lines = [line for _path, line in shards.iter_raw_lines(sources)]
    assert any("seed" in line for line in lines), "a read-only corpus scan was blocked by the window"


# --- fail-closed ----------------------------------------------------------


def test_an_unparseable_flag_still_freezes(tmp_path):
    """Presence is the signal, not content: a truncated or half-written
    flag must not silently thaw the branch."""
    _, clone = _repo(tmp_path)
    store.ensure_branch_checked_out(clone, BRANCH)
    store.commit_and_push_with_retry(clone, BRANCH, _apply("seed"), "seed")
    _open_window(clone, body="{ this is not json")

    state = maintenance.read_window(clone, BRANCH)
    assert state.active and state.unparseable
    with pytest.raises(maintenance.MaintenanceWindowActive):
        store.commit_and_push_with_retry(clone, BRANCH, _apply("x"), "x")


def test_an_unreachable_remote_refuses_rather_than_assuming_open(tmp_path):
    """Three answers, not two. 'Cannot tell' takes the cheap side: one
    skipped capture cycle beats a write landing mid-migration."""
    _, clone = _repo(tmp_path)
    store.ensure_branch_checked_out(clone, BRANCH)
    store.commit_and_push_with_retry(clone, BRANCH, _apply("seed"), "seed")

    subprocess.run(
        ["git", "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git")],
        cwd=clone, check=True,
    )
    subprocess.run(["git", "update-ref", "-d", f"refs/remotes/origin/{BRANCH}"], cwd=clone, check=True)

    with pytest.raises(maintenance.MaintenanceWindowUnknown):
        maintenance.read_window(clone, BRANCH)


def test_a_branch_that_does_not_exist_yet_is_not_frozen(tmp_path):
    """First run ever: there is nothing there to freeze, and refusing
    would make the store impossible to bootstrap."""
    _, clone = _repo(tmp_path)
    assert maintenance.read_window(clone, BRANCH).active is False


def test_no_window_means_writes_are_allowed(tmp_path):
    _, clone = _repo(tmp_path)
    store.ensure_branch_checked_out(clone, BRANCH)
    store.commit_and_push_with_retry(clone, BRANCH, _apply("seed"), "seed")
    state = maintenance.assert_writes_allowed(clone, BRANCH)
    assert state.active is False
    assert "CLOSED" in state.describe()


# --- the flag itself ------------------------------------------------------


def test_the_flag_lives_under_a_path_the_durable_store_already_stages():
    """Otherwise opening a window would need a new staging rule, and the
    one that got forgotten would be the one that mattered."""
    assert any(
        maintenance.MAINTENANCE_FLAG_PATH.startswith(p)
        for p in store.DURABLE_STORE_PATHS
    )


def test_the_flag_payload_records_who_why_and_when():
    payload = maintenance.build_flag_payload(reason="shard cutover", opened_by="ceo")
    assert payload["reason"] == "shard cutover"
    assert payload["opened_by"] == "ceo"
    assert payload["opened_at"]
    assert "research_maintenance_window.py" in payload["note"]
