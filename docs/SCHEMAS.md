# Canonical Schemas

All schemas are pydantic v2 models (or, for lighter-weight registries, plain
dataclasses) under `src/cfb_edge_finder/schemas/` and
`src/cfb_edge_finder/data/sources.py`. This document summarizes intent and
rationale; the code is the source of truth for exact fields.

## Canonical game ID

`cfb_edge_finder.ids.canonical_game_id(season, week_label, away_slug, home_slug, neutral_site=False)`
produces `cfb-{season}-{week_label}-{away_slug}-at-{home_slug}` for a
site-based game, or `cfb-{season}-{week_label}-{team_a}-vs-{team_b}`
(alphabetically sorted) when `neutral_site=True`.

**Why does neutral-site use a different, order-invariant format?**
"Home"/"away" is bookkeeping-only for a neutral-site game, and different
vendors are known to disagree about which team they label home for exactly
these games. Building the ID from away-at-home order the way site-based
games work would let the same physical game fork into two different
canonical IDs depending on which vendor's designation was ingested first
-- a real collision-safety failure caught during a pre-merge audit.
Sorting the two team slugs makes the ID depend only on the *set* of teams,
never on a vendor's arbitrary designation. True home-field-advantage
modeling is unaffected -- it still reads `GameRecord.home_team_id` /
`neutral_site` directly, routed through
`cfb_edge_finder.ratings.home_field_advantage_points()`, which returns
`0.0` unconditionally for a neutral-site game.

**Known, deliberately undertaken risk (not "fixed" here):** bowl-game week
labels are often sponsor-branded and can change name year to year or even
mid-season; two vendors could plausibly disagree on a bowl's current
sponsor name within one season, which would fork the ID the same way the
home/away case did. This needs a stable, non-sponsor bowl-identity mapping
in Milestone B's ingestion layer (e.g. keyed by host city/stadium), not a
change to `canonical_game_id()` itself, which correctly builds an ID from
whatever `week_label` it's given.

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
but the system does not assume it is impossible --
`cfb_edge_finder.ids.assert_unique_game_ids()` is the ready-made check a
future ingestion step should run against its own produced IDs.

**Rescheduled games:** kickoff time is excluded from the ID by design (see
above), so a same-week reschedule never changes the ID. If a postponement
moves a game to a genuinely different `week_label`, the ID *does* change
-- `GameRecord.previous_game_id` exists to preserve traceability from the
new ID back to the old one, so a `ProspectiveSnapshot` captured before the
move remains reachable.

**Postponed/canceled games:** `GameRecord.status` includes `"postponed"`
and `"canceled"` as first-class values (not inferred from absence), so a
game doesn't just quietly stop appearing in a feed.

## Game record (Milestone B additions)

Beyond the identity fields above, `GameRecord` carries: `week_number`
(structured regular-season integer, independent of the `week_label`
slug), `cfp_round` (`CFPRound`: `first_round`/`quarterfinal`/`semifinal`/
`national_championship`, only meaningful when `season_type` is `cfp`),
`bowl_display_name` (human-readable, possibly sponsor-branded, kept
separate from the stable slug specifically because sponsor names change
-- see "Canonical game ID" above), `kickoff_source_raw` (the as-received
kickoff string before UTC normalization, for auditability), and
`primary_source` (which vendor's designation is currently authoritative
for this record). See `docs/MILESTONE_B.md` "Week and postseason
semantics" for the full rationale on why these are separate,
non-ID-affecting fields rather than a change to the ID format itself.

## Team registry

`cfb_edge_finder.teams.registry.TeamRecord`: `team_id` (canonical slug,
never a vendor ID), `display_name`, `conference` (best-effort, not
live-verified -- see `docs/MILESTONE_B.md`), `subdivision`,
`primary_vendor`/`vendor_ids` (deliberately empty in the seed data --
never fabricated from memory, populated only by real ingestion
observation), `active`, `season_start`/`season_end` (for renamed
programs). `resolve_team_alias()` is exact-string-match only, with a
separate `AMBIGUOUS_ALIASES` table that fails loud
(`AmbiguousTeamAliasError`) rather than guessing -- see
`docs/MILESTONE_B.md` "Team registry" for the full alias strategy.

## Source observations and conflict records

`RawGameObservation` (`schemas/observation.py`): exactly what one vendor
reported for one game, before team-alias resolution or any other
normalization -- kept only long enough to detect and report disagreement,
never merged silently into a `GameRecord`. `ConflictRecord`/
`FieldConflict`: one unresolved disagreement between two or more sources
for what is believed to be the same physical game, produced by
`cfb_edge_finder.ingestion.reconciliation.cross_check_secondary` and
never auto-resolved by picking one source arbitrarily -- `resolution`
stays `None` until something (a human, a later milestone) decides. See
`docs/MILESTONE_B.md` "Duplicate-source and reschedule reconciliation."

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

