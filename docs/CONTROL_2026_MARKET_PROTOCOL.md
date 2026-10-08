# CONTROL × 2026 Kalshi game-winner market: pricing study protocol

Status: **PRE-REGISTERED.** This was committed before any of the following:
* any 2026 CONTROL tier was reconstructed;
* any quote was joined to a CONTROL side;
* any settlement or result was read for a CONTROL game.

| Item | Value |
|---|---|
| Base main | `be483103e9c6f895047f0a410d64906ba59c00ae` |
| Machine-readable protocol | `data/scripting/validation/control_market_2026/protocol.json` |
| **Protocol canonical SHA-256** | **`61aa278d86582532556d0174abc2e1c5f883ebaa882285fe603d1ee2af23e1db`** (sorted keys, compact separators) |
| Football methodology | `cfb-script-engine/2.0.0`, CONTROL claim, unchanged |
| CONTROL calibration (context only) | `control_margin_v2.json`, sha256 `626c649b…9730` |
| Kind of study | **MARKET-PRICING research.** It changes no production rule, threshold, interval, SIFT output, recommendation or stake. |

The study runner (`scripts/run_control_market_study.py`) checks two things before it reveals anything:
* this protocol file is marked PRE-REGISTERED and still hashes to the value above;
* the stage-2 freeze manifest exists and still hashes to the value recorded at freeze.

If either check fails, it refuses to reveal.

## 1. Question

> When the CFB engine identified a MODERATE or STRONG CONTROL edge before kickoff in 2026, did buying that side in
> the Kalshi game-winner market at the pre-registered pregame executable price produce positive fee-adjusted returns?

The study keeps two questions apart:
* **predictive signal:** did the CONTROL side win as often as history says?
* **market edge:** was the price charged low enough for that win rate to pay, after fees?

A high win rate alone answers only the first. Historical CONTROL rates are shown as context. They are **not fair
probabilities**, and no "historical rate − price" figure is ever called an edge.

## 2. Football signal (unchanged)

CONTROL is the production V2 claim. It reads `{SIDE}_SUSTAINED_EFFICIENCY_ADVANTAGE`:
* MODERATE when |net| ≥ 1.0;
* STRONG when |net| ≥ 2.0;
* subject to the production uncertainty gate |net| ≥ uncertainty.

The four mutually exclusive tiers are HOME/AWAY × MODERATE/STRONG. Nothing upstream is altered: opponent adjustment,
efficiency inputs, thresholds, gate, home/away distinction and calibration intervals are all as in production.

Historical context (frozen; not refitted; not a fair price):

| Tier | 2021–25 n / win / median | 2014–20 n / win / median |
|---|---|---|
| HOME MODERATE | 404 / 79.95% / +12 | 512 / 79.5% / +13 |
| HOME STRONG | 628 / 90.13% / +23 | 824 / 91.0% / +24 |
| AWAY MODERATE | 404 / 66.58% / +7 | 514 / 67.9% / +7 |
| AWAY STRONG | 378 / 80.95% / +14.5 | 552 / 84.6% / +17 |

## 3. Populations (never merged without also being shown separately)

| Label | Definition |
|---|---|
| `RETROSPECTIVE_FOOTBALL_REPLAY_WITH_PROSPECTIVELY_CAPTURED_MARKET_PRICE` | Every completed 2026 game in the frozen production game log with kickoff before `2026-10-08T01:39:58Z` (the first V2 ledger write). Both teams and both scores must be present, oriented by the frozen ESPN schedule. CONTROL is reconstructed by the Wave 1/2 replay: production `build_content` on `gamelog.before(rows, data_cutoff(kickoff))` only, then the production `derive_claims` CONTROL rule. **This is not prospective football evidence.** Only the market price was captured prospectively. |
| `PROSPECTIVE_V2_CONTROL` | V2 ledger rows of kind `FINAL_PREGAME` (`origin/script-ledger`, commit `5e0e1d60`) for games completed by the freeze. The ledger's CONTROL tier is read as-is and never recomputed. |

