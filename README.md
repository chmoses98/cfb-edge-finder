# cfb-edge-finder

**A Kalshi market discovery, capture and normalization system for college
football.** It answers one question, reliably:

> For this physical college-football game, what EXACTLY can I bet on
> Kalshi right now?

It does **not** predict games. The projection model this repository used
to build was retired from the live path in September 2026 because it did
not produce an edge worth wagering on -- see
`docs/MODEL_RETIREMENT_2026.md`. The live artifact carries no model
probability, no fair value, no expected score and no edge; the judgement
about what a price is worth belongs to whoever reads the catalog.

## The live path

```bash
python scripts/build_kalshi_cfb_catalog.py --out-dir data/live --print-summary
```

**No credentials.** Kalshi's market-data endpoints are public reads and
physical-game identity comes from Kalshi's own `football_game` milestones,
so the live path needs no `CFBD_API_KEY` and no Kalshi key. It never
touches an order or portfolio endpoint, and has no ability to place a bet.

| Artifact | What it is |
|---|---|
| `data/live/cfb_market_catalog.json` | **Primary product.** The slate index (~0.7 MB): every game's identity, events, per-family market counts, completeness diagnostics, and a pointer to its detail file. |
| `data/live/games/<game_key>.json` | One game's complete inventory: every contract with raw settlement rules, executable prices, quoted sizes and liquidity. |
| `data/live/cfb_catalog_status.json` | Counts and a content fingerprint, for change detection. |

A live capture is 239 games and 15,312 contracts. Inlined into one file
that is **52 MB** — not ingestible, and not something to commit every 30
minutes. Split, the index is 59× smaller, an unchanged slate rewrites
nothing, and a single price move rewrites exactly one small file.

Refreshed by `.github/workflows/kalshi-market-catalog.yml` every 30
minutes in the Thursday-Sunday slate window and 6-hourly otherwise,
committing only when the market surface actually changed.

### What makes it trustworthy

- **Discovery is never filtered by market family.** Classification happens
  *after* discovery and is a label, never a gate. A family Kalshi invents
  tomorrow is captured and labelled `unknown` -- never dropped. The
  previous architecture drove discovery from eight hardcoded series; the
  live exchange carries **145 CFB series, 98 with open events**.
- **A request failure is never a zero.** Every sweep reports whether it
  completed. A failed event fetch marks its game `INCOMPLETE` and names
  the event in the artifact; it cannot reduce to "0 markets".
- **Two independent discovery paths.** Milestones are the spine; a dynamic
  series sweep runs alongside and reports anything the milestones did not
  name, so a gap is a number in the output instead of silence.
- **Completeness is claimed separately for native and combo markets.**
  Kalshi's CFB combos are dynamically instantiated and cannot be
  enumerated per game, and the artifact says so rather than implying
  coverage it does not have.

### Consuming it

```bash
python scripts/run_cfb_preflight.py --last-successful-run-at <last successful run> --game <GAME_KEY>
```

`CATALOG FRESH` / `CATALOG STALE`, plus which games may be handicapped.
Exit 0 = fresh and usable, 2 = stale, 3 = a requested game's discovery was
incomplete.

**The gate:** a game may not be used for handicapping unless its
`native_game_markets_complete` is true. `capture_complete: false` at the
slate level does **not** invalidate games that individually passed — each
game's own completeness is authoritative. See
`docs/RUN_CFB_CONTRACT.md`.

**Freshness:** the catalog does not commit when nothing changed, so an old
`captured_at` does not mean the collector stopped — and a recent one does
not prove it is alive. Liveness comes from the last successful production
run; the artifact corroborates it.

See `docs/KALSHI_MARKET_CATALOG.md` for the discovery flow, the live
endpoint behaviour it was built from, and the output schema, and
`docs/RUN_CFB_CONTRACT.md` for the consumer procedure.

### Exhaustive execution

```bash
python -m cfb_edge_finder.execution prepare-live --refresh
```

The catalog says what can be bet. `cfb_edge_finder.execution` answers the
next question -- *did we actually look at all of it before picking a
bet?* -- and refuses to produce a shortlist until the answer is provably
yes:

```
eligible_contracts == evaluated_contracts + explicitly_unpriceable_contracts
unaccounted_contracts == 0
```

