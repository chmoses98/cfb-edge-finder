# CFB Edge Finder — Architecture (Foundation Phase)

This document describes the target architecture and the reasoning behind
it. It is deliberately a foundation, not a finished system -- see
`docs/ROADMAP.md` for what's built now vs later, and
`docs/MLB_ARCHITECTURE_AUDIT.md` for which patterns were carried over from
`edge-finder-api` and why.

## 1. Why this shape

The mission constraint that drives every decision here: a normal CFB week
may have 50-80+ games and thousands to tens of thousands of Kalshi
contracts. The system must NOT run a heavyweight football simulation
separately for every contract. The required pattern is:

```
GAME INPUTS
  -> GAME-LEVEL PROJECTION (a rating system, not yet built -- Milestone C)
  -> SCORE / MARGIN / TOTAL DISTRIBUTION  (GameDistribution, built)
  -> MANY MARKET PROBABILITIES            (projections/distribution.py, built)
```

One `GameDistribution` (5 numbers: home_mean, away_mean, home_sd, away_sd,
correlation) prices moneyline, spread, every alt-spread line, total, every
alt-total line, and team totals -- all as cheap closed-form functions, with
zero re-simulation per contract. This is implemented and tested today in
`src/cfb_edge_finder/projections/distribution.py`; see
`tests/test_distribution.py::test_one_distribution_prices_the_full_auburn_baylor_style_market_set`
for the exact example from the mission brief.

A Kalshi price update should trigger a cheap re-read of an existing
distribution's derived probabilities, never a football-model rerun --
unless the underlying game inputs (ratings, injuries, weather) actually
changed. The football engine (whatever eventually populates
`GameDistribution`) and the Kalshi pricing engine are separate modules on
purpose (`ratings/`+`projections/` vs `kalshi/`), so this separation is
structural, not just a convention.

## 2. Component diagram

```
                      ┌──────────────────────┐
                      │   data sources        │  CFBD, ESPN, NWS/Visual
                      │  (Milestone B)        │  Crossing, Kalshi -- see
                      └──────────┬────────────┘  docs/DATA_SOURCES.md
                                 │
                                 v
                      ┌──────────────────────┐
                      │   GameRecord           │  canonical game_id,
                      │  (schemas/game.py)     │  season/week/teams
                      └──────────┬────────────┘
                                 │
                                 v
                      ┌──────────────────────┐
                      │   ratings/             │  opponent-adjusted O/D,
                      │  (Milestone C,          │  QB value, continuity,
                      │   not yet built)        │  home-field
                      └──────────┬────────────┘
                                 │
                                 v
                      ┌──────────────────────┐
                      │  GameDistribution       │  home/away mean+sd,
                      │  (schemas/projection.py,│  correlation
                      │   BUILT)                │
                      └──────────┬────────────┘
                                 │
                    ┌────────────┴─────────────┐
                    v                            v
        ┌──────────────────────┐    ┌──────────────────────────┐
        │ projections/            │    │  kalshi/ market discovery │
        │ distribution.py (BUILT) │    │  (Milestone E)             │
        │ price_market(family,     │    │  broad sweep -> allowlist  │
        │ side, line) -> fair prob │    │  classify -> parse ticker  │
        └──────────┬────────────┘    └──────────────┬─────────────┘
                    │                                 │
                    └──────────────┬──────────────────┘
                                    v
                      ┌──────────────────────┐
                      │  MarketRecord +         │  every discovered ticker,
                      │  CoverageLedger          │  including rejected/
                      │  (schemas/market.py,     │  unsupported ones --
                      │   kalshi/coverage_ledger,│  see section 4
                      │   BUILT)                 │
                      └──────────┬────────────┘
                                 │
                                 v
                      ┌──────────────────────┐
                      │  executable_price /      │  fee-aware net EV
                      │  net_executable_edge      │  (placeholder math,
                      │  (Milestone G)            │  UNVERIFIED fee const)
                      └──────────┬────────────┘
                                 │
                                 v
                      ┌──────────────────────┐
                      │  ProspectiveSnapshot     │  what the model knew,
                      │  (schemas/snapshot.py,   │  before kickoff
                      │   BUILT; capture          │
                      │   pipeline Milestone F)   │
                      └──────────┬────────────┘
                                 │
                                 v
                      ┌──────────────────────┐
                      │  betting/ recommendation │  NOT built -- explicitly
                      │  layer (Milestone H)      │  out of scope this phase
                      └──────────────────────┘
```

## 3. Game/projection flow

