"""The cutover runbook must never tell an operator to stage the migration
with a plain `git add`.

*** THE LIVE NEAR-MISS THIS GUARDS ***
`data/research/` is matched by this repo's `.gitignore`, and the
`research-data` branch carries that `.gitignore` too. So during the
migration commit the two halves of the change are treated differently:

  * the legacy `{season}.jsonl` monoliths are already TRACKED, so
    `git add -A` stages their DELETION;
  * every `{date}.partNNN.jsonl` the migration just wrote is untracked
    and IGNORED, so `git add -A` silently skips it.

On 2026-09-14 the production cutover ran the runbook's `git add -A
data/research` and staged **7 deletions, 0 additions -- 204,271 rows
removed and nothing put back**. Git reported nothing: skipping an ignored
path is `git add`'s documented behaviour, not an error. Only an
insertions-vs-deletions check caught it before the commit.

So the correctness of that one flag cannot rest on prose alone. These
tests read the runbook and fail if the unsafe form ever comes back.

Deliberately narrow: this asserts the DOCUMENT, not the storage layer.
Nothing here touches sharding, migration semantics, or any production
code path.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNBOOK = REPO / "docs" / "CUTOVER_SHARDING.md"


def _bash_block_lines() -> list[str]:
    """Only lines a reader would actually RUN -- inside ```bash fences.

    The unsafe form is quoted on purpose in the warning prose ("this is
    what went wrong"), and that quotation must stay legal; it is the
    explanation. Scanning executable blocks only is what separates the
    instruction from the cautionary tale."""
    lines, inside = [], False
    for line in RUNBOOK.read_text(encoding="utf-8").splitlines():
        if line.startswith("```bash"):
            inside = True
        elif line.startswith("```"):
            inside = False
        elif inside:
            lines.append(line)
    return lines


def test_the_runbook_exists_and_has_runnable_blocks():
    """Guard against this whole module passing vacuously."""
    assert RUNBOOK.is_file()
    assert _bash_block_lines(), "no ```bash blocks found -- the scanner is stale"


def test_every_git_add_in_the_runbook_forces_ignored_paths():
    """THE regression guard. A `git add` without -f under data/research
    stages the monolith deletions and none of the shards."""
    offenders = [
        line.strip()
        for line in _bash_block_lines()
        if re.search(r"\bgit\s+add\b", line) and not re.search(r"\bgit\s+add\b[^|&;]*\s-[A-Za-z]*f", line)
    ]
    assert not offenders, (
        "runbook tells the operator to stage with a non-forced `git add`: "
        f"{offenders} -- data/research is gitignored, so this stages the monolith "
        "DELETIONS and silently skips every shard. Use `git add -f`."
    )


def test_at_least_one_forced_add_is_actually_prescribed():
    """The inverse of the check above: -f must be present, not merely
    'no unforced add because there is no add at all'."""
    assert any(
        re.search(r"\bgit\s+add\b.*\s-f", line) or re.search(r"\bgit\s+add\s+-f", line)
        for line in _bash_block_lines()
    ), "the runbook no longer prescribes a forced add for the migration commit"


def test_the_runbook_carries_the_pre_commit_stop_condition():
    """`git add` exits 0 exactly when it skips the shards, so a truth
    gate between staging and committing is the whole defence."""
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "STOP CONDITION" in text
    assert "DO NOT COMMIT" in text
    assert "MONOLITH DELETIONS WITHOUT THE EXPECTED" in text


def test_the_runbook_requires_an_insertions_equals_deletions_check():
    """The one check that would have caught the live near-miss on its
    own: a relocation adds as many lines as it removes."""
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "--numstat" in text
    assert "insertions" in text and "deletions" in text


def test_the_gitignore_rule_that_makes_the_flag_necessary_still_exists():
    """Ties the doc to the repo fact behind it. If `data/research/` ever
    stops being ignored, this fails and whoever changed it learns that a
    runbook depends on the rationale."""
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^data/research/\s*$", ignore, re.MULTILINE), (
        "data/research/ is no longer gitignored -- revisit the `git add -f` "
        "rationale in docs/CUTOVER_SHARDING.md"
    )
