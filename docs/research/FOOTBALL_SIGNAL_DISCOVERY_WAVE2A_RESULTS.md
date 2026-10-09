# Football Signal Discovery Lab — Wave 2A (CFB): retrospective replay results

**Kind: RETROSPECTIVE.** This replays the frozen Wave-2 rules on earlier games. It is not prospective evidence, it is
never pooled with Wave 2, and it changes nothing in Wave 2: both streams stay `PROSPECTIVE_TRACKING`, with
NO SETTLED SAMPLE.

* **Protocol:** `FOOTBALL_SIGNAL_DISCOVERY_WAVE2A_PROTOCOL.md`, committed first at `5beabddd`.
* **Outputs:** `data/scripting/validation/signal_discovery_wave2a/`.
* **Code:**
  * `src/cfb_edge_finder/signal_discovery/wave2a/` (`replay.py`, `history.py`);
  * `scripts/signal_lab_wave2a_cfb.py` (`extract` → `replay` → `history`);
  * tests in `tests/test_signal_discovery_wave2a_cfb.py` (33).

## 1. Verdict

| Stream | 2026 replay (n settled) | Result | Replay verdict (pre-registered rule) |
|---|---|---|---|
| CFB-PROS-001 RUSHING_EDGE_SPREAD | 66 | ATS-like 33–33 (50.0 %); mean residual **+0.37** pts [−3.66, +4.46]; median −0.22; natural-rung ROI **−5.4 %** [−28.3 %, +17.8 %] | `SUPPORTIVE`, at the boundary: the rule's mean > 0 and ATS ≥ 50 % conditions are met, but by nothing. In plain terms the 2026 replay is **flat**. |
| CFB-PROS-002 STRONG_CONTROL_MARKET_SKEPTICISM | 5 | 2–3; mean residual −7.45; ROI −24.5 % | `INSUFFICIENT_REPLAY_DATA` |

* **History.** Over 2014–2025 the PROS-001 rule averaged **+1.40** pts against the sportsbook close
  [+0.81, +2.00], 52.8 % ATS, over 2,713 games, positive in 12 of 12 seasons. The 2026 replay's interval includes
  both 0 and +1.40: it can neither confirm nor reject that history.
* **STRONG CONTROL is rarely skeptical on Kalshi.** Of 45 priced 2026 STRONG CONTROL games, 40 had a CONTROL-side
  implied margin of at least +3 (median +21).

## 2. Integrity

* **Rules.** Thresholds, standardisation, checkpoint, center, rung, fee and settlement are the frozen `wave2`
  functions, imported, not copied (tested: `R.W is wave2`). The Wave-2 candidate hash is checked on every run.
* **Feature cutoff.** Football features come from the unmodified production builder at the 04:00 ET cutoff;
  `history_for` raises `LeakageError` if any history row is not strictly earlier.
  * The replay uses the current log vintage, so a later correction to a past box score could differ from what was
    known on the day.
  * The CONTROL replay matches all 89 frozen CONTROL-study rows exactly (0 disagreements).
* **No outcome in membership.** No score enters membership. A test doctors the target games' points and box score
  and shows the membership digest unchanged.
* **No post-kickoff quote.**
  * Every capture is strictly before kickoff, and a post-kickoff attempt raises.
  * Catalog snapshots count as in-window only if the whole build interval (captured_at ± elapsed seconds) is
    inside [K−180, K−60].
* **Bids in research-corpus rows.** The center uses `1 − NO ask` as the YES bid. That book identity held on all
  16,106 catalog spread markets checked. Entry prices are always the bought side's own captured ask.
* **Separation from Wave 2.** Nothing is written under `research-signals:wave2/` (tested). No replay row is in any
  prospective n.
* **Freeze timing.** Every settled replay game kicked off before the Wave-2 freeze (2026-10-08T22:26:33Z). The 4
  completed games between the freeze and activation all fell below the threshold.

## 3. Coverage (every denominator)

