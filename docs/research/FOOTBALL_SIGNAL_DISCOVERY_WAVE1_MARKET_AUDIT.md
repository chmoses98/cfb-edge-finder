# CFB MARKET DATA AUDIT (2026-10-08)

Every figure below was read from git objects (`git show`) or computed by the scripts in `scratchpad/cfb_market_audit/scripts/`. No outcome was joined to any line or price, and no ATS or ROI figure was computed. Note: the local clone was shallow, so I ran `git fetch --deepen` on `main` to expose its 87 catalog commits. No tracked files were changed.

## 1. Historical sportsbook lines (CFBD `/lines`, research-data `data/research_cache/v2/<yr>/lines_{regular,postseason}.json.gz`)

**Provenance.** `manifest.json` records one call per season and season type: `/lines?year=Y&seasonType=…` with no classification filter. The cache was fetched on 2026-09-02T05:17Z, with every call returning `ok`. There is no 2026 file.

**Shape.** Each record is a game (`id, season, week, seasonType, startDate, home/away team, id, conference, classification, score`) with a nested `lines[]`. Every one of the 36,102 line objects has exactly these keys: `provider, spread, spreadOpen, overUnder, overUnderOpen, homeMoneyline, awayMoneyline, formattedSpread`. **None has a timestamp.** The `cfbd_client.fetch_lines` docstring calls these "closing" lines, but the data cannot confirm it. Treat them as end-of-life CFBD values with unknown capture time. `spreadOpen` and `overUnderOpen` exist only from 2021.

**Sign convention: verified home-perspective.** A negative `spread` means the home team is favored. In all 35,954 non-pick'em lines whose `formattedSpread` parses, the text agrees with the sign. Spot checks:

| Game | Home team | `spread` | `formattedSpread` |
|---|---|---|---|
| 2022 CFP final | Georgia (home) vs TCU | −13.5 | "Georgia −13.5" |
| 2024 CFP final | Notre Dame (home) vs Ohio State | +8.5 | "Ohio State −8.5" |
| 2025 CFP final | Indiana (home) | −7.5 | — |

**Coverage per season (regular + postseason, game-level counts).**

| Season | Games (FCS-v-FCS) | Any line | spread | spreadOpen | O/U | O/U open | ML (both sides) | `consensus` spread | Providers (line rows) |
|---|---|---|---|---|---|---|---|---|---|
| 2014 | 868 (0) | 865 | 865 | 0 | 760 | 0 | 0 | 810 | consensus 810, teamrankings 752, numberfire 741 |
| 2015 | 870 (0) | 832 | 832 | 0 | 765 | 0 | 0 | 795 | consensus, numberfire, teamrankings |
| 2016 | 873 (0) | 866 | 866 | 0 | 760 | 0 | 0 | 842 | consensus, teamrankings, numberfire |
| 2017 | 874 (0) | 874 | 873 | 0 | 785 | 0 | 0 | 859 | consensus, teamrankings, numberfire |
| 2018 | 884 (0) | 869 | 869 | 0 | 851 | 0 | 0 | 800 | + Caesars 708 |
| 2019 | 888 (0) | 881 | 881 | 0 | 881 | 0 | 0 | 880 | + Caesars 762, Bovada 318 |
| 2020 | 568 (0) | 567 | 567 | 0 | 567 | 0 | 0 | 567 | consensus, numberfire, teamrankings, Bovada 515, Caesars 439, SugarHouse 94, William Hill (NJ) 80 |
| 2021 | 887 (0) | 887 | 887 | 878 | 887 | 879 | 738 | 870 | Bovada 879, consensus 870, William Hill (NJ) 862, teamrankings 769, numberfire 145 |
| 2022 | 1,463 (567) | 1,459 | 1,459 | 852 | 1,459 | 855 | 718 | 1,244 | William Hill (NJ) 1,408, consensus 1,244, Bovada 859, teamrankings 766 |
| 2023 | 1,416 (506) | 1,413 | 1,413 | 873 | 1,411 | 874 | 794 | 29 | Bovada 854, William Hill (NJ) 835, DraftKings 757, ESPN Bet 469, Caesars (CO) 76 |
| 2024 | 1,573 (653) | 1,557 | 1,557 | 864 | 1,553 | 864 | 831 | 0 | ESPN Bet 1,551, DraftKings 817, Bovada 804 |
| 2025 | 1,597 (663) | 1,597 | 1,597 | 938 | 1,594 | 938 | 867 | 0 | ESPN Bet 1,542, Bovada 934, DraftKings 805, "Draft Kings" 64 |

Scope notes:

