# Recommendation + Risk Skeleton (Empirical Gates Disabled)

This document describes a layer that **does not make betting decisions and
cannot be configured to make them.**

The skeleton exists so that a future, evidence-based recommendation engine
has somewhere to attach. Every step that would turn analysis into an
action -- qualification, scoring, sizing, execution -- is either structurally
disabled or entirely absent. This is not a feature flag away from being
live. Turning it on requires writing code that does not exist yet, plus a
reviewed empirical threshold artifact that cannot currently be produced,
because the corpus contains **zero settled games**.

## Why build a disabled layer at all

The alternative is to build recommendation logic later, at the same moment
the first settled results arrive -- exactly when the temptation to fit
thresholds to a tiny sample is strongest. Building the plumbing now, with
the decision points explicitly stubbed, means the later work is *only* the
empirical question: what threshold is justified, on what evidence? The
structural questions (which contract expressions are the same bet, what
concentration a position implies, which quotes are trustworthy) are
answered here, where no result can bias the answer.

## Pipeline stages

| # | Stage | Status |
|---|-------|--------|
| 1 | Candidate formation | Implemented |
| 2 | Data-quality eligibility | Implemented (gates evaluated and reported) |
| 3 | Expression de-duplication | Implemented |
| 4 | Correlation / exposure grouping | Implemented (measured, never enforced) |
| 5 | Scoring container | Structure only -- composite is permanently `None` |
| 6 | Card construction | Implemented -- always emits zero entries |
| 7 | **Stake sizing** | **Absent** |
| 8 | **Execution / order placement** | **Absent** |

Stages 7 and 8 are not stubs. There is no module, no function, and no
parameter for them anywhere in the package, and a test scans the code for
their vocabulary (`stake`, `bankroll`, `kelly`, `place_order`, ...) to keep
it that way.

## The two independent locks

A candidate must clear **both** to become actionable. Neither can currently
be cleared.

**Lock 1 -- qualification is disabled.** `evaluate_eligibility()` returns
`QUALIFICATION_DISABLED` for every candidate, unconditionally. There is no
config field, environment variable, or argument that changes this. The
`EligibilityConfig` dataclass deliberately contains **no cutoff fields at
all** -- not set to a safe default, simply not present -- so there is
nothing to set to a permissive value.

**Lock 2 -- no validated threshold artifact exists.** Qualification is
defined to consume a versioned `ThresholdArtifact`. The default provider is
`NullThresholdProvider`, which returns `NO_VALIDATED_THRESHOLD_SET`. An
artifact carries provenance (source corpus, prospective-only flag, settled
game count, model version, approval state) and its numeric content is an
opaque `values: dict[str, float]` -- there are no named numeric fields such
as `min_edge`, because a named field invites a default, and a default is a
magic number.

Compatibility is checked on three axes -- model version, timing label, and
contract family. `None` on any axis is treated as a **mismatch, not a
wildcard**: an artifact that does not say what it applies to applies to
nothing.

## Evidence readiness

`assess_readiness()` reports how close a contract family is to supporting
any empirical claim:

`NO_SETTLED_DATA → LOW_SAMPLE → RESEARCH_ACCUMULATING → VALIDATION_PENDING → VALIDATED`

The function **terminates at `VALIDATION_PENDING` and never returns
`VALIDATED`.** The final transition is a human review decision about
holdout discipline and out-of-sample behaviour, not an arithmetic
consequence of a counter crossing a number.

The sample counts that drive the earlier transitions (5, 30) are counts of
independent *clusters* -- settled games -- not profitability cutoffs. They
gate whether a question may be asked, never what the answer is.

Current state, every family: **`NO_SETTLED_DATA`** (settled n = 0).

## Exposure grouping (measured, never enforced)

`build_exposure_keys()` maps a candidate to the exposures it actually
creates, so concentration is measured in football terms rather than ticker
terms. Notably, **buying NO on team T is exposure to T's opponent** -- a
naive per-ticker count would see two independent positions where there is
one doubled position.