Audit at protocol time:
* The V2 ledger holds 214 `PUBLICATION` rows, **0 `FINAL_PREGAME` rows**, and the earliest V2 kickoff is
  2026-10-08T23:00Z.
* So the prospective V2 population is expected to be **empty** in this run. It is reported as such, and nothing
  stands in for it.
* V1 frozen artifacts exist for only 3 completed games (Oct 6–7), and none of them carries a sustained-efficiency
  finding.

Frozen football inputs (read from git at `be483103`, hash-checked):
* `data/football/2026/team_games.jsonl`: `6e6cf409…b242`;
* `data/football/2026/schedule.json`: `daee74ee…0b2`.

Known replay limitations, stated in advance:
* availability is `OBSERVED_CLEAN`, as in Waves 1/2. It affects data-quality confidence only, never CONTROL;
* box scores were fetched after the fact (`observed_at` ≥ 2026-10-06), so any later ESPN stat corrections are
  included.

## 4. Market data (genuinely captured before kickoff; nothing re-priced)

| Source | Where | Timestamp | YES / NO price |
|---|---|---|---|
| `RESEARCH_OBSERVATION` | `research-data@292f3aac`, `data/research/observations/2026/*.jsonl`, every row whose `kalshi_market_ticker` starts `KXNCAAFGAME-` (any family label) | `observation.captured_at` | `executable_yes_price` / `executable_no_price` |
| `CATALOG_SNAPSHOT` | every `main` commit touching `data/live` up to `be483103`; `data/live/games/<key>.json`, game-winner markets | `data/live/cfb_catalog_status.json` `captured_at` at that commit | `yes_ask` / `no_ask` |

Known coverage at protocol time. These are capture counts only; no CONTROL join and no outcome had been read:
* research capture covers moneyline kickoffs from about Sep 3 to Sep 13 (T_90 / T_60 checkpoints);
* the catalog history covers kickoffs from Sep 17 to Oct 7, with roughly one snapshot per game in the 60–180 min
  window;
* kickoffs Aug 29 – Sep 2 and Sep 13 – 17 have no game-winner captures. Those games are
  `ENTRY_QUOTE_UNAVAILABLE`, and **missing means missing**.

**Orientation.**
* Market `<event>-<CODE>` pays YES iff team CODE wins.
* It is resolved to an ESPN competitor in two ways:
  1. **By team codes:** the event key splits into the two ESPN abbreviations of a schedule event within 36 h.
  2. **By name:** the production name keys are used. The market's team is `yes_sub_title` (catalog) or the
     `game_id` slug side named by `observation.team` (research).
* Disagreement between the two methods, or two markets resolving to one team, is `ORIENTATION_CONFLICT`. If neither
  method resolves, the market is `ORIENTATION_UNRESOLVED`.
* Both conditions are outcome-independent exclusions.

**Contract bought (primary):** the CONTROL team's own market, **YES**, at its captured executable YES ask.
* Descriptive secondary: the opponent's market, **NO**, at its own captured NO ask. This is never `1 − YES`.
  Executable YES and NO quotes routinely sum to more than 1.

**Valid quote:**
* the side's ask is in [0.01, 0.99] and is a whole cent;
* the market status, when recorded, is active/open;
* the quote was captured strictly before ESPN kickoff;
* the catalog book is not a sentinel full-width book.

Missing is never imputed.

## 5. Entry checkpoint (one primary rule)

**PRIMARY_60_180:** the **last** valid executable quote of the primary contract with
`kickoff − 180 min ≤ captured_at ≤ kickoff − 60 min`.
* If none exists, the game is **`ENTRY_QUOTE_UNAVAILABLE`** and is excluded from primary economics. No quote outside
  the window is ever searched for.
* Ties on `captured_at` go to `RESEARCH_OBSERVATION`.

Descriptive checkpoints only (they never enter the verdict):

