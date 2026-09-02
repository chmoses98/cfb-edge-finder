# CFB Model Repair — Early-Season Talent Prior

**Verdict: ONE candidate promoted. `0.5.0-early-season-talent-prior`.
Totals remain unrepaired and are documented as such.**

| Field | Value |
|---|---|
| Starting main | `fe4e535` |
| research-data | `bd36a184` |
| Control | `0.4.0-milestone-c2-live-margin-correction` |
| Control config hash | `3741c6f522972fa2de46493b47a80de756aabc0a038d12c33f6f3e204f66bd83` |
| Candidate spec sha256 | `d0f11ac7e427ce37668c9b65a9d90a410dcabaa7bfa9d7905491e4b210dbc0c8` |
| CFBD requests | **0** (durable preseason cache only) |

The candidate registry, synthetic grid, clustering rules, folds and
acceptance gates were written and hashed **before any candidate result
existed**. No gate was changed after seeing results.

---

## 1. Control reproduction (gating step)

Reproduced through the production entry points
(`fit_fbs_efficiency_ratings` → `build_expanding_residual_pool` →
`project_game` → `apply_margin_correction`), 2021-2025, FBS-vs-FBS:

| Segment | n | LL | Brier | marginMAE | RMSE | bias | favTail | totMAE | totBias |
|---|---|---|---|---|---|---|---|---|---|
| Week 1 | 238 | 0.5627 | 0.1909 | 15.92 | 20.21 | −6.32 | −9.24 | 13.59 | +2.96 |
| Weeks 4+ | 2,949 | 0.6123 | 0.2125 | 13.74 | 17.39 | −0.42 | −0.68 | 13.32 | +0.48 |

Against the documented reference (LL 0.5623, Brier 0.1907, MAE 15.92,
bias −6.32, favTail −9.25, totMAE 13.58, totBias +2.94) every figure
lands inside Monte Carlo noise. **Control accepted as reproduced.**

---

## 2. What was actually wrong

Two things were widely assumed and both turned out to be **false**.

### The live path has no probability calibration at all

`modeling/calibration.py`'s Platt layer is called from exactly one
place — `backtest.py:351`. The deployed Kalshi pricing path is
`GameProjectionCache.get_or_build` → `price_one_market` →
`price_parsed_contract` → `projections.distribution.price_market`, a
normal CDF with a 0.5 continuity correction. **No family is
recalibrated in production.** Confirmed empirically: inverting the
captured Week 0 ladders recovers (μ, σ) with a fit residual of
**0.000**, which is only possible if the deployed pricer is exactly
that closed form.

### The variance was already honest — this was a point-estimate failure

Deployed σ against **bias-corrected** realised error, 2021-2025:

| Segment | channel | deployed σ | realised σ | ratio |
|---|---|---|---|---|
| Week 1 | margin | 19.32 | 19.21 | 0.994 |
| Week 1 | total | 17.64 | 17.00 | 0.964 |

Both inside 8%. The predeclared gate for Candidate F (variance repair)
therefore did not fire on the segment of interest, and where F was
tested it made calibration **worse**. The natural first guess — that
96% probabilities came from a too-narrow distribution — is wrong.

### So why *could* CONTROL say P(Over 27.5) = 96.3%?

Because the mean was wrong, not the spread. For UNC-TCU the deployed
distribution was **μ = 58.97, σ = 17.39**; 27.5 sits 1.79σ below that
mean, so 96.3% is arithmetically correct *given* the projection. The
actual total was 25. The defect is the +8.4-point total over-projection
on that slate (historically +2.96 in Week 1, and worst of all at
neutral sites, +4.41), not the width of the distribution.

Historical confirmation that this is systematic, Week-1 totals,
2021-2025 — **every bin over-predicts**:

| bin | predicted | observed | gap |
|---|---|---|---|
| 0.20-0.40 | 0.2994 | 0.2436 | −0.0558 |
| 0.40-0.60 | 0.4999 | 0.4162 | −0.0837 |
| 0.90-0.95 | 0.9219 | 0.8627 | −0.0591 |
| **0.95-1.00** | **0.9631** | **0.8696** | **−0.0935** |

Week-1 total ECE is 0.0520 against 0.0113 in Weeks 4+. **The Week 0
catastrophe was not bad luck — it is a reproducible early-season
property of the totals channel.**

The spread channel fails in the mirror direction (observed *above*
predicted in the 0.4-0.9 bins), which is the margin under-projection
showing up as probability error.

---

## 3. Candidate results

Rolling-origin, every fold fit only on strictly-prior seasons.

| Candidate | Verdict | Why |
|---|---|---|
| **A — talent margin prior** | **ACCEPT** | Week-1 margin MAE improves in **4/4** folds; pooled paired Δ **−1.89 [−2.47, −1.31]**; Weeks 4+ flat in every fold; zero new fitted parameters |
| B — early-season total bias | REJECT | **Fails G1**: 2 improve / 2 flat. The bias is not stable — its sign flips (2022 actual −1.50 vs fitted +2.06, making it worse) |
| C — favourite-tail slope | REJECT | Gate fired, tested, **1/4 folds** improve. Talent already absorbs the effect |
| D — spread calibration | REJECT | 4/4 alone, but **after A the added effect is −0.0003 [−0.0008, +0.0003]** — statistically flat. Predeclared simplicity tie-break selects the model with fewer fitted parameters |
| E — total calibration | REJECT | Fails G1 and G4: worsens Brier in 2022/2023 and materially damages the low bins (0.05-0.10 gap −0.054 → **+0.099**) |
| F — variance/tail repair | REJECT | Diagnostic showed σ already honest; where tested it worsened Brier and ECE in every fold |

