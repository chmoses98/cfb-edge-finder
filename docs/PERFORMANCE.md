# Scanner Performance

Performance hardening of the scheduled research scanner
(`scripts/research_scan_and_capture.py`). Engineering only: no modelling,
recommendation, qualification, staking, execution, or research-semantics
change is part of this work.

## 1. The original bottleneck

The scanner re-derived *"what is already in the corpus?"* from disk **once
per market ticker**, in two separate places inside the per-ticker loop:

| Where | Call | Cost per call |
|---|---|---|
| `_apply_scan`, per **ticker** | `persistence.read_observation_rows(obs_path)` | full read **+ pydantic re-validation of every historical row** |
| `persistence.append_json_rows`, per **row written** | `_load_existing_keys(path, ...)` | full read + JSON decode of every historical row |

Only two small facts were ever extracted from that first full read — the
set of timing labels already captured for *this one ticker*:

```python
existing_rows = persistence.read_observation_rows(obs_path)      # entire corpus, validated
already_captured_for_ticker = {
    r.observation.snapshot_timing.label
    for r in existing_rows
    if r.observation.kalshi_market_ticker == ticker               # ...then filtered to one ticker
}
```

**Call path:** `main()` → `git_durable_store.commit_and_push_with_retry()`
→ `apply_fn` → `_apply_scan()` → *per series* → *per event* → **per
market** → `read_observation_rows()`.

**Asymptotics.** With `T` tickers and `H` history rows the run did
`O(T x H)` work before any pricing decision — and the first read happened
*before* `timing.resolve_due_labels()`, so it was paid for **every**
ticker on **every** run, including runs that captured nothing at all.
Corpus-size dependency was linear and ticker-count dependency was linear,
so the product grew as the season progressed. Target shape:
`O(H + T)`.

This is why runtime tracked corpus growth rather than slate size: the live
scanner went from ~3 minutes early on to ~6.5 minutes as the corpus grew,
without the market universe changing much.

## 2. Baseline profile

Measured against the real corpus (`origin/research-data`,
`data/research/observations/2026.jsonl`) and the last pre-change scheduled
run on `main@6015276`.

**Live run 18** (run id `33075615623`, 2026-08-27 13:12 UTC) — the "Scan
and capture" step alone, excluding checkout/pip:

| Metric | Value |
|---|---|
| Scan step wall clock | **331 s** (5 m 31 s) |
| Corpus rows at run time | 1,724 |
| Markets/tickers scanned | 4,578 |
| Games scanned | 3,550 |
| Observations written | 0 (everything already captured) |
| **History file reads** | **4,578** (one per ticker) |

Other successful `main` runs, same shape: 8 m 56 s, 9 m 09 s, 10 m 28 s
(total workflow duration).

**Cost of a single history read**, measured directly on the 1,724-row live
corpus:

| Operation | Time |
|---|---|
| `read_observation_rows` (full pydantic validation) | 99.5 ms |
| `_read_all` (JSON decode only) | 37.1 ms |
| `read_observation_keys` (JSON + key extraction) | 21.9 ms |

At 4,578 tickers that is ~455 s of pure history reading on this hardware —
i.e. **history re-reading alone accounted for the large majority of the
331-second live scan step**, on a run that produced zero new research
output. GitHub's runners are roughly 2x faster than the measuring
container, which is consistent with the observed 331 s.

**Full-run profile** at the live corpus size (1,724 rows) with a fully
mapped and priced 69-game / 897-ticker slate, `n_simulations=6000`:

| Phase | Legacy | Optimized |
|---|---|---|
| Total | **240.45 s** | **3.17 s** |
| History loading | ~89 s (897 reads) | **0.024 s** (1 read) |
| Model projection | 2.35 s | 2.35 s |
| Contract pricing | 0.46 s | 0.46 s |
| Game mapping | 0.01 s | 0.01 s |
| Persistence write | 0.18 s | 0.175 s |
| Rows written | 1,794 | 1,794 (identical) |
| Peak RSS | — | 173 MB |

## 3. Optimized design

**One load per scan *attempt*.** `research/persistence.py` gains
`ObservationIndex` + `load_observation_index()`: a single pass over the
observations file that derives *both* lookups the scanner needs.

```python
index = persistence.load_observation_index(obs_path)   # once, at the top of _apply_scan
...
already_captured_for_ticker = set(index.captured_labels_for(ticker))   # O(1), per ticker
```

The index is loaded **inside `_apply_scan`**, i.e. once per *attempt*,
deliberately **not** once per process. `git_durable_store.commit_and_push_with_retry`
hard-resets the working tree to the fresh remote tip and re-runs `apply_fn`
on a push rejection; an index hoisted into `main()` would dedup a retry
against stale content and silently reintroduce the duplicate-row race that
module exists to prevent. *Once per attempt* is the correct scope — the
bug was *once per ticker*.

