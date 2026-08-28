# Prospective CONTROL vs TALENT-SHADOW Capture

**Purpose:** record both arms side-by-side at every future supported
checkpoint, so 2026 becomes **untouched confirmation evidence** for
`shadow-preseason-talent-v1`.

The shadow has earned prospective testing. It has **not** earned
production promotion.

| Field | Value |
|---|---|
| Control (canonical) | `0.4.0-milestone-c2-live-margin-correction` |
| Control spec sha256 | `e18db59c02e0280c8fea4040dd6b95ea6809e3c7f9f517de44553aa30ec373c8` |
| Shadow (research) | `shadow-preseason-talent-v1` |
| Shadow spec sha256 | `af88af99eadae807daf06d865241b6ac5e87ebdc128ae7a9efc51bd745f70985` |
| Frozen beta | `0.018993` |
| Hypothesis sha256 | `8c4702cf7a705145d14b1d2ec64aeaecb93827710dc9621d9543e7c3d34f253f` |
| Settled 2026 games at registration | **0** |

---

## 1. Both arms frozen

`research/preseason/shadow_spec.py` describes each arm and hashes it.
`assert_specs_frozen()` refuses to capture if either moves — a
side-by-side comparison where one side quietly changed is not a
comparison.

### The beta reproducibility gap, recorded not glossed

`TALENT_BETA = 0.018993` was fit against CONTROL residuals, and those
residuals carry Monte Carlo noise. Refitting the same development data at
8,000 simulations instead of 2,000 gives **0.018898** — a 0.5% difference,
about **0.013 points** on a typical 2.7-point adjustment. Immaterial to
every conclusion, but recorded in `BETA_FIT_PROVENANCE` because a
"frozen" constant that cannot be reproduced from the cache without also
knowing the simulation count is not fully frozen.

---

## 2. Architecture: linked records, not new fields

A shadow record is a **separate row linked by `observation_key`**, not a
field inside the canonical observation.

| Property | Why |
|---|---|
| Canonical rows stay byte-identical | A reader that knows nothing about shadows parses the corpus exactly as before; v1/v2 rows are never rewritten |
| Shadow can fail alone | Missing talent yields a shadow record with a reason; the control row still lands |
| Shadow is disposable | It can be re-derived or dropped without risking the prospective evidence |

**`model_probability` remains CONTROL.** Nothing in the shadow modules
writes a corpus row; `build_shadow_record` takes the control's numbers as
inputs and returns a new object. A test asserts the shadow record does not
even *define* a `model_probability` key, so a careless join cannot
overwrite the control.

### Fields recorded

`observation_key` · `game_id` · `timing_label` · `captured_at` ·
`market_ticker` · `market_family` · executable YES/NO prices ·
control model version / probability / margin · shadow model version /
probability / margin · talent home / away / differential ·
talent source version · beta · shadow−control probability ·
shadow−control margin · availability + reason · capture mode · code SHA ·
provenance.

---

## 3. Fail-closed coverage

| Reason | Meaning |
|---|---|
| `TALENT_MISSING_HOME` / `_AWAY` / `_BOTH` | No talent value; **no adjustment**, never a silent zero |
| `TALENT_SEASON_MISALIGNED` | Talent row does not precede the predicted season |
| `UNSUPPORTED_POPULATION` | Not FBS-vs-FBS; the candidate was validated there only |
| `CONTROL_NOT_PRICED` | No control projection to adjust |
| `NOT_PROSPECTIVE` | Refused outright — raises |
| `CAPTURED_AT_OR_AFTER_KICKOFF` | Not pregame |

*"We had no talent data" and "talent said these teams are equal" are
different claims.* Collapsing them to a zero delta would corrupt the very
coverage statistics that say how much of 2026 the shadow actually saw.

---

## 4. Preregistered hypothesis

Registered with **0 settled 2026 games and 0 CLOSING captures** in the
corpus, so it is a genuine prediction.

