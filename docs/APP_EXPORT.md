# App export (`edge_finder.app.v1`)

`scripts/app_export.py` publishes the Edge Finder app bundle for college football to
`app/latest/` on `main`. It is an **adapter, not a model**: everything in it is reshaped from data
this repository already commits or already keeps on a data branch, and it invents nothing — no
probability, no edge, no thesis, no P&L.

```
python scripts/app_export.py --out app/latest \
    [--data-root data/live] [--accounting-dir <checkout of accounting-data>] \
    [--now <iso-utc>] [--commit-sha X] [--workflow-run-id Y] [--skip-unchanged]
```

The contract itself (schemas, ids, builders, publication rules) is vendored byte-for-byte under
`contract/edge_finder_contract/` and must not be edited here; `tests/test_app_contract_v1.py`
fails if the copy drifts from its `MANIFEST.json`.

## The one thing to know first: the CFB model is retired

`docs/MODEL_RETIREMENT_2026.md` removed model-generated probabilities from the live path. The live
product of this repository is the **Kalshi market inventory**. Consequently:

| File | Content | Why |
|---|---|---|
| `model_prices.json` | **empty, by design** | there is no production model to price with |
| `theses.json` | **empty** | no prose is produced anywhere in the live path |
| `recommendations.json` | **empty** | candidate artifacts are private/gitignored and the exporter never reads the decision store |
| `health.json` | `model_required=false`, `model_status=NOT_APPLICABLE`, `bet_authority=RESEARCH_ONLY` | the sport has no betting authority; `overall_status` is `RESEARCH_ONLY` when the catalog is fresh |

A wager in `wagers.json` was placed by the owner and recommended by nothing in this repository.
`performance.notes[0]` repeats that sentence; `wagers[].recommendation_id` / `model_price_id` are
always null because there is nothing to link to (linkage is still applied through the contract's
temporal `linkage.apply_links`, which is what guarantees the null is honest rather than skipped).

## What is exported, from where

| Output | Source | Loader used |
|---|---|---|
| `events.json` (+ participants) | `data/live/cfb_market_catalog.json` `games[]` (`game_key`, `title`, `kickoff`, `identity{...}`) | `cfb_edge_finder.execution.slate.load_catalog`, `execution.semantics.build_game_teams` |
| `markets.json` | `data/live/games/<game_key>.json` `markets[]` | same `load_catalog` |
| `wagers.json` | `accounting-data` branch `wagers/<season>.jsonl` (`cfb_accounted_wager.v1`) | `cfb_edge_finder.accounting.store.read_rows` |
| `settlements.json` | `settlements/<season>.jsonl` (`cfb_wager_settlement.v1`) with `settlement_amendments/<season>.jsonl` applied | `accounting.economics.apply_amendments` |
| `runs.json` | the catalog capture (`capture.content_fingerprint`, `capture.captured_at`) | — |
| `board.json`, `event_detail/<event_id>.json`, `performance.json`, `health.json`, `manifest.json` | derived from the above by the contract's builders | `edge_finder_contract.board/performance/health/publish` |

The accounting branch is **never checked out into the working tree**: the workflow fetches it
with `git fetch --depth=1 origin accounting-data` and extracts it with `git archive` into
`$RUNNER_TEMP/acct`, read-only. When `--accounting-dir` is absent or missing, wagers and settlements
export empty, the manifest status is `PARTIAL`, and health carries a warning saying so.

### Events

* `event_id` = `ids.event_id("CFB", "kalshi_milestone_id", identity.milestone_id)` when Kalshi's
  `football_game` milestone named the game (217 of 224 games on the 2026-10-02 catalog); otherwise
  `ids.event_id("CFB", "kalshi_game_key", game_key)`. Every other id travels in `source_ids`
  (`kalshi_game_key`, `kalshi_milestone_id`, `kalshi_main_event_ticker`).
