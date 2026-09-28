# Decision records — what the live workflow decided, kept privately

```bash
export CFB_DECISION_STORE=/path/to/private/checkout      # outside this repository
touch "$CFB_DECISION_STORE/.private-decision-store"        # once, by a person
python -m cfb_edge_finder.execution candidates --batch early_b1     # writes a record
python scripts/cfb_postmortem.py --base-dir <accounting-data> --season 2026 --decisions "$CFB_DECISION_STORE"
```

## The gap this closes

The 2026-09-26 postmortem attributed **none** of the season's 93 wagers to a
recommendation. Nothing the live workflow produced — the handicap payloads,
the candidate artifacts, the decision-time prices — was kept anywhere the
postmortem could reach afterwards. The Actions artifacts expire in seven days
and, on a public repository, are public; the operator's local files are local.

A **decision record** is the versioned, self-contained statement of one
`candidates` run, written at the moment the run produced its shortlist:

| block | what it holds |
|---|---|
| `games` | per game: kickoff, teams, the handicapper's **thesis** and **opposing case** verbatim, confidence (stated and effective), data-quality ceiling, and the full handicap payload that priced every rung |
| `candidates` | the **selected** shortlist: ticker, side, `observed_price` and `observed_at`, fair probability, edge, **bet_up_to**, tier (robustness), confidence, `recommended_stake` (null — nothing sizes a bet), correlation group, the alternatives each one beat |
| `evaluated_not_selected` | every candidate the reduction removed, with the survivor it lost to and why |
| identity | `record_id` (content hash), `created_at`, `slate_date`, `batch`, `shard`, `kickoff_window`, the artifact's `generated_at`, the catalog capture time, and the source file paths |

Schema `cfb_decision_record/1.0.0`. Fields the handicapper did not emit are
`null`, never filled: there is no recommended stake and no bankroll context in
the live workflow today, and the record says so.

## Where it lives, and why nowhere here

Both repositories in this system are public, and a decision record carries the
prices and sizes of the owner's betting decisions. So the store is a directory
the operator names — `CFB_DECISION_STORE` or `--decision-store` — and it is
expected to be a **private repository checkout** or a private drive. Nothing in
this repository chooses a default, because every default it could choose is
inside a public checkout.

`candidates` resolves the store **before writing anything** and refuses (exit
`5`) when:

- no store is configured;
- the directory does not exist;
- it carries no `.private-decision-store` marker (a person's one-time statement
  that the directory is private);
- it is inside this repository's own working tree;
- it is a git checkout whose `origin` names `cfb-edge-finder` or
  `kalshi-bet-router`.

There is no fallback to a public path and no quiet skip. To run without a
record the operator passes `--no-decision-record`, and the run prints that no
auditable record exists. A write that fails **after** the artifact exists exits
`6` and says the shortlist is on disk with no record of it.

Writes are atomic (temp file, fsync, rename). With `CFB_DECISION_STORE_GIT_SYNC=1`
the record is also committed and pushed; a failed push is an error that names
the local file and says it is not yet durable.

`tests/test_decision_records.py::test_the_public_repository_holds_no_decision_record`
scans the repository for the schema tag on every run.

### The private repository

Creating one is an **owner action**: the GitHub integration this system runs
under cannot create repositories. Any private repository works; clone it, add
the marker, point the variable at it. Retention is then the repository's.

## What the scheduled slate workflow does NOT write

`.github/workflows/cfb-execution-slate.yml` runs `prepare-live` only. It
handicaps nothing and selects nothing, so there is no decision to record, and
it holds no secret that could reach a private store. Decisions are made where
`evaluate` and `candidates` run — the operator's machine — and that is where
the record is written.

## Attribution in the postmortem

`scripts/cfb_postmortem.py --decisions DIR` (or `$CFB_DECISION_STORE`) reads
every record and matches each wager to at most one decision candidate:

1. exact market ticker and side, and the game the ticker names;
2. a record created **before** the order, inside the 12-hour match window, and
   before that game's kickoff;
3. among several qualifying runs, the **nearest earlier** one — the record that
   was on the screen when the order was placed.

Two records created at the same instant, or one record carrying the same ticker
and side twice, are `ambiguous` and stay so. A record created after kickoff is
rejected and counted. The report states `matched / unmatched / ambiguous`, and
for each matched wager: thesis, opposing case, bet-up-to, decision-time price,
execution price, price delta, whether the fill stayed inside the bet-up-to,
tier and confidence, recommended stake (null) beside the actual stake, and the
alternatives that existed for the same game in that record.

**No monetary figure depends on any of this.** `tests/test_decision_records.py`
runs the postmortem with and without the store and asserts the totals are
byte-identical. The report prints prices and stakes and is therefore LOCAL, as
`docs/POSTMORTEM.md` already says.