> **Primary.** For supported FBS-vs-FBS games in Weeks 1–3 of 2026,
> `shadow-preseason-talent-v1` will reduce absolute margin error relative
> to CONTROL, measured as a paired per-game difference with a
> game-clustered interval.

**Secondary:** improve winner Brier/log loss · reduce favourite-tail bias
· improve contract-level calibration · produce model-market residuals
better aligned with closing prices.

### Explicitly prohibited

- refitting beta on 2026
- changing decay based on 2026 outcomes
- selecting games, weeks or families where the shadow looks good
- modifying the shadow because Kalshi disagrees with it
- reporting a direction before the interval is computed

Market families, weeks and timing labels were all fixed **before** any
result existed.

---

## 5. Analytics at n = 0

`compare([])` returns `INSUFFICIENT_NATURAL_EVIDENCE` and **no numbers at
all**. Reporting a delta of 0.0 would invite a reader to treat absence of
measurement as a measured null. There is a real difference between "we
measured no effect" and "we have not measured", and only the second is
true today.

---

## 6. Current 2026 snapshot

`scripts/shadow_snapshot.py` — research only, sorted by `game_id`, never
by delta. A delta-sorted table is an opportunity ranking wearing a
research header.

- Contracts evaluated: **751**
- Shadow available: **751** (coverage **100%**)
- Unavailable: **0**
- Distinct games: **86**
- Median |margin delta|: **2.06 pts** · max **7.60 pts**

The largest differences are heavy-favourite non-conference openers —
exactly where the control's Week 1 favourite-tail bias (−9.25) says it
under-projects most. **Not a bet, edge, play, or recommendation.**

---

## 7. Live scanner wiring

`research_scan_and_capture.py` now emits a linked shadow record after each
canonical observation is built.

### Sidecar, not a dependency

| Guarantee | Mechanism |
|---|---|
| Canonical capture never blocked | `_build_shadow_sidecar` returns `None` on any problem; the hook is guarded on it |
| Shadow failure never propagates | `for_contract` catches everything and returns `None` |
| Failures stay diagnosable | The exception **type** is recorded, not just a count |
| Canonical rows written first | Shadow persistence runs after `append_observation_rows`, and is itself wrapped |

During development the broad `except` swallowed an `AttributeError` from a
typo, and only the failure counter revealed anything was wrong — which is
exactly why `failure_types` now exists.

### One transform per game, not per ticker

Measured on the real 2026 corpus: **94 game transforms for 1,115
contracts** (0.084 per contract). Overhead **0.028 ms/contract**, 0.031 s
total. The scanner already builds one `CorrectedGameProjection` per game;
the shadow mirrors that exactly.

### Transformation order, traced not assumed

The historical runner built margins as `raw + margin_delta` and then
added the talent delta, so the delta is applied **after** the C.2 margin
correction. That ordering could not be distinguished historically — C.2
was a **no-op for every evaluated season** (artifact cutoff
`AsOf(2026, 0)`; measured mean `|margin_delta| = 0.000`). For 2026 it
becomes active, so the order was resolved from what the code *did*.

### A real inconsistency in the historical winner channel

The historical CONTROL probability came from `prob_home_win()`, which
splits simulated ties 50/50; the SHADOW used `mean(margin > 0)`, where
ties resolve to AWAY. Measured gap: **mean |Δp| = 0.0095**, exactly half
the 1.89% simulated tie mass — and it ran **against** the shadow, so the
historical log-loss gain was achieved despite a small handicap.

Live capture therefore records **three** probabilities: the canonical
control value (unchanged, tie-split), a comparison **basis** control
value computed the same way as the shadow, and the shadow value. Paired
comparisons use basis-vs-shadow so the arms differ only by the talent
delta.

### Append-only dedup

Key: `observation_key | shadow_model_version`. A retry writes **0**
duplicate rows; a future candidate version coexists rather than
overwriting this one's evidence. Shadow rows live in
`data/research/shadow/` — a separate file, so canonical observations stay
byte-identical.

