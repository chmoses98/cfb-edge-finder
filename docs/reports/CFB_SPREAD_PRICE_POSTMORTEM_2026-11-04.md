# CFB full-game spread losses, price sensitivity and the team-total lean (2026-11-04)

Research only. Aggregate only: no ticker, order id, fill id or team name appears here or in
`spread_price_eval_2026-11-04.json`. Nothing is wired into pricing, recommendation or execution; no threshold, sizing rule or
exclusion is changed. Regenerate every number with:

```
python3 scripts/research/spread_price_eval.py --json docs/reports/spread_price_eval_2026-11-04.json
python3 -m pytest tests/test_spread_price_eval.py
```

Data: the 308 settled wagers in `app/latest/wagers.json` (generated 2026-11-04T02:41Z; placed 2026-09-11 to 2026-11-03),
`data/football/2026/team_games.jsonl` (622 games through week 9) and `schedule.json`. 1U = $75. Intervals are 95%
game-clustered bootstrap. Cells were inspected after the fact: an interval that excludes zero is a lead, and Bonferroni
counts are given where a family of cells was scanned.

## What the data can and cannot do

* **Settled P&L has no model probability, no bid/ask at the fill and no true closing price.** `model_run_id` and
  `recommendation_id` are null on every wager. The comparison below is therefore "the price the bettor paid" against "a
  point-in-time model built here", not against what the production model said. The price paid is an **ask**, so it contains
  roughly one half-spread (1.0-2.0c, from live captures of *other, later* games: a proxy, not the book at any fill).
* **Bet placement time is known** (`placed_at`, UTC) and is used as the information cut-off: a model sees only games
  that finished (kickoff + 4h) before it. 24 of the 150 settled game spread/total bets were placed at or after scheduled kickoff; they
  are excluded from the model comparison (their price contains live game state) and reported separately.
* **Kalshi strike semantics were wrong in the earlier join.** A ticker number N settles YES iff the value is **>= N** (the
  rules say "more than N - 0.5"). With that rule, results derived from final scores agree with Kalshi's recorded settlement on
  **212 of 212** joined spread, total and team-total bets, including the 7 that land exactly on the strike (the earlier join used "> N" and
  left those seven unexplained). A team-code alias table (each alias checked against the schedule) also lifts coverage: all 109 game spreads,
  41 game totals and 62 team totals now join (previously 77, 33 and 50); moneyline bets have no numeric strike and are not joined here.
* Nothing was settled in the team-total market after the earlier sample (section C).

## A. Does a point-in-time rating, with a discrete margin distribution, price the spread/total bets better than the price paid?

Model: walk-forward ridge off/def rating (FBS teams individually, all non-FBS pooled as "FCS", unpenalised intercept and home
field), penalty chosen on weeks 4-6 only (3.0); margin scale from earlier out-of-sample residuals; margin distribution =
discretised Normal x a key-number shape factor (observed / expected frequency of each |margin| in earlier games, shrunk toward
1). Scored: 108 FBS-vs-FBS spread/total bets in 96 games (11 dropped for under 150 games of history, 6 FCS-FCS games without
ratings). **This is accuracy; profitability is separate.**

**A1. Against the price paid (the thing that matters for these bets): no evidence the model is better.**

| set | n (games) | hit rate | Brier price | Brier model | Brier gain of model over price (95% CI) | log-loss gain |
|---|---|---|---|---|---|---|
| all FBS spread+total | 108 (96) | 50.9% | 0.2502 | 0.2641 | -0.014 (-0.042 to +0.016) | -0.030 (-0.092 to +0.035) |
| spreads | 77 (69) | 49.4% | 0.2524 | 0.2719 | -0.020 (-0.058 to +0.018) | -0.043 (-0.127 to +0.039) |
| totals | 31 (31) | 54.8% | 0.2447 | 0.2445 | +0.000 (-0.038 to +0.038) | +0.001 |
| dev weeks 4-6 | 53 (47) | 64.2% | 0.2507 | 0.2497 | +0.001 (-0.043 to +0.046) | +0.002 |
| held-out weeks 7+ | 55 (49) | 38.2% | 0.2496 | 0.2779 | -0.028 (-0.069 to +0.010) | -0.061 |

The price paid scores 0.250, the score of a coin flip: the bets were all taken near 50%, so there is little to discriminate with
at n = 108. The rating model is more dispersed than the price (bin means 0.28 to 0.73 against prices that are 0.35-0.65 on 101 of 108 bets) and
its extremes did not hold (the bin where it said under 35% hit 53%, n = 19). Regression of (outcome - price) on (model - price):
slope **0.20 (CI -0.45 to +0.84)**, where 0 means the price already contains what the model knows and 1 means the model is right. The
interval cannot rule out a modest value in the model, nor show any. Profitability, kept separate: bets where the model said the
price beat its own fee-inclusive breakeven returned -13.7% (n = 52, CI -42% to +17%); the rest -20.8% (n = 56, CI -48% to +12%).

