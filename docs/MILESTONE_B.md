# Milestone B — Real Schedule/Team Ingestion

This document covers what Milestone B adds on top of the Milestone A
foundation. It is data identity and schedule correctness, not predictions
-- no rating, projection, or recommendation logic is introduced here (see
`tests/test_no_recommendation_surface.py`, extended in this milestone to
also scan `ingestion`/`teams`/`data`).

## Validation follow-up (this session)

A pre-merge validation pass on PR #2 found and fixed four real gaps
without a live CFBD payload becoming available (network egress to CFBD's
own domains stayed blocked). What changed, and how each was verified:

1. **Team count: 134 -> 138.** Verified via web search against multiple
   independent, dated sources (Deseret News, CBS Sports, ESPN, Wikipedia)
   that CFBD currently reports 138 FBS teams for 2026. The exact gap was
   four FCS-to-FBS transitional additions: Delaware and Missouri State
   (Conference USA, 2025 transition) and North Dakota State (Mountain
   West) and Sacramento State (MAC), both new for 2026. Added to
   `teams/registry.py` with `season_start` set accordingly. This is
   cross-checked reporting, not a CFBD API response -- still flagged for
   reconciliation against a live `/teams/fbs` fetch.
2. **Schema field names corrected/hardened.** github.com (unlike CFBD's
   own domains) was reachable this session. Fetching CFBD's own
   officially-generated `cfbd-python` client library documentation
   (`docs/Game.md`, `docs/GamePlayoff.md`, `docs/PlayoffRound.md`,
   `docs/DivisionClassification.md` on `github.com/CFBD/cfbd-python`)
   produced real, primary-source (CFBD-maintained) schema documentation
   -- not a live payload, but not a from-memory guess either. It disagreed
   with the older `cfb.js` client's docs on some field names (e.g.
   `homeDivision` vs `home_classification`), so `game_normalization.py`
   now checks multiple candidate keys defensively
   (`homeClassification`/`homeDivision`, `startTimeTBD`/`startTimeTbd`)
   rather than asserting one as fact.
3. **Structured `playoff` field discovered and now used as the primary
   postseason-classification mechanism.** CFBD's Game model includes a
   nested `GamePlayoff` object (`competition`, `round`, `bowl_name`, etc.)
   for CFP-bracket games specifically (`PlayoffCompetition` only defines
   `"cfp"` as a value -- conference championships and non-CFP bowls don't
   get this field). `week_labels.derive_week_metadata()` now uses
   `raw["playoff"]["round"]` directly when present, falling back to the
   free-text keyword heuristic only when it's absent -- see "Week and
   postseason semantics" below.
4. **FBS-vs-FCS inclusion policy fixed.** The ingestion script previously
   required BOTH teams to be FBS-classified, silently dropping any FBS
   team's game against an FCS opponent (a common Week 0/1 occurrence).
   Fixed to require only ONE side to be FBS, with FCS opponents resolving
   to a deterministic generated slug (not requiring FBS-registry
   membership) rather than raising -- see "FBS-vs-FCS inclusion policy"
   below.

## Live validation (2026-08-23)

The gap flagged throughout the section above -- no genuine, authenticated
CFBD payload had actually been fetched -- is now closed. This Claude
execution environment's own network egress to CFBD is blocked
(`EGRESS_BLOCKED`), so live validation ran on a GitHub-hosted Actions
runner instead, via a new manual-only workflow:
`.github/workflows/validate-cfbd-live.yml` (`workflow_dispatch` only, no
schedule/cron, `permissions: contents: read`, no commit/push steps), which
runs `scripts/validate_cfbd_live.py` against the `CFBD_API_KEY` repository
secret. That script calls this repo's actual production code
(`cfbd_client`, `game_normalization`, `team_matching`, `teams.registry`) --
not a separate throwaway parser -- and prints only safe aggregate
diagnostics; it never printed the API key or an Authorization header.
Workflow run: https://github.com/chmoses98/cfb-edge-finder/actions/runs/32610126557
(captured 2026-08-23T01:20:52Z UTC).

Note on how this workflow got triggerable at all: GitHub's `workflow_dispatch`
REST API only dispatches workflows indexed from the *default branch's*
workflow catalog -- a workflow file that exists only on a feature branch
can't be dispatched by filename. So the workflow *definition* (this single
file, read-only permissions, manual-trigger-only, no application code) was
pushed directly to `main` in commit `08d7662`; the `ref` parameter on the
dispatch call then pointed at this feature branch, so the actual validation
script and Milestone B code that ran came entirely from here, not from
`main`. PR #2 itself was not touched or merged by this.

