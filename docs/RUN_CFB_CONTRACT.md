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
**2** = `CATALOG STALE`, **3** = a requested game may not be used, **4** =
`CATALOG INCONSISTENT` (see below). Only **0** permits handicapping.

Get the run timestamp and fingerprint from **Actions → "Kalshi CFB Market
Catalog" → latest successful run on `main`**, or:

```
/repos/chmoses98/cfb-edge-finder/actions/workflows/kalshi-market-catalog.yml/runs
  ?branch=main&status=success&per_page=1
  -> .workflow_runs[0].updated_at        # --last-successful-run-at
  -> .workflow_runs[0].head_sha          # which commit produced it
```

**`branch=main` is not optional.** Without it the API happily returns a
successful run from any branch — including a feature branch, including one
of your own. **A feature-branch workflow run must never certify the main
catalog**: it read a different commit, may have written to a different
place, and says nothing about whether the production collector on `main` is
alive. Omitting the filter is how a dead production schedule gets certified
by a green run that had nothing to do with it.

### 1 — Verify production freshness

**Do not read `captured_at` and call it fresh.** The catalog does not commit
when the market surface has not changed, so that timestamp is the last
*change*, not the last *observation*.

| Committed `captured_at` | Last successful run on `main` | Verdict |
|---|---|---|
| 6 hours old | 12 min ago, fingerprint **matches** published | **FRESH** — re-observed and unchanged |
| 12 min old | none / failed | **STALE** — the collector stopped; you are reading its last gasp |
| any | 12 min ago, fingerprint **disagrees** with published | **INCONSISTENT** (exit 4) — the run and the artifact describe different content |
| any | 12 min ago, no fingerprint supplied | **FRESH**, but corroboration was not supplied — stated as such in the output |

**A supplied fingerprint that disagrees fails closed.** It is worse
evidence than no fingerprint at all: it is positive evidence that the
committed artifact is not what the live run observed (a partial commit, a
run against another branch, a hand-edited file). That is never freshness,
no matter how recent the run, and `assess_freshness` checks it *before* it
looks at run age. Supplying no fingerprint is allowed — freshness then
rests on the run history alone, and the verdict says so out loud rather
than implying two signals agreed.

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

#### 8a — First: is there a book at all?

**Read `book_state` before you read any price.** A price field existing is
not the same fact as liquidity existing, and the difference is invisible
once a number is printed.

| `book_state` | Meaning | Live count |
|---|---|---|
| `two_sided` | positive quoted size on **both** the bid and the ask | 14,999 / 15,444 |
| `bid_only` | someone bids, nobody offers | 132 |
| `ask_only` | someone offers, nobody bids | 44 |
| `empty_book` | price fields present, **no quoted size on either side** | 269 |
| `no_quote` | no price fields at all | 0 |

**The 0/100 trap.** 269 live contracts are quoted **$0.00 bid / $1.00 ask
with zero size on both sides**. The midpoint of that is $0.50. It is
arithmetically correct and it is not a market probability — no market said
50%, the market said nothing. Those contracts also carry *positive volume
and open interest* (one had 29,913 OI and a $0.99 last price), so every
activity measure makes them look healthy. They are real traded contracts
whose book has emptied.

So:

- **`yes_mid`, `no_mid` and `implied_probability_yes_mid` are `null`
  unless `book_state == "two_sided"`.** `mid_is_published` says so
  explicitly. 445 of 15,444 contracts (2.9%) have no published mid.
- **`is_sentinel_full_width_book: true`** names the 0/100 shape directly.
- **A 100-cent spread is not evidence of a 50/50 market.** It is evidence
  there is no market. `yes_bid_ask_spread_cents` is still published
  precisely because it is that evidence.
- **Volume and open interest are history; quoted size is the offer.**
  Never infer executability from activity.
- `liquidity_basis` names the fields the flags were computed from
  (`yes_bid_size` / `yes_ask_size`). Kalshi publishes `no_bid_size` /
  `no_ask_size` as `null` on *every* contract, so NO-side size carries no
  signal — a binary's YES and NO sides are the same book.

**An empty book does not remove a contract from the menu.** Discovery
completeness and quote quality are different concepts; a contract with no
bid today may have one at kickoff. It is simply not price discovery until
liquidity appears.

#### 8b — Then: the price

`mechanics` gives you the market's own arithmetic and nothing more:

- `yes_bid_is_executable` / `yes_ask_is_executable` — whether that side
  has positive quoted size. **These outrank any midpoint.**
