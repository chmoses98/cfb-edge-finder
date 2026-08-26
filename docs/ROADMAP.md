# Roadmap

The mission's suggested sequence, kept largely as proposed with rationale
noted per milestone. Milestone A is complete as of this PR; everything
else is future work for subsequent missions.

## Milestone A — Foundation (this PR)

Repo, canonical IDs, schemas (game/projection/market/coverage/snapshot/
provenance), the coverage-ledger invariant checker, the distribution-based
market-pricing math, a placeholder fee-aware EV shape, a data-source
registry, tests, CI, and this documentation set.

**Status: done.** See the final report for exact test/lint counts.

## Milestone B — Data ingestion

Schedule/team ingestion against CFBD (primary), with an ESPN client built
and unit-tested as the cross-check fallback (not yet wired into the
default CLI run). Establishes the team registry/alias-resolution
mechanism referenced but not built in Milestone A. See
`docs/MILESTONE_B.md` for the full design (week/postseason semantics,
neutral-site handling, duplicate/reschedule reconciliation, storage,
known limitations).

**Status: done and merged, including a genuine live validation pass.**
`scripts/validate_cfbd_live.py`, run twice via a GitHub Actions
`workflow_dispatch` runner (this dev environment's own network egress to
CFBD stays blocked, same constraint noted throughout this document),
confirmed the team registry, schema assumptions, and schedule-ingestion
pipeline against real, authenticated 2026 CFBD data -- see
`docs/MILESTONE_B.md`'s "Live validation" section for the full diagnostic
output. `scripts/ingest_schedule.py` still runs in fixture mode inside
this environment for the same network-access reason; the live-validated
code path is proven correct, not merely tested against synthetic fixtures.

## Milestone B.5 — Historical Kalshi CFB market audit

Determines, from genuine historical Kalshi evidence (real tickers, CFTC
self-certification filings, directly-quoted contract templates and live
prices -- `kalshi.com`/Kalshi's API domains are themselves blocked from
this environment, so this was done via web search rather than a live
feed), which CFB market families Kalshi has actually offered, how they're
structured, and which should be Milestone C's first-class targets. See
`docs/KALSHI_CFB_MARKET_AUDIT.md` for the full evidence trail and
`src/cfb_edge_finder/kalshi/cfb_market_family_registry.py` for the
machine-readable, tested classification.

**Status: done.** CORE_V1 result: game winner, point spread, game total --
all CONFIRMED via real tickers/self-certification filings, not assumed.
First-half totals, team totals, touchdown props, and all season/futures
families (national champion, conference champion, Heisman, AP poll, win
totals, coach markets, etc.) are explicitly scoped OUT of Milestone C's
first wave -- see that document's "Scope exclusions" section for why. This
also means Milestone D's "team_total" family below is UNVERIFIED against
real Kalshi evidence and should not be assumed a real target without
further confirmation. Whether FBS-vs-FCS games get individual Kalshi
listings is UNVERIFIED, not assumed either way; re-check against a live
Kalshi feed before Milestone C finalizes its default game universe.

## Milestone C — Baseline game model

