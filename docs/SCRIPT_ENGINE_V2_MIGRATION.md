# Script Engine V2 — production migration design

Status: **design of record for Wave 3**. Written before any production code changed.
Methodology: `cfb-script-engine/2.0.0` (V2 claims) beside `cfb-script-engine/1.3.0` (V1 scripts, unchanged).
Evidence: `docs/ARCHETYPE_V2_HOLDOUT_RESULTS.md`, `docs/ARCHETYPE_V1_V2_COMPARISON.md`, frozen parameters
`data/scripting/validation/archetype_v2_frozen_parameters.json` (SHA-256 `683d075d99efdfed16f7fb35a9fd58234808d1a354f8fb8e3352c64e972bf7aa`).

## 1. What this wave does, and does not, do

V2 is a different **reading** of the findings the unmodified V1 engine already produces. It adds no
metric, refits no threshold, and changes no part of the football pipeline above the findings
(opponent adjustment, matchup vector, findings, confidence gates and the 04:00 ET no-leakage cutoff
are byte-identical).

Deployment is staged (section 15). This wave ships **Stage 1 and Stage 2**:

* **Stage 1**: the production publisher builds, freezes, publishes and ledgers V2 claims *beside*
  V1 for every game. The V2 claims are marked `activation: "SHADOW"`.
* **Stage 2**: SIFT renders the V2 game read as a labelled preview under the V1 read.

V1 stays the **active** publication. That includes its scripts, its market map, its expression
labels (`BEST_EXPRESSION`, `MULTI_SCRIPT`, …) and its ledger stream. Two reasons:

* Activation would change the market labels SIFT shows. This wave must not change recommendations or
  staking.
* 2026 has no V2 prospective record yet, so V2 needs one before it replaces anything.

Activation (Stage 3) is a separate, reviewed change. Section 16 gives its gate.

## 2. Research roles (fixed)

| Block | Role | Used for |
|---|---|---|
| 2021–2025 | Development | The only seasons any V2 number was fitted on: the CONTROL distributions. |
| 2014–2020 | Out-of-time validation, opened once in Wave 2 | Validation evidence quoted beside the frozen values. **No longer sealed.** No Wave 3 decision is tuned on it and then reported as validated by it. |
| 2026 | Prospective | The V2 shadow ledger starts now. No 2026 outcome is read by any V2 rule or number. |

## 3. Final V2 claim taxonomy

Claims are **independent dimensions**. A game can carry any combination. Two rules hold throughout:

* No combination is a separately calibrated archetype.
* No claim carries a probability; `probability` is always `null`.

Every claim reads only findings in the frozen V1 football artifact.

