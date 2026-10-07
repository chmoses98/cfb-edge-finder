# Historical Archetype Validation — Wave 1 Results

**Verdict: HISTORICAL ARCHETYPE AUDIT COMPLETE (2021-2025, CFBD source) — PRODUCTION UNCHANGED.
Known data gap: no explosive-play data in the historical source, so `EXPLOSIVE_UPSET` cannot be tested.**

* Protocol (pre-registered, committed before any outcome was joined): `docs/ARCHETYPE_HISTORICAL_PROTOCOL.md` @ `621eff005`
* Generated tables (every number below, and more): `docs/ARCHETYPE_HISTORICAL_TABLES.md`
* Machine-readable report: `data/scripting/validation/archetype_historical_report.json`
* Row-level frozen pregame records + outcomes: `data/scripting/validation/archetype_rows/rows_<season>.jsonl.gz`
* Code: `src/cfb_edge_finder/archetype_research/`, `scripts/run_archetype_historical_study.py`,
  `scripts/archetype_historical_tables.py`, `tests/test_archetype_historical_research.py`
* Starting main: `d2befdcde97f3bc667589675644987ce963a1b8b`. Methodology under test: `cfb-script-engine/1.3.0`.
  Research version `cfb-archetype-historical-research/1.0.0`.

Reproduce:

```bash
for y in 2021 2022 2023 2024 2025; do mkdir -p /tmp/cfbd/$y; for f in games games_teams \
  advanced_regular_nogarbage drives_regular advanced_postseason drives_postseason; do \
  git show origin/research-data:data/research_cache/v2/$y/$f.json.gz > /tmp/cfbd/$y/$f.json.gz; done; done
python3 scripts/run_archetype_historical_study.py $(for y in 2021 2022 2023 2024 2025; do echo --cfbd $y=/tmp/cfbd/$y; done) \
  --espn-pregame-only data/football/2026/team_games.jsonl data/football/2026/schedule.json \
  --out data/scripting/validation --tables docs/ARCHETYPE_HISTORICAL_TABLES.md
```

Two independent runs produced byte-identical row files and identical analyses (about 3 minutes on 5 processes).

---

## 1. Method in one paragraph

