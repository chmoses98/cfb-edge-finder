# CONTROL × 2026 Kalshi game-winner market: results

**This is a retrospective market study.** The football side of every evaluated row is a replay at the production
cutoff, and only the prices were captured prospectively. The study contains **no** true prospective V2 row, because
no V2 `FINAL_PREGAME` row existed for a completed game at the freeze.

* **Research only.** Nothing here is a recommendation, a price threshold, or a probability.
* **Full tables:** `docs/CONTROL_2026_MARKET_TABLES.md`.
* **Machine-readable report:** `data/scripting/validation/control_market_2026/control_market_report.json`.
* **Per-game rows:** `control_market_rows.jsonl.gz`, beside the report.

| Item | Value |
|---|---|
| Base main | `be483103e9c6f895047f0a410d64906ba59c00ae` |
| Protocol commit | `767ed7bc8e35c911e6dd90d6e8b33829cedefc05`, protocol sha256 `61aa278d…23e1db` |
| Implementation + freeze commit | `4ab7e7830d11c482dde52131340e5fa31d63e54a`, manifest sha256 `bc74f8bf…4ab7` |
| Results | this commit (report generated once by `reveal` at code `4ab7e783`) |

## 1. Verdict

The pre-registered rule gives: **2026 CONTROL FOOTBALL SIGNAL HOLDS BUT MARKET IS APPROXIMATELY EFFICIENT.**

Read that with its basis. **Only one tier, HOME STRONG, met the n ≥ 20 evaluability floor**, and it is APPROXIMATELY
EFFICIENT. The other three tiers are INSUFFICIENT DATA. The overall label therefore rests on one tier, and the honest
summary is:

> The CONTROL football signal kept working in 2026. At the prices actually available 1–3 hours before kickoff,
> buying the CONTROL side did not produce positive fee-adjusted returns where there was enough data to judge
> (HOME STRONG). The other tiers are too thin to classify.

## 2. Coverage

| | n |
|---|---|
| Completed 2026 games replayed (kickoff 2026-08-29 → 2026-10-07, log weeks 1–6) | 391 |
| CONTROL games | **89** (no CONTROL claim: 302) |
| HOME MODERATE / HOME STRONG / AWAY MODERATE / AWAY STRONG | 4 / 61 / 3 / 21 |
| With a game-winner market resolved to the game | 83 (+1 market present but unorientable) |
| With a valid PRIMARY_60_180 entry quote (= primary economics n) | **45** |
| Missing entry quote | 44 |
| With settlement | 89 (all; game-log final scores; 8 also cross-checked against research-data settlements, 0 mismatches) |
| CLV available | 10 of 45 |
| Retrospective replay rows / prospective V2 rows | 89 / **0** |

**Why 44 CONTROL games have no primary entry:**

| Reason | Games |
|---|---|
| `ENTRY_QUOTE_UNAVAILABLE`: no capture in the 60–180 min window | 32 |
| `ENTRY_QUOTE_UNAVAILABLE`: captured in the window, but the CONTROL team's YES ask was **$1.00** (no offer below a dollar; all six were HOME STRONG, and all six won) | 6 |
| `NO_GAME_WINNER_MARKET` | 5 |
| `ORIENTATION_UNRESOLVED` | 1 |

* **No capture in the window (32).** Research capture stopped after 2026-09-16 (fail-closed). The catalog history
  from Sep 18 has a median gap of about 4.6 h, and kickoffs Aug 29 – Sep 2 and Sep 13 – 17 were never captured.
* **No game-winner market (5).** All five are FBS-vs-FCS games on Sep 11–12, none of which appears in any capture.
* **Orientation unresolved (1).** Charlotte @ App State: the Kalshi code `CHAR` vs ESPN `CLT`, and "Appalachian St."
  vs ESPN "App State". The pre-registered matcher could not orient it.

