# The live execution workflow

```
repo  ->  <window>.analysis.json  ->  ChatGPT  ->  every good bet
```

**The repo finds and organises the entire market. ChatGPT does the
handicapping and evaluates every market. You get every good bet.**

---

## The invariant

Every mechanically eligible Kalshi contract for every game in the window
is in the file the handicapper opens:

```
eligible_contracts == contracts_in_analysis_artifact
unaccounted_contracts == 0
```

The builder counts the rows it actually wrote and **refuses to publish**
an artifact that falls short, so a compaction bug is a failed build rather
than a quiet omission.

Upstream of that there is a second identity:

```
contracts_discovered == contracts_eligible + every explicit mechanical exclusion
```

`prepare-live` exits 2 rather than publish a slate where that does not
close, and every exclusion is named, counted, and carried into the
artifact's own `reconciliation` block.

The optional verification path adds a third, for a handicap that has been
fed back into the repo by hand:

```
eligible_contracts == evaluated_contracts + explicitly_unpriceable_contracts
```

A game that cannot satisfy it is `INCOMPLETE` and names its exact missing
tickers; `report` refuses (exit 3) on any INCOMPLETE game. That path is
not required to get bets.

---

## What is NOT in this workflow

- **No pre-filter on attractiveness.** There is no "interesting market",
  no "likely edge", no top-N before evaluation. The only things removed
  before the handicap are objective mechanical failures (below), each one
  named and counted.
- **No model.** Every fair probability arrives from outside the
  repository in a handicap payload. `cfb_edge_finder.execution` imports
  nothing from `modeling`, `recommendation`, `decision` or `sizing`, and
  the retired projection model is not on this path. The one thing it
  reuses from `projections/` is the covariance algebra that turns two
  team-score distributions into a margin and a total — arithmetic applied
  to numbers a human supplied.
- **No stake, and no order.** `stake_placeholder` is a blank. Nothing in
  the package can reach a Kalshi order or portfolio endpoint, which
  `tests/test_execution_workflow.py` asserts structurally.
- **No claim of edge.** `--min-edge` is an operator input. This
  repository has never validated a bar at which a CFB Kalshi edge is
  real, and none is claimed.

---

## The live workflow

```
repo  ->  <window>.analysis.json  ->  ChatGPT  ->  every good bet
```

That is the whole round trip. **ChatGPT writes nothing back** — no
handicap file, no commit, no repo state, no second visit. You download one
file, upload it, and get the answer in the conversation.

1. **Generate the artifacts** — one command, or dispatch the
   **CFB Execution Slate** Action:

   ```bash
   python -m cfb_edge_finder.execution prepare-live --refresh
   ```

2. **Download the analysis artifact** for the kickoff window you are
   working:

   ```
   data/execution/latest/shards/early.analysis.json
   ```

   (Or, from the Action, the `cfb-analysis-artifacts` upload.)

3. **Upload it to ChatGPT with this prompt:**

   > Run CFB. Bankroll $1,400. Independently handicap every game in this
   > file, evaluate every available Kalshi market, and return every bet
   > you believe has positive EV. Do not use repo projections.

4. **ChatGPT returns every qualifying bet directly.** 0 bets, 6 bets, 100
   bets — whatever survives its own handicap. There is no cap and no
   target number anywhere in this workflow.

5. Move to the next window: `afternoon`, `evening`, `late`.

### The division of labour

| | does |
|---|---|
| **the repo** | finds and organises the ENTIRE market: discovery, mechanical disposition, compaction, sharding, reconciliation |
| **ChatGPT** | handicaps every game, forms its own fair probabilities, inspects every contract, returns every good bet |
| **you** | check the manifest, upload one file, size the bets |

The repo has no view about any game and computes no fair probability. The
projection model was retired from the live path in September 2026 and is
deliberately not trusted for betting decisions; nothing in the artifact
defers to it.

### The optional verification path

`handicap-template`, `evaluate`, `status`, `report` and the durable state
store all still exist, and they still enforce the coverage invariant
below. They are **not required to get bets** — they are there to re-check
a handicap arithmetically after the fact. Use them if you want the proof;
skip them entirely and the live workflow above is unaffected.

`report --top N` is a display truncation for debugging. It has no place in
the live workflow, never touches the ledger, and is off by default.

