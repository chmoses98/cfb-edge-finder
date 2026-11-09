# RUN CFB card: Tuesday Nov 10, 2026 MAC slate (3 games)

Authority: **analyst advice only**. RESEARCH_ONLY, no order, approval or stake automation. Nothing here is an empirically
validated or positive-EV wager (no reliability table exists; `qualified_positive_ev` stays false). Prices are **captured**
(`quotes.json`, commit 5a36864cf, 2026-11-09 19:33 UTC), **not live-verified**: a direct Kalshi request from this session
returned HTTP 429. Handicaps: `docs/reports/CFB_ANALYST_HANDICAPS_2026-11-10.json` (signed, evidence, thesis, opposing case,
stated uncertainty region). Audit: `docs/reports/CFB_MARKET_AUDIT_2026-11-10_analyst.{md,json}`. Written 2026-11-09 ~22:00 UTC,
about 21 hours before the first kickoff (7:00 pm ET Nov 10).

## (a) Game coverage

| Game | Kickoff ET | My projection (home / away, margin, total) | Flags and research |
|---|---|---|---|
| Kent St. at Bowling Green | 7:00 pm | BG 24.0, Kent 18.5; BG by 5.5, total 42.5 | QBs Kastantin / DeShields from box scores, no injury found, not confirmed. Kent skill absences reported but possibly stale. 53F, 10 mph. Three reads of the total disagree (adjusted ~38, baseline 43.9, vs bottom-third opponents 45-52), so I blend to 42.5. |
| Western Michigan at Akron | 7:00 pm | Akron 19.5, WMU 28.0; WMU by 8.5, total 47.5 | WMU QB Lowry; Akron QB Broughton back from a 5-game ankle absence (22/34 vs Ohio). TE Alexander out (unofficial). ~50F. Margin reads disagree: matchup metrics WMU -6, adjusted net ratings WMU -10, **Kalshi ladder centred near WMU -14.5 (not used to form my number)**. |
| Ohio at Miami (OH) | 8:00 pm | Miami 23.0, Ohio 24.5; Ohio by 1.5, total 47.5 | Ohio QB Poulos (0 INT); Miami QB Heavner (McComb and Gotkowski out for the season); Ohio K Hryszko out. 50F, 6 mph. Net ratings Ohio -0.92 vs Miami -5.59 plus home edge. |