The bulleted reasons above are a post-reveal descriptive diagnostic. Every exclusion was decided by capture timing or
book state, never by result. Even so, **the priced subset is not a random half**:
* priced CONTROL games won 36/45 (80.0%);
* unpriced CONTROL games won 40/44 (90.9%).

Part of that gap is structural: the six $1.00-ask games are mega-favorites that could not be bought for less than the
payout. The rest is chance (no capture).

## 3. Football performance (all 89 CONTROL games; no market data needed)

| Tier | n | W–L | 2026 win | Wilson 95% | Dev 2021–25 | Val 2014–20 | 2026 median margin | Continuation |
|---|---|---|---|---|---|---|---|---|
| HOME MODERATE | 4 | 4–0 | 100% | [51.0, 100] | 79.95% | 79.5% | +23 | INSUFFICIENT |
| HOME STRONG | 61 | 54–7 | 88.5% | [78.2, 94.3] | 90.13% | 91.0% | +24 | CONSISTENT |
| AWAY MODERATE | 3 | 2–1 | 66.7% | [20.8, 93.8] | 66.58% | 67.9% | +1 | INSUFFICIENT |
| AWAY STRONG | 21 | 16–5 | 76.2% | [54.9, 89.4] | 80.95% | 84.6% | +11 | CONSISTENT |
| ALL CONTROL | 89 | 76–13 | 85.4% | [76.6, 91.3] | — | — | +21 | — |

The football relationship is continuing in 2026 wherever there is a sample to judge it.

## 4. Primary economics

Contract: the CONTROL team's own market, YES, at the last valid executable ask in [k−180, k−60] min. One contract;
taker fee at the entry price. ROI is fee-adjusted P/L ÷ (entry + fee).

| Tier | n | W | Win | Mean / median entry | Entry cost | Fees | Gross P/L | Fee-adj P/L | ROI [boot 95%] | Mean / median P/L | Break-even | Realized − BE |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| HOME MOD | 4 | 4 | 100% | .818 / .825 | 3.27 | 0.06 | +0.73 | **+0.67** | +20.1% [withheld, n<5] | +.168 / +.160 | 83.2% | +16.8 pp |
| HOME STRONG | 29 | 24 | 82.8% | .881 / .930 | 25.55 | 0.39 | −1.55 | **−1.94** | **−7.5% [−22.2, +4.7]** | −.067 / +.020 | 89.5% | −6.7 pp |
| AWAY MOD | 2 | 2 | 100% | .585 / .585 | 1.17 | 0.03 | +0.83 | **+0.80** | +66.7% [withheld] | +.400 / +.400 | 60.0% | +40.0 pp |
| AWAY STRONG | 10 | 6 | 60.0% | .666 / .655 | 6.66 | 0.17 | −0.66 | **−0.83** | −12.2% [−52.9, +23.5] | −.083 / +.045 | 68.3% | −8.3 pp |
| ALL MOD | 6 | 6 | 100% | .740 / .795 | 4.44 | 0.09 | +1.56 | +1.47 | +32.5% [+13.6, +72.4]* | +.245 / +.190 | 75.5% | +24.5 pp |
| ALL STRONG | 39 | 30 | 76.9% | .826 / .910 | 32.21 | 0.56 | −2.21 | −2.77 | −8.5% [−22.9, +4.1] | −.071 / +.020 | 84.0% | −7.1 pp |
| ALL CONTROL | 45 | 36 | 80.0% | .814 / .880 | 36.65 | 0.65 | −0.65 | **−1.30** | **−3.5% [−16.6, +8.6]** | −.029 / +.040 | 82.9% | −2.9 pp |

\* The ALL MOD interval is degenerate: every one of the 6 contracts won, so resampling only re-mixes prices and says
nothing about the chance of a loss. Six games are not evidence.

**Market scoring on identical rows** (descriptive; asks embed spread and fees):

