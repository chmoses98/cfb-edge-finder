# Storage Strategy

## What stays in git

Source code, tests, configuration, docs, schemas, and compact canonical
artifacts (a season's `GameRecord`s, model-version manifests, a wager
ledger once one exists). This is everything under `src/`, `tests/`,
`docs/`, `config/`, `scripts/`, and small, deliberately-curated files under
`data/`.

## Per-blob size ceiling (learned the hard way, 2026-09-14)

GitHub refuses any blob over **100 MiB** (104,857,600 bytes) at
pre-receive with `GH001`, and warns above 50 MB. This is not advisory
for a git-backed durable store: the refusal rejects the WHOLE push, so
one oversize file stops every other write on the branch with it.

That happened. `data/research/observations/2026.jsonl` -- one file per
family-season, appended to every ten minutes -- reached 99.72 MiB, and
the next append crossed the limit. Every durable push failed, so every
prospective capture failed, including CLOSING lines that can never be
recovered after kickoff.

The rule that follows: **anything append-only in git must be bounded by
construction, not by hope.** The research corpus is now sharded by the
UTC date of each row's own timestamp, with a rollover at 45 MB
(`research/shards.py`), so no single blob can grow without limit. The
durable store refuses to push a changed blob above 90 MB rather than
letting GitHub reject the branch, and CI runs the same check against the
live corpus. Git LFS was deliberately NOT used: it moves the problem
into a quota-bearing pointer store and changes how every consumer reads
the data, where the actual defect was an unbounded file.

An artifact that is genuinely a single document (the football-state
snapshot, a v2 parquet) cannot be sharded this way. Those are the ones
the size guard exists to catch before they become the next incident.

## What does not stay in git (V1 recommendation)

Raw high-frequency Kalshi price captures, large historical/backtest
datasets, play-by-play dumps, feature tables, and repeated snapshots.

## Why this differs from edge-finder-api's default

The MLB audit (`docs/MLB_ARCHITECTURE_AUDIT.md` section 16) found that
edge-finder-api commits **everything** to git -- no external storage
exists there at all, and `data/` has grown to ~962MB, dominated by Kalshi
registry snapshots (389MB) and MLB-specific Statcast pitch-level data
(33MB, not applicable to CFB). That repo manages this actively with
filename-date-based retention pruning and a safe-commit helper that never
trusts `git rebase --autostash`'s exit code (built after a real incident
left conflict markers committed to main).

CFB's per-play data volume is expected to be smaller than MLB's
pitch-level Statcast data (there's no per-pitch analog in football), but
the mission explicitly flags that a normal CFB week can still mean
thousands to tens of thousands of Kalshi contract observations with
repeated price snapshots across a large game slate -- git-as-database at
that scale, repeated indefinitely across seasons, is not free to clone,
diff, or CI against, and growing it from day one without a plan is worse
than choosing deliberately now.

## V1 recommendation

**Low-complexity, not zero-complexity:** use git for everything that's
naturally small and valuable to diff/review (schemas, canonical game
records, model version manifests, a wager ledger), and a single
low-overhead object store -- e.g. an S3-compatible bucket (S3, R2,
Backblaze B2) with lifecycle rules -- for anything raw or repeated
(Kalshi price sweeps, feature tables, play-by-play caches). A manifest
file committed to git (path/hash/timestamp, not the bytes) lets a
git-tracked record point at an archived blob without embedding it,
preserving reproducibility without repository bloat.

**Update (Milestone E, preseason readiness):** durable persistence is now
implemented -- not the S3-compatible object store speculated above, but
the "everything small enough stays in git" half of this document's own
recommendation, applied directly: compact, normalized `KalshiResearchObservation`
rows on a dedicated `research-data` branch (never `main`), append-only,
deterministically deduped. The season-scale estimate in
`docs/MILESTONE_E.md` shows even a deliberately worst-case capture volume
stays comfortably within what git handles as line-oriented text, so the
object-store client speculated above was not needed after all -- see that
document's "Durable persistence" section for the full comparison and
reasoning. `data/raw/` and `data/archive/` remain reserved and gitignored
for genuinely raw/high-churn data if that ever changes.

`data/.gitignore` rules already reserve `data/raw/` and `data/archive/` as
paths that must never be added to git even accidentally, regardless of
which specific object store is chosen later.

## Retention discipline to carry forward (from the MLB audit)

Whatever the eventual store, two disciplines from edge-finder-api are worth
reproducing exactly because they were each hard-won from real incidents:

1. **Prune by filename-embedded date, not filesystem mtime.** mtime reads
   as "now" after any fresh checkout, so mtime-based retention silently
   never prunes anything.
2. **Never trust an automated git operation's exit code for safety-
   critical commits.** Check `git diff --name-only --diff-filter=U` (or
   the equivalent for whatever VCS operation is in play) directly before
   ever running `add`/`commit`/`push` in an automated job.
