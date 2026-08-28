# Preseason-Prior / Week 1 Model Quality Research — v1

**Status: HARNESS COMPLETE, EMPIRICAL RESEARCH BLOCKED ON DATA ACCESS.**

Research question: *which preseason information actually improves
early-season out-of-sample CFB projections beyond the current model?*

**No candidate was evaluated, because the historical data required to
evaluate one is unreachable from this environment.** Every candidate is
reported `BLOCKED_NO_HISTORICAL_DATA` — which is **not** a rejection. A
rejection asserts a measurement; none was made.

| Field | Value |
|---|---|
| Control model version | `0.4.0-milestone-c2-live-margin-correction` |
| Control config hash | `3741c6f522972fa2de46493b47a80de756aabc0a038d12c33f6f3e204f66bd83` |
| Production main | `33df3f0` |
| Production changed | **NO** |
| Shadow model created | **NO** |

---

## 1. The blocker, precisely

Three independent facts, each verified:

1. **No CFBD API key.** `Settings.from_env()` reports `cfbd_api_key`
   absent.
2. **CFBD is denied by egress policy.** `api.collegefootballdata.com:443`
   returns **403 to CONNECT**. The agent-proxy status endpoint records
   it as `connect_rejected — "gateway answered 403 to CONNECT (policy
   denial or upstream failure)"`. The proxy documentation is explicit
   that policy denials must be reported, not routed around. This matches
   a constraint documented since Milestone B.
3. **No cached historical seasons exist.** The only historical corpus in
   the repository is
   `src/cfb_edge_finder/data/fixtures/cfb_backtest_fixture_corpus.json` —
   66 rows whose own provenance field reads *"Synthetic, illustrative
   fixture — NOT real CFBD data."*

Multi-season returning production, talent, coaching records and game
results are all required and all unavailable. Producing candidate metrics
without them would mean fabricating the data, which this mission
forbids absolutely.

**What would unblock it:** a cached export of the required endpoints for
the research seasons, committed or mounted at
`data/research_cache/preseason/`, or a session with CFBD egress and a
key. The harness runs the moment either exists.

---

## 2. Control model — frozen (Part 1)

Read from production at import time and hashed, so a control that drifts
mid-research fails the test suite rather than silently invalidating every
comparison.

| Component | Value |
|---|---|
| Ridge lambda | 10.0 (FCS 4.0) |
| Pace shrinkage k | 4.0 · pace mode `matchup` |
| Season shrinkage k | 4.0 |
| **Week 1 carryover weight** | **0.0000** |
| Simulations | 20,000 · residual scale 0.85 |
| Fallback residual SD | 14.0 · min pool 40 |
| Early-season uncertainty scale | 0.30 |
| FCS-opponent uncertainty scale | 0.35 |
| QB uncertainty multipliers | returning 1.00 · mixed 1.10 · new 1.20 · **unknown 1.20** |
| QB continuity thresholds | high 0.70 · low 0.35 |
| Margin correction | `c2-margin-linear-v1-2022-2025`, a=1.34131, b=0.81173, n=2935 |
| FCS treatment | pseudo-team, 3 tiers, not priced for research |
| **Preseason info in point estimate** | **none** |
| Preseason info in uncertainty only | QB continuity proxy |

The zero carryover weight is the defining control property: with no 2026
games played, the point estimate contains no current-season information
at all.

---

## 3. Data-source audit (Parts 3 & 16)

Extends Milestone C section 1 rather than competing with it; its verdicts
are carried forward verbatim in `MILESTONE_C_VERDICT`.

| Feature family | Source | Known before season | Revision risk | Verdict |
|---|---|---|---|---|
| Returning production (passing) | `/player/returning` | YES | snapshot-stable | **USABLE** |
| Returning production (broader) | `/player/returning` | YES | snapshot-stable | **USABLE** |
| Talent composite | `/talent` | YES | snapshot-stable | **USABLE** |
| Coaching change | `/coaches` | YES | snapshot-stable | **USABLE** (schema unverified) |
| Prior-season scores | `/games` | YES | immutable | **USABLE** (control already) |
| QB identity | `/roster` + depth chart | PARTIAL | **retroactively revised** | UNUSABLE |
| Transfer portal | none historical | PARTIAL | **retroactively revised** | UNAVAILABLE |
| Preseason SP+/Elo/SRS | `/ratings/*` | **UNCONFIRMED** | unknown | UNUSABLE |
| Historical lines | `/lines` | NO (closing) | immutable | **EVALUATION ONLY** |
| Weather | NWS / Visual Crossing | NO | unknown | UNUSABLE (postgame) |
| Injuries / suspensions | none | PARTIAL | unknown | UNAVAILABLE |

### Why the rejections are rejections

- **QB identity** — a roster queried today reflects every subsequent
  transfer, injury and depth-chart change. No as-of snapshot exists, and
  no source names the *expected* starter before Week 1. Reconstructing it
  from today's data imports the outcome into the feature. Continuity
  *proxies* remain testable; identity does not.
- **Transfer portal** — portal activity is continuous and rankings are
  restated as players move. Applying today's view to a 2021 preseason is
  precisely the backward leak the mission forbids.
- **Preseason ratings** — Milestone C found it ambiguous whether a given
  week's rating is pre- or post- that week's games and could not resolve
  it, because CFBD's documentation domains are blocked. That is
  unchanged. An unconfirmed timing semantic on a *team-strength rating*
  is the highest-value leak available, so it stays disqualified rather
  than adopted hopefully.