| Group | Brier, entry ask | Brier, historical tier rate |
|---|---|---|
| ALL CONTROL | 0.123 | 0.167 |
| HOME STRONG | 0.110 | 0.148 |
| AWAY STRONG | 0.171 | 0.284 |

* The executable price was a better forecast of the CONTROL side winning than the frozen historical tier frequency.
* No paired model-vs-market score exists, because V2 CONTROL carries no probability by design.

## 5. Moderate vs strong; home vs away

* **Strength is priced.**
  * Prices: STRONG entries averaged 82.6¢ against MODERATE's 74.0¢. Within home, STRONG averaged 88.1¢ against 81.8¢;
    within away, 66.6¢ against 58.5¢.
  * Win rates: realized STRONG win rate in the priced subset was 76.9%. MODERATE was 6/6.
  * Economics: STRONG lost money (−8.5%) and MODERATE made money (+32.5%) on n = 6.
  * The MODERATE result is the kind of thing that must be frozen and re-tested. It is not evidence that the market
    under-rewards MODERATE.
* **Home costs more and wins more.**
  * HOME STRONG cost 88.1¢ and won 82.8% in the priced subset (88.5% overall).
  * AWAY STRONG cost 66.6¢ and won 60.0% in the priced subset (76.2% overall).
  * Both are below break-even (−6.7 pp and −8.3 pp). There is no sign the market overcharges home CONTROL relative
    to away, or underprices away CONTROL: both lose by a similar margin, and the AWAY sample (10) is tiny.
* **HOME STRONG is the one tier priced so high the signal cannot pay much.**
  * Its median entry is 93¢, so a single loss costs as much as roughly 16 wins earn.
  * In 2026 the 95–99¢ cell went 14–0 for +1.2% ROI, and the 50–84¢ cells went 5–5.

## 6. CLV and open-to-close movement

* **CLV.** Only 10 of 45 entries have a close in (k−60, k): 8 research T_60 → CLOSING, and 2 catalog snapshots.
  * **All 10 are exactly flat (CLV 0.000).**
  * This is a property of the books, not a bug. Across every research-captured game-winner market, the YES ask was
    unchanged between T_60 and T_30 in 167 of 182 cases, and between T_90 and T_60 in 168 of 182.
  * **CLV neither supports nor contradicts the economic result. It is uninformative here.**
* **Open → entry movement** of the CONTROL team's YES ask (EARLY_OPEN can be days earlier):
  * ALL CONTROL: mean −6.0¢, median −1¢; 44% of games moved toward CONTROL, 51% away.
  * HOME STRONG: mean −2.0¢, median +1¢; 59% toward.
  * AWAY STRONG: mean −15.8¢, median −11¢; 70% away (n = 10).
  * MODERATE: 6/6 moved away, mean −9.3¢.
* There is no systematic drift toward the CONTROL side before entry. If anything, away and moderate CONTROL sides got
  cheaper between open and entry. That is descriptive only, on small n. Some of the move also reflects results that
  arrived between the open and the football cutoff.

## 7. Timing (ALL CONTROL primary economics, pre-registered blocks)

| Block | n | Win | Mean entry | ROI [95%] |
|---|---|---|---|---|
| Weeks 1–2 | 8 | 87.5% | .806 | +6.4% [−21.5, +28.4] |
| Weeks 3–4 | 23 | 82.6% | .848 | −4.1% [−21.8, +12.3] |
| Weeks 5+ | 14 | 71.4% | .764 | −8.3% [−35.3, +13.3] |

Every interval spans zero, and the point estimates drift down. The sample cannot distinguish a stable null from a
fading early-season effect.

## 8. Market-efficiency classification (pre-registered rules)

