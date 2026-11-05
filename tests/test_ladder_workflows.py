"""Pin the isolation properties of the two archive workflows (same narrow-scan style as test_research_workflow_concurrency)."""

import os
import re
import subprocess
from pathlib import Path

import pytest

W = Path(__file__).resolve().parents[1] / ".github" / "workflows"
CASES = {
    "cfb-1q-ladder-capture.yml": ("cfb-1q-ladder-archive", "cfb-1q-ladder-archive-write"),
    "cfb-espn-raw-archive.yml": ("cfb-espn-raw-archive", "cfb-espn-raw-archive-write"),
}


@pytest.mark.parametrize("name", CASES)
def test_pushes_only_its_own_orphan_branch_with_its_own_group(name):
    text = re.sub(r"^\s*#.*$", "", (W / name).read_text(), flags=re.M)
    branch, group = CASES[name]
    assert (
        "scripts/archive_publish.sh /tmp/" in text and f" {branch} " in text
    )  # the only push path is the verified publisher script
    assert "git push" not in text
    assert f"group: {group}" in text and "cancel-in-progress: false" in text
    assert "research-data" not in text


@pytest.mark.parametrize("name", CASES)
def test_no_secrets_no_trading_no_main_writes(name):
    text = re.sub(r"^\s*#.*$", "", (W / name).read_text(), flags=re.M)
    assert "secrets." not in text.replace("${{ github.token }}", "")
    for bad in ("portfolio", "/orders", "KALSHI_API", "CFBD_API_KEY", "HEAD:main", "HEAD:${GITHUB_REF_NAME}"):
        assert bad not in text, bad
    assert "contents: write" in text and text.count("contents: write") == 1  # only the push job can write


def test_pull_request_smoke_cannot_push_and_uses_a_temp_directory():
    text = (W / "cfb-1q-ladder-capture.yml").read_text()
    smoke = text[text.index("  smoke:") : text.index("  capture:")]
    assert (
        "git push" not in smoke
        and "archive_publish" not in smoke
        and "RUNNER_TEMP" in smoke
        and "contents: write" not in smoke
    )


@pytest.mark.parametrize("name", CASES)
def test_write_jobs_run_only_from_main_and_never_interpolate_inputs_into_shell(name):
    text = re.sub(r"^\s*#.*$", "", (W / name).read_text(), flags=re.M)
    assert "github.ref == 'refs/heads/main'" in text
    run_blocks = re.findall(r"run: \|?\n?(.*?)(?=\n      - |\Z)", text, flags=re.S)
    for rb in run_blocks:
        assert "${{ github.event.inputs" not in rb and "${{ inputs" not in rb, rb[:120]


def test_capture_job_checks_append_only_and_verifies_before_publish():
    text = (W / "cfb-1q-ladder-capture.yml").read_text()
    assert text.index("ladder_store.verify") < text.index("archive_publish.sh")
    assert "non-append change" in text


def _git(cwd, *a, check=True):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=check)


def _setup_bare(tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    return bare


def _worktree(tmp_path, bare, name, files):
    wd = tmp_path / name
    wd.mkdir()
    _git(wd, "init", "-q")
    _git(wd, "checkout", "-q", "--orphan", "arch")
    _git(wd, "remote", "add", "origin", str(bare))
    for rel, txt in files.items():
        p = wd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt)
    return wd


def _publish(wd, msg="m"):
    env = {
        "GITHUB_TOKEN_FOR_ARCHIVE": "x",
        "GITHUB_REPOSITORY": "o/r",
        "PATH": os.environ["PATH"],
        "HOME": str(wd),
    }
    return subprocess.run(
        [str(W.parent.parent / "scripts" / "archive_publish.sh"), str(wd), "HEAD:arch", msg],
        capture_output=True,
        text=True,
        env=env,
    )


def test_publisher_keeps_both_runs_when_a_concurrent_push_wins_and_state_conflicts(tmp_path):
    bare = _setup_bare(tmp_path)
    a = _worktree(tmp_path, bare, "a", {"state/latest.json": "A\n", "runs/d/a.json": "a\n"})
    b = _worktree(tmp_path, bare, "b", {"state/latest.json": "B\n", "runs/d/b.json": "b\n"})
    assert _publish(a).returncode == 0  # run A wins the race and creates the branch
    r = _publish(b)  # run B's push is rejected (unrelated histories): it must re-apply on top, not drop data
    assert r.returncode == 0, r.stderr
    out = tmp_path / "check"
    subprocess.run(["git", "clone", "-q", "-b", "arch", str(bare), str(out)], check=True)
    assert (out / "runs/d/a.json").read_text() == "a\n" and (out / "runs/d/b.json").read_text() == "b\n"
    assert (out / "state/latest.json").read_text() == "B\n"  # the later run's state wins; no run's rows are lost


def test_publisher_fails_loudly_when_it_cannot_publish(tmp_path):
    wd = _worktree(tmp_path, tmp_path / "does-not-exist.git", "c", {"runs/d/c.json": "c\n"})
    r = _publish(wd)
    assert r.returncode != 0 and ("FAILED to publish" in r.stderr or "fatal" in r.stderr)