| Family | Activation evidence (finding in the frozen V1 artifact) | Claims | Does NOT claim |
|---|---|---|---|
| **CONTROL** `side` ∈ {home, away}, `strength` ∈ {MODERATE, STRONG} | `{SIDE}_SUSTAINED_EFFICIENCY_ADVANTAGE`; `strength` is that finding's own strength | The side with the sustained-efficiency edge. Carries the frozen historical margin distribution of that tier (section 5). | A probability of winning, a covering line, a "pulls away" margin, or any band as a forecast. |
| **CLOSENESS** | `EVEN_MATCHUP` **and** `closeness_grants_margin(EVEN_MATCHUP)` (`abs(gap) + uncertainty ≤ 2.0`, the strong-edge threshold `NET_THRESHOLD[1]`): the production authorisation rule, unchanged | "The evidence supports a relatively close game." | A winner, an underdog keeping it close, a scoring level, or a margin band. "No CONTROL" never implies CLOSENESS, and `NARROW_EFFICIENCY_GAP` alone never activates it. |
| **PACE** `level` ∈ {HIGH, LOW} | `HIGH_POSSESSION_ENVIRONMENT` / `LOW_POSSESSION_ENVIRONMENT` | An expected possession / play-volume environment above or below the FBS norm. | "Over" / "under", a total band, or a margin. |
| **SCORING_ENVIRONMENT** `level` ∈ {ELEVATED, SUPPRESSED} | ELEVATED: `HIGH_SCORING_ENVIRONMENT` or `BOTH_OFFENSES_EFFICIENT` (STRENGTHENED when both are present, or HIGH is STRONG). SUPPRESSED: `LOW_SCORING_ENVIRONMENT`. Both activators present → no claim (abstention `MIXED`). | ELEVATED: a qualitative environment that adds information beyond the scoring baseline (`incremental_over_baseline: true`). SUPPRESSED: descriptive only, because the baseline already expects low scoring (`incremental_over_baseline: false`). | A numeric total band, or a margin. SUPPRESSED is never shown as additional predictive information. |
| **DEFENSIVE_SUPPRESSION** | `BOTH_DEFENSES_CONTROL` | Both defenses out-rate the offense they face; a lower-scoring tendency. Independent of the V1 GRIND exclusivity. | A numeric total band, a margin, or a winner. |
| **DISRUPTION_EDGE** (per side) | `{SIDE}_DISRUPTION_ADVANTAGE`, with **no volatility requirement** | The side has a repeatable pressure / disruption mechanism advantage. When CONTROL points the same way, it is listed as supporting that thesis (`aligned_with_control`). Volatility findings are shown as context only. | A winner. `winner_authority: false` always; disruption alone never creates a side read. |
| **EXPLOSIVE_UPSET** | **Unchanged V1 rule and V1 script**, carried verbatim when the V1 artifact publishes it | Exactly what V1 claims, with its V1 bands and V1 band authority. | Anything new. Status `UNTESTED_HISTORICALLY_DATA_UNAVAILABLE`: it is not evidence of failure, and it is not retired, recalibrated or strengthened. |

Fail-closed rules:

* **CONTROL found on both sides** (impossible by construction: one signed net decides the side): no CONTROL claim, abstention `CONTROL_BOTH_SIDES`.
* **CONTROL tier missing from the calibration artifact:** no historical range. The claim still stands; the range is null with a reason.
* **Calibration artifact hash mismatch:** the build raises. Nothing is published with an unverified range.

### What V2 does NOT read

Several findings are not activators. CONTROL lists them only as `context` for drill-down, and they
never gate or weight it:

* `{SIDE}_EARLY_DOWN_ADVANTAGE`, `_RUSH_`, `_PASS_`, `_FINISHING_`, `_EXPLOSIVE_`;
* `{SIDE}_SCORING_ADVANTAGE`, `_DEFENSIVE_CONTROL`;
* `HIGH_VARIANCE_MATCHUP`, `NARROW_EFFICIENCY_GAP`, and the dependence findings.

The historical range is conditional on the CONTROL finding **alone**. That is exactly how it was
fitted.

## 4. Retired / deprecated V1 concepts

These are retired **from the V2 taxonomy**. V1 keeps generating them until activation, so the active
publication does not change in this wave.

