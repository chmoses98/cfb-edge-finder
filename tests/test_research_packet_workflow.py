"""The Script Engine workflow's research-packet steps: fail soft for the ledger, fail VISIBLE for the operator."""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/script-engine.yml"


def _steps():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["build"]["steps"]


def _index(name_prefix):
    names = [s.get("name", "") for s in _steps()]
    return next(i for i, n in enumerate(names) if n.startswith(name_prefix))


def test_packets_are_built_after_the_football_artifacts_and_verified_strictly():
    steps = _steps()
    build = steps[_index("Build the RUN CFB research packets")]
    assert build["id"] == "packets" and build["continue-on-error"] is True
    assert "build_research_packets.py" in build["run"]
    assert "verify_research_packets.py --strict --require-horizon-hours" in build["run"]
    assert _index("Build (football artifact frozen first") < _index("Build the RUN CFB research packets")


def test_a_packet_failure_is_never_silent_and_never_committed():
    steps = _steps()
    discard = steps[_index("Discard unverified research packets")]
    assert discard["if"] == "steps.packets.outcome == 'failure'" and "::error" in discard["run"]
    assert (
        _index("Build the RUN CFB research packets")
        < _index("Discard unverified")
        < _index("Commit football data and live payloads")
    )
    final = steps[-1]
    assert final["name"].startswith("Fail the run if the research packets failed")
    assert "steps.packets.outcome == 'failure'" in final["if"] and "exit 1" in final["run"]
    assert _index("Commit the prospective ledger") < len(steps) - 1  # red only AFTER the ledger and data commits


def test_the_prospective_ledger_protection_is_unchanged():
    steps = _steps()
    ledger = steps[_index("Commit the prospective ledger")]
    assert ledger["if"] == "github.ref_name == 'main'"
    text = WORKFLOW.read_text()
    assert "Ledger NOT pushed from" in text and "script-ledger-unpushed" in text
    commit = steps[_index("Commit football data and live payloads")]
    assert "git add data/football data/scripting/live data/research_packets" in commit["run"]
    assert "script-ledger" not in commit["run"]