### Exit codes

| Code | Command | Meaning |
|---|---|---|
| 0 | any | done |
| 2 | `prepare-live` | reconciliation did not close; the slate is not usable |
| 4 | `prepare-live` | zero eligible contracts (usually a catalog older than the freshness bar). The slate and every exclusion are still written. |
| 2 | `evaluate` | a payload was rejected (team mismatch, stale packet hash, unknown game) |
| 3 | `evaluate` | the shard still has games with no handicap |
| 3 | `report` | the shard gate is shut: at least one game is INCOMPLETE |

---

## The analysis artifact

`<window>.analysis.json`, schema `cfb_execution_analysis/1.0.0`. One file
per kickoff window, self-contained.

```jsonc
{
  "how_to_use": [...],            // Stage A then Stage B, and "no cap"
  "do_not": [...],
  "model_projections": "absent",
  "reconciliation": {             // the artifact audits itself
    "games_discovered": 115, "games_included": 31,
    "contracts_discovered": 3710, "mechanical_exclusions": 0,
    "eligible_contracts": 3710,
    "contracts_in_analysis_artifact": 3710,
    "unaccounted_contracts": 0
  },
  "freshness": {...},             // capture age, freshest/oldest quote age
  "fee_model": {...},             // stated ONCE, not on 3,710 contracts
  "price_conventions": {...},     // stated ONCE
  "factual_data_not_in_artifact": [...],
  "games": [
    { "game_key": "...", "matchup": "LSU at Ole Miss",
      "game_context": { ... },    // STAGE A reads this
      "markets": {                // STAGE B reads this
        "game_spread": {
          "period": "full_game", "kind": "spread", "contracts": 29,
          "yes_means": "<team> wins the <period> by MORE than <line> points",
          "ticker_prefix": "KXNCAAFSPREAD-26SEP19LSUMISS-",
          "columns": ["t","team","line","yes_bid","yes_ask","no_bid","no_ask",
                      "yes_entry","yes_fee","yes_breakeven",
                      "no_entry","no_fee","no_breakeven"],
          "quote_age_s": 516240,
          "rows": [["LSU10","away",9.5,0.31,0.32,0.68,0.69,
                    0.32,0.015232,0.3352,0.69,0.014763,0.7048], ...]
        }
      }
    }
  ]
}
```

**`game_context` sits before `markets` on every game**, and the file's
keys are not alphabetised, so a reader meets the facts before the prices.
That is the two-stage flow — handicap first, then look at the market — and
it needs no round trip to enforce.

### Why a column table

A Saturday window is ~5,000 contracts. Repeating fourteen key names on
each of them triples the file for no information. Columns are named once,
rows are values, **every eligible contract still has its own row**, and
the builder counts the rows it wrote and refuses to publish if they do not
equal the eligible universe.

Compaction only ever removes repetition, never a contract:

| Hoisted | Because |
|---|---|
| fee model and its caveats | identical on all 14,222 contracts |
| price conventions | identical |
| "NO is the exact negation", "full ticker = prefix + t" | identical |
| `factual_data_not_in_artifact` | identical on every game |
| `quote_age_s` | stated once per family when all its rows share it |
| `ticker_prefix` | a spread ladder's 29 tickers differ only after it |

`yes_means` is deliberately **not** hoisted. It is the one line that
decides whether a row is read correctly, and a reader four thousand rows
deep should not have to hold a glossary in mind.

### What the artifact does NOT carry

No fair value, no win probability, no projected score or margin, no
rating, no ranking, no edge, no recommendation, no shortlist — asserted
mechanically in `tests/test_execution_analysis.py`, both as a field scan
over the artifact and as an import scan proving the modules that build it
cannot reach the model.

It also names the facts it lacks, at the top level, rather than being
quietly thin: team records, recent form, offensive/defensive statistics,
opponent-adjusted ratings, injuries, depth charts, weather, rest and
travel, coaching context. The live path is credential-free and Kalshi-only
by construction, so none of that is available to put there — and saying so
is what stops a reader assuming it was already accounted for.

### File size