| V1 archetype | V2 status | Why |
|---|---|---|
| `HOME_CONTROL` / `AWAY_CONTROL` | **Replaced** by CONTROL{side, strength} | Same activating finding. STRONG vs MODERATE carries the useful information: ordering held in 7/7 holdout seasons in both directions. |
| `FAVORITE_PULLS_AWAY` | **Retired** (folded into CONTROL/STRONG) | Every V1 pulls-away was already a same-side CONTROL game. Directional accuracy is unchanged (82.1%). The second-edge requirement added no demonstrated value. Its 17–45 band was poorly calibrated. |
| `UNDERDOG_HANGS_AROUND` | **Retired** | No predictive lift in either historical block. Its counters did not add information. |
| `COMPETITIVE_TOSSUP` | **Retired** (replaced by CLOSENESS) | The standalone label showed no lift. Its useful part is EVEN_MATCHUP closeness. |
| `COMPETITIVE_SHOOTOUT` | **Retired as an atomic script**: decomposed into SCORING_ENVIRONMENT=ELEVATED (+ PACE, + CLOSENESS when each clears) | Lift was inconsistent by era (2014–2020 yes, 2021–2025 no). The components generalised independently. |
| `COMPETITIVE_GRIND` | **Retired as an atomic script**: decomposed into SCORING_ENVIRONMENT=SUPPRESSED (descriptive) / DEFENSIVE_SUPPRESSION / PACE=LOW (+ CLOSENESS) | Same era inconsistency. Its exclusivity with DEFENSIVE_SUPPRESSION swallowed most suppression games. |
| `PACE_DRIVEN_OVER` | **Renamed and narrowed** to PACE=HIGH; its total band is dropped | The football claim is possession volume. "Over" is a market word. Its band was uncalibrated. |
| `DEFENSIVE_SUPPRESSION` (V1 script) | **Kept as an independent claim**; its total band is dropped | Validated on its own (holdout realized-suppression lift 1.67, below median scoring in 7/7 seasons). The numeric band was uncalibrated. |
| `TURNOVER_DISRUPTION` | **Replaced** by DISRUPTION_EDGE (no volatility requirement, no winner, no band) | The disruption edge carried the information. Volatility's increment was weak. With no efficiency edge, the disruption side won only 51.3%. |
| `EXPLOSIVE_UPSET` | **Unchanged** | Untested historically: the data were unavailable. |

Machine-readable form: `claims_v2.retired_v1` lists each code and its V2 replacement, so a consumer
can translate.

## 5. CONTROL calibration contract

### 5.1 Promotion

This is the reviewed promotion step.

* **Source:** the Wave 2 frozen parameter file (hash above), verified on load.
* **Output:** the production artifact `data/scripting/calibration/control_margin_v2.json`, which is
  copied, not recomputed. Its fields:
  * `schema`: `cfb_control_margin_calibration/1.0.0`
  * `calibration_version`: `cfb-control-margin/2021-2025/1.0.0`
  * the exact development values;
  * the source hash;
  * the validation block, kept separately (copied from `archetype_v2_holdout_report.json`).
* **Hashing:** the artifact carries its own `sha256` over canonical JSON without that key, by the same
  rule as the research file.
* **Tests:**
  * byte/content stability;
  * an exact-value match against the research file;
  * the research file's hash is still `683d075d…`.

### 5.2 What is promoted, and what deliberately is not

Promoted, per tier:

* development `n`;
* win rate, with hits/n and CI95;
* median (`p50`) and mean;
* `central_50` = [p25, p75] and `central_80` = [p10, p90].

All values are in the **lead side's margin** (lead points minus opponent points).

Deliberately **not** promoted: the per-threshold rates (`p_margin_ge_3`, `_7`, … `_28`). Those are the
ingredients of a contract-level probability, and production must not derive a fair price from claim
frequency.

### 5.3 Values

These are the exact values, all fitted on 2021–2025.

| Tier | n | Win rate | Median | Central 50 | Central 80 | 2014–2020 validation: n, win, median, cov50, cov80 |
|---|---|---|---|---|---|---|
| HOME_CONTROL_MODERATE | 404 | 0.7995 | 12.0 | [3.0, 24.0] | [−7.0, 34.0] | 512, 0.7949, 13.0, 0.5254, 0.7852 |
| HOME_CONTROL_STRONG | 628 | 0.9013 | 23.0 | [8.0, 35.0] | [1.0, 48.0] | 824, 0.9102, 24.0, 0.5291, 0.8216 |
| AWAY_CONTROL_MODERATE | 404 | 0.6658 | 7.0 | [−4.0, 18.0] | [−13.7, 30.0] | 514, 0.6790, 7.0, 0.5370, 0.7840 |
| AWAY_CONTROL_STRONG | 378 | 0.8095 | 14.5 | [3.0, 27.0] | [−5.3, 37.0] | 552, 0.8460, 17.0, 0.5236, 0.7645 |

### 5.4 Wording contract

The range is a **"historical empirical range"**: margins realised by past games that carried the same
football claim. It is **not**:

* a prediction interval;
* a probability, or a fair price;
* an expected value, or a guarantee.

The win rate is a **historical conditional frequency**. SIFT must not render it as "X% chance to win"
or similar.

The payload carries these sentences verbatim in `historical_range.label` and `historical_range.not`.

## 6. Closeness, pace, scoring, suppression, disruption semantics

Sections 3 and 4 give the activation rules. This section covers what each claim states and what
evidence it carries.

| Claim | Statement | Numeric content | Evidence carried |
|---|---|---|---|
| CLOSENESS | Fixed statement: "The evidence supports a relatively close game." | None | `EVEN_MATCHUP` (with its value/uncertainty) |
| PACE | "possession volume above / below the FBS norm" | None. The historical scoring correlation is shown as research context only, citing Wave 2. | The possession finding and its strength |
| SCORING_ENVIRONMENT ELEVATED | Qualitative | No band | Activators, plus PACE=HIGH as listed support |
| SCORING_ENVIRONMENT SUPPRESSED | Qualitative, with the explicit note "the scoring baseline already expects lower scoring; this adds no validated information beyond it" | No band | Activators |
| DEFENSIVE_SUPPRESSION | Qualitative | No band | `BOTH_DEFENSES_CONTROL` |
| DISRUPTION_EDGE | Mechanism sentence | None | The disruption finding. Volatility findings are listed as context; `aligned_with_control` is true, false, or null when there is no CONTROL. |

V2 claims are not double counted. SUPPRESSED and DEFENSIVE_SUPPRESSION can both appear, but:

* the story mentions the baseline-expected low scoring once;
* neither carries a number.

## 7. Confidence semantics

`data_confidence` (HIGH / MEDIUM / LOW) keeps its field name for compatibility. It is
**DATA-QUALITY CONFIDENCE**: how complete and stable the football evidence is (coverage, sample,
adjustment stability, availability, identity). Wave 2 confirmed that it is **not monotonic** in
outcome.

**OUTCOME STRENGTH** is expressed only by specific football evidence: CONTROL `strength`, with its
historical range.

In V2 the claims object carries:

* `data_quality: {level, describes, all_gates_pass, reasons}`, where `describes` = "the quality and
  completeness of the football evidence — never how likely a claim is to come true";
* `outcome_strength_source: "CONTROL.strength"`.

## 8. No-claim semantics

When no claim activates:

* `status` is `NO_SUPPORTED_CLAIM`;
* the statement is exactly: **"No supported matchup claim cleared the evidence requirements."**;
* the explanation is: "the current evidence taxonomy did not authorize a claim; this does not mean
  the game is unusually unpredictable."

EXPLOSIVE_UPSET (V1-carried) counts as a claim for status purposes.

## 9. Deterministic story synthesis

`story` is a presentation layer, not evidence.

* It is a pure function of the claims, using fixed templates and a fixed clause order:
  CONTROL → CLOSENESS → environment (PACE, SCORING_ENVIRONMENT, DEFENSIVE_SUPPRESSION) →
  DISRUPTION → EXPLOSIVE_UPSET.
* Every clause lists the claim keys it renders (`claims: [...]`).
* No clause introduces a winner, margin or scoring statement that a cited claim does not state.
* No combination gets its own name or probability.
* `story.generated_from` = "V2 claims only; deterministic templates; no market input; not a
  calibrated archetype".

Examples:

* **CONTROL(home, STRONG) + PACE HIGH + ELEVATED:** "{Home} holds a strong sustained-efficiency
  control edge, in a faster, elevated-scoring environment."
* **CLOSENESS + DEFENSIVE_SUPPRESSION + PACE LOW:** "Close-game profile, with defensive suppression
  and a slower possession environment."

## 10. Market authority (V2 shadow map)

The V2 map is built only after both artifacts are frozen. It reads the V1 market map's settlement
semantics (`wins_when`, `kind`) and never a price.

