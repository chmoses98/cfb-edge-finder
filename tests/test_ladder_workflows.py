"""Pin the isolation properties of the two archive workflows (same narrow-scan style as test_research_workflow_concurrency)."""
import re
from pathlib import Path

import pytest

W = Path(__file__).resolve().parents[1] / ".github" / "workflows"
CASES = {"cfb-1q-ladder-capture.yml": ("cfb-1q-ladder-archive", "cfb-1q-ladder-archive-write"), "cfb-espn-raw-archive.yml": ("cfb-espn-raw-archive", "cfb-espn-raw-archive-write")}


@pytest.mark.parametrize("name", CASES)
def test_pushes_only_its_own_orphan_branch_with_its_own_group(name):
    text = re.sub(r"^\s*#.*$", "", (W / name).read_text(), flags=re.M)
    branch, group = CASES[name]
    assert set(re.findall(r"HEAD:([A-Za-z0-9._/-]+)", text)) == {branch}
    assert f"group: {group}" in text and "cancel-in-progress: false" in text
    assert "research-data" not in text


@pytest.mark.parametrize("name", CASES)
def test_no_secrets_no_trading_no_main_writes(name):
    text = re.sub(r"^\s*#.*$", "", (W / name).read_text(), flags=re.M)
    assert "secrets." not in text.replace("${{ github.token }}", "")
    for bad in ("portfolio", "/orders", "KALSHI_API", "CFBD_API_KEY", "HEAD:main", "HEAD:${GITHUB_REF_NAME}"):
        assert bad not in text, bad
    assert "contents: write" in text and text.count("contents: write") == 1          # only the push job can write


def test_pull_request_smoke_cannot_push_and_uses_a_temp_directory():
    text = (W / "cfb-1q-ladder-capture.yml").read_text()
    smoke = text[text.index("  smoke:"):text.index("  capture:")]
    assert "git push" not in smoke and "RUNNER_TEMP" in smoke and "contents: write" not in smoke


def test_capture_job_checks_append_only_and_verifies_before_publish():
    text = (W / "cfb-1q-ladder-capture.yml").read_text()
    assert text.index("ladder_store.verify") < text.index("git push origin HEAD:cfb-1q-ladder-archive")
    assert "non-append change" in text