* FBS-involved games carry a spread in 97–100% of cases in every season.
* From 2022 the CFBD response also includes FCS-vs-FCS games, which the counts in parentheses show.
* In 2014–2020, 10–110 FBS games per season have a spread from only one provider.

**Inconsistencies and duplicates.**

* **Duplicates.** There are no duplicate game ids and no exact duplicate providers within a game.
* **DraftKings alias.** "DraftKings" and "Draft Kings" are separate provider strings and both appear for the same game: 10 games in 2025 regular and 46 in 2025 postseason. Normalize the alias before taking a median.
* **Non-book providers.** `teamrankings` and `numberfire` are aggregator or projection sites, not books. `consensus` is CFBD's own aggregate. Before 2018 these are the only providers.
* **Missing spread.** 61 rows have `spread = null` while `formattedSpread` reads "Team −NaN".
* **Outliers.** Some games show very wide disagreement between providers: the maximum within-game spread range is 45 points (2014), and 1–29 games per season have a range of 7 points or more. A median is robust to these.
* **ML sign disagreements.** 206 lines have a moneyline favorite that contradicts the spread sign:
  * 137 have |spread| < 3, where a genuine split between spread and price is possible.
  * 69 have |spread| ≥ 3. 47 of these are Bovada postseason lines, consistent with home and away moneylines being swapped at neutral sites.
* **2023 postseason file** includes 24 FCS-playoff games coded as weeks 13–15.

**Choosing one consensus line (recommendation).**

1. Normalize provider aliases.
2. Take the **median** across available providers for each of `spread` and `overUnder`.
3. Optionally, prefer `consensus` where it exists (2014–2022). In 2023 it covers only 29 games, and in 2024–2025 it is absent.

This median rule is already what research-data's `data/research/v2/preds/market_close.parquet` uses. I verified that in all 6,266 rows `pred_margin = −median(spread)` and `pred_total = median(overUnder)`. That file covers seasons 2017–2019 and 2021–2025 only. The script that built it is not in any branch. Where both exist, it differs from `consensus` in 865 of 3,800 games.

## 2. Kalshi 2026 observations

### 2a. Research corpus (research-data `data/research/observations/2026/*.jsonl`, 20 files)

* **Volume.** 44,019 rows from 297 runs. Every row has `capture_mode = PROSPECTIVE` and schema `research_corpus_v2`.
* **Capture window.** `captured_at` runs from 2026-08-26T06:58Z to 2026-09-16T23:30Z. Kickoffs covered run from Aug 29 to Sep 24 (weeks 1–3, plus 4 rows in week 4).
* **Why it stops.** Capture fail-closed after Sep 16. `capture_state` logs `missed_window` rows through Sep 24, and `operational_state` has read `DEADLINE_AT_RISK (CFBD_QUOTA_EXHAUSTED + FOOTBALL_STATE_STALE_HARD)` since Sep 25.

**Families captured.** Only `KXNCAAFGAME`, `KXNCAAFSPREAD` and `KXNCAAFTOTAL` were captured.

| Family | Rows | Markets | Events / games | Rows with both asks in [0.01, 0.99] |
|---|---|---|---|---|
| moneyline | 1,956 | 306 | 153 | 1,836 |
| spread | 23,630 | 4,011 | 152 | 23,217 |
| total | 17,252 | 2,888 | 152 | 16,748 |
| unresolved (`parse_unresolved`, not priced) | 1,181 | 407 | 18 games | — |

* **Total.** 7,344 markets across 156 games.
* **Spread and total are strike ladders.** A median game has 20 spread strikes and 19 total strikes. Each spread contract reads "team wins by > X.5" (`semantic_operator ">"`, `team` = home/away). Each total contract reads "over X.5".

**Side-specific executable quotes.** Every row carries both `executable_yes_price` and `executable_no_price`. In every row YES + NO ≥ 1.00 (the observed sums run from 1.00 to 1.29+), which shows they are the two asks rather than complements. The rows also carry `market_midpoint` and `market_status`. They do **not** carry bids, sizes or volume.

**Cadence.** This is checkpoint sampling, not a continuous quote history: at most 9 rows per market (median 8). Checkpoints are defined in `research/timing.py`:

| Checkpoint | Window | Observed rows |
|---|---|---|
| EARLY_OPEN | first sighting | 7,344 |
| T_7D | 6–8 days before kickoff | 3,701 |
| T_3D | 60–84 h | 6,804 |
| T_24H | 18–30 h | 4,743 |
| T_6H | 4–8 h | 4,387 |
| T_90 | 60–120 min (observed 116–119) | 4,235 |
| T_60 | 45–75 min (observed 73–74) | 4,235 |
| T_30 | 15–45 min | 4,287 |
| CLOSING | see below | 4,283 |

