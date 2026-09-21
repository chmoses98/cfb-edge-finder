# The live execution workflow

```
repo  ->  compact factual game batches  ->  handicap 5-8 games
      ->  the repo prices EVERY eligible contract deterministically
      ->  sensitivity, dominance and correlation reduction
      ->  a small candidate artifact you can act on
```

**The repo finds and organises the entire market, supplies the facts, and
does all the arithmetic. You (or ChatGPT) handicap the games. Nothing
here has a view about a football game.**

---

## Operator instructions

Four commands. Repeat the last two per batch.

```bash
# 1. build the slate: catalog, factual context, shards, handicap batches
python -m cfb_edge_finder.execution prepare-live --refresh

# 2. see what is left to do
python -m cfb_edge_finder.execution batches

# 3. upload data/execution/latest/batches/early_b1.context.json with the
#    prompt below, then save the answer as handicaps.json

# 4. feed the answer back and read the candidates
python -m cfb_edge_finder.execution evaluate   --batch early_b1 --handicaps handicaps.json
python -m cfb_edge_finder.execution candidates --batch early_b1
```

The prompt for step 3, which is also what `prepare-live` prints:

> Run CFB. Handicap every game in this file ONCE, from its factual context.
> Return one handicap payload per game: a score distribution per period, an
> uncertainty block on each one, explicit probabilities (with ranges) for
> anything a distribution cannot price, and your thesis and strongest opposing
> case. Do not price individual contracts.

Then place whatever you want on Kalshi. The router records it
automatically; nothing is typed into this repository by hand.

Afterwards:

```bash
python scripts/cfb_postmortem.py --base-dir <accounting-data checkout> \
  --season 2026 --candidates data/execution/latest/candidates --unit 70
```

---

## What the reader is never asked to do

**ChatGPT writes nothing back**. It does not commit a handicap file, open a
branch, or visit the repository at all. You paste the payload into
`evaluate`; a round trip through GitHub is **not required to get bets**.

**There is no bet cap.** There is no cap and no target number: 0 candidates, 6
or 400, whatever survives evaluation and reduction. `--top` is a display
truncation for debugging a long list on a terminal — it is recorded in the
artifact when used and changes nothing about what was evaluated, priced or
reduced.

**No stake is computed and no order can be placed.** The repository sizes
nothing, and nothing in it can reach an order endpoint.

---

## Why it changed

The previous path was one artifact per kickoff window containing every
eligible contract, handed to one LLM pass:

```
repo  ->  <window>.analysis.json  ->  ChatGPT  ->  every good bet
```

Sharding had already solved FILE SIZE. Rebuilt from the retained
2026-09-19 catalog (commit `5db144e`):

| | |
|---|---|
| games | 115 |
| eligible contracts | 14,222 |
| shards | 4 (one per kickoff window) |
| analysis artifact size | 134-591 KB |
| largest shard | 40 games, 4,978 contracts |
| early window | 31 games, 3,710 contracts, 448 KB |

A 448 KB file is not slow to open. **31 games are slow to handicap, and
3,710 rows are slow to read through when 29 of every 30 of them are a
mathematical consequence of a distribution the reader has not written
down yet.** Bytes were never the constraint, so a byte budget could not
fix it.

Two other things were wrong. The artifact carried no team facts at all --
records, form, statistics, injuries, weather, rest -- so every handicap
started with the reader going and finding them, one game at a time, while
a kickoff window closed. And a handicap was a POINT ESTIMATE: five numbers
per period, propagated through closed-form algebra into several hundred
fair values that all looked equally sharp.

---

## The invariants, unchanged

Every mechanically eligible Kalshi contract is still accounted for.

```
contracts_discovered == contracts_eligible + every explicit mechanical exclusion
eligible_contracts   == evaluated_contracts + explicitly_unpriceable_contracts
unaccounted_contracts == 0
```

`prepare-live` exits 2 rather than publish a slate where the first does
not close. A game that cannot satisfy the second is `INCOMPLETE` and
names its exact missing tickers; `candidates` and `report` refuse (exit 3)
on any INCOMPLETE game.

Nothing is filtered before evaluation. The candidate artifact is a view of
the finished ledger, and every contract it does not show is in that ledger
with a terminal status.

---

