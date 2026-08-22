# MLB Architecture Audit (edge-finder-api → cfb-edge-finder)

Read-only audit of `chmoses98/edge-finder-api`, conducted to identify which
architectural patterns are sport-agnostic and worth reproducing here, versus
which are MLB-specific and must not be carried over. The MLB repository was
not modified in any way to produce this audit.

Each section: what the MLB repo does, the concrete files, and a reusability
verdict.

---

## 1. Market-Universe Capture / Kalshi Discovery

**What it does:** Two-tier discovery. `api/kalshisearch.js` runs a broad,
prefix-agnostic pull (`/markets?status=open&limit=1000`). `lib/kalshi_discovery.py`'s
`discover_unknown_series()` diffs the broad pull against a hardcoded series
allowlist and *retains but does not activate* anything unknown. A separate
`GET /series` sweep tags association via ticker-prefix/title heuristics.
`scripts/discover_kalshi_mlb_markets.py` is the real universal engine:
parse → classify → match to game → price, and it never drops anything.
`lib/kalshi_mlb_single_game_registry.py` is a strict allowlist gate with a
closed exclusion-reason enum, plus a non-fatal "new unclassified series"
early warning that never auto-includes anything.

**A. Sport-agnostic (reproduce):** Broad prefix-agnostic sweep → allowlist
classification → parse/classify/match/price pipeline → never-drop
discipline → strict registry with closed exclusion-reason enum + early
warning for new series. HHMM elapsed-minutes matching for same-day
disambiguation.

**B. MLB-specific (do not reuse):** `SERIES_CATALOGUE`/series-ticker values
(`KXMLBGAME`, etc.), MLB market-family taxonomy, non-MLB-competition
exclusion lists (WBC, college baseball).

## 2. Market-Ledger Completeness & Accepted/Rejected/Missing/Failed States

**What it does:** A fixed required-market list is the real-money gate, but
nothing on it is ever silently dropped -- a missing one gets a synthetic
"Evaluation Failed" row. Every row's status is exactly one of
Accepted/Rejected/Missing Data/Evaluation Failed, each with a required
companion field. A separate accounting layer computes coverage with **two
independent denominators** so a bug that drops a market before the main
pipeline even runs is still caught.

**A. Sport-agnostic:** Fixed required-market list + synthetic failure rows,
closed status enum with mandatory companion field, dual-denominator
coverage accounting to catch silent drops upstream of the main pipeline.

**B. MLB-specific:** The 11 specific MLB market names.

*cfb-edge-finder equivalent:* `MarketStatus` (`src/cfb_edge_finder/schemas/common.py`)
and `CoverageLedger.assert_no_missing()` (`src/cfb_edge_finder/kalshi/coverage_ledger.py`)
reproduce this pattern directly.

## 3. Ticker Resolution

**What it does:** `lib/kalshi_ticker_time.py` computes true elapsed-clock-
minutes distance (not raw subtraction) between ticker-embedded start times,
fixing a real hour-boundary bug in doubleheader disambiguation.
`scripts/write_tracked_tickers.py` hard-fails if any real-money bet lacks a
ticker.

**A. Sport-agnostic:** Fully reusable as-is -- already sport-agnostic in
the MLB repo. The "fail loud if a real-money bet has no ticker" pattern
applies identically to CFB.

**B. MLB-specific:** None.

## 4. Executable-Price Logic & Fee-Aware Net EV

**What it does:** `scripts/executable_price.py` normalizes bid/ask into
executable prices and implements a "bet up to" ceiling check.
`lib/edgelab/kalshi_fees.py`'s order-decomposition never lets unused cash
count as loss. The production formula:
`netExecutableEdge = (rawEdgeVsExecutable - expectedFeeDrag) * confidenceMultiplier`,
computed once and reused everywhere, never silently zero-fee on an
unregistered series.

**A. Sport-agnostic:** Fully reusable -- Kalshi's fee schedule and
ask/bid mechanics are exchange-level, not sport-level.

**B. MLB-specific:** None (Kalshi-platform-specific, not sport-specific).

*cfb-edge-finder equivalent:* `src/cfb_edge_finder/kalshi/executable_price.py`
reproduces the shape of this formula, but the fee constant is an explicit
UNVERIFIED placeholder -- see that module's docstring and
`docs/DATA_SOURCES.md`.

## 5. Bet-Up-To Prices & Qualification Tiers