Today's slate: 137–604 KB per window, one file per window. Past
`--max-analysis-bytes` (default 900,000) a window subshards into
`early_1.analysis.json`, `early_2.analysis.json`, … **splitting only
between games — never a game's contracts.** At `--max-analysis-bytes
250000` today's slate becomes nine files of 45–254 KB, still 14,222
contracts and 115 games with no game split.

---

## Mechanical dispositions

Every discovered contract gets exactly one, and every exclusion is
published with the contract's ticker and a reason:

| Status | Test |
|---|---|
| `eligible` | survived all of the below |
| `game_started` | kickoff has passed (or is inside `--min-seconds-before-kickoff`) |
| `market_closed` / `market_paused` / `market_unopened` | not tradeable, or `close_time` has passed |
| `stale_quote` | the capture is older than `--max-capture-age-minutes` |
| `missing_executable_price` | neither side is buyable (an ask of 0.00 or 1.00 is a sentinel, not an offer) |
| `unsupported_fee_model` | `catalog/fees.py` cannot compute the fee — fail closed |
| `mapping_failure` | team-scoped but no team resolves; or no kickoff |
| `duplicate` | the ticker was already dispositioned in this capture |
| `unsupported_market_semantics` | no title, subtitle or rules: YES cannot be stated |
| `not_game_scoped` | season-level inventory, no physical game |

**Not knowing how to price something is not on this list.** An unfamiliar
family stays eligible and becomes a counted `unpriceable_from_handicap`
after the handicap, not a disappearance before it.

### Why staleness is a property of the capture

A pregame Kalshi contract nobody has traded for two days carries a
two-day-old `updated_time` and a perfectly live book. Keying staleness on
that would exclude most of a Saturday slate for no reason. What actually
goes stale is *our observation* of the book, which is one timestamp for
the whole capture. `--max-quote-age-minutes` still exists for a
per-contract gate and defaults to off.

---

## The handicap payload

`cfb_handicap_payload/1.0.0`. One score distribution per period prices
every rung of every ladder in that period — a game with 29 alternate
spreads, 19 totals and 28 team totals needs **one** distribution, not 76
hand-entered probabilities.

```jsonc
{
  "schema_version": "cfb_handicap_payload/1.0.0",
  "game_key": "26SEP19LSUMISS",
  "packet_hash": "...",                       // what this handicap was built against
  "teams": {"home": "Ole Miss", "away": "LSU"},
  "confidence": "medium",
  "thesis": "...",                            // carried verbatim into the report
  "opposing_case": "...",
  "period_distributions": {
    "full_game":   {"home_mean": 27.5, "away_mean": 24.0,
                    "home_sd": 10.5, "away_sd": 10.0, "correlation": 0.12},
    "first_half":  {"...": "..."},
    "first_quarter": {"...": "..."}
  },
  "explicit_probabilities": {
    "KXNCAAF1H-26SEP19LSUMISS-TIE": 0.09,     // YES probability, keyed by full ticker
    "KXNCAAFFIRSTTDTEAM-26SEP19LSUMISS-LSU": 0.46
  },
  "low_confidence_families": [],
  "declared_unpriceable_families": []
}
```

What it derives structurally: moneyline, spread and every alternate rung,
total and every alternate rung, team totals, winning-margin bands — each
for whichever period the contract belongs to.

What it will **not** invent:

- **Ties.** An exact-margin point mass is not reliably derivable from a
  continuous margin distribution. A normal approximation overstates a
  full-game tie by roughly 40x and understates a first-quarter tie by
  roughly 3x. Ties price from an explicit probability or not at all.
- **First-TD, stat props, overtime, 1H/FT doubles, and any family Kalshi
  ships tomorrow.** Explicit probability, keyed by ticker, or
  `unpriceable_from_handicap`.
- **A period you left blank.** Every contract needing it is unpriceable.
  Returning the template untouched is a valid answer that yields a
  COMPLETE game with zero priced contracts.

### Two rejections that are not negotiable

- The payload must echo `teams.home` and `teams.away`. A home/away flip
  inverts every spread, moneyline and team total in the game at once and
  leaves every count perfectly balanced — the echo is what catches it.
- A payload whose `packet_hash` does not match the packet on disk is
  rejected unless `--allow-hash-mismatch` is passed. (The handicap is
  about the game and usually survives a price move; the flag exists for
  exactly that case, and it is an explicit choice.)

---

## Pricing arithmetic

Football scores are integers, so every contract asks about `P(X >= k)`:

```
"over 16.5"        greater, half-integer   -> X >= 17  -> cut at 16.5
"wins by over 4"   greater, integer        -> X >= 5   -> cut at 4.5
"1+ overtimes"     greater_or_equal, 1     -> X >= 1   -> cut at 0.5
```

A blanket 0.5 continuity correction applied to a strike that is already a
half-integer shifts every Kalshi rung by a full point — a systematic
mispricing of the whole ladder in one direction.

EV on a Kalshi binary, where the fee is charged on entry only:

```
EV = fair*(1 - entry) - (1 - fair)*entry - fee = fair - entry - fee
```

Both sides are evaluated on their own quoted ask and their own fee.
Buying NO costs `no_ask`, never `1 - yes_ask` — that complement is the NO
*bid*, the wrong side of the spread, and the two disagreed on all 15,444
live contracts the catalog measured.

---

## Resumability

Each game's state file records the packet hash and the eligible-ticker
hash it was produced against. On reload:

| Change | Handicap | Evaluation |
|---|---|---|
| nothing | reused | reused (no re-pricing) |
| quotes moved, same tickers | kept | **invalidated** — every price moved under it |
| ticker set changed | kept, flagged `complete_needs_review` | **invalidated** |

`status` prints where to resume; `evaluate` re-runs only what is not
already valid, and says how many games it reused.

---

## Evaluation statuses

Priced (counted as `evaluated`): `positive_ev`, `negative_ev`,
`zero_or_negligible_edge`, `below_required_edge`, `too_uncertain`.

Unpriced (counted as `unpriceable`): `unpriceable_from_handicap`,
`non_executable`, `invalid_semantics`.

There is no third bucket. Anything else would be `unaccounted`, and
`unaccounted > 0` makes the game INCOMPLETE and names the exact missing
tickers.

---

## The report (optional path)

Not part of the live workflow — the live workflow ends when ChatGPT
answers. This is what `report` produces if you run the verification path,
and it is a **view of the ledger**, never a separate dataset:

```
Eligible contracts:      3710
Priced/evaluated:        3624
Explicitly unpriceable:     86
Unaccounted:                 0