| Checkpoint | Rule |
|---|---|
| EARLY_OPEN | first valid quote ever captured |
| W_180_360 | last valid quote in [k−360, k−180) |
| W_15_60 | last valid quote in (k−60, k−15] |
| CLOSING | last valid quote in (k−60, k) |

## 6. Fees, settlement, economics

* **Fee.** `kalshi.fee_schedule.calculate_fee_cents(price_cents, 1, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER, M=1)`.
  * This is the verified July 2026 taker schedule: ceiling to the cent of 0.07 × P × (1 − P), at the captured
    entry price of the side bought.
  * It cannot be computed for a non-integer-cent price or a price outside [1, 99]. That case is `FEE_UNAVAILABLE`
    and the economics are unavailable. **The fee is never 0 by default.**
* **Settlement.** The contract is worth $1 if the CONTROL team won, else $0.
  * The source is the final score in the frozen production game log.
  * It is cross-checked against `official_kalshi_settlement` / `derived_contract_settlement` for the same ticker in
    research-data, where present. Any disagreement is `SETTLEMENT_MISMATCH` and the game is excluded.
  * A missing score or a tie is `SETTLEMENT_UNAVAILABLE`.
* **Per contract:**
  * outlay = entry + fee;
  * gross P/L = payout − entry;
  * fee-adjusted P/L = payout − entry − fee.
* **ROI (primary)** = Σ fee-adjusted P/L ÷ Σ outlay. A secondary ROI over entry price only (the repo analytics
  convention) is also shown.
* **Break-even win probability** = mean outlay. Realized − break-even is reported.
* **Unit.** One game, one CONTROL side, one KXNCAAFGAME contract, one research contract. There is **no staking,
  bankroll, Kelly, multi-unit sizing, parlay or selection among games.**

## 7. Metrics (per tier; then ALL MODERATE, ALL STRONG, ALL CONTROL; every pool beside its tiers)

**Counts:**
* eligible CONTROL games;
* games with a valid entry quote;
* games missing a quote, with reasons;
* games with a valid settlement;
* wins and losses.

**Prices:**
* realized win rate (Wilson and bootstrap 95%);
* entry price: mean, median, p10/p25/p50/p75/p90.

**Money:**
* total entry cost, total fees, gross payout;
* gross P/L, fee-adjusted P/L;
* ROI with a bootstrap 95% interval;
* mean and median P/L per contract.

**Comparisons:**
* break-even rate, and realized − break-even;
* market-forecast scoring: Brier and log loss of the entry ask and, where both sides of the book were captured, of
  the mid. These are scored against the same rows as the historical tier frequency, labelled descriptive.
* No paired model-vs-market score is computed: **V2 CONTROL carries no probability by design**, so no valid model
  probability exists on these rows.

**Price buckets** (entry ask, whole cents, fixed in advance):
* buckets: 1–49, 50–59, 60–69, 70–79, 80–84, 85–89, 90–94, 95–99;
* every bucket is reported, including empty ones;
* per tier × bucket: n, wins, win rate, mean entry, ROI, P/L, interval, and mean CLV.

**CLV** (same contract, same side, CLOSING checkpoint):
* close − entry on that side's own ask;
* n, mean, median, favorable / flat / unfavorable %, mean logit movement;
* a missing close is `CLOSE_MISSING`, is **never zero-filled**, and `clv_n` is shown beside n.

**Open-to-close movement:** EARLY_OPEN → PRIMARY → CLOSING per tier, with the direction and magnitude toward or
away from the CONTROL side. Descriptive only.

**Slices** (descriptive): data-quality confidence HIGH/MEDIUM/LOW; prior-games bucket (min of the two teams: 0/1/2/3/4+);
log week; FBS-vs-FBS vs FCS-involved; quote source; population.

**Season blocks** (log `week`; fixed now): weeks 1–2, weeks 3–4, weeks 5+.

