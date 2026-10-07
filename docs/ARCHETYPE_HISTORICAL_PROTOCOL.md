# Historical Archetype Validation — Pre-registered Protocol (Wave 1)

Status: **PRE-REGISTERED.** Committed on branch
`research/historical-archetype-validation` before any historical outcome was
joined to a historical pregame script. Results go in
`docs/ARCHETYPE_HISTORICAL_VALIDATION.md`; any deviation from this protocol is
listed there under *Deviations*, with the reason.

Starting main SHA: `d2befdcde97f3bc667589675644987ce963a1b8b`.
Production methodology under test: `cfb-script-engine/1.3.0`
(`cfb-opponent-adjustment/1.0.0`). **Nothing in production changes in this
wave**: archetypes, finding thresholds, margin bands, band authority,
realized classification, SIFT, market mapping and the prospective ledger
are read-only inputs to this study.

Before this commit the only outcome-side look at the replay was a timing
pilot that counted generated script statuses and PRIMARY archetypes for the
first 400 games of 2023 (pregame only; no result was joined).

## 1. Question

For every completed FBS-involved game of 2021-2025: what would the current
production engine have published that morning, and what happened?

* **A. Taxonomy** — do the realized archetype labels describe real games
  (coverage, overlap, rarity, missing shapes)?
* **B. Pregame identification** — when a script clears its evidence gate, how
  often does the game realize that archetype, versus its base rate?

## 2. Data

* Source: CFBD season cache on the `research-data` branch
  (`data/research_cache/v2/<season>/`): `games`, `games_teams`,
  `advanced_regular_nogarbage`, `drives_regular`, `advanced_postseason`,
  `drives_postseason`. Converted by the production adapter
  `scripting.cfbd.team_games_from_cfbd` (regular + postseason enhanced files
  concatenated). Every file is fingerprinted (SHA-256).
* Never read: `lines_*` (market lines), Elo / win-probability /
  excitement fields of `games`, rankings, ratings, talent, recruiting.
  The loader names the files it reads; a test pins that list.
* Why CFBD and not the production ESPN source: ESPN and CFBD hosts are
  denied by this research environment's network policy, so no historical
  ESPN summaries can be acquired here. CFBD is the same source the
  pre-registered scoring-baseline validation used for 2024-2025.
* Known source gap (documented, not repaired): CFBD publishes no
  explosive-play counts, no early-down split, no designed-rush / dropback
  split, no neutral-situation pass rate and no primary passer. Therefore
  `*_EXPLOSIVE_ADVANTAGE`, `*_SCORING_DEPENDENT_ON_EXPLOSIVES`,
  `*_EARLY_DOWN_ADVANTAGE`, `*_QB_CHANGE_RECENT` cannot fire, the
  `EXPLOSIVE_UPSET` script cannot be generated, and the realized
  `EXPLOSIVE_UPSET` label cannot be assigned. These are reported as
  **DATA_UNAVAILABLE**, never as zero-rate findings.
* 2026: the production ESPN log (`data/football/2026`) is used **pregame
  only** for a source-sensitivity comparison of generation rates (how often
  explosive findings and `EXPLOSIVE_UPSET` are generated under ESPN). No
  2026 outcome is read by this study. The prospective ledger is not read
  beyond counting its rows.

## 3. Walk-forward replay (no leakage)

For every target game *g* with kickoff *k*:

1. `cutoff = gamelog.data_cutoff(k)` (04:00 America/New_York on the local date).
2. `history = gamelog.before(all_rows, cutoff)` — games with
   `kickoff + 4h30 < cutoff`. **Only `history` is passed to the production
   builder**; the target game and every later game are physically absent
   from its input.
3. `football.build_content(FootballPacket(rows=history, ...))` — the
   unmodified production path (adjust → matchup → findings → confidence →
   scripts). Availability is reconstructed as OBSERVED with no QB doubt
   (historical injury lists do not exist; scripts do not depend on it);
   identity PASS; freshness FRESH. The as-published tier
   (availability NOT_OBSERVED) is recorded beside it.
4. The pregame record is frozen as canonical JSON with a SHA-256 hash.
5. Only then are the target's two box-score rows read, and realized
   classification and scoring run on (frozen record, rows). The reveal step
   re-hashes the frozen record and refuses to proceed on a mismatch.

