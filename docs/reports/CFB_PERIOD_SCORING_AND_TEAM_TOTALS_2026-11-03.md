# CFB 1Q / 1H scoring shape, and the team-total postmortem (2026-11-03)

Research only. Aggregate only (no ticker, order id or fill id). Nothing here is wired into pricing, recommendation or
execution, promotes a model, or changes 1U = $75, the 5U-per-bet cap or the Auburn exclusion. Regenerate with:

```
python3 scripts/research/period_scoring_eval.py --team-games team_games.jsonl --backfill period_scores.jsonl
```

Data: 620 of 622 games with quarter scores (573 backfilled and validated, 47 already in `team_games`); 2 quarantined for
TEAM_MAPPING_MISMATCH; 498 are FBS-vs-FBS. Walk-forward, point in time: every statistic for a test week uses only earlier
weeks. Dev weeks 5-6 chose the ridge penalty only; **held-out weeks 7-9 (171 FBS games)** were never used to tune.
Intervals are 95% bootstrap over games.

## 1. 1Q / 1H scoring

**Proven on held-out data: period scores are lumpy and a Normal misprices the key numbers.**
Discrete empirical pmf minus Normal, held-out log-likelihood (nats/game, + = discrete better):

| target | gain | 95% CI |
|---|---|---|
| Q1 total | +0.866 | +0.742 to +0.985 |
| Q1 margin | +0.735 | +0.608 to +0.856 |
| 1H total | +0.249 | +0.117 to +0.380 |
| 1H margin | +0.110 | -0.025 to +0.245 (not established) |

The Normal's error is largest at the strikes that matter (all 498 FBS games, descriptive, in-sample):
Q1 total over 7.5: empirical 59.0% vs Normal 68.8% (-9.8 pts); over 10.5: 41.8% vs 51.5% (-9.8); over 6.5: 79.5% vs 73.9%
(+5.6). 1H total over 24.5: 51.6% vs 57.8% (-6.2). A Normal-priced period total is therefore wrong by up to ~10c at 7.5/10.5.

**Not established: team ratings for these periods.** Ridge off/def ratings vs league average, held-out MSE change:
1H total +5.8% (CI -1.2 to +13.6), Q1 total +0.7% (-0.4 to +1.8), Q1 margin +0.7% (-13.2 to +15.0), 1H margin -7.5%
(-26.2 to +11.2). A single earlier run with a different penalty/baseline showed 1H total at +7.0% with a CI excluding
zero; this reproducible version does not, so treat it as *suggestive, not demonstrated*. Added covariates (pace, early
success rate, explosive rate) did not improve any target (every interval included zero): **insufficient evidence**.

**The 1Q/1H wagers actually placed (the only genuine historical period quotes).** 33 of 36 matched to a game; results
derived from recovered quarter scores agree with Kalshi's settlement on 32 of 32 non-tied bets. Net ROI -14.3%
(CI -53% to +38%), hit rate 61% vs 54% fee-inclusive breakeven. Q1 spreads -30% (n=11), 1H spreads -28% (n=11), Q1 totals
+30% (n=8), 1H totals +42% (n=3). **All intervals but one include zero; the one that excludes it has n=3.** On the 11
total bets, the price implied a 51.7% breakeven while the climatological discrete probability (games before that week)
was 55.9%, and 8 of 11 won. Total-market bets paying below the discrete probability is a lead (n=11), not an edge.

Caveat that matters for money: crossing a period book costs 14-19c (see WAGER_LOSS_STRUCTURE_2026-11-03). A ~4-10c
shape correction does not pay for a taker in those books; at best it is a filter and a resting-order input.

## 2. CFB team-total postmortem

Question: +19.8% historical ROI vs negative proxy CLV.

* **CLV is measured on the actual fill price, fees excluded.** Verified for all 308 wagers: stake = price x contracts +
  fees. Fees are therefore not in CLV; they are accounted separately (about 3.2% of outlay overall).
* **The "close" is a last-capture proxy, not a true close.** Team-total CLV vs it: mean -5.3c (CI -9.9c to -0.6c, n=28),
  of which ~2.3c is mechanical half-spread (buying at the ask vs a mid-based reference). Remaining ~3c is real adverse
  movement or capture-timing noise; the proxy cannot separate them. True-close recovery (Kalshi candlesticks) is pending.
* **Outcomes.** Team-total NO is +2.5 sd from the fair-price null (raw p=0.011; Bonferroni over 8 cells p=0.09),
  consistent in both chronological halves. Promising hypothesis only.
* **Not explained by the handicap.** The point-in-time model leaned the same side as the bet in only 6 of 43 bets; for
  NO bets it projected the team 2.6 points *above* the strike, while actual scores landed 4.7 below. Correlation of model
  lean with Under margin: -0.34 (n=28). So the P&L is consistent with selection, regression, luck, or a market-structure
  pricing effect (e.g. thin team-total books misplacing the over/under skew) that cannot be tested without historical
  team-total lines for all games, which were not captured.
* **Separation of causes** (what the data can support): *fees* ~3c/contract, measured; *spread* ~2.3c, measured; *timing /
  adverse movement* ~3c, upper-bound only (proxy close); *handicapping* not supported by the model; *variance* dominates
  (CI on ROI spans zero). No repeatable advantage is demonstrated.

## 3. Ranked findings

| # | Finding | Evidence | Status |
|---|---|---|---|
| 1 | A Normal misprices Q1/1H totals and margins at key numbers; a discrete pmf is better | held-out +0.87 / +0.74 / +0.25 nats, CIs exclude 0 (1H margin does not) | **Proven (held-out, 171 games)** |
| 2 | Fees + spread, not handicapping, explain the bulk of realised loss; period books cost 14-19c to cross | WAGER_LOSS_STRUCTURE | **Proven (arithmetic on quotes)** |
| 3 | Team-total NO outperformed; model does not explain it | z=+2.54, Bonferroni p=0.09; corr -0.34 | Promising hypothesis |
| 4 | Period total bets priced below discrete probability | 51.7% breakeven vs 55.9%, n=11 | Promising hypothesis |
| 5 | 1H total ratings beat league average | +5.8%, CI -1.2 to +13.6 | Insufficient evidence |
| 6 | Pace / early success / explosive rate help period predictions | all CIs include 0 | Insufficient evidence |
| 7 | Team-total CLV is negative because of timing | proxy close only | Insufficient evidence (needs true close) |

Financial impact is not estimated for #1: a correction of up to ~10c on a Q1 key number would matter only for resting
orders or filtering, because the cost to cross is larger. No fee-adjusted ROI improvement is claimed.