**What it does:** A closed-form price-ceiling inversion per tier, a
confidence tiering function with a disagreement cap, and -- most
importantly -- **multiple independent status axes** per ledger row:
bet-eligibility, CLV-capture, and review-integrity are all tracked
separately, with the explicit principle that missing CLV data never blocks
a live actionable bet. A newer doc separates seven independent status
fields, proving analysis/paper-tracking and real-money eligibility are
orthogonal concerns.

**A. Sport-agnostic:** The whole multi-axis eligibility model -- tier
thresholds, disagreement caps, price ceilings, and especially the
orthogonal status-field separation.

**B. MLB-specific:** The specific numeric thresholds and disagreement-cap
values -- must be re-derived from CFB backtests, not copied.

## 6. Research Captures / Prospective Snapshots

**What it does:** A Snapshot is an immutable, hash-verifiable,
content-addressed manifest of everything the model knew at one moment. Key
decision: no source was found to be safely "referenced immutable" -- every
believed-write-once source turned out mutable in some real scenario, so
**everything is FROZEN_COPY** (bytes physically duplicated, hash-reverified
later). Capture is deliberately non-fatal to the production workflow but
never silently invisible (status record + step-summary warning + separate
allowed-to-fail integrity check). Retention pruning is filename-date-based,
not mtime-based, after mtime was found to always read "now" post-checkout.

**A. Sport-agnostic:** The entire snapshot philosophy -- immutable
content-addressed manifest, FROZEN_COPY-by-default, non-fatal-but-never-
invisible capture with a separate integrity check, filename-based
retention.

**B. MLB-specific:** None in the mechanism; only component names are MLB
inputs.

*cfb-edge-finder equivalent:* `ProspectiveSnapshot`
(`src/cfb_edge_finder/schemas/snapshot.py`) is the record shape; the
FROZEN_COPY capture mechanism itself is a Milestone F concern.

## 7. CLV Tracking

**What it does:** Seed tracked tickers from accepted bets, poll near
closing time, snapshot-diff the closing price against entry price.
"Pending/unavailable" CLV is formally never a block on a live bet.

**A. Sport-agnostic:** Fully reusable -- exchange mechanics, not sport
mechanics.

**B. MLB-specific:** Pregame polling cadence (tuned to first-pitch timing)
will need retuning to CFB's pregame windows.

## 8. Calibration

**What it does:** Measurement-only layer over decided bets only (push/void
excluded from the denominator entirely). A three-tier sample-size gate
(`n<20`/`20-99`/`>=100`) where the underlying number is always computed
regardless of status -- status is a reading instruction, never a filter.

**A. Sport-agnostic:** The decided-bets-only denominator, non-configurable
sample-size-status gate, calibration-error formula.

**B. MLB-specific:** The actual calibration factor values and any
MLB-market-specific bucket dimensions.

## 9. Manual Wager Imports

**What it does:** A canonical single-write-API ledger
(`data/edgelab/bets/bets.jsonl`) with schema validation, deterministic
IDs, file-locked read-modify-write, and idempotent-vs-conflict handling on
resubmission. A legacy parallel dual-write system also exists as
documented technical debt.

**A. Sport-agnostic:** The canonical-ledger pattern -- single write API,
idempotent IDs, explicit conflict handling, entry-method provenance, bulk
import with ambiguity-refusal.

**B. MLB-specific:** The legacy dual-write-path debt itself should **not**
be replicated -- cfb-edge-finder should start with only a canonical
ledger, never a second competing write path.

## 10. Stable sourceBetKey / importBatchId

**What it does:** Bet IDs are built from caller-assigned, explicit,
required-together `importBatchId` + `sourceBetKey` when no entry timestamp
is known -- deliberately never derived from an ambient field (found to
collide) or a positional index (found to break under reordering).

**A. Sport-agnostic:** This whole ID-design pattern is directly reusable
and contains no MLB content.

**B. MLB-specific:** None.

## 11. Settlement/Reporting

**What it does:** An explicit settlement-source hierarchy with a
documented priority order and fallback-flagging (primary source, fallback
cross-check, last-resort with an explicit flag). A closed result enum
(WIN/LOSS/PUSH/VOID/PENDING). Reports are always regenerated from the
ledger, never hand-edited.

**A. Sport-agnostic:** The pattern -- settlement-source hierarchy with
fallback-flagging, closed result enum, regenerate-never-edit reports.

**B. MLB-specific:** F5/innings-based settlement boundary logic; CFB has
no innings and needs its own quarter/half/full-game and overtime rules.

## 12. Correlation/Risk Controls

