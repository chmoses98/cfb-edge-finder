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
| `evaluated_not_selected` | every candidate the reduction removed, with the survivor it lost to and why, and — from a 1.1.0 artifact — its `card_role`, whether placing it beside its core `requires_incremental_justification`, its `cash_path_relation`, `tail_extension` and `extension_points` |
| `card_review` | the artifact's final-review contract verbatim (`cfb_card_review/1.0.0`): which candidate was the core of each view, the related alternatives grouped by cash path, thesis and game groupings, and every exposure placeholder **null**. `null` on a record built from a 1.0.0 artifact |
| identity | `record_id` (content hash), `created_at`, `slate_date`, `batch`, `shard`, `kickoff_window`, the artifact's `generated_at`, the catalog capture time, and the source file paths |

Schema `cfb_decision_record/1.0.0`. Fields the handicapper did not emit are
`null`, never filled: there is no recommended stake and no bankroll context in
the live workflow today, and the record says so.

Each selected candidate also carries `card_role`, `thesis_group`,
`thesis_peers` and `wins_when`, and each of its related alternatives the same
incremental-exposure fields as `evaluated_not_selected`. These card-review
fields are **optional within 1.0.0** and the schema was deliberately not
bumped: they are purely additive, no 1.0.0 field changed meaning, every reader
uses `.get`, and records already in the store stay valid as written. When a
`card_review` block is present the validator requires
`sizing_authority: "operator"`, `repository_sizes_positions: false`, and every
`exposure_after_sizing` figure null — a decision record never invents a stake.

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

### Activating the store on the machine that runs `candidates`

`evaluate` and `candidates` run **where the operator types them** — a terminal
on the operator's own machine, per the workflow above. Nothing in GitHub
Actions runs `candidates`, so no repository setting, secret or workflow can
configure this; the variable has to exist in the environment that launches the
command. Once, on that machine:

```bash
# 1. create the store OUTSIDE every checkout of cfb-edge-finder / kalshi-bet-router.
#    The command refuses a path inside a public checkout and creates nothing there.
python -m cfb_edge_finder.execution decision-store init ~/private-cfb-decisions

# 2. make the variable part of the environment that launches `candidates`.
#    For a terminal that is the login shell's profile:
echo 'export CFB_DECISION_STORE="$HOME/private-cfb-decisions"' >> ~/.zshrc   # or ~/.bashrc
exec "$SHELL" -l

# 3. prove it, from a fresh shell, before the next slate:
python -m cfb_edge_finder.execution decision-store check
```

`check` prints the resolved root, whether the marker is present, how many
records the store holds and whether any fail validation, and exits `5` (the
same code `candidates` uses) when the store is unusable. If `candidates` is
ever launched by something other than a login shell — a wrapper script, a
service, a Claude Code session, cron — the variable must be set in **that**
launcher's environment; a shell profile does not reach it. A cloud session's
container is ephemeral, so a store created there is lost with it unless it is
a checkout of a private remote with `CFB_DECISION_STORE_GIT_SYNC=1`.

Back up the directory like anything else private (a private remote, an
encrypted drive). Never a public repository: the store holds the prices and
sizes of the owner's decisions.

### The private repository

Creating one is an **owner action**: the GitHub integration this system runs
under cannot create repositories (re-checked 2026-09-28: `POST /user/repos`
still returns 403 for this integration). Any private repository works; clone
it, run `decision-store init` on the clone, point the variable at it, and set
`CFB_DECISION_STORE_GIT_SYNC=1` so every record is committed and pushed.
Retention is then the repository's.

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
tier and confidence, recommended stake (null) beside the actual stake, the
candidate's `card_role` and `thesis_group`, and the alternatives that existed
for the same game in that record — each with whether placing it beside its core
would have required incremental justification, its cash-path relation, and
whether it was a tail extension.

**No monetary figure depends on any of this.** `tests/test_decision_records.py`
runs the postmortem with and without the store and asserts the totals are
byte-identical. The report prints prices and stakes and is therefore LOCAL, as
`docs/POSTMORTEM.md` already says.
