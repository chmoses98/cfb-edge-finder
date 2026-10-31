# RUN CFB — the consumer contract

The exact procedure a ChatGPT betting session follows when the operator
says **RUN CFB**.

> **The repository NEVER provides the football projection or the betting
> recommendation.** It answers one question — *what can I bet on this game
> right now, at what price, under what settlement rules* — and nothing
> else. The handicap happens outside this repository, in the session.

---

## RETRIEVAL — how a ChatGPT session gets the prices (read this first)

**Never fetch `data/live/cfb_market_catalog.json` or `data/live/games/<game_key>.json`
through the GitHub connector.** The catalog is ~1.5 MB and every marquee
game file is ~1.27 MB. GitHub's Contents API returns a file's metadata and
SHA with `"encoding": "none"` and an **empty `content`** for anything over
1 MB, and GitHub code search does not index files over 384 KB. Neither is
a sign the prices are missing; the files were too large to read through
that path.

Read the **execution index** instead. Every file in it is under 250 KB, its
paths are deterministic, and you never need code search.

| File | Size | What it gives you |
|---|---|---|
| `data/live/cfb_execution_index.json` | ~140 KB | capture timestamp + fingerprint, freshness rules, every slate, and one row per game with its quote-file paths |
| `data/live/execution/slates/<YYYY-MM-DD>.json` | ≤ ~75 KB | one US/Eastern slate date: each game's teams, kickoff (UTC and ET), window, usability, quote-file paths |
| `data/live/execution/games/<game_key>/quotes.json` | ≤ ~165 KB | **every contract** for that game: YES/NO bid & ask, quoted sizes, book state, buy-side executability and buy-side fee |
| `data/live/execution/games/<game_key>/manifest.json` | ~20 KB | full identity, events, completeness diagnostics, and the list of detail shards |
| `data/live/execution/games/<game_key>/events/<event_ticker>.json` | ≤ ~160 KB | the **verbatim** full contract records for one Kalshi event: `rules_primary`, `rules_secondary`, `settlement_sources`, full `fee` block, `mechanics` |

If a file ever grows past the 250 KB shard budget, it is split into
`quotes.part01of02.json`, `quotes.part02of02.json` (or
`<event_ticker>.part01of02.json` …). The index, slate and manifest always
list the exact paths, so **use the listed paths; never construct one.**

### The procedure (e.g. “RUN CFB — afternoon games”)

1. **Get the index.** Fetch `data/live/cfb_execution_index.json` from
   `chmoses98/cfb-edge-finder` (ref `main`). Note `capture.captured_at` and
   `capture.content_fingerprint`.
2. **Identify the slate.** The slate date is the **US/Eastern calendar date
   of kickoff**: today's ET date unless the operator names another. Find it
   in `slates[]` and fetch its `path`. **Never pick games by the date inside
   the game key.** Kalshi's key date is unreliable: Missouri at Ole Miss
   (15:30 ET, Oct 17) is keyed `26OCT16MIZZMISS`.
3. **Select the games.** Filter the slate's `games[]` by `window`:

   | `window` | Kickoff (US/Eastern) |
   |---|---|
   | `early` | before 14:00 (noon window) |
   | `afternoon` | 14:00–17:59 (the 3:30 window) |
   | `evening` | 18:00 or later (primetime, late night) |

   or by team name / `game_key` if the operator names games. Each row
   carries `away_team`/`home_team` when Kalshi's title says “AWAY at HOME”;
   for “A vs B” titles both are `null` (home field is not stated, so do not
   infer it from the order).
4. **Read each game's pointers.** `quote_files` (one or more paths) and
   `manifest`.
5. **Retrieve every quote file listed** for each selected game. Check, per
   game:
   - each file's `game_fingerprint` equals the slate row's
     `game_fingerprint` (a mismatch means the collector committed between
     your reads: re-fetch the index and slate, then retry);
   - the sum of `market_count` over the parts equals the row's
     `market_count`. Fewer rows than that means you do not have the full
     menu. Say so, and do not treat the game as complete.
6. **Verify freshness and completeness** (Step 1 and Step 4 below):
   - fetch `data/live/cfb_catalog_status.json` (500 bytes); its
     `content_fingerprint` must equal the index's `capture.content_fingerprint`,
     otherwise **INCONSISTENT**: stop;
   - check the latest successful `kalshi-market-catalog.yml` run **on
     `main`** (Step 0/1) and apply the staleness table;
   - a game is usable only if `usable == true`
     (`native_game_markets_complete`); otherwise report it unavailable with
     its `incomplete_diagnostics`.
