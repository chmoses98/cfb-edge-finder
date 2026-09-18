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
   "quadratic"`, `fee_multiplier: 1`), and is joined in per contract. An
   **event** may override it (`fee_type_override`,
   `fee_multiplier_override`), and the override wins. Markets themselves
   carry no fee keys at all — verified across the live CFB surface, see
   `docs/evidence/kalshi_fee_and_override_probe.txt`.

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

### Layout: an index plus one file per game

A live capture is **239 games and 15,312 contracts**. Every contract
carries its own settlement rules, which is not padding — it is the thing a
handicapper reads to know what the contract means. Inlined into one
document that measured **52 MB**, and stayed above 31 MB with compact
separators and every dispensable field stripped.

The mission's two requirements for the primary artifact — *compact enough
that ChatGPT can ingest it efficiently* and *retaining all betting-relevant
contract information* — cannot both hold in a single 15,000-contract file.
Splitting resolves it without dropping anything, because it matches how the
artifact is read: nobody asks "what can I bet across 239 games", they ask
"what can I bet on THIS game".

| Artifact | Live size | Contents |
|---|---|---|
| `data/live/cfb_market_catalog.json` | **0.71 MB** | Slate index: every game's identity, events, per-family counts, completeness, and a pointer to its detail file |
| `data/live/games/<game_key>.json` | 14–701 KB (median 150 KB) | That game's complete contract inventory |
| `data/live/cfb_markets_flat.json` | ~50 MB | Optional (`--flat`), off by default: one row per contract slate-wide |

Measured at live scale: the index is **59× smaller** than the monolithic
file. Re-publishing an unchanged slate rewrites **0** game files; a single
price move rewrites **exactly 1**. That is the difference between a commit
of a few KB and a commit of 52 MB, every thirty minutes, for the whole
season.

Detail files for games that leave the slate are **pruned** — otherwise
`games/` grows without bound and a stale file keeps advertising a finished
game's menu as current.

### `data/live/cfb_market_catalog.json` — the slate index

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
games[]:      game_key, title, kickoff, market_count, family_distribution,
              markets_file  <- "games/<game_key>.json"
              identity{source, milestone_id, milestone_status, league,
                       division, conference, tier, season_year, season_type,
                       season_week, main_game_event_ticker},
              events[]{event_ticker, series_ticker, title, sub_title,
                       competition, competition_scope, mutually_exclusive,
                       settlement_sources},
              completeness{...}

Each per-game detail file (`games/<game_key>.json`) is that same entry
with `markets[]` inlined in place of `markets_file`.
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
fee{...}  mechanics{...}
```

Detail files omit the raw Kalshi payload by default so they stay efficient
to read; every betting-relevant field is present as a named key.
`--include-raw` embeds the untouched payload, so nothing captured is ever
unreachable.

### Market mechanics

Published per contract. Everything here is derivable from the order book
alone and would be identical whoever was playing:

```
yes_mid  no_mid  yes_bid_ask_spread  yes_bid_ask_spread_cents
implied_probability_yes_mid / _yes_ask / _yes_bid / _last
two_sided_quote
```

Fees are **not** in this block — see below.

Every value is a pure function of the quote, with no dependence on when it
was computed. That is deliberate: clock-derived countdowns
(`quote_age_seconds`, `seconds_until_close`, `seconds_until_occurrence`)
were published in the first production build and caused **all 239 game
files to be rewritten on every run** even when no price had moved,
defeating the per-game change detection the split artifact exists for.
They carried no information — each is a subtraction of two absolute
timestamps already published — so they were removed rather than
special-cased. A consumer computes them against its own clock.

`implied_probability` is the price restated in probability units — which is
what a $1 binary contract's price already is. A one-sided book yields
`null` for the mid rather than an invented price; a price outside `[0,1]`
yields `null` rather than a clipped, confident-looking 100%.

`tests/test_catalog_schema_and_isolation.py` pins the exact key set of this
block, so a model-shaped field cannot be added to it quietly.

### Fees — `src/cfb_edge_finder/catalog/fees.py`

Fees live in their own module and their own published block, for one
reason: **what a fee number represents matters as much as its value**, and
the earlier versions of this code got that wrong twice.

**Kalshi's actual rounding rule.** Fees are six-decimal dollar amounts.
The trade fee is the model fee rounded up to the nearest **$0.000001** —
not to a cent ([fee rounding][fr]):

```
trade_fee     = ceil_6dp(model_fee)
aligned_change= floor_precision(revenue − trade_fee)
rounding_fee  = (revenue − trade_fee) − aligned_change
net fee       = trade fee + rounding fee − rebate        (≥ $0.00)
```

An earlier helper here rounded up to a whole cent. On the exchange's own
worked example that is a **2.75× overstatement** — $0.01 where the trade
fee is $0.003639 — which is not a rounding nicety on a 2¢ longshot.

**What is published, and what is deliberately not.** Only the trade fee
is account-independent. The rounding fee, the rebate and the per-*order*
fee accumulator all depend on the member's target balance precision
($0.0001 direct, $0.01 non-direct), which a public catalog cannot know. So
the `fee` block publishes the trade fee, sets `is_net_fee: false`, and
lists the four things it excludes by name. The rounding and rebate
mechanics *are* implemented, and are exercised against Kalshi's official
worked examples in `tests/test_catalog_fees.py`, to prove the rules were
read correctly — they are simply not published as though we knew an
account's net cost.