## Handicap batches

The unit of work is a BATCH: five to eight games' worth of reasoning,
sized by a deterministic cost model, carrying facts and **zero contract
rows**.

`batching.game_cost` sums six published terms:

| term | why |
|---|---|
| base handicap | one game, handicapped once |
| extra periods | a first-half and a first-quarter distribution are two more football opinions |
| unusual families | a family with no closed-form route needs a judgement per family. MEASURED from the contracts' own `requires`, never guessed from a name |
| explicit tickers | ...and then a number per ticker. Capped |
| missing context | a factual domain the repo could not fill is research the reader has to do. This is why an enriched slate batches LARGER |
| market breadth | a mild size term, capped hard: ladder rungs are not the expensive part |

The weights were calibrated against the measured shape of the retained
slate (its games are bimodal: 79 with two periods and ~28 contracts, 36
with seven periods and ~300) so the cheapest costs about 0.7 and the most
expensive about 2.3. **They say nothing about any game's betting merit and
are not an input to any price.**

A game is never split. `MAX_GAMES_PER_BATCH = 8` is a hard ceiling on top
of the cost budget, for the part of a reader's load that is not in the
data. The byte budget survives as a safeguard.

---

## The handicap payload, `cfb_handicap_payload/2.0.0`

One score distribution per period prices every rung of every ladder in
that period. A game with 29 alternate spreads, 19 totals and 28 team
totals needs **one** distribution, not 76 hand-entered probabilities.

```jsonc
{
  "schema_version": "cfb_handicap_payload/2.0.0",
  "game_key": "26SEP19LSUMISS",
  "teams": {"home": "Ole Miss", "away": "LSU"},
  "confidence": "medium",
  "thesis": "...",
  "opposing_case": "...",
  "period_distributions": {
    "full_game": {
      "home_mean": 27.5, "away_mean": 24.0,
      "home_sd": 10.5, "away_sd": 10.0, "correlation": 0.12,
      "uncertainty": {
        "margin_points": 3.0,     // how far the MARGIN could be out
        "total_points": 5.0,      // ...and the TOTAL, independently
        "sd_scale_low": 0.9,      // how much narrower the real spread could be
        "sd_scale_high": 1.2      // ...and how much wider
      }
    },
    "first_half": {"...": "..."}
  },
  "explicit_probabilities": {"KXNCAAF1H-26SEP19LSUMISS-TIE": 0.09},
  "explicit_probability_ranges": {"KXNCAAF1H-26SEP19LSUMISS-TIE": [0.06, 0.13]},
  "data_quality": {"confidence_ceiling": "medium", "gates": []},
  "low_confidence_families": [],
  "declared_unpriceable_families": []
}
```

### Two rejections that are not negotiable

- The payload must echo `teams.home` and `teams.away`. A home/away flip
  inverts every spread, moneyline and team total in the game at once and
  leaves every count perfectly balanced.
- A payload whose `packet_hash` does not match the packet on disk is
  rejected unless `--allow-hash-mismatch` is passed.

### Schema 1.0.0 is still accepted

It prices everything exactly as it always did. It simply cannot produce a
robust recommendation, because nothing in it says how wrong it might be.

---

## Uncertainty, and why a point estimate cannot be "robust"

**Do not trust a single point projection.** That is the whole reason 2.0.0
exists.

The region is a box on four independent axes, each a thing a handicapper
can actually reason about. Margin and total are perturbed ORTHOGONALLY --
being wrong about who is better is not the same mistake as being wrong
about how many points get scored, and moving them together would
understate a whole class of error.

The evaluator prices every contract at the base case AND at every corner
of that box, and reports:

| status | meaning |
|---|---|
| `robust_positive_ev` | the fee-adjusted edge clears the operator's bar at EVERY corner |
| `sensitive_positive_ev` | base case clears it; somewhere in the region it does not, but the edge never goes negative. **Also the ceiling for any handicap with no stated region** |
| `not_robust` | part of the region takes the edge at or below breakeven. Excluded from candidates |
| `below_required_edge`, `zero_or_negligible_edge`, `negative_ev` | priced, counted, not a candidate |
| `too_uncertain` | the handicap flagged this family low-confidence |
| `unpriceable_from_handicap` | no route from the supplied distributions and no explicit probability |
| `unpriceable_insufficient_data` | a measured data-quality gate closed this family |
| `non_executable`, `invalid_semantics` | mechanically unbettable |