7. **Inspect executable prices.** Per contract in `quotes[]`:
   - **buying YES** → price `yes_ask`, size `yes_ask_size`, fee
     `buy_yes_fee`, executable iff `buy_yes_executable`;
   - **buying NO** → price `no_ask`, size `yes_bid_size` (the NO ask *is*
     the YES bid book), fee `buy_no_fee`, executable iff
     `buy_no_executable`. **Never use `1 − yes_ask` as the NO price.**
   - read `book_state` first; `empty_book` / `bid_only` / `ask_only` are not
     two-sided markets.
   - `title` states what YES means. Before recommending a contract, open its
     event detail shard (`event_files[event_ticker]` in the quote file) and
     read `rules_primary` / `rules_secondary`.
8. **Return the inventory for independent handicapping.** Report what you
   retrieved per game (contracts and families), then follow Steps 7–10. Label
   every price **“captured at `<captured_at>`”**, never “live” or “current
   executable”. Freshness verification (step 6) says the collector is alive.
   It does not make a captured quote a live order-book quote, and a quote can
   move after capture. Confirm on Kalshi before placing any order.

### When a retrieval fails

**One failed lookup never means “prices unavailable”.** Work down this list
before reporting anything:

| Symptom | Do this |
|---|---|
| A file returns empty `content` / `encoding: none` | You fetched an oversized legacy file. Use the execution-index path for the same game instead. |
| A fetch errors or times out | Retry the same path once (or twice). Then try the same path at an explicit `ref` of `main`. |
| The slate file fails | Use `games[]` in `cfb_execution_index.json`, which carries the same game rows with `slate_date`, `window` and `quote_files`. |
| The index fails | Fetch `data/live/execution/slates/<today ET>.json` directly. The path is deterministic. |
| A quote file fails | Retry. Then use the game's `manifest.json` → `detail_files[]`: the event shards together hold every contract with full prices. |
| `game_fingerprint` mismatch between files | The collector committed between your reads. Re-fetch the index and slate, then the quote files. |
| A game is not in the slate | Check the adjacent ET date's slate (late kickoffs, mis-keyed dates) and the index's `games[]`. Only then report the game as not listed by Kalshi in this capture. |
| Run history (Actions API) is not reachable from the connector | Proceed, but state **freshness UNVERIFIED**: prices captured at `captured_at`, collector liveness not confirmed. Do not call the quotes current. |
| Code search finds nothing | Expected. Do not use search. Every path is in the index. |

Report “prices unavailable” for a game only when the index row, the slate
row, the quote files **and** the detail shards for that game have all
failed, and name each path that failed.

### Not to be used as the price source

- `data/live/cfb_market_catalog.json`, `data/live/games/*.json`: complete,
  but over 1 MB for marquee games (unreadable through the connector).
  Scripts and SIFT integrations still read them from disk.
- `app/latest/board.json`: not the complete Kalshi inventory.
- `kalshi-bet-router`: not required for any step.

### Verifying the retrieval path yourself

`scripts/simulate_connector_retrieval.py` retrieves a slate using only the
Contents API's default JSON response (the connector's access path) and
fails if any requested game's inventory comes back short:

```bash
python scripts/simulate_connector_retrieval.py --date 2026-10-17 --window afternoon
python scripts/simulate_connector_retrieval.py --date 2026-10-17 --game 26OCT17AUBUGA --details
```

The execution layer is rebuilt by every catalog run and verified
(`scripts/build_cfb_execution_index.py --verify-only`) before anything is
committed. See `docs/KALSHI_MARKET_CATALOG.md` §4.

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

Through the GitHub connector: `data/live/cfb_execution_index.json` and the
slate file it points to (see **RETRIEVAL** above). With local disk access
only, `data/live/cfb_market_catalog.json` (~1.5 MB, over the connector's
1 MB limit) carries the same per-game identity (`division`, `conference`,
`tier`, `season_week`), events, `market_count`, `family_distribution`,
`completeness` and `markets_file`.

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

### 5 — Open that game's contracts

Through the GitHub connector: every file in the game row's `quote_files`
(all contracts, executable quotes), and, for full contract text, the event
shards listed in `manifest.json` → `detail_files[]`. Those shards hold the
game file's market records verbatim. With local disk access only,
`data/live/games/<game_key>.json` (up to ~1.3 MB) has the same records
inlined.

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
- **Captured, not live.** Every price is a capture from the last run that
  changed the market surface. A session that cannot reach the Actions run
  history cannot prove the collector is alive, and must say so.