| Step | PROS-001 | PROS-002 |
|---|---|---|
| Completed 2026 games before activation (pinned log `8173fd5e`) | 397 | 397 |
| Feature / CONTROL reconstructable | 251 (146 `FEATURE_HISTORY_UNAVAILABLE`: no prior game for a team, mostly week 1 and FCS) | 82 STRONG CONTROL (`RETROSPECTIVE_REPLAY_AT_PRODUCTION_CUTOFF`; no V2 `FINAL_PREGAME` row exists before Oct 8) |
| Selected by the frozen rule | **97** (\|z\| ≥ 1; 154 below) | 82 |
| Kalshi event matched (frozen winner matcher) | 92 (5 `NO_MARKET`) | 76 (6 `NO_MARKET`) |
| Spread ladder captured inside PRIMARY_60_180 | 66 (26 `NO_PRIMARY_WINDOW_CAPTURE`) | 45 (31) |
| Oriented / center computed | 66 / 66 | 45 / 45 |
| Frozen eligibility | 66 | **5** (40 `NOT_ELIGIBLE`: implied margin ≥ +3) |
| Executable natural-rung contract, fee known | 66 | 5 |
| Settled (ESPN final score) | **66** | **5** |

* **Capture sources of the 66 PROS-001 entries:**
  * 21 from the research corpus (`research-data` @ `292f3aac`, kickoffs Aug 29 – Sep 13, entries 74–134 minutes
    before kickoff);
  * 45 from catalog snapshots (`main`, 91 snapshots Sep 18 – Oct 9, entries 78–178 minutes before kickoff).
* **Where coverage is missing.** Research capture stopped after Sep 16 and the catalog began on Sep 18. Most
  `NO_PRIMARY_WINDOW_CAPTURE` rows are catalog-era games with no snapshot inside the 2-hour window; the median
  snapshot gap is 4.6 h.

## 4. CFB-PROS-001: historical characterisation (`RETROSPECTIVE_DISCOVERY_CORPUS`, `SPORTSBOOK_CLOSE_CHARACTERIZATION`)

This **reproduces** Wave 1 exactly: n 2,713, 1,408–1,260–45, mean residual 1.4003870 and median 1.0, identical to
`evaluation_report.json` (`reproduction_of_wave1_follow_rule.exact = true`). It is characterisation, not validation.

| | Value |
|---|---|
| Eligible / with line and score | 2,718 / 2,713 |
| Side | home 1,340 (mean residual +0.89, 49.9 % ATS), away 1,373 (+1.90, 55.6 %) |
| Favourite / underdog at the close | favourite 2,070 (+1.36, 52.0 %, wins 81.6 %), underdog 640 (+1.52, 55.1 %, wins 37.0 %) |
| Actual side margin | mean +11.7, median +10.0; outright 71.1 % |
| Residual vs close | mean **+1.40** [+0.81, +2.00]; median +1.0; p10/25/75/90 = −18 / −9 / +12 / +22; positive 51.9 % |
| ATS-like | 1,408–1,260–45, 52.8 % [50.9, 54.7]; assumed −110 ROI +0.7 % (no executable price) |
| By season (mean residual) | 2014 +1.37 · 2015 +2.74 · 2016 +1.38 · 2017 +0.63 · 2018 +1.13 · 2019 +1.44 · 2020 +1.52 · 2021 +1.77 · 2022 +0.28 · 2023 +1.06 · 2024 +1.77 · 2025 +1.71 (**12/12 positive**) |
| Concentration | top team 2.1 % of rows |

## 5. CFB-PROS-001: 2026 replay (`RETROSPECTIVE_2026_REPLAY`)

**Answers to protocol §8:**

1. **Qualified:** 97 of 251 feature-eligible games (38.6 %).
   * The 2026 feature is shifted from Wave 1: mean +0.54 and SD 1.77, against Wave-1 +0.14 and 1.42. That is the
     declared ESPN-log-versus-CFBD source difference.
   * It is reported, never re-standardised.
2. **Valid PRIMARY_60_180 ladders:** 66.
3. **Mean margin residual:** +0.37 pts, game bootstrap 95 % [−3.66, +4.46], SD 16.8.
4. **Median residual:** −0.22.
5. **Positive residual rate:** 50.0 % [38.3, 61.7].
6. **ATS-like against the market center:** 33–33–0.
7. **Fee-adjusted ROI** (natural rung, captured side ask + July-2026 taker fee, $0.02 on every entry):
   * 66 contracts, 33 paid, mean price $0.508;
   * P/L −$1.87, ROI **−5.4 %** [−28.3 %, +17.8 %].
8. **Similar to history?** The point estimate is lower (+0.37 against +1.40), but the 2026 interval contains the
   historical mean and zero, so 66 games cannot tell them apart.
