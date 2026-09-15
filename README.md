# accounting-data

The wager ledger. **Accounting, not prediction.**

    CFB MODEL STATUS = RESEARCH ONLY / DISABLED
    CFB ACCOUNTING   = ENABLED

This branch holds `wagers/<season>.jsonl` — one line per College Football
wager the owner actually placed, reconstructed from the venue's own execution
evidence. A row here says the owner placed a bet. It says nothing about
whether this repository's model recommended it, was consulted about it, or was
right, and every field that could imply otherwise is refused outright by
`cfb_edge_finder.accounting.wager.validate`.

## Why a branch rather than `main`

The same reason `research-data` is a branch: bot-written data stays out of
reviewed code history. A ledger that grows on every delivery would otherwise
fill `main`'s log with commits nobody reviews, and the one place a real change
to the accounting code needs to be visible is that log.

## What holds here

* **Append-only.** Rows are appended; nothing is rewritten. A wager record is a
  claim about money that already moved, so correcting one by editing it in
  place would destroy the evidence of what was believed before.
* **Deduplicated on `source_bet_key`**, computed against the whole file. The
  key is derived from the venue's own order identity, so re-running a backfill
  re-derives the same key and writes nothing. "Re-running produces zero new
  wagers" is a property of the store, not a promise about its callers.
* **No predictive package may read it.** A projection that could read the wager
  ledger is one refactor from being trained on it, which would make the owner's
  betting history into model authority. `tests/test_accounting_isolation.py`
  holds both directions of that empty dependency edge.

## What writes here

`scripts/import_routed_wagers.py`, handed a payload by kalshi-bet-router.
Nothing else. The router never edits a file on this branch: it proposes rows to
that importer, which is what keeps its duplicate detection and its validation
on the path.
