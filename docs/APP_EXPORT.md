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
