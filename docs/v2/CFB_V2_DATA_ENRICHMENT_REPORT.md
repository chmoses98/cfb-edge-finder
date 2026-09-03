# CFB MODEL V2 — DATA ENRICHMENT REPORT

Research branch `claude/cfb-model-v2-research-krupoc`. Production
`0.5.0-early-season-talent-prior` untouched; V2 (`docs/v2/V2_SPEC.json`,
sha `d55b5ba0…`) frozen and used as the benchmark throughout. **Zero
metered CFBD calls were spent.** No 2026 outcome was used to fit, select,
tune, reject or promote anything (Week 0 was not used at all; Week 1
outcomes are embargoed).

## DATA ENRICHMENT VERDICT

**D — CURRENT V2 IS NEAR THE PRACTICAL FREE-DATA CEILING, with one modest
find (B) and a live-only category (C) now being captured.**

A serious search turned up two genuinely new free historical sources
(ESPN-derived play-by-play with passer identity for every game since 2014;
stats.ncaa.org box scores and rosters since 2013) and a free forecast
archive (Open-Meteo). Every football-information family built from them —
quarterback identity and experience, transfer-portal movement, coaching
transitions and lame-duck bowls, roster continuity, scheme, regime flags
— tests **flat** on V2's frozen rolling-origin folds. The one repeatable
gain is internal: feeding V2 its own within-season residuals recovers
about **0.04 points of margin MAE**, one-tenth of the 0.43-point gap to the
closing market. Stacking shows V2 adds **nothing** beyond the *opening*
line, and even a leaky hindsight version of "who actually started at QB"
is worth only ~0.05 points overall. What the market knows that V2 does
not is, on the evidence, not sitting in any free archive: it is
same-week availability, opt-out and motivation information that only
exists as it happens — which is exactly what the new live sidecar now
records.

**Primary recommendation (repeated at the end): PRODUCTIONIZE ORIGINAL V2.**

---

## 1. What information does V2 currently not know?

From the code-verified feature list (`V2_SPEC.json`): V2 knows opponent-
adjusted scoring/efficiency/drive/box state from strictly-prior games,
preseason talent, recruiting, returning production, head-coach change and
tenure, prior-season strength and SP+, preseason polls, FBS-newcomer
status, and situational context (neutral, conference game, rest, travel,
elevation, dome, kickoff hour). It does **not** know: who the quarterback
is or whether he changed; injuries, suspensions and opt-outs; transfer
portal traffic; coordinator changes; lame-duck or interim coaching
situations; weather; or anything about a team that is not encoded in its
own past box scores.

## 2. What does the market appear to know that V2 does not? (Phase 1)

Persisted out-of-sample V2 predictions vs the CFBD consensus closing
spread, 6,266 FBS-vs-FBS games, 2017–2025 (`scripts/v2_enrich/residual_forensics*.py`):

| \|V2 − close\| | games | V2 MAE | market MAE | V2 side wins | Wk1 share | postseason share | neutral share | non-conf share | coach-change share | FBS-newcomer share | mean \|talent diff\| |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0–3 | 3,349 | 12.27 | 12.24 | 50.4% | 5% | 4% | 7% | 29% | 39% | 0.8% | 124 |
| 3–5 | 1,484 | 12.30 | 11.97 | 49.6% | 6% | 5% | 7% | 30% | 43% | 2.0% | 139 |
| 5–7 | 830 | 13.26 | 12.24 | 48.2% | 7% | 7% | 10% | 37% | 43% | 2.5% | 142 |
| 7–10 | 420 | 13.72 | 11.97 | 47.4% | 9% | 7% | 10% | 40% | 51% | 4.3% | 164 |
| 10+ | 183 | 16.67 | 13.38 | 53.6% | 13% | 16% | 22% | 57% | 51% | 13.7% | 183 |