Keys span game, thesis dimension, team-direction, equivalence class, and
model thesis. `evaluate_concentration()` tallies them and always returns
`enforced=False` with status `RISK_LIMITS_DISABLED_PENDING_VALIDATION`.
Limits are structurally present and structurally inert: correlation-aware
limits require knowing correlation magnitude empirically, which requires
settled data.

## Card output

`build_research_card()` produces a diagnostic object with:

- `entries: tuple[()] = ()` -- typed as the empty tuple, so adding an entry
  is a type error, not a behavioural drift.
- `maximum_acceptable_price` -- `BET_UP_TO_UNAVAILABLE_NO_VALIDATED_THRESHOLD`.
  A price ceiling is a threshold; without an artifact there is no ceiling
  to state.
- `shadow_status` -- `SHADOW_DISABLED_NO_VALIDATED_THRESHOLDS`. Shadow mode
  would record what the engine *would* have done; with no thresholds there
  is nothing it would have done.
- `actionable_count` -- **computed by counting actionable results, not
  hardcoded to zero.** This matters: a hardcoded zero would report success
  even if the locks failed. A test asserts the count is derived, so the
  zero observed in practice is evidence rather than decoration.

## Safety tests

`tests/test_recommendation_safety.py` proves the properties that matter:

- **Zero-actionable, end to end.** Run against a universe engineered to
  *pass* every data-quality gate -- fresh quotes, active markets, verified
  fees, resolved semantics -- so the zero comes from the threshold boundary
  rather than incidental data problems. The test asserts a non-empty set of
  candidates cleared quality first; otherwise the zero would be vacuous.
- **No hard-coded profitability thresholds.** An AST scan rejects any float
  literal strictly between 0 and 1 in the skeleton modules -- the shape a
  cutoff like `0.05` or `0.08` would take. One allowlisted constant:
  `EVEN_MONEY_PIVOT = 0.5` in `odds.py`, a property of American-odds
  notation, justified by a companion test proving `odds.py` contains no
  eligibility surface and imports nothing from the skeleton.
- **No optimizer.** No public name in the package may contain `optimize`,
  `maximize`, `find_best`, `tune`, `auto_approve`, or `promote`. Threshold
  selection must be a reviewed act.
- **No self-approving artifact.** No code path constructs provenance with
  `approval_state=APPROVED_FOR_LIVE`. The enum member is *referenced* by
  the `LIVE_APPROVAL_STATES` membership check -- that reference is the gate
  working, and is exactly what must be permitted while assignment is not.
- **No sizing or execution vocabulary**, and no import of `betting`.

Scans are AST-based and exclude string literals: these modules' docstrings
describe the boundary in prose, and a naive text scan would flag the very
sentences documenting it.

## Live and scale validation

Against the genuine research corpus (1,724 observations, 102 games, 1,158
contracts):

```
candidate expressions    : 2316      equivalence clusters : 1334
dominated expressions    : 110       nested ladder groups : 99
blocked: qualification disabled : 2316
ACTIONABLE CANDIDATES    : 0         card entries         : 0
```

Scale (synthetic, quality gates passing):

| contracts | load | pipeline | peak RSS | actionable |
|-----------|------|----------|----------|------------|
| 5,000     | 0.08s | 0.22s   | 60 MB    | 0 |
| 25,000    | 0.42s | 1.39s   | 182 MB   | 0 |
| 100,000   | 1.90s | 8.33s   | 644 MB   | 0 |

Growth is linear: local exponents measured across 12.5k–200k are 1.12,
1.09, 1.28, 0.93 -- oscillating around 1.0 rather than trending toward 2.
The Mission 1 invariant holds throughout: the observation ledger is read
**once per run** at every size.

## What must happen before anything here can act

1. Settled games accumulate prospectively (the corpus has none yet).
2. Analytics produce out-of-sample calibration and fee-adjusted results
   with cluster-aware uncertainty.
3. A human proposes a threshold artifact, with holdout discipline, and
   reviews it to `APPROVED_FOR_SHADOW`.
4. Shadow mode runs and is evaluated against realised outcomes.
5. Only then does approval to live, sizing, and execution become a
   conversation -- and each requires code that does not exist today.

None of steps 1-5 is a configuration change.
