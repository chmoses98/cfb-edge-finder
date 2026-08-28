# Empirical Research Gate

## Verdict

> **EMPIRICAL THRESHOLD RESEARCH BLOCKED ON NATURAL SAMPLE SIZE.**

No empirical threshold can be derived, proposed, or approved from the
current corpus. Not because the bar is set high, but because the
quantity required to compute anything is **zero**.

Measured 2026-08-28 against `origin/research-data`.

---

## What the corpus actually contains

| Quantity | Count |
|---|---|
| Observation rows | 1,909 |
| — `capture_mode = PROSPECTIVE` | 1,909 (100%) |
| — `capture_mode = RETROSPECTIVE_BACKFILL` | 0 |
| Duplicate `observation_key`s | 0 |
| Malformed rows | 0 |
| Distinct games observed | 102 |
| Distinct market tickers | 1,158 |
| Schema `research_corpus_v1` / `v2` | 1,724 / 185 |

Timing labels captured:

| Label | Rows |
|---|---|
| `EARLY_OPEN` | 1,158 |
| `T_7D` | 364 |
| `T_3D` | 387 |
| `T_24H`, `T_6H`, `T_90`, `T_60`, `T_30`, `CLOSING` | **0** |

---

## What the corpus does not contain

| Quantity required for threshold research | Count |
|---|---|
| Games with settlement `status = settled` | **0** |
| `CLOSING` observations | **0** |
| Settled prospective observations | **0** |
| CLV-measurable observations | **0** |
| Calibration-measurable observations | **0** |

### The 751 settlement rows are not settled games

The settlement ledger holds 751 rows across 86 distinct `game_id`s, and
**all 751 carry `status = pending_not_final`** — the settler has looked
at these markets, and their games have not kicked off. A count of
settlement *rows*, or of their distinct `game_id`s, would report a
sample of 86 games that does not exist. `week1_ops_health.py` filters on
`status == "settled"` for exactly this reason.

---

## Why no threshold can be proposed

An empirical threshold is a claim of the form *"at this family, this
timing label, and this model-market gap, realised returns were positive
out of sample."* Every term in that sentence needs settled outcomes:

- **ROI** needs settled contracts. There are none.
- **CLV** needs a `CLOSING` price to compare against. There are none.
- **Calibration** needs realised outcomes against predicted
  probabilities. There are none.
- **A confidence interval** needs a sample. n = 0 admits no interval,
  and a point estimate without one is not evidence.

There is no statistical technique that recovers a threshold from zero
settled observations. Any number produced now would be an assertion
wearing the costume of a result.

Retrospective backtest data cannot substitute. It is a different
population, gathered under different conditions, with the outcomes
already known — which is precisely the distinction `capture_mode` exists
to keep mechanical.

---

## What clears this gate

Only time and real games:

1. Games kick off and finish.
2. The settlement pipeline records `status = settled` against the
   official source.
3. Prospective observations — captured before those outcomes were known,
   including `CLOSING` — link to settled results.
4. A sample large enough to support an interval, not just a point,
   accumulates.
5. A human being examines that evidence and decides whether any
   threshold is justified.

Step 5 is a decision, not a computation. `assess_readiness` cannot
return `VALIDATED` for any sample size — verified by brute force over
2,662 input combinations — so no amount of accumulating data can promote
itself.

---

## What is being built while blocked

The infrastructure that will consume this evidence, built now so it is
not built later under time pressure:

- `decision/artifact.py` — the threshold artifact schema and its
  fail-closed loader. **Ships zero threshold values.** An artifact that
  is missing, malformed, or unapproved is refused, and an unapproved
  artifact is not even returned to the caller, so its rules are
  unreachable rather than merely unused.
- `decision/shadow.py` — the shadow decision pipeline. Its
  `SHADOW_QUALIFIED` count is *counted*, so a broken lock raises the
  number instead of hiding behind a hardcoded zero.
- `decision/portfolio.py` — correlation grouping with no coefficients
  and no position limits.
- `sizing/` — stake arithmetic, imported by nothing in the decision
  path, with no default bankroll, multiplier, cap, or haircut.

None of it becomes usable because it exists.