It compacts the live universe into per-game execution packets, shards
them by kickoff window without ever splitting a game, takes a handicap
supplied from OUTSIDE the repository (no model is involved), prices every
eligible contract that handicap can price, marks every contract it cannot
as explicitly UNPRICEABLE, and keeps durable per-game state so an
interrupted session resumes instead of restarting. A live Saturday is 115
games and 14,222 eligible contracts across four window shards.

No stake is computed, no order can be placed, and no edge is claimed --
see `docs/LIVE_EXECUTION.md`.

---

This is a separate, from-scratch repository. It reuses architectural
*patterns* audited from the production MLB system at
`chmoses98/edge-finder-api` (see `docs/MLB_ARCHITECTURE_AUDIT.md`), but no
code or MLB-specific logic was copied, and that repository was not
modified to produce this one.

## Retired research (preserved, not deleted)

Everything below this line predates the pivot. All of it still exists, all
of its tests still pass, and every manual workflow is still dispatchable --
but none of it is on the live path, and four scheduled workflows that fed
the model are hibernated. `docs/MODEL_RETIREMENT_2026.md` is the plan:
what was hibernated, what is preserved, how to revive any of it, and the
one irreversible consequence.

## What's here

- `src/cfb_edge_finder/ids.py` -- canonical, source-independent game IDs.
- `src/cfb_edge_finder/schemas/` -- pydantic schemas: `GameRecord`,
  `ProjectionRecord`/`GameDistribution`/`UncertaintyProfile`,
  `MarketRecord`, `CoverageLedgerEntry`, `ProspectiveSnapshot`,
  `ModelVersion`/`DataProvenance`.
- `src/cfb_edge_finder/projections/distribution.py` -- the core "one game
  distribution prices many markets" math: moneyline, spread, alt-spread,
  total, alt-total, team-total, all closed-form off a single
  `GameDistribution`.
- `src/cfb_edge_finder/kalshi/coverage_ledger.py` -- the market-coverage
  invariant checker: every discovered Kalshi ticker must resolve to a
  known `CoverageOutcome`, and `assert_no_missing()` catches markets that
  would otherwise silently vanish from the pipeline.
- `src/cfb_edge_finder/kalshi/executable_price.py` -- the *shape* of a
  fee-aware net-EV calculation. The fee constant is an explicit,
  documented placeholder -- not verified production math yet.
- `src/cfb_edge_finder/data/sources.py` -- machine-readable data-source
  registry, companion to `docs/DATA_SOURCES.md`.
- `src/cfb_edge_finder/teams/registry.py` -- canonical FBS team registry
  with exact-match, fail-loud alias resolution (Miami/Miami (OH),
  USC/South Carolina, Ole Miss/Mississippi, etc.) -- see
  `docs/MILESTONE_B.md`.
- `src/cfb_edge_finder/data/cfbd_client.py` / `espn_client.py` -- primary
  (CFBD) and fallback (ESPN) schedule clients, env-var auth, unit-tested
  against mocked HTTP -- no live call has been made from this environment
  (network egress blocked here; see `docs/MILESTONE_B.md`).
- `src/cfb_edge_finder/ingestion/` -- week/postseason semantic
  normalization, source-row-to-`GameRecord` normalization, and
  duplicate/reschedule reconciliation.
- `scripts/ingest_schedule.py` -- `python scripts/ingest_schedule.py --season 2026`
  runs the full pipeline. Falls back to deterministic fixture data when
  `CFBD_API_KEY` isn't set (this environment's default) and says so
  explicitly -- see `docs/MILESTONE_B.md`.
- `docs/` -- architecture, schema rationale, storage strategy, the MLB
  audit, data-source research, Milestone B design, and the milestone
  roadmap.

## What's deliberately not here yet

Player props, automated bet placement, bankroll management, a large ML
model, NFL support, a shared cross-sport package, production staking
thresholds, a dashboard -- see mission section 11 / `docs/ROADMAP.md`.
`ratings/`, `betting/`, and `research/` exist as documented empty package
stubs so later milestones have an obvious home, not as placeholders for
hidden complexity.

## Getting started

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

