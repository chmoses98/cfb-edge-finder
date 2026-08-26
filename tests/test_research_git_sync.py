"""Part A: the git-level durability guarantee -- two independent
"workflow runs" writing to the same research-data branch, one racing the
other, converge to the union of both with no data loss and no duplicate
rows. Uses local bare git repos only -- no network required."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "tests")
from research_factories import make_corpus_row, make_observation  # noqa: E402

from cfb_edge_finder.research import git_durable_store, persistence

BRANCH = "research-data"


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    assert result.returncode == 0, f"{args} failed: {result.stderr}"
    return result


def _init_bare_and_clones(tmp_path: Path) -> tuple[Path, Path, Path]:
    bare = tmp_path / "bare.git"
    _run(["git", "init", "--bare", str(bare)], tmp_path)

    clone_a = tmp_path / "clone_a"
    _run(["git", "clone", str(bare), str(clone_a)], tmp_path)
    _run(["git", "config", "user.email", "test@example.com"], clone_a)
    _run(["git", "config", "user.name", "Test Runner"], clone_a)
    # A fresh bare repo has no branches/commits at all -- give it one seed
    # commit on main via clone_a, pushed BEFORE clone_b exists, so every
    # subsequent clone has a real ref to work with before the orphan
    # research-data branch exists (avoids two independent clones both
    # trying to push an unrelated first commit to the same ref).
    (clone_a / "README.md").write_text("seed\n")
    _run(["git", "add", "README.md"], clone_a)
    _run(["git", "commit", "-m", "seed"], clone_a)
    _run(["git", "push", "-u", "origin", "HEAD:main"], clone_a)

    clone_b = tmp_path / "clone_b"
    _run(["git", "clone", str(bare), str(clone_b)], tmp_path)
    _run(["git", "config", "user.email", "test@example.com"], clone_b)
    _run(["git", "config", "user.name", "Test Runner"], clone_b)
    return bare, clone_a, clone_b


def test_single_writer_creates_orphan_branch_and_pushes(tmp_path):
    _bare, clone_a, _clone_b = _init_bare_and_clones(tmp_path)
    git_durable_store.ensure_branch_checked_out(clone_a, BRANCH)

    def apply_fn(repo_dir: Path) -> persistence.AppendResult:
        path = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, 2026)
        row = make_corpus_row(observation=make_observation(kalshi_market_ticker="MKT-A"))
        return persistence.append_observation_rows(path, [row])

    result = git_durable_store.commit_and_push_with_retry(clone_a, BRANCH, apply_fn, "capture: MKT-A")
    assert result.append_result.written == 1
    assert result.attempts == 1


def test_two_writers_racing_converge_to_union_with_no_data_loss(tmp_path):
    """Genuine race: BOTH clones check out the branch (as independent
    orphans, since neither has seen it pushed yet) BEFORE either pushes.
    A pushes first and wins outright; B's push is then rejected as
    non-fast-forward (its local history is an unrelated orphan root), so
    `commit_and_push_with_retry` must hard-reset B onto A's just-pushed
    tip and re-run `apply_fn` there before succeeding on retry."""
    _bare, clone_a, clone_b = _init_bare_and_clones(tmp_path)
    git_durable_store.ensure_branch_checked_out(clone_a, BRANCH)
    git_durable_store.ensure_branch_checked_out(clone_b, BRANCH)  # races A -- branch doesn't exist remotely yet

    def apply_fn_a(repo_dir: Path) -> persistence.AppendResult:
        path = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, 2026)
        row = make_corpus_row(observation=make_observation(kalshi_market_ticker="MKT-A"))
        return persistence.append_observation_rows(path, [row])

    def apply_fn_b(repo_dir: Path) -> persistence.AppendResult:
        path = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, 2026)
        row = make_corpus_row(observation=make_observation(kalshi_market_ticker="MKT-B"))
        return persistence.append_observation_rows(path, [row])

    result_a = git_durable_store.commit_and_push_with_retry(clone_a, BRANCH, apply_fn_a, "capture: MKT-A")
    assert result_a.attempts == 1  # A wins the race outright, no rejection

    result_b = git_durable_store.commit_and_push_with_retry(clone_b, BRANCH, apply_fn_b, "capture: MKT-B")
    assert result_b.append_result.written == 1
    assert result_b.attempts > 1  # B's first push was rejected -- retry loop actually engaged

    # A fresh third clone (a subsequent workflow run) must see BOTH rows.
    clone_c = tmp_path / "clone_c"
    _run(["git", "clone", str(tmp_path / "bare.git"), str(clone_c)], tmp_path)
    _run(["git", "checkout", BRANCH], clone_c)
    final_path = persistence.canonical_path(clone_c / "data" / "research", persistence.OBSERVATIONS_SUBDIR, 2026)
    rows = persistence.read_observation_rows(final_path)
    tickers = {r.observation.kalshi_market_ticker for r in rows}
    assert tickers == {"MKT-A", "MKT-B"}
    assert len(rows) == 2  # no duplication, no data loss


def test_retried_run_with_identical_logical_row_does_not_duplicate(tmp_path):
    # Simulates a workflow retry: the SAME logical checkpoint is captured
    # twice (different snapshot_id, same observation_key) via two
    # separate push_with_retry calls from independent clones.
    bare, clone_a, clone_b = _init_bare_and_clones(tmp_path)
    git_durable_store.ensure_branch_checked_out(clone_a, BRANCH)

    shared_observation = make_observation(kalshi_market_ticker="MKT-RETRY")

    def apply_fn(repo_dir: Path) -> persistence.AppendResult:
        path = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, 2026)
        row = make_corpus_row(observation=shared_observation)
        return persistence.append_observation_rows(path, [row])

    git_durable_store.commit_and_push_with_retry(clone_a, BRANCH, apply_fn, "capture: retry-1")

    git_durable_store.ensure_branch_checked_out(clone_b, BRANCH)
    result_retry = git_durable_store.commit_and_push_with_retry(clone_b, BRANCH, apply_fn, "capture: retry-2")
    assert result_retry.append_result.written == 0
    assert result_retry.append_result.skipped_duplicate == 1

    final_path = persistence.canonical_path(clone_b / "data" / "research", persistence.OBSERVATIONS_SUBDIR, 2026)
    rows = persistence.read_observation_rows(final_path)
    assert len(rows) == 1


def test_no_op_apply_produces_no_new_commit(tmp_path):
    _bare, clone_a, _clone_b = _init_bare_and_clones(tmp_path)
    git_durable_store.ensure_branch_checked_out(clone_a, BRANCH)

    def apply_fn(repo_dir: Path) -> persistence.AppendResult:
        return persistence.AppendResult(written=0, skipped_duplicate=0, keys_written=())

    result = git_durable_store.commit_and_push_with_retry(clone_a, BRANCH, apply_fn, "no-op")
    assert result.attempts == 1
    assert result.append_result.written == 0
