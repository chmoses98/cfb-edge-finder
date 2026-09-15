# wagers

One file per season: `<season>.jsonl`, one line per wager the owner actually
placed.

## Why this directory is committed while it is empty

Git does not track empty directories, and its absence is not the same fact as
its emptiness.

kalshi-bet-router reconciles a wager against the rows a destination already
holds before deciding whether to write it, and **zero existing rows is exactly
the input that makes every wager look missing**. So a `wagers/` that had simply
never been created would be indistinguishable, to the reader, from a ledger
with nothing in it yet — and the run that could not find it would import the
owner's entire betting history a second time while reporting "0 rows" and
looking correct.

This file makes the directory's existence a fact the reader can check. Present
and empty means no wager has been recorded yet. Absent means something is wrong
with the checkout, and the reader refuses rather than guessing.