Opponents actually played were verified from `schedules.json` (Kent's road scoring 4.4 ppg vs 26.8 at home; WMU's wins over
Kent 38-7 and BG 23-10; Ohio's 28-20 win at Akron). No havoc metric used. Historical seeds were not used for the numbers; the
seed cross-check ran only as a draft comparison.

## Market-family coverage (284 contracts, 568 sides, all reconciled, 0 structural defects)

| Family | Contracts | Sides | Analyst-priced | Candidates | Not priced |
|---|---:|---:|---:|---:|---|
| Game moneyline | 6 | 12 | 12 | 0 | |
| Game spread (incl. alternate rungs) | 86 | 172 | 172 | 1 | |
| Game total | 57 | 114 | 114 | 0 | |
| Team total | 21 | 42 | 42 | 0 | |
| 1H moneyline | 9 | 18 | 12 | 1 (size 41) | 6 tie sides: need a discrete model, SHAPE_ONLY |
| 1H spread | 63 | 126 | 126 | 1 (size 1) | |
| 1H total | 42 | 84 | 84 | 0 | |
| Quarters, props, winning margin, overtime | 0 listed | 0 | | | none listed for these games |

Totals: 562 analyst-evaluated sides (3 `EVALUATED_CANDIDATE`, 559 `EVALUATED_PASS`), 6 shape-only, 0 unpriceable,
0 insufficient evidence, 0 not evaluated. Every disposition came from my signed handicaps, not from derived estimates.

## (b) Ranked card

### Analyst recommendation (stated uncertainty; not validated, not +EV-labelled)

| Rank | Game | Bet (YES/NO) | Kalshi Price | Bet Up To | Stake | Why I Like It | Confidence |
|---|---|---|---|---|---|---|---|
| 1 | Western Michigan at Akron | **NO** "WMU wins by over 13.5" (`KXNCAAFSPREAD-26NOV10WMUAKR-WMU14`), i.e. Akron +13.5 | $0.47 NO ask, size 12,543, captured (rung last changed 11-05 20:06 UTC); all-in $0.4875 at 1U | **$0.48** (lower bound 0.50) | advisory <= 1U ($75 all-in, about 154 contracts) | My WMU margin is 8.5 (range roughly 4 to 13 over the stated region): WMU's defense is the weakest in the game (adj 32.7 ppg allowed, worst sack rate nationally), its wins are narrow versus bad teams, and Akron's QB is back. Probability WMU does NOT win by more than 13.5: low/central/high 0.50/0.63/0.78 vs break-even 0.487. Cheapest clean expression: the NO on the WMU -13.5 rung beat Akron ML and the 1H rungs on liquidity, and no lower rung clears at its lower bound. Would change my mind: Lowry or Broughton news, WMU skill/OL returns, or a line move showing the market knows something about Akron's QB. | **Low-moderate.** The lower bound clears break-even by only 1.3 points, I disagree with the market by about 6 points, QB/injuries are unconfirmed, and the model is uncalibrated. |

### Conditional candidates (clear at their lower bound but not executable as quoted)

| Game | Bet | Captured price | Bet Up To | Why conditional |
|---|---|---|---|---|
| Western Michigan at Akron | NO "WMU wins 1H by over X" (`KXNCAAF1HSPREAD-26NOV10WMUAKR-WMU5`) | $0.38, size **1** | $0.39 | one contract is not a 1U ticket; same thesis as rank 1, do not stack |
| Western Michigan at Akron | YES Akron wins 1H (`KXNCAAF1H-26NOV10WMUAKR-AKR`) | $0.20, size 41, last changed 10-28 | $0.21 | size about $8 and a 12-day-old rung; same thesis, do not stack |

### Passes (every other side)

All 559 other evaluated sides are passes at the captured price. Notable ones: Ohio at Miami moneyline (Ohio $0.52 vs my
0.41/0.53/0.65, Miami $0.49 vs 0.32/0.45/0.56), Bowling Green moneyline ($0.66 vs my 0.51/0.63/0.77), every WMU/Akron
moneyline, the Kent St./BG Under lean (NO Over 44.5 at $0.49 does not clear), (WMU $0.86 vs 0.57/0.71/0.85; Akron YES $0.16 vs low 0.14) and all totals and team totals (my lower bounds never clear
break-even). Ties and 1H ties are not priced.

## (c) Exposure, correlation, gaps

* Recommended gross exposure: **1U ($75) total, 1U in one game, 1U on the day**, well under the 5U per game / 15U per day flags.
  Conditional candidates share the rank 1 thesis (Akron stays within the number or leads early), so they are the same bet, not diversification.
* Existing exposure: the accounting export (`app/latest/wagers.json`, 330 wagers, generated 2026-11-09 20:44 UTC) holds no
  position on these three games. Router fills after that time were not visible to me.
* Owner rules: no Auburn game; the recommendation is not a bet against Auburn; kickoffs are at least 21 hours away.
* Remaining evidence gaps: starting QBs and injuries are unconfirmed (no official injury report retrieved; search summaries
  only, several pages unreachable); weather is forecast only; the ML and many spread rungs were last changed days ago (a thin
  surface is not a live price); Kalshi was not directly queried; no EPA; 1H/quarter numbers rest on a share of the full-game
  total (0.49 / 0.51 / 0.22 / 0.27 / 0.23 / 0.28) not on team splits; ties and overtime unpriced.
* Re-check before any placement: confirm Lowry and Broughton, refresh the `KXNCAAFSPREAD-26NOV10WMUAKR-WMU14` NO ask (still at or below $0.48) and size.
