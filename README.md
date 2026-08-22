# cfb-edge-finder

Foundation for a quantitative college-football market-pricing and
betting-research system, targeting Kalshi's CFB markets. This is the
architecture/foundation phase only -- **no complete betting model exists
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
  known `MarketStatus`, and `assert_no_missing()` catches markets that
  would otherwise silently vanish from the pipeline.
- `src/cfb_edge_finder/kalshi/executable_price.py` -- the *shape* of a
  fee-aware net-EV calculation. The fee constant is an explicit,
  documented placeholder -- not verified production math yet.
- `src/cfb_edge_finder/data/sources.py` -- machine-readable data-source
  registry, companion to `docs/DATA_SOURCES.md`.
- `docs/` -- architecture, schema rationale, storage strategy, the MLB
  audit, data-source research, and the milestone roadmap.

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

Copy `.env.example` to `.env` and fill in real credentials before any
Milestone B ingestion work begins (nothing in this phase requires it).

## Documentation map

- `docs/ARCHITECTURE.md` -- component diagram, game/projection flow,
  Kalshi flow, coverage-ledger design, uncertainty approach.
- `docs/SCHEMAS.md` -- canonical game ID, and every schema's rationale.
- `docs/DATA_SOURCES.md` -- researched CFB data sources, costs, auth,
  fallbacks, and unresolved risks.
- `docs/STORAGE_STRATEGY.md` -- what stays in git vs. external storage,
  and why.
- `docs/MLB_ARCHITECTURE_AUDIT.md` -- what was reused from edge-finder-api
  and what was deliberately left behind.
- `docs/ROADMAP.md` -- milestones A-H, critical path, what's next.