- `implied_probability_yes_ask` / `_yes_bid` — the published price restated
  in probability units (a $1 binary's price *is* its probability). Always
  present; pair each with its executability flag.
- `implied_probability_yes_mid` — only when the book is two-sided.
- `yes_bid_ask_spread_cents` — what crossing costs.
- `two_sided_quote` — **executable liquidity on both sides**, not merely
  two numbers being present.

Fees are a separate, self-describing `fee` block on each contract, because
what a fee figure *represents* matters as much as its value:

| Field | What it is |
|---|---|
| `model` / `multiplier` | the **effective** schedule — an event's `fee_type_override`/`fee_multiplier_override` if present, otherwise the parent series' |
| `source` | `event_override`, `series`, or `unavailable` — where those two values came from |
| `support` | `supported`, `unsupported_model`, or `metadata_unavailable` |
| **`model_trade_fee_at_yes_ask`** | **buying YES.** Kalshi's model fee at the price a YES buy actually executes at, rounded up to $0.000001 as the exchange rounds it |
| **`model_trade_fee_at_no_ask`** | **buying NO.** The same schedule at the *quoted* NO ask — never `1 − yes_ask` |
| `basis_yes_ask` / `basis_no_ask` | the two **executable** prices those figures were computed from |
| `model_trade_fee_at_yes_mid` / `basis_yes_mid` | the same schedule at the mid — non-executable market arithmetic, and `null` whenever the mid is |
| `executable_bases` / `non_executable_bases` | which bases you may act on, named, so you never have to infer it from a key |
| `per_contracts` | how many contracts the figures cover (the schedule is linear in contracts) |
| `maker_fee_applies` | `true` on `quadratic_with_maker_fees` (the resting side pays too), `false` on `quadratic`, **`null` when the schedule is unknown** |
| `formula` | the arithmetic used, or `null` when no fee could be computed |
| `is_net_fee` / `excludes` | always `false` / the four things it leaves out |
| `unavailable_reason` | why there is no number, when there is no number |

**Price the side you are actually buying:**

| Your wager | Compare your probability against | Plus |
|---|---|---|
| **buying YES** | `basis_yes_ask` (the YES ask) | `model_trade_fee_at_yes_ask` |
| **buying NO** | `basis_no_ask` (the NO ask) | `model_trade_fee_at_no_ask` |

**Neither midpoint is an executable price**, and **`1 − yes_ask` is not the
NO ask.** The two sides of a Kalshi binary mirror each other *across* the
spread — verified on all 15,444 live contracts:

```
no_ask == 1 − yes_bid          no_bid == 1 − yes_ask
```

So `1 − yes_ask` is the NO **bid** — the price you could *sell* NO at.
Using it to price a NO *buy* is not a rounding difference; it is the wrong
side of the spread, and it understates your cost by the full spread width.
The quoted `no_ask` and `1 − yes_ask` disagreed on **15,444 of 15,444**
live contracts, and the two side fees themselves differ on **15,057**.

If `no_ask` is absent, `model_trade_fee_at_no_ask` is `null`; an absent
price is not an invitation to invent one.

The quadratic schedule is symmetric about $0.50, so on a tight book the two
side fees are nearly identical — which is exactly why it is tempting to let
one stand for the other. On a wide book they are not: at a 90¢ YES ask
against a 30¢ NO ask, the NO fee is over **twice** the YES fee.

**It is a TRADE fee, not a net fee.** Kalshi's net fee is
`trade fee + rounding fee − rebate`, and the last two depend on your
balance precision ($0.0001 direct, $0.01 non-direct) and on a per-*order*
accumulator carried across fills. A public, account-agnostic catalog cannot
know any of that, so it publishes the component it can compute and names
the rest in `excludes` rather than implying a net cost.

**A `null` fee is a refusal, not free.** If `fee_type` or `fee_multiplier`
was missing, or the schedule is one this code does not model, the figures
are `null` and `unavailable_reason` says which. Missing metadata is never
defaulted to `quadratic` or to a multiplier of 1 — a plausible default
would hide the failure on exactly the series that differs. Treat a `null`
fee as unknown cost and price accordingly.

**Only two fee models are priced**, by exact name: `quadratic` and
`quadratic_with_maker_fees`. A future `quadratic_v2` or
`quadratic_special` produces `support: "unsupported_model"` and `null`
amounts, **not** today's formula applied to tomorrow's schedule. A shared
name prefix is not a shared formula.

Time-to-close and quote age are **not** published as countdowns, on
purpose: they tick every capture, so publishing them rewrote all 239 game
files on every run even when no price had moved. Compute them yourself from
the absolute timestamps that *are* published — `close_time`,
`occurrence_datetime`, `updated_time` on the contract and `captured_at` on
the capture — against your own clock, which is the more correct number
anyway.

Compare **your** probability against the **executable** price of the side
you are buying, net of that side's fee. A one-sided or empty book
(`two_sided_quote: false`) or a stale quote is a reason for caution, and
never a reason to fall back on a mid — on those books there is no
published mid to fall back on, by design.

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