| Market family | CONTROL | CLOSENESS | PACE | SCORING_ENV | DEF_SUPPRESSION | DISRUPTION | EXPLOSIVE_UPSET |
|---|---|---|---|---|---|---|---|
| Moneyline | **DIRECT FOOTBALL AUTHORITY**: directional only, `ALIGNED` / `OPPOSED` | NO | NO | NO | NO | NO | V1 classification, unchanged |
| Spread / alt spread ladder / margin band | **RESEARCH CONTEXT ONLY**: the historical range is shown beside it; no per-contract relation is computed or scored | NO | NO | NO | NO | NO | V1 classification, unchanged |
| Total / alt totals | NO | NO | RESEARCH CONTEXT ONLY (qualitative) | RESEARCH CONTEXT ONLY (qualitative) | RESEARCH CONTEXT ONLY (qualitative) | NO | V1 (`RESEARCH_UNCALIBRATED`) |
| Team totals | NO | NO | NO | RESEARCH CONTEXT ONLY (qualitative) | RESEARCH CONTEXT ONLY (qualitative) | NO | V1 (`RESEARCH_UNCALIBRATED`) |

Every total and team-total expression carries `authority: RESEARCH_UNCALIBRATED` in the V2 map. It
never has a relation and is never scored.

Why the empirical intervals do not grant SUPPORTED / PARTIAL / CONTRADICTED:

* A central range is a statement about a distribution. Treating an endpoint as a pays / doesn't-pay
  boundary would turn it into a deterministic outcome claim.
* Per-contract coverage would amount to a fair probability.
* No contract-level calibration exists.

Further rules:

* Central-50 and central-80 are displayed identically and influence nothing.
* The moneyline alignment is the **only** relation V2 computes. It states which side a contract pays
  on relative to the CONTROL side, not how likely that is.
* The V2 map has no survival score, no labels and no "best expression".

In this wave the V1 market map stays the active one, unchanged, including its `ARCHETYPE_DEFINITION`
7–24 band classification of spreads. SIFT shows V2 historical ranges beside V1 so the V1 definitional
band is no longer the only margin information on the page. Retiring the V1 band from market mapping
is part of activation.

## 11. Machine-readable schema

### 11.1 The frozen V2 claims artifact

| | |
|---|---|
| File | `data/scripting/live/frozen_v2/<game_key>.json.gz` |
| Envelope | Same shape as V1: `artifact_hash`, `generated_at`, `regeneration`, `content` |
| `content.schema_version` | `cfb_script_claims/2.0.0` |
| `content.methodology_version` | `cfb-script-engine/2.0.0` |

Content keys (exact set, enforced by the freezer):

```
schema_version, methodology_version, activation ("SHADOW"),
source {football_artifact_hash, football_methodology_version, football_schema_version},
calibration {artifact, schema, calibration_version, sha256, source_parameters_sha256,
             development_seasons, validation_seasons},
event_id, game_key, season, teams {home{team_id,name}, away{...}}, kickoff_utc,
football_data_cutoff, market_blind (true),
data_quality {level, describes, all_gates_pass, reasons}, outcome_strength_source,
status ("CLAIMS_PUBLISHED" | "NO_SUPPORTED_CLAIM"), status_statement,
claims {
  control: null | {family, side, strength, tier, evidence[], statement,
                   context {rule, same_side[], opposing[]},
                   historical_range: null | {label, not, variable, tier, n, win_rate {rate,hits,n,ci95},
                                             median, mean, central_50, central_80,
                                             development_seasons,
                                             validation {seasons, n, win_rate, median, coverage_50, coverage_80},
                                             calibration_sha256}},
  closeness: null | {family, evidence[], value, uncertainty, statement, claims_winner (false)},
  pace: null | {family, level, strength, evidence[], statement},
  scoring_environment: null | {family, level, strengthened, evidence[], supports[],
                               incremental_over_baseline, statement, numeric_band (null)},
  defensive_suppression: null | {family, evidence[], statement, numeric_band (null)},
  disruption: [{family, side, strength, evidence[], volatility_context[],
                aligned_with_control, winner_authority (false), statement}],
  explosive_upset: {status ("UNTESTED_HISTORICALLY_DATA_UNAVAILABLE"), methodology ("V1, unchanged"),
                    scripts: [<each V1 EXPLOSIVE_UPSET script object, verbatim>]}
},
abstentions [{family, reason}],
story {headline, clauses [{text, claims[]}], generated_from},
retired_v1 {<V1 archetype>: <V2 replacement or "RETIRED">},
probability (null)
```

