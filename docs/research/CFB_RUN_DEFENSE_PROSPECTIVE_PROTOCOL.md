# CFB run-defense prospective confirmation — Wave 2D Track B protocol (PRE-REGISTERED)

This file is committed **alone**, before any Track B code and before any eligible game kicks off.

**Kind:** PROSPECTIVE research.
* No stream here is a betting rule, a stake, a SIFT badge or a production change.
* The streams are new and separate from `CFB-PROS-001`, `CFB-PROS-002` and every Wave-2 ledger. No retrospective
  row is ever pooled into a prospective count.

| Item | Value |
|---|---|
| Starting main | `1e40ecbcbbc714a0b2a96aec7a8a79c1e4388352` |
| Track A protocol | `9d70580d` |
| Track A report | `integration_report.json`, SHA-256 `0effb387…2fb` |
| Track A decision | **REJECT**: P1 and P3 fail the favourite-tail bias gate G6; P2 has no signal |

## 1. Activation and population

**Activation.**
* `ACTIVATION_UTC = 2026-10-13T12:00:00Z`.
* Only games kicking off at or after this instant are eligible.
* **First eligible kickoff** in the production schedule: Delaware @ Middle Tennessee, ESPN `401871067`,
  2026-10-13T23:00Z.

**Backfill.**
* None. No game kicking off before activation is ever written.
* No retrospective row is ever added.

**Primary population.**
* Every ESPN-schedule game whose two teams are both FBS in the production game log (`team_division == "fbs"`), and
  whose kickoff is ≥ `ACTIVATION_UTC`.

**FCS research population (`C2C-F4`).**
* Games with exactly one FBS team.
* Recorded separately, never pooled with the primary population, and never part of any primary metric.
* FCS-vs-FCS games are not recorded.

## 2. Frozen pregame observation (one per game)

**Timing.**
* Frozen at the first conductor cycle with 0 < minutes-to-kickoff ≤ **360**.
* Retried every cycle until kickoff.
* A game reaching kickoff unobserved gets one `MISSED` row with its reason.
* An observation is never written at or after kickoff, and never rewritten.

**Each observation freezes:**

* **Run-defense feature `x_rd`.**
  * Definition: (`defdiff.rush_success_rate` − 0.13804024626610417) / 1.4218539928759784.
  * Builder: the exact Wave-1/Wave-2 production builder `wave2_cycle.feature_for` on the production ESPN game log
    (04:00 ET cutoff).
  * This is the CFB-PROS-001 feature, unchanged.
* **Other features (recorded, never thresholded):**
  * `x_rr` and `x_ro`, the Wave-2C constructs;
  * `net.sustained_efficiency`, the broader efficiency state;
  * prior-game counts.
* **P0, the current production projection.**
  * The literal live object: `GameProjectionCache` over the live football state
    (`research-data:data/research/football_state/<season>.json`) and the live talent table, through the production
    loader.
  * Recorded: expected margin, expected total, raw margin, C.2 delta, talent delta, the margin SD of
    `to_game_distribution()`, and the model version (resolved exactly as live).
  * The game is matched to CFBD by its shared numeric id.