**A2. Where the model *is* demonstrably better: shape and ratings, judged on games (not bets).** Walk-forward, 172 dev and
171 held-out FBS-vs-FBS games with at least 150 games of history:

| claim | held-out weeks 7-9 | dev weeks 4-6 |
|---|---|---|
| key-number shape minus plain Normal, log-likelihood of the integer margin (nats/game) | **+0.156** (+0.090 to +0.220) | +0.131 (+0.093 to +0.170) |
| ridge ratings minus no ratings, same Normal (nats/game) | +0.229 (+0.181 to +0.278) | +0.340 (+0.268 to +0.414) |
| margin MSE: league average + home field / raw points per game / ridge | 365 / 336 / **225** | 682 / 625 / **345** |
| total MSE, same three | 324 / 322 / 297 | 269 / 301 / 246 |

Opponent adjustment is the part that matters for margins: ridge beats raw points per game by 111 MSE points held-out (CI 63 to
161). For totals the ridge gain over raw is not established held-out (CI -3 to +55) and over the league average includes zero.
The shape factor moves a spread probability by a mean of **2.2c (max 5.2c)**, which is below the 2.5-3.2c a taker needs
(section B), so a better distribution does not by itself make a taker-priced bet pay.

**A3. Efficiency, pace, schedule.** Point-in-time season-to-date covariates against the ridge's out-of-sample residual (Pearson r,
game-bootstrap CI): pace (plays) vs total residual +0.03 (-0.08 to +0.13), held-out -0.03 (-0.18 to +0.12); combined success rate vs total
residual +0.01 / -0.03; success-rate difference vs margin residual +0.11 (+0.01 to +0.21) over weeks 4+, +0.06 (-0.09 to +0.21) held-out; explosive-rate difference vs
margin residual +0.13 (+0.02 to +0.24) / +0.08 (-0.05 to +0.21). Four tests: none survives Bonferroni and no held-out interval excludes zero; the
consistent positive sign for margins is worth a prospective test, nothing more. Pace does not explain total misses (the interval
rules out only |r| above about 0.15).

**A4. Descriptive cuts of the 78 scored game spreads** (looked at after the fact; 12 cells scanned, so only a cell with p under 0.004 would
survive Bonferroni, and none does): all -22.6% (CI -47% to +5%); strike under 7: -45% (n = 21); strike 7-13: -24% (n = 41); strike 14+: +58%
(CI +25% to +82%, n = 16); YES side -40% (CI -67% to -6%, n = 47); NO side +9% (n = 31); bet team is the model favourite
-14% (n = 55) vs underdog -41% (n = 23). **YES-side spreads priced above 50c: n = 31, price 56.5%, model 48.1%, actual hit rate
35.5%, ROI -48.7% (CI -76% to -13%), Brier price 0.262 vs model 0.255.** The model also disliked these, but marginally.

## B. Price sensitivity and fee-adjusted EV

**B1. Arithmetic (PROVEN).** The exchange's own quoted taker fee equals `0.07 * ask * (1 - ask)` to within 7e-18 on all
18,688 captured contract sides. Fee per contract, share of price and break-even win rate at the price paid (before any spread):

| price | fee / contract | fee as % of price | break-even win rate | maker fee (quadratic schedule) |
|---|---|---|---|---|
| 10c | 0.63c | 6.3% | 10.6% | 0.16c |
| 20c | 1.12c | 5.6% | 21.1% | 0.28c |
| 30c | 1.47c | 4.9% | 31.5% | 0.37c |
| 50c | 1.75c | 3.5% | 51.8% | 0.44c |
| 70c | 1.47c | 2.1% | 71.5% | 0.37c |
| 90c | 0.63c | 0.7% | 90.6% | 0.16c |

Order rounding (`ceil` to the cent) adds under 0.05c per contract at 20 contracts. **Minimum edge over the mid to clear fee + captured
half-spread**, by ask band (median half-spread of live two-sided quotes; proxy, not fills):

| band | game spread (n quotes) | fee + half-spread | game total (n quotes) | fee + half-spread |
|---|---|---|---|---|
| under 30c | 1,096 | 2.5c | 586 | 3.0c |
| 30-50c | 724 | 2.7c | 565 | 3.2c |
| 50-70c | 675 | 2.7c | 560 | 2.7c |
| 70c and up | 1,431 | 2.5c | 781 | 3.0c |