### The bound is named honestly

For moneyline, spread, total and team total the fair probability is
monotone in every axis, so the corner minimum IS the region minimum:
`sensitivity_bound: exact_corner_extremum`. A winning-margin BAND is a
difference of two tails and is not monotone, so its corners are a sample:
`grid_extremum`. An explicit probability with a stated range is
`stated_range`. One with no range is `not_tested`, and `not_tested` can
never be robust.

### Measured on the retained slate

Same 14,222 contracts, same reconciliation, one handicap set, run twice:

| | with a stated region | same handicap, no region |
|---|---|---|
| `robust_positive_ev` | 5,192 | **0** |
| `sensitive_positive_ev` | 542 | 7,376 |
| `not_robust` | 1,642 | 0 |
| unaccounted | 0 | 0 |

The region is what separates them, and nothing else does.

---

## The factual context layer

The packet now carries facts, each with its own source, observation time
and quality. Free and keyless: ESPN's public scoreboard and core
endpoints, Open-Meteo. CFBD is optional and keyed.

Twelve domains: `identity`, `records`, `rest_and_travel`, `recent_results`,
`scoring`, `efficiency`, `situational`, `turnovers_and_pace`,
`availability`, `environment`, `coaching`, `market_reference`.

**It forms no opinion.** Every value is something a source published or
arithmetic on published values — a division, a subtraction, a win-loss
record. There is no rating, no projected score and no fitted coefficient,
and `tests/test_execution_context.py` asserts the absence structurally.

`scoring.opponent_adjusted` is `false` as a FIELD, not only in a
docstring: a reader who assumes a schedule adjustment would be reading a
number this repository has never computed.

### Missing is a value

An empty ESPN injury list is `quality: fresh` and the observation "no
injuries listed". An unreadable one is `quality: missing` and the
observation "we could not look". **Those are different facts and the
difference is worth a touchdown on a college line.** The collector never
lets the second become the first.

### Freshness is measured against the slate clock

Not against the collection clock. A context file that recorded `fresh`
when written and was read two days later would report two-day-old weather
as current. Budgets are per domain: 6 hours for weather, 36 for records,
a week for a venue.

### Data quality gates a FAMILY, not a sport

A team with fewer than `MIN_GAMES_FOR_TEAM_TOTAL` observed scoring games
makes that game's team-total families `unpriceable_insufficient_data` --
counted, named, reconciled, and never given a probability. It fires on a
COUNT OF OBSERVED GAMES, so an FCS team with a full season passes and an
FBS team in week one does not. It is not an FCS rule.

A handicapper may LOWER the data quality and may not raise it: the ceiling
and the gates are statements about what was fetched, and the evaluator
merges the payload's block with the measurement, taking the lower ceiling
and the UNION of the gates.

---

## Deterministic full-market pricing

Football scores are integers, so every contract asks about `P(X >= k)`:

```
"over 16.5"        greater, half-integer   -> X >= 17  -> cut at 16.5
"wins by over 4"   greater, integer        -> X >= 5   -> cut at 4.5
"1+ overtimes"     greater_or_equal, 1     -> X >= 1   -> cut at 0.5
```

A blanket 0.5 continuity correction on a strike that is already a
half-integer shifts every Kalshi rung by a full point.

EV on a Kalshi binary, fee charged on entry only:

```
EV = fair*(1 - entry) - (1 - fair)*entry - fee = fair - entry - fee
```

**Both sides are evaluated on their own quoted ask and their own fee.**
Buying NO costs `no_ask`, never `1 - yes_ask` — that complement is the NO
*bid*, the wrong side of the spread, and the two disagreed on all 15,444
live contracts the catalog measured. The fee-model allowlist is preserved:
`catalog/fees.py` cannot compute it, the contract is `unsupported_fee_model`
and never eligible.

### What it will not invent

Ties, first-TD, stat props, overtime, 1H/FT doubles, and any family Kalshi
ships tomorrow. Explicit probability keyed by ticker, or
`unpriceable_from_handicap`. **No contract silently disappears.**

---

## Candidate reduction

