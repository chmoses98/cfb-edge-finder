# Prospective Research Protocol — v1

**Status: PREREGISTERED.** Written and committed **before any Week 1
outcome exists**, and before any settled prospective observation exists
anywhere in the corpus (0 settled games at time of writing; see
`docs/EMPIRICAL_RESEARCH_GATE.md`).

That timing is the entire point. A protocol written after seeing results
is not a protocol, it is a rationalisation. The commit that introduces
this file is the evidence of when it was fixed, and
`research/protocol.py` computes a content hash so any later report can
prove which text it followed.

| Field | Value |
|---|---|
| Protocol version | `prospective_research_protocol_v1` |
| Preregistered | 2026-08-28, before the 2026-08-29 slate |
| Settled games at preregistration | **0** |
| CLOSING observations at preregistration | **0** |
| Supersedes | — |

---

## 0. What this protocol is for

To decide, **in advance**, what would count as evidence that this model
knows something the market does not — so that when results arrive we are
reading a pre-committed analysis rather than searching a fresh dataset
for something flattering.

It does **not** authorise anything. Passing every analysis here produces
a `DRAFT_RESEARCH_FINDING`, never an approved threshold, never a
recommendation, and never a stake.

---

## 1. Primary questions

Stated before any outcome is observable. Q1–Q3 are the confirmatory
questions; Q4–Q10 are prespecified but **descriptive** (see §5).

1. **Does signed model-minus-market disagreement predict actual
   outcomes?** For contracts where the model's probability exceeds the
   fee-adjusted break-even, does the contract settle YES more often than
   the executable price implied?
2. **Does signed disagreement predict movement toward CLOSING?**
   Do prices move in the direction the model indicated, measured from the
   observation's timing label to the genuine CLOSING quote?
3. **Does larger disagreement correspond to improved fee-adjusted
   one-contract research P/L?** Monotonicity across the descriptive gap
   buckets, not a cutoff.
4. How does performance differ by family: **winner / spread / total**?
5. How does performance differ by timing: **EARLY_OPEN, T_7D, T_3D,
   T_24H, T_6H, T_90, T_60, T_30**?
6. How does performance differ by **executable price band**?
7. Does **model-above-market** differ from **model-below-market**?
   (Asymmetry is expected: a NO side is a different contract, not a
   mirror.)
8. Does **model calibration** outperform **executable market
   calibration** on the same events?
9. Does **CLV corroborate** the outcome results, or do they disagree?
10. Do findings **replicate across future weeks**?

### 1a. The Week 1 ablation question

Registered now because Week 1 makes it unavoidable: the model's Week 1
point estimate contains **no 2026 information at all**
(`season_carryover_weight(0) == 0.0` — see
`docs/WEEK1_FOOTBALL_INPUT_AUDIT.md`).

> **Do model-market disagreements concentrate on teams with known 2026
> roster, coaching, or quarterback churn?**

If they do, the disagreement is the market's information advantage, not
the model's edge. This is why contextual capture
(`docs/CONTEXT_CAPTURE.md`) is prospective and recorded now: the question
can only be answered later with data that had to be captured before the
games.

---

## 2. Population — declared in advance

| Dimension | Included | Excluded |
|---|---|---|
| Capture mode | `PROSPECTIVE` only | `RETROSPECTIVE_BACKFILL`, always and without exception |
| Teams | **FBS vs FBS only** | FBS-vs-FCS, FCS-vs-FCS |
| Families | moneyline, spread, total | anything else |
| Pricing | `pricing_status == "model_priced"` | unpriced |
| Market status | executable at capture | non-executable |
| Fee schedule | `VERIFIED_CURRENT` | unverified |
| Semantics | resolved from persisted fields | `EQUIVALENCE_UNRESOLVED` |
| Settlement | terminal (`settled`) | `pending_not_final`, void |

**Model-version partitioning is mandatory.** Observations priced by
different model versions are different populations and are never pooled.
A contract with no recorded model version is excluded, not defaulted.

**Timing partitioning is mandatory.** An EARLY_OPEN observation and a
T_30 observation of the same contract are different observations of
different information states, never averaged together.

---

## 3. Uncertainty — declared in advance

Three dependence structures exist in this data and all three are handled
before any interval is reported:

1. **Game clustering.** Every contract on one game shares one football
   outcome. The unit of independent evidence is the **game**, not the
   contract. All confidence intervals are clustered on `game_id`.