For each of 4,546 completed FBS-involved games in 2021-2025, the study computed the production cutoff (04:00 ET on the
game's local date). It selected only the games that had finished before that cutoff (kickoff + 4h30 < cutoff) and passed
**only those rows** to the unmodified production builder `scripting.football.build_content`. That builder runs the
opponent adjustment, matchup vector, findings, confidence and scripts. The study froze the pregame record with a
SHA-256 hash. Only then did it reveal the game's own box score. Realized classification reuses production
`realized.realized_features` / `classify` / `score_scripts`. No market line, price, Elo or win-probability field is read.

## 2. Data coverage

| Season | Scheduled FBS-involved | Completed | Box both teams | Success-rate data | Drive data | Explosive counts | Eligible | Excluded |
|---|---|---|---|---|---|---|---|---|
| 2021 | 887 | 887 | 887 | 887 | 887 | **0** | 739 | 148 |
| 2022 | 896 | 896 | 895 | 896 | 896 | **0** | 747 | 149 |
| 2023 | 910 | 910 | 910 | 910 | 910 | **0** | 762 | 148 |
| 2024 | 920 | 919 | 919 | 915 | 918 | **0** | 784 | 136 |
| 2025 | 934 | 934 | 934 | 934 | 934 | **0** | 793 | 141 |
| **Total** | 4,547 | 4,546 | | | | 0 | **3,825** | 722 |

Every exclusion is a game where a team had **no prior game** before the cutoff (the production profile cannot exist).

| Reason | Count |
|---|---|
| NO_PRIOR_HISTORY_BOTH | 423 |
| NO_PRIOR_HISTORY_AWAY | 283 |
| NO_PRIOR_HISTORY_HOME | 15 |
| NOT_COMPLETED | 1 |

The away-only exclusions are mostly FCS opponents. Their only logged games are against FBS teams, which is the same
data constraint production has. As a result only 19-32 FBS-vs-FCS games per season are eligible.

There were no identity failures, no missing scores and no orientation mismatches. All 4,546 targets passed the leakage
check (latest history kickoff + 4h30 < cutoff). Eligible games include 214 postseason games. File SHA-256
fingerprints are in the tables doc and the JSON.

**Source.** CFBD cache on `research-data` (`data/research_cache/v2/<season>/`), the same source the scoring-baseline
validation used. ESPN and CFBD hosts are **denied by this environment's network policy**, so no historical ESPN data
could be acquired. CFBD lacks explosive-play counts, early-down splits, the designed-rush/dropback split, neutral pass
rate and primary passer. Consequences:

* `*_EXPLOSIVE_ADVANTAGE`, `*_SCORING_DEPENDENT_ON_EXPLOSIVES`, `*_EARLY_DOWN_ADVANTAGE` and `*_QB_CHANGE_RECENT`
  never fire.
* `EXPLOSIVE_UPSET` can be neither generated nor realized.
* Box scores are the final, corrected versions rather than the versions available that morning.

**Source sensitivity (2026 ESPN log, pregame only, no outcome read):**

* In 245 eligible 2026 games, 113 had at least one explosive finding, but only **2** produced an `EXPLOSIVE_UPSET`
  candidate (1 PRIMARY).
* So the historical gap removes about 1% of games from the upset script. It removes more from finding-level
  dependence and contradiction weights.
* Status and PRIMARY mixes were otherwise similar to CFBD 2025 weeks 1-6.

## 3. Provenance / contamination

| Season | What had already seen its outcomes | Role in Wave 1 |
|---|---|---|
| 2021 | Script engine: nothing. The retired projection model used it heavily (preseason-prior dev set, model-repair diagnostics, V2 walk-forward). | RETROSPECTIVE_VALIDATION |
| 2022 | Same as 2021 (also Milestone C/C2 development). | RETROSPECTIVE_VALIDATION |
| 2023 | Same as 2022. | RETROSPECTIVE_VALIDATION |
| 2024 | Script-engine scoring-baseline validation (commit 335f3d8): per-game PRIMARY archetype + actual scores stored; training block for Splits A/B. | DEVELOPMENT |
| 2025 | Opponent-adjustment sanity check during design (SCRIPT_ENGINE §4); Split A test block. | DEVELOPMENT |
| 2026 to 10-06 | The engine was built on the live 2026 log; real-slate reads drove changes; Split B test block. | DISCOVERY |
| 2026 from 10-06T23:00Z | Script ledger (methodology frozen at 1.3.0). | PROSPECTIVE |

The whole scripting package landed in one commit on 2026-10-06 (PR #96). Three margin and archetype rule changes came
after the 2024-2026 per-game outcomes had been computed:

* 14cdbb9 / v1.2.0
* a7cf50d / v1.3.0
* 23d5ff2

Their stated reasons are structural and no commit cites hit rates. Even so, 2024-2025 cannot be called untouched.
**No untouched historical season remains after this wave for 2021-2025.** Seasons 2014-2020 in the same cache were
**not opened** and are the recommended sealed holdout. The 2026+ ledger remains the only genuinely prospective test.
It held 763 rows (1 FINAL_PREGAME, 1 settled) when audited.

Every conclusion below is checked in two blocks:

* **RV** = 2021-2023, 2,248 games
* **DEV** = 2024-2025, 1,577 games

## 4. Question A — does the realized taxonomy describe real games?

Reference lead side = the sign of the pregame sustained-efficiency net (pre-registered). The efficiency favourite won
69.3% of games. The winner also won the efficiency battle in 75.9%.

| Coverage (3,825 games) | Production labels | + PACE / SUPPRESSION descriptors |
|---|---|---|
| ≥ 1 label | 91.9% | 95.8% |
| exactly 1 | 33.8% | 25.0% |
| multiple | 58.2% | 70.9% |
| AMBIGUOUS | **8.1%** (RV 8.2%, DEV 7.9%) | 4.2% |

| Realized label | Base rate | Season range | Season χ² p |
|---|---|---|---|
| UNDERDOG_HANGS_AROUND | 38.1% | 36.4-40.7% | 0.47 |
| FAVORITE_PULLS_AWAY | 33.7% | 31.1-35.7% | 0.37 |
| PACE_DRIVEN_OVER (research) | 27.6% | 26.2-28.6% | 0.81 |
| HOME_CONTROL | 21.6% | 21.1-22.7% | 0.92 |
| DEFENSIVE_SUPPRESSION (research) | 17.9% | 16.6-21.7% | 0.053 |
| COMPETITIVE_TOSSUP | 17.8% | 16.8-18.6% | 0.85 |
| AWAY_CONTROL | 16.1% | 15.1-16.7% | 0.93 |
| TURNOVER_DISRUPTION | 15.3% (primary label in only 2.6%) | 14.2-16.1% | 0.85 |
| COMPETITIVE_GRIND | 10.3% | 8.8-**14.3%** (2022) | **0.002** |
| COMPETITIVE_SHOOTOUT | 10.0% | 9.5-11.1% | 0.80 |
| EXPLOSIVE_UPSET | 0% — DATA_UNAVAILABLE | — | — |

Realized base rates are very stable across seasons; only GRIND (2022) varies.

**Structural redundancy (by construction of `realized.classify`):**

* **UNDERDOG_HANGS_AROUND (realized) is just "a one-score game with a reference lead".**
  * Every TOSSUP, GRIND and SHOOTOUT game also carries it: P(HANGS | TOSSUP) = P(HANGS | GRIND) = 1.00,
    P(HANGS | SHOOTOUT) = 0.997.
  * It adds no information beyond |margin| ≤ 8.
* **CONTROL (7-24) and FAVORITE_PULLS_AWAY (17+) overlap on 17-24-point wins.** 34% of HOME_CONTROL games also carry
  PULLS_AWAY.
* **COMPETITIVE_TOSSUP is the residual of the one-score games.** It covers those that are neither 2+ points above nor
  2+ points below baseline on both sides. It is a margin bucket with no mechanism.
* The exclusive "primary" realized labels split games as:

  | Primary label | Share |
  |---|---|
  | FAVORITE_PULLS_AWAY | 20.9% |
  | COMPETITIVE_TOSSUP | 17.8% |
  | HOME_CONTROL | 17.7% |
  | AWAY_CONTROL | 12.6% |
  | GRIND | 10.4% |
  | SHOOTOUT | 10.0% |
  | AMBIGUOUS | 8.1% |
  | TURNOVER_DISRUPTION | 2.6% |

**What is uncovered (AMBIGUOUS, 309 games):**

| Uncovered family | Games | Share of AMBIGUOUS |
|---|---|---|
| Winner by 9-24 who **lost** the efficiency battle | 209 | 68% |
| Underdog (by reference lead) won by 25+ **and** won efficiency | 81 | 26% |
| Winner by 25+ who lost efficiency | 19 | 6% |

The first family is the largest missing shape: a win built on turnovers, special teams or field position. Overall, 24%
of all winners lost the efficiency battle. Most of those wins are absorbed by the one-score labels or by
`TURNOVER_DISRUPTION`.

**Exploratory clustering (k-means on 8 standardized realized features; k = 3 by second difference).** It found three
coarse families, none dominated by AMBIGUOUS (6-10% each):

| Cluster | Share | Centroid | Covered by |
|---|---|---|---|
| High-scoring, competitive | 30% | total 68, |m| 10 | shootout / tossup / control |
| Low-scoring, competitive | 41% | total 43, |m| 10 | tossup / grind / control |
| Decisive | 29% | |m| 32, big success / YPP / havoc edges | 67% PULLS_AWAY |

**No large, football-interpretable family is missed.** The missing shape is the narrower "won without winning
efficiency" family above. (EXPLORATORY.)

## 5. Question B — can the pregame engine identify them?

Status mix over 3,825 eligible games:

| Status | Games |
|---|---|
| SCRIPTS_GENERATED | 1,906 |
| SINGLE_SCRIPT | 1,392 |
| NO_SCRIPT_CLEARED_EVIDENCE | 527 (13.8%) |

**LABEL_MATCH** (primary yardstick): the realized label set contains the archetype on the same side. **Lift** is PPV
over the base rate of the same oriented event. Intervals are Wilson (PPV) and game bootstrap (lift).

| Archetype | Generated | Base | PPV (95% CI) | Lift (95% CI) | RV lift | DEV lift | Recall any / PRIMARY | Stability |
|---|---|---|---|---|---|---|---|---|
| HOME_CONTROL | 1,019 | 21.6% | 30.9% (28-34) | **1.43** (1.33-1.54) | 1.39 | 1.49 | 38% / 30% | STABLE |
| AWAY_CONTROL | 762 | 16.1% | 31.5% (28-35) | **1.96** (1.78-2.14) | 1.85 | 2.12 | 39% / 29% | STABLE |
| FAVORITE_PULLS_AWAY | 936 | 33.7% | 55.8% (53-59) | **1.66** (1.58-1.74) | 1.62 | 1.71 | 41% / 11% | STABLE |
| UNDERDOG_HANGS_AROUND | 836 | 38.1% | 37.2% (34-41) | **0.98** (0.90-1.05) | 0.95 | 1.01 | 21% / 3% | no signal |
| COMPETITIVE_SHOOTOUT | 434 | 10.0% | 9.7% (7-13) | **0.97** (0.73-1.24) | 0.86 | 1.14 | 11% / 10% | no signal |
| COMPETITIVE_GRIND | 687 | 10.3% | 11.8% (10-14) | 1.14 (0.93-1.36) | 1.17 | 1.06 | 20% / 16% | weak / unstable |
| COMPETITIVE_TOSSUP | 391 | 17.8% | 17.1% (14-21) | **0.96** (0.77-1.15) | 1.01 | 0.91 | 10% / 9% | no signal |
| PACE_DRIVEN_OVER | 421 | 27.6% | 42.8% (38-48) | **1.55** (1.39-1.72) | 1.60 | 1.49 | 17% / 6% | STABLE |
| DEFENSIVE_SUPPRESSION | 19 | 17.9% | 21.1% (9-43) | 1.18 (0.27-2.38) | 0.77 | 2.35 | 1% / 0% | INSUFFICIENT_SAMPLE |
| TURNOVER_DISRUPTION | 253 | 7.7%¹ | 14.2% (10-19) | **1.84** (1.32-2.37) | 1.68 | 2.14 | 6% / 2% | STABLE (weak) |
| EXPLOSIVE_UPSET | 0 | — | — | — | — | — | — | DATA_UNAVAILABLE |

¹ per game-side, weighted to the home/away mix of generated scripts.

* **HOME_CONTROL and AWAY_CONTROL do differ.** AWAY_CONTROL has the larger lift because its base rate is lower. Its
  supported side wins less often (74.5% vs 86.4% as PRIMARY).
* **Roles.** DANGER is overwhelmingly UNDERDOG_HANGS_AROUND (658 of 836 HANGS scripts are DANGER), at 37.7% vs 38.1%
  base. FAVORITE_PULLS_AWAY is SECONDARY in 657 of 936 scripts.
* **Evidence score does not rank realization.**

  | Archetype | Realization by evidence bucket |
  |---|---|
  | HOME_CONTROL | ≤1 27%, 2 34%, 3 37%, ≥4 30% |
  | PACE_DRIVEN_OVER | ≤1 47%, 2 38%, 3 32%, ≥4 0% |
  | COMPETITIVE_GRIND | 12%, 16%, 11%, 6% |
  | FAVORITE_PULLS_AWAY | ≥4 57% vs below 4 33-36% (n 35) — the only clear positive gradient |

* **Confidence tier is not monotone.** HOME_CONTROL: HIGH 31.6%, MEDIUM 42.3%, LOW 20.2%. FAVORITE_PULLS_AWAY: LOW
  70.4% (n 206), HIGH 53.2%.
* **Production "described"** (which includes the UNCALIBRATED team-points bands) is far lower:

  | Script position | LABEL_MATCH | Production "described" |
  |---|---|---|
  | PRIMARY | 25.7% | 17.8% |
  | Any script | 45.7% | 28.4% |

  HOME_CONTROL described is 9.1% vs 30.9% label match: the team-points bands, not the margin, fail most often.

**Rolling origin.** For history ≤ t-1 → season t (2022, 2023, 2024, 2025), the next season's PPV fell inside the
history Wilson interval in 4/4 splits for:

* HOME_CONTROL
* AWAY_CONTROL
* PACE_DRIVEN_OVER

It did so in 3/4 for FAVORITE_PULLS_AWAY (2025 ran high: 62%) and TURNOVER_DISRUPTION. Lift stayed on the same side of
1 in every split for CONTROL, PULLS_AWAY, PACE and TD. The SHOOTOUT, GRIND and TOSSUP lifts flip sides.

## 6. Confusion — PRIMARY archetype → what the game became

Columns are the exclusive realized primary label (multi-label incidence is in the tables doc).

| PRIMARY (n) | Realized as itself | Most common other outcomes | AMBIGUOUS | Supported side |
|---|---|---|---|---|
| HOME_CONTROL (799) | 26.7% | **PULLS_AWAY 37.2%**, TOSSUP 17.3%, SHOOTOUT 5.1%, GRIND 4.4% | 5.3% | won 25+ 37.7%, 17-24 17.0%, 7-16 18.9%, 1-6 12.8%, lost 13.6% |
| AWAY_CONTROL (592) | 24.2% | PULLS_AWAY 22.8%, TOSSUP 17.2%, SHOOTOUT 10.3%, GRIND 10.3%, HOME_CONTROL 5.4% | 6.9% | won 25+ 23.3%, lost 25.5% |
| FAVORITE_PULLS_AWAY (275) | 35.6% | **HOME/AWAY_CONTROL 28.4%**, TOSSUP 17.1% | 4.4% | won 25+ 36.0%, 7-24 36.7%, lost 14.2% |
| UNDERDOG_HANGS_AROUND (124) | (n/a: HANGS is never a primary label) | HOME_CONTROL 18.6%, PULLS_AWAY 17.7%, AWAY_CONTROL 16.9%, TOSSUP 13.7% | 9.7% | underdog lost by 9+ **48.4%** |
| COMPETITIVE_SHOOTOUT (407) | 9.6% | TOSSUP 19.7%, HOME_CONTROL 18.9%, **GRIND 16.0%**, PULLS_AWAY 11.1% | 11.6% | — |
| COMPETITIVE_GRIND (533) | 12.2% | HOME_CONTROL 18.9%, TOSSUP 17.3%, AWAY_CONTROL 14.1%, SHOOTOUT 11.3% | 10.9% | — |
| COMPETITIVE_TOSSUP (364) | 17.3% | GRIND 16.2%, HOME_CONTROL 15.9%, SHOOTOUT 14.6%, AWAY_CONTROL 13.2% | 10.4% | — |
| PACE_DRIVEN_OVER (126) | (env label 46.8%) | HOME_CONTROL 19.8%, SHOOTOUT 16.7%, TOSSUP 15.9% | 9.5% | — |
| TURNOVER_DISRUPTION (78) | 3.9% | GRIND 17.9%, SHOOTOUT 15.4%, HOME_CONTROL 15.4%, TOSSUP 14.1% | 7.7% | lost 38.5% |

**How it fails:**

* **CONTROL → PULLS_AWAY.** The favourite usually wins, but by *more* than the 7-24 definition.
* **PULLS_AWAY → CONTROL.** The two are interchangeable.
* **HANGS_AROUND → favourite wins comfortably.**
* **The competitive scripts → anything.** Their realized distribution is close to the unconditional mix. A predicted
  SHOOTOUT becomes a GRIND about as often as a SHOOTOUT.

## 7. NO_SCRIPT (abstention)

* **Rate:** 13.8% (527 / 3,825). By season: 15.7%, 11.8%, 13.5%, 13.5%, 14.4%.
* **By tier:** HIGH 7.8%, MEDIUM 20.1%, LOW 29.2%.

| | NO_SCRIPT (527) | Scripted (3,298) |
|---|---|---|
| Realized-primary entropy | 2.854 bits | 2.828 bits |
| Largest realized-primary share | 20.9% (TOSSUP) | 21.8% (PULLS_AWAY) |
| AMBIGUOUS | 7.8% | 8.1% |
| One-score games | 42.9% | 37.4% |
| Efficiency favourite won | 64.6% | 70.1% |
| \|margin\| median (p10-p90), SD | 11 (3-31), 11.6 | 14 (3-35), 13.3 |
| Total median (SD) | 53 (16.6) | 53 (17.0) |

* **Abstention is not more heterogeneous than scripting.** The entropy is the same within every tier:

  | Tier | NO_SCRIPT | Scripted |
  |---|---|---|
  | HIGH | 2.83 | 2.86 |
  | MEDIUM | 2.84 | 2.80 |
  | LOW | 2.81 | 2.60 |

* No-script games are slightly closer and less favourite-dominated, but they do not collapse into one realized shape.
* Only 55 abstentions had a rejected candidate (gate cleared, evidence ≤ 0). Those candidates realized at 9-18%, near
  base.
* **Reading:** the gates are not visibly too strict, because no obvious shape is being withheld. But abstention also
  does not isolate unusually unpredictable games. The scripted games are, by realized-shape concentration, *just as
  heterogeneous* as the abstentions. The gate is a coverage decision, not an uncertainty filter.

## 8. Evidence-gate ablation (LABEL_MATCH rate; lift vs base)

* **HOME_CONTROL:**

  | Condition | Rate | Lift |
  |---|---|---|
  | Base | 21.6% | — |
  | Efficiency net > 0 (any size) | 28.5% | 1.32 |
  | Required finding | 30.8% | 1.43 |
  | MODERATE | 34.9% | 1.62 |
  | STRONG | 28.2% | 1.31 |
  | Published | 30.9% | 1.43 |

  The gate adds a little over the sign of the net. STRONG realizes *less* than MODERATE because strong edges overshoot
  the 24-point cap.
* **AWAY_CONTROL:** base 16.1%, net > 0 25.9%, required 31.7% (MODERATE 29.7%, STRONG 33.9%), published 31.5%.
* **FAVORITE_PULLS_AWAY:**

  | Condition | Rate |
  |---|---|
  | Base | 33.7% |
  | Any lead edge | 45.4% |
  | **STRONG lead edge alone** | **55.6%** |
  | + finishing | 58.5% |
  | + disruption | 57.5% |
  | + rush | 62.8% |
  | + pass | 56.9% |
  | Full gate | 55.8% |
  | STRONG + scoring-advantage only (excluded from qualifying) | 54.8% |

  **The second-mechanism requirement adds nothing measurable** (55.8% vs 55.6%). The STRONG efficiency edge carries the
  archetype.
* **UNDERDOG_HANGS_AROUND:**

  | Condition | Rate |
  |---|---|
  | Base | 38.1% |
  | Any lead edge | 31.8% |
  | MODERATE | 37.2% |
  | STRONG | 27.3% |
  | + underdog disruption | 37.8% |
  | + lead turnover-prone | 34.0% |
  | + both defenses control | 35.5% |
  | + resolved narrow gap | 37.2% |
  | + low possessions (pace, non-qualifying) | 28.5% |
  | Full gate | 36.3% |
  | Published | 37.2% |

  **No counter raises the one-score rate above base.** Structurally, `NARROW_EFFICIENCY_GAP` is resolved exactly when a
  MODERATE efficiency finding exists (n 808 = 808): every moderate edge auto-qualifies HANGS_AROUND.
* **TURNOVER_DISRUPTION** (per game-side):

  | Condition | Rate |
  |---|---|
  | Base | 7.7% |
  | Disruption advantage | 13.1% (lift 1.71) |
  | + opponent turnover-prone | 11.5% |
  | + defense turnover-reliant | 3.0% (n 33) |
  | + high-variance matchup | 14.9% |
  | Full gate | 14.0% |
  | Volatility finding without a disruption edge | 7.6% (= base) |

  **The disruption edge is the signal; the volatility requirement is not.**
* **COMPETITIVE_SHOOTOUT:**

  | Condition | Rate |
  |---|---|
  | Base | 10.0% |
  | HIGH_SCORING present | 8.9% |
  | BOTH_OFFENSES_EFFICIENT | 12.0% |
  | Gate | 9.7% |
  | With resolved closeness | 9.9% |
  | Without | 9.4% |

  Components:

  | Component | Published | All games |
  |---|---|---|
  | One-score | 44.2% | 38.1% |
  | Total above baseline | 45.9% | 49.0% |

* **COMPETITIVE_GRIND:**

  | Condition | Rate |
  |---|---|
  | Base | 10.3% |
  | LOW_SCORING | 8.1% |
  | **BOTH_DEFENSES_CONTROL** | **14.3%** |
  | LOW_POSSESSION | 8.1% |
  | Gate | 11.5% |
  | Published | 11.8% |

* **COMPETITIVE_TOSSUP:**

  | Condition | Rate |
  |---|---|
  | Base | 17.8% |
  | EVEN_MATCHUP | 16.4% |
  | Resolved | 15.7% |
  | Unresolved | 18.7% |
  | Published | 17.1% |

  The **one-score** component *does* rise: 48.6% when published vs 38.1% base (RV 47.5%, DEV 50.0%). The closeness
  evidence works for closeness. The realized TOSSUP label's extra "mixed scoring" condition is what erases it.
* **PACE_DRIVEN_OVER:**

  | Condition | Rate |
  |---|---|
  | Base | 27.6% |
  | HIGH_POSSESSION MODERATE | 42.7% |
  | STRONG | 38.3% |
  | Published | 42.8% |
  | PRIMARY | 46.8% |
  | + HIGH_SCORING support | 35.0% (support *lowers* it) |

* **DEFENSIVE_SUPPRESSION:**

  | Condition | Rate | Lift |
  |---|---|---|
  | Base | 17.9% | — |
  | **BOTH_DEFENSES_CONTROL (= gate)** | **34.8%** (n 224) | **1.95** |
  | + LOW_SCORING support | 36.1% | — |
  | **Published** | 21.1% (**n 19**) | — |

  **The gate is informative but almost never published.** The exclusivity rule with COMPETITIVE_GRIND (which ranks
  first and absorbs the same finding) suppresses 205 of 224 clears.

Useful conditions:

* the sustained-efficiency edge (direction and strength)
* the STRONG threshold
* HIGH_POSSESSION_ENVIRONMENT
* BOTH_DEFENSES_CONTROL
* the disruption advantage
* resolved closeness, **for closeness only**

Conditions that added nothing measurable:

* the PULLS_AWAY second edge
* the HANGS_AROUND counters
* the TD volatility requirement
* HIGH/LOW_SCORING_ENVIRONMENT, as a label driver
* evidence-score ranking

## 9. Margin distributions (supported side; PRIMARY unless noted)

| Archetype (definition band) | n | Win | Median | p10 / p25 / p75 / p90 | ≥1 / ≥7 / ≥14 / ≥21 | Band coverage |
|---|---|---|---|---|---|---|
| HOME_CONTROL (7-24) | 799 | 86.4% | 19 | −4 / 6 / 31 / 44 | 86 / 74 / 60 / 48% | **35.9%** |
| AWAY_CONTROL (7-24) | 592 | 74.5% | 10 | −10 / −1 / 24 / 35 | 75 / 60 / 42 / 32% | **36.7%** |
| FAVORITE_PULLS_AWAY (17-45), any role | 936 | 87.6% | 20 | −3 / 7 / 34 / 45 | 88 / 75 / 62 / 49% | **48.0%** |
| FAVORITE_PULLS_AWAY (17-45), PRIMARY | 275 | 85.8% | 17 | −3 / 5.5 / 31 / 41.6 | 86 / 73 / 58 / 41% | 43.3% |
| UNDERDOG_HANGS_AROUND (underdog −7…+8) | 124 | 36.3% | −7 | −29 / −21 / 6 / 14 | — | **33.9%** |
| UNDERDOG_HANGS_AROUND, any role | 836 | 26.8% | −9 | −32.5 / −22 / 2 / 10 | — | 35.8% |
| TURNOVER_DISRUPTION (1-21) | 78 | 61.5% | 3 | −14 / −4.8 / 14.8 / 25 | 62 / 39 / 30 / 13% | 48.7% |
| TURNOVER_DISRUPTION, any role | 253 | 72.3% | 10 | −12 / −3 / 24 / 38 | 72 / 57 / 43 / 30% | 44.3% |
| COMPETITIVE_SHOOTOUT (\|m\| ≤ 8) | 407 | — | \|m\| 10 | — | — | 45.2% |
| COMPETITIVE_GRIND (\|m\| ≤ 8) | 533 | — | \|m\| 12 | — | — | 40.7% |
| COMPETITIVE_TOSSUP (\|m\| ≤ 8) | 364 | — | \|m\| 9 | — | — | 48.1% |
| (all games: efficiency favourite) | 3,823 | 69.3% | 7 | −13 / −3 / 22 / 34 | 69 / 55 / 40 / 29% | — |

**Post-hoc exploratory split (deviation D1)** by the required edge's strength:

| Split | n | Win | Median | Band (7-24) coverage |
|---|---|---|---|---|
| HOME_CONTROL, MODERATE | 392 | 80.1% | 12 | 42% |
| HOME_CONTROL, STRONG | 627 | 90.1% | 23 | 31% |
| AWAY_CONTROL, MODERATE | 384 | 67.2% | 7 | 36% |
| AWAY_CONTROL, STRONG | 378 | 81.0% | 14.5 | 39% |

* **Every one of the 936 FAVORITE_PULLS_AWAY scripts was published alongside a CONTROL script for the same side.**
  Margins are almost identical:

  | Scripts | Median | ≥21 |
  |---|---|---|
  | HOME_CONTROL with PULLS_AWAY | 23.5 | 56% |
  | PULLS_AWAY overall | 20 | 49% |

* **Conclusion for the hand-set bands:**
  * The 7-24 CONTROL band captures about a third of outcomes and **cuts off the upper tail** (p75 = 31, p90 = 44 for
    home control).
  * The 17-45 PULLS band starts above the median.
  * The HANGS band [−7, +8] covers about a third, with a median underdog margin of −7 to −9.
* The **direction** is reliable (86-90% for home control or strong edges). The **ranges** are definitions that the
  empirical distributions do not resemble. They should become empirical quantile bands in a later, reviewed wave.

## 10. Scoring environments (qualitative signal only; bands stay UNCALIBRATED_DESCRIPTIVE)

| Script (any role) | n | Mean total percentile | Extreme third | Extreme quartile | Baseline-matched pct diff | Uncalibrated band coverage |
|---|---|---|---|---|---|---|
| COMPETITIVE_SHOOTOUT (high) | 434 | 0.614 | 48.6% (vs 33%) | 38.7% (vs 25%) | **+0.036** | 30.6% |
| PACE_DRIVEN_OVER (high) | 421 | 0.567 | 40.9% | 31.6% | **+0.031** | 36.1% |
| COMPETITIVE_GRIND (low) | 687 | 0.377 | 49.8% | 41.3% | **−0.063** | 40.9% |
| DEFENSIVE_SUPPRESSION (low) | 19 | 0.318 | 57.9% | 47.4% | −0.057 | 31.6% |

* The qualitative environment **does discriminate high- vs low-scoring games** in both blocks:

  | Script | Mean total percentile, RV | DEV |
  |---|---|---|
  | SHOOTOUT | 0.63 | 0.58 |
  | GRIND | 0.36 | 0.40 |

* Almost all of that information is already in the descriptive baseline. For a shootout, the baseline sits 8.3 points
  above the season mean and the actual total 7.2 above, so actual minus baseline is −1.1.
* Matched on season × baseline-total quintile, the environment adds only +3 to −6 percentile points. GRIND adds the
  most (about −6, in both blocks).
* corr(baseline total, actual total) = 0.29.
* The uncalibrated total bands contain the actual total only 31-41% of the time.
* **Numerical scoring bands remain UNCALIBRATED_DESCRIPTIVE. Nothing is promoted.**

## 11. Season stability

* **Base rates.** Realized base rates are stable: χ² p > 0.3 for every label except GRIND (p = 0.002; 2022 = 14.3%)
  and DEFENSIVE_SUPPRESSION (p = 0.053).
* **Stable direction.** PPV is homogeneous across seasons for every archetype with signal (p 0.21-0.95). Lift is above
  1 in **every** season for:

  | Archetype | Lift range across seasons |
  |---|---|
  | HOME_CONTROL | 1.30-1.52 |
  | AWAY_CONTROL | 1.78-2.17 |
  | FAVORITE_PULLS_AWAY | 1.54-1.74 |
  | PACE_DRIVEN_OVER | 1.38-1.61 |
  | TURNOVER_DISRUPTION | 1.57-2.71, n 31-72 |

* **Unstable direction (lift crosses 1):**
  * UNDERDOG_HANGS_AROUND (0.86-1.06)
  * COMPETITIVE_SHOOTOUT (0.77-1.39)
  * COMPETITIVE_GRIND (0.92-1.31)
  * COMPETITIVE_TOSSUP (0.72-1.10)
  * DEFENSIVE_SUPPRESSION (n 1-7 per season)
* RV and DEV reach the same conclusion for every archetype: lift CI above 1 in both blocks for CONTROL, PULLS_AWAY,
  PACE and TD, and CI containing 1 in both blocks for HANGS, SHOOTOUT, GRIND and TOSSUP. Point estimates for the
  no-signal archetypes sit on either side of 1 (e.g. SHOOTOUT RV 0.86, DEV 1.14). DEFENSIVE_SUPPRESSION is too small to
  say.

## 12. Deviations from the protocol

* **D1.** Added a post-hoc, exploratory margin split by required-edge strength and by PULLS_AWAY co-publication
  (§9). It is labelled exploratory everywhere and motivates nothing by itself.
* **D2.** Ablation condition names `B_<finding>_alone` were renamed `B_<finding>_present` (wording only; same
  computation).
* **D3.** Added a per-season count of records passing the leakage check to the coverage table.

Definitions, thresholds, yardsticks and blocks are as pre-registered. The first full run and the final run are
identical apart from D1-D3.

## 13. Limitations

* **Historical source.** The data are CFBD, not production's ESPN, and there are no explosive-play data. As a result:
  * EXPLOSIVE_UPSET is untestable.
  * Explosive contradictions and supports never fire.
  * The QB-change finding never fires, so HIGH tier is slightly over-assigned.
  * CFBD's success-rate definition differs from ESPN's.
* **Box-score timing.** Box scores are post-correction finals.
* **Availability.** Historical injury lists do not exist; availability was reconstructed as observed-clean. Scripts do
  not depend on it.
* **Contamination.** 2024-2025 are DEVELOPMENT (§3). After this wave, 2021-2023 have been examined once.
* **Taxonomy circularity.** Realized labels for pulls-away / hangs-around / upset need a reference favourite. The
  pre-registered choice (the engine's own efficiency sign) makes those labels engine-relative. The sensitivity check
  (baseline-margin sign) gives the same AMBIGUOUS rate (8.1%), and the two leads agree in 77% of games.
* **Shootout/grind labels.** The realized SHOOTOUT/GRIND labels require a one-score margin. The scripts claim one only
  with closeness evidence, so LABEL_MATCH is strict for them. DEFINITION_CHECK (45-47%) and the components in §8 show
  the parts separately.
* **FBS-vs-FCS.** Most FBS-vs-FCS games are excluded for lack of FCS history, which is also true in production.
* **Pre-registered.** Nothing here is a betting result: no price was read and no profitability is implied.