**The headline is the executable price.** `model_trade_fee_at_yes_ask` is
the fee a taker buying YES pays, because a YES buy executes at the ask.
`model_trade_fee_at_yes_mid` is retained as a market mechanic under a name
that cannot be mistaken for it. A mid-based fee presented as *the* fee
understates every taker order.

**Effective schedule, with event precedence.** The block publishes the
effective `fee_type`/`fee_multiplier` — the event's override if present,
otherwise the series' — plus `source` (`event_override` / `series` /
`unavailable`) so a reader can see which applied. The event object is
already in hand during capture, so honouring the precedence costs no extra
request. Live CFB carries **zero** overrides today (0 of 1,937 open
events; the keys are absent from the payload), which is precisely why it is
tested rather than assumed: nothing in production would notice if it broke.

**Two quadratic spellings.** `quadratic` and `quadratic_with_maker_fees`
(the latter on ~29% of live CFB markets, including the `KXNCAAFGAME`
moneylines). The taker fee is identical under both; `_with_maker_fees`
means the resting side is charged too, which `maker_fee_applies` reports.
Matching only the exact string `quadratic` published a null fee on nearly
a third of the menu — a defect the first production run on main exposed.

**Measured on the live surface**, not asserted from fixtures — the first
production run is what found `quadratic_with_maker_fees`, and a fee that
is null on a third of the menu passes every test written against a
fixture that lacks it. Section C of
`docs/evidence/kalshi_fee_and_override_probe.txt` builds the real catalog
and audits every published block:

| | |
|---|---|
| contracts carrying a fee block | 15,326 |
| computable at the ask | **15,326 / 15,326** (0 with an ask but no fee) |
| `support` | `supported` on all 15,326 |
| `source` | `series` on all 15,326 — **0 event overrides live** |
| effective model | `quadratic` 10,672 / `quadratic_with_maker_fees` 4,654 |
| effective multiplier | `1.0` on all 15,326 |
| `is_net_fee` | `false` on all 15,326 |

Three named live contracts, each recomputed by hand from the exchange's
formula and matching the published figure exactly:

```
KXNCAAF2HSPREAD-26SEP17SYRPITT-PITT10  ask 0.47  -> $0.017437   (old helper $0.02,  1.15x)
KXNCAAFSPREAD-26SEP18MIAWAKE-MIA21     ask 0.51  -> $0.017493   (old helper $0.02,  1.14x)
KXNCAAF1HFT-26SEP17SYRPITT-PITTSYR     ask 0.01  -> $0.000693   (old helper $0.01, 14.43x)
```

The longshot is the case that mattered: the retired helper rounded a
$0.000693 fee up to a whole cent, **14× the real cost**, on precisely the
contracts where a cent of assumed fee decides whether a price clears.

185 live contracts are quoted at an ask of $0.00 or $1.00. There the
quadratic term `P(1−P)` is zero, so the trade fee is correctly $0.00
while the mid-based figure reads $0.0175 — the sharpest available
demonstration of why the mid figure cannot be the headline: a consumer
reading it as *the* fee books a cost the executable order does not incur.

**Missing metadata fails closed.** A missing `fee_type` is not treated as
quadratic; a missing `fee_multiplier` is not treated as 1; a series whose
metadata request failed is not treated as a series without fees. Each
publishes `null` figures with a distinct `unavailable_reason`, and
`maker_fee_applies` is `null` rather than `false` when the schedule is
unknown. Every one of those defaults would be right in the ordinary case
and silently wrong in exactly the case a consumer needs warning about — a
data failure must not be published as a measurement.

[fr]: https://docs.kalshi.com/getting_started/fee_rounding

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

## 5b. Request cost

Measured on the live exchange (2026-09-17):

| | Requests | Catalog build + full audit |
|---|---|---|
| Per-event market fetching | ~14,000 | 10.5 min |
| Bulk series prefetch | ~400 | **3 min 40 s** |

Per-event fetching is the obviously-correct primitive, but ~239 live games
x ~29 events each does not fit inside a 30-minute cadence with a 30-minute
job timeout. Markets are bulk-loaded one series at a time and bucketed by
event, and the `/events` sweep the reconciliation path already needed is
reused as an event index (which also removes the per-event
`GET /events/{ticker}`).

The completeness guarantee is unchanged, because an index can answer
NEGATIVELY and that answer must never be trusted blindly. The bulk result
is used only for a **non-empty** bucket from a series that swept to
**completion**; an empty bucket, a series whose sweep died mid-chain, and
a series never swept at all each fall through to a direct per-event fetch.
Absence is always confirmed, never inferred.

`discovery.events_served_from_prefetch` and
`discovery.events_fetched_individually` are published per capture, so a
cheap run and an expensive one are distinguishable and a jump in
individual fetches -- meaning the bulk sweeps are degrading -- is visible
even when the menu comes out complete.

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