### Deployment boundary

`shadow_capture_started_at` is stamped on every record. Absence before
deployment is **expected, not missing data**.

## 8. Reconstructed vs captured evidence

| Provenance | Admissible as headline prospective evidence |
|---|---|
| `PROSPECTIVE_SHADOW_CAPTURE` | **YES** — written by the live scanner before the game |
| `RECONSTRUCTED_RESEARCH` | **NO** — re-derived after the fact |

`compare()` drops reconstructed rows by default. A reconstructed value
was computed after the outcome existed and could have been computed
differently; letting it stand beside captured rows would quietly convert
the prospective test into a retrospective one. `shadow_snapshot.py`
output is reconstructed and says so.

## 9. What this does not do

No qualification, no ranking, no stake, no execution. The only question
is **CONTROL vs TALENT SHADOW**. A test greps the shadow modules for
qualification/stake/bankroll vocabulary and fails if any appears.

## 9. The live wiring defect the first real run exposed

The first Research Capture run on `main` after the sidecar merged
(workflow run `33189458187`, head `52c0cf5`) wrote **158 canonical
observations and 0 shadow rows**, reporting:

```
"shadow_games_offered": 0, "shadow_failures": 0,
"shadow_contracts_priced": 0, "shadow_rows_written": 0
```

Every counter read 0, which is also exactly what a healthy run with
nothing eligible produces. The log could not distinguish the two.

**Root cause.** `main` calls `ensure_branch_checked_out` *before*
`_apply_scan`. That runs `git checkout -B research-data`, which replaces
the working tree — including the editable install's `src/` — with the
`research-data` branch's own stray `src/` snapshot. That snapshot is a
fossil of the old stray-source-tree incident: 82 files, carrying
`research/` but **no `research/preseason/` package at all**.

Modules already in `sys.modules` survive the swap. A function-local
import does not. `_build_shadow_sidecar` imported
`research.preseason.corpus` lazily, so on every real run that import
raised `ModuleNotFoundError`, the broad `except` swallowed it, and the
sidecar was silently `None` — while every test passed, because no test
checks out the data branch mid-run.

Measured: `cfb_edge_finder.research.preseason.corpus` is the one
dependency not already bound at scanner import time, and it is absent
from the stale tree.

**Fixes.**

1. Every `cfb_edge_finder` import in the scanner is now module-level, so
   the modules bind while main's tree is still on disk. The cache *read*
   stays where it was — that JSON exists only on `research-data`, and a
   file read is not an import. Verified by simulating the swap: the
   builder returns `ACTIVE` with 137 teams at beta 0.018993 where it
   previously returned `None`.
2. `_build_shadow_sidecar` now returns `(sidecar, state)`. A `None`
   sidecar names its reason (`UNAVAILABLE_ModuleNotFoundError`,
   `UNAVAILABLE_NO_CACHE_FOR_SEASON_2026`, …), surfaced as
   `shadow_sidecar_state`. `shadow_failure_types` and
   `shadow_unavailable_reasons` are surfaced too.
3. Regression tests assert each required module is bound at scanner
   import time, and that the scanner contains **no** function-local
   `cfb_edge_finder` import — the shape of the bug, not just this
   instance of it.
4. And the test that would actually have caught it: every check above
   inspects the *current* process, where the working tree never moves —
   which is exactly why 2,079 tests passed while the real workflow
   failed. `test_sidecar_builds_after_a_real_data_branch_checkout` builds
   a throwaway repo with a real remote, a code branch and an orphan data
   branch carrying a stale `src/`, then in a subprocess imports the
   scanner, calls the **real** `ensure_branch_checked_out`, and only then
   builds the sidecar. Measured: pre-fix `SIDECAR=NONE`, post-fix
   `SIDECAR=BUILT STATE=ACTIVE BETA=0.018993`.