After exhaustive evaluation, redundant expressions are reduced. Three
nested groupings:

| key | meaning |
|---|---|
| EXPRESSION | (game, period, driver, direction, line, side) — the same contract twice |
| VIEW | (game, period, driver, direction) — one ladder, one direction |
| THESIS | (game, direction on the game) — everything that wins together |

Only the best member of a VIEW survives. Members of a THESIS all survive
and are grouped, because they are different bets that move together.

Comparison order, deterministic and total: fee-adjusted edge (outside a
tie band), then entry fee, then robustness, then worst-case edge, then
ticker. **There is no top-N.** Every removal names its survivor and a
reason from `exact_equivalent`, `dominated_duplicate`, `fee_dominated`,
`worse_robustness`, `inferior_expression`, `correlation_group_alternative`
— and lands in `<batch>.reduction.json` beside the artifact.

`--top` is a display truncation, is recorded in the artifact when used,
and changes nothing in the reduction.

---

## Staking guardrails

The repository sizes nothing and can place no order. What it does is make
correlated exposure impossible to miss: the candidate artifact carries
`exposure.by_game` and `exposure.by_thesis`, and states that two different
tickers are not diversification.

`LSU -2.5` and `LSU -6.5` are one opinion at two prices; the reduction
already collapses them. A spread and a team total on the same side are two
bets in one thesis group.

---

## Market disagreement

The market is not the answer key — finding where it is wrong is the point.
So disagreement is MEASURED and published, never obeyed.

Per game, over its priced contracts on the YES axis: the mean absolute and
the mean SIGNED `fair - implied`. The signed test is a flip detector: an
honest disagreement roughly cancels across the two sides of a total, while
a home/away flip, a period mix-up or a units error pushes every contract
the same way.

`normal` / `elevated` / `extreme` / `insufficient_sample`. **Extreme is a
prompt to re-check orientation, period and units before betting. It never
invalidates a handicap.**

---

## Resumability

| change | handicap | evaluation |
|---|---|---|
| nothing | reused | reused |
| quotes moved, same tickers | kept, still `complete` | invalidated |
| ticker set changed | kept, flagged `complete_needs_review` | invalidated |
| **material factual change** | **retired to `superseded_handicaps`, current one `needs_review`** | invalidated |

Material means `availability`, `environment` or `identity` — the facts a
handicapper reasons FROM. A refreshed yards-per-play is not a different
game, and an invalidation that fired on it would fire on everything.

A game whose handicap needs review is NOT reported complete even when its
arithmetic closes. The prior handicap is kept as history so a postmortem
can tell a handicap error from news that arrived after the opinion.

---

## Exit codes

| Code | Command | Meaning |
|---|---|---|
| 0 | any | done |
| 2 | `prepare-live` | reconciliation did not close, or the batches do not cover the slate |
| 4 | `prepare-live` | zero eligible contracts (usually a catalog older than the freshness bar) |
| 2 | `evaluate` | a payload was rejected (team mismatch, stale packet hash, unknown game) |
| 3 | `evaluate` | games in scope still have no handicap |
| 3 | `candidates` / `report` | the gate is shut: at least one game is INCOMPLETE |

---

## Known limits

- **Combos are not enumerable.** Kalshi's CFB parlays are dynamically
  instantiated and no endpoint lists them per game.
- **The fee is the trade fee.** Rounding fee and rebate depend on account
  balance precision and fill sequence, neither knowable without
  credentials. Published fees are a lower bound on the true net fee.
- **Home/away on a neutral or `vs`-titled game is a convention.** Kalshi
  lists the away side first; the packet labels that
  `home_away_confidence: convention`.
- **`--min-edge` has no empirical backing.** This repository has never
  validated a bar at which a CFB Kalshi edge is real, and none is claimed.
- **The batching weights are calibrated, not validated.** They were set
  against one slate's measured shape to produce batches in the intended
  range. They are a measure of handicapping work and nothing else.
- **`exact_corner_extremum` is exact for the monotone families only.** A
  margin band's bound is a grid minimum and says so.
- **The context collector could not be exercised against live endpoints**
  from the environment this was built in (egress blocked). Its parsers are
  tested against recorded payload shapes; the first scheduled slate run is
  its live proof, and it fails soft by construction.