9. **Concentration:**
   * By week: wk1 1 game (−8.3); wk2 +2.01 (11–9); wk3 −3.65 (7–13); wk4 −4.82 (5–6); wk5 +8.47 (10–4).
   * Leave-one-week-out ranges from −1.81 (without wk5) to +2.12 (without wk3).
   * Top-5 sides removed: −2.54, 27–33. Leave-one-team-out ranges from −0.25 to +0.93.
   * Top-5 conferences removed: −3.22. Largest team share 3 %.
10. **Uncertainty.** It is very large: with residual SD ≈ 17, separating +1.4 from 0 needs about 600 games. The
    prospective primary review at 400 is the right instrument.

**Other splits (descriptive, never a rule):**

* Underdog sides +5.87 (11–6), favourite sides −1.54 (22–27).
* Away +1.33 (13–10), home −0.14 (20–23).
* By capture source: research corpus +1.52 (11–10), catalog −0.17 (22–23).

**CLV.** A close (a capture 0–60 minutes before kickoff) exists for 25 of 66 games and is missing for 41, which
stay missing.

* The mean center move toward the side was +0.02 pts; it moved toward the side in 28 % of games.
* Mean contract CLV +$0.002 (n 25).

## 6. CFB-PROS-002: historical characterisation (`SPORTSBOOK_CLOSE_CHARACTERIZATION`)

* **Rule.** STRONG CONTROL (tier identical to the frozen V2 rows) with a CONTROL-side close-implied margin
  strictly below +3.
* **Base rate.** 2,373 STRONG games had a line and **70** were eligible. That is the Wave-1 discovery cell
  exactly, so it is not independent.

| | Value |
|---|---|
| Outright | 55.7 % |
| ATS-like | 42–27–1, **60.9 %** [49.1, 71.5] |
| Mean residual | **+5.25** [+1.18, +9.12]; median +3.25 |
| Side | away 43 (+6.53, 64.3 %), home 27 (+3.20, 55.6 %) |
| CONTROL side favourite / underdog | favourite 27 (+4.10, 55.6 %), underdog 42 (+6.07, 63.4 %) |
| By season (n, mean) | 2014 9 +13.2 · 2015 8 −1.5 · 2016 8 +6.9 · 2017 2 +32.3 · 2018 3 +11.6 · 2019 6 +1.3 · 2020 2 +6.5 · 2021 7 +0.8 · 2022 2 +11.4 · 2023 5 +0.9 · 2024 12 +0.3 · 2025 6 +8.3 |
| Neutral site | 5 games, −2.15 |
| All STRONG CONTROL (context) | 2,373 games, ATS 50.2 %, mean +0.38 |

## 7. CFB-PROS-002: 2026 replay

* **Candidates.** 82 STRONG CONTROL games replayed at the production cutoff; 76 had a Kalshi event.
* **Priced.** 45 had a PRIMARY_60_180 center, and only **5** of those had an implied margin below +3:
  +2.89, +2.78, −1.67, +1.12, −6.86.
* **Results:**
  * Outright 2–3.
  * ATS-like 2–3.
  * Mean residual −7.45, median −10.78.
  * Natural-rung P/L −$0.65 on 5 contracts (ROI −24.5 %).
* **Verdict.** `INSUFFICIENT_REPLAY_DATA`. Five games say nothing.

## 8. Game-level table (every settled replay game)

Market center = the frozen PRIMARY_60_180 Kalshi ladder center from the side's perspective. Contract = the natural
rung, bought at the side's own captured ask plus the taker fee. Source: RES = research corpus, CAT = catalog
snapshot, with minutes before kickoff.

**CFB-PROS-001 (66)**

