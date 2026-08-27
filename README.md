# cfb-edge-finder

Foundation for a quantitative college-football market-pricing and
betting-research system, targeting Kalshi's CFB markets. Milestone A
(architecture/schemas) and Milestone B (real schedule/team ingestion) are
done -- **no projection model or betting recommendation logic exists
yet.** See `docs/ROADMAP.md` for what comes next.

This is a separate, from-scratch repository. It reuses architectural
*patterns* audited from the production MLB system at
`chmoses98/edge-finder-api` (see `docs/MLB_ARCHITECTURE_AUDIT.md`), but no
code or MLB-specific logic was copied, and that repository was not
modified to produce this one.

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
- `docs/PROSPECTIVE_COLLECTION.md` -- the scheduled collection regime:
  checkpoint schedule, closing definition and completeness accounting,
  cadence, concurrency, persistence, health checks, reschedules.
- `docs/PERFORMANCE.md` -- scanner performance: the per-ticker history
  re-read bottleneck, the one-load-per-run fix, output-equivalence proof,
  scale benchmarks, concurrency behaviour, remaining bottlenecks.
- `docs/ROADMAP.md` -- milestones A-H, critical path, what's next.