Positive-EV contracts:      41
Passed correlation review:  35
Final bets selected:        35     <- every survivor; no cap
```

The correlation review is deterministic: two contracts that are the same
view in different clothes (YES on "home by more than 6.5", NO on "away by
more than 5.5") reduce to the same `(game, period, driver, direction)`
and only the highest fee-adjusted edge survives. The rest are published
as `dominated_duplicates`, not dropped.

Every final bet carries the market, side, executable price, implied and
fair probability, raw and fee-adjusted edge, the handicapper's own
confidence and thesis verbatim, a deterministic statement of why that
market expresses the thesis, the strongest opposing case as supplied, and
an empty `stake_placeholder`.

**There is no bet cap.** `final_bets_selected` is every survivor of the
correlation review unless you pass `--top N`, which exists for reading a
long list on a terminal and changes nothing in the ledger.

---

## Known limits

- **Combos are not enumerable.** Kalshi's CFB parlays are dynamically
  instantiated and no endpoint lists them per game; the catalog says so
  and this workflow inherits that limit. Per-game combo-eligible event
  tickers are reported, the combos themselves are not.
- **The fee is the trade fee.** Rounding fee and rebate depend on account
  balance precision and fill sequence, neither knowable without
  credentials. Published fees are therefore a lower bound on the true net
  fee, which the artifact states.
- **Home/away on a neutral or `vs`-titled game is a convention.** Kalshi
  lists the away side first; the packet labels that
  `home_away_confidence: convention` so a reader can overrule it.
- **`--min-edge` has no empirical backing.** See above. It applies only
  to the optional verification path; the live workflow has no threshold of
  its own, because the judgement is ChatGPT's.
- **The artifact carries no team-level facts.** Records, form, statistics,
  injuries, depth charts and weather are not in the repo's live path
  (credential-free, Kalshi-only), so ChatGPT must supply them from its own
  knowledge. The artifact names the gap explicitly rather than implying
  coverage it does not have. This is the single biggest limit on how
  thorough the handicap can be.