| Tier | Verdict | Why |
|---|---|---|
| HOME MODERATE | INSUFFICIENT DATA | n = 4 (4–0, +20.1%); far below the n ≥ 20 floor |
| HOME STRONG | **APPROXIMATELY EFFICIENT** | ROI −7.5%; 95% [−22.2, +4.7] includes 0; win 82.8% vs break-even 89.5%; football signal intact (88.5% overall) |
| AWAY MODERATE | INSUFFICIENT DATA | n = 2 |
| AWAY STRONG | INSUFFICIENT DATA | n = 10; ROI −12.2% [−52.9, +23.5] |

No tier qualifies as EVIDENCE or POSSIBLE UNDERPRICING. No tier is OVERPRICED: no upper bound is below 0.

## 9. Descriptive price regions (DISCOVERY ONLY; requires a new frozen prospective test)

All cells are LOW_SAMPLE and EXPLORATORY.

* **STRONG CONTROL priced 50–84¢: 15 games, 7 wins**, about −35% ROI. These are games where the market disagreed
  with a strong efficiency edge, and the market was right more often than CONTROL.
* **STRONG CONTROL priced 90–99¢: 20 games, 20 wins**, +1.4% to +7.1% ROI. These are tiny per-contract margins: one
  loss at 97¢ (plus a 1¢ fee) erases about 49 wins.
* **MODERATE (any side), 6 games, 6 wins** at a mean of 74¢.

None of these is a price rule. Each would have to be frozen as a new hypothesis before any future outcome.

## 10. Sensitivities (descriptive; cannot change a verdict)

| | HOME STRONG ROI | ALL CONTROL ROI |
|---|---|---|
| Primary | −7.5% (n 29) | −3.5% (n 45) |
| FBS-vs-FBS only | −7.2% (n 20) | −2.1% (n 36) |
| Opponent-NO contract (its own NO ask) | −12.1% (n 21) | −5.5% (n 37) |
| Excluding the 2 incidentally seen games | −4.5% (n 28) | −1.4% (n 44) |
| ROI on entry price (repo convention) | −7.6% | −3.5% |

All replayed CONTROL games are replay-eligible, so that sensitivity equals the primary. There are no HIGH data-quality
rows. MEDIUM (n 12): −7.5%. LOW (n 33): −2.1%.

## 11. Deviations and disclosures

1. **Incidental outcome exposure before the freeze.** While debugging two orientation bugs, I printed the ESPN
   schedule entries of games 401856802 (West Virginia @ Virginia) and 401864574 (Charlotte @ App State), and those
   entries include final scores.
   * Both bugs were fixed uniformly and mechanically:
     * `-vs-` research slugs do not state which team is home, so they no longer supply team names (codes still do);
     * an unorientable market is labelled ORIENTATION_UNRESOLVED rather than NO_GAME_WINNER_MARKET.
   * The first fix brought one game (Virginia, a HOME STRONG **loss**) into the priced set. The fix lowered the
     tier's ROI.
   * The sensitivity excluding both games is reported above. No verdict changes.
2. **Implementation clarifications not spelled out in the protocol.**
   * Catalog snapshots were taken only from commits whose capture was complete. Every one was.
   * `vs` slugs give no names (above).
   * The descriptive mid used for Brier is the captured YES midpoint.
3. **Artifact location.** The protocol suggested `data/research/control_market_2026/`, but `data/research/` is
   ignored on main (it lives on the `research-data` branch). Artifacts are therefore in
   `data/scripting/validation/control_market_2026/`, beside the earlier archetype studies. The protocol JSON was
   moved there before its commit, and its hash is unchanged.
4. **Branch.** The work is on the session's designated branch `claude/keen-volta-ixkih1`, cut fresh from main at
   `be483103`, rather than `research/control-2026-market-edge`.

## 12. What this does not show

* It does not show that CONTROL has no market value. 45 priced games and 1 evaluable tier cannot rule out a
  moderate edge: HOME STRONG's interval still reaches +4.7%, and ALL CONTROL's reaches +8.6%.
* It does not test true prospective V2 CONTROL. There are no such rows yet.
* It says nothing about spreads, totals or any other market family.