Large disagreements are concentrated in early season, bowls, neutral
sites, non-conference games, coaching transitions, FBS newcomers and
large talent mismatches. Where the market beats V2 most (share of the
total V2−market gap on all games): coaching-change games carry **51%** of
the gap on 42% of games; service-academy games **12%** of the gap on 4%
of games (V2's side of 7+ disagreements wins only **31%** there);
postseason **12%** on 5%; FBS-newcomer/young programs **9%** on 3%;
lame-duck bowls (next-season coach already hired before the bowl) have a
V2 MAE of 18.4 vs market 14.1 among 7+ disagreements.

Reading the 40 largest disagreements game by game (Army–Navy 2021,
FSU–Georgia 2023 with opt-outs, Washington State–Syracuse 2024 after a
coaching change and portal exodus, Texas Tech–FIU 2021, Colorado 2023,
New Mexico State repeatedly, Wake–ODU 2021 after ODU's cancelled 2020)
gives four hypotheses about what the market knew: (H1) roster reality
after heavy turnover; (H2) bowl availability — opt-outs, interim staffs,
motivation; (H3) programs whose recent box scores do not describe the
current team (newcomers, cancelled seasons, service academies' scheme);
(H4) same-week quarterback availability.

Two further facts shape everything below. The market's *own* residuals
are not persistent within a season (corr −0.006), but V2's are (0.06),
and a team's cumulative V2 residual correlates **0.28** with the current
V2−market gap: the market already prices a within-season team drift that
V2 under-weights. And when V2 disagrees with the **opening** line by 7+
points the close moves toward V2 **57–63%** of the time, in every
season (slope of close−open on V2−open positive in all 5 seasons), yet
V2's side against the close still wins only ~50%: V2 leads the opener but
the close absorbs it.

## 3–6. Sources found, and their timing classes

Full table with URLs, fields, seasons, limits, terms and difficulty:
`docs/v2/DATA_ENRICHMENT_INVENTORY.md`. Summary:

| Source | Class | Used |
|---|---|---|
| sportsdataverse `cfbfastR_cfb_pbp` (ESPN play-by-play, passer/rusher names, EPA) 2014–2025, free parquet | **A** (prior-game aggregates) | QB, scheme families |
| sportsdataverse `ncaa_mfb_*` (stats.ncaa.org player box scores, rosters with games_started, schedules) 2013–2025 | **A** (player_stats as prior-game aggregate) / **B** (rosters are end-of-season) | roster-continuity family (B) |
| Open-Meteo Archive (observed), Historical Forecast (2022+), Previous Runs day-ahead (2024+), live Forecast | **B** / **A−** / **A** / **C** | weather family (§11); live sidecar |
| CFBD `/player/portal` (`transferDate` event dates, revised ratings/destinations) | **B** (exits A, entries B) | portal family |
| CFBD `/coaches` hire dates | **A** | coaching family |
| ESPN core API: event odds, team injuries, athletes (runner-reachable; `site.api` blocked, depth charts unsupported) | **C** | live sidecar |
| NWS `api.weather.gov` | **C** | sidecar alternative |
| footballscoop / Wikipedia coordinator changes | B/D (editorial HTML, no structured feed) | documented, not integrated |
| 247/On3/Rivals pages, Kaggle box scores, sports-statistics.com | **E** (terms / provenance / blocked) | rejected |
| CFBD `/roster`, `/stats/player/season`, `/games/players` | metered — **0 budget** | not fetched |

## 7–12. Did each family help? (Phase 10 ablations)

Every family is appended to *both* V2-MARGIN members and re-run through
V2's own code on the identical 8 rolling-origin folds
(`scripts/v2_enrich/enrich_eval.py`); V2 itself is re-run from the same
code, so comparisons are like-for-like. Paired per-game deltas with a
2,000-resample game bootstrap. Negative = better than frozen V2.

| Family (class) | features | coverage | pooled Δ margin MAE [95% CI] | Week 1 Δ | Weeks 4+ Δ | seasons better / 8 | large-disagreement MAE 14.62 → | verdict |
|---|---|---|---|---|---|---|---|---|
| **QB identity/experience** (A): expected starter = prior game's primary passer; career starts/dropbacks/EPA per dropback (shrunk), starter-change-last-game, split-snaps flag | 12 | 88% | **+0.015** [−0.011, +0.040] | −0.009 | +0.019 | 3 | 14.51 | flat — REJECT |
| **Transfer portal** (B; 2021+): dated exits/entries, star-weighted, QB-specific, net | 7 | 52% (portal era) | +0.007 [−0.030, +0.046] | −0.079 [−0.221, +0.073] | +0.027 | 2 of 4 portal-era | 14.52 | flat — REJECT |
| **Coaching transitions** (A): lame-duck bowl, interim, late hire, postseason×lame-duck | 5 | 100% | +0.008 [−0.014, +0.029] | +0.009 | +0.018 | 3 | 14.66 | flat (postseason −0.10 [−0.35, +0.15]) — REJECT |
| **Regime flags** (A): FBS age ≤2, shortened 2020, service academy | 3 | 100% | +0.007 [−0.014, +0.030] | −0.024 | +0.005 | 4 | 14.52 | flat — REJECT |
| **Scheme** (A): prior-games pass rate (level, diff, abs-diff) | 4 | 88% | −0.000 [−0.012, +0.011]; total −0.000 | +0.007 | −0.001 | 4 | 14.58 | flat — REJECT |
| **Roster continuity** (B): S−1 starts of players on the S roster, QB starts, conference change | 4 | 88% | +0.005 [+0.002, +0.009] | +0.012 | +0.005 | 1 | 14.62 | degrades slightly — REJECT |
| **V2 self-correction** (A): team's prior-games V2 residual this season (shrunk mean, last game) | 2 | 100% | **−0.039 [−0.073, −0.007]** | −0.026 | −0.038 [−0.076, 0.000] | 7 (1 tie) | **14.31** | **modest, repeatable — ACCEPT as optional component** |
| self-correction + total self-correction | 3 | 100% | −0.033 [−0.068, 0.000]; total −0.013 [−0.028, +0.002] | −0.027 | −0.032 | 6 | 14.31 | margin-only version preferred |
| Weather / environment (B / A− / A) | 7 per product | fetch in progress | pending | | | | | see §11 |
| *Leaky upper bound* — the ACTUAL game's primary passer (hindsight, class D) | 7 | 88% | −0.053 [−0.101, −0.004] | **+0.237** | −0.075 | 5 | 14.34 | prices live QB info at ≈0.05–0.08 pts (bowls −0.24); never a feature |

Detailed per-family JSON (all folds, segments, large-disagreement
direction): `docs/v2/enrichment/enr_<family>.json`.

**QB (§7):** no. Within-season starter identity and experience built from
play-by-play add nothing V2's efficiency state does not already carry;
even knowing the actual starter (leaky) is worth ~0.05 points, and it
*hurts* Week 1 (+0.24) because prior-season experience is not what
decides openers. **Transfers (§8):** no repeatable signal in the four
portal-era folds; Week 1 hints (−0.08) are inside noise. **Roster
continuity (§9):** no (and slightly harmful). **Coaching (§10):** the
lame-duck/interim flags point the right way in bowls (−0.10) but are far
from significant on 333 postseason games. **Weather (§11):** see below.
**Unexpected (§12):** the only thing that helped was V2's own residual
history — a statement about the market's superior within-season tracking,
not about a missing dataset.

## 11. Weather / environment

Design (`scripts/v2_fetch_weather.py`, runner job `mode=weather`): three
Open-Meteo products, each a different timing class and each evaluated
separately on totals and margin — observed ERA5 archive (class **B**,
2014+, a proxy for what a forecast would have said), the Historical
Forecast API (archived short-lead forecasts, class **A−**, 2022+), and
the Previous Runs API day-ahead forecast (class **A**, 2024+). Features
per game: kickoff-hour wind and 3-hour max gust, 3-hour precipitation,
temperature, and cold / windy (≥15 mph) / rain flags, all zero for dome
venues.

**Status at report time: fetch still running on the GitHub runner (run
33701784573), no result yet.** The first attempt (season-long hourly
requests per venue) was throttled by Open-Meteo's call weighting and
never completed, and its concurrency group collided with the collector
(see the incident note in §19); the second attempt uses light per-game
requests with a wall-clock guard and pushes partial output (newest
seasons first) to the isolated `research-data-v2enrich` branch. When it
lands, `scripts/v2_enrich/README.md` documents the one-command ablation
(`build_weather.py` → `enr_wx_{archive,hforecast,prevrun}.json`) and this
section will be updated. Prior expectation, stated before seeing the
result: weather is a *totals* signal (wind/rain lower totals), unlikely to
move margin MAE, and a class-B observed proxy would overstate what a
pregame forecast could deliver. The live sidecar already captures the
class-C forecast every 6 hours so a prospective test needs nothing more.

## 13. Full ablation leaderboard (pooled margin MAE, 8 folds, 6,266 games)

| candidate | MAE | vs V2 |
|---|---|---|
| closing market (evaluation only) | 12.188 | −0.447 |
| **MARKET-INFORMED: V2 + close (ridge feature)** | 12.270 | −0.365 |
| **V2 + self-correction** | 12.596 | −0.039 |
| V2 + self-correction (+ total self-corr) | 12.601 | −0.033 |
| **frozen V2** (`ens_margin_d025_eq`) | 12.635 | — |
| V2 + scheme | 12.635 | 0.000 |
| V2 + opening spread (ridge feature; 2021+ only) | 12.634 | −0.001 (2021+ only, flat) |
| V2 + regime flags | 12.642 | +0.007 |
| V2 + portal | 12.642 | +0.007 |
| V2 + coaching | 12.643 | +0.008 |
| V2 + roster continuity (B) | 12.640 | +0.005 |
| V2 + QB | 12.649 | +0.015 |
| 0.5.0 (common 2021–25 games only) | 14.066 vs V2 12.514 | — |

## 14. Performance by chronological fold (margin MAE)

| season | 2017 | 2018 | 2019 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|
| V2 | 12.772 | 12.981 | 12.523 | 13.089 | 12.477 | 12.407 | 12.639 | 12.217 |
| V2 + self-correction | 12.772 | **12.879** | **12.475** | **13.050** | 12.474 | 12.390 | **12.537** | 12.214 |
| V2 + QB | 12.800 | 13.024 | 12.552 | 13.118 | 12.524 | 12.396 | 12.593 | 12.218 |
| V2 + portal | — | — | — | 13.089 | 12.561 | 12.402 | 12.612 | 12.223 |
| V2 + coaching | 12.845 | 12.986 | 12.544 | 13.060 | 12.463 | 12.394 | 12.641 | 12.238 |
| closing market | 12.18 | 12.62 | 12.16 | 12.54 | 12.02 | 12.12 | 12.07 | 11.82 |

## 15. Large-disagreement performance (|V2 − close| ≥ 7, n = 603)

| model | MAE | moved toward truth | moved toward market | model's side beats close |
|---|---|---|---|---|
| V2 | 14.62 | — | — | 49.3% |
| closing market | 12.40 | — | — | — |
| V2 + self-correction | **14.31** | 56.7% | 71.0% | 49.3% |
| V2 + QB | 14.51 | 56.2% | 61.4% | 49.3% |
| V2 + coaching | 14.66 | 49.9% | 49.4% | 49.3% |
| V2 + portal | 14.52 | 49.9% | 54.6% | 49.3% |
| leaky actual-QB upper bound | 14.34 | 57.4% | 66.2% | 49.3% |
| V2 + close (ridge) | 12.83 | 73.0% | 100% | 48.4% |

Nothing free changes which *side* of a large disagreement is right. The
self-correction family moves toward the market 71% of the time — it
explains part of *why* V2 was wrong (persistent drift), but not the part
the market gets right and V2 does not.

## 16. V2 vs enriched V2 vs market (common 2021–2025 games, n = 3,675)

| segment | n | 0.5.0 | V2 | V2+selfcorr | OPEN | CLOSE |
|---|---|---|---|---|---|---|
| all — margin MAE | 3,675 | 14.066 | 12.514 | 12.488 | 12.216 | **12.115** |
| all — RMSE | 3,675 | 17.810 | 15.761 | 15.731 | — | 15.297 |
| all — margin bias | 3,675 | −1.10 | −0.25 | −0.20 | −0.01 | −0.11 |
| all — total MAE | 3,675 | 13.246 | 12.784 | 12.814 | — | 12.499 |
| all — winner log loss | 3,675 | 0.6060 | 0.5357 | **0.5340** | — | 0.5392 |
| Week 1 — margin | 238 | 15.957 | 12.763 | 12.751 | 12.129 | 12.171 |
| Weeks 1–3 — margin | 724 | 15.407 | 12.713 | 12.738 | 12.262 | 12.212 |
| Weeks 4+ — margin | 2,951 | 13.736 | 12.465 | 12.427 | 12.205 | 12.091 |
| neutral | 118 | 13.782 | 11.781 | 11.773 | 12.439 | 12.311 |
| favourites ≥10 | 1,625 | 15.252 | 12.763 | 12.732 | 12.498 | 12.369 |
| \|V2−close\| ≥ 7 | 349 | 15.583 | 14.247 | 13.924 | 12.392 | 12.304 |
| coaching-transition games | 1,671 | 14.497 | 12.765 | 12.731 | 12.369 | 12.293 |
| lame-duck bowls | 24 | 11.581 | 10.246 | 9.859 | 9.698 | 9.344 |
| service academies | 150 | 14.112 | 13.970 | 13.830 | 13.213 | 12.755 |
| QB changed last game | 1,238 | 13.904 | 12.473 | 12.453 | 12.301 | 12.238 |

Paired on the common set: V2+selfcorr − V2 = −0.026 [−0.069, +0.015]
(flat); V2+selfcorr − close = +0.373 [+0.241, +0.489] (market still
better). Contract calibration with the frozen uncertainty model (7 test
seasons, 5,490 games, game-equal weighted): spread Brier 0.11727 → 0.11685,
ECE 0.0058 → 0.0063, 95%+ bin gap +0.002 → −0.004; total Brier 0.17498 →
0.17497. Winner: V2 log loss 0.5357 vs V2+selfcorr 0.5340 vs close 0.5392
on common games (V2 already beats the close on winner log loss while
losing on margin MAE).

## 17. Does enriched V2 add information beyond the opening market? (Phase 13, MARKET-INFORMED)

Chronological stacking on persisted out-of-sample predictions, 2022–2025
test seasons (fit on prior portal-era seasons), n = 3,167
(`scripts/v2_enrich/market_informed_stack.py`):

| model | MAE | coefficient on V2 / market |
|---|---|---|
| OPEN only | 12.126 | — |
| V2 only (refit) | 12.415 | — |
| **V2 + OPEN** | 12.122 | V2 0.20–0.27, open 0.73–0.80 |
| V2+selfcorr + OPEN | 12.126 | 0.20–0.26 |
| CLOSE only | 11.999 | — |
| V2 + CLOSE | 12.000 | V2 0.04–0.10 |

Paired: (V2+OPEN) − OPEN = **−0.004 [−0.037, +0.029]**; (V2+CLOSE) −
CLOSE = +0.001. V2 adds no measurable information beyond the opening
line, and enrichment does not change that. When V2+OPEN differs from the
opener by ≥2 points (n = 232) the close moves the same way 57% of the
time but the stacked side beats the close only 49.6%. **No alpha is
claimed.**

## 18. What live information should we begin capturing prospectively?

Implemented and validated: `scripts/live_info_sidecar.py` +
`.github/workflows/live-info-sidecar.yml` (research-only). For every
upcoming game inside 8 days it appends timestamped rows (fetched_at,
source URL, payload sha256, parsed facts, game id, kickoff, lead hours)
for: Open-Meteo hourly **forecast** at kickoff (wind, gusts, precip and
probability, temperature, humidity, cloud), ESPN core **posted odds** per
book (spread, total, moneylines), and ESPN core **injury lists** for both
teams (empty lists are recorded too). Rows go to the isolated
`research-sidecar` orphan branch; the workflow never touches main,
research-data, the collector or 0.5.0, uses its own concurrency group,
fails independently and spends zero CFBD calls.

Validation capture (run 33694005734, fetched 2026-09-02T23:12Z, commit
`4ec1d68` on `research-sidecar`): 91 upcoming games, lead 23–120 hours;
91 odds rows (ESPN currently exposes one book, DraftKings, per event);
182 injury rows (ESPN's CFB injury feed is sparse: 178 of 182 teams
returned an empty list, so this feed is a weak proxy for availability and
the athletes endpoint should be added); 91 forecast rows of which 60
parsed and 31 recorded a transport failure (`http_status` 0, timeout)
because the historical weather bulk fetch was hammering Open-Meteo from
the same runner at the same minute. Failures are recorded as rows, not
raised, and the next capture retries them; a single retry was added to
the fetch helper after this run. The second capture (00:01Z, commit
`40d95be`) appended another 91+91+182 rows with 84 of 91 forecasts
parsed. Capture cost: 0 CFBD calls, ~23 minutes of runner time (ESPN
latency). The 6-hourly cron only
fires once the workflow file exists on the default branch — enabling it
is a one-line follow-up PR, deliberately left for the productionization
mission so this research mission changes nothing on main.

Still worth adding later (free, live-only, C): ESPN core athletes per
team for depth-chart inference (status/position), NWS forecasts as a
second weather opinion, and Kalshi's own line history (already captured
by the collector). Not addable for free: dated injury/availability
archives and coordinator databases (see inventory, paid candidates).

## 19. CFBD quota usage and remaining September budget

This mission: **0 metered calls.** Runner-verified `/info` at 2026-09-02
22:59Z: **133 remaining of 1,000** (867 used). ≈225 calls were burned
between the V2 fetch (358 remaining at 05:17Z) and that reading by
consumers other than the collector's fast path (hourly conductor live
schedule fetch, settlement, weekly report, manual-dispatch diagnostic
steps). The heartbeat's `cfbd_quota_remaining` has read 999 since
2026-09-01 and is not a burn meter. 133 calls will not last September at
the observed rate; when they run out the collector serves the cached
football state for up to 6 hours and then fails closed. This is an
operational finding for the owner, outside this mission's scope.

### Incident: collector disruption on 2026-09-02 (self-reported)

The first historical weather fetch (run 33693555995) was written with the
same `research-data-write` concurrency group as the collector's Research
Capture workflow, copying the preseason fetch pattern. GitHub keeps one
pending run per group, so while the hour-long fetch held the group each
newly queued collector run cancelled the previous one: Research Capture
runs 1025–1036 (23:10Z–00:05Z on 2026-09-02/03, about one hour, all
preseason, no kickoffs inside the window) were cancelled. I noticed at
00:07Z, cancelled the weather run, and the queued collector run 1037
started at 00:07:54Z. The weather job now uses its own concurrency group
and its own orphan branch (`research-data-v2enrich`) so it can never
queue against the collector; the sidecar always had its own group and
branch. No captured observation was altered and no CFBD quota was spent
by the incident, but one hour of hourly snapshots is missing from the
ledger.

## 20. Paid data that would matter

Timestamped historical odds (opening→closing paths per book) for the
line-movement question; dated injury/availability/opt-out feeds (the one
information category the forensics keep pointing at); a coordinator
database; and a paid CFBD tier to remove the quota risk. Documented in the
inventory, none purchased.

## 21–23. Best final research specification; readiness; next-step architecture

`docs/v2/V2_ENRICHED_CANDIDATE_SPEC.json` freezes the self-correction
component (features, timing rule, model, folds, hashes) as an **optional
component** on top of the unchanged V2 spec. It is not promoted: its gain
is 0.04 points, flat on the common 2021–2025 set, and it does not move
calibration or the market comparison. The productionization mission
should implement **original V2** as specified in `V2_SPEC.json`, with
the self-correction feature left as a documented, switchable addition to
evaluate after one prospective season. Architecture note for that mission:
the self-correction feature needs the model's own prior live predictions
per team, which the observation ledger already stores.

## 24. Remaining risks

The market's remaining 0.4-point advantage appears to be information that
is generated in the days before a game and never archived for free, so
the ceiling here is a statement about free *history*, not about the
information itself; the sidecar starts closing that gap prospectively.
The self-correction result is small and could be noise despite 7/8
non-worse folds. Weather could only be tested with observed conditions
before 2022 (class B). All free sources are unofficial (undocumented ESPN
endpoints, community data releases) and can change without notice; the
inventory records each risk. The CFBD quota, not any model, is the
nearest operational threat.

---

## PRIMARY RECOMMENDATION: PRODUCTIONIZE ORIGINAL V2

V2 beats 0.5.0 by 1.4 points of margin MAE, 0.5 on totals and 0.07 in
winner log loss on every season, with calibrated contract tails, and
nothing found in the free-data frontier changes it enough to justify a
different specification. The enriched candidate is frozen and documented
for a later, prospectively-validated decision; the live sidecar is the
correct next investment in information, not another feature search over
the same archives.
