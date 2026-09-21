# Postmortem — what the money did, and what it was attributed to

```bash
python scripts/cfb_postmortem.py --base-dir <accounting-data checkout> --season 2026
python scripts/cfb_postmortem.py --base-dir . --season 2026 \
    --candidates data/execution/latest/candidates --unit 70
```

Exit 0 on a report (FINAL or PARTIAL — both are real answers), 2 if the ledger
could not be read.

## The money comes from the ledger and only the ledger

`wagers/<season>.jsonl` and `settlements/<season>.jsonl` on the
`accounting-data` branch. Those rows came from the venue's own fill evidence
through this repository's own importer, which is the only thing in the system
that knows what actually happened.

**Accounting is never inferred from a recommendation.** A bet the analysis
loved and the owner did not place is not in the ledger and does not appear in
any total. A bet the owner placed that no artifact ever mentioned is in every
total, marked `not_recommended`. That asymmetry is the invariant: the ledger is
the truth and the artifacts are a commentary on it.

## What `--candidates` adds: cuts, and nothing else

Pointing it at a slate's candidate artifacts lets the same realised profit and
loss be read by:

robustness tier · handicap confidence · factual data quality · recommended edge
bucket · family · period · game · attribution state

**Not one monetary figure changes.** Run it with and without: every total is
the same number, and `tests/test_postmortem.py` asserts exactly that. If it
were not true, the attribution layer would be able to move the accounting, and
then the accounting would no longer be the record of what happened.

Without artifacts, those cuts all read `not_recommended` and the report says so
rather than quietly showing fewer rows.

## Linking a fill to a recommendation

`accounting/recommendation_link.py` reads the wager ledger and **never mutates
it**. A match needs the same market ticker, the same side, a fill inside
`DEFAULT_MATCH_WINDOW_SECONDS` (12h) of the artifact, and a price within
`DEFAULT_PRICE_TOLERANCE` (0.02).

| `MatchState` | Meaning |
|---|---|
| `matched` | recommended, and filled at or better than the stated price |
| `matched_worse_price` | recommended, but the fill was past the price the analysis said to stop at. **Still a real bet and still in every total** |
| `not_recommended` | the owner placed it; no artifact proposed it |
| `recommended_not_taken` | an artifact proposed it; no fill exists |
| `ambiguous` | more than one plausible link. Named, never guessed between |

`recommended_not_taken` costs nothing and is not a loss. It is listed because
the difference between "the analysis was wrong" and "the analysis was ignored"
is the only interesting question a postmortem can answer.

## Why a tier reading needs eight settled bets

`MIN_SETTLED_FOR_A_TIER_READING = 8`. Below that the report prints the rows and
**refuses to characterise them**. Three settled `robust_positive_ev` bets going
2-1 is not evidence that robustness works, and a report willing to say it was
would eventually say the opposite with equal confidence.

Nothing here validates an edge, and the report makes no profitability claim at
any sample size. It reports realised results.

## Issue categories are prompts, not verdicts

`HANDICAP_ERROR` means *a handicap whose games went badly is worth re-reading*.
It does not mean the handicap was wrong — a correct handicap loses often, and a
report that graded opinions by outcome would train the operator to chase
results. The same applies to `DATA_QUALITY`, `PRICE_SLIPPAGE` and
`MARKET_DISAGREEMENT`: each one names something to go and look at.

## It prints tickers, stakes and payouts, and is therefore LOCAL

This is the owner's report about the owner's money. Unlike the delivery and
settlement workflows — which print counts and never rows — the postmortem prints
everything, because that is the whole point of it.

No workflow runs it. `--json` writes the same content to a file for a tool to
read.

## Where a unit result comes from

`--unit` or nothing. A unit inferred from the average stake would be a number
that never existed, so absent the flag no unit figure is stated at all.