**What it does:** Ordered, downgrade-only gates (never a hard reject) run
post-evaluation: team-total safety, portfolio composition, and
correlation/concentration (run first so its downgrades feed the others). A
`CORRELATION_RULES` table detects same-thesis market pairs and keeps the
higher-edge expression, downgrading the other to paper. Empirically-
triggered rule suspensions require explicit, numerically-justified
re-qualification criteria.

**A. Sport-agnostic:** The gate architecture itself -- ordered
downgrade-only gates, correlation-group detection with "keep highest edge,
downgrade siblings," per-game/cluster stake caps, empirically-triggered
suspension with re-qualification criteria.

**B. MLB-specific:** The specific correlation pairs (ML+F5, NRFI/YRFI vs
F5) -- CFB needs its own table (e.g. spread + moneyline same side, team
total + game total).

## 13. Provenance/Model Versioning

**What it does:** Three never-conflated commit-SHA concepts
(production/snapshot-writer/candidate-replay). Provenance capture runs
deliberately *after* every in-job git rebase (a real ordering bug was found
and fixed). A documented, still-unfixed gap: no object in most of the
pipeline actually carries modelVersion/calibrationVersion/pipelineRunId.

**A. Sport-agnostic:** The three-SHA distinction, the capture-after-rebase
ordering discipline, code-path-scoped dirty checks.

**B. MLB-specific:** None in the mechanism -- but cfb-edge-finder
deliberately does NOT repeat the missing-modelVersion gap: `ModelVersion`
and `DataProvenance` are baked into `ProjectionRecord` and
`ProspectiveSnapshot` from day one (see `src/cfb_edge_finder/schemas/provenance.py`).

## 14. Fail-Loud Safety Gates

**What it does:** A tiered rule system (T1 hard gate / T2 soft gate / T3
sizing scalar). A sentinel-value rejection pattern (single canonical
constants file, separate field-taxonomies for "model output can
legitimately be 100" vs "100 in a settlement field is contamination"). A
closed game-status-string taxonomy drives pregame/live/final gating.

**A. Sport-agnostic:** The T1/T2/T3 tiering mechanism, sentinel-value
rejection pattern, closed game-status taxonomy.

**B. MLB-specific:** All 75 numbered MLB handicapping rules themselves
(xFIP regression, bullpen fatigue, platoon splits, park factors) -- only
the empty tiering *framework* should be reused, refilled with CFB-specific
rules once they exist.

## 15. Canonical Schemas

**What it does:** Target objects designed to match what the code already
writes -- migration is a rename/formalize exercise, not a redesign.
Honestly documents its own gaps (dual field names for the same concept,
game identified by an unstable string, missing version fields) rather than
silently fixing them mid-migration. Newer artifacts adopt a clean envelope
convention (stage/date/schemaVersion/producedBy/sourceStage).

**A. Sport-agnostic:** The envelope convention, the discipline of
documenting gaps honestly, the required/optional table format.

**B. MLB-specific:** All actual field content is MLB-only.
cfb-edge-finder's schemas (`src/cfb_edge_finder/schemas/`) are written
from scratch specifically to avoid the dual-field-name debt and the
missing-version gap this audit found.

## 16. Storage Architecture

**What it does:** Everything lives in git-committed `data/` -- no external
storage exists. `data/` is ~962MB, dominated by Kalshi registry snapshots
(389MB) and pipeline artifacts; MLB-specific Statcast raw data adds 33MB.
Growth is actively managed via filename-date-based retention pruning. A
shared safe-commit helper never trusts `git rebase --autostash`'s exit
code after a real incident left literal conflict markers committed to
main.

**A. Sport-agnostic:** Git-as-database as a starting choice, plus its
supporting discipline (filename-based retention, safe-commit-never-trusts-
rebase-exit-code). Worth re-evaluating at scale, but the discipline
travels regardless of the final storage choice -- see
`docs/STORAGE_STRATEGY.md`.

**B. MLB-specific:** `data/statcast_raw/` and the Savant/Statcast fetch
pipeline are pure MLB content, not storage-architecture pattern. CFB has
no comparable per-play tracking-data volume, so its `data/` growth profile
is expected to be smaller.

---

## Baseball-specific concepts confirmed present but explicitly out of scope for CFB

Platoon splits, Statcast/Savant pitch-level data, bullpen workload, lineup
confirmation, park factors, F5/F3/F7 markets, YRFI/NRFI, and
doubleheader-specific game-identity resolution (the underlying HHMM-distance
*algorithm* is reusable; its *doubleheader* use case is MLB-specific --
CFB's analog, if it ever matters, would be conference-championship-weekend
or bowl-season same-day scheduling).