**B2. Realised ROI by entry price and side, fees counted once** (net = `profit_loss`, already after fees; gross = net + fees; 290 settled
non-combo wagers; wins above price = wins - sum of prices, z against the fair-price null):

| side | price | n | net ROI (CI) | gross ROI | fees / outlay | wins above price (z) |
|---|---|---|---|---|---|---|
| YES | 30-50c | 57 | -5.5% (-38% to +27%) | -1.8% | 3.7% | +1.9 (+0.51) |
| YES | 50-70c | 106 | -15.2% (-36% to +8%) | -12.2% | 3.0% | +0.7 (+0.14) |
| NO | 30-50c | 29 | +6.2% (-37% to +47%) | +9.7% | 3.5% | +0.3 (+0.13) |
| NO | 50-70c | 80 | +12.4% (-13% to +37%) | +15.4% | 3.0% | +12.9 (+2.90) |
| all | 30-50c | 86 | -1.2% (-28% to +25%) | +2.4% | 3.6% | +2.3 (+0.49) |
| all | 50-70c | 186 | -3.3% (-20% to +14%) | -0.3% | 3.0% | +13.6 (+2.01) |

Under 30c (n = 7, -70.5%) and over 70c (n = 11, -32%) are too thin to read. Game spreads only: YES 50-70c -51.9% (CI -76% to -22%, n = 42); NO 50-70c +19.3% (n = 28, CI -22% to +55%). The NO 50-70c cell has z = +2.90 on 11
price-by-side cells (Bonferroni p about 0.04) but 28 of its 80 bets are team-total NO (section C); without them the cell is +0.4% ROI (n = 52), wins above price +6.5, z = +1.83. The fee is a roughly constant 3.0-3.7% of outlay in the 30-70c range where nearly all of the book sits, i.e. 3-4 points of ROI that an
edge must first supply.

**B3. A rule fixed before looking, judged chronologically.** Rule: exclude YES-side game spreads priced above X; X is the grid value
(20/30/40/50/60c) whose excluded set has the lowest first-half ROI with at least 10 bets. First half = 145 bets placed before
2026-10-10, second half = 145 bets.

* Derived on the first half only: **X = 50c** (excluded n = 21, ROI -35.9%).
* Second half, never used to choose X: excluded n = 19, **ROI -62.8% (CI -92% to -26%)**; kept bets (n = 126) -0.5% (CI -19% to +18%);
  all second-half bets -9.7%. Applying the rule lifts second-half ROI by **+9.2 points (CI +2.2 to +17.4)**, $1,335 of loss avoided
  (about 17.8U), one-sided bootstrap p = 0.007 (Bonferroni over the two rules tried, 0.014). Whole sample: 40 bets, -$1,954 on $3,851
  (-50.7%, CI -75% to -19%) against a total net of -$919.
* **Why this is not proven:** X was derived out of sample, but the *rule class* ("YES-side game spreads") was chosen after the full
  sample, including the second half, was seen (WAGER_LOSS_STRUCTURE showed YES spreads at -37.9%). The out-of-sample test is
  therefore of the threshold, not of the idea. It is also 19 bets in 17 or so games. It needs bets placed after 2026-11-03.
* Second fixed rule (nothing tuned): exclude any bet under 30c. Only 1 second-half bet falls under it: untestable.

**B4. Timing.** Of 211 joined game spread/total/team-total bets, 40 (19%) were placed after scheduled kickoff: ROI +2.0% (CI -26% to
+31%); 158 within 3h before kickoff: -5.9% (CI -24% to +13%); 13 between 3 and 24h before; essentially none earlier. No cell is distinguishable
from zero. In-play prices carry game state, which is why those bets are not scored against a pregame model.

## C. Team-total NO, prospectively

* **Settled team-total bets placed after the earlier sample (last placed 2026-10-31): zero.** The refreshed export has 309 wagers,
  the same 308 settled plus one pending game spread placed 2026-11-03, and the `accounting-data` ledger has the same 309 rows. There is
  nothing new to test, so the lean is **not tested prospectively**; the script reports `n_after_earlier_sample` and re-tests with
  `--after` as soon as bets exist.
* Current sample (better join, 36 NO bets): wins above price **+7.3 (z = +2.46, raw p = 0.014, Bonferroni over 8 family-by-side cells p = 0.11)**,
  ROI +27.9% (CI -2.9% to +57.2%). Team-total YES: -3.9% (n = 26).
* Power: the observed edge is 20 points of win probability per bet, and it is a post-hoc maximum, so it is probably inflated. Even if it were
  real, 76 bets are needed for 80% power at the Bonferroni level; at 5.1 bets per week that is about 8 more weeks, i.e. late in the bowl
  season. At a more realistic half-size edge the requirement is about 300 bets.

