# RUN CFB — the consumer contract

The exact procedure a ChatGPT betting session follows when the operator
says **RUN CFB**.

> **The repository NEVER provides the football projection or the betting
> recommendation.** It answers one question — *what can I bet on this game
> right now, at what price, under what settlement rules* — and nothing
> else. The handicap happens outside this repository, in the session.

---

## Step 0 — Preflight (never skip)

```bash
python scripts/run_cfb_preflight.py \
  --last-successful-run-at <completion time of the last SUCCESSFUL catalog run> \
  --last-run-fingerprint  <that run's content_fingerprint> \
  --game <GAME_KEY>
```

Exit codes are the verdict: **0** = fresh and every requested game usable,
**2** = `CATALOG STALE`, **3** = a requested game may not be used.

Get the run timestamp from **Actions → "Kalshi CFB Market Catalog" → latest
successful run**, or:

```
/repos/chmoses98/cfb-edge-finder/actions/workflows/kalshi-market-catalog.yml/runs?status=success&per_page=1
  -> .workflow_runs[0].updated_at
```

### 1 — Verify production freshness

**Do not read `captured_at` and call it fresh.** The catalog does not commit
when the market surface has not changed, so that timestamp is the last
*change*, not the last *observation*.

| Committed `captured_at` | Last successful run | Verdict |
|---|---|---|
| 6 hours old | 12 min ago, fingerprint matches published | **FRESH** — re-observed and unchanged |
| 12 min old | none / failed | **STALE** — the collector stopped; you are reading its last gasp |

Tolerance is 3× the cadence for the current window:

| Window | Cadence | Stale after |
|---|---|---|
| Thu 18:00 → Sun 06:00 UTC | 30 min | 90 min |
| Rest of the week | 6 h | 18 h |

Wide enough for GitHub's unpunctual scheduler, tight enough to catch a dead
collector within ~90 minutes of a slate.

**If STALE: stop. Do not handicap from a catalog that cannot be shown to be
live.**

### 2 — Read the current slate index

`data/live/cfb_market_catalog.json` (~1.4 MB, 239 games). Each entry carries
identity (`division`, `conference`, `tier`, `season_week`), its Kalshi
events, `market_count`, `family_distribution`, `completeness`, and
`markets_file`.

### 3 — Identify games that have not started

Use `kickoff` and `identity.milestone_status`. Live statuses are
`scheduled`, `created`, `time-tbd`, `inprogress`. A game already in progress
is still listed; decide deliberately whether in-play is in scope.

### 4 — Reject any game whose inventory is incomplete

**THE GATE:**

```
A GAME MAY NOT BE USED FOR HANDICAPPING OR BET SELECTION UNLESS
    completeness.native_game_markets_complete == true
```

If false (or absent — absent means *not proven complete*, never
permission):

- report the game **unavailable because market discovery is incomplete**;
- name the diagnostics: `failed_event_tickers`,
  `pagination_failed_event_tickers`, `events_fetched` vs
  `related_event_tickers_reported`, `api_failures`;
- **do not** pretend its menu is complete — a partial menu is
  indistinguishable from a thin one by inspection;
- **do not** recommend a bet from that game's partial catalog.

Slate level:

- `capture_complete == true` → the whole captured slate passed.
- `capture_complete == false` → **does NOT invalidate** games whose own
  `native_game_markets_complete` is true. It is a health signal, not a gate;
  one game's 429 must not discard 238 good menus.
- **Each game's own completeness is authoritative for that game.**

Enforced in code by `cfb_edge_finder.catalog.consumer`:

```python
from cfb_edge_finder.catalog.consumer import require_usable_game
verdict = require_usable_game(entry)   # raises IncompleteGameError with diagnostics
```

An `unknown` family does **not** block a game: that is a labelling gap, not
a discovery gap — the contract was captured in full.

### 5 — Open that game's detail file

`data/live/games/<game_key>.json` (14–701 KB). Same entry as the index, with
`markets[]` inlined.

### 6 — Inspect every available contract

Every contract carries what YES and NO mean:

`title`, `yes_sub_title`, `no_sub_title`, `rules_primary` (the settlement
condition), `rules_secondary` (postponement etc.), `settlement_sources`,
`status`, `market_type`, `strike_type`, `floor_strike`/`cap_strike`
(**points**, not money), `yes_bid`/`yes_ask`/`no_bid`/`no_ask` (**dollars
per $1 contract**), `yes_bid_size`/`yes_ask_size` (**contract counts**),
`volume`, `open_interest`, `liquidity_dollars`, `open_time`/`close_time`/
`occurrence_datetime`, and a `mechanics` block.

Read **all 19 families**, not just moneyline/spread/total — quarters,
halves, team totals, team-stat props, first-TD, overtime are all live
inventory. Note Kalshi encodes "3+ touchdowns" as `floor_strike: 2.5`.

### 7 — Handicap the game OUTSIDE the repository

Form your own view of the football game: talent, injuries, weather, rest,
scheme, motivation, market context. **Nothing in this repository helps you
here, and nothing in it should.**

### 8 — Use current contract prices to test for positive EV

`mechanics` gives you the market's own arithmetic and nothing more:

- `implied_probability_yes_mid` / `_yes_ask` / `_yes_bid` — the price
  restated in probability units (a $1 binary's price *is* its probability);
- `yes_bid_ask_spread_cents` — what crossing costs;
- `estimated_fee_per_contract_at_mid` + `maker_fee_applies` — Kalshi's
  quadratic fee, peaking near 50¢; `fee_is_taker_side_only` is true, so a
  resting order on a `quadratic_with_maker_fees` series costs more;
- `two_sided_quote` — whether anyone is showing both sides.

Time-to-close and quote age are **not** published as countdowns, on
purpose: they tick every capture, so publishing them rewrote all 239 game
files on every run even when no price had moved. Compute them yourself from
the absolute timestamps that *are* published — `close_time`,
`occurrence_datetime`, `updated_time` on the contract and `captured_at` on
the capture — against your own clock, which is the more correct number
anyway.

Compare **your** probability against the **executable** price (ask to buy
YES, bid to sell), net of fee. A one-sided book (`two_sided_quote: false`)
or a stale quote is a reason for caution, not a reason to assume a mid.

### 9 — Compare every relevant expression of the same handicap

One view is usually expressible several ways: game spread vs alternate
rungs vs team total vs first-half spread vs quarter lines. Ladders are
flagged `is_alternate_line: true`. Price them all; the cheapest expression
of the same thesis is frequently not the obvious one.

### 10 — Return only wagers whose price justifies the thesis

State, per recommendation: the contract ticker, exactly what YES means, the
executable price and size, the fee, your probability, and why the price
justifies it. If no price justifies the thesis, **return nothing** — that
is a correct outcome.

---

## What the repository will never do

No projection, no fair value, no expected score, no model edge, no Kelly
stake, no recommendation — asserted by test
(`test_catalog_schema_and_isolation.py`), not by convention. No bet
placement and no order-writing capability exists. No `CFBD_API_KEY` and no
Kalshi credential is used on the live path.

## Known limits a session must respect

- **Combo/parlay markets are not enumerable per game.** Kalshi instantiates
  them dynamically. `multivariate_market_coverage.claim` is
  `eligible_legs_only` and is deliberately never folded into
  `native_game_markets_complete`.
- **Top-of-book size only.** Full depth is one request per market from
  `/markets/{ticker}/orderbook`, not in the artifact.
- **~10-day horizon.** Games beyond it are not published.
- **78 of 15,314 contracts are `unknown`** — captured in full, just not
  labelled. Read their `rules_primary`.