**Only A is promoted.** Components were not stacked automatically.

### CONTROL vs the promoted candidate (pooled out-of-sample, 2022-2025)

| Segment | metric | CONTROL | CANDIDATE |
|---|---|---|---|
| Week 1 | margin MAE | 15.59 | **13.70** (Δ −1.89 [−2.47, −1.31]) |
| Week 1 | margin bias | −6.07 | **−4.02** |
| Week 1 | favTail bias | −8.72 | **−7.57** |
| Week 1 | margin 90% coverage | 0.884 | **0.894** (target 0.900) |
| Week 1 | spread Brier | 0.1359 | **0.1206** (Δ −0.0152 [−0.0201, −0.0111]) |
| Week 1 | **total** Brier | 0.1940 | **0.1940 — unchanged** |
| Weeks 4+ | margin MAE | 13.58 | 13.66 (Δ +0.086 [−0.035, +0.207], flat) |
| Weeks 4+ | spread Brier | 0.1305 | 0.1309 (flat) |

---

## 4. Week 0 diagnostic replay — an honest conflict

The candidate was frozen on historical evidence before this ran. Week 0
fitted nothing.

| | CONTROL | CANDIDATE |
|---|---|---|
| margin MAE (5 games) | **12.20** | 13.79 |
| margin bias | −3.91 | **−2.45** |
| moneyline Brier (n=12) | 0.2828 | **0.2728** |
| spread Brier (n=174) | **0.2050** | 0.2106 |
| total Brier (n=114) | 0.2702 | 0.2702 |
| all Brier (n=300) | **0.2329** | 0.2357 |

**The candidate is slightly worse on Week 0 while winning decisively on
five seasons of history.** This is reported, not resolved away. The
driver is one game: New Mexico State at Florida State, where FSU's large
talent edge pushed the projection from +22.3 to +28.8 against an actual
+17. Five games cannot separate that from noise; 189 Week-1 games across
four out-of-sample folds can. The historical evidence is trusted, and
the conflict is a live caution rather than a settled matter.

### The UNC-TCU total ladder — unchanged, by design

| contract | CONTROL | CANDIDATE | market | settled |
|---|---|---|---|---|
| Over 27.5 | 0.963 | **0.963** | 0.91 | no |
| Over 36.5 | 0.897 | **0.897** | 0.76 | no |
| Over 46.5 | 0.754 | **0.754** | 0.50 | no |

The talent prior moves the margin channel and preserves the total
exactly. **No accepted candidate repairs this failure mode.** Candidate
B was the one that would have tried, and the evidence rejected it.

---

## 5. Week 1 market sanity check (descriptive only)

87 upcoming games, 1,622 markets. Nothing was tuned to the market.

| family | mean abs model-market gap | >20% disagreements |
|---|---|---|
| spread CONTROL | 0.1935 | 270 |
| spread CANDIDATE | **0.1722** | **226** |
| total (both) | 0.1446 | 116 |

The candidate independently moves toward the market on spreads, which is
what a genuinely more accurate point estimate should do. Totals are
untouched. Neither model produces a pathological "model ≥0.90 while
market ≤0.60" contract.

---

## 6. Production change

- `modeling/talent_prior.py` — new. Imports `TALENT_BETA` from
  `research.preseason.shadow_prior` rather than restating it, so the
  production constant cannot drift from the research record.
- `modeling/score_model.py` — `CorrectedGameProjection` gains
  `talent_margin_delta` (default **0.0**) and a `total_margin_delta`
  property. The default is what keeps CONTROL byte-identical through
  the patched code.
- `kalshi/game_projection_cache.py` — optional `talent_by_team`;
  applied FBS-vs-FBS only.
- `capture_kalshi_cfb_snapshot.py` — adds
  `TALENT_PRIOR_MODEL_VERSION` and `resolve_model_version`. The version
  is **derived from whether the prior actually ran**, so a run with no
  talent cache prices as, and is stamped as, the 0.4.0 control.
- `research_scan_and_capture.py` — loads talent through the production
  `FeatureTable.get` (whose `validate_for` raises on season
  misalignment), reports state in telemetry.

### The prospective boundary

`ObservationIndex.labels_by_ticker` is keyed by `(ticker, label)` and is
**not** model-version aware, and `resolve_due_labels` only offers a
label whose window is currently open. A label captured under 0.4.0
therefore still reads as captured under 0.5.0, and a passed window is
never re-offered. The new version can only capture forward from its
freeze. Both properties are pinned by tests.

---

## 7. Remaining weaknesses

1. **Totals are not repaired.** Week-1 total ECE stays 0.0520 and a
   model 95-100% total event still lands ~87% of the time. This is the
   single largest known defect and it is now measured rather than
   suspected.
2. **Week 0 disagrees with history** for the promoted candidate. Five
   games, but not nothing.
3. The model remains **worse than the executable market** on the Week 0
   corpus in every family, before and after the repair.
4. The Week-1 sample is small in every fold (38-53 games).
5. `EARLY_SEASON_UNCERTAINTY_SCALE` is effectively inert in production:
   `games_played_for` counts all historical rows, so a Week-1 team
   carries weight ≈0.93 and receives ~2% inflation rather than the
   intended 30%. Not repaired here — the variance diagnostic says the
   deployed width is already about right, so changing it now would
   break something that measures as correct. Recorded for the future.
