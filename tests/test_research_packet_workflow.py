"""The Script Engine workflow's research-packet steps: fail soft for the ledger, fail VISIBLE for the operator.

Read with a narrow text scan, not a YAML parser: this repository's CI installs no PyYAML (see
tests/test_research_workflow_concurrency.py for the same choice)."""

from __future__ import annotations

import re
from pathlib import Path

TEXT = (Path(__file__).resolve().parents[1] / ".github/workflows/script-engine.yml").read_text()


def _steps() -> list[tuple[str, str]]:
    """[(step name, step body)] in file order, for the `build` job."""
    chunks = re.split(r"(?m)^      - name: ", TEXT)[1:]
    return [(c.split("\n", 1)[0], c) for c in chunks]


def _index(prefix: str) -> int:
    return next(i for i, (name, _) in enumerate(_steps()) if name.startswith(prefix))


def _body(prefix: str) -> str:
    return _steps()[_index(prefix)][1]


def test_packets_are_built_after_the_football_artifacts_and_verified_strictly():
    body = _body("Build the RUN CFB research packets")
    assert "id: packets" in body and "continue-on-error: true" in body
    assert "build_research_packets.py" in body
    assert "verify_research_packets.py --strict --require-horizon-hours" in body
    assert _index("Build (football artifact frozen first") < _index("Build the RUN CFB research packets")


def test_a_packet_failure_is_never_silent_and_never_committed():
    discard = _body("Discard unverified research packets")
    assert "if: steps.packets.outcome == 'failure'" in discard and "::error" in discard
    built = _index("Build the RUN CFB research packets")
    assert built < _index("Discard unverified") < _index("Commit football data and live payloads")
    steps = _steps()
    name, final = steps[-1]
    assert name.startswith("Fail the run if the research packets failed")
    assert "steps.packets.outcome == 'failure'" in final and "exit 1" in final
    assert _index("Commit the prospective ledger") < len(steps) - 1  # red only AFTER the ledger and data commits


def test_the_prospective_ledger_protection_is_unchanged():
    ledger = _body("Commit the prospective ledger")
    assert "if: github.ref_name == 'main'" in ledger
    assert "Ledger NOT pushed from" in TEXT and "script-ledger-unpushed" in TEXT
    commit = _body("Commit football data and live payloads")
    assert "git add data/football data/scripting/live data/research_packets" in commit
    assert "script-ledger" not in commit
