# CFB realised P&L: where it went, and what it costs to cross the book (2026-11-03)

Aggregate only. No ticker, order id or fill id appears here. Regenerate every number with:

```
python3 scripts/research/wager_loss_structure.py --wagers app/latest/wagers.json
python3 scripts/research/crossing_cost.py
```

Data: the 308 settled CFB wagers in `app/latest/wagers.json` (export of 2026-11-03T08:49Z; 236 games; 2026-09-11 to 2026-10-31).
1U = $75. Intervals are 95% game-clustered bootstrap (positions in one game are one outcome, not independent bets).

## What this analysis cannot do

**It cannot separate handicapping error from pricing error.** No model probability, recommendation id or model-run id is
recorded with any of these wagers (all null in the export), so there is nothing to compare the market price to. Anything
that claims to split the two for this sample would be invented. The same limit is stated for the NFL wagers in
`nfl-edge-finder/docs/research/ACTUAL_WAGER_DIAGNOSIS_2026-10-25.md`.

Cells below were looked at *after* the results, so an interval that excludes zero is a lead for a prospective test, not
a validated edge, and a positive cell is not shown to be profitable by a sample whose interval includes zero.

## Findings

**1. Fees are most of the loss, and there is no detected gross edge.**
Net -$919 on $22,158 outlay (-4.1%, CI -18% to +9%). Before fees: -$212 (-1.0%, CI -15% to +13%). Fees are $707 (3.2% of
outlay), i.e. about three quarters of the net loss. The book is statistically indistinguishable from "fair prices, minus
fees". NFL shows the same shape: win rate 45.7% against 46.2% expected at the prices paid, fees 3.3% of outlay, results
-0.95 sd from the fair-price null before fees and -1.26 sd after.

**2. Fee burden is heaviest where the contract is cheap.** Fees as a share of outlay: under 30c 5.4%, 30-50c 3.7%, 50-70c 3.0%,
70c and up 1.5%. (Kalshi's fee is proportional to price x (1 - price), so this is arithmetic, not a sample result.)

**3. Fixed structural rules recover only a minority of the loss.** Rules were fixed before the numbers were read:

| scenario | net $ | ROI |
|---|---|---|
| actual | -919 | -4.1% |
| cap every bet at 5U | -782 | -3.6% |
| cap outlay per game at 5U | -737 | -3.4% |
| 5U per bet + 5U per game + no combos | -697 | -3.3% |
| one position per game (earliest placed) | +448 | +2.6% (CI -13% to +17%) |

The 5U cap matters mechanically (one 6.8U position lost $512; 43% of positions sit in games that carry two or more
positions) but is worth ~$140-220 here. "One position per game" moves the sign, but its interval is wide and it removes 72
bets, so it is a lead, not a finding. In NFL, three games carried about half the net loss; in CFB the worst three games are
14% of game-level losses, so the concentration is milder.

**4. Market family and side (leads only).** Game spread: 109 bets, -20.5% (CI -41% to +1%). Buying the YES side of a game spread:
64 bets, -37.9% (CI -62% to -9%); the NO side +7.3% (CI -27% to +39%). Team totals +16.1% (CI -8% to +40%), game totals +7.6%,
moneyline +2.5%. NFL independently shows the same ordering (spread -43%, team total +9%, total +9%, parlays -39%). Two
independent samples pointing the same direction is encouraging, but both were examined after the fact and no family
interval is established in both. Not a validated edge.

**5. The apparent early edge did not persist.** Chronological halves (fixed split): first 154 bets +7.4% (CI -16% to +32%),
second 154 bets -10.2% (CI -27% to +7%), while the mean bet roughly doubled ($50 to $94). That is the signature of a
sample with no stable edge, not of a strategy that stopped working.

**6. Bet size carried no information.** Spearman correlation of bet size with per-bet return is -0.000 (permutation p = 0.995).
Sizing up on conviction did not select better bets in this sample.

**7. Results derived from final scores agree with Kalshi's settlements** on 175 of 175 non-tied full-game bets that could be
joined (182 of 308). The other 126 are period markets (need quarter scores, see the backfill), combos, and team-code
mismatches. Period-market P&L (1Q/1H spreads and totals, 36 bets) therefore cannot be checked against results yet.

**8. Cost to cross the book, by market family** (30c-70c ask, live captures of 2026-11-03, not the books at any fill):

| family | half-spread | taker fee | cost to cross (mean / median) |
|---|---|---|---|
| game spread | 1.6c | 1.65c | 3.3c / 3.2c |
| game total | 2.0c | 1.68c | 3.7c / 3.6c |
| team total | 2.3c | 1.66c | 3.9c / 4.0c |
| game moneyline | 6.8c | 1.66c | 8.5c / 7.2c |
| first-half spread | 12.5c | 1.66c | 14.1c / 15.5c |
| first-half total | 17.4c | 1.69c | 19.0c / 25.7c |

A taker needs an edge over the **mid** at least this large to break even. A 2c model edge cannot clear 3.3-3.9c on the
tightest families and is nowhere near the period markets. Wide period books may be stale or illiquid, which is why the
median is shown, and these are captured quotes, not realised slippage. About 56% of live CFB contracts
(4,557 of 8,118) use the `quadratic` schedule with no maker fee; 44% charge makers too.

## Concrete implications for bet selection (none implemented as a strategy change)

1. **Record the model probability, model run and recommendation id with every candidate and every bet.** This is the
   single missing input. Until it exists, no future analysis can tell a wrong handicap from a bad price, and every
   "improvement" below stays unprovable. It needs an owner decision on where the number lives (the repository is public).
2. **Do not cross the book on period markets without an edge well above 14-19c**, or do not cross at all. This is a
   restriction (it can only remove candidates) and would need the owner's sign-off as a risk rule.
3. **Measure before changing execution.** On the `maker_fee_applies=false` contracts a resting order pays no fee, which is
   the largest controllable cost here. Fill probability and adverse selection are unknown, so the right step is a
   shadow measurement of resting-order fills, not a switch.
4. **Hold the evaluation out.** Freeze the rules now and judge them on wagers placed after 2026-11-03. Any family or
   price-band rule chosen from the tables above and tested on the same wagers would be selection on the outcome.

Nothing in this report promotes a model, changes a threshold (the default minimum net edge stays 0.0 by design), or
alters the 1U = $75, 5U-per-bet and Auburn rules.
