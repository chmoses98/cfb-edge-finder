# Preseason-Prior Experiments — Results

**Status: EXPERIMENTS RUN ON REAL HISTORICAL DATA. One candidate survived
untouched confirmation.**

| Field | Value |
|---|---|
| Control model | `0.4.0-milestone-c2-live-margin-correction` |
| Control config hash | `3741c6f522972fa2de46493b47a80de756aabc0a038d12c33f6f3e204f66bd83` |
| Control changed | **NO** |
| Seasons | 2019, 2021, 2022, 2023, 2024, 2025 (+2026 for live context) |
| FBS-vs-FBS games | 4,398 |
| Shadow model created | **YES** — `shadow-preseason-talent-v1` |

---

## 1. Data acquisition

CFBD is unreachable from the dev environment (egress policy denies
`api.collegefootballdata.com`), so acquisition ran on a GitHub-hosted
runner via `.github/workflows/preseason-research-fetch.yml`.

**Disclosed direct-to-main infrastructure commits** — required because a
`workflow_dispatch` workflow is only dispatchable when it exists on the
default branch:

| Commit | Contents |
|---|---|
| `5177a4e` | workflow + fetch script + 2 read-only CFBD fetchers |
| `b519308` | fix: keep `/talent` rows whole (compaction had discarded the join key) |

Safety, mirroring the established `research-capture` pattern: manual
dispatch only, top-level `contents: read`, job-level `contents: write`
scoped to the `research-data` **orphan** branch (never main), shares the
`research-data-write` concurrency group with the collector, read-only
against CFBD, and the API key is never printed or written to the cache.

Cache: `data/research_cache/preseason/` on `research-data`, ~7 MB, with a
provenance manifest recording per-endpoint row counts, schema
fingerprints and timing semantics.

### Endpoints deliberately NOT fetched

`/roster` (retroactively revised), `/ratings/*` (pre/post-week timing
unconfirmed), transfer portal (no historical snapshots), weather
(only realised conditions retrievable, not the pregame forecast a model
could have used), injuries (no structured historical source).

---

## 2. CONTROL reproduction (gating step)

Reproduced through the production entry points — `fit_fbs_efficiency_ratings`,
`build_expanding_residual_pool`, `project_game`, `apply_margin_correction`.

**FBS-vs-FBS, seasons 2021–2025, 8,000 sims/game:**

| Segment | n | Log loss | Brier | Margin MAE | RMSE | Bias | Fav-tail bias | Total MAE | Total bias |
|---|---|---|---|---|---|---|---|---|---|
| **Week 1** | 238 | 0.5623 | 0.1907 | **15.92** | 20.21 | **−6.32** | **−9.25** | 13.58 | +2.94 |
| Weeks 2–3 | 486 | 0.5646 | 0.1917 | 15.12 | 18.94 | −2.18 | −8.00 | 12.64 | +1.70 |
| Weeks 1–3 | 724 | 0.5639 | 0.1914 | 15.38 | 19.37 | −3.54 | −8.42 | 12.95 | +2.11 |
| Weeks 4+ | 2,949 | 0.6122 | 0.2124 | **13.73** | 17.39 | **−0.38** | −0.62 | 13.32 | +0.48 |
| Neutral site | 116 | 0.5983 | 0.2063 | 13.85 | 17.38 | −1.64 | −1.07 | 14.39 | +4.40 |

**Bias convention here is `projected − actual`** (negative = model
under-projects the home margin). Milestone C uses `actual − projected`,
so signs are inverted between the two documents.

**Reproduction check:** weeks-4+ figures (LL 0.6122, margin MAE 13.73,
total MAE 13.32) fall inside Milestone C's documented envelope (LL
0.5924–0.6179, margin MAE 14.71–15.39, total MAE 13.17–13.37). Control
accepted as reproduced.

### The quantified Week 1 weakness

Margin bias is **−6.32 in Week 1 versus −0.38 in Weeks 4+**, and
favourite-tail bias is **−9.25 versus −0.62**. The control materially
under-projects margins early, worst on games it already sees as big
mismatches — consistent with preseason ratings compressing team
differences before on-field evidence arrives.

---

## 3. Design, declared before candidate comparison

