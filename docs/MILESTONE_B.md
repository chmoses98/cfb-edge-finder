# Milestone B — Real Schedule/Team Ingestion

This document covers what Milestone B adds on top of the Milestone A
foundation. It is data identity and schedule correctness, not predictions
-- no rating, projection, or recommendation logic is introduced here (see
`tests/test_no_recommendation_surface.py`, extended in this milestone to
also scan `ingestion`/`teams`/`data`).

## Data sources

### Primary: CollegeFootballData.com (CFBD) REST API v2

Chosen and verified as proposed in `docs/DATA_SOURCES.md` (Milestone A).
**This session's network egress to `api.collegefootballdata.com` and
`collegefootballdata.com` was blocked** (same restriction encountered in
Milestone A) -- two direct-fetch attempts failed with `EGRESS_BLOCKED`.
What follows was cross-checked via web search (a different, unblocked code
path) rather than a live API call, and is flagged accordingly:

- **Auth:** Bearer token via `CFBD_API_KEY`, required for every tier and
  every endpoint (confirmed via search of CFBD's own docs pages).
- **Pricing/limits:** free tier ~1,000 calls/month; academic tier and paid
  Patreon tiers exist above that. Not re-verified beyond the Milestone A
  research.
- **Terms:** commercial use permitted; **reselling/redistributing raw API
  data without permission is prohibited**; attribution is requested.
  Violation risks access revocation.
- **Endpoints used:** `/games` (season/week/team/venue/kickoff/status) and
  `/teams/fbs` (not yet called by the ingestion script -- team identity
  currently comes from the seed registry, see below).
- **What was NOT live-verified this session:** the exact raw field names
  (`homeTeam`, `neutralSite`, `startDate`, `startTimeTBD`, etc.),
  historical depth, and current rate-limit numbers. `cfbd_client.py` and
  `game_normalization.py` say so explicitly in their own docstrings and
  are built from CFBD's well-documented, community-standard v2 schema
  (the same one `cfbfastR`/`cfbd` Python clients target), not fabricated.

### Fallback: ESPN hidden/unofficial scoreboard API

Also not live-verified this session (same block). No authentication, no
ToS grant, no stability guarantee -- used only for cross-checking
(`cfb_edge_finder.ingestion.reconciliation.cross_check_secondary`), never
as the sole source of truth. `espn_client.py` exists and is unit-tested
(mocked HTTP), but the ingestion CLI does not call it by default in this
milestone -- wiring a live dual-source run is a natural next step once
either source's network access is actually available.

### If live credentials are unavailable (this session's actual situation)

Per the mission's own allowance: the CFBD client is fully implemented with
env-var configuration (`CFBD_API_KEY`, `cfb_edge_finder.config.Settings`)
and covered by deterministic mocked unit tests
(`tests/test_cfbd_client.py`) -- no live integration test exists, and none
of this milestone's code or docs claim one. `scripts/ingest_schedule.py`
auto-detects this: with no `CFBD_API_KEY` set, it falls back to a
deterministic fixture (`src/cfb_edge_finder/data/fixtures/cfbd_games_2026_sample.json`)
and prints an explicit notice that the run is not real data. **No live
2026 schedule data was fetched or is claimed anywhere in this repository.**

## Team registry

`src/cfb_edge_finder/teams/registry.py`. 134 FBS teams, seeded from
general knowledge -- **not live-verified**, and the module's own
docstring says so loudly, with the Pac-12 (rebuilt) conference membership
specifically flagged as highest-uncertainty (it was mid-transition as of
training data). `vendor_ids` is deliberately left empty for every seed
team: fabricating specific CFBD/ESPN numeric team IDs from memory would
be exactly the kind of unverified-but-authoritative-looking data this
project refuses to produce elsewhere (see
`kalshi/executable_price.py`'s fee-rate handling for the same principle).
Vendor IDs are meant to be populated by real ingestion runs observing
them, not hardcoded.

### Alias strategy

Exact-string-match only -- **no fuzzy/similarity matching anywhere**.
`resolve_team_alias()`:
1. Exact match against a registry `display_name` -> that team.
2. Exact match against `ALIASES` (a flat `{alias: team_id}` dict) -> the
   mapped team.
3. Exact match against `AMBIGUOUS_ALIASES` -> raises
   `AmbiguousTeamAliasError` with the candidate list. Bare `"Miami"` is
   the concrete case (Miami (FL) vs Miami (OH)).
4. Anything else -> raises `UnknownTeamAliasError`.

Both error types are caught per-game during ingestion (never per-batch)
and accumulated into the run summary as `unresolved_team_aliases`, so one
bad or unmapped team name never aborts an entire ingestion run -- see
`tests/test_teams_registry.py` for the full mission-listed case coverage
(Miami/Miami (OH), USC/South Carolina, UTSA, UCF, Ole Miss/Mississippi,
Louisiana/Louisiana-Lafayette, UConn, Hawai'i/Hawaii, directional
abbreviations).

## Week and postseason semantics

Two deliberately independent layers, not a redesign of Milestone A's
already-tested canonical ID format:

- **`week_label`** (unchanged format, `cfb_edge_finder.ids`): the stable,
  ID-safe slug used inside `game_id` -- `wkNN` / `bowl-<slug>` /
  `cfp-<slug>` / `conf-champ-<slug>`.
- **Structured fields on `GameRecord`** (new this milestone):
  `week_number: int | None`, `cfp_round: CFPRound | None`
  (`FIRST_ROUND`/`QUARTERFINAL`/`SEMIFINAL`/`NATIONAL_CHAMPIONSHIP`),
  `bowl_display_name: str | None` (human-readable, sponsor-name-volatile,
  kept OUT of the stable slug on purpose -- see Milestone A's documented
  bowl-naming risk in `docs/SCHEMAS.md`).

`cfb_edge_finder.ingestion.week_labels.derive_week_metadata()` computes
both from a source's raw `seasonType`/`week`/free-text descriptor. Week 0
works because the existing `wk\d{2}` slug pattern already allows `wk00`
(verified in Milestone A). Postseason classification is **heuristic**
(keyword matching on a free-text descriptor, since CFBD's raw schema does
not appear to expose a first-class "is this a CFP quarterfinal" boolean,
as far as could be determined without live verification) and **fails
loud** on anything unrecognized (`UnclassifiablePostseasonError`) rather
than guessing -- see `tests/test_week_labels.py`.

Army-Navy is not special-cased: it is an ordinary regular-season (or, in
some years, late-week) FBS-vs-FBS game like any other and needs no
distinct handling from this layer's point of view.

## Neutral-site handling

Unchanged from Milestone A's fix, exercised here against real-shaped
data: `canonical_game_id(..., neutral_site=True)` builds the ID from
alphabetically-sorted team slugs, invariant to which team a given vendor
happens to label "home." `tests/test_game_normalization.py::test_neutral_site_game_id_invariant_to_vendor_home_away_reversal`
and `tests/test_ingest_schedule_script.py`'s fixture run (Florida
State/Georgia Tech kickoff-classic-style game, SEC Championship, Rose
Bowl, and all three tested CFP rounds) exercise this against
CFP/bowl/conference-championship/kickoff-classic scenarios specifically,
per mission section 7's requested test coverage.

## Duplicate-source and reschedule reconciliation

`cfb_edge_finder.ingestion.reconciliation` -- three explicitly separate
operations (see that module's docstring for the full rationale):

1. **`merge_same_game_update`** -- an ordinary re-observation of the SAME
   `game_id` (e.g. a kickoff-time or venue update). Identity-bearing
   fields (season/week_label/home/away/neutral_site) are asserted
   unchanged (`IdentityMismatchError` if not); everything else updates.
2. **`detect_reschedule`** -- the SAME vendor game id observed under a
   DIFFERENT `game_id` than a prior artifact recorded (a true
   cross-week reschedule). Stamps `previous_game_id` for traceability.
   Exercised end-to-end in `tests/test_ingest_schedule_script.py::test_reschedule_detected_across_two_runs`
   against a real two-run artifact, distinguished from an ordinary
   kickoff-time-only update
   (`test_ordinary_kickoff_update_does_not_change_game_id_or_add_previous_game_id`)
   specifically to prove identity doesn't churn on routine updates.
3. **`cross_check_secondary`** -- compares a secondary source's raw
   observation against an already-normalized primary `GameRecord` for the
   same physical game (matched via `find_match`: season + both teams as
   an unordered pair, never "teams match" alone). Missing fields
   (currently `venue`) get filled in explicitly; disagreements produce a
   `ConflictRecord` and are **never silently overwritten** -- resolution
   stays `None` (unresolved) until a human or a later milestone decides.

Postponed/canceled games use `GameRecord.status`'s existing
`"postponed"`/`"canceled"` values (Milestone A); a postponement that
moves a game to a different week is a `detect_reschedule` case, not a
`status` case.

## Storage

`scripts/ingest_schedule.py` writes a compact canonical artifact to
`data/schedules/{season}.json` (one JSON file: manifest header + sorted
list of `GameRecord`s). **This directory is gitignored**
(`data/schedules/`, `data/ingestion_reports/`) rather than committed by
default in this milestone: a fixture-mode run's output is synthetic test
data, and committing it would misleadingly look like a real ingested
2026 schedule sitting in the repository. A genuine live-fetched artifact,
once CFBD_API_KEY is configured and a real run has been verified, is a
reasonable thing to commit explicitly (`git add -f`) as exactly the kind
of "compact canonical artifact" `docs/STORAGE_STRATEGY.md` already
describes as git-appropriate -- raw API responses themselves still never
belong in git (`data/raw/`, already excluded in Milestone A).

## Known limitations / unresolved risks (carry into Milestone C planning)

1. **No live verification was possible this session** for CFBD's exact
   schema, ESPN's exact schema, or current CFBD pricing/rate limits --
   confirm all three directly before any production ingestion run.
2. **Team registry conference assignments are best-effort**, not
   live-verified; the rebuilt Pac-12's membership is the specific
   highest-risk entry. Reconcile against a live `/teams/fbs` fetch before
   trusting conference-based logic (e.g. a future conference-championship
   detector keyed off actual divisional standings).
3. **Postseason classification is a heuristic on free text**, not a
   guaranteed-correct parse of a structured field CFBD may or may not
   actually expose -- verify against real API responses and widen/adjust
   `_CFP_ROUND_KEYWORDS`/`_KNOWN_CONFERENCES_FOR_CHAMPIONSHIP` in
   `week_labels.py` as real descriptors are observed.
4. **Bowl sponsor-name volatility remains unsolved by design** (documented
   already in Milestone A) -- `bowl_display_name` is separated from the
   ID-safe slug specifically so this doesn't corrupt identity, but the
   slug itself can still differ year to year for the same physical bowl.
5. **ESPN cross-check is implemented and unit-tested but not wired into
   the default CLI run** -- `cross_check_secondary`/`find_match` exist and
   work, but `scripts/ingest_schedule.py` doesn't call the ESPN client by
   default yet. A reasonable near-term follow-up, not required for this
   milestone's "trustworthy game universe" bar since CFBD alone already
   produces validated, deterministic, provenance-tagged records.
6. **Vendor IDs beyond `cfbd` are not populated** in the team registry
   seed -- `TeamRecord.vendor_ids` stays empty until a real ingestion run
   (or an ESPN cross-check pass) observes and records them.
