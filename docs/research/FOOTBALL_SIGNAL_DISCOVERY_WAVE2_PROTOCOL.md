# Football Signal Discovery Lab — Wave 2 (CFB): prospective confirmation protocol

Status: **PRE-REGISTERED**

This protocol and its machine-readable twin were committed **before any Wave-2 population game kicked off**. No
outcome of a game kicking off on or after the activation instant had happened or been joined to anything when
this commit was made. The code that implements it (`signal_discovery.wave2`) refuses to run unless the
candidate file's canonical SHA-256 equals the value pinned below.

| Item | Value |
|---|---|
| Base main | `45a527718b274a3417529bab0b7256db3c3569b6` (Wave 1 merged, #109) |
| Version | `cfb-signal-discovery-wave2/1.0.0` |
| **Candidates** | `data/scripting/validation/signal_discovery_wave2/candidates.json` |
| **Candidates canonical SHA-256** | **`bf19c972bd230ff505c50ccd3a631dbc027d41b4900ad76c97248a4cfc6e1ce5`** |
| Population | games kicking off on/after **2026-10-10T00:00:00Z**, observed pre-entry; **no backfill** |
| Kind | **PROSPECTIVE.** Research signals only. They are **not** bets, recommendations, SIFT badges, staking rules or automatic wagers. Nothing here reaches SIFT, the app export, the router, the recommendation engine, the pricer, CONTROL methodology, game scripts, the betting card, stake sizing or the bankroll. |

Frozen context (not reopened): CFB Moderate CONTROL ATS, n 1,834, 921-869-44, 51.5 % cover, ROI −1.8 %: **no ATS
edge**.

## 1. Streams

| Id | Hypothesis | Wave-1 source | Primary question | Reads (settled n) | Falsified |
|---|---|---|---|---|---|
| **CFB-PROS-001** RUSHING_EDGE_SPREAD (H1-CFB) | "An opponent-adjusted rushing / rush-defense quality edge of at least the Wave-1 frozen qualifying magnitude produces a positive realized margin residual relative to the pre-registered Kalshi spread-market expectation." | CFB-DSC-001 + its follow rule | mean residual > 0 | 50 EARLY_READ, 100 INTERIM, 200 INTERIM, **400 PRIMARY_REVIEW** | mean residual ≤ 0 at n ≥ 400 |
| **CFB-PROS-002** STRONG_CONTROL_MARKET_SKEPTICISM (H2-CFB) | "STRONG CONTROL games in which the pre-registered Kalshi spread-ladder market expectation assigns the CONTROL side an implied margin below +3 points outperform that market expectation prospectively." | CFB-SIG-023 | mean residual > 0 | 50 INTERIM, **100 PRIMARY_REVIEW** | ATS-like rate < 52 % at n ≥ 100 (descriptive threshold) |

Verdicts: `PROSPECTIVE_TRACKING` below the first read, then the read's state; at the primary review a falsified
stream is `REJECTED`, otherwise `REVIEW_REQUIRED`. **`EDGE_CONFIRMED` is never emitted by code.** Economics are
reported beside the residual and never rescue or reject it.

### CFB-PROS-001: exact Wave-1 rule (recovered, not retuned)

* Feature: `defdiff.rush_success_rate = home_def_q.rush_success_rate − away_def_q.rush_success_rate`.
  * This is `evaluate._derive_set2` applied to `features.pregame_features`, the unmodified production football
    builder at the production 04:00 ET data cutoff.
  * Positive means the home defence is better against the run (opponent-adjusted rush success allowed).
* z = (feature − **0.13804024626610417**) / **1.4218539928759784**. These are the 2014–2025 mean and SD in
  `evaluation_report.json`.
* **Qualifies when |z| ≥ 1.0.** Side = home if z > 0, away if z < 0 (expected sign +1).
* Feature eligibility is `features.eligibility(row) is None`.
* `net.rushing` (CFB-SIG-015) is recorded for description only. It never selects a game.

### CFB-PROS-002

* CONTROL comes from the first production V2 `FINAL_PREGAME` ledger row the conductor sees before the window
  closes. Tier and side are copied verbatim, never recomputed.
* Eligible when CONTROL is STRONG, the side is known, the PRIMARY_60_180 ladder is valid, and the CONTROL side's
  `market_implied_margin` is **< +3.0** (strict).
* The +3 is frozen. There is no moneyline substitution, and no ranking, home/away, conference, favourite, injury
  or price filter.

## 2. Checkpoint: PRIMARY_60_180

* **Window:** kickoff −180 to −60 minutes, inclusive.
* **Capture:** the existing research conductor attempts the game's `KXNCAAFSPREAD` event at the same resilient
  slots as the CONTROL winner capture: 165, 120, 90 and 70 minutes. A missed slot is caught up while the window is
  open.
* **Logging:** every attempt is logged as OK, NO_MARKET or REQUEST_FAILED.
* **Checkpoint snapshot:** the **last** in-window capture whose oriented ladder yields a `market_implied_margin`.
  Nothing outside the window is searched.
* **Closing context:** a 30-minute capture is kept for closing context only. It is never an entry.

## 3. Market center: `market_implied_margin`

This is the Wave-1 algorithm (`scripts/signal_discovery_cfb_kalshi_2026_spread.py`), with the arithmetic
unchanged. It is not a "fair spread" or a "true spread".

1. Each rung "<team> wins by over s" with status active/open and 0 < yes_bid ≤ yes_ask < 1 has
   mid = (yes_bid + yes_ask)/2.
2. From perspective T:
   * a rung of T gives the point (s, mid);
   * a rung of T's opponent gives the point (−s, 1 − mid).
3. Sort the points ascending.
4. At the **first** consecutive pair with p1 ≥ 0.5 ≥ p2 and p1 ≠ p2:
   implied = s1 + (p1 − 0.5)/(p1 − p2)·(s2 − s1).
5. If there is no crossing, the value is missing. It stays missing and is never filled.

**Orientation**

* A rung's code is its ticker suffix minus the trailing integer (floor_strike + 0.5).
* The code is mapped to an ESPN team through the frozen production matcher, `control_market.quotes.orient_markets`,
  run on the same game's captured KXNCAAFGAME markets.
* The two codes must be the two different competitors of the game's ESPN event. Anything else is
  `ORIENTATION_FAILURE`.

## 4. Deterministic contract economics (secondary)

* **Rung:** the natural rung is the side-perspective point with the smallest |p − 0.5|.
  * Ties go to the smaller |s|, then to the ticker.
  * The rung is never optimised.
* **Buying it:**
  * the side's own rung is bought as YES at the captured `yes_ask`;
  * the opponent's rung is bought as NO at the captured `no_ask`.
  * **NO is never 1 − YES.**
  * If the ask is not executable, the economics are unavailable (QUOTE_NOT_EXECUTABLE). No other rung is used.
* **Fee:** the repository's July 2026 taker schedule (`kalshi.fee_schedule`), with the KXNCAAFSPREAD multiplier
  (default M = 1, per the schedule's own text). An uncomputable fee is `FEE_UNAVAILABLE`, never 0.
* **Payout:**
  * YES on "<side> wins by over s" pays iff the side's margin is > s.
  * NO on "<opp> wins by over s" pays iff the opponent's margin is ≤ s.
  * Strikes are half-points, so there is no push.
* **CLV:** side-aware, using the same ticker and same side at the closing-context capture. A missing close stays
  missing.

## 5. Ledger (append-only, on the `research-signals` branch)

All files live under `wave2/<season>/`:

* `spread_attempts.jsonl`
* `spread_quotes.jsonl`
* `ledger.jsonl`

`ledger.jsonl` holds three record types, and each row is written once:

* **OBSERVATION:** frozen before the window closes.
* **ENTRY:** written once the window has closed.
* **SETTLEMENT:** written after the final score.

Every row carries:

* sport, signal_id, version, ESPN game id, Kalshi game key;
* ticker, rung, side, ask and fee (where they apply);
* the candidates SHA-256, the code SHA and the feature/artifact hash;
* generated_at, kickoff, checkpoint quote timestamp;
* status and exclusion reason.

Statuses are PENDING, ELIGIBLE, ENTRY_UNAVAILABLE, MARKET_NOT_OFFERED, ORIENTATION_FAILURE, IDENTITY_FAILURE,
FEE_UNAVAILABLE, SETTLEMENT_PENDING, SETTLED, EXCLUDED_PROTOCOL and SYSTEM_FAILURE.

* A population game with no observation by the time its window closes is `SYSTEM_FAILURE`. It is never
  reconstructed later.
* Settlement is automated, deterministic and idempotent.
* The cumulative status report is rewritten on every change at `wave2/reports/<season>/wave2_status.json`.

## 6. Declared prospective conditions (differences from Wave 1)

1. **Market center.** Wave 1 judged CFB-DSC-001 against the CFBD consensus closing spread. Wave 2 judges it against
   the Kalshi ladder at PRIMARY_60_180. This is a **new prospective test condition**, chosen because it is the
   market actually capturable before kickoff.
2. **Football source.** The football source is the production ESPN game log, the same one production V2 CONTROL
   uses. Wave 1 replayed CFBD box scores through the same builder. The mean and SD are not re-estimated, and the
   observed feature distribution is reported so any scale shift is visible.
3. **CONTROL.** PROS-002 reads CONTROL from the production V2 ledger. Wave 1 replayed V2 CONTROL tiers and showed
   them identical to the frozen V2 rows.

## 7. Statistics

* One row per (stream, game).
* Mean, median and SD of the residual, with a 95 % bootstrap percentile CI (4,000 resamples,
  `default_rng(20261010)`).
* Positive-residual rate and ATS-like rate, with Wilson 95 % intervals.
* Contract ROI on outlay, with a bootstrap CI.
* **NO SETTLED SAMPLE** is never shown as 0.

## 8. Not added

No further candidates. Ideas go to `docs/research/FUTURE_HYPOTHESES.md`.