`GameDistribution`: `home_mean` (>=0), `away_mean` (>=0), `home_sd` (>0),
`away_sd` (>0), `correlation` (in [-1, 1], default 0.0), all validated
finite (`math.isfinite`; NaN/+-inf rejected even where a bare `gt`/`ge`
bound wouldn't catch them -- e.g. `home_sd=inf` passes `gt=0` but fails
the explicit finiteness check). A coherent, correlated bivariate score
projection -- the single artifact that prices every downstream market.
Marked **PROVISIONAL / RESEARCH-ONLY** in its own docstring: not a
validated betting model, and no recommendation/staking logic may be
derived from it (mechanically checked by
`tests/test_no_recommendation_surface.py`). See `docs/ARCHITECTURE.md`
section 5 for the full modeling assumptions behind this parametric form.

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
`market_family`, `line`, `side`, `team` (nullable; see below),
`discovered_at`, `last_seen_at`, `coverage_outcome`, `coverage_reason`,
`recommendation_readiness` (nullable; see Coverage ledger below).

Exists for every discovered ticker, including ones that end up
`UNSUPPORTED_MARKET` or `PASS` -- markets are archived even when never
recommended (mission section 1.5).

`MarketFamily` (closed): `moneyline`, `spread`, `alt_spread`, `total`,
`alt_total`, `team_total`, `first_half_moneyline`, `first_half_spread`,
`first_half_total`, `other`. `Side` (closed): `home`, `away`, `over`,
`under`, `yes`, `no`.

`team` (`Side.HOME`/`Side.AWAY`) is required for, and only meaningful for,
`MarketFamily.TEAM_TOTAL` -- validated as orthogonal to `side`
(`Side.OVER`/`Side.UNDER`), matching
`cfb_edge_finder.projections.distribution.price_market()`'s dimensional
model exactly (that module's docstring explains why team identity is
never encoded into `side` itself). A team-total market needs both "which
team" and "over or under" as independent facts; conflating them into one
`Side` value would make roughly half the enum's meaning context-dependent.

## Coverage ledger

Two orthogonal axes -- see `docs/ARCHITECTURE.md` section 4 for the full
rationale for why they're split rather than one flat enum:

- `CoverageOutcome` (closed): pipeline mechanics only --
  `DISCOVERED`/`MAPPED` (non-terminal), `EVALUATED`/`TICKER_UNRESOLVED`/
  `MISSING_INPUT`/`EVALUATION_FAILED`/`UNSUPPORTED_MARKET`/`GAME_STARTED`
  (terminal).
- `RecommendationReadiness` (closed): `PASS`/`WATCH`/`EARLY_VALUE`/
  `ACTIONABLE` -- only ever set once `CoverageOutcome` is `EVALUATED`
  (enforced by validation on both `MarketRecord` and `CoverageLedgerEntry`).

`StatusTransition`: `outcome`, `at`, `reason`. `CoverageLedgerEntry`:
`market_ticker`, `game_id`, `current_outcome`, append-only
`history: list[StatusTransition]`, `recommendation_readiness` -- schema
validation enforces `current_outcome == history[-1].outcome`, non-empty
history, and `recommendation_readiness is None` whenever `current_outcome`
isn't `EVALUATED`.

`CoverageLedger` (`kalshi/coverage_ledger.py`) is the operational layer:
`record_discovered()`, `transition()` (moves `CoverageOutcome` through its
audit trail), `set_recommendation_readiness()` (sets the orthogonal,
non-audit-trailed readiness value -- moving `CoverageOutcome` off
`EVALUATED` clears it), `summary()` (counts per `CoverageOutcome`),
`readiness_summary()` (counts per `RecommendationReadiness`, including
`None`), and `assert_no_missing(discovered_tickers)` -- the invariant
check that catches a market silently falling out of the pipeline before
it ever reaches the ledger. Both mutation methods reconstruct entries
through `CoverageLedgerEntry`'s full constructor rather than
`model_copy(update=...)`, because pydantic v2 does not re-run validators
on that path -- using it would have silently defeated the
readiness-requires-EVALUATED invariant. See `docs/ARCHITECTURE.md` section 4.

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

## Timestamps

Every persisted timestamp field (`GameRecord.kickoff_utc`/`discovered_at`/
`last_updated_at`, `ProjectionRecord.projection_timestamp`,
`DataProvenance.data_timestamp`, `ProspectiveSnapshot`'s timestamp fields,
`StatusTransition.at`) is `pydantic.AwareDatetime`, not plain `datetime`.
A naive datetime (no tzinfo) is rejected at validation time rather than
silently accepted and later misinterpreted as local time somewhere
downstream -- `tests/test_schemas.py` has explicit naive-datetime-rejection
tests for the game and projection records.

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