Eligibility: completed, both team rows present with points, both teams have
≥ 1 prior game in `history`, and the sustained-efficiency net and the
scoring baseline are defined. Every other game is counted with an exclusion
reason. Regular season and postseason are both included; season type is a
reported segment.

## 4. Realized taxonomy (Question A)

Labels are **multi-label**. Production rules are reused unchanged:
`realized.realized_features` and `realized.classify`.

* **Reference lead side** (needed by the production classifier for
  pulls-away / hangs-around / upset): the sign of the pregame
  sustained-efficiency `net_home_advantage` (the engine's own notion of which
  side owns the matchup; any magnitude). Sensitivity: the sign of the pregame
  scoring-baseline home margin. Pregame quantities only.
* **Baseline**: the pregame scoring baseline (as production).
* Production labels: `COMPETITIVE_SHOOTOUT`, `COMPETITIVE_GRIND`,
  `COMPETITIVE_TOSSUP`, `HOME_CONTROL`, `AWAY_CONTROL`,
  `FAVORITE_PULLS_AWAY`, `EXPLOSIVE_UPSET`, `TURNOVER_DISRUPTION`,
  `UNDERDOG_HANGS_AROUND`; `AMBIGUOUS` when none applies. The production
  "primary" realized label (`classify(...)["primary"]`) is the exclusive
  class used in confusion matrices.
* Research environment descriptors (production `realized.py` has none for
  these two scripts), multi-label, never part of `AMBIGUOUS`:
  * `PACE_DRIVEN_OVER`: total plays > 2 × the pregame league mean of
    `plays_per_game` **and** total points > pregame baseline total.
  * `DEFENSIVE_SUPPRESSION`: each offense's realized efficiency (success
    rate; yards/play when success rate is missing, same basis as
    `efficiency_winner`) below the pregame league mean of that metric
    **and** total points < pregame baseline total.
* Also reported: production-faithful labels (lead side from the published
  PRIMARY, exactly as `realized.realized_record`) for scripted games.

Reported: base rate of every label; share of games with ≥ 1 production
label, exactly one, several, `AMBIGUOUS`; with and without the two
environment descriptors; pairwise overlap (count, Jaccard, P(B|A)); rates by
season with a χ² homogeneity test; per-game features (margin, total,
efficiency and success-rate winner, explosive profile where available,
disruption profile, pace, scoring environment).

## 5. Pregame identification (Question B)

A script is *generated* when it is published in any role; PRIMARY,
SECONDARY, ALTERNATE and DANGER are also reported separately.

Realization yardsticks (all three reported; **LABEL_MATCH is primary**):

1. **LABEL_MATCH** — the game's realized label set (§4, reference lead)
   contains the archetype with a consistent side: `HOME/AWAY_CONTROL` by
   name; `FAVORITE_PULLS_AWAY` and `EXPLOSIVE_UPSET` and
   `UNDERDOG_HANGS_AROUND` relative to the reference lead (identical to the
   script's own orientation, which is derived from the same efficiency
   net); `TURNOVER_DISRUPTION` requires the realized winner to be the
   script's disruption side; the rest are side-free.
2. **DEFINITION_CHECK** — production `realized.score_scripts` restricted to
   the archetype-definition checks: the `home_margin` band (when stated) and
   the mechanism check (when defined); for a script with neither, the
   qualitative total environment (ELEVATED: total > baseline; SUPPRESSED:
   total < baseline).
3. **PRODUCTION_DESCRIBED** — production `score_scripts(...).described`
   unchanged (includes the UNCALIBRATED_DESCRIPTIVE scoring bands).

Per archetype: count; base rate (LABEL_MATCH predicate over all eligible
games, oriented the way the script would orient it; for
`TURNOVER_DISRUPTION` the home/away mix of generated scripts); precision
(PPV); lift = PPV / base; recall; specificity; Wilson 95% intervals for
proportions; game-level bootstrap (2,000 resamples, seed 20261007) for
lift; rates by evidence-score bucket (≤ 1, 2, 3, ≥ 4), by role, by
reconstructed confidence tier, by season. N < 30 is flagged
`INSUFFICIENT_SAMPLE` and no edge is claimed from it.

## 6. Confusion matrix

