# Market Expression & Correlation Framework

How related Kalshi contracts on one CFB game relate to each other, and
what each executable expression of the same event actually costs.

**Research-only.** This layer produces structural facts (which contracts
settle together) and arithmetic facts (what each costs after fees). It
selects nothing, ranks nothing, tiers nothing, and sizes nothing.

## 1. The grouping hierarchy

```
game_group              one CFB game
  dimension_group       one latent football quantity
    equivalence_group   one terminal truth condition
      expression        one executable side of one ticker
```

The levels are deliberately distinct:

- `Team A -3.5` and `Team A -7.5` share a **dimension** (the same final
  margin decides both) but are **not the same event**.
- `Team A wins YES` and `Team B wins NO` **are the same event**, expressed
  two ways.

Collapsing those two ideas is exactly the mistake that makes four
correlated positions look like four independent theses.

### Dimensions

| Dimension | Families | Why |
|---|---|---|
| `MARGIN` | moneyline **and** spread | A moneyline *is* the margin contract at threshold 0 (`home_margin > 0`). Putting it in a separate dimension would hide that a team's moneyline and that team's spread rungs move together. |
| `TOTAL` | total | Combined final score. |

`WINNER` survives in the enum only as a label for winner-specific
diagnostics; no family maps to it, because the winner is not a separate
latent quantity from the margin.

## 2. Exact equivalence — proved, never assumed

Two contracts are declared equivalent **only** when the persisted
semantics (family, team/side, threshold, operator) imply identical
settlement truth conditions under `research/settlement.py`'s actual
rules. Anything short of that is `EQUIVALENCE_UNRESOLVED` and is excluded
from comparison rather than guessed at.

### The moneyline pair

`settle_market` computes `actual_winner = HOME if home_margin > 0 else
AWAY`, and a moneyline settles YES iff its own team is that winner. Every
possible final score — **including a 0 margin, which that rule assigns to
AWAY** — falls to exactly one side, so the sample space is partitioned
with no gap:

```
home wins  <=>  home_margin > 0        (home ticket YES  ==  away ticket NO)
away wins  <=>  home_margin <= 0       (away ticket YES  ==  home ticket NO)
```

Both are written in the same canonical margin language as the spread
rungs, so the equivalence falls out of the shared notation rather than a
special case.

This is a property of the **settlement rule, not the model.** The model's
two winner probabilities need not sum to 1 — see §7 — and that
discrepancy has no bearing on whether the *contracts* are equivalent.

### What is deliberately *not* claimed

Cross-team spread equivalence. `away margin > u` is `home margin < -u`;
the complement of `home margin > t` is `home margin <= t`. For integer
scores and half-point lines these coincide only for specific `(t, u)`
pairs that do not arise in the observed ladders. Rather than encode a
fragile arithmetic special case, those pairs are classified
nested-same-dimension. **Under-claiming equivalence is the safe failure
mode.**

## 3. Correlation taxonomy

Structural and logical — deliberately **not** estimated from settled
outcomes, of which this corpus currently has none.

| Class | Meaning |
|---|---|
| `EXACT_EQUIVALENT` | provably identical settlement conditions |
| `SAME_MARGIN_DIMENSION_NESTED` | both read the final margin |
| `SAME_TOTAL_DIMENSION_NESTED` | both read the combined score |
| `SAME_GAME_DIFFERENT_DIMENSION` | same game, different latent quantity |
| `UNRELATED_GAME` | different games |
| `EQUIVALENCE_UNRESOLVED` | semantics too incomplete to classify |

## 4. Fee-aware break-even

A contract pays exactly $1.00 if its condition holds. Buying one at
executable price `p` with entry fee `f` costs `p + f`, and

```
EV(q) = q·(1 − p − f) + (1 − q)·(−(p + f)) = q − (p + f)
```

so

```
fee_adjusted_break_even_probability = p + f
```

Using the raw price as the break-even probability — the naive reading of
an order book — **understates the required probability by the whole fee.**

An unknown fee yields an unknown cost, never a silent zero: substituting
zero would make an expression look cheaper than it can be transacted.

`research_probability_surplus` = model probability − fee-adjusted
break-even. Deliberately **not** called an edge, a betting edge, or an
expected value: this milestone is pre-recommendation, and a name implying
a decision would be the first step toward making one.

## 5. Equivalent events are not equivalent prices

Two expressions of the same event routinely cost very different amounts,
because each side's ask is quoted independently. **The live corpus shows
this emphatically**: median `yes_ask + no_ask` on a single ticker is
**1.24**, 61% of contracts exceed 1.20, and one ticker quotes 0.75 / 0.91
(sum 1.66). Those are wide, thin preseason books.

`ECONOMICALLY_DOMINATED_EQUIVALENT` flags an expression that settles on
the same event, pays the same $1, and costs strictly more all in. It is a
statement about **arithmetic**, not desirability: it holds regardless of
whether either expression is worth holding, and is determined without
reference to any settled outcome.

`lowest_break_even_expression` is a read-only property reporting which of
several identical payouts costs least. That is a fact about prices, not a
suggestion to hold any of them.

> **Caveat.** Dominance is computed from captured **top-of-book asks at a
> single instant**. It does not establish that either side is fillable at
> size, that the quote was fresh, or that acting on it is possible.

## 6. Ladder structure

Every rung of one team's spread ladder reads the same number at a
different threshold, so the events are strictly nested:

```
{margin > 27.5} ⊂ {margin > 13.5} ⊂ {margin > 1.5}
```

- **`MODEL_MONOTONICITY_VIOLATION`** — model probability *rises* with the
  threshold. Not a judgement call: the harder event is a strict subset,
  so this contradicts the model's own distribution.
- **`MARKET_LADDER_INCOHERENCE`** — the harder rung is quoted more
  expensively. **Recorded, never repaired.** A quoted ask can be stale,
  thin or wide, and rewriting it would destroy the evidence that the
  market looked like that at capture time.
- **`DUPLICATE_THRESHOLD`**, **`INCONSISTENT_SEMANTIC_OPERATOR`**,
  **`IMPOSSIBLE_THRESHOLD`** — parser/structure problems.

An incoherent pair of asks is **not an arbitrage claim.** Acting on it
would require buying one rung and selling (or buying the NO of) another,
at size, simultaneously, with fees on both legs — none of which two
top-of-book asks establish.

## 7. Model tie mass

The model's two winner probabilities for a game often sum to slightly
**less than 1** (observed ~0.977–0.990). The shortfall is simulated mass
on an exact 0 margin, which the model treats as neither team winning
while settlement assigns it to AWAY.

Reported as `MODEL_TIE_MASS`, **not corrected**. It is a model
diagnostic; contract equivalence is derived from the settlement rule and
is unaffected.

## 8. Static price inconsistency

Restricted, deliberately, to the **complementary-pair** case: `E` and
`NOT E` jointly pay exactly $1.00 in every possible world, so if their two
cheapest all-in costs sum to less than $1.00 the shortfall is a
guaranteed arithmetic surplus — no distributional assumption, no
modelling.

General nested-ladder inequalities (YES on an easier rung plus NO on a
harder one) also admit guaranteed-payoff arguments, but they depend on
exact integer/half-point boundary arithmetic **and** on both legs being
simultaneously fillable at the quoted size. Top-of-book asks cannot
establish that, so it is **not implemented**. Correctness over novelty.

Both legs must have known fees; an unknown fee makes the claim unprovable
and returns nothing rather than a maybe. No order is ever placed, nothing
is sized, and depth and latency are not modelled.

## 9. Projection reuse

`CachedGameProjection.projection_snapshot_id` is a stable hash of the
frozen projection request. It exists so downstream analysis can **prove**
rather than assume that a game's moneyline, spread ladder and total ladder
came from the same simulated distribution — if two contracts on one game
carried different ids at the same timing label and model version, their
probabilities would not be mutually consistent and no ladder ordering
could be trusted.

Derived from the request rather than `id()` or a uuid, either of which
would differ between runs and make the check vacuous. Tests assert 40
contract lookups build the model once, and that ratings are fitted once
per `as_of` across games.

## 10. Same-game exposure primitives

For each contract: game, team exposure (in HOME/AWAY terms), dimension,
direction, threshold, equivalence-group key. A `NO` on "team T does well"
is recorded as exposure to the **opposing** team.

This is what a future risk layer would need to notice that

```
Team A ML YES,  Team B ML NO,  Team A -3.5 YES,  Team A -7.5 YES
```

are not four independent positions — the first two are literally the same
event, and all four move with one final margin. **Constructing or ranking
a set of positions is not part of this milestone.**

## 11. Correlation-aware counts

The report states raw contract count alongside game, dimension and
equivalence group counts, precisely so a contract count is never mistaken
for an independent-sample count. On the current universe, **751 contracts
arise from 86 games and 110 dimension groups.**

## 12. Scale

One pass over the ledger, then dict bucketing. No step rescans all
contracts per contract.

| Games | Contracts | Load | Group | Total | Ledger reads | Peak RSS |
|---|---|---|---|---|---|---|
| 100 | 7,400 | 0.10 s | 0.29 s | 0.39 s | 1 | 85 MB |
| 500 | 37,000 | 0.67 s | 1.69 s | 2.35 s | 1 | 239 MB |
| 2,000 | 148,000 | 2.76 s | 7.73 s | 10.48 s | 1 | 821 MB |

Linear in contract count.

## 13. Snapshot selection

The corpus holds many snapshots per ticker. Expression structure is a
statement about the market at **one instant**, so comparing an
`EARLY_OPEN` ask against a `T_30` ask on a sibling contract would
manufacture "dominance" out of the passage of time. One snapshot per
ticker is selected (latest by default) and the number collapsed is
reported.

## 14. Limitations

1. **Structural, not empirical.** The taxonomy is logical. No correlation
   coefficient is estimated from outcomes — there are no settled outcomes.
2. **Top-of-book only.** Every cost is a single quoted ask at one instant.
   Depth, staleness and fillability are not modelled.
3. **Dominance is arithmetic, not advice.** It says one identical payout
   costs more, nothing about whether either is worth holding.
4. **Ladder incoherence is not arbitrage.** See §6.
5. **Cross-team spread equivalence is not attempted.** See §2.
6. **Fee model is entry-side taker fees** from the verified schedule;
   settlement-side fees, maker fills and rebates are not modelled.
7. **One model version** is represented in the corpus so far.

## 15. Future integration

The equivalence groups, exposure primitives and correlation-aware counts
are the inputs a later qualification/risk layer would need. That layer —
choosing thresholds, constructing sets of positions, controlling
same-game exposure — is a **separate, later, deliberate mission**. Nothing
in this package can perform it: no function returns a "best" anything, and
structural tests fail if such a surface appears.