**Key index structure.** A plain `set[str]` of canonical `observation_key`
values plus a `dict[str, set[str]]` of ticker → captured timing labels.
Exact, deterministic, no probabilistic structure, no bloom filter, no
weakening of the canonical key. Both lookups are derived in the same pass.
`observation_key_of()` is now the single shared key definition used by the
index, the append path, and `read_observation_keys`, so an index cannot
drift from the key persistence actually enforces.

Like the existing dedup loader — and for the same schema-drift reason
documented on `append_json_rows` — the index reads the **decoded dict**,
never a re-validated typed model. A row written under an older schema can
no longer break a new run's dedup. Undecodable lines are counted in
`malformed_rows` and surfaced in telemetry rather than silently dropped.
(This is a deliberate robustness change in a failure path: the old
per-ticker `read_observation_rows` would have aborted the entire run on a
single legacy-schema row.)

**Append path.** `append_json_rows` takes an optional pre-loaded
`existing_keys` set. It is a pure read-cache — it changes *how many times
the file is read*, never *which rows are considered duplicates*. Callers
that omit it (every non-scanner caller) behave exactly as before.

**Write strategy.** Rows are buffered and written in **one appending,
fsync'd batch per file per attempt**, replacing one open/read/fsync cycle
per row. Still strictly append-only: existing lines are never rewritten,
reordered, or re-serialized, and the corpus is never rewritten wholesale
(it never was). Crash safety is unchanged — the durability boundary that
matters is the all-or-nothing git commit in `git_durable_store`, so a
crash mid-scan leaves the durable store exactly as untouched as before.
`index.register_pending()` keeps the scheduling view current between the
buffer and the write, so mid-run lookups see what a re-read would have
seen.

**Other redundant work removed** (profiling-supported only): a linear
`next(g for g in games ...)` scan over ~3,550 games per mapped event
became a `games_by_id` dict built once — millions of comparisons per run
for an O(1) lookup. No other repeated work was found worth changing:
ratings fitting, residual-pool construction, schedule loading, team
registry loading and fee-schedule initialisation were already done once
per run or once per `as_of` (see below), and profiling showed market
parsing and JSON deserialization outside the history path to be
insignificant.

**Game projection reuse** was already correct and is now *asserted*.
`GameProjectionCache` runs the football model at most once per game, and
`_ratings_and_pool_for_as_of` fits ratings/residual pool at most once per
`as_of`. Added counters (`projection_builds`, `projection_cache_hits`,
`ratings_fits`) make this measurable;
`tests/test_research_scan_projection_reuse.py` fails if a change makes
projection per-contract, or refits ratings per game. Deepening the
contract ladder 5x prices 5x the contracts with **identical** projection
and ratings-fit counts.

## 4. Complexity improvement

| | Before | After |
|---|---|---|
| History reads per run | `T` (once per ticker) + one per row written | **1** |
| History work | `O(T x H)` | `O(H)` |
| Total shape | `O(T x H)` | `O(H + T)` |

## 5. Output-equivalence proof

Equivalence is **re-run, not asserted from review**.
`tests/reference/legacy_apply_scan.py` is the pre-optimization
`_apply_scan` extracted **verbatim** from `main@6015276` and frozen.
`tests/test_research_scan_equivalence.py` runs both algorithms against the
same captured market input, same games, same model inputs and same clock,
then diffs the resulting corpus files.

Only `observation.snapshot_id` (a fresh `uuid4` per observation) is
normalized away. Generated timestamps are *pinned* rather than normalized,
by passing both runs the same `now` — so they must match exactly.

Compared and identical: observation count, canonical observation keys (and
their order), game IDs, market tickers, coverage outcome, coverage reason,
mapping reason, parse status, pricing status, model probability,
executable yes/no price, research probability gap, gross gap, fee-adjusted
gap, fee fields (`estimated_taker_fee`, `fee_schedule_version`,
`fee_status`, `fee_verification_status`), model version, training cutoff,
snapshot timing label, provenance, uncertainty, family/side/team/threshold,
market midpoint, `data_versions`, the capture-state log, `AppendResult`
counters, and every `CaptureHealthReport` field.

The suite includes a guard (`test_harness_actually_produces_priced_observations`)
that fails if the fixture prices nothing — without it every comparison
would pass vacuously on two empty files. This caught a real vacuous-fixture
bug during development: synthetic team names do not resolve through the
real team registry, so the first version of the harness mapped no games
and priced nothing. The harness now uses real registry FBS teams.

## 6. Synthetic scale results

`scripts/benchmark_research_scan.py` (run by hand, never a CI gate).
Corpus rows are schema-valid rows cloned from real scanner output — a
compact stub would crash the legacy path it is compared against and
understate row size ~15x.

Optimized, history/scheduling path, measured:

| Corpus rows | 500 tickers | 2,000 tickers | 5,000 tickers | History loads | Peak RSS |
|---|---|---|---|---|---|
| 1,000 | 0.004 s | 0.005 s | 0.008 s | 1 | 54–56 MB |
| 10,000 | 0.033 s | 0.040 s | 0.036 s | 1 | 57–58 MB |
| 50,000 | 0.154 s | 0.158 s | 0.161 s | 1 | 61–63 MB |
| 100,000 | 0.325 s | 0.321 s | 0.353 s | 1 | 69–71 MB |

Fully mapped and priced 69-game / 897-ticker slate:

| Corpus rows | Runtime | History load | Peak RSS |
|---|---|---|---|
| 1,000 | 0.571 s | 0.003 s | 82 MB |
| 10,000 | 0.545 s | 0.031 s | 84 MB |
| 50,000 | 0.759 s | 0.161 s | 89 MB |
| 100,000 | 0.901 s | 0.327 s | 97 MB |

**Scaling conclusion.** Runtime is flat in ticker count and linear in
corpus size, with the entire corpus dependency confined to one load —
`O(H + T)`, as intended. Memory grows with the *index* (keys + per-ticker
label sets), not with the decoded corpus: +17 MB going from 1k to 100k
rows.

CI asserts *instrumentation*, not wall clock
(`tests/test_research_scan_scale.py`): history load count is exactly 1
across the corpus/ticker grid, the index equals a full canonical read at
100,000 rows, and two deliberately enormous complexity bounds trip only on
a genuine algorithmic regression. Timing thresholds were rejected on
purpose — they flake on shared runners and train people to re-run red
builds.

## 7. Concurrency behaviour

Overlapping runs were **already** protected, in two layers, and both were
audited rather than changed:

1. **GitHub Actions `concurrency: group: research-data-write,
   cancel-in-progress: false`** — shared by *all three* durable-store
   writers (`research-capture`, `research-settlement`,
   `research-weekly-report`), so a capture run cannot interleave with a
   settlement run either. `cancel-in-progress: false` matters: cancelling
   would kill a writer mid commit/push.
2. **`git_durable_store.commit_and_push_with_retry`** — fetch, reset to
   the fresh remote tip, re-run `apply_fn`, recompute dedup from the
   just-fetched content. This still works correctly *because* the index is
   loaded per attempt.

`tests/test_research_workflow_concurrency.py` is new: it reads the
workflow YAML and fails if any workflow granting `contents: write` lacks
the shared group or sets `cancel-in-progress: true`. The risk it guards is
a *fourth* writer being added later without the guard — an omission that
would otherwise surface as a corrupted corpus months later.

The optimization strictly *reduces* overlap risk: a 3-second scan against
an hourly schedule has far more headroom than a 5–10 minute one.

## 8. Observability

One compact `PERF {...}` JSON line per run — deliberately **not**
per-ticker (`research/scan_telemetry.py`). Per-ticker logging would bury
the scaling signal in thousands of lines and add I/O to the very loop
being measured. `--telemetry-json PATH` also writes it to a file.

Fields: `run_started_at`, `run_completed_at`, `wall_clock_seconds`,
`discovered_market_count`, `observation_count`, `history_row_count`,
**`history_load_count`**, `history_load_seconds`, `distinct_games`,
`game_projection_count`, `ratings_fit_count`, `priced_contract_count`,
`unresolved_count`, `duplicate_count`, `malformed_row_count`,
`persistence_write_seconds`, plus per-phase `market_discovery_seconds`,
`game_mapping_seconds`, `projection_seconds`, `contract_pricing_seconds`.

`history_load_count` is the headline invariant: **it must read 1**.
Anything higher means the per-ticker re-read has regressed.

These are observability counters only — nothing here feeds a pricing,
mapping, scheduling or persistence decision, and corpus rows are identical
whether or not telemetry is collected.

## 9. Known remaining bottlenecks

Genuine, and deliberately **not** addressed here:

1. **Model projection now dominates.** At the live shape it is 2.35 s of a
   3.17 s run (~74%). That is real, intended model work
   (`n_simulations=6000` Monte Carlo per game), and reducing it would be a
   modelling change — explicitly out of scope for this milestone.
2. **History load grows linearly with the corpus** — 0.33 s at 100,000
   rows. Correct and expected for an append-only JSONL store, and ~1,400x
   cheaper than before. If the corpus reaches millions of rows, a
   per-season sharded or indexed store would be the next step; at the
   projected volume (thousands of rows/week) this is comfortable for the
   foreseeable future.
3. **Market discovery is serial across the three series** and is network-
   bound. Not measurable offline and not a corpus-growth problem, so no
   change was made on speculation.
4. **`map_kalshi_event_to_game` scans all candidate games per event.**
   Measured at 0.01 s per run at the full slate — genuinely insignificant,
   so it was left alone rather than optimized prematurely.

## 10. Deferred

- No change to collection cadence (still hourly).
- No closing-price capture, settlement or analytics work.
- No MLB repository change of any kind.
