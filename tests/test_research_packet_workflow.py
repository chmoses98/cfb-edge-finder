"""The research-packet workflow: isolated from the Script Engine, read-only to the ledger, fail-visible.

Read with a narrow text scan, not a YAML parser: this repository's CI installs no PyYAML (see
tests/test_research_workflow_concurrency.py for the same choice)."""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"
PACKETS = (WORKFLOWS / "cfb-research-packets.yml").read_text()
EXECUTABLE = "\n".join(line for line in PACKETS.splitlines() if not line.lstrip().startswith("#"))
ENGINE = (WORKFLOWS / "script-engine.yml").read_text()


def _steps() -> list[tuple[str, str]]:
    return [(chunk.split("\n", 1)[0], chunk) for chunk in re.split(r"(?m)^      - name: ", PACKETS)[1:]]


def _body(prefix: str) -> str:
    return next(body for name, body in _steps() if name.startswith(prefix))


def _index(prefix: str) -> int:
    return next(i for i, (name, _) in enumerate(_steps()) if name.startswith(prefix))


def test_packets_do_not_depend_on_or_ride_inside_the_script_engine():
    """A real run showed the Script Engine's build step hitting its 45-minute timeout on main; the packets must neither
    wait behind it nor be lost with it. The engine workflow is left exactly as it was."""
    assert "research_packets" not in ENGINE and "build_research_packets" not in ENGINE
    assert "script_engine.py build" not in EXECUTABLE and "script-ledger" not in EXECUTABLE


def test_build_then_strict_verification_then_commit_and_nothing_commits_on_failure():
    assert _index("Build the research packets") < _index("Verify every expected packet") < _index("Commit the packets")
    verify = _body("Verify every expected packet")
    assert "verify_research_packets.py --strict --require-horizon-hours" in verify
    for name in ("Build the research packets", "Verify every expected packet", "Commit the packets"):
        step = _body(name)
        assert "continue-on-error" not in step and "if: always()" not in step  # a failure fails the run, visibly


def test_a_failed_game_log_refresh_is_a_visible_warning_not_silence():
    collect = _body("Refresh the game log into a scratch copy")
    assert "id: collect" in collect and "continue-on-error: true" in collect
    assert "data/football" in collect and '"$RUNNER_TEMP/football/$SEASON"' in collect  # scratch copy, never the repo
    warn = _body("Warn if the game log could not be refreshed")
    assert "if: steps.collect.outcome == 'failure'" in warn and "::warning" in warn


def test_it_stages_only_its_own_path_and_never_touches_the_ledger_or_orders():
    commit = _body("Commit the packets")
    assert "git add -A data/research_packets" in commit and "git add -A data/football" not in commit
    assert 'HEAD:"${GITHUB_REF_NAME}"' in commit or "HEAD:${GITHUB_REF_NAME}" in commit
    assert "git pull --rebase" in commit
    for forbidden in ("script-ledger", "research-data", "KALSHI", "secrets."):
        assert forbidden not in EXECUTABLE


def test_the_workflow_file_is_valid_yaml_when_a_parser_is_available():
    """A real push of this workflow failed as 'a workflow file issue' because an unquoted step name held ': '. CI installs no
    PyYAML, so this guard runs wherever one exists (and a text check below runs everywhere)."""
    for line in PACKETS.splitlines():
        stripped = line.strip()
        if stripped.startswith("- name: ") and not stripped[8:].startswith(('"', "'")):
            assert ": " not in stripped[8:], f"unquoted step name with ': ' is invalid YAML: {stripped}"
    try:
        import yaml
    except ImportError:
        return
    assert yaml.safe_load(PACKETS)["jobs"]["packets"]["steps"]
