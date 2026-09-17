# Kalshi CFB market catalog — the live path

The repository's live product. For every upcoming physical
college-football game, the complete inventory of every Kalshi market
attached to it: raw contract semantics, executable prices, quoted sizes,
liquidity, settlement rules, and honest diagnostics about how complete the
capture actually is.

It contains **no model output**. No projection, no fair probability, no
expected score, no edge. The per-contract `mechanics` block is arithmetic
on the quoted price and nothing else — see [Market mechanics](#market-mechanics).

---

## 1. Verified Kalshi endpoint behaviour

Everything in this section was measured against the live API from a GitHub
Actions runner on **2026-09-17** (this dev environment has no egress to
Kalshi's hosts — organization policy answers 403 to CONNECT). The raw
transcripts are committed under `docs/evidence/`, and the probes are
re-runnable from `validate-kalshi-cfb-live.yml` (`milestone_surface` and
`milestone_reconciliation`).

Base URL: `https://api.elections.kalshi.com/trade-api/v2`. Every call is
an unauthenticated GET.

### `limit` is mandatory, and 1000 is too many

`GET /milestones` with no `limit` returns:

```
HTTP 400 {"msg":"Query argument limit is required, but not found"}
```

`limit=200` works on `/milestones`, `/events`, `/markets` and `/series`.
The first revision of our own probe requested `limit=1000`, above the
accepted maximum, so **every paginated sweep failed instantly and reported
an empty market universe** — the run finished in 11 seconds claiming zero
milestones, zero events and zero markets, while non-paginated calls in the
same run returned HTTP 200 bodies. That is exactly the failure mode this
system exists to make impossible, caught on our own probe first.

### The documented milestone filter returns nothing

The mission's documented filter —
`category=Sports & competition="College Football" & type=football_game` —
returns **HTTP 200 with zero milestones**. The endpoint answers; the filter
matches nothing.

**Why:** a milestone payload has no `competition` field at all. Its keys
are `id`, `title`, `start_date`, `end_date`, `category`, `type`, `details`,
`primary_event_tickers`, `related_event_tickers`, `source_id`,
`source_ids`, `notification_message`, `last_updated_ts`. The league lives
at `details.league`, spelled **`NCAAFB`**, and is not a query parameter.

The working equivalent:

```
GET /milestones?type=football_game&limit=200   (paginated)
  -> 2,052 milestones   {NCAAFB: 1519, NFL: 432, CFL: 61, UFL: 40}
  -> filter client-side on details.league == "NCAAFB"
```

That is ~11 pages for the whole exchange. Unfiltered, `/milestones` exceeds
24,000 rows and our sweep hit its page cap without finishing — so the
`type` filter is not an optimization, it is what makes this affordable.

### A milestone is a complete physical game

```json
{
  "id": "a852dcb8-dc87-4c25-8b9a-c007ca70ee2e",
  "title": "Syracuse vs Pittsburgh",
  "start_date": "...",
  "category": "Sports",
  "type": "football_game",
  "details": {
    "league": "NCAAFB",
    "division": "FBS",
    "conference": "ACC",
    "tier": "A_MINUS",
    "status": "scheduled",
    "main_game_event_ticker": "KXNCAAFGAME-26SEP17SYRPITT",
    "season": {"year": 2026, "type": "REG", "week": 3},
    "home_team_id": "17b70f39-...", "away_team_id": "862690f7-..."
  },
  "primary_event_tickers": ["KXNCAAFGAME-26SEP17SYRPITT", "KXNCAAFSPREAD-26SEP17SYRPITT", ...25 more],
  "related_event_tickers": [...]
}
```

All **239** NCAAFB milestones with a future `start_date` carried
`related_event_tickers`. `details.status` observed across 1,519 NCAAFB
milestones: `scheduled` 751, `closed` 542, `complete` 126, `created` 48,
`time-tbd` 41, `inprogress` 11.

This is why no external schedule provider is needed: Kalshi states the
game's identity, division, conference, week and marquee tier itself.

### Query status vocabulary ≠ response status vocabulary

A genuine trap:

| Query | Returns markets whose `status` is |
|---|---|
| `status=open` | `active` |
| `status=settled` | `finalized` |
| `status=closed` | `closed` |
| `status=unopened` | (none observed for CFB) |
| `status=active` | **HTTP 400 — rejected** |

Observed `status` values across an unfiltered `KXNCAAFGAME` sweep (1,140
markets): `active` 478, `finalized` 660, `closed` 2. An earlier revision
of this repository's own probe compared market status against the literal
string `"open"` and **counted every tradeable market as closed**.

### `competition` on an event has two CFB spellings

`product_metadata.competition` across 1,505 `KXNCAAFGAME` events:

- `"NCAA Football"` — 1,495
- `"College Football Playoffs"` — **10**

Matching only the majority string would silently drop every CFP game — the
highest-profile games of the season. Both are accepted
(`identity.CFB_COMPETITIONS`).

`product_metadata.competition_scope` is Kalshi's own family label
(`"Game"`, `"1st Half Spread"`, …) and is published per event in the
catalog.

### An event's inline market list is not sufficient

| Source | Markets returned for one event |
|---|---|
| `GET /events?event_ticker=X&with_nested_markets=true` | **1** |
| `GET /events/X` (inline `markets`) | 22 |
| `GET /markets?event_ticker=X` (paginated) | 22 |

The list endpoint with nested markets truncates badly. Discovery
therefore always pages `GET /markets?event_ticker=…` and never trusts an
inline list.

Largest single-event market count observed: **31**
(`KXNCAAFSPREAD-26SEP18HOUTTU`), so pagination is not currently exercised
in production — but it stays mandatory, because Kalshi adds ladder rungs as
a game approaches and an unpaged sweep would silently truncate the first
event to cross 200.

### The market payload

Live keys, all present on 303/303 markets of one game:

```
ticker  event_ticker  status  market_type  strike_type  title
yes_sub_title  no_sub_title  rules_primary  rules_secondary
floor_strike (274/303)  custom_strike (208/303)
yes_bid_dollars  yes_ask_dollars  no_bid_dollars  no_ask_dollars
yes_bid_size_fp  yes_ask_size_fp  last_price_dollars  previous_price_dollars
volume_fp  volume_24h_fp  open_interest_fp  liquidity_dollars
notional_value_dollars  price_level_structure  price_ranges
open_time  close_time  expected_expiration_time  expiration_time
latest_expiration_time  occurrence_datetime  updated_time  created_time
can_close_early  early_close_condition  settlement_timer_seconds
exchange_index  result  expiration_value
```

Three consequences:

1. **Prices are decimal-dollar STRINGS** (`"yes_ask_dollars": "0.1200"`),
   not integer cents. `contract.py` accepts both spellings and divides a
   bare `yes_bid`-style key by 100; a value that parses as neither is
   preserved raw and reported as missing rather than coerced to 0, because
   a 0 price and an absent price are very different things.
2. **A market carries no `series_ticker`.** The catalog takes it from the
   parent event, which knows it for certain.
3. **Fee metadata is on the SERIES**, not the market (`fee_type:
   "quadratic"`, `fee_multiplier: 1`), and is joined in per contract.

Top-of-book quoted size is on the market (`yes_bid_size_fp`,
`yes_ask_size_fp`). Full depth is available per market from
`GET /markets/{ticker}/orderbook` (`orderbook_fp.yes_dollars` /
`no_dollars` as `[price, size]` pairs) — one request per market, so the
catalog publishes the inline top-of-book and leaves depth to an on-demand
call.

### Affordable-refresh filters

`GET /markets` accepts `min_close_ts` and `max_close_ts` (unix seconds) and
a `tickers=` list, all verified working. These are the levers for a
cheaper future refresh; the current implementation bounds cost with the
`--horizon-days` game window instead.

---

## 2. The discovery flow

```
  GET /milestones?type=football_game            [paginated, limit=200]
        └─ filter details.league == "NCAAFB"
           filter details.status ∈ {scheduled, created, inprogress, time-tbd}
           filter start_date within --horizon-days
                 │
                 ▼  ONE ROW = ONE PHYSICAL GAME
     union(primary_event_tickers, related_event_tickers)
                 │
                 ▼  per event
  GET /events/{event_ticker}          → title, sub_title, settlement_sources,
                                        product_metadata, mutually_exclusive
  GET /markets?event_ticker={t}       [FULL cursor pagination]
                 │
                 ▼  per market
     normalize losslessly (raw payload retained)
     classify  (label, never filter — unknown is kept)
     compute mechanics (price arithmetic only)
                 │
                 ▼
     group under the physical game key (event-ticker suffix)

  ─── AND, INDEPENDENTLY ───
  GET /series?category=Sports                   [dynamic, no allowlist]
        └─ CFB-looking series → GET /events?series_ticker=…&status=open
           → any game-level event the milestones did NOT name is
             attached to its game AND reported as
             discovery.events_only_in_series_sweep
```

### Physical-game identity

An event ticker is `<SERIES>-<GAMEKEY>` where `GAMEKEY` is `YYMONDD` plus
team codes. `KXNCAAFSPREAD-26SEP17SYRPITT` and
`KXNCAAF1HTOTAL-26SEP17SYRPITT` share `26SEP17SYRPITT`, so the suffix is
the join key across every series for one game.

Verified against 1,946 live open CFB event tickers: 1,669 matched the
pattern; the 277 that did not were all season-level inventory
(`KXNCAAFAWARD-26APPOY`, `KXNCAAFSEC-26`, `KXNCAAFBIGTENWINS-26W10`),
which correctly have no physical game and are published separately in
`season_level_events`.

Two guards stop a false positive: a series ticker containing a
season-level marker (`POLL`, `AWARD`, `RANK`, `SEED`, `CHAMPION`, …) is
excluded even when its suffix parses as a date
(`KXNCAAFCFPPOLL-26NOV17R1`), and a game key with no team codes is
rejected.

### Why two paths

A single discovery path fails silently: if a series is missed — an
unrecognized ticker, a family launched this morning — its markets are
simply absent and nothing in the output says so. The series sweep exists
to make that visible, not to be the spine. Its series selection is read
from Kalshi at runtime and is deliberately generous; a false positive
costs one wasted request whose results `is_cfb_event` discards, while a
false negative costs an entire silently-missing family.

**This is not hypothetical.** The retired architecture drove discovery
from eight hardcoded CFB series. The live exchange carries **145 CFB
series, 98 with open events**: quarter winners, quarter spreads and totals,
second halves, both-teams-to-score per quarter, a dozen team-stat families
(sacks, turnovers, field goals, interceptions, receiving yards, rushing
attempts…), first touchdown scorer, first team to score, overtime, most
overtimes, highest-scoring quarter. An allowlist would have published a
confident, complete-looking menu missing most of what Kalshi offers.

---

## 3. Classification

Applied **after** discovery. It is a label, never a gate.

Primary signal is a structural read of the series ticker — period token
plus family suffix — so a series that does not exist yet classifies
correctly without a code change: a `KXNCAAF2HTEAMTOTAL` launched tomorrow
resolves to a second-half team total because the parts are parsed, not
looked up. Where the ticker resolves nothing, Kalshi's own contract text
is used as corroboration. Nothing infers semantics from what a sportsbook
would normally offer.

Labels: `game_moneyline`, `game_spread`, `game_total`, `team_total`,
`first_half_{moneyline,spread,total,team_total}`,
`second_half_{moneyline,spread,total}`,
`quarter_{moneyline,spread,total}`, `winning_margin`,
`both_teams_to_score`, `first_score`, `touchdown_scorer`, `player_prop`,
`team_stat_prop`, `game_stat_prop`, `overtime`, `multivariate_combo`,
`season_futures`, `other`, **`unknown`**.

`unknown` is a first-class, fully-retained outcome, and a market labelled
`unknown` does **not** make its game incomplete — it is captured, just not
labelled. Every classification carries a `classification_rationale` so a
surprising label is debuggable from the artifact alone.

`is_alternate_line` is decided across siblings, not per market: within one
event and family, more than one distinct strike means a ladder and every
rung is flagged. A moneyline with two contracts is two sides, not two
rungs.

Two substring bugs the test suite caught, both worth knowing about:

- `"OT" in "TOTALFG"` is true (index 1), so a substring check published a
  real total-field-goals contract as an overtime market. Overtime suffixes
  now match on equality.
- `"CS"` alone means the FCS-champion market, but as a substring it sits
  inside `KXNCAAFCSGAME` — the FCS **game** series. Treating it as a
  substring would have buried every FCS game's menu under season futures.

---

## 4. Output schema

### `data/live/cfb_market_catalog.json` — the primary product

```
schema_version: "cfb_market_catalog/1.0.0"
capture:      captured_at, source, authenticated:false,
              contains_model_projections:false, content_fingerprint, notes
discovery:    spine, milestone_filter, reconciliation_path,
              milestones_considered, milestones_selected,
              milestone_sweep_complete, series_discovered,
              series_with_open_events, series_sweep_complete,
              events_only_in_series_sweep[], requests_made,
              request_failures, pagination_failures, failed_paths[], errors[]
totals:       physical_games, events, markets, season_level_events,
              family_distribution{}, status_distribution{},
              unknown_family_markets
completeness: capture_complete, games_incomplete[], games_incomplete_count,
              native_game_markets_complete,
              multivariate_market_coverage{}
games[]:      game_key, title, kickoff,
              identity{source, milestone_id, milestone_status, league,
                       division, conference, tier, season_year, season_type,
                       season_week, main_game_event_ticker},
              events[]{event_ticker, series_ticker, title, sub_title,
                       competition, competition_scope, mutually_exclusive,
                       settlement_sources},
              market_count, family_distribution{},
              markets[]  ← see below
              completeness{...}
season_level_events[]:  CFB inventory with no physical game
```

Each `markets[]` entry:

```
market_ticker  event_ticker  series_ticker  status
family  period  classification_confidence  classification_rationale
is_alternate_line
title  yes_sub_title  no_sub_title
market_type  strike_type  floor_strike  cap_strike  functional_strike
custom_strike
yes_bid  yes_ask  no_bid  no_ask
yes_bid_size  yes_ask_size  no_bid_size  no_ask_size
last_price  previous_price
volume  volume_24h  open_interest  liquidity_dollars  notional_value
open_time  close_time  expected_expiration_time  expiration_time
occurrence_datetime  updated_time
rules_primary  rules_secondary  early_close_condition  can_close_early
settlement_timer_seconds  settlement_sources
exchange_index  fee_type  fee_multiplier
mechanics{...}
```

The primary artifact omits the raw Kalshi payload so it stays efficient to
ingest end-to-end; every betting-relevant field is present as a named key.
`cfb_markets_flat.json --include-raw` carries the untouched payload, so
nothing captured is ever unreachable.

### Market mechanics

Published per contract. Everything here is derivable from the order book
alone and would be identical whoever was playing:

```
yes_mid  no_mid  yes_bid_ask_spread  yes_bid_ask_spread_cents
implied_probability_yes_mid / _yes_ask / _yes_bid / _last
two_sided_quote
estimated_fee_per_contract_at_mid  fee_formula
quote_age_seconds  seconds_until_close  seconds_until_occurrence
```

`implied_probability` is the price restated in probability units — which is
what a $1 binary contract's price already is. A one-sided book yields
`null` for the mid rather than an invented price; a price outside `[0,1]`
yields `null` rather than a clipped, confident-looking 100%.

Fee is Kalshi's published quadratic schedule,
`ceil(fee_multiplier × 0.07 × contracts × P × (1−P))` in cents, with the
multiplier taken from the series. The quadratic shape matters: a 2¢
longshot and a 50¢ coin flip carry very different round-trip costs.

`tests/test_catalog_schema_and_isolation.py` pins the exact key set of this
block, so a model-shaped field cannot be added to it quietly.

### Determinism

Mappings are written with sorted keys, every list has an explicit sort, and
`write_json` is atomic (temp-file-then-replace) so a reader mid-slate never
sees a half-written catalog. Two captures of an unchanged surface produce
byte-identical market content.

`catalog_content_fingerprint` hashes the market content with
capture-time and clock-derived fields (`captured_at`, `mechanics`,
`quote_age_seconds`, `seconds_until_*`, request counters) stripped, so the
scheduled job can skip a commit entirely when nothing moved. **Prices and
sizes are inside the fingerprint** — a price move is a real change worth
recording.

---

## 5. Completeness semantics

### A failure is never a zero

Every sweep returns its items **and** whether it completed. The
distinction is structural: `PageSweep` refuses to be constructed as
complete-with-a-failure-reason or incomplete-without-one.

A sweep is incomplete when a request raised, a page was not a JSON object,
the cursor repeated (a server-side loop, caught on the second sighting), or
the page cap was reached with a cursor outstanding. An **empty but
complete** sweep — a game that genuinely has no markets — is a different,
explicitly distinguishable outcome.

The incident behind this is recorded in `KalshiClient._get`'s own
docstring: a 429 partway through a series sweep was treated as "0 markets
and continuing", and the run reported 2,966 markets instead of ~4,578 while
looking perfectly healthy.

### Per-game diagnostics

```
related_event_tickers_reported   events_fetched
failed_event_tickers[]           pagination_failed_event_tickers[]
events_added_by_series_sweep[]
markets_discovered  markets_open  markets_unopened  markets_paused
markets_closed  markets_other_status
markets_classified  markets_unknown
api_failures
native_game_markets_complete
```

`native_game_markets_complete` is true only when every event we were told
about was fetched in full. A market with an unrecognized status is
**counted** (`markets_other_status`), never dropped.

### The two coverage axes are never merged

`native_game_markets_complete` says nothing about combo markets, and
`multivariate_market_coverage` claims only `eligible_legs_only`:

> Kalshi's CFB combo markets are **dynamically instantiated**, not
> pre-listed per game. Exactly three multivariate collections reference
> NCAAF events (`KXMVESPORTSMULTIGAMEEXTENDED-R`, `KXMVECROSSCATEGORY-R`,
> `KXMVECROSSCATEGORY-SHARD1-R`), each listing the same 1,018 NCAAF event
> tickers among 2,603 cross-sport legs, with `size_min: 2` and
> `functional_description`: *"The resulting market will only resolve to YES
> if every associated market resolves to YES."* An instantiated combo
> appears under a synthetic hash-suffixed event
> (`KXMVESPORTSMULTIGAMEEXTENDED-S20264E1F9411A8E`) whose legs are named in
> `custom_strike`, not under any single game's event.

**There is no endpoint that enumerates the combos available for one
physical game.** The catalog reports which of a game's events Kalshi lists
as combo-eligible legs, and states plainly that it does not cover the
rest.

---

## 6. Automation

`.github/workflows/kalshi-market-catalog.yml`:

| Window | Cadence |
|---|---|
| Thu 18:00 → Sun 06:00 UTC | every 30 min |
| rest of the week | every 6 hours |

~150 runs/week. A flat `*/10` schedule — what the retired collector ran —
spends ~1,000 runs/week to poll a slate that barely moves Monday to
Thursday, so this polls the window that matters *more* often than a
uniform hourly schedule while spending a fraction of the runs.

- **Commits only on change**, via the content fingerprint. No timestamp
  churn, so repository growth tracks real market movement.
- **Consumes no secret.**
- **Quiet.** A transient Kalshi failure does not fail the job: the catalog
  publishes with INCOMPLETE diagnostics, a warning annotation is emitted,
  and the next tick recovers. Only a genuinely unusable capture — zero
  games discovered, or the builder crashing — fails and notifies. The
  retired collector emailed a red run for every reset socket, which
  trained everyone to ignore it.
- **Refuses to publish an empty catalog** over a possibly-good one: zero
  games is far more likely to be a filter or API change than a genuinely
  empty college-football slate.
- Shares the repository's single-writer concurrency group
  (`research-data-write`, `cancel-in-progress: false`), which
  `tests/test_research_workflow_concurrency.py` enforces.

---

## 7. Known limitations

1. **Combo/multivariate markets are not enumerable per game.** Structural,
   not a gap in effort — see §5. Reported as `eligible_legs_only`.
2. **Full order-book depth is not captured.** Top-of-book size is
   published; depth costs one request per market (30,000+ markets) and is
   available on demand from `/markets/{ticker}/orderbook`.
3. **The horizon is a deliberate bound.** `--horizon-days 10` publishes
   the actionable slate. The exchange carries ~239 live CFB games at once;
   a full-season sweep is affordable manually (`--horizon-days -1`) but not
   every 30 minutes.
4. **`status=open` on the reconciliation sweep** means the series path sees
   only currently-open events. That is correct for a live menu, and the
   milestone path is the one that decides which games are published.
5. **Series selection for the reconciliation path is a heuristic**
   (ticker prefix, title, tags). A CFB series matching none of those would
   be missed by path 2 — but path 1 (milestones) does not depend on it, and
   every event is confirmed via `product_metadata.competition`.
6. **Classification is best-effort by design.** `unknown` markets are
   retained in full; a rising `totals.unknown_family_markets` is the signal
   that Kalshi launched something new, not an error.
7. **No settlement outcome tracking.** This is an inventory of what is
   currently offered, not a results ledger.
8. **Not a checkpointed observation series.** The catalog is a
   change-detected snapshot of the current surface. It is not a substitute
   for a pre-kickoff observation captured at a known offset from kickoff —
   see `docs/MODEL_RETIREMENT_2026.md` on closing lines.

---

## 8. Running and auditing it

```bash
# Build the catalog (no credentials needed)
python scripts/build_kalshi_cfb_catalog.py --out-dir data/live --print-summary

# Whole season, raw payloads embedded
python scripts/build_kalshi_cfb_catalog.py --horizon-days -1 --include-raw

# Fail loudly if the capture is incomplete (for a gate)
python scripts/build_kalshi_cfb_catalog.py --fail-on-incomplete

# Empirical completeness audit: rediscover independently and diff
python scripts/audit_kalshi_catalog_completeness.py            # stratified sample
python scripts/audit_kalshi_catalog_completeness.py --audit-all
```

The audit builds a **third** discovery path that touches neither
milestones nor `/events` (`/series` → `/markets?series_ticker`, unfiltered
by status), buckets it by physical game key, and diffs it against the
catalog across a stratified cross-section — marquee games by
`details.tier`, power-conference games by `details.conference`, G5/other,
FBS-vs-FCS by `details.division`, plus the thinnest game in each stratum.
Every discrepancy is re-fetched individually and explained rather than
counted, and it exits non-zero when any audited game is missing markets.

`.github/workflows/audit-kalshi-catalog.yml` runs it read-only from a
runner. Kalshi's own web and mobile clients render from these same public
endpoints — there is no separate website-only market feed — so independent
rediscovery, not a screenshot diff, is the honest operationalization of
"visible markets vs discovered markets".
