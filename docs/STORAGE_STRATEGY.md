# Storage Strategy

## What stays in git

Source code, tests, configuration, docs, schemas, and compact canonical
artifacts (a season's `GameRecord`s, model-version manifests, a wager
ledger once one exists). This is everything under `src/`, `tests/`,
`docs/`, `config/`, `scripts/`, and small, deliberately-curated files under
`data/`.

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

This is explicitly **not implemented in this foundation phase** -- no
object-store client, no bucket, no credentials are wired up yet, because
there is no real capture volume to store until Milestone E (Kalshi
universe capture) exists. Building the storage client before there's
anything to store would be premature infrastructure. What this phase does
establish is the git/non-git boundary and the reasoning, so Milestone E
doesn't have to make this decision under pressure once volume actually
shows up.

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
