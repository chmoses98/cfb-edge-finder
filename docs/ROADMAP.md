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

Schedule/team/game ingestion against CFBD (primary) with ESPN as a
cross-check fallback, producing validated `GameRecord`s with real
`source_game_ids` and `DataProvenance`. Establishes the actual team-slug
master table referenced but not built in Milestone A (`ids.py` assumes a
normalized slug; Milestone B needs to guarantee that normalization is
collision-free across the real FBS+FCS team set, not just assert it by
construction).

**Critical path item:** stand up a real `Settings`-driven CFBD client and
confirm actual free/paid tier limits directly (see
`docs/DATA_SOURCES.md` "Unresolved data risks" #2) before assuming a call
budget for the rest of the roadmap.

## Milestone C — Baseline game model

Opponent-adjusted offense/defense, QB value, roster continuity, coaching/
system adjustments, home-field advantage -> populates real
`GameDistribution` values (today only synthetic ones exist, in tests).
This is explicitly the first milestone allowed to be a real, if simple,
predictive model -- Milestone A deliberately stops short of this per
mission section 6 ("do not build a simplistic Team A 31, Team B 24 model
and treat it as finished").

## Milestone D — Market probability engine

Mostly already built in Milestone A
(`projections/distribution.py::price_market`) for moneyline/spread/
alt-spread/total/alt-total/team-total. Milestone D's remaining work is
first-half markets (needs a first-half `GameDistribution`, deliberately
deferred) and validating the Normal-approximation assumption against real
Milestone C output once it exists.

## Milestone E — Kalshi universe capture

Discover, map, and archive the complete supported market universe,
reproducing the MLB audit's broad-sweep -> allowlist-classify -> parse ->
match -> price pipeline (`docs/MLB_ARCHITECTURE_AUDIT.md` section 1) with
CFB's own series tickers (`KXNCAAFGAME`, `KXNCAAFWINS`, `KXNCAAF` --
see `docs/DATA_SOURCES.md`). This is also where the storage-strategy
decision in `docs/STORAGE_STRATEGY.md` actually gets implemented, once
there's real capture volume to justify it.

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
   target week (blocked only on confirming CFBD's actual rate limits).
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

Milestone B, specifically the CFBD client and the real team-slug master
table -- every later milestone depends on `GameRecord`s that are actually
populated from live data rather than test fixtures, and Milestone B has no
dependency on anything else in this roadmap.