2. **Contract dependence within a game.** A margin ladder is one thesis
   expressed many ways (`decision/portfolio.py` groups exactly this).
   Counting twenty contracts as twenty observations would understate
   uncertainty by roughly the square root of the ladder depth.
3. **Snapshot dependence.** The same contract observed at T_7D and T_24H
   is two correlated looks at one market, not two samples.

**n is reported three ways, always together:** observations, distinct
contracts, distinct games. A result quoted only in observations is not a
result.

---

## 4. Discovery and validation — the separation rule

> **No threshold may ever be validated on any game used to discover it.**

Mechanically enforced by `research/holdout.py`, which refuses a
validation run whose game set intersects the discovery set, and records
both sets by identifier in the report.

The permitted progression, in order, with no step skippable:

```
PROSPECTIVE DATA
  -> RESEARCH ANALYSIS            (this protocol)
  -> DRAFT_RESEARCH_FINDING       (research_threshold_candidates.py)
  -> FROZEN CANDIDATE RULE        (hashed, immutable)
  -> FUTURE UNSEEN PROSPECTIVE DATA
  -> VALIDATION REPORT            (holdout.py)
  -> HUMAN REVIEW                 (a person, not a computation)
  -> possible later shadow artifact
```

Retrospective/backtest data may inform **hypotheses**. It can never serve
as prospective validation, because its outcomes were known when it was
assembled.

---

## 5. Confirmatory vs descriptive

- **Confirmatory:** Q1, Q2, Q3 only. Prespecified here, on the
  prespecified population, reported whatever the result.
- **Descriptive:** Q4–Q10 and every bucketed breakdown. These generate
  hypotheses for future weeks. A descriptive result is never promoted to
  a finding within the same data that produced it.

Existing descriptive gap buckets remain descriptive. They are reporting
conveniences, not candidate cutoffs.

---

## 6. Anti-p-hacking commitments

Written down because the temptation is strongest exactly when the data
finally arrives:

1. **No cutoff is chosen tonight.** Not 5%, not 8%, not 10%. Any
   threshold must come from the discovery engine operating on a sample
   that satisfies §7, and must then survive unseen future data.
2. **No combinatorial cut mining.** The discovery engine does not sweep
   thousands of (family × timing × gap × price) rectangles and report the
   best. `research_threshold_candidates.py` refuses to rank by ROI.
3. **Multiple comparisons are declared, not discovered.** Any breakdown
   reports how many slices were examined. A nominal p-value from one of
   40 slices is reported as one of 40.
4. **No outcome-dependent redefinition.** The population in §2 and the
   questions in §1 do not change after results are seen. If they must
   change, it becomes protocol v2, with the reason recorded, and prior
   results are not retroactively reinterpreted under it.
5. **The direction is fixed in advance.** Q1–Q3 predict that positive
   signed disagreement associates with *better* results. A finding in the
   opposite direction is a negative result, not a rediscovered strategy.
6. **One slate is never a result.** See §7.
7. **Negative results are reported.** A protocol that only surfaces
   findings when they are favourable is a marketing document.

---

## 7. Sample-size philosophy

No minimum n is asserted here, because asserting one without evidence
would be the same invention this protocol forbids. What *is* fixed:

- **A Week 1 slate cannot promote anything.** One week is a handful of
  independent game clusters. It is a pipeline test and a hypothesis
  generator.
- **Report the interval, not the point.** A point estimate without a
  cluster-aware interval is not reportable.
- **Insufficient sample is a verdict, not a gap to fill.** The correct
  output is `EMPIRICAL_THRESHOLD_RESEARCH_BLOCKED_ON_SAMPLE`.
- **The minimum enters as a declared input**, carried on a candidate
  rule as `minimum_settled_games` and stated by a human who can defend
  it — never inferred from the data it will then be applied to.

---

## 8. What no result under this protocol can do

- It cannot approve a threshold artifact. Approval is a human act
  recorded in the artifact's own `approval_state`, and no code path
  writes it (verified by AST scan).
- It cannot enable real-money qualification.
- It cannot connect staking to the decision path.
- It cannot promote evidence to `VALIDATED`. `assess_readiness` cannot
  return that state for any sample size.

---

## 9. Amendment

Changes create **v2** with a new version string and hash. This file is
not edited in place after results exist. Any report states the protocol
version and hash it followed, so a reader can tell whether the analysis
predates or postdates what it analysed.
