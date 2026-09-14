# Cutover runbook: season monoliths -> UTC-date shards

Status: **not executed.** Steps A and B are prerequisites; C-I are held
pending approval. Nothing in this document has been run against
production.

---

## 1. The race this runbook exists to prevent

After the sharding change merges, readers accept a legacy monolith **and**
shards, but every new write goes to a shard. That creates a window:

```
merge  ->  a scheduled capture runs  ->  it writes the first shard
       ->  monolith and shards now coexist
       ->  migration refuses (blind migration into a populated shard set
           is how rows get duplicated)
       ->  cutover stuck, corpus split across two layouts,
           GH001 still blocking every push
```

**The shared concurrency group does not prevent this.**
`concurrency: group: research-data-write` *serializes* writers. The
capture that runs in the gap holds the group entirely legitimately and
runs alone exactly as designed — and lands its shard anyway.
Serialization is not exclusion.

Two independent mechanisms close it, because each alone has a gap:

| | Mechanism | Stops | Gap it leaves |
|---|---|---|---|
| **Lock 1** | Workflows disabled in the Actions UI / API | Every scheduled fire and every `workflow_dispatch`, including conductor-dispatched ones, **on any code version** | Does **not** cancel a run already in flight |
| **Lock 2** | Maintenance flag on `research-data` (`research/maintenance.py`) | Every writer running the merged code, at `ensure_branch_checked_out` and again immediately before every push | Cannot stop a run that started on **pre-merge** code |

Lock 1 covers what Lock 2 cannot (old code); Lock 2 covers what Lock 1
cannot (anything re-enabled, dispatched, or run manually after the
merge). Step B — verifying the branch tip has not moved — is what closes
the in-flight gap that neither lock covers.

**And if a write lands anyway**, the cutover is no longer stuck:
`scripts/migrate_research_shards.py --allow-existing-shards` appends only
the monolith rows whose dedup key is not already in the shards, refuses
on any same-key/different-bytes conflict, and proves that every
pre-existing shard line survived byte-for-byte. Prevented *and*
recoverable.

---

## 2. Writer census

Every automation capable of moving `refs/heads/research-data`, audited
against the workflow files at this PR's head.

### Writers — all must be stopped

