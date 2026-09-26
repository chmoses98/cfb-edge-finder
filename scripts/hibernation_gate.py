"""Run-time hibernation gate for research workflows.

Called as the first job of a hibernated workflow. Decides, from the GitHub
event context, whether this run may do any work, and writes
`proceed=true|false` to $GITHUB_OUTPUT for the real job's `if:`.

A REFUSAL EXITS 0, DELIBERATELY. A refused unattended dispatch is the
hibernation working, not an outage: failing it would turn an external
scheduler that was never switched off back into a failure email every five
minutes -- the exact storm this gate exists to end. It is not silent
either: the refusal is a `::warning::` annotation and a job-summary line on
every such run, so a still-running caller stays visible in the Actions tab.

Stdlib only, and nothing is installed first: the gate must be cheap enough
that a caller dispatching every five minutes costs seconds, not a
`pip install` and a CFBD request.

Inputs come from the environment (set by the workflow):
  GATE_WORKFLOW        workflow file name, e.g. research-capture.yml
  GATE_EVENT_NAME      github.event_name
  GATE_ACTOR           github.actor
  GATE_TRIGGER_SOURCE  inputs.trigger_source (optional)
  GATE_DRY_RUN         inputs.dry_run (optional, "true"/"false")
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.research.hibernation import decide  # noqa: E402


def main() -> int:
    decision = decide(
        workflow_file=os.environ.get("GATE_WORKFLOW", ""),
        event_name=os.environ.get("GATE_EVENT_NAME") or None,
        actor=os.environ.get("GATE_ACTOR") or None,
        trigger_source=os.environ.get("GATE_TRIGGER_SOURCE") or None,
        dry_run=os.environ.get("GATE_DRY_RUN", "").strip().lower() == "true",
    )
    verdict = "PROCEED" if decision.proceed else "REFUSED"
    print(f"HIBERNATION-GATE {verdict}: {decision.reason}")
    if not decision.proceed:
        print(f"::warning title=Hibernated workflow refused::{decision.reason}")

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"proceed={'true' if decision.proceed else 'false'}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"**Hibernation gate: {verdict}** -- {decision.reason}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