- **Weather** — not preseason information, and belongs to a separate
  totals question. Critically, only *realised* conditions are
  retrievable; the historical **pregame forecast** a model could actually
  have used is not. Realised weather is postgame.
- **Injuries** — no mandatory report, no structured historical API.
  Constructing labels from archived reporting would be hindsight.

---

## 4. Research design, declared in advance (Parts 2, 7, 10, 15)

### Walk-forward split — fixed before any candidate result

| Role | Seasons |
|---|---|
| Development | 2019, 2021, 2022, 2023 |
| Selection | 2024 |
| Confirmation | 2025 (untouched) |
| **Excluded** | **2020** |

**2020 is excluded, not pooled.** Conference-only schedules, opt-outs and
cancellations make its preseason-to-outcome relationship a different
process; blending it in would corrupt the very signal under study.

The split was written down before results existed and was not chosen by
trying alternatives. The existing 2022–2024 / 2025 framework is a subset,
so results stay comparable if earlier seasons prove unavailable.

### Discipline enforced in code, not intention

- `assert_control_unchanged()` refuses to run against a drifted control.
- `ConfirmationLedger` raises `ConfirmationSpentError` on a second look
  at the confirmation season. A candidate that fails confirmation is
  **rejected** — retuning and re-running turns confirmation into
  development.
- One candidate family at a time. There is deliberately **no** function
  that sweeps feature subsets or hyperparameter grids: with enough
  combinations something always looks excellent, and its edge is
  selection, not signal.
- Every result carries the control version and hash it was measured
  against.

### Effect typing

`EffectType` forces the question *mean, uncertainty, both, or neither?*
A new quarterback might not justify subtracting points while genuinely
widening the error distribution. `UNDETERMINED_NO_DATA` is kept distinct
from `NEITHER` — the latter is a measured negative result.

---

## 5. Candidate results (Parts 4, 6, 11, 14)

| Candidate | Verdict | Effect type |
|---|---|---|
| `returning_production_broader` | `BLOCKED_NO_HISTORICAL_DATA` | `UNDETERMINED_NO_DATA` |
| `talent_composite` | `BLOCKED_NO_HISTORICAL_DATA` | `UNDETERMINED_NO_DATA` |
| `coaching_change` | `BLOCKED_NO_HISTORICAL_DATA` | `UNDETERMINED_NO_DATA` |

No control baseline metrics (Week 1 log loss, Brier, margin MAE/bias,
total MAE, coverage) are reported, because computing them requires the
same unavailable historical data. **Reporting estimated values would be
fabrication.**

---

## 6. Live 2026 research context flags (Part 12)

Observable from the genuine prospective corpus. **These are research
context flags, not betting opportunities, and no wager is implied.**

### Flag 1 — the control's only preseason input is inactive

`qb_status_confirmed` is **`False` on all 1,115 priced observations**.
That flag is true only when *both* teams' continuity states are known, so
at least one side of every priced game resolved to `UNKNOWN` — meaning
returning-production data was unavailable at pricing time.

Consequence: every game receives the `UNKNOWN` **1.20** uncertainty
multiplier rather than a differentiated 1.00 / 1.10 / 1.20. The control's
single preseason signal is, in live 2026, carrying no differentiating
information at all — it is applying one blanket widening everywhere.

This is the most exposed point in the control, and it is exactly where a
validated returning-production feature would act.

### Flag 2 — no 2026 on-field information exists in any point estimate

Definitional at Week 1: with zero games played, `season_carryover_weight`
is 0. (`early_season_prior_weight ≈ 0.07` on the observations is the
ratings snapshot's *internal* blend between its own fitted season and the
one before it — it is **not** 2026 information.)

### What cannot be flagged

Per-team returning production, talent, coaching changes and roster
movement for 2026 are **not** observable from this environment, for the
same egress reason. Naming specific exposed teams would require the data
the mission is blocked on.

---

## 7. Leakage audit (Part 16)

| Feature | Guard | Status |
|---|---|---|
| Returning production | dated to `applies_to_season − 1` | **PASS** (tested) |
| Talent | dated to prior signing cycle | **PASS** (tested) |
| Coaching change | reads only S and S−1, never S+1 | **PASS** (tested) |
| Any feature | `validate_for()` raises on derived ≥ applies | **PASS** (tested) |
| Feature table | rejects season mismatch at build | **PASS** (tested) |
| Missing values | never imputed | **PASS** (tested) |

Guards **raise** rather than filter. A leak that produces a slightly
optimistic number is more dangerous than one that crashes: the crash gets
fixed, the optimistic number gets published.

---

## 8. Verdict

> **MORE PRESEASON RESEARCH REQUIRED — blocked on historical data access,
> not on evidence against the candidates.**

> **NO PRESEASON PRIOR CANDIDATE EARNED PROMOTION TO SHADOW.**

No shadow model was created. Part 13 is explicit that one must not be
created merely to exist, and no candidate has survived confirmation —
none has even been measured.

### Next step

Obtain a leakage-safe historical export for the research seasons and
place it at `data/research_cache/preseason/`. Then:

```bash
python3 scripts/research_preseason_prior.py --json-out preseason.json
```

The control freeze, splits, guards, metrics and confirmation ledger are
all in place and tested. The research is one dataset away from running.