**Sensitivities** (descriptive; they cannot change a verdict):
* the opponent-NO contract;
* FBS-vs-FBS only;
* the replay-eligibility subset (both teams with at least one prior game, profile and baseline defined);
* ROI over entry price.

## 8. Uncertainty

* Nonparametric bootstrap resampling games with replacement. Each row is one game, so tiers never share a cluster.
* Percentile 95% interval, 10,000 resamples, seed `20261008`.
* Withheld below n = 5; the point estimate is still shown.
* Football win rates also carry a Wilson 95% interval.
* All tier × bucket, slice and block cells are **EXPLORATORY**, with uncorrected multiplicity.

## 9. Verdict rules (fixed now)

Per tier, with `n` = rows that have an entry quote, a fee and a settlement:

| Verdict | Rule |
|---|---|
| **INSUFFICIENT DATA** | n < 20 |
| **EVIDENCE OF UNDERPRICING** | ROI > 0 **and** ROI 95% lower bound > 0 **and** n ≥ 50 **and** clv_n ≥ 10 with mean CLV > 0 |
| **POSSIBLE UNDERPRICING** | ROI > 0, but not all of the above |
| **OVERPRICED** | ROI ≤ 0 **and** ROI 95% upper bound < 0 |
| **APPROXIMATELY EFFICIENT** | ROI ≤ 0 **and** the 95% interval includes 0 |

**Overall:**

| Condition | Verdict |
|---|---|
| All tiers insufficient | INSUFFICIENT 2026 MARKET DATA |
| Every evaluable tier EVIDENCE OF UNDERPRICING | 2026 CONTROL SHOWS MARKET UNDERPRICING |
| Every evaluable tier APPROXIMATELY EFFICIENT | 2026 CONTROL FOOTBALL SIGNAL HOLDS BUT MARKET IS APPROXIMATELY EFFICIENT |
| Every evaluable tier OVERPRICED | 2026 CONTROL SIDE IS OVERPRICED BY THE MARKET |
| Anything else | 2026 CONTROL RESULTS ARE MIXED BY TIER / PRICE |

**Football continuation** (per tier):
* CONSISTENT if the 2026 Wilson interval contains or exceeds the development rate;
* BELOW_HISTORICAL if its upper bound is below the development rate;
* INSUFFICIENT if n < 20.

## 10. Inclusion and exclusion

**Every** eligible CONTROL game enters. The only exclusions are outcome-independent, and each is counted with its
reason:

`NO_GAME_WINNER_MARKET`, `ORIENTATION_UNRESOLVED`, `ORIENTATION_CONFLICT`, `ENTRY_QUOTE_UNAVAILABLE`,
`FEE_UNAVAILABLE`, `SETTLEMENT_UNAVAILABLE`, `SETTLEMENT_MISMATCH`.

A game is **never** excluded for its:
* team, conference, favorite/underdog status, price, result, week or ranking;
* injury hindsight;
* data-quality confidence;
* whether anyone wagered on it.

## 11. Stages

1. **PROTOCOL**: this commit.
2. **FREEZE**: write the replayed population, CONTROL tiers, orientation and every selected quote (all checkpoints,
   both contracts) to `control_market_manifest.json.gz` without reading any result. Hash it and commit.
3. **REVEAL**: join settlements, re-check both hashes, and generate the report once.
   * Outputs: `control_market_report.json`, `control_market_rows.jsonl.gz`, `docs/CONTROL_2026_MARKET_RESULTS.md`,
     `docs/CONTROL_2026_MARKET_TABLES.md`.

## 12. What this study may not do

* Change a threshold, tier, window, bucket or rule after stage 2.
* Turn a profitable-looking bucket into a "bet up to" price. Any such region is reported as discovery and must
  become a **separately frozen prospective hypothesis**.
* Publish a probability.
* Emit recommendation, stake, play or "+EV" language in any conclusion field.
* Touch V1, V2 shadow, thresholds, intervals, SIFT, market mapping, the router, staking or the prospective ledger.