Opponent-adjusted offense/defense, QB value, roster continuity, coaching/
system adjustments, home-field advantage -> populates real
`GameDistribution` values (today only synthetic ones exist, in tests).
This is explicitly the first milestone allowed to be a real, if simple,
predictive model -- Milestone A deliberately stops short of this per
mission section 6 ("do not build a simplistic Team A 31, Team B 24 model
and treat it as finished").

## Milestone C.2 — Improve CFB forecast quality before Kalshi pricing

A diagnosis-and-ablation pass over the Milestone C hardened baseline,
before proceeding to Kalshi pricing. See `docs/MILESTONE_C2.md` for the
full write-up: one hyperparameter change (`ridge_lambda` 25.0 -> 10.0)
was adopted after a genuine live ablation showed a real, stable,
multi-metric improvement; a favorite-tail margin-bias pattern and a
high-total shootout effect were both diagnosed in detail via new
leakage-safe segmentation tooling (`modeling/diagnostics.py`) but
explicitly NOT fixed this pass -- reported as open weaknesses, not
smoothed over. FBS-vs-FCS margin/spread output remains
UNSUPPORTED_FOR_PRICING.

**Status: done, not merged pending review; recommends another
model-quality pass (not Milestone D) as the next step**, since the
model's largest, most broadly-distributed known bias remains open.

## Milestone D — Market probability engine

Mostly already built in Milestone A
(`projections/distribution.py::price_market`) for moneyline/spread/
alt-spread/total/alt-total/team-total. Milestone D's remaining work is
first-half markets (needs a first-half `GameDistribution`, deliberately
deferred) and validating the Normal-approximation assumption against real
Milestone C output once it exists.

## Milestone E — Preseason Production/Research Readiness

Discovery/mapping/pricing of the supported market universe (this
milestone's original scope, reproducing the MLB audit's broad-sweep ->
allowlist-classify -> parse -> match -> price pipeline,
`docs/MLB_ARCHITECTURE_AUDIT.md` section 1) was already delivered by
Milestone D, ahead of this milestone's original sequencing -- see
`docs/MILESTONE_D.md`. Milestone E was re-scoped to what the mission
actually needed next: the durable, autonomous, season-long capture
machine that has to exist BEFORE the first meaningful 2026 game, not
individual pieces built "as we go." This is also where the storage-
strategy decision in `docs/STORAGE_STRATEGY.md` actually gets
implemented, now that real capture volume exists to justify it.

**Status: done, not merged.** Durable append-only persistence (a
dedicated `research-data` git branch, deterministic dedup, race-safe
concurrent writes), the hourly scheduler with timing-bucket due/missed
logic, kickoff-change/postponement handling, a rigorous closing
definition, CFBD-sourced settlement (winner/spread/total, verified
operators, no special-cased overtime), CLV/gap-bucket/correlation-aware
research metrics, health monitoring with collapse thresholds, weekly/
season reports, and a hard-disabled future qualification interface. Full
write-up in `docs/MILESTONE_E.md`; see the mission's own final report for
exact test/lint counts and the launch-readiness checklist. Recommendation/
staking/execution logic remains entirely absent -- mechanically checked.

## Milestone F — Prospective snapshots

Opening/intermediate/pre-kickoff market/model observation capture using
`ProspectiveSnapshot` (schema already built in Milestone A) plus a
FROZEN_COPY-style capture mechanism reproducing
`docs/MLB_ARCHITECTURE_AUDIT.md` section 6.

**This is the critical path to a live Week 0/Week 1 capture** (see below).

## Milestone G — Edge research

Executable price, fees, net edge, bet-up-to, CLV, and calibration.
`kalshi/executable_price.py` already establishes the *shape* of the net-EV
formula in Milestone A; Milestone G's job is replacing the placeholder fee
constant with a verified one and wiring in real CLV/calibration tracking
reproducing the MLB audit's multi-axis eligibility pattern
(`docs/MLB_ARCHITECTURE_AUDIT.md` section 5).

## Milestone H — Controlled recommendation layer

Only after prospective validation (mission section 12). This is where the
"net executable edge x model confidence x data completeness x calibration
confidence x market quality x uncertainty penalty" ranking formula from
mission section 7 gets built and its weights fit against real Milestone F/G
data -- not before, since fitting it earlier would mean guessing weights
with no evidence, which the mission explicitly warns against.

## Deviations from the mission's suggested sequence

None structurally -- the eight milestones are kept as proposed. The one
refinement: Milestone A intentionally front-loads the coverage-ledger
invariant checker and the distribution-pricing math (nominally Milestones
D and part of E) because both are pure, testable logic with zero external
dependencies, and having them proven correct now de-risks every later
milestone that depends on them. Neither required real data or a real
Kalshi connection to build correctly.

## Critical path to a live Week 0/Week 1 capture

1. Milestone B: a working CFBD client producing real `GameRecord`s for the
   target week. **Built and tested, blocked only on live network access**
   -- `scripts/ingest_schedule.py` runs today against fixtures; pointing
   it at a real `CFBD_API_KEY` from an environment with network access is
   the remaining step, not new code.
2. Milestone C: even a minimal opponent-adjusted rating system -- it does
   not need to be good yet, it needs to produce a `GameDistribution` per
   game so the rest of the pipeline has real input instead of synthetic
   test fixtures.
3. Milestone E: a working Kalshi discovery sweep for `KXNCAAFGAME` (the
   series ticker structure is already identified in
   `docs/DATA_SOURCES.md`), mapped to `GameRecord`s via team-slug/date
   matching.
4. Milestone F: point a capture job at (2)+(3) before a real kickoff and
   write `ProspectiveSnapshot` records.

Everything after that (G, H) is refinement of an already-flowing pipeline,
not new plumbing -- which is why F is the meaningful finish line for "get
prospective capture online," not H.

## What should be built next (immediately after this PR)

Milestone C, the baseline game model -- Milestone B's ingestion pipeline
(CFBD client, team registry, week/postseason normalization, duplicate and
reschedule reconciliation) is built and tested end-to-end against
fixtures; what it's still missing is a live network path to actually run
it for real, which is an infrastructure/access question, not a design
question. Milestone C can proceed against the same fixture-derived
`GameRecord`s in the meantime. See `docs/MILESTONE_B.md` "Known
limitations" for the specific items (live schema/rate-limit verification,
team-registry conference cross-check, ESPN cross-check wiring) worth
resolving whenever network access is available.