## Ranked findings

| # | Finding | Evidence | Estimated impact | Proposed fix | Status |
|---|---|---|---|---|---|
| 1 | YES-side game spreads priced above 50c lose | threshold from first half (n=21, -35.9%); second half n=19 -62.8% (CI -92% to -26%); whole sample 40 bets -50.7% (CI -75% to -19%); model also rated them below price (48.1% vs 56.5% paid) | second half +9.2 pts ROI (CI +2.2 to +17.4); about $1.3k of 145 bets' loss; whole sample -$1,954 | Candidate risk rule "no YES-side game spread above 50c" (a restriction only), shadow-tested on bets after 2026-11-03; owner decision | PROMISING (rule class chosen after seeing full sample; n small) |
| 2 | Fees plus half-spread need 2.5-3.2c of edge over the mid; fee is 3-4% of outlay at 30-70c | quoted fee equals formula on 18,688 sides; about 6,400 captured contract sides | the whole gross-to-net gap (about 3 pts ROI); a 2c model edge cannot pay | Require model edge net of fee + half-spread before any taker order; prefer resting orders where maker fee is zero (fill rate unmeasured) | PROVEN (arithmetic; spreads are live-capture proxies) |
| 3 | Opponent-adjusted ratings predict margins far better than raw points or the league average | held-out margin MSE 225 vs 336 vs 365; ridge-vs-raw gain 111 (CI 63 to 161) | accuracy only; the production model should already do this | None needed; keep as the benchmark any new rating must beat | PROVEN (accuracy, 171 held-out games) |
| 4 | A discrete key-number margin distribution beats a Normal | +0.156 nats/game held-out (CI +0.090 to +0.220); dev +0.131 | moves a spread probability 2.2c on average (max 5.2c), under the 2.5-3.2c cost | Use as a filter or resting-order input, not as a standalone taker edge | PROVEN (accuracy); NOT shown profitable |
| 5 | The model beats the price paid | Brier gain -0.014 (CI -0.042 to +0.016); slope 0.20 (CI -0.45 to +0.84) | none demonstrated | Record model probability with every candidate and bet (still the missing input); re-test with 100+ more bets | NEGATIVE for use now; INSUFFICIENT to rule out a modest value |
| 6 | Team-total NO outperformed | z = +2.46 on 36 bets (Bonferroni p = 0.11); ROI +27.9% (CI -2.9% to +57.2%); zero settled bets since the earlier sample | none estimable | Freeze the hypothesis; test only on bets placed after 2026-11-03; about 8 weeks at the current rate | INSUFFICIENT (not testable yet) |
| 7 | Efficiency (success, explosive rate) improves margin residuals; pace explains totals | margin r +0.11/+0.13 over weeks 4+, held-out +0.06/+0.08 with CIs spanning 0; pace/total r about 0 | none | Prospective test only | INSUFFICIENT (pace: NEGATIVE within +/-0.15) |
| 8 | Big-line spreads (14+) paid, small ones did not | strike 14+ +58% (CI +25% to +82%, n=16); under 7 -45% (n=21) | n/a | Do not act: 12 cells scanned, none survives Bonferroni | INSUFFICIENT |
| 9 | In-play (after kickoff) bets differ | 19% of bets; ROI +2.0% (CI -26% to +31%) | none | Record whether a bet was in play and the game state | INSUFFICIENT |
| 10 | Excluding sub-30c contracts helps | 7 bets total, 1 in the second half | none | n/a | INSUFFICIENT |
| 11 | Earlier join mis-stated strike semantics | ">= N" reproduces 212 of 212 settlements, ties included; ">" left 7 unexplained | corrects every earlier derived result | Use `yes_wins` in this script | PROVEN |

No finding here shows the book has a positive edge after fees. The one candidate that survives a chronological split (row 1) is a
restriction on a bet type that was already losing, selected after the whole sample was visible.

## Limits and what was not done

* No true close: the only historical prices are fill prices. Period and team-total lines for games that were not bet on were never captured, so
  team-total mispricing cannot be tested market-wide.
* The half-spread proxy comes from quotes of games on 2026-11-03 to 11-07, not the books at the time of any fill.
* Ratings are a single season, with 150 to 500 games of history; weeks 1-3 are not scored. FCS-vs-FCS games have no ratings.
* The bets are the owner's selected bets, near 50% by construction, so Brier cannot discriminate between candidate models well; a test across all
  available strikes would need captured historical ladders.
* CI: this PR adds a standalone script, 20 unit tests and docs; no production module is touched.