1. A schedule source (CFBD primary) produces raw game rows.
2. Each row is normalized into a `GameRecord` keyed by the canonical
   `game_id` (`cfb-{season}-{week_label}-{away_slug}-at-{home_slug}`,
   built via `cfb_edge_finder.ids.canonical_game_id` -- see
   `docs/SCHEMAS.md` for the exact rationale).
3. Team ratings (Milestone C, not built) combine opponent-adjusted
   offense/defense, QB value, roster continuity, coaching/system
   adjustments, and home-field advantage into a `GameDistribution`.
4. `GameDistribution` is wrapped in a `ProjectionRecord` with a
   `ModelVersion` and `DataProvenance`, so every projection is
   reconstructable: what code produced it, and what it knew at the time.
5. `projections.distribution.price_market()` derives any number of market
   probabilities from step 4's single distribution.

## 4. Kalshi flow and the coverage ledger

Per mission section 4, every discovered Kalshi market must terminate in a
known state -- silent omission is forbidden. `MarketStatus`
(`schemas/common.py`) is the closed vocabulary:

- Non-terminal: `DISCOVERED`, `TICKER_UNRESOLVED`, `MAPPED`, `WATCH`,
  `EARLY_VALUE`
- Terminal: `MISSING_INPUT`, `EVALUATION_FAILED`, `UNSUPPORTED_MARKET`,
  `GAME_STARTED`, `REJECTED`, `ACCEPTED`

`CoverageLedger` (`kalshi/coverage_ledger.py`) enforces two invariants,
directly reproducing edge-finder-api's dual-denominator coverage pattern
(see `docs/MLB_ARCHITECTURE_AUDIT.md` section 2):

- Every `CoverageLedgerEntry.current_status` must equal the last entry in
  its own `history` (schema-level, enforced by pydantic).
- `CoverageLedger.assert_no_missing(discovered_tickers)` takes a set of
  tickers derived **independently** of the ledger's own
  `record_discovered()` calls (e.g. straight from a raw Kalshi sweep
  response) and raises `CoverageInvariantError` if any are absent --
  catching a bug that drops a market before it ever reaches the ledger,
  not just one that mishandles it afterward.

This means the system can prove, at any point, the exact breakdown of
"games discovered / contracts discovered / contracts mapped / contracts
evaluated / contracts unresolved / contracts unsupported / qualified tiers
/ rejected count" that mission section 4 requires -- `CoverageLedger.summary()`
returns counts per status today.

## 5. Uncertainty and modeling assumptions (mission section 7)

`GameDistribution` treats each team's score as approximately Normal, and
derives margin/total analytically from the two marginals and their
correlation (default `0.0`, an explicit placeholder, not an empirical
finding). This is a deliberately simple parametric form chosen specifically
so many market probabilities can be derived cheaply from one small set of
numbers -- see the extensive docstring in
`src/cfb_edge_finder/projections/distribution.py` for the exact
assumptions, including:

- A 0.5-point continuity correction for evaluating `P(X > threshold)` on
  what's really an integer-valued score.
- Push probability is not separately modeled -- it's the small gap left by
  the continuity correction, which is why `tests/test_distribution.py`
  checks cover/total complementarity with a tolerance, not exact equality.
- First-half markets are explicitly unsupported by this module
  (`UnsupportedMarketFamilyError`) because they need a separate first-half
  `GameDistribution`, not a decomposition of the full-game one.

Uncertainty is first-class, not folded into generic variance:
`UncertaintyProfile` (`schemas/projection.py`) carries `data_completeness`,
`qb_status_confirmed`, and `early_season_prior_weight` alongside every
projection. This is deliberately NOT wired into a ranking formula yet --
mission section 7's "net edge x confidence x completeness x calibration x
market quality x uncertainty penalty" formula is a Milestone H concern, and
building it before there is real backtest data to calibrate its weights
would just be guessing thresholds, which the mission explicitly says not
to do.

## 6. Storage

See `docs/STORAGE_STRATEGY.md` for the full reasoning. Summary: source
code, schemas, tests, docs, and compact canonical artifacts live in git.
Raw high-frequency captures, large historical datasets, and repeated
snapshots do not -- `data/` in this repo is deliberately near-empty at
this phase (see `data/README.md`).

## 7. What's deliberately not here yet

Per mission section 11 and `docs/ROADMAP.md`: no player props, no
automated bet placement, no bankroll management, no large ML model, no
NFL support, no shared cross-sport package, no production staking
thresholds, no dashboard. `ratings/`, `betting/`, and `research/` exist as
empty-but-documented package stubs specifically so later milestones have
an obvious home rather than requiring a restructure -- they are not
placeholders for hidden complexity.