| Date | Game | Side | z | Center | Margin | Residual | Contract → P/L | Source |
|---|---|---|---|---|---|---|---|---|
| 09-04 | San José State @ Eastern Michigan | home EMU | +1.32 | +2.33 | −6 | −8.33 | YES EMU3 @0.50+0.02 → −0.52 | RES 74 |
| 09-11 | Rutgers @ Boston College | home BC | +1.68 | +2.89 | +7 | +4.11 | YES BC3 @0.54 → +0.44 | RES 74 |
| 09-12 | Arizona State @ Texas A&M | home TXAM | +1.05 | +14.75 | +28 | +13.25 | YES TXAM15 @0.51 → +0.47 | RES 74 |
| 09-12 | Penn State @ Temple | away PSU | −1.58 | +24.12 | +18 | −6.12 | YES PSU25 @0.49 → −0.51 | RES 74 |
| 09-12 | App State @ East Carolina | away APP | −1.59 | −6.57 | +3 | +9.57 | NO ECU7 @0.50 → +0.48 | RES 74 |
| 09-12 | Western Kentucky @ Georgia | home UGA | +1.52 | +40.10 | +50 | +9.90 | YES UGA40 @0.52 → +0.46 | RES 74 |
| 09-12 | Alabama @ Kentucky | away ALA | −2.03 | +8.75 | +28 | +19.25 | YES ALA10 @0.49 → +0.49 | RES 74 |
| 09-12 | California @ Syracuse | home SYR | +2.15 | +3.88 | −3 | −6.88 | YES SYR4 @0.52 → −0.54 | RES 74 |
| 09-12 | Duke @ Illinois | away DUKE | −1.13 | −6.12 | +4 | +10.12 | NO ILL7 @0.52 → +0.46 | RES 74 |
| 09-12 | Utah State @ Washington | home WASH | +1.16 | +28.44 | +2 | −26.44 | YES WASH28 @0.53 → −0.55 | RES 74 |
| 09-12 | Rice @ Notre Dame | home ND | +1.56 | +44.80 | +52 | +7.20 | YES ND45 @0.52 → +0.46 | RES 74 |
| 09-12 | UNLV @ North Texas | home UNT | +1.49 | −2.95 | +38 | +40.95 | NO UNLV3 @0.46 → +0.52 | RES 74 |
| 09-12 | Delaware @ Vanderbilt | away DEL | −1.24 | −20.86 | −9 | +11.86 | NO VAN21 @0.48 → +0.50 | RES 74 |
| 09-12 | South Alabama @ Tulane | home TULN | +1.06 | +9.71 | +4 | −5.71 | YES TULN10 @0.52 → −0.54 | RES 74 |
| 09-12 | Middle Tennessee @ Marshall | home MRSH | +1.45 | +12.83 | +2 | −10.83 | YES MRSH14 @0.49 → −0.51 | RES 74 |
| 09-12 | San Diego State @ UCLA | home UCLA | +1.46 | +13.25 | +18 | +4.75 | YES UCLA14 @0.50 → +0.48 | RES 74 |
| 09-12 | Navy @ Florida Atlantic | away NAVY | −1.11 | +4.12 | −8 | −12.12 | YES NAVY5 @0.49 → −0.51 | RES 74 |
| 09-12 | Charlotte @ Ole Miss | home MISS | +1.29 | +44.67 | +32 | −12.67 | YES MISS46 @0.48 → −0.50 | RES 74 |
| 09-13 | Southern Miss @ Auburn | away USM | −1.13 | −32.67 | −35 | −2.33 | NO AUB34 @0.53 → −0.55 | RES 79 |
| 09-13 | Georgia Southern @ Clemson | away GASO | −1.16 | −19.43 | −15 | +4.43 | NO CLEM21 @0.53 → +0.45 | RES 134 |
| 09-13 | Louisiana @ USC | home USC | +1.61 | +31.00 | +19 | −12.00 | YES USC31 @0.52 → −0.54 | RES 74 |
| 09-19 | Portland State @ Oregon | home ORE | +2.37 | +58.93 | +84 | +25.07 | YES ORE59 @0.52 → +0.46 | CAT 124 |
| 09-19 | Maine @ Boston College | home BC | +3.35 | +37.00 | +6 | −31.00 | YES BC36 @0.54 → −0.56 | CAT 84 |
| 09-19 | Temple @ Toledo | home TOL | +1.24 | +5.33 | +1 | −4.33 | YES TOL6 @0.50 → −0.52 | CAT 144 |
| 09-19 | Miami (OH) @ Cincinnati | away MOH | −1.48 | −14.75 | −4 | +10.75 | NO CIN15 @0.50 → +0.48 | CAT 174 |
| 09-19 | Wagner @ California | home CAL | +2.43 | +57.50 | +42 | −15.50 | YES CAL58 @0.51 → −0.53 | CAT 174 |
| 09-19 | USC @ Rutgers | away USC | −1.39 | +21.42 | +7 | −14.42 | YES USC22 @0.50 → −0.52 | CAT 174 |
| 09-19 | Duquesne @ Washington State | home WSU | +2.78 | +37.56 | +41 | +3.44 | YES WSU39 @0.48 → +0.50 | CAT 174 |
| 09-19 | SE Louisiana @ UL Monroe | home ULM | +1.32 | +9.88 | −3 | −12.88 | YES ULM10 @0.52 → −0.54 | CAT 88 |
| 09-19 | Florida State @ Alabama | home ALA | +1.86 | +18.75 | +14 | −4.75 | YES ALA18 @0.53 → −0.55 | CAT 133 |
| 09-19 | East Carolina @ Old Dominion | home ODU | +1.15 | +2.69 | −3 | −5.69 | YES ODU3 @0.52 → −0.54 | CAT 178 |
| 09-19 | FIU @ Florida Atlantic | away FIU | −1.39 | −6.33 | −6 | +0.33 | NO FAU7 @0.51 → +0.47 | CAT 178 |
| 09-19 | Troy @ Missouri | home MIZZ | +1.42 | +26.86 | +10 | −16.86 | YES MIZZ28 @0.49 → −0.51 | CAT 88 |
| 09-19 | Murray State @ Oklahoma State | home OKST | +3.46 | +55.93 | +59 | +3.07 | YES OKST55 @0.56 → +0.42 | CAT 88 |
| 09-19 | UT Martin @ Memphis | home MEM | +2.47 | +33.33 | +24 | −9.33 | YES MEM34 @0.50 → −0.52 | CAT 88 |
| 09-19 | Georgia Southern @ Jacksonville State | away GASO | −1.08 | −3.22 | −4 | −0.78 | NO JVST4 @0.53 → −0.55 | CAT 88 |
| 09-19 | Nicholls @ Sam Houston | home SHSU | +3.51 | +21.00 | +59 | +38.00 | YES SHSU21 @0.53 → +0.45 | CAT 88 |
| 09-19 | LSU @ Ole Miss | away LSU | −2.03 | +2.78 | −8 | −10.78 | YES LSU3 @0.53 → −0.55 | CAT 118 |
| 09-19 | Colorado @ Northwestern | away COLO | −1.18 | −3.35 | −34 | −30.65 | NO NW4 @0.52 → −0.54 | CAT 118 |
| 09-19 | Michigan State @ Notre Dame | home ND | +1.70 | +29.07 | +17 | −12.07 | YES ND30 @0.49 → −0.51 | CAT 118 |
| 09-20 | James Madison @ San Diego State | away JMU | −1.32 | −2.33 | +13 | +15.33 | NO SDSU3 @0.51 → +0.47 | CAT 135 |
| 09-25 | Howard @ Rutgers | home RUTG | +2.44 | +41.60 | +51 | +9.40 | YES RUTG42 @0.51 → +0.47 | CAT 78 |
| 09-26 | Northwestern @ Indiana | home IND | +1.77 | +20.31 | +6 | −14.31 | YES IND21 @0.50 → −0.52 | CAT 138 |
| 09-26 | Iowa @ Michigan | away IOWA | −1.04 | −5.25 | +1 | +6.25 | NO MICH6 @0.51 → +0.47 | CAT 100 |
| 09-26 | Boise State @ Western Michigan | away BSU | −1.29 | +7.29 | +25 | +17.71 | YES BSU8 @0.49 → +0.49 | CAT 100 |
| 09-26 | Robert Morris @ Buffalo | home BUFF | +2.97 | +28.79 | +3 | −25.79 | YES BUFF29 @0.51 → −0.53 | CAT 100 |
| 09-26 | Stonehill @ Ohio | home OHIO | +1.98 | +32.79 | +28 | −4.79 | YES OHIO34 @0.48 → −0.50 | CAT 100 |
| 09-26 | Gardner-Webb @ Marshall | home MRSH | +1.69 | +24.20 | +1 | −23.20 | YES MRSH25 @0.49 → −0.51 | CAT 100 |
| 09-26 | LIU @ FIU | home FIU | +3.89 | +36.74 | +17 | −19.74 | YES FIU36 @0.54 → −0.56 | CAT 101 |
| 09-26 | Central Michigan @ Miami | home MIA | +1.37 | +41.31 | +49 | +7.69 | YES MIA42 @0.50 → +0.48 | CAT 131 |
| 09-26 | Kansas State @ Cincinnati | away KSU | −1.26 | +5.88 | −5 | −10.88 | YES KSU6 @0.52 → −0.54 | CAT 161 |
| 09-26 | Mercyhurst @ Western Kentucky | home WKU | +2.27 | +40.43 | +45 | +4.57 | YES WKU42 @0.48 → +0.50 | CAT 161 |
| 10-03 | Ohio State @ Iowa | home IOWA | +1.30 | −14.42 | −17 | −2.58 | NO OSU15 @0.51 → −0.53 | CAT 87 |
| 10-03 | Wyoming @ North Dakota State | home NDSU | +1.10 | +17.00 | +28 | +11.00 | YES NDSU17 @0.54 → +0.44 | CAT 87 |
| 10-03 | Akron @ Central Michigan | away AKR | −1.02 | −6.86 | −24 | −17.14 | NO CMU7 @0.48 → −0.50 | CAT 87 |
| 10-03 | Bowling Green @ Miami (OH) | home MOH | +1.24 | +10.67 | −4 | −14.67 | YES MOH11 @0.51 → −0.53 | CAT 87 |
| 10-03 | Old Dominion @ Georgia State | away ODU | −1.02 | −2.57 | −32 | −29.43 | NO GAST3 @0.50 → −0.52 | CAT 87 |
| 10-03 | Marshall @ James Madison | home JMU | +1.85 | +15.50 | +28 | +12.50 | YES JMU15 @0.53 → +0.45 | CAT 102 |
| 10-03 | Florida @ Missouri | home MIZZ | +1.12 | −5.88 | +28 | +33.88 | NO FLA6 @0.49 → +0.49 | CAT 107 |
| 10-03 | UTEP @ New Mexico | home UNM | +1.40 | +23.33 | +54 | +30.67 | YES UNM24 @0.50 → +0.48 | CAT 117 |
| 10-03 | Arkansas @ Texas A&M | home TXAM | +1.19 | +13.80 | +27 | +13.20 | YES TXAM14 @0.52 → +0.46 | CAT 88 |
| 10-03 | Miami @ Clemson | away MIA | −1.34 | +15.25 | +28 | +12.75 | YES MIA15 @0.52 → +0.46 | CAT 118 |
| 10-03 | Army @ Louisiana Tech | home LT | +1.63 | +0.25 | +2 | +1.75 | YES LT2 @0.48 → +0.50 | CAT 118 |
| 10-04 | Indiana @ Rutgers | away IND | −2.23 | +24.38 | +32 | +7.62 | YES IND25 @0.50 → +0.48 | CAT 148 |
| 10-04 | Baylor @ Arizona State | away BAY | −1.04 | −3.19 | +36 | +39.19 | NO ASU4 @0.53 → +0.45 | CAT 137 |
| 10-04 | Cincinnati @ Arizona | home ARIZ | +1.80 | +7.08 | +27 | +19.92 | YES ARIZ8 @0.48 → +0.50 | CAT 167 |

