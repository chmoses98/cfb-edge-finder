# The live execution workflow

**One question, and a refusal.**

> Before this shortlist existed, was every mechanically eligible Kalshi
> contract on every handicapped game explicitly evaluated against that
> game's handicap?

If the answer is not provably yes, no shortlist is produced.

---

## The invariant

For every handicapped game:

```
eligible_contracts == evaluated_contracts + explicitly_unpriceable_contracts
unaccounted_contracts == 0
```

A game that cannot satisfy that is `INCOMPLETE`, and an incomplete game
may not be represented as fully scanned anywhere. The shard-level gate is
the same rule applied to every game in the shard at once: `report`
refuses, exit code 3, and names the games that are not done.

Upstream of the handicap there is a second identity:

```
contracts_discovered == contracts_eligible + every explicit mechanical exclusion
```

`prepare-live` exits 2 rather than publish a slate where that does not
close.

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

## The five commands

```bash
# 1. discover, compact, shard, reconcile  (add --refresh to re-run Kalshi discovery first)
python -m cfb_edge_finder.execution prepare-live --refresh

# 2. the blank payloads for one kickoff window
python -m cfb_edge_finder.execution handicap-template --shard early

# 3. price EVERY eligible contract against the filled payloads
python -m cfb_edge_finder.execution evaluate --shard early --handicaps handicaps_early.json

# 4. where the slate stands, and where to resume
python -m cfb_edge_finder.execution status --shard early

# 5. the shortlist -- refuses until every game in the shard is COMPLETE
python -m cfb_edge_finder.execution report --shard early --top 6
```

Or one GitHub Action: **CFB Execution Slate** (`workflow_dispatch`). It
does step 1 on a runner with network access to Kalshi and uploads two
artifacts — `cfb-execution-slate` (everything) and
`cfb-handicap-briefs` (just the briefs, a few hundred KB).

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

## The artifacts

```
data/execution/latest/
  cfb_execution_slate.json        every game, every eligible contract, full economics
  shard_manifest.json             per-shard counts, byte sizes, hashes, reconciliation
  shards/<window>.json            the same contracts, one kickoff window       <- the evaluator reads this
  shards/<window>.brief.json      the same contracts, ~20x smaller, no prices  <- the handicapper reads this
  templates/<window>.handicap_template.json
  state/<game_key>.json           durable per-game progress
  state_index.json
  ledgers/<window>.json           EVERY eligible contract's disposition, winners and losers
  ledgers/games/<game_key>.json   one game's rows, used to resume without re-pricing
  reports/<window>.json|.txt      the shortlist, once the gate passes
```

### Why two files per shard

The full shard carries every price, fee, breakeven and quote timestamp —
several MB, and exactly what deterministic evaluation needs. The brief
carries the same contracts as a column table, with **no prices at all**,
and is what a handicapping session consumes.

Dropping prices from the brief is not only compaction. A handicap is
supposed to be independent of the market it will be compared against, and
a price column next to every rung is an anchor. The tests assert the two
files contain identical ticker sets, so the brief can never quietly
become a filtered subset.

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

## The report

The shortlist is a **view of the ledger**, never a separate dataset. It
separates, explicitly:

```
Eligible contracts:      3710
Priced/evaluated:        3624
Explicitly unpriceable:     86
Unaccounted:                 0

Positive-EV contracts:      41
Passed correlation review:  35
Final bets selected:         6
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
- **`--min-edge` has no empirical backing.** See above.