ruff check src tests
pytest -v
```

Copy `.env.example` to `.env` and set `CFBD_API_KEY` to run schedule
ingestion against the real CFBD API:

```bash
python scripts/ingest_schedule.py --season 2026
```

Without a key, it automatically runs in deterministic fixture mode and
prints a notice -- no live 2026 data is fetched or implied.

## Documentation map

- `docs/KALSHI_MARKET_CATALOG.md` -- **the live path**: discovery
  architecture, verified Kalshi endpoint behaviour, output schema,
  completeness semantics, automation cadence, known limitations.
- `docs/RUN_CFB_CONTRACT.md` -- **the consumer contract**: the RUN CFB
  procedure, the per-game completeness gate, and how freshness is
  determined without guessing.
- `docs/LIVE_EXECUTION.md` -- **exhaustive execution**: the coverage
  invariant, the mechanical disposition vocabulary, kickoff-window
  sharding, the handicap payload schema, the per-game completion gate,
  durable resume, and the five commands.
- `docs/MODEL_RETIREMENT_2026.md` -- **the pivot**: what was retired from
  the live path, what was hibernated and why, what is preserved, how to
  revive it, and the Actions/storage before-and-after.
- `docs/ARCHITECTURE.md` -- component diagram, game/projection flow,
  Kalshi flow, coverage-ledger design, uncertainty approach.
- `docs/SCHEMAS.md` -- canonical game ID, and every schema's rationale.
- `docs/DATA_SOURCES.md` -- researched CFB data sources, costs, auth,
  fallbacks, and unresolved risks.
- `docs/MILESTONE_B.md` -- schedule/team ingestion: source decisions,
  team registry, alias strategy, week/postseason semantics, neutral-site
  handling, duplicate/reschedule reconciliation, known limitations.
- `docs/STORAGE_STRATEGY.md` -- what stays in git vs. external storage,
  and why.
- `docs/MLB_ARCHITECTURE_AUDIT.md` -- what was reused from edge-finder-api
  and what was deliberately left behind.
- `docs/MARKET_EXPRESSION.md` -- market-expression and correlation
  framework: grouping hierarchy, proved exact equivalence, fee-aware
  break-even, dominated equivalents, ladder coherence, exposure
  primitives.
- `docs/ANALYTICS.md` -- research analytics: model-market gaps, side-aware
  CLV, calibration and market comparison, fee-adjusted unit economics,
  gap/price/timing slices, cluster-aware uncertainty, and limitations.
- `docs/SETTLEMENT.md` -- settlement and outcome attribution: result
  source, exact winner/spread/total semantics, Kalshi cross-checking,
  settlement states, research-unit P/L, closing linkage, incremental
  workflow.
- `docs/PROSPECTIVE_COLLECTION.md` -- the scheduled collection regime:
  checkpoint schedule, closing definition and completeness accounting,
  cadence, concurrency, persistence, health checks, reschedules.
- `docs/PERFORMANCE.md` -- scanner performance: the per-ticker history
  re-read bottleneck, the one-load-per-run fix, output-equivalence proof,
  scale benchmarks, concurrency behaviour, remaining bottlenecks.
- `docs/MAPPING_COVERAGE.md` -- what the ~1,400 unresolved Kalshi
  markets actually are: the reconciling classification, why 87% are
  populations we decline on purpose, and the one FBS-vs-FBS matchup
  absent from the schedule source.
- `docs/EXTERNAL_SCHEDULER.md` -- why GitHub's schedule service stopped
  being the primary clock (1.7% delivery measured), the independent
  scheduler that replaces it, the exact fine-grained token permission,
  and the setup steps.
- `docs/COLLECTION_TRIGGER.md` -- how the collector is invoked: the
  conductor chain, the cron fallback, the manual emergency path, the
  trigger SLA derived from the 14-minute closing window, heartbeats, and
  the normal/degraded/emergency operating states.
- `docs/WEEK1_READINESS.md` -- end-to-end Week 1 audit: the system
  diagram, workflow cadences, schema-version policy and how legacy rows
  are treated, current live health, pending live proofs, findings, and
  the Week 1 operating procedure.
- `docs/RECOMMENDATION_SKELETON.md` -- the deliberately disabled
  recommendation/risk skeleton: the two independent locks (qualification
  disabled, no validated threshold artifact), evidence readiness,
  exposure grouping, and the safety tests proving zero actionable output.
- `docs/ROADMAP.md` -- milestones A-H, critical path, what's next.