**CLOSING definition (`research/closing.py`).** CLOSING is the last clean pregame quote, graded EXACT when taken within 10 minutes of kickoff and NEAR_CLOSE when taken within 60. The observed captures were 8.9–14 minutes before kickoff.

**Closing coverage** (from `capture_state`; markets captured vs `missed_window`):

| Family | Captured | Missed | Games captured | Games missed |
|---|---|---|---|---|
| KXNCAAFGAME | 184 | 128 | 92 | 64 |
| SPREAD | 2,351 | 1,736 | 90 | 66 |
| TOTAL | 1,748 | 1,197 | 92 | 63 |

Captures within 10 minutes of kickoff: moneyline 51 games, spread 49, total 51.

**PRIMARY_60_180.** The CONTROL protocols define it as the **last valid executable YES ask with `kickoff−180 ≤ captured_at ≤ kickoff−60`**: no price cap, NO is never taken as 1−YES, and missing stays missing. In this corpus the T_90 and T_60 checkpoints fall inside it. Games with a quote inside the window: moneyline 91, spread 89, total 91.

**Settlement (`data/research/settlements/2026`, 11,509 rows, `settlement_v1`).**

* Final status per ticker:

  | Family | Settled / total |
  |---|---|
  | moneyline | 192 / 306 |
  | spread | 2,536 / 4,011 |
  | total | 1,824 / 2,888 |

* The rest are `pending_not_final`, because settlement stopped when capture stopped.
* `official_kalshi_settlement` is **null in every row**. Settlement is always `derived_contract_settlement`, taken from scores: ESPN fallback on 9,906 rows and CFBD on 1,603. No mismatch was flagged.

**Fees.** `kalshi/fee_schedule.py` implements `KALSHI_FEE_SCHEDULE_2026_07_07_TAKER`: `ceil_to_cent(M·0.07·C·P·(1−P))` in Decimal, with M = 1 for GAME, SPREAD and TOTAL. There is also a maker schedule at 0.0175, which is unused.

* Every observation has `estimated_taker_fee` = the per-contract cent ceiling **at the YES ask**. It matched in all 42,082 `VERIFIED_CURRENT` rows.
* 1,937 rows are `unverified`: the 1,181 unresolved rows plus 756 rows whose ask was 0.00 or 1.00.

**Orientation.**

* Research rows map each market to home/away through `kalshi_game_mapping_v1`. 2.7% of rows are unresolved (1,181 rows: 657 SPREAD, 456 TOTAL, 68 GAME).
* The CONTROL study's frozen matcher (team codes + names) oriented 83 of 84 game-winner events. The one failure was Charlotte (Kalshi `CHAR` vs ESPN `CLT`).

### 2b. Live catalog (main `data/live/`): full contract inventory with quote history