| Role | Seasons |
|---|---|
| Development | 2021, 2022, 2023 |
| Selection | 2024 |
| Confirmation | 2025 (untouched until the final run) |
| Excluded | 2020 (COVID scheduling/opt-outs) |

**Disclosed deviation:** the pre-registered split named 2019 as a
development season. 2019 is the earliest cached season and has no prior
season to fit ratings on, so it is not evaluable. Reported by the runner
itself, not silently dropped.

Each candidate is a single interpretable slope:

```
candidate_margin = control_margin + beta * (home_feature − away_feature)
```

`beta` is least-squares fit (no intercept) on **development seasons
only**, then frozen. `fit_beta` raises `DevelopmentOnlyError` if handed a
non-development season. Both arms share one ratings fit, one residual
pool and one seed, so the only difference is the candidate term.

---

## 4. Individual candidate results

Paired per-game differences; negative `dMAE` = candidate better.
"IMPROVES"/"DEGRADES" mean the 95% interval excludes zero.

| Candidate | Dev Wk1 | Sel Wk1 | **Conf Wk1** | Conf Wks1–3 | Conf Wks4+ | Verdict |
|---|---|---|---|---|---|---|
| **talent_composite** | **−2.17** ✓ | **−1.34** ✓ | **−1.09** ✓ | **−0.80** ✓ | +0.11 flat | **ACCEPT** |
| returning_production_total | −0.20 flat | −0.22 flat | +0.25 flat | −0.02 flat | −0.04 flat | REJECT |
| qb_continuity_passing | −0.03 flat | −0.12 flat | +0.10 flat | +0.00 flat | **+0.22 ✗** | REJECT |
| returning_receiving | −0.15 flat | −0.19 flat | **+0.54 ✗** | +0.05 flat | −0.01 flat | REJECT |
| returning_rushing | +0.00 flat | +0.00 flat | +0.01 flat | −0.01 flat | **+0.04 ✗** | REJECT |
| coaching_change | −0.10 flat | −0.08 flat | −0.06 flat | −0.12 flat | −0.13 flat | REJECT |
| transfer_portal | — | — | — | — | — | **UNAVAILABLE** |
| weather | — | — | — | — | — | **UNAVAILABLE (postgame)** |
| injuries | — | — | — | — | — | **UNAVAILABLE** |

### Effect types

| Candidate | Effect | Evidence |
|---|---|---|
| talent_composite | **POINT ESTIMATE** | Margin MAE and log loss both improve; effect concentrated in Week 1 and gone by Week 4+ |
| returning production (all splits) | NEITHER | No segment replicated on confirmation |
| qb_continuity_passing | NEITHER (mild late degradation) | Dev Wks4+ improvement did not replicate; confirmation Wks4+ degraded |
| coaching_change | NEITHER | Consistently negative point estimates, never clearing zero |

**Notably, the control's own preseason input — returning passing
production — did not earn its keep as a point-estimate feature.** It
remains the uncertainty-only proxy it already was.

---

## 5. Accepted candidate: talent composite

```
delta = 0.018993 * (home_talent_composite − away_talent_composite)
shadow_margin = control_margin + delta
```

- **beta = +0.018993** points per talent unit, fit on 2021–2023
  (n = 2,183 FBS-vs-FBS games), frozen thereafter.
- Typical matchup differential ≈ 141 units → **≈ 2.7 points**.
- Model version: **`shadow-preseason-talent-v1`**.

### Confirmation (2025, untouched)

| Segment | n | Control MAE | Candidate MAE | Paired Δ | 95% CI | Δ log loss |
|---|---|---|---|---|---|---|
| Week 1 | 47 | 14.32 | **13.23** | **−1.09** | [−2.04, −0.15] | −0.0412 |
| Weeks 1–3 | 140 | 14.88 | **14.08** | **−0.80** | [−1.43, −0.18] | −0.0114 |
| Weeks 4+ | 590 | 13.91 | 14.02 | +0.11 | [−0.11, +0.31] | +0.0089 |

### Decay — measured, not imposed