**Not fixed here:** the stale `src/` tree on `research-data` is still
there. Removing it would be a separate, deliberate change to the data
branch; the import fix makes the scanner correct regardless of what that
branch carries, which is the more robust guarantee.

## 10. Probability semantics: v1 defect, v2 repair

The first 12 genuine prospective rows exposed a second defect — this one
in what the shadow's probability *meant*.

### The defect

v1 computed one number per game:

```python
shadow_probability = mean(control_margin_samples + delta > 0)   # P(HOME wins)
```

and wrote it onto **every contract on that game**. Real captured rows:

| game | control | shadow (v1) |
|---|---|---|
| boise-state-at-oregon | 0.1185 / 0.8699 | **0.9315 / 0.9315** |
| texas-state-at-texas | 0.0452 / 0.9491 | **0.9790 / 0.9790** |

So on an away-side row `shadow_minus_control_probability` compared
P(home wins) with P(away wins) — not a paired delta.

Reproducing across families showed the blast radius was **wider than the
moneyline** where it was first spotted. Every contract received the
winner probability:

```
GAME-HOME    control=0.7900  shadow=0.8566
GAME-AWAY    control=0.2000  shadow=0.8566
SPREAD-HOME  control=0.5500  shadow=0.8566
SPREAD-AWAY  control=0.4400  shadow=0.8566
TOTAL-OVER   control=0.5000  shadow=0.8566
TOTAL-UNDER  control=0.4900  shadow=0.8566
```

Only the home-side winner contract was ever correct.

### The repair

The canonical arm prices analytically, not by Monte Carlo:

```python
distribution = cached_projection.projection.to_game_distribution()
price_parsed_contract(parsed, distribution, named_team_side=side)
```

`price_parsed_contract` already encodes every supported proposition —
P(named team wins), P(named team wins by strictly more than T) with the
spread sign derivation, P(total over/under T) — each with the same
continuity correction. So the shadow re-implements none of it: it builds
the **same** `GameDistribution` with the talent delta applied and calls
the **same** function with the **same** parsed contract and resolved
side. Orientation, tie handling, threshold semantics and market inputs
are identical *by construction*.

The delta is applied exactly the way `CorrectedGameProjection` applies
the C.2 correction — `home_mean += delta/2`, `away_mean -= delta/2`, SDs
and correlation untouched — so a talent shift and a margin correction
mean the same thing to the rest of the system.

### The three channels

| field | meaning |
|---|---|
| `control_probability_canonical` | what production wrote. Audit only, never the counterfactual. |
| `control_probability_basis` | control priced through the identical pricer/contract/side as the shadow. |
| `shadow_probability` | the same, on the talent-shifted distribution. |
| `shadow_minus_control_basis_probability` | **the experimental delta.** |

### Totals are unchanged — a result, not an oversight

The frozen candidate moves margin and preserves total, so it makes **no
prediction about totals**: a total contract's shadow probability equals
its basis probability exactly. Asserted by test.

### Versioning

`shadow_model_version` stays `shadow-preseason-talent-v1`. Beta stays
0.018993. This is **instrumentation** versioning:

| version | margin channel | probability channel |
|---|---|---|
| `shadow_observation_v1` | **valid** | **defective** for every orientation except home-side winner |
| `shadow_observation_v2` | valid | contract-oriented; canonical/basis/shadow triple persisted |

The first 12 rows are **never rewritten**. The dedup key
(`observation_key\|shadow_model_version`) is unchanged, so no v2 duplicate
can be created for them either.

### Channel-aware analytics

`compare()` now decides eligibility **per channel**, because the v1
defect never touched the margin:

```
n_margin_paired       : 3
n_probability_paired  : 1
probability_state     : MEASURED
probability_exclusions: {"PROBABILITY_SEMANTICS_V1": 2}
```

Excluded rows are counted and named, never silently dropped. With no
eligible probability rows the channel returns
`INSUFFICIENT_NATURAL_EVIDENCE` and no numbers — the margin channel still
reports.
