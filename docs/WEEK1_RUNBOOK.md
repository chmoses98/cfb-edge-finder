# Week 1 Runbook

The operating procedure for the first live college-football weekend of
the 2026 season.

**What this weekend is for:** collecting prospective observations that
did not exist before, including the CLOSING checkpoints that can only
ever be captured once. It is not for making decisions. Every empirical
gate is locked, no threshold artifact is approved, and there is nothing
to act on. The single measure of success is *the data we could only get
this weekend, we got*.

---

## 0. The one-line version

Confirm collection is running before you sleep, confirm it is still
running when you wake up, and never manufacture a data point you missed.

---

## 1. NIGHT BEFORE

### 1.1 Run the ops health check

```bash
python3 scripts/week1_ops_health.py --data-repo-dir <path-to-data-checkout>
```

Read the OVERALL line:

| State | Meaning | What to do |
|---|---|---|
| `HEALTHY` | Collecting, intact, locked. | Nothing. Go to sleep. |
| `WARN` | Degraded but still collecting. | Read the check's `remedy` line. Resolve before the first kickoff if you can; a WARN does not require you to stay up. |
| `BLOCKED` | Collection has stopped, integrity is broken, or a safety lock has failed. | Fix tonight. A blocked night is lost CLOSING data that cannot be recovered. |
| `PENDING_NATURAL_DATA` | Machine fine; no settled games exist yet. | **Expected all weekend.** Nothing to fix. |

`PENDING_NATURAL_DATA` is the normal state before any game has finished.
It is not a warning and it is not something to work around.

### 1.2 Confirm the independent clock

GitHub's own cron delivered **1 of ~57 expected `*/10` runs** over a
measured 573-minute window in this repository — 1.7%, with gaps of 95,
144, 171, 296, 653 and 777 minutes. It is not a clock. The external
scheduler (cron-job.org, see `docs/EXTERNAL_SCHEDULER.md`) is the
primary trigger and GitHub's cron is only a backstop.

The `external_scheduler` check must not be `BLOCKED`. If it is:

1. Check the external job's own history for non-`204` responses.
2. If the external scheduler cannot be restored tonight, dispatch the
   capture workflow manually across each kickoff window. This is
   tedious and it is still better than losing CLOSING.

### 1.3 Confirm the kickoff windows you care about

CLOSING fires only when `0 < minutes_to_kickoff <= 14`, strictly before
kickoff. Know when the first and last kickoffs are so you know when the
irreplaceable windows open and close.

---

## 2. DURING THE SLATE

Check `week1_ops_health.py` between windows, not continuously. The
things worth reacting to:

- **`collection_freshness` BLOCKED** — collection has stopped. Dispatch
  the capture workflow manually now, then find out why.
- **`closing_coverage` BLOCKED or WARN** — CLOSING checkpoints were due
  and not captured. Those specific ones are gone. Fix the trigger so the
  *next* window is not also lost; do not try to recover the lost ones.
- **`corpus_integrity` BLOCKED** — stop and investigate the writer.
  Do not repair the ledger by hand.
- **`safety_locks` BLOCKED** — stop entirely. A lock that has opened is
  more serious than any amount of missed data.

Everything else waits until the slate is over.

---

## 3. NEXT MORNING

### 3.1 Re-run ops health

Same command. You are checking that collection survived the night, not
looking for results.

### 3.2 Let settlement run, then look at what settled

```bash
python3 scripts/research_settle.py --season 2026
python3 scripts/week1_ops_health.py --data-repo-dir <path> --json-out ops.json
```

The line that matters is `games with status=settled`. Note that a
settlement row exists for every market the settler has *looked at*,
including games that have not kicked off — those carry
`status=pending_not_final` and are **not** settled games. Counting rows,
or counting their distinct `game_id`s, would report a sample that does
not exist.

### 3.3 Produce the Research Decision Report

```bash
python3 scripts/build_research_decision_report.py \
    --data-repo-dir <path> --json-out decision_report.json
```