| # | Workflow | Trigger | How it writes | Stopped by |
|---|---|---|---|---|
| 1 | `research-capture.yml` — Research Capture | `schedule: */10 * * * *`, `workflow_dispatch` | `research_scan_and_capture.py` -> `GitDurableStore` | Locks 1 + 2 |
| 2 | `research-settlement.yml` — Research Settlement | `schedule: 0 */6 * * *`, `workflow_dispatch` | `research_settle.py`, `research_attribute.py` -> `GitDurableStore` | Locks 1 + 2 |
| 3 | `research-weekly-report.yml` — Research Weekly Report | `workflow_dispatch` only | `research_weekly_report.py`, `research_season_report.py` -> `GitDurableStore` | Locks 1 + 2 |
| 4 | `preseason-research-fetch.yml` — Preseason Research Cache Fetch | `workflow_dispatch` only | **raw `git push origin HEAD:research-data`** — does *not* go through `GitDurableStore` | Lock 1, plus the explicit `--status` gate added to the push step |
| 5 | `research-collection-conductor.yml` — Collection Conductor | `schedule: 17 * * * *`, `workflow_dispatch` | Writes nothing itself; **dispatches** Research Capture runs. Sits in its own `research-collection-conductor` group, so it does **not** queue behind `research-data-write` | Lock 1 (disable it too — otherwise it keeps dispatching #1) |

#4 is the reason Lock 2 alone is insufficient and the workflow-disable
step is mandatory: a raw `git push` consults no library. It now consults
the flag explicitly, but only from this PR's head onwards.

### Non-writers — confirmed, leave running

| Workflow | Why it cannot write `research-data` |
|---|---|
| `capture-resilience-probe.yml` | `--no-push`; writes on the runner, pushes nothing; no `research-data-write` group |
| `settlement-source-probe.yml` | `--no-push`; same |
| `live-info-sidecar.yml` | Pushes to `research-sidecar`, a **different** branch; group `research-sidecar-write` |
| `ci.yml` | Fetches `research-data` read-only for the blob-size guard |
| `backtest-cfb-baseline-live.yml`, `validate-cfbd-live.yml`, `validate-kalshi-cfb-live.yml` | No `research-data` reference at all |

Verify the census is still accurate immediately before step A — a
workflow added since this audit would not be in this table:

```bash
grep -rln "research-data" .github/workflows/
grep -rn "git push" .github/workflows/
```

---

## 3. The runbook

Steps **A** and **B** are prerequisites. **C-I are not to be executed
without explicit approval.**

### A. Freeze every writer

```bash
# Lock 1 — stops schedules AND workflow_dispatch, on any code version.
for wf in "Research Capture (scheduled scanner)" \
          "Research Settlement" \
          "Research Weekly Report" \
          "Preseason Research Cache Fetch (manual)" \
          "Research Collection Conductor (trigger layer)"; do
  gh workflow disable "$wf" --repo chmoses98/cfb-edge-finder
done
gh workflow list --repo chmoses98/cfb-edge-finder --all   # confirm: all five disabled
```

Disabling does **not** cancel runs already in flight. Drain them:

```bash
gh run list --repo chmoses98/cfb-edge-finder --status in_progress
gh run list --repo chmoses98/cfb-edge-finder --status queued
# wait for both to empty (a capture cycle is ~10 minutes), or cancel:
#   gh run cancel <id> --repo chmoses98/cfb-edge-finder
```

Recommended additional belt, if repository-admin access is available: a
branch ruleset on `research-data` restricting pushes to the operator
performing the cutover. That is the only lock enforced by the **server**,
so it stops old code, raw pushes, and anything not yet audited.

### B. Verify the tip has not moved

```bash
git ls-remote https://github.com/chmoses98/cfb-edge-finder research-data
# MUST be b3f9bbc8d74c07f7bfe06798bd0d3ba5d695f986
```

If it has moved, a writer landed after the audit: **stop**, re-run the
census, and restart at A.

Then open Lock 2, so anything that starts after the merge refuses:

```bash
python scripts/research_maintenance_window.py --open \
    --reason "season-monolith -> UTC-date shard cutover" \
    --opened-by "<operator>" \
    --expected-reopen "within 2h"
python scripts/research_maintenance_window.py --status   # OPEN
```

Re-verify the tip one final time — opening the window is itself a commit,
so the expected value is now that commit, with `b3f9bbc` as its parent
and **only** `data/research/MAINTENANCE_WINDOW.json` in its diff:

```bash
git fetch origin research-data
git show --stat origin/research-data          # one file, one line added
git rev-parse origin/research-data^           # b3f9bbc8d74c07f7bfe06798bd0d3ba5d695f986
```

### C. Merge PR #40

Only after A and B both pass.

### D. Migrate

Two checkouts, because the code and the data live on different branches:
run the MERGED code from `main`, pointed at a **separate, fresh** working
copy of `research-data`.

```bash
git clone https://github.com/chmoses98/cfb-edge-finder /tmp/code
git -C /tmp/code checkout main && git -C /tmp/code pull

git clone --branch research-data \
    https://github.com/chmoses98/cfb-edge-finder /tmp/data
git -C /tmp/data rev-parse HEAD     # record this; it is the rollback point

python /tmp/code/scripts/migrate_research_shards.py \
    --data-repo-dir /tmp/data --season 2026 \
    --dry-run --report /tmp/migration-dry-run.json
```

Review the dry-run report, then apply:

```bash
python /tmp/code/scripts/migrate_research_shards.py \
    --data-repo-dir /tmp/data --season 2026 \
    --report /tmp/migration.json
```

If — and only if — it refuses because shards already exist beside a
monolith (a writer landed despite A and B), re-run with
`--allow-existing-shards`. That is the documented recovery, and it
strengthens the proof rather than relaxing it. If it then reports a
same-key/different-bytes conflict, **stop and escalate**: two copies of
one observation disagree, and neither may be discarded automatically.

### E. Verify equivalence

From `/tmp/migration.json`, per family and in total:

- `rows_out == rows_in` (plus `preexisting_shard_rows` if the recovery
  path was used)
- `duplicate_keys == 0`
- `missing_keys == 0`
- `order_violations == 0`
- `byte_identical == true`
- `preexisting_rows_lost == 0`
- `largest_shard_bytes` comfortably under 45,000,000

Expected baseline, from the rehearsal against the corpus at `b3f9bbc`:
**204,271 rows across 7 families, 112 shards, largest blob 104.56 MB ->
27.71 MB, 0 duplicates, 0 missing, 0 order violations.**

### F. Verify the monoliths are gone

```bash
cd /tmp/data
git ls-tree -r --name-only HEAD -- data/research | grep -E '/2026\.jsonl$' && echo "STILL PRESENT" || echo "clean"
python /tmp/code/scripts/check_research_blob_sizes.py --data-repo-dir /tmp/data --strict
```

`--strict` **without** `--allow-legacy-monolith`: after the migration
there is no legacy monolith left, so any oversize blob is a genuine
regression.

Then commit and push the migrated corpus:

```bash
cd /tmp/data
git add -A data/research
git commit -m "research: migrate season monoliths to UTC-date shards"
git push origin HEAD:research-data      # expect NO GH001, NO 50 MB warning
```

The maintenance window does **not** block this, and must not: it gates
`GitDurableStore`, and this is a raw `git push` by the operator
performing the cutover. That is the one write the window exists to
protect. Never `--force`.

### G. Close the maintenance window

```bash
python scripts/research_maintenance_window.py --close --reason "migration applied"
python scripts/research_maintenance_window.py --status   # CLOSED
```

Workflows stay disabled — Lock 1 alone is enough for the supervised
single write in step H, which is why no override mechanism exists.

### H. One controlled durable-write proof

One real capture against the migrated corpus:

```bash
gh workflow run "Research Capture (scheduled scanner)" \
    --repo chmoses98/cfb-edge-finder -f trigger_source=cutover-proof
```

(Enable only this workflow for the run, then disable it again.) Confirm:

- the push succeeds — **no GH001, no 50 MB warning**
- the new rows land in a **shard**, not a monolith
- no duplicate keys appear
- `git ls-remote` shows the tip advanced by exactly that commit

### I. Re-enable everything

```bash
for wf in "Research Capture (scheduled scanner)" \
          "Research Settlement" \
          "Research Weekly Report" \
          "Preseason Research Cache Fetch (manual)" \
          "Research Collection Conductor (trigger layer)"; do
  gh workflow enable "$wf" --repo chmoses98/cfb-edge-finder
done
gh workflow list --repo chmoses98/cfb-edge-finder --all   # confirm: all five enabled
```

Watch the next two scheduled capture cycles and the next settlement run.

---

## 4. Rollback

Before step D, rollback is: close the window, re-enable the workflows.
Nothing was changed.

After step D, `research-data` is an append-only branch and the
pre-migration commit `b3f9bbc8d74c07f7bfe06798bd0d3ba5d695f986` still
exists, so the corpus is recoverable in full at any point. The migration
is a byte-for-byte relocation: no row is rewritten, so restoring the old
layout is a checkout of that commit, not a reconstruction.

Do not force-push `research-data` under any circumstances. It is the only
copy of the prospective observations.