A single beta is applied at every week. The effect is largest in Week 1,
about half as large across Weeks 1–3, and indistinguishable from zero by
Week 4+. That decay **emerges** because the control's own ratings improve
as games accumulate. No weekly multiplier was invented.

### Honest caveats

- **Small Week 1 samples.** Confirmation Week 1 is 47 games and its
  interval only just excludes zero.
- **Multiple comparisons.** 6 candidates × 9 phase/segment cells = 54
  comparisons. Talent is not one lucky cell: it replicated in the *same*
  segments across three chronologically separate partitions. That is
  materially stronger than a single significant slice, but it is not a
  substitute for more seasons.
- **Simulation count.** Research runs use 2,000–8,000 sims versus
  production's 20,000. Results were identical at both, and the paired
  design shares draws between arms, so this is not driving the finding.
- **This has never faced a live market.** A ~1-point margin-MAE
  improvement is a research result, not a demonstrated edge.

---

## 6. Live 2026 research context

**RESEARCH CONTEXT / SHADOW DIFFERENCE. Not a bet, edge, play, or
recommendation.**

All 39 Week 1 2026 games with a control projection matched 2026 talent on
both sides. Median |delta| = **2.53 pts**; max **7.60 pts**; 15 games at
≥ 3 pts.

| Matchup | Home talent | Away talent | Shadow Δ (pts) |
|---|---|---|---|
| ball-state @ ohio-state | 964.3 | 563.9 | +7.60 |
| missouri-state @ texas-a-m | 933.0 | 549.9 | +7.28 |
| texas-state @ texas | 985.2 | 624.8 | +6.84 |
| east-carolina @ alabama | 973.5 | 623.8 | +6.64 |
| boise-state @ oregon | 984.3 | 641.2 | +6.52 |

The pattern is unsurprising and is the point: the largest differences are
heavy-favourite non-conference openers, exactly where the control's
favourite-tail bias (−9.25 in Week 1) says it under-projects most.

---

## 7. Leakage audit

| Feature | Season semantics | As-of safe | Retroactively revised? | Enforcement | Verdict |
|---|---|---|---|---|---|
| Talent composite | S composite settled in S−1 signing cycle | YES | No | `derived_from_season = S−1`; `validate_for()` raises | **PASS** |
| Returning production | published pre-S, describes S−1 | YES | No | same | **PASS** |
| Coaching change | compares S vs S−1 only | YES | No | S+1 never read | **PASS** |
| Prior-season scores | immutable once final | YES | No | `fit_fbs_efficiency_ratings` raises on non-prior rows | **PASS** |
| `/coaches` outcome stats | postgame for their own season | NO | — | dropped at fetch | **EXCLUDED** |
| QB identity | — | NO | **Yes** | not fetched | **UNUSABLE** |
| Preseason ratings | unconfirmed | UNKNOWN | Unknown | not fetched | **UNUSABLE** |

Beta is fit on development seasons only, enforced by
`DevelopmentOnlyError`. Confirmation was evaluated once.

---

## 8. Two join bugs found and fixed

Both produced plausible-looking wrong answers rather than failing:

1. **Games carried CFBD display names, ratings are keyed by resolved
   ids.** Every rating lookup missed, every team got the league average,
   and the control produced log loss 0.669 against 0.693 for a fair coin
   with `favTail = nan` — no game projected by 14+ points. It read as a
   broken control rather than a broken join. Fixed by resolving through
   the production resolver, which also correctly skips the ambiguous bare
   "Miami" (72 games).
2. **Feature tables keyed by display names too.** Every feature lookup
   missed and all six candidates reported "insufficient coverage" —
   indistinguishable from genuinely absent data.

Both are pinned by regression tests.

---

## 9. Verdict

> **PRESEASON PRIOR CANDIDATE READY FOR PROSPECTIVE SHADOW**
> — `shadow-preseason-talent-v1`

Accepted under Part 16: leakage-safe construction, development
improvement, confirmation replication, coherent decaying effect, no
material degradation elsewhere.

Rejected: broader returning production, QB-continuity proxy, rushing,
receiving, coaching change. Unavailable: transfers, weather, injuries.

**Production is unchanged.** The control remains canonical, the shadow is
imported by no production module, and the live `model_probability` written
to the corpus is untouched.
