# CFB wager loss review and independent bet-selection risk protocol

Status: **analyst procedure**, not a claim of a profitable strategy. This protocol never places orders and does not promote CFB research models.

## Verified Oct 22–30, 2026 router ledger

Source: the append-only \`accounting-data\` branch in this repository (\`wagers/2026.jsonl\`, \`settlements/2026.jsonl\`, \`settlement_amendments/2026.jsonl\`). For any settled wager, apply its canonical amendment if one exists. Group by **US/Eastern execution date**, not ticker date.

- 67 executed bets; 60 settled, 7 pending; 30 won and 30 lost.
- Settled stake $5,878.5074; realized P&L **-$684.1374**; realized ROI **-11.6379%**. Additional pending exposure $682.9741 is excluded from realized ROI.
- Oct 24: **44** wagers and $4,105.92 of recorded gross exposure.
- Game spreads: 19 settled, 6 wins, 13 losses, **-$1,306.9190**, -57.56% ROI. Before Oct 22, 82 game-spread bets returned approximately +2.8% ROI; the reversal is a trigger for diagnosis, not proof spreads are unprofitable.
- Team totals: 20 settled, 15 wins, 5 losses, **+$796.1430**, +36.93% ROI. This is *not* enough out-of-sample evidence to recommend blindly betting all team totals.
- Oct 22 JMU had $375 correlated exposure in one game; Oct 24 Houston/Utah had $260 on related outcomes; the Oct 24 Texas A&M team-total ticker had bought YES and NO on the same underlying contract (inspect fill chronology before calling it a hedge).
- Three highest-risk losing spreads, $300 JMU, $200 Air Force and $200 against Utah, lost approximately $700 together. Larger stakes were not supported by documented, validated fee-adjusted probabilities.

## Required for every future RUN CFB betting card

1. **Separate football opinion from price edge.** After verified opponent-quality, scheme, injury, weather, tempo and three-game-script evaluation, identify the exact market and buy side. The contract's captured ask alone does NOT justify any edge. Never invent fair probabilities or a bet-up-to limit. If there is no defensible, source-grounded probability interval after fees and market comparison, label a *football lean / conditional candidate*, not a positive-EV bet, and do not assign a conviction stake.
2. **Show actual all-in acquisition cost.** Select YES at its executable YES ask or NO at its executable NO ask; add its actual trade fee, check quoted buy-side size and settlement conditions, distinguish capture from live market verification. Only specify bet-up-to cents if supported by a defensible estimated fair probability **and** uncertainty margin.
3. **Aggregate and communicate risk across games and periods.** Before recommending stakes, compare all prior recommendations for the day (including other window cards), identify the same physical game, derivative contracts and economically overlapping scripts. Show the *total cumulative daily gross proposed exposure* and *same-game combined gross exposure*. Do not count correlated legs as independent diversification.
4. **Interim exposure alerts, not proven profitable thresholds:** flag any single game with >5U of combined recommendations, any date with >15U of combined CFB recommendations, any same-ticker opposing sides, any stake >5U, and cumulative exposure that would be surprising relative to bankroll. Re-rank or reduce positions by actual marginal evidence; never silently hide the flag. These are risk-control warnings, NOT empirically calibrated value rules.
5. **No post-loss stake inflation.** No recovery bets, doubles, extra props, or extra parlays to chase a loss. If 2+ bets rely on the same team gaining offensive control or one team's scoring floor, name the correlation and compare a single better contract versus stacking.
6. **Performance loop.** For every settled slate, reconcile recommendation → actual router execution (including NO vs YES, exact price, fees, side, time) → settlement (amendment-aware), then add a timestamped near-close quote if obtainable. Group ROI by family, side, price band, favorite/underdog, opponent-quality cohort, execution timing and staking tier. Mark missing recommendation provenance and closing-line metrics **UNKNOWN**, never manufacture them.
7. **No retrospective overfitting.** Do not ban spreads or increase team-total stakes because of 19 and 20 recent settled bets respectively. Evaluate any proposed market-family preference prospectively and out-of-sample, with fees and correlated-game clustering.

## Architecture boundaries

The current CFB repository is a Kalshi market-catalog and research source, **not a licensed/validated predictive wagering engine**. Its retired prediction model remains research-only. This document governs external analysts interpreting that data. The router and \`accounting-data\` preserve what the owner actually traded; neither establishes that an analyst recommendation or bet was positive-EV.

**Do not activate auto-trading, modify Kalshi credentials, change market collector cadence, or publish a profitable-edge claim from this review.**
