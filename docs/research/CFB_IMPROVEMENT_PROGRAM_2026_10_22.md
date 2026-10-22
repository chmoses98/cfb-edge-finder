# CFB improvement program — first increment (2026-10-22)

## Status and boundaries

Research-only change. The production V1 Script Engine, V2 frozen claims, Kalshi catalog,
prospective ledger, market mapper, pricing, qualifications, and SIFT publication are
**not modified** by this increment. The V2 report is augmented with a prospective
football-only diagnostic; the report schema becomes `cfb_script_engine_v2_report/1.1.0`.
Historical ledger rows are not edited.

The first goal is to treat MODERATE CONTROL as a useful **pregame explanatory input**
without mistaking the hypothesis "CONTROL plus another favorable matchup factor"
for a confirmed or priced betting signal.

## Implemented

- The reporting pipeline reads existing, prospectively frozen V2 FINAL_PREGAME
  settlement rows and keeps only the latest row per physical game.
- The CONTROL side/strength tier is crossed with *four pregame dimensions*:
  PACE, SCORING_ENVIRONMENT, DEFENSIVE_SUPPRESSION and DISRUPTION_ALIGNMENT.
- Cohorts remain divided by HOME/AWAY and MODERATE/STRONG, because pooling them
  would obscure large differences in base rates.
- Every cohort records observations, decided outcomes, wins, losses, undecided
  results, historical control-side margin, and explicit insufficient-sample status.
- No claim of win-rate lift, market inefficiency, probabilities, EV, betting
  recommendation or causal mechanism is made. The same game appears in several
  tables, so cross-table counts are not independent.
- UNAVAILABLE is not silently treated as NO_CLAIM.
- Tests cover deduplication, missingness, home/away orientation and the absence
  of signal promotion.

## Required next research increment — test incremental predictive value

The descriptive 2026 cohorts are **already observed** and cannot be used as
unseen validation. Proposed hypotheses must be preregistered and tested on later
untouched weeks/seasons.

1. Baseline: a probability and score/margin distribution calibrated using
   opponent-adjusted offensive/defensive matchup profiles, home/away, and
   team/opponent quality; compare both to a market-price-only reference and
   a base model without CONTROL.
2. Add CONTROL **side and strength** as features; compare proper scoring rules,
   signed residuals, calibration by price and field position, and incremental
   net-of-fee market performance.
3. Add *predeclared* CONTROL interactions one at a time: aligned rushing
   efficiency, opposing rush defense, pressure versus pass protection,
   aligned disruption, and pace; include neutral/opposed cases and missingness.
   Rushing and opponent-quality components must come from frozen pregame
   measurements rather than retrofitting realized-game outcomes.
4. For each component, require improvements across distinct seasons and
   actual strength-of-schedule tiers, not just aggregate backtests. Use
   blocked walk-forward estimates and account for multiple hypotheses.
5. Freeze full-game, 1H and 1Q distributions *before* reading Kalshi prices.
   Audit contract settlement, use exact executable asks and fees, calibrate
   by family and price range. No pricing claims from qualitative V2 bands.
6. Require prospective confirmation of net-of-fee economics before promoting
   betting signals or changing sizing.

The 2026 V2 sample (20 settled CONTROL claims in the checked report) is too
small to identify market edge. In particular, conditional slices must not
be represented as independent wagers or as repeated validation samples.

## Script Engine repair — follow-on increment

Retire unvalidated V1 narrative archetypes per `docs/SCRIPT_ENGINE_V2_MIGRATION.md`
**only after** its Stage-3 migration checks pass. Retain distinct scoring:
(a) CONTROL winner direction, (b) scored margin and points distributions,
(c) mechanism agreement, and (d) strict script-description match.
The observed V1 strict-match rate is not a direct estimate of winner accuracy.

Replace arbitrary V1 7–24 or 17–45 outcome bands with independently validated
marginal and joint distributions; grade all three realistic scripts (control,
competitive, disruption), including actual probabilities only when calibrated.
A value signal and a narrative label are separate outputs. Preserve no-leakage,
frozen artifacts and explicit abstention.

## Accounting follow-on increments (separate PRs; private data)

**CLV:** Link exact Kalshi ticker+side wager fills and weighted entry costs
against an explicitly timestamped, executable, same-contract pre-kickoff
close. Enforce cutoff `0 < minutes_to_kickoff <= 14`. Differentiate a genuine
captured close, a near-close observation and missing/unavailable history.
A market candlestick is not by itself a verified executable two-sided quote.
Report same-side signed movement, spread/midpoint checks when supported, and
fees separately. Never infer CLV from game result.

**Bankroll:** Collect private read-only Kalshi portfolio/balance snapshots and
open-position exposure on the existing account-side integration. Distinguish
cash, portfolio/mark-to-market value, deposits/withdrawals and realized
settled P/L. Credentials, account balances and private transaction records
must not enter public GitHub or SIFT feed files. The current CFB app export
correctly reports bankroll and CLV as unavailable rather than making values up.

## Promotion / release criteria

- Tests, code review, CI and shadow comparisons pass.
- The descriptive diagnostic carries **no live recommendation authority**.
- A subsequent model/market promotion requires a separate reviewed proposal
  and prospective evidence; do not treat this first increment as that approval.