* **P1, the frozen research candidate.**
  * δ = **3.592270771756417** × `x_rd` (Track A's final P1 coefficient, P0-LIVE errors 2015–2025, n 7,196).
  * Home margin = P0 + δ; total = P0 total exactly.
  * δ = 0 when the feature is missing.
  * FBS-vs-FBS only.
* **`C2C-F3` shadow (`UNCERTAINTY_SCALED_RUSH_SIGNAL`).**
  * `x_rd_unc` = `x_rd` × n / (n + 3), where n = min(prior games home, prior games away).
  * Recorded only. It never changes P1, `CFB-PROS-001`, `CFB-PROS-003` or production.
* **Reliability metadata.** Prior-game counts, the history-row count and the log freshness.

**If inputs are unavailable**, the field is recorded as missing with a reason. Nothing is imputed, and no market
price is ever inferred when absent.

## 3. Settlement (append-only, once per game, idempotent)

**When.** After kickoff, once the production game log has a final score for both teams.

**What it records:**
* the actual home margin and total;
* `possession_seconds`, `pass_att` and `ints_thrown` per team;
* play-log `drives` and `scoring_opps` where present (otherwise null);
* the Kalshi PRIMARY_60_180 home-perspective centre:
  * computed **read-only** from the Wave-2 spread captures, using the Wave-2 `checkpoint` / `implied_margin`
    functions;
  * null when there is no ladder, orientation or crossing.

**Rules:**
* A frozen observation is never mutated.
* Repeated settlement writes nothing.

## 4. CFB-MODEL-PROS-001 — RUSH_PROJECTION_INCREMENT

**Hypothesis:** "The frozen rush/run-defense projection correction improves prospective FBS-vs-FBS margin prediction
relative to the prior production model."

**Status:** research comparison only (Track A rejected production promotion).

**Comparison:**
* P0 version: the live version at observation, `0.5.0-early-season-talent-prior` (or 0.4.0 if the talent prior is
  off).
* Candidate: P1 as frozen in §2.

**Eligible games:** primary population with P0 and `x_rd` both frozen.

**Metrics:**
* **Primary:** d = |P0 − actual| − |P1 − actual|. Positive means P1 is better. Reported as the mean d, with a
  bootstrap 95 % CI clustered by kickoff date (seed 20261013, 2,000 draws).
* **Secondary:**
  * squared-error difference;
  * winner Brier, P(home margin > 0), Normal with the frozen P0 SD (production continuity correction);
  * margin bias;
  * favourite-oriented bias by |P0| bin (the G6 concern);
  * market residual (actual − Kalshi centre), descriptive only.

**Review sizes:** EARLY_READ n = 50; INTERIM n = 100 and 200; PRIMARY_REVIEW n = 400. Never auto-confirmed.

**Failure rule (at PRIMARY_REVIEW):**
* Mean d ≤ 0 → `REJECTED`.
* Otherwise → `REVIEW_REQUIRED`. A human decides, and any production change needs its own protocol.

## 5. CFB-PROS-003 — RUSH_DEFENSE_RESIDUAL

**Hypothesis:** "Pregame opponent-adjusted run-defense residual, frozen before kickoff, predicts positive
final-margin residual after broader team efficiency is accounted for."

**Baseline:** the frozen P0 margin. It contains no run-defense increment.

**Primary test (continuous; no threshold):**
* OLS without intercept: (actual − P0) = b1 · `x_rd` + b2 · `eff_z`.
* `eff_z` = `net.sustained_efficiency` / 2.069489312273229 (the Wave-2C SD).
* Statistic: b1 (points per SD of run defense, holding efficiency).
* Bootstrap 95 % CI clustered by kickoff date.

**Secondary (market; no bet, no stake, no badge):** the same regression with y = actual − Kalshi PRIMARY_60_180
centre, on games where the centre exists.

**Review sizes:** 50 / 100 / 200 / 400.

**Failure rule (at PRIMARY_REVIEW, n = 400):**
* b1 ≤ 0 → `REJECTED`.
* Otherwise → `REVIEW_REQUIRED`.

## 6. CFB-MECH-PROS-001 — RUSH_DEFENSE_FORCED_PASS_CHANNEL

**Population:** primary population with `x_rd` frozen.

**Outcomes (home − away), from the settlement box score:**
1. possession seconds;
2. **opponent** pass attempts: away pass_att − home pass_att;
3. **opponent** interceptions: away ints_thrown − home ints_thrown;
4. drives and scoring opportunities where present;
5. final margin.

**Questions (slopes on `x_rd`, OLS without intercept):**
* Does a stronger run defense predict:
  1. more possession?
  2. more opponent passes?
  3. more opponent interceptions?
* Mediation ingredients are captured, but no mediation model is refit during the season.

**Formal review at n = 400:**
* All three slopes ≤ 0 → `REJECTED`.
* Otherwise → `REVIEW_REQUIRED`.

These are never betting rules.

## 7. Status artifact

The cumulative report lives at `run_defense/reports/<season>/run_defense_status.json` on the research-signals store.

**For each stream:** status, eligible, observed, settled, the current primary metric, and the next review.

**Status vocabulary:**

| Status | Condition |
|---|---|
| `PROSPECTIVE_TRACKING` | n < 50 |
| `EARLY_READ` | 50 ≤ n < 100 |
| `INTERIM` | 100 ≤ n < 400 |
| `PRIMARY_REVIEW` | n ≥ 400 (the review computation); it resolves to `REVIEW_REQUIRED` or `REJECTED` |

There is no automatic `EDGE_CONFIRMED` state.

## 8. Workflow and isolation

**Where it runs:**
* Inside `scripts/cfb_research_conductor.py`, after the Wave-2 step, in its own try/except.
* A failure is logged to `run_defense/<season>/cycles.jsonl` and never stops CONTROL capture, Wave 2, settlement or
  publication.

**Inputs:**
* The P0 inputs (football state, preseason cache) are read-only copies from `research-data`.
* If they are missing, P0 is recorded as unavailable (fail closed); production projection and capture are
  untouched.

**Writes:** only under `run_defense/` on the research-signals store.

## 9. Not operationalised

`C2C-F5`, `C2C-F6`, `C2C-F7` and `C2C-F8` remain future hypotheses.