* `start_time_utc` = `games[].kickoff`. `start_time_confidence` is `SCHEDULED` for milestone games
  (Kalshi's own schedule) and `ESTIMATED` for the event-ticker-only games (kickoff taken from the
  contracts' occurrence time).
* `status`: `LIVE` when the milestone says `inprogress`; `FINAL` when the milestone says final or
  **every** market of the game is closed/finalized; otherwise `LIVE` if `kickoff <= now`, else
  `SCHEDULED`. Past games stay on the board for as long as the catalog keeps them.
* Participants: `TEAM`, source `kalshi_team_code` with the code Kalshi uses in that game's own
  tickers (`WKU`, `NMSU`); the Kalshi team UUID from `custom_strike.football_team` is in
  `source_ids.kalshi_football_team`. A team no contract names by code falls back to source
  `kalshi_team_name`. Home/away come from `build_game_teams` (the milestone's "A at B" phrasing is
  stated, "A vs B" is a labelled convention — both are echoed in `extensions.home_away_source`).
* `league` = `identity.league` (`NCAAFB`), `season` = `identity.season_year`, `competition` =
  `identity.division` (`FBS`/`FCS`); conference, week, tier and the family distribution are in
  `extensions`.

### Markets

* `market_id` = `mkt_kalshi_<TICKER>`; standard fields map 1:1 from the catalog record
  (`yes_bid/yes_ask/no_bid/no_ask/last_price` already in dollars 0–1, `volume`, `open_interest`,
  `close_time` → `close_time_utc`). `captured_at` = `capture.captured_at` for every market.
* `market_family` = the catalog's `family`; the 36 markets the catalog could not classify keep
  `market_family: "unknown"` — kept, never dropped. `period` is the catalog's period label.
* `line` = `floor_strike` for spread families; `threshold` = `floor_strike` for everything else that
  has one (totals, team totals, props). `side` is `HOME`/`AWAY` for team-keyed contracts (resolved
  through the team UUID or the ticker suffix), `OVER` for totals (YES = over), null otherwise.
* `market_status`: `active` → `OPEN`, `closed` → `CLOSED`, `finalized`/`settled` → `SETTLED`,
  `unopened` → `UNOPENED`, anything else → `UNKNOWN`.
* `extensions` is deliberately small: `{book_state, fee_at_yes_ask, strike_type,
  sentinel_full_width_book}`. The raw record stays in `data/live/games/`, referenced by
  `raw_market_reference` (`games/<key>.json#<ticker>`).
* **Sentinel books.** A finalized contract's 0/1 book is Kalshi's post-settlement sentinel, which
  the catalog flags as `is_sentinel_full_width_book`. Those quotes are exported as null (the
  contract would otherwise compute a meaningless 0.5 `market_probability`); `last_price` is kept.
* Market stubs: a wager whose ticker is no longer on the board gets `build.market_stub` (prices
  null, `source: cfb_accounting_ledger`, family from `catalog.classification.classify_market` on the
  series ticker, `market_status: SETTLED` when a settlement exists). Every wager's `market_id`
  therefore exists in `markets.json`.

### Wagers and settlements

* `wager_id` = `ids.wager_id("CFB", source_bet_key)` for routed rows (every row the router delivers
  carries one); `source = KALSHI_ROUTER`. A row without a key would use the ledger's own `wager_id`
  as `native_id` with `source = OTHER` (none exist today). `selection` = `side` (YES/NO),
  `contracts`, `stake` (the ledger's stake, which per the amendment derivation already includes the
  entry fee), `average_price` = `execution_price`, `fees` = `fees_paid`, `placed_at` = `executed_at`.
* A wager is joined to an event by parsing `market_ticker` → event ticker → `game_key`
  (`catalog.identity.parse_event_ticker`) → the board's game. Games that left the catalog give
  `event_id: null` (the ticker still names the game in `source_ids.kalshi_game_key`).
* `settlement_id` is derived from the wager id by the contract. `result` is the ledger's
  `WON`/`LOST` (a null result becomes `UNKNOWN` with a `result_absent` refusal), `settled_at` the
  ledger's, `gross_payout` = `gross_return`, `net_pnl` = the **canonical** `net_profit_loss` after
  `settlement_amendments` (the as-filed figures and `canonical_amendment_id` ride in
  `extensions`/`source_ids`). `fees` on the settlement is null: the ledger states no separate
  settlement fee. `verification_status = EXCHANGE_CONFIRMED` (router settlements are the exchange's
  own result). A settlement the ledger refuses to price carries its `refusals` and a null `net_pnl`.
* `performance.json` totals are the contract's sums over these objects;
  `tests/test_app_contract_v1.py` asserts they equal `accounting.report.summarize(...)` on the same
  rows (same `realized_profit_loss`, won/lost/settled counts).

### Run, health, freshness

* `run.native_run_id` = `capture.content_fingerprint`; `run_id` is deterministic from it plus
  `completed_at` (= `--now`). `commit_sha`/`workflow_run_id` from the CLI. `source_ids` also carries
  `ledger_digest` (sha256 of the ledger files read) and `export_state_key` (see "quiet" below).
* `health.last_market_capture` = `capture.captured_at`. Thresholds: **market_data fresh 45 min /
  stale 6 h**, export the same. Rationale: `kalshi-market-catalog.yml` runs every 30 minutes in the
  Thu 18:00–Sun 06:00 UTC slate window and every 6 hours otherwise, and commits only when the
  market surface's fingerprint changed, so between the two thresholds the capture is `AGING`.
  Outside the window (and whenever the surface does not move) `STALE` is the truthful reading: the
  committed prices really are that old.
* `router_as_of` = latest `executed_at` in the ledger and `settlement_as_of` = latest `settled_at`.
  Neither is a heartbeat — the ledger records no delivery time — so a quiet week reads `STALE` on
  those two non-required components without changing `overall_status`.
* `next_scheduled_run` = `now + 30 min` (the export cron).

### Quiet publication (`--skip-unchanged`)

`runs.json` carries `export_state_key` = sha256 of (catalog fingerprint, ledger digest, health
`overall_status`/`freshness_status`/`market_data_status`, commit sha, every event's status). With
`--skip-unchanged` the exporter builds the bundle, and publishes nothing when the tree already
carries that key and `publish.verify_published` is clean. A freshness flip (`AGING` → `STALE`), a
game going `LIVE`, a new catalog commit or any ledger change republishes. Without the flag
(local runs, tests) it always publishes.

### Failure path

Any exception during the build leaves `app/latest` untouched except `health.json`, which is
rewritten with `export_failed=true`, `payload_run_id` = the previously published run (from the
existing manifest), `errors=[...]`, and the process exits 1. With a previous payload `overall_status`
is `DEGRADED`; with none it is `UNAVAILABLE`. The workflow commits that health file and stays red.

## Where the app reads it

`contract/edge_finder_contract/registry.json` → `CFB`: repo `chmoses98/cfb-edge-finder`, branch
`main`, `app_root` `app/latest`, raw base
`https://raw.githubusercontent.com/chmoses98/cfb-edge-finder/main/app/latest`.

Writer: `.github/workflows/app-export.yml` — `workflow_run` after "Kalshi CFB Market Catalog",
cron `*/30 * * * *`, `workflow_dispatch`; `contents: write`; shares the `research-data-write`
concurrency group with the catalog (queue, never cancel); commits `app/latest` to `main` only when
something changed, with the same rebase-retry push loop the catalog uses. It is deliberately NOT a
step inside the fingerprint-gated catalog workflow, which would never run on a quiet tick and would
never see a ledger-only change.

## Size

The 2026-10-02 catalog (224 games, 13,676 markets + 94 stubs) exports to ~28 MB: `markets.json`
~13 MB and the same markets again across `event_detail/` (one file per event, which is what the
app's detail screen reads). Every market is standard fields plus the four-key extensions above.

## Known gaps

* No model, so no model prices, theses or recommendations; `board.items[].health_flags` carries
  `NO_MODEL_PRICES` on every event. This is the retirement, not a bug.
* `wagers[].event_id` is null for every ledger wager whose game has left the catalog (the catalog
  publishes a ~10-day horizon and prunes finished games). The 97 wagers on the 2026 ledger all
  predate the current board; `source_ids.kalshi_game_key` keeps the join available.
* The ledger holds no router delivery timestamp and no Kalshi order/fill ids, so `kalshi_order_id`,
  `kalshi_fill_ids`, `router_ingested_at` and `side` (BUY/SELL) are null.
* No CLV, no bankroll history: the repository records neither.
* Seven of 224 games have no Kalshi milestone (identity from the event ticker only, kickoff
  `ESTIMATED`); they carry no league/season/conference.
* The catalog's `completeness` block is passed through in each event detail's `context`, not
  interpreted.

## Contract feedback

* `build.market` computes `market_probability = (yes_bid + yes_ask) / 2` whenever both quotes are
  present, which turns a 0/1 post-settlement sentinel book into "0.5". The adapter nulls sentinel
  quotes to avoid it; a `market_probability=False`-style opt-out or a book-state input would be
  cleaner.
* `health.build_health` treats `router_as_of`/`settlement_as_of` as heartbeats; a ledger that only
  records placements has no heartbeat to give, so those components read `STALE` in quiet weeks.
* `board.build_event_detail` filters `markets` over the full list per call; the adapter pre-groups
  markets by event before calling it, which is worth doing inside the contract for 13k-market
  sports.

## Research explorer (`app/latest/explorer/`, contract 1.1.0)

`scripts/research_export.py` publishes the research graph beside the v1 bundle. It runs **after**
`app_export.py`, reads the published v1 files (`manifest.json`, `events.json`, `markets.json`,
`wagers.json`), so every `prt_` / `evt_` / `mkt_kalshi_` id is the v1 id, and `run_id`,
`generated_at` and `commit_sha` are the v1 publication's own (`--now` overrides `generated_at`).

```
python scripts/research_export.py --out app/latest [--data-root data/live] \
    [--include-cfbd --research-root <git archive of research-data>] \
    [--min-interval-minutes N] [--check-due] [--force] [--now <iso-utc>]
    [--script-engine-dir data/scripting/live/sift]
```

**CFBD values are off by default.** `docs/DATA_SOURCES.md` records that redistributing raw CFBD API
data is prohibited, and the owner has not resolved whether season aggregates may be republished. Until
then the explorer publishes no CFBD-derived value: without `--include-cfbd` the research root is never
read and every CFBD-backed capability is UNAVAILABLE with the reason "CFBD redistribution licence
unresolved; owner opt-in required (--include-cfbd)". The workflow's job-level `INCLUDE_CFBD: "false"`
is the single switch: setting it to `"true"` fetches research-data and passes `--include-cfbd`.
Everything below about CFBD metrics describes the opt-in path.

**Refresh cadence.** `--min-interval-minutes N` gates rebuilds. In order, the tree is rewritten when:

1. `--force` was given (App Export's `force_explorer` dispatch input): bypasses only the age throttle;
2. it is missing, or the v1 event set changed (`research.refresh_due`);
3. the CFB Script Engine publication it embeds changed, at any age: the content fingerprint of
   `--script-engine-dir` differs from the one recorded for the published tree, or none is recorded;
4. it is older than N minutes (`research.refresh_due`).

Otherwise the exporter prints the reason and leaves `explorer/` untouched. The workflow checks first
(`--check-due`, written to `$GITHUB_OUTPUT`) with N = 180, so the 30-minute cron rewrites the
explorer at most every 3 hours unless games were added or removed or the scripts changed, and the
history deepening and the research fetch run only when a rebuild is due.

*Script Engine fingerprint.* `script_engine_fingerprint` is one sha256 over the payloads the explorer
embeds (`data/scripting/live/sift/*.json.gz`, decompressed): game key plus the canonical JSON
(sorted keys) of each payload, in game-key order. It leaves out only the engine's per-run clocks
`script_generation.mapped_at` and `script_generation.prices_captured_at`, which move on every run
whatever the content. Everything else counts: artifact hash, freeze time, methodology version, scripts,
market map and labels. So file order, gzip bytes and a run that changed nothing cannot move it, and a
new artifact, a removed game, a re-mapped market or a methodology change always does. After each
successful publish the exporter writes `explorer/sources.json` (`cfb_explorer_sources/1.0.0`): the
fingerprint, the payload count and methodology versions, bound to the published `explorer/index.json`
by its sha256. A record bound to another tree (a copy, an older run) reads as no record. The record is
written after the atomic swap, so a crash in between, a failed publish or an explorer published before
fingerprints all err the same way: the next run rebuilds once. The contract's `explorer_index` schema
is shared with other repositories and closed (`additionalProperties: false`), so the record lives
beside the index rather than in it.

*Triggers.* App Export runs after every Kalshi catalog run, on its 30-minute clock, on manual dispatch,
and after the **CFB Script Engine** workflow completes. The job starts on a Script Engine completion
only when that run succeeded, on `main`, in this repository. The checkout is always `main`, so only
main's payloads can be fingerprinted, embedded or recorded. Because invalidation is by content, not by
which event started the run, any later run (the clock, the catalog) also rebuilds when a Script Engine
publication was missed. For example, GitHub may drop a queued run in the shared `research-data-write`
concurrency group.

Honest framing (audit 2026-10-03): CFB's live, maintained surface is **market inventory + market
history + accounting**. Everything team-level is a frozen CFBD corpus (fetched once, 2026-09-02) on
the orphan `research-data` branch that nothing refreshes, with **no 2026 in-season stats**.

### Inputs

| Input | Where | Used for |
|---|---|---|
| v1 bundle | `app/latest/*.json` | events, participants, markets, wagers, run id |
| catalog git history | `git log` / `ls-tree` / `cat-file` over `data/live` on main | market history |
| CFBD snapshot | `research-data:data/research_cache/v2/<season>/{teams_fbs,ratings_sp,season_advanced,talent,recruiting_teams,returning_production,games}.json.gz` | team identity, season metrics, rankings, pregame Elo |
| v2 research dataset | `research-data:data/research/v2/dataset.parquet` (+ `.meta.json`) | opponent-adjusted states (RESEARCH); needs `pyarrow` (`pip install -e ".[research]"`) |

When a rebuild is due the workflow deepens main to 18 days (`git fetch --shallow-since`). This is
still needed at a 3-hour cadence: market history is rebuilt from git on every rebuild and the
checkout is depth 1, so without it the history would be the current snapshot only; the catalog lists
games about two weeks ahead and the exporter stops at the first commit listing none of them. With the
CFBD opt-in it also fetches `research-data` with `git fetch --depth=1` and extracts only the two paths
above with `git archive` into `$RUNNER_TEMP/research`. If that fetch fails, the explorer still
publishes markets, market history, wagers, profiles and capabilities; every CFBD-backed capability is
UNAVAILABLE for that run with the reason "research-data branch not available" (tested).

### What it publishes

* **Market history** — one `market_history/<evt_>.json` per v1 event. The catalog commits only when
  its fingerprint changes; the exporter walks those commits newest to oldest (stopping at the first
  that lists none of the board's games), plus the working tree, and emits a point per ticker only when
  the book (yes bid/ask) or last trade changed (`source` = `catalog@<sha12>`). Labelled
  **"change-detected snapshots, not a closing series"**. Sentinel 0/1 books are null quotes. A
  document over 400 KB keeps each ticker's most recent states and says so in its quality limitations.
  The model-era research observation ledger (Aug 26–Sep 16 2026) covers none of the current events
  and is not republished.
* **Event research** — one per v1 event: participants, every v1 market (`market_ref`), the ledger
  wagers on that game, `market_history_path`, and matchup rows (home vs away observations of the same
  metric) where a team maps to CFBD. No projections: the model is retired. Markets are never dropped;
  a document over 150 KB trims matchup rows from the end and notes it.
* **Team profiles** — one per v1 participant (266). Mapped teams carry CFBD season aggregates for the
  latest two seasons per metric with comparison context, rush/pass splits for the latest season,
  ranking links for every season, series links, and CFBD identity + home-stadium attributes in
  `extensions.cfbd`. Unmapped teams carry Kalshi markets and games only.
* **Rankings** — every CFBD season metric × season over all FBS teams with a stored value (universe
  "FBS teams, <season> season (CFBD)"); FBS teams not on the board appear under a `cfbd_team_id`
  participant id with no profile path.
* **Series** — pregame Elo per completed game (PARTIAL) and opponent-adjusted offensive PPA,
  defensive PPA and margin states (RESEARCH), each capped at the last 40 completed games, through 2025.
* **Metrics** — SP+ (overall/offense/defense), season advanced offensive/defensive PPA, success
  rate, explosiveness, defensive havoc, offensive havoc allowed, talent, recruiting points,
  returning production (percentPPA), pregame Elo, and five RESEARCH states (adj off/def PPA, adj
  off/def success rate, adj margin).

### Identity (Kalshi participant ↔ CFBD team)

Exact only: the Kalshi display name must equal the CFBD `school`, one of its `alternateNames`, or
resolve through `teams.registry.resolve_team_alias` to the same canonical slug as a CFBD school name;
each rule must name exactly one CFBD team, and no team may be claimed twice. The CFBD abbreviation is
recorded (`abbreviation_matches_kalshi_code`) but never used alone. On the 2026-10-03 board: **139 of
266** participants map (106 by school, 30 by registry alias, 3 by alternate name; 122 of them with a
matching abbreviation); the **127 unmapped are all FCS** programs, which CFBD `teams_fbs` does not list.

### Capability statuses (`explorer/capabilities.json`)

With the CFBD opt-in (default: every CFBD-backed row below is UNAVAILABLE, reason above):

| Status | Capabilities | Why |
|---|---|---|
| VERIFIED | market_prices, game_markets, team_props, wager_history, search | production catalog + ledger; wager_history evidence = event docs that list on-board wagers |
| PARTIAL | team_profiles, event_research, team_metrics, advanced_stats, rankings, comparisons, time_series, situational_splits (play_type only), opponents, venue_effects (stadium attributes, no effects), market_price_history, player_props (inventory only) | real but frozen CFBD snapshot / change-detected history |
| RESEARCH | opponent_adjustment, matchup_metrics | v2 research dataset; builder not on main |
| UNAVAILABLE | player_profiles, player_metrics, player_game_logs, usage, lineups, play_by_play, schedule_strength, recent_form_windows, injuries, weather, projection_distributions, raw_projections, calibration, historical_accuracy, clv, team_game_logs, historical_results | no data, retired model, hibernated captures, or not republished (licence) |

`team_game_logs` and `historical_results` are PARTIAL in the audit but are **not republished**:
`docs/DATA_SOURCES.md` records that redistributing raw CFBD API data is prohibited, so only
season-level aggregates and one per-game field (pregame Elo) are published. That licence question is
open and is stated in every CFBD-backed capability's `limitations`.

### Sizes (real data, 2026-10-03 board, full catalog history, contract 1.1.1)

Measured with `research.tree_bytes`. **Default (no CFBD): 33.3 MB** — market_history 21.3 MB (261,
max 399 KB), events 6.9 MB, teams 4.7 MB, index.json 279 KB, search_index.json 169 KB,
capabilities.json 14 KB. **With `--include-cfbd`: 58.4 MB** — adds rankings 5.4 MB (170, max 34 KB)
and series 7.4 MB (554, max 14 KB); teams 13.2 MB (max 122 KB), events 10.4 MB (max 150 KB),
index.json 418 KB (one compact entry per file, ≈1,270 files), search_index.json 232 KB.

### Deliberately not published

Raw CFBD box-score, advanced per-game, drive and play rows; game scores; betting lines; recruiting
and portal player rows; injuries, weather and odds sidecars; the retired model's probabilities,
shadow ledgers, calibration and backtests; any 2026 in-season team statistic (none exists).

### Failure and publication order

`research.publish_explorer` validates everything and swaps the tree in atomically; on failure the
previous `explorer/` is untouched and the step exits 1. The workflow step (`id: research_export`)
is `continue-on-error`, so `app/latest` is still committed, and a final step fails the job. Since
contract 1.1.1 the v1 `publish.publish` never prunes `explorer/`, so a skipped or failed explorer run
keeps the last published tree beside the new v1 payload (its `run_id` then names the earlier v1 run).


## CFB Script Engine extension

`research_export.py --script-engine-dir data/scripting/live/sift` (the
default) embeds each game's CFB Script Engine payload under
`event_research.extensions.script_engine`. `extensions` is the contract's
open, backward-compatible slot, so no schema, `MANIFEST.json` or vendored
contract changes, and other sports' documents are unaffected. Payloads are
trimmed (correlations, then unlabelled expressions, then registry
descriptions, then all but best/multi-script expressions) only when an event
would exceed its 150 KB budget, with a note in `context.notes`. When payloads
are present the capability manifest reports `opponent_adjustment` and
`matchup_metrics` as `RESEARCH`. See `docs/SCRIPT_ENGINE.md`.