Expect `SHADOW_QUALIFIED: 0`. That zero is **counted, not hardcoded** —
if it is ever non-zero, a lock has failed and the script exits non-zero.
The report contains no ranking, no sizing, and nothing actionable, by
construction.

### 3.4 Ask the only research question available

*Did we capture the checkpoints we needed, especially CLOSING?*

Not "what were the edges". There are no edges yet, because there is no
validated evidence yet.

---

## 4. WHAT MUST NEVER BE MANUALLY BACKFILLED

These are not preferences. Every one of them, if violated, silently
destroys the research value of the entire corpus — and the damage is not
visible afterwards, which is what makes it dangerous.

### 4.1 CLOSING observations — NEVER

A CLOSING row written after kickoff is a row created with knowledge that
did not exist at capture time. Even if the price used is genuinely a
pre-kickoff price, the *decision to record it* was made afterwards. That
is the exact shape of look-ahead bias, and one such row invalidates every
CLV claim the corpus can ever support, because no downstream analysis can
tell it from a real one.

**If a CLOSING window is missed, it is gone. Record that it was missed
and move on.** The capture-state log already records an explicit reason
for every market that reaches kickoff without a CLOSING row; that record
is the correct output, not a substitute row.

### 4.2 Any prospective checkpoint, after its window

Same reasoning as CLOSING, at every label: EARLY_OPEN, T_7D, T_3D,
T_24H, T_6H, T_90, T_60, T_30. A checkpoint's whole meaning is
"what the market looked like at this distance from kickoff, recorded
without knowing the outcome."

### 4.3 Prices reconstructed from anywhere but the live capture

No re-derivation from a later snapshot, no interpolation between two
captures, no substituting `1 - yes_ask` for a missing `no_ask`. The
executable NO price is `no_ask` — the price you could actually have paid.

### 4.4 Model probabilities re-run under a newer model

A stored observation carries the model version that produced it.
Re-pricing history with today's model produces numbers that were never
available at the time. If a backfill tool is ever built, its rows must be
stamped `RETROSPECTIVE_BACKFILL` and they must never enter a prospective
analysis — the shadow pipeline rejects any non-`PROSPECTIVE` row before
anything else, and that gate exists for exactly this.

### 4.5 Settlement outcomes entered by hand

Settlements come from the settlement pipeline against the official
source. A hand-entered result is an unverifiable claim about a game.

### 4.6 Anything to make a check go green

`PENDING_NATURAL_DATA` clears when games finish, and by no other means.
A missed CLOSING stays missed. A gap in the corpus is data about how the
collection performed, and it is worth more than a fabricated row.

---

## 5. WHAT IS LOCKED, AND STAYS LOCKED

Verified positively by `week1_ops_health.py`'s `safety_locks` check on
every run — not asserted here:

- `QUALIFICATION_FOR_REAL_MONEY: DISABLED`
- `EMPIRICAL_THRESHOLD_ARTIFACT: ABSENT_OR_UNAPPROVED`
- `AUTO_VALIDATION: IMPOSSIBLE` — `assess_readiness` cannot return
  `VALIDATED` for any sample size
- `STAKING_CONNECTION_TO_DECISION_PIPELINE: ABSENT` — the sizing math in
  `cfb_edge_finder.sizing` is imported by nothing in the decision path,
  enforced by a test
- `EXECUTION: ABSENT`, `KALSHI_CLIENT: READ_ONLY`,
  `ORDER_PLACEMENT: NONE`, `BANKROLL_ACCESS: NONE`

None of these open because a weekend went well. They open, if ever, by a
deliberate human decision on evidence that does not exist yet.

---

## 6. Escalation

| Symptom | Severity | Action |
|---|---|---|
| Safety lock reported open | Highest | Stop everything. Do not run the pipeline for any decision purpose. |
| Corpus integrity broken | Highest | Stop writing. Do not repair by hand. |
| Collection stopped mid-slate | High | Manual dispatch now; diagnose after. |
| External scheduler silent | High | Manual dispatch across kickoff windows. |
| CLOSING missed | High, unrecoverable | Fix forward only. Never backfill. |
| 0 settled games | None | Expected. Wait. |