Builder: `cfb_edge_finder.scripting.claims.build_claims(football_content, calibration)`. It is pure
and market-blind, and is registered in `FOOTBALL_MODULES`, so the market-blindness tests cover it.

### 11.2 SIFT payload (`event_research.extensions.script_engine`)

* `version` goes from `cfb_script_engine_payload/1.1.0` to `1.2.0`. The change is **additive**: every
  1.1.0 field is unchanged.
* New `claims_v2`:
  * the frozen claims content: identity duplicates dropped, and
    `claims.explosive_upset.scripts` replaced by `script_ids` into `game_scripts`;
  * `claims_artifact_hash`, `generated_at`;
  * `market_authority` (policy table, moneyline alignments, counts).
* Old decoders ignore the key.
* `research_export.fit_script_engine` never trims `claims_v2`. It is small (about 3–5 KB); the
  `OMITTED_OVER_BUDGET` marker remains the only case without it.

## 12. Prospective ledger versioning

| | V1 (unchanged) | V2 (new) |
|---|---|---|
| Rows | `publications.jsonl` | `publications_v2.jsonl` |
| Schema | `cfb_script_ledger/1.0.0` | `cfb_script_ledger/2.0.0` |
| Stored artifacts | `artifacts/<hash>.json.gz` | `artifacts_v2/<claims hash>.json.gz` |

V1 is untouched: same code path, same schema, existing rows never read-modify-written.

Each V2 row carries:

* `methodology_version: "cfb-script-engine/2.0.0"`;
* `activation: "SHADOW"`;
* the claims hash, the football artifact hash it was derived from, and the calibration sha256;
* compact claims and moneyline alignments with entry prices.

V2 rows follow the same rules as V1:

* PUBLICATION and FINAL_PREGAME checkpoints;
* rows are refused at or after kickoff (`LedgerOrderError`);
* a game already kicked off is skipped by the builder, so **no backfill is possible**.

Reports compare V1 rows (before activation) and V2 rows (from now) as separate methodologies, and
never pool them.

## 13. Backward compatibility / migration strategy

* V1 code, artifacts, hashes and ledger are unchanged. V1 artifact hashes do not move, because V2
  lives in a separate artifact.
* V2 is additive in the payload and in the ledger directory.
* Unknown-key-tolerant consumers are unaffected. SIFT's decoder uses optional reads.
* At activation, V1 remains readable. Old ledger rows keep their methodology, and the SIFT decoder
  keeps decoding 1.1.0 payloads.

## 14. SIFT rendering implications

The GAME READ panel:

* renders `claims_v2.story.headline` with the CONTROL chip (side + strength);
* shows the historical outcome range (central 50 / central 80, n, provenance line "2021–2025
  development; validated 2014–2020; calibration <hash8>");
* shows chips for CLOSENESS, PACE, SCORING ENVIRONMENT (SUPPRESSED carries its "baseline already
  expects it" note), DEFENSIVE SUPPRESSION and DISRUPTION EDGE (side + mechanism);
* keeps the structured claims as drill-down;
* gives the no-claim state its V2 statement;
* labels the whole panel "V2 preview (shadow)" while `activation == "SHADOW"`.

Banned wording:

* "% chance";
* "probability";
* "prediction interval";
* "+EV";
* "over" / "under" as claim names.

The historical win rate is either not shown or shown as "historically, N of M such games were won by
the control side".

## 15. Staged deployment

1. **Stage 1 (this wave):** the publisher writes V2 beside V1 and the V2 ledger starts.
2. **Stage 2 (this wave):** SIFT renders the V2 preview when present and is unchanged when absent.
3. **Stage 3 (future, reviewed):** V2 becomes the active publication. The V1 market map's definitional
   bands retire, SIFT drops the preview label, and V1 code is removed after one compatibility week.
4. **Stage 4:** post-deploy live verification (the section 28 checklist).

## 16. Activation gate (Stage 3)

All of the following must hold:

* the shadow comparison is understood, with **zero unexplained winner-direction disagreements**;
* at least one full slate of V2 FINAL_PREGAME rows is ledgered;
* SIFT preview checks pass on phone and desktop;
* a reviewed decision on retiring the V1 definitional band from spread mapping;
* no change to recommendations or staking without its own review.

## 17. Tests (§25 map)

| Area | Tests |
|---|---|
| CONTROL | home MODERATE/STRONG and away MODERATE/STRONG; no PULLS_AWAY in V2; intervals exact vs the research file; artifact hash exact |
| CLOSENESS | EVEN activates; NARROW alone does not; an unresolved EVEN does not; no winner |
| PACE | HIGH and LOW independent of other claims |
| DEFENSIVE_SUPPRESSION | surfaces when V1 GRIND would have excluded it |
| DISRUPTION | surfaces without volatility; no winner authority; no ML authority |
| Retirements | HANGS_AROUND / TOSSUP / PULLS_AWAY / SHOOTOUT / GRIND / PACE_DRIVEN_OVER never appear as V2 claims |
| EXPLOSIVE_UPSET | carried verbatim from V1; V1 builder output unchanged |
| Scoring | no numeric total or team-points band in V2; totals and team totals are `RESEARCH_UNCALIBRATED` in the V2 map; a scoring environment never yields ML or spread authority |
| Confidence | data-quality semantics: changing the confidence level changes no claim |
| Ledger | V1 rows byte-unchanged; V2 rows carry 2.0.0; refused after kickoff; no backfill of kicked-off games |
| Payload | additive 1.2.0; V1 fields identical; `claims_v2` survives trimming; within budget |
| Market-blindness | the claims builder is structurally price-free; byte-identical claims under different prices |
| SIFT (sift repo) | every V2 dimension renders; ranges labelled "historical empirical range"; no probability language |

## 18. Verification against the research (done before wiring the build)

**Claim equivalence.** `derive_claims` (production) reproduces the Wave 2 research definition
(`archetype_research.v2.claims.v2_claims`) on **every** committed historical record:

* 4,546 development records (2021–2025);
* 5,823 validation records (2014–2020).

There are **0 mismatches** across CONTROL tier, CLOSENESS_EVEN, PACE, SCORING_ENVIRONMENT (level and
strengthened), DEFENSIVE_SUPPRESSION and DISRUPTION (side and strength). The production
`closeness_grants_margin` agrees with the research `closeness_resolved` on every record. The check is
the test `test_production_claims_equal_the_wave2_research_definition_on_every_historical_record`, so
the validated numbers describe exactly the claims production states.

**Calibration.** `scripts/promote_control_calibration.py --check` re-derives the production artifact
from the frozen research file (`683d075d…`) and the holdout report. The result is identical, with
sha256 `626c649b91379bc7d8855f121b32c2d148d4e9b129ea674b612a9a0978e89730`.

**V1 untouched.** The same `--as-of` build on main and on this branch produces byte-identical results:

* all 214 V1 frozen artifacts;
* the V1 ledger rows and stored artifacts;
* all 221 SIFT payloads, apart from the added `claims_v2` and the version string.

A test also proves that disabling V2 leaves the V1 ledger stream byte-identical.

## 19. Shadow comparison (current slate)

See `docs/SCRIPT_ENGINE_V2_SHADOW_COMPARISON.md`. Over 214 games:

* **0 winner-direction disagreements;**
* **0 unexpected structural differences;**
* one explained V2-only CONTROL (V1 dropped its CONTROL candidate at evidence 0);
* retirements and newly surfaced claims exactly as this design predicts.