* **What it is.** Each catalog commit overwrites `cfb_market_catalog.json` (the slate index) and `games/<key>.json` (every market on Kalshi's public REST v2). Market fields include `yes_ask`/`no_ask`/`yes_bid`/`no_bid` with sizes, `last_price`, `volume`, `open_interest`, `status`, `floor_strike`, `yes_sub_title` and `is_alternate_line`. Each market also carries a per-market fee block, computed as `ceil_to_$0.000001(0.07·C·P·(1−P))` at each side's own ask.
* **History in git.** There are 87 snapshots on main, from 2026-09-18T00:21Z to 2026-10-08T14:06Z. The median gap is 4.6 h (range 0.3–17.7 h), and all 87 are `capture_complete`.
* **Latest snapshot.** 221 games, 12,963 markets, 1,380 events, 295 season-level events.
* **No settlement field.** Finalized markets carry no result field, only `status = finalized` and a `last_price` near 0 or 1.
* **Nothing before 2026.** No Kalshi price history from any earlier season exists in any branch.

**Derived quote sets.**

* `data/scripting/validation/control_market_2026/quotes_2026.jsonl.gz` holds game-winner quotes only. It combines 31,408 catalog rows (85 snapshots, Sep 18 – Oct 7) with 2,024 research rows: 918 markets in 459 events.
* `research-signals` (150 commits since 2026-10-08T06:14Z, written every ~5 minutes) holds one live YES ask per game in `signals/cfb_research_signals.json` for 215 games.
* The conductor's own `capture/2026/{attempts,quotes}.jsonl` **do not exist yet**. All 11 games in its window are `CAPTURE_PENDING`, and the first eligible kickoff is 2026-10-08T23:00Z.

### 2c. Other 2026 inputs

* **ESPN odds sidecar** (`research-sidecar`, `espn_odds/2026.jsonl`). 5,103 timestamped fetches covering 254 games, fetched Sep 2–17 (57 distinct fetch times) for kickoffs Sep 3–25.
  * Fields: DraftKings spread, O/U and moneyline.
  * Coverage: 1,146 fetches returned no book; 25 games have a fetch within 1 h of kickoff.
  * This is the only time-stamped sportsbook source in the repo.
* **`data/football/2026`** (main, `cfb_football_gamelog_manifest/1.0.0`, fetched 2026-10-08T14:00Z).
  * Content: ESPN scoreboard plus summary box score, drives and injuries. 938 events in the schedule; 393 completed games involving an FBS team.
  * `team_games.jsonl` has 786 rows (2 per game) with the fields `season, week, game_id, kickoff_utc, team, team_id, team_division, opponent, opponent_id, opponent_division, site, points_for, points_against, box{…}, pbp{…}, pbp_source, primary_passer, primary_passer_att, notes, observed_at, source, schema_version`.
  * Rows by week: 1: 198, 2: 172, 3: 150, 4: 142, 5: 118, 6: 6.
  * It has **no period scores**, so 1H, 2H and quarter contracts cannot be settled from repo data. The manifest states "nothing … carries a price, a line, a total or an odds block."
* **`data/scripting`** on main:
  * `live/{sift, frozen (V1), frozen_v2}` holds per-game artifacts (222 / 218 / 215).
  * `validation/` holds `records_2024/2025/2026` (919 / 934 / 390 rows of football validation records) plus the frozen CONTROL market study (protocol, manifest, quotes, rows, report) and the prospective protocol.

## 3. Script ledger and research signals

**`script-ledger`, `data/scripting/ledger/2026`:**

* **V1 `publications.jsonl`.**
  * 1,529 rows covering 218 games, recorded Oct 6 23:00 – Oct 8 14:00: 1,527 `PUBLICATION` and 2 `FINAL_PREGAME`.
  * Status counts: SCRIPTS_GENERATED 696, NO_SCRIPT_CLEARED_EVIDENCE 481, SINGLE_SCRIPT 352.
  * Expressions are priced from the catalog `prices_captured_at`, with cost = ask + catalog fee.
* **V2 `publications_v2.jsonl`.**
  * 643 rows covering 215 games, all `PUBLICATION`, all `activation = SHADOW`, with **0 `FINAL_PREGAME`**. Kickoffs run Oct 8 23:00 – Oct 22.
  * CONTROL tier on the latest row per game:

    | Tier | Games |
    |---|---|
    | HOME_STRONG | 19 |
    | AWAY_STRONG | 12 |
    | AWAY_MODERATE | 10 |
    | HOME_MODERATE | 8 |
    | none | 166 |

    Row-level counts are 57 / 36 / 30 / 24 / 496. No game's tier changed between rows.
  * 147 rows carry game-winner moneyline entries.
* **`realized.jsonl`.** 12 rows (10 PUBLICATION, 2 FINAL_PREGAME) for 3 games. They are settled from the ESPN game log by `script_engine.py`, using box-score features and the expression `won` flag.
* **Reports.** The V1 report shows 3 settled games. The V2 report shows FINAL_PREGAME = 0 and settled = 0.

**`research-signals`.** This branch holds the signals contract and `prospective_report.json`. H1 (MODERATE ROI) and H2 (STRONG below 85¢) both have n = 0; `moderate_status` reads `VALUE_WATCH` and population `final_pregame_games` = 0. Settlement rows (`prospective/2026/settlements.jsonl`) do not exist yet.

## 4. Wager branches

`kalshi-router/CFB` and `accounting-data` both hold `cfb_accounted_wager.v1` records of **real Kalshi wagers**. All are `IMPORTED_RECEIPT`, with `fees_are_estimated = false`.

| | Count |
|---|---|
| Wagers | 141 |
| Distinct markets / events | 138 / 130 |
| Execution window | 2026-09-11 → 2026-10-07 |
| Side YES / NO | 95 / 46 |
| Import batch `kalshi-router-v1` | 123 |
| Import batch `gap-backfill` | 18 |

Wagers by series:

| Series | Wagers |
|---|---|
| SPREAD | 63 |
| TOTAL | 27 |
| TEAMTOTAL | 14 |
| GAME | 9 |
| 1QSPREAD | 6 |
| 1HSPREAD | 6 |
| 1QTOTAL | 4 |
| 1Q | 4 |
| KXMVECROSSCATEGORY (combo) | 4 |
| 1HTOTAL | 3 |
| 1H | 1 |

Settlement records:

| Branch | Settlements | Amendments | Note |
|---|---|---|---|
| accounting-data | 141 (all SETTLED) | 93 | — |
| kalshi-router/CFB | 137 | 93 | 4 settlements behind |

The amendments are `router-settlement-economics.v2`, which fixes fees that the earlier net figure had subtracted twice.

**Fees actually paid.** In 137 of 141 wagers, `fees_paid` equals the **unrounded** `0.07·C·P·(1−P)` to within $0.0001. That is lower than the research code's per-contract cent ceiling and closer to the catalog's $0.000001 model.

## 5. Summary by market family

"Exec" means both YES and NO asks fall in [0.01, 0.99] while the market is active. The catalog family names in brackets are the ones the scripts report.

**Research corpus columns:** kickoff span, markets / games, PRIMARY_60_180 games, CLOSING games.

**Catalog columns:** markets / games, PRIMARY_60_180 games, games with a quote under 60 minutes, games with a quote within 10 minutes (EXACT).

| Family | Research corpus | Catalog | Exec quotes | Cadence | Settlement in repo | Fees | Orientation |
|---|---|---|---|---|---|---|---|
| ML (`KXNCAAFGAME`) | Aug 29–Sep 24 (wk 1–3); 306 / 153; 91; 92 (51 EXACT) | 1,202 / 601 game keys; 191; 87; 3 | research 1,836 of 1,956 rows; catalog 39,290 of 43,786 | 9 checkpoints; catalog ~4.6 h; signals 5 min (YES only, from Oct 8) | research derived 192 / 306; official 0; catalog none | verified 2026-07-07 taker (cent ceiling); catalog per-market model | research 68 rows unresolved; study 83 / 84 oriented |
| Spread (`KXNCAAFSPREAD`) | same span; 4,011 / 152 (ladder ~20 / game); 89; 90 | 9,963 / 454; 210; 95; 3 | research 23,217; catalog 165,691 rows | as ML | derived 2,536 / 4,011; official 0 | yes (M = 1) | 657 rows unresolved |
| Alt spread | same contracts as spread | `is_alternate_line = true` on every strike; no main-line flag | — | — | — | — | — |
| Total (`KXNCAAFTOTAL`) | same span; 2,888 / 152 (~19 / game); 91; 92 | 7,480 / 455; 210; 95; 3 | 16,748 / 120,106 rows | as ML | derived 1,824 / 2,888 | yes | 456 rows unresolved |
| Alt total | same contracts as total | — | — | — | — | — | — |
| Team total (`KXNCAAFTEAMTOTAL`) | none | 5,948 / 245; 118; 43; 3 | 89,001 rows | catalog only | none; derivable from ESPN `points_for`, not done | catalog model only | `yes_sub_title` team name |
| 1H (`1H`, `1HSPREAD`, `1HTOTAL`, `1HTEAMTOTAL`) [first_half_moneyline / spread / total / team_total] | none | ML 1,725 / 575, spread 5,267 / 247, total 3,474 / 247, team total 881 / 52; PRIMARY 203 / 118 / 118 / 22 | most rows | catalog only | none; no period scores | catalog only | as above |
| 1Q (`1Q`–`4Q` ML / SPREAD / TOTAL) [quarter_moneyline / spread / total] | none | all four quarters together: ML 1,644, spread 6,060, total 6,028; 137 games; PRIMARY 59 | most rows | catalog only | none | catalog only | as above |
| 2H [second_half_moneyline / spread / total] | none | 414 / 2,955 / 1,945; 138 games | most rows | catalog only | none | catalog only | as above |
| Winning margin | — | **not offered**: no series in 87 snapshots; folded into the spread ladder | — | — | — | — | — |
| Other | none | overtime 157 / 147; first-TD team 294 / 98; team stat props 841 / 69; game stat props 64 / 64; 1H/FT combos and delay ("unknown") 218 / 36; multivariate combos not enumerable | — | — | — | — | — |

**Bottom line.**

* **Sportsbook history.** Spread and total cover 2014–2025, but every line is a single undated value per provider. Openers exist only from 2021, and moneylines only from 2021.
* **Kalshi history.** It exists only for 2026.
  * Research checkpoints cover game ML, spread and total from Aug 29 to Sep 24, with side-specific asks, derived settlement and a fee model.
  * The catalog covers every family from Sep 17 to Oct 22, at about 4.6-hour cadence, with almost no captures within 10 minutes of kickoff and no settlement.
* **Official settlement.** Kalshi settlement values are absent everywhere.
