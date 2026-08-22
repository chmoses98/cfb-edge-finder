# Canonical Schemas

All schemas are pydantic v2 models (or, for lighter-weight registries, plain
dataclasses) under `src/cfb_edge_finder/schemas/` and
`src/cfb_edge_finder/data/sources.py`. This document summarizes intent and
rationale; the code is the source of truth for exact fields.

## Canonical game ID

`cfb_edge_finder.ids.canonical_game_id(season, week_label, away_slug, home_slug)`
produces `cfb-{season}-{week_label}-{away_slug}-at-{home_slug}`.

**Why not a vendor ID (e.g. a CFBD numeric id)?** Using a vendor ID as the
canonical identity would lock the whole system to that vendor's ID scheme
and break if the vendor changes IDs or is ever swapped out. Vendor IDs are
still tracked, just not as identity -- `GameRecord.source_game_ids` is a
`{vendor: id}` map for cross-referencing.

**Why exclude kickoff time?** Kickoff time moves (flex scheduling, weather
delays). A stable ID must not change when that happens, so only inputs that
are fixed at schedule-publish time (season, week label, both team slugs)
feed the ID.

**Team slugs**, not raw names: `slugify_team()` normalizes accents/case/
punctuation so "Ohio State" from one source and "Ohio St." from another
resolve to the same slug -- source name variance is a known, common failure
mode this sidesteps.

**Week label vocabulary** is closed and validated
(`wk01`..`wk15` | `bowl-<slug>` | `cfp-<slug>` | `conf-champ-<slug>` |
`allstar-<slug>`), enforced by `validate_week_label()`.

**Collision policy:** two source records producing the same canonical ID
within a season is treated as a data-quality failure at ingestion time
(fail loud), never silently overwritten. FBS teams essentially never play
each other twice in the same labeled week, so this is expected to be rare,
but the system does not assume it is impossible.

## Provenance / version scheme

`ModelVersion` (semver `model_version`, separate
`ratings_component_version` and `pricing_engine_version`, optional
`git_commit_sha`) and `DataProvenance` (ratings/roster/injury snapshot
versions, `schedule_source`, `data_timestamp`, open-ended
`completeness_flags: dict[str, bool]`) are embedded directly into
`ProjectionRecord` and `ProspectiveSnapshot` -- not referenced by ID.

This is a deliberate reaction to a documented gap found in the MLB audit:
edge-finder-api's `CANONICAL_SCHEMAS.md` flags, as an unresolved issue,
that almost nothing in that pipeline actually carries
`modelVersion`/`calibrationVersion`/`pipelineRunId`. Baking both into every
projection and snapshot from day one avoids retrofitting that gap later.

## Projection record

`GameDistribution`: `home_mean`, `away_mean`, `home_sd` (>0), `away_sd`
(>0), `correlation` (in [-1, 1], default 0.0). A coherent, correlated
bivariate score projection -- the single artifact that prices every
downstream market. See `docs/ARCHITECTURE.md` section 5 for the modeling
assumptions behind this specific parametric form.

`UncertaintyProfile`: `data_completeness` (0-1), `qb_status_confirmed`
(bool), `early_season_prior_weight` (0-1), free-text `notes`. First-class,
not folded into `GameDistribution`'s variance -- see mission section 7 and
`docs/ARCHITECTURE.md` section 5.

`ProjectionRecord`: `projection_id`, `game_id`, `model_version`,
`provenance`, `projection_timestamp`, `distribution`, `uncertainty`.
Validates `provenance.data_timestamp <= projection_timestamp`.

## Market record

`MarketRecord`: `market_ticker` (Kalshi's own ticker, the external key),
`event_ticker`, `series_ticker`, `game_id` (nullable until mapped),
`market_family`, `line`, `side`, `discovered_at`, `last_seen_at`,
`status`, `status_reason`.

Exists for every discovered ticker, including ones that end up `REJECTED`
or `UNSUPPORTED_MARKET` -- markets are archived even when never
recommended (mission section 1.5).

`MarketFamily` (closed): `moneyline`, `spread`, `alt_spread`, `total`,
`alt_total`, `team_total`, `first_half_moneyline`, `first_half_spread`,
`first_half_total`, `other`. `Side` (closed): `home`, `away`, `over`,
`under`, `yes`, `no`.

## Coverage ledger

`MarketStatus` (closed, see `docs/ARCHITECTURE.md` section 4 for the full
list and terminal/non-terminal split). `StatusTransition`: `status`, `at`,
`reason`. `CoverageLedgerEntry`: `market_ticker`, `game_id`,
`current_status`, append-only `history: list[StatusTransition]` -- schema
validation enforces `current_status == history[-1].status` and non-empty
history.

`CoverageLedger` (`kalshi/coverage_ledger.py`) is the operational layer:
`record_discovered()`, `transition()`, `summary()` (counts per status),
and `assert_no_missing(discovered_tickers)` -- the invariant check that
catches a market silently falling out of the pipeline before it ever
reaches the ledger. See `docs/ARCHITECTURE.md` section 4.

## Prospective snapshot

`ProspectiveSnapshot`: `snapshot_id`, `sport` (`"cfb"`), `game_id`,
`model_version`, `projection_timestamp`, `data_timestamp`, `provenance`,
`market_snapshot_id`, `market_ticker`, `market_family`,
`fair_probability` (0-1), `executable_price` (0-1, nullable),
`uncertainty`, `captured_at`.

Embeds full `ModelVersion`/`DataProvenance` rather than referencing them
by ID, so a snapshot remains self-describing even if the versioned records
it points to are later pruned from hot storage (see
`docs/STORAGE_STRATEGY.md`). This directly answers mission section 8:
"what did the model know at the time it made this estimate?"

## Schema-versioning convention

Every schema module lives under `src/cfb_edge_finder/schemas/` and is
covered by `tests/test_schemas.py` for validation and deterministic
serialization. Because Kalshi-facing records embed `ModelVersion` inline,
a breaking schema change is a code change (new pydantic model fields) plus
a bump to `ModelVersion.pricing_engine_version` and/or the package's own
`__version__` in `src/cfb_edge_finder/__init__.py` -- not a silent
reinterpretation of old records. A formal migration tool is not built in
this foundation phase; it is not yet needed because no persisted data
exists yet to migrate.