For each PRIMARY archetype: distribution of the exclusive production primary
realized label (incl. `AMBIGUOUS`), and the multi-label incidence of every
label.

## 7. NO_SCRIPT analysis

For eligible games with status `NO_SCRIPT_CLEARED_EVIDENCE`: frequency by
season and confidence tier; realized primary-label distribution; Shannon
entropy and max share of that distribution versus scripted games (overall
and within confidence tier); |margin| and total distributions (mean, SD,
quantiles); `AMBIGUOUS` rate; the realization rate (LABEL_MATCH) of the
best *rejected* candidate (gate cleared, evidence ≤ 0) where one exists.

## 8. Evidence-gate ablation (research only)

For each archetype, LABEL_MATCH rate under: (A) base rate; (B) each required
finding alone, and weaker states (sign of the net only, MODERATE vs STRONG);
(B') required finding + each individual second mechanism; (C) the full gate
cleared (candidate exists in `candidates_considered`); (D) published, by
role and evidence bucket. Ablation conditions are computed from the frozen
findings; production builders are not modified or re-parameterized.

## 9. Margin distributions

For each side/margin archetype (PRIMARY and any role): the supported side's
realized margin (control/pulls-away: lead; hangs-around/upset: the
underdog; disruption: disruption side; competitive: |home margin|): win
rate, mean, median, p10/p25/p50/p75/p90, P(margin ≥ 1, 7, 14, 21), and
coverage of the hand-set band. Same for the corresponding realized label.

## 10. Scoring environments

For `COMPETITIVE_SHOOTOUT`, `COMPETITIVE_GRIND`, `PACE_DRIVEN_OVER`,
`DEFENSIVE_SUPPRESSION` (generated in any role, and PRIMARY): within-season
percentile of the actual total; P(top third / top quartile) for elevated,
P(bottom third / bottom quartile) for suppressed; mean actual − season mean;
mean actual − baseline; and a baseline-matched contrast: within strata of
(season × baseline-total quintile), flagged minus unflagged mean percentile,
weighted by flagged count. Coverage of the uncalibrated total band is
reported to reconfirm `UNCALIBRATED_DESCRIPTIVE`. No band is promoted.

## 11. Season roles (from the provenance audit, `docs/ARCHETYPE_HISTORICAL_VALIDATION.md` §D)

| Season | Script-engine exposure before this wave | Role in Wave 1 |
|---|---|---|
| 2021 | none (retired model used it) | RETROSPECTIVE_VALIDATION |
| 2022 | none (retired model used it) | RETROSPECTIVE_VALIDATION |
| 2023 | none (retired model used it) | RETROSPECTIVE_VALIDATION |
| 2024 | scoring-baseline validation stored per-game archetypes + scores | DEVELOPMENT |
| 2025 | adjustment sanity check; scoring-baseline test block | DEVELOPMENT |
| 2026 | engine built on the live 2026 log; ledger from 2026-10-06T23:00Z | DISCOVERY (to 10-06) / PROSPECTIVE (after) |

No season is called an untouched holdout after this wave: Wave 1 examines
2021-2025 once, under this protocol. Seasons 2014-2020 in the same cache are
**not opened** by this wave and are recommended as a sealed historical
holdout for any later methodology change.

Rolling origin: for t = 2022..2025, the pooled 2021..t-1 PPV / base / lift
per archetype versus season t, reporting whether season t's PPV lies in the
history Wilson interval. Production uses season-to-date data only, so
features never carry across seasons; no multi-season prior is tested.

## 12. Exploratory taxonomy check

Clearly labelled exploratory. Standardized realized features (|margin|,
total, success-rate and yards/play differential oriented to the winner,
turnover differential, sacks+TFL differential, total plays, points per
scoring opportunity differential); numpy k-means (k-means++, seed 20261007)
for k = 2..10; k by the largest second difference of inertia; each cluster
described in football terms with its realized-label and `AMBIGUOUS`
composition. Plus a rule-based tabulation of `AMBIGUOUS` games.

## 13. What would change a conclusion

Nothing in this wave changes production. A finding motivates a Wave-2
proposal only if it holds with N ≥ 30 per cell, in the pooled 2021-2023
RETROSPECTIVE_VALIDATION block and with the same sign in 2024-2025.