- **GitHub's scheduler is unreliable.** It creates only 2–8 of the catalog's
  `*/30` cron runs a day. The CFB Research Conductor's 5-minute loop now
  dispatches the catalog whenever a run is due (see
  `docs/KALSHI_MARKET_CATALOG.md` §6), so the cadence no longer depends on
  the cron. If the conductor's chain breaks, captures fall back to the
  sparse cron and `CATALOG STALE` during a slate becomes likely again. Each
  catalog run's summary lists missed capture windows
  (`scripts/catalog_schedule.py report`). STALE is still STALE: dispatch the
  workflow, never lower the bar.


## Post-Oct-22 wager risk audit (required for betting recommendations)

Before placing **stakes** or reporting any price as a calculated **bet-up-to**, also read [`docs/CFB_BET_RISK_PROTOCOL.md`](CFB_BET_RISK_PROTOCOL.md). Its factual retrospective is the Oct 22–30, 2026 Kalshi router ledger. It adds a recommendation-level risk review, same-game and cumulative-day gross exposure disclosure, amendment-aware loss reconciliation, and a prohibition on treating captured asks as fair-value price limits. It does **not** modify the authoritative Kalshi market-retrieval process, activate a model, or justify placing orders without a verified fee-adjusted value case. When a fair-probability range is not supportable, a matchup lean may be stated but must **not** be presented as a qualified positive-EV bet.

## ChatGPT full-slate handoff: no user uploads or manual CLI required

**This is a separate operator path from the terminal-only workflow in
`docs/LIVE_EXECUTION.md`.** When the operator says "RUN CFB -- afternoon",
ChatGPT must perform the complete retrieval and handicap **within the session**
rather than asking the operator to download `cfb-handicap-batches`, paste a
handicap JSON, or run `evaluate`/`candidates` on a laptop.

1. Query the repository's **latest successful `CFB Execution Slate` workflow
   run on `main`**, using the repository Actions runs collection. Verify that
   its `head_sha` and build time reflect the intended current source version.
   Fetch the run's artifact list and download **both**
   `cfb-handicap-batches` and `cfb-analysis-artifacts` through the GitHub
   connector. The connector downloads ZIPs directly into the ChatGPT runtime;
   inspect the extracted files there. Do not make the user upload them.
2. The context artifact contains all games in batches of 5–8; read **every**
   applicable batch. The analysis artifact contains the **complete market
   menu with both asks, sizes and fees**. Check batch-manifest game and
   eligible-contract reconciliation against the analysis artifact. The
   `cfb_execution_index.json` and listed per-game quote/event files remain
   the fallback, and are always valid for completeness checks.
3. **Select by actual kickoff, not arbitrary batch name.** For a request about
   "2 pm Central games", start with kickoff 15:00 Eastern (19:00 UTC
   during daylight time on Oct 31, 2026), and include the rest of the
   requested afternoon window if the operator says "afternoon slate".
   Games already kicked off cannot receive a new pregame recommendation.
4. **Audit factual histories before handicapping.** Historical scoreboard
   opponents must belong to that team's exact ESPN team ID, not a nickname
   such as "Tigers", "Bulldogs" or "Eagles". Reject any packet whose recent
   results include competing opponents on the same day or an implausible
   21-day scoring sample. Old artifacts created before this identity fix are
   *not* repaired by a source-code merge; use a post-fix build or independently
   verify/reconstruct those histories. Never price a wager from a corrupted
   scoring statistic.
5. Research opponent quality, QB/injuries, efficiency, tempo, weather, and
   scripts independently. Form an explicit, explainable probability or score
   distribution with uncertainty **without copying the market's implied
   probability as a fair estimate**. If insufficient evidence exists, leave
   the contract unqualified rather than filling it with a plausible guess.
6. Compare *all* relevant listed contracts against their proper buy-side
   ask + trade fee and payout condition. The repository's standalone
   `evaluate`/`candidates` CLI may be used where available, but **is not
   mandatory** for ChatGPT's in-session calculations: the terminal workflow
   is not a reason to stop at the research packet. Account for liquidity,
   independent game and correlated-thesis exposure. A calculated bet-up-to
   must be derived from a defensible fair-value range and uncertainty.
7. **Labels mean what they say:** a two-sided quote with a published
   `buy_yes_executable` or `buy_no_executable` field was executable
   *at capture*, not necessarily right now. Unless a new direct exchange
   request successfully returns the same buy-side price and sufficient
   size, report `captured reference`, not `live verified`. Continue the
   handicap and provide conditional prices instead of pretending there were
   no quotes or no betting opinions. Any unfounded probability is a football
   lean, **not** a qualified positive-EV wager.

**Output:** one complete game-coverage accounting plus one ranked betting
table, clearly separating qualified bets and conditional candidates. No
invented valuations, no fabricated live verification, no automatic orders,
and no pregame wagers after kickoff.