Every fee is $0.02. All 66 rows are `SETTLED`.

**CFB-PROS-002 (5)**

| Date | Game | Side | CONTROL | Center | Margin | Residual | Contract → P/L | Source |
|---|---|---|---|---|---|---|---|---|
| 09-11 | Rutgers @ Boston College | home BC | HOME_STRONG | +2.89 | +7 | +4.11 | YES BC3 @0.54 → +0.44 | RES 74 |
| 09-19 | LSU @ Ole Miss | away LSU | AWAY_STRONG | +2.78 | −8 | −10.78 | YES LSU3 @0.53 → −0.55 | CAT 118 |
| 09-26 | Oklahoma State @ West Virginia | away OKST | AWAY_STRONG | −1.67 | +17 | +18.67 | NO WVU2 @0.50 → +0.48 | CAT 161 |
| 10-03 | Virginia @ Florida State | away UVA | AWAY_STRONG | +1.12 | −31 | −32.12 | YES UVA2 @0.50 → −0.52 | CAT 87 |
| 10-03 | Akron @ Central Michigan | away AKR | AWAY_STRONG | −6.86 | −24 | −17.14 | NO CMU7 @0.48 → −0.50 | CAT 87 |

## 9. Reproducibility

`python scripts/signal_lab_wave2a_cfb.py extract && … replay && … history` regenerates every output byte for byte
(tested).

| Item | Value |
|---|---|
| Inputs | main `8173fd5e`, research-data `292f3aac`, script-ledger `8be862ef` |
| Hashes | football log `e37fe5ca…`, schedule `6b73c7a1…` |
| Wave-2 candidates | `bf19c972…` |
| Wave-2 protocol sha256 | `79f748db…` |
| Wave-2A protocol sha256 | `e204e2f7…` |
| Bootstrap | seed 20261010, 4,000 draws |
| Recorded per run | `extract_manifest.json` and the report's `inputs` / `membership_digest` |
