"""The durable store's two new defences against the GH001 outage.

1. **Fail fast on a deterministic rejection.** The retry loop exists for
   a concurrent-writer RACE: another run moved the tip, so re-fetching
   and re-applying genuinely changes the outcome. A GH001 oversize
   rejection is the opposite -- the same bytes are refused identically
   every time -- and the live incident burned five full attempts (each
   with a fetch and a complete re-apply) per run, every ten minutes, to
   rediscover the error it already had after the first.

2. **Refuse before pushing at all.** A changed blob above
   `shards.PUSH_REFUSAL_BYTES` is caught locally, so the operator gets a
   precise error naming the file and its size rather than a pre-receive
   rejection after a wasted round trip.

The race behaviour must be UNCHANGED -- that is what the concurrency
tests in test_research_git_sync.py depend on -- so it is asserted here
too, against the real retry loop.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cfb_edge_finder.research import git_durable_store as store
from cfb_edge_finder.research import persistence, shards

BRANCH = "research-data"

# The exact stderr GitHub produced in the 2026-09-14 incident.
GH001_STDERR = (
    "remote: warning: File data/research/shadow/2026.jsonl is 70.39 MB; this is larger than "
    "GitHub's recommended maximum file size of 50.00 MB        \n"
    "remote: error: File data/research/observations/2026.jsonl is 105.20 MB; this exceeds "
    "GitHub's file size limit of 100.00 MB        \n"
    "remote: error: GH001: Large files detected. You may want to try Git Large File Storage - "
    "https://git-lfs.github.com.        \n"
    " ! [remote rejected] HEAD -> research-data (pre-receive hook declined)\n"
    "error: failed to push some refs to 'https://github.com/chmoses98/cfb-edge-finder'\n"
)

NON_FAST_FORWARD_STDERR = (
    " ! [rejected]        HEAD -> research-data (fetch first)\n"
    "error: failed to push some refs to 'https://github.com/chmoses98/cfb-edge-finder'\n"
    "hint: Updates were rejected because the remote contains work that you do not have locally.\n"
)


# --- classification -------------------------------------------------------


def test_the_live_gh001_stderr_is_classified_deterministic_and_oversize():
    assert store._push_failure_is_deterministic(GH001_STDERR)
    assert store._push_failure_is_oversize(GH001_STDERR)


def test_a_non_fast_forward_race_is_not_classified_deterministic():
    """The regression that would silently disable concurrency recovery."""
    assert not store._push_failure_is_deterministic(NON_FAST_FORWARD_STDERR)
    assert not store._push_failure_is_oversize(NON_FAST_FORWARD_STDERR)


@pytest.mark.parametrize(
    "stderr",
    [
        " ! [rejected] HEAD -> research-data (non-fast-forward)\n",
        "error: cannot lock ref 'refs/heads/research-data'\n",
        "fatal: unable to access 'https://github.com/...': Could not resolve host\n",
        "error: RPC failed; curl 92 HTTP/2 stream 5 was not closed cleanly\n",
    ],
)
def test_transient_and_race_failures_still_retry(stderr):
    assert not store._push_failure_is_deterministic(stderr)


# --- the retry loop, against a real local remote --------------------------


def _repo(tmp_path: Path) -> Path:
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "T")):
        subprocess.run(["git", "config", key, value], cwd=clone, check=True)
    (clone / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "README.md"], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=clone, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "HEAD:main"], cwd=clone, check=True)
    return clone


def _apply(repo_dir: Path) -> persistence.AppendResult:
    return persistence.append_sharded_json_rows(
        repo_dir / "data" / "research",
        shards.OBSERVATIONS_SUBDIR,
        2026,
        [
            {
                "observation_key": "k1",
                "observation": {"captured_at": "2026-09-12T10:00:00+00:00"},
            }
        ],
        persistence.observation_key_of,
    )


def _fail_push_with(monkeypatch, stderr: str, attempts: list[int]):
    real_run = store._run

    def fake_run(args, cwd):
        if args[:2] == ["git", "push"]:
            attempts.append(1)
            return subprocess.CompletedProcess(args, 1, "", stderr)
        return real_run(args, cwd)

    monkeypatch.setattr(store, "_run", fake_run)


def test_a_gh001_rejection_fails_on_the_first_attempt(tmp_path, monkeypatch):
    clone = _repo(tmp_path)
    store.ensure_branch_checked_out(clone, BRANCH)
    attempts: list[int] = []
    _fail_push_with(monkeypatch, GH001_STDERR, attempts)

    with pytest.raises(store.GitDurableStoreOversizeError) as exc:
        store.commit_and_push_with_retry(clone, BRANCH, _apply, "test", max_retries=5)

    assert len(attempts) == 1, f"GH001 was retried {len(attempts)} times instead of failing fast"
    assert "attempt 1" in str(exc.value)
    assert "GH001" in str(exc.value)


def test_a_non_fast_forward_rejection_still_uses_every_retry(tmp_path, monkeypatch):
    """The behaviour the concurrent-writer recovery depends on.

    Seeded with a real successful push first, because a retry resets to
    the remote tip -- which is exactly the loop being asserted, and it
    needs a tip to reset to."""
    clone = _repo(tmp_path)
    store.ensure_branch_checked_out(clone, BRANCH)
    store.commit_and_push_with_retry(clone, BRANCH, _apply, "seed")

    attempts: list[int] = []
    _fail_push_with(monkeypatch, NON_FAST_FORWARD_STDERR, attempts)

    # A distinct key per attempt, so every attempt genuinely has
    # something to commit (a re-applied duplicate would short-circuit).
    def apply_new(repo_dir: Path) -> persistence.AppendResult:
        return persistence.append_sharded_json_rows(
            repo_dir / "data" / "research",
            shards.OBSERVATIONS_SUBDIR,
            2026,
            [
                {
                    "observation_key": f"attempt-{len(attempts)}",
                    "observation": {"captured_at": "2026-09-12T10:00:00+00:00"},
                }
            ],
            persistence.observation_key_of,
        )

    with pytest.raises(store.GitDurableStoreError) as exc:
        store.commit_and_push_with_retry(clone, BRANCH, apply_new, "test", max_retries=5)

    assert len(attempts) == 5, f"a race was retried {len(attempts)} times, expected 5"
    assert "after 5 attempts" in str(exc.value)
    assert not isinstance(exc.value, store.GitDurableStoreOversizeError)


def test_an_oversize_error_is_a_gitdurablestoreerror_so_callers_are_unaffected():
    assert issubclass(store.GitDurableStoreOversizeError, store.GitDurableStoreError)


# --- the pre-push local guard --------------------------------------------


def test_a_changed_oversize_blob_is_refused_before_the_push(tmp_path, monkeypatch):
    clone = _repo(tmp_path)
    store.ensure_branch_checked_out(clone, BRANCH)
    pushed: list[int] = []

    real_run = store._run

    def fake_run(args, cwd):
        if args[:2] == ["git", "push"]:
            pushed.append(1)
            return subprocess.CompletedProcess(args, 0, "", "")
        return real_run(args, cwd)

    monkeypatch.setattr(store, "_run", fake_run)

    def apply_big(repo_dir: Path) -> persistence.AppendResult:
        path = repo_dir / "data" / "research" / "observations" / "2026.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 5000)
        return persistence.AppendResult(written=1, skipped_duplicate=0)

    with pytest.raises(store.GitDurableStoreOversizeError) as exc:
        store.commit_and_push_with_retry(
            clone, BRANCH, apply_big, "test", max_retries=5, max_blob_bytes=1000
        )

    assert pushed == [], "the oversize blob was pushed instead of refused locally"
    assert "observations/2026.jsonl" in str(exc.value)
    assert "GH001" in str(exc.value)


def test_an_unchanged_oversize_blob_does_not_block_an_unrelated_write(tmp_path, monkeypatch):
    """An oversize file already on the remote creates no new git object,
    so it cannot trip GH001 and must not block a write that does not
    touch it -- otherwise the guard would be a second outage."""
    clone = _repo(tmp_path)
    store.ensure_branch_checked_out(clone, BRANCH)

    big = clone / "data" / "research" / "observations" / "2026.jsonl"
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_bytes(b"x" * 5000)
    store.commit_and_push_with_retry(
        clone, BRANCH, lambda _d: persistence.AppendResult(0, 0), "seed big", max_blob_bytes=10_000
    )

    # Now write something small, with the guard set below the existing
    # blob's size. The unchanged big file must be ignored.
    result = store.commit_and_push_with_retry(
        clone, BRANCH, _apply, "small write", max_blob_bytes=1000
    )
    assert result.append_result.written == 1


def test_a_normal_sharded_write_passes_the_production_guard(tmp_path):
    clone = _repo(tmp_path)
    store.ensure_branch_checked_out(clone, BRANCH)
    result = store.commit_and_push_with_retry(clone, BRANCH, _apply, "normal write")
    assert result.append_result.written == 1
    assert result.attempts == 1

    tracked = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", f"origin/{BRANCH}"],
        cwd=clone, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert tracked == [
        str(shards.shard_path(Path("data/research"), shards.OBSERVATIONS_SUBDIR, 2026, "2026-09-12", 1))
    ]


# --- no Git LFS anywhere --------------------------------------------------


def test_the_repository_introduces_no_git_lfs():
    """The fix is the storage layout. LFS would move the problem into a
    quota-bearing pointer store and change how every consumer reads the
    corpus."""
    root = Path(__file__).resolve().parents[1]
    assert not (root / ".gitattributes").exists() or "filter=lfs" not in (
        root / ".gitattributes"
    ).read_text()
    for pattern in ("git-lfs", "git lfs", "lfs.fetchexclude", "lfs install"):
        hits = subprocess.run(
            ["git", "grep", "-il", pattern, "--", "src", "scripts", ".github", "tests"],
            cwd=root, capture_output=True, text=True,
        ).stdout.split()
        # research/shards.py and this file name LFS only to say it is not used.
        assert not [h for h in hits if h != "tests/test_research_push_guard.py"], (
            f"unexpected Git LFS reference in {hits}"
        )