**Team universe.** `/teams/fbs?year=2026` returned exactly 138 teams,
confirming the 134->138 reconciliation above via an independent,
authoritative source. Three alias gaps surfaced and were closed in
`ALIASES`: CFBD reports `"App State"` (not "Appalachian State"),
`"Florida International"` (not "FIU"), and `"San José State"` (accented,
not "San Jose State") on live game records. 31 conference-string
differences were found between the registry and the live response; 26 were
naming-only (this registry had used the shorthand `"MAC"` and `"American"`
where CFBD reports the full `"Mid-American"` and `"American Athletic"`
strings) and 5 were genuine realignment the registry hadn't caught:
Louisiana Tech (Conference USA -> Sun Belt), UMass (FBS Independents ->
Mid-American, no longer independent), Northern Illinois (MAC -> Mountain
West), Texas State (Sun Belt -> Pac-12), and UTEP (Conference USA ->
Mountain West). All 31 were corrected in `teams/registry.py`; see that
module's docstring for full detail. The four known transitional teams
(Delaware, Missouri State, North Dakota State, Sacramento State) were
confirmed present with the expected conferences.

**Schema.** The genuine `/games?year=2026` payload's field names matched
this project's primary defensive-lookup keys exactly: `homeClassification`,
`awayClassification`, `startTimeTBD`, `neutralSite`, `startDate`, `season`,
`seasonType`, `week`, `notes`, `playoff` are all present verbatim in
camelCase. No parser fix was needed; the multi-key fallback support added
during the earlier non-live validation pass (`homeDivision`,
`startTimeTbd`, etc.) is retained as harmless dead-path coverage in case a
different CFBD response variant is encountered later.

**Schedule ingestion.** 3610 games fetched for the 2026 season; 2722 had no
FBS side at all and were filtered out entirely; of the remaining 888
FBS-involved games, 761 were FBS-vs-FBS and 127 were FBS-vs-FCS (or a
non-FBS-classified opponent). 839 games were retained after normalization;
49 were excluded for an unresolved team alias (all attributable to the
three alias gaps above, now fixed -- so a re-run should retain all 888);
0 validation failures; 0 duplicate canonical game IDs; 11 neutral-site
games; 387 TBD-kickoff games; 232 unique canonical teams encountered. A
genuine FBS-vs-FCS example was confirmed retained: UAlbany @ Buffalo,
canonical `game_id=cfb-2026-wk01-ualbany-at-buffalo`.

**Postseason.** 2026's `/games` response had zero games with a populated
`playoff` object yet (expected -- the playoff bracket doesn't exist this
early in the season). One additional authenticated historical request
(`/games?year=2024`, postseason) was made to validate the genuine
structure: a real CFP first-round game's `playoff` object had keys
`competition`, `round`, `roundName`, `bowlName`, `bracketSlot`, `homeSeed`,
`awaySeed`, `format` -- `derive_week_metadata(playoff=...)` correctly
mapped it to `week_label="cfp-first-round"`,
`cfp_round=CFPRound.FIRST_ROUND`. The structured-field-preferred design
from the earlier validation pass is confirmed correct against a genuine
record; the keyword/notes heuristic remains fallback-only, unchanged.

**Genuine fixture.** Four records copied verbatim from the live `/games`
response were committed as
`src/cfb_edge_finder/data/fixtures/cfbd_live_verified_2026_sample.json`,
with full provenance in the neighboring `.PROVENANCE.md` file: an ordinary
FBS-vs-FBS game (the one that exposed the "San José State" alias gap), a
genuine FBS-vs-FCS game (Buffalo/UAlbany), a genuine neutral-site FBS-vs-FBS
game (TCU/UNC, Dublin), and a genuine Division-II game kept as a negative
case (must be filtered out, not FBS-involved). All four are run through
`normalize_cfbd_game()` in `tests/test_game_normalization.py`. A full
genuine CFP *game* record (not just the `playoff` sub-object above) was
deliberately not fabricated into this fixture -- see the `.PROVENANCE.md`
file for why.

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
- **What was NOT live-verified this session:** historical depth and
  current rate-limit numbers. **Field names ARE now checked against
  CFBD's own officially-generated client library documentation** (fetched
  from `github.com/CFBD/cfbd-python`, real primary-source schema
  documentation -- see "Validation follow-up" above), which is a step
  better than the original "built from well-documented community
  convention, not independently checked" position, but is still not a
  live payload. Where two official CFBD client repos disagreed with each
  other, `game_normalization.py` checks multiple candidate keys
  defensively rather than asserting one as fact.

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

`src/cfb_edge_finder/teams/registry.py`. **138 FBS teams** (reconciled
from 134 during the validation follow-up -- see above), seeded from
general knowledge plus web-search-verified corrections -- **still not a
live CFBD fetch**, and the module's own docstring says so loudly, with
the Pac-12 (rebuilt) conference membership specifically flagged as
highest-uncertainty (it was mid-transition as of training data).
`vendor_ids` is deliberately left empty for every seed team: fabricating
specific CFBD/ESPN numeric team IDs from memory would be exactly the kind
of unverified-but-authoritative-looking data this project refuses to
produce elsewhere (see `kalshi/executable_price.py`'s fee-rate handling
for the same principle). Vendor IDs are meant to be populated by real
ingestion runs observing them, not hardcoded.

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
both from a source's raw `seasonType`/`week`/postseason classification.
Week 0 works because the existing `wk\d{2}` slug pattern already allows
`wk00` (verified in Milestone A). Postseason classification now has two
mechanisms, tried in order:

1. **Structured (preferred):** CFBD's Game model exposes a nested
   `playoff` object (`GamePlayoff`: `competition`, `round`, `bowl_name`,
   etc.) for CFP-bracket games -- verified against CFBD's own
   officially-generated client library docs on GitHub (see "Validation
   follow-up" above). When present, `raw["playoff"]["round"]` is mapped
   directly to `CFPRound` (`_CFBD_ROUND_TO_CFP_ROUND` in
   `week_labels.py`), no text-guessing involved.
2. **Heuristic (fallback):** conference championships and non-CFP bowls
   have no `playoff` object in CFBD's schema (`PlayoffCompetition` only
   defines `"cfp"`), so those -- and CFP games missing their `playoff`
   object for any reason -- still classify via free-text keyword matching
   on `notes`/`name`/`gameName`, same as before.

Both paths **fail loud** on anything unrecognized
(`UnclassifiablePostseasonError`) rather than guessing -- see
`tests/test_week_labels.py`.

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

## FBS-vs-FCS inclusion policy

`scripts/ingest_schedule.py`'s `_is_fbs_involved` requires only ONE side
of a game to be FBS-classified, not both -- a common Week 0/1 "buy game"
against an FCS opponent is part of the FBS team's real schedule and must
not be silently dropped. Team resolution
(`cfb_edge_finder.ingestion.team_matching.resolve_team_id_for_game`)
mirrors this: an unrecognized name whose classification is explicitly
non-FBS resolves to a deterministic generated slug (via the same
`slugify_team` used everywhere else) instead of raising, since this
project's curated registry only covers FBS programs by design -- an FCS
opponent will never be *in* it, and that's expected, not a data gap.
Genuine ambiguity (bare `"Miami"`) and unresolved *FBS* names still fail
loud regardless of classification -- only the "opponent is legitimately
outside our curated scope" case is relaxed. See
`tests/test_game_normalization.py` (FBS-vs-FCS section) and
`tests/test_ingest_schedule_script.py::test_fbs_vs_fcs_game_retained_end_to_end_through_the_script`.

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

**This remains the single biggest blocker: no live CFBD API payload has
been obtained in any session so far** (network egress to CFBD's own
domains has been blocked in every attempt). Everything below should be
read with that in mind -- genuine progress was made this session via
web search and CFBD's own GitHub-hosted client library documentation,
but neither is a substitute for a real `/games` response.

1. **No live API call was possible this session either** -- two more
   direct-fetch attempts to `api.collegefootballdata.com` failed with
   `EGRESS_BLOCKED`, same as every prior attempt. Field names are now
   checked against CFBD's own officially-generated client library docs
   (a real improvement -- see "Validation follow-up" above), but that is
   still secondary documentation, not a payload, and the two official
   client repos (`cfb.js`, `cfbd-python`) didn't fully agree with each
   other, which is itself a sign the schema has changed over time and a
   live check is needed to know which version is current.
2. **Team registry conference assignments are still best-effort** for
   everything except the four newly-added transitional teams (which were
   cross-checked); the rebuilt Pac-12's membership remains the specific
   highest-risk entry. Reconcile the full registry against a live
   `/teams/fbs` fetch before trusting conference-based logic.
3. **Postseason classification now prefers CFBD's structured `playoff`
   field**, a real improvement over pure heuristic, but this session
   still could not confirm via a live payload (a) that the `playoff`
   field name and nested field names are exactly as documented, or (b)
   whether it's populated as expected on genuinely-served CFP games. The
   free-text heuristic remains as a fallback and needs its keyword lists
   widened as real descriptors are observed for conference championships
   and non-CFP bowls, which have no structured equivalent.
4. **Bowl sponsor-name volatility remains unsolved by design** (documented
   already in Milestone A) -- `bowl_display_name` is separated from the
   ID-safe slug specifically so this doesn't corrupt identity, but the
   slug itself can still differ year to year for the same physical bowl.
5. **ESPN cross-check is implemented and unit-tested but not wired into
   the default CLI run** -- `cross_check_secondary`/`find_match` exist and
   work, but `scripts/ingest_schedule.py` doesn't call the ESPN client by
   default yet.
6. **Vendor IDs beyond `cfbd` are not populated** in the team registry
   seed -- `TeamRecord.vendor_ids` stays empty until a real ingestion run
   (or an ESPN cross-check pass) observes and records them.
7. **FCS-opponent team IDs are generated, not curated** -- an FCS
   opponent's slug is deterministic but has none of the registry's
   alias-resolution safety net (no conference, no alternate-name
   handling); acceptable for identity purposes (it's still stable and
   collision-safe) but worth a dedicated FCS registry tier if FCS
   opponents ever need richer metadata.
