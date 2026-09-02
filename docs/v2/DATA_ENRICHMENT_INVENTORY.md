# V2 Data-Enrichment Source Inventory

Mission: find free, legal, maintainable pregame information that closes the
V2-vs-closing-market gap. Every source below is classified for
**historical timing safety**:

| Class | Meaning |
|---|---|
| **A** | historically timing-safe: what was known before each historical game can be reconstructed |
| **B** | historically approximable: data exists but as-of semantics are imperfect; exploratory only, caveated |
| **C** | current/future timing-safe only: capture prospectively from now; no honest historical claim |
| **D** | retrospective / leaky: only final/revised information exists |
| **E** | unavailable / unreliable: rejected |

Access notes: the dev container's egress proxy blocks most third-party
hosts (Open-Meteo, ESPN, footballscoop, sports-statistics, api.github.com);
`raw.githubusercontent.com` and GitHub release-asset downloads work from
the container, and everything below marked "runner" was probed from a
GitHub-hosted runner by `.github/workflows/v2-enrichment-research.yml`
(`scripts/v2_enrichment_probe.py`, read-only, zero metered CFBD calls).

## CFBD quota — audited, not assumed

| Reading | Value |
|---|---|
| `/info` (unmetered) on the runner, 2026-09-02 22:59Z | **133 remaining / 1,000**, 867 used, resets 2026-10-01 |
| V2 research fetch (2026-09-02 05:17Z) | 768 → 358 remaining (422 calls) |
| Unexplained burn 05:17Z → 22:59Z | **≈225 calls** — not the collector's schedule refreshes (heartbeat `cfbd_requests` sums to 14 on 09-01 and 26 on 09-02) |
| Collector heartbeat `cfbd_quota_remaining` | **stale at 999** since the 09-01 00:22Z probe; the durable `cfbd_access/state.json` is only refreshed by an `/info` probe when the gate engages, so heartbeats do NOT measure real burn |

Consumers beyond the collector's fast path that spend metered calls every
day: the hourly conductor's live `/games` schedule fetch
(`fetch_schedule_health`), the settlement workflow, the weekly report, and
the read-only diagnostic steps (`validate_collection_schedule.py`,
`week1_readiness.py --live`) that run on every *manual* Research Capture
dispatch. At the observed rate the remaining 133 calls do not cover
September; once exhausted the collector's quota gate serves the cached
`football_state` until its 6-hour schedule bound, then fails closed.

**Research budget for this mission: 0 metered calls.** Everything below
was obtained from the existing V2 cache, from free keyless APIs, or from
public GitHub releases. Operational recommendation (outside this
mission's scope): stop the conductor/diagnostic live fetches or move the
account to a paid CFBD tier before Week 2.

## Sources

### Already in the repo (V2 cache, `data/research_cache/v2/` on research-data)

| Source | Fields | Seasons | Timing class | Notes |
|---|---|---|---|---|
| CFBD `/games`, `/stats/game/advanced`, `/drives`, `/games/teams` | scores, efficiency, drives, box | 2014–2025 | A (strictly-prior games) | V2 state |
| CFBD `/talent`, `/recruiting/teams`, `/player/returning`, `/coaches`, `/rankings` wk1 | preseason | 2014–2026 | A | V2 preseason |
| CFBD `/lines` | closing + opening spread/total, moneyline, per book | 2014–2025 (opener 2021+) | pregame but MARKET | evaluation + market-informed experiment only |
| CFBD `/player/portal` | name, position, origin, destination, stars, rating, `transferDate` | 2021–2026 | **B** | `transferDate` is a portal-entry event date (A for exits); `destination` and `rating` are revised as players commit (B for entries); rating null 35–82%, stars null 12% |
| CFBD `/coaches` `hireDate` | head coach, hire date | 2014–2026 | **A** | enables lame-duck-bowl / interim / late-hire flags |
| CFBD `/venues` | lat/lon, dome, elevation, timezone | static | A | weather join key |
| CFBD `/games` pregame Elo | external rating | 2014–2026 | A | benchmark only |

### Discovered free sources

| Source | URL / access | Fields | Seasons | Update | As-of semantics | Cost / auth | Limits / terms | Reliability | Expected value | Class | Difficulty | Recommendation |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **sportsdataverse `cfbfastR_cfb_pbp`** (ESPN-derived play-by-play) | `github.com/sportsdataverse/sportsdataverse-data/releases/download/cfbfastR_cfb_pbp/play_by_play_{Y}.parquet` (reachable from container) | every play: passer/rusher/receiver names, EPA, success, play type, pos_team, home/away, pregame Elo | 2014–2025 (2002+ via legacy repo) | nightly in-season | play events are POSTGAME for their own game; aggregating strictly-prior games is timing-safe | free, none | GitHub release limits; data licence not stated in repo (MIT-style open data project); attribution required | high; 100% of V2 games matched by ESPN game id | **QB identity/experience/EPA by game, scheme (pass rate), tempo** | **A** (prior-game aggregates) | low (12 × ~100 MB parquet) | **USED** — QB and scheme families |
| **sportsdataverse `ncaa_mfb_*`** (stats.ncaa.org box scores) | same host, tags `ncaa_mfb_player_stats`, `ncaa_mfb_rosters`, `ncaa_mfb_schedule`, `ncaa_mfb_pbp`, `ncaa_mfb_drives`, `ncaa_mfb_team_stats`, `ncaa_mfb_linescore`, `ncaa_mfb_officials` | per-game player passing/rushing/receiving lines with `espn_game_id`; season rosters with `games_played`/`games_started`, class, hometown | 2013–2025 | 2026-08-24 release | player_stats: POSTGAME per game (A as prior-game aggregate); rosters: end-of-season snapshot (**D** within season; usable as S−1 experience for season S = A) | free, none | ESPN-id coverage ≈88% of games | good | cross-check for QB; returning-starter counts (S−1 `games_started` of players on S roster) | A / B | low | acquired 2013–2025 (2 MB); QB cross-check; returning-starters not tested this pass |
| **Open-Meteo Archive API** (ERA5 reanalysis) | `archive-api.open-meteo.com/v1/archive` (runner) | hourly temp, humidity, precip, rain, snow, wind speed/gusts/direction, cloud | 1940–present | daily | OBSERVED conditions — what happened, not what was forecast | free, keyless, CC-BY 4.0 | 10k calls/day, 5k/h, 600/min | high | totals (wind/precip) | **B** (observed proxy) | low | **FETCHED** via `scripts/v2_fetch_weather.py` |
| **Open-Meteo Historical Forecast API** | `historical-forecast-api.open-meteo.com/v1/forecast` (runner) | same variables, archived short-lead forecasts | ~2022–present | continuous | forecast issued 0–6 h before valid time | free, keyless | same | high | pregame-forecast weather | **A−** (short lead) | low | **FETCHED** 2022+ |
| **Open-Meteo Previous Runs API** | `previous-runs-api.open-meteo.com/v1/forecast`, `*_previous_day1` | same, at fixed 1-day lead | ~2024–present | continuous | forecast issued 24 h before | free, keyless | same | high | true day-ahead forecast; prospective capture | **A** | low | **FETCHED** 2024+; live sidecar candidate |
| Open-Meteo Forecast API (live) | `api.open-meteo.com/v1/forecast` | 7-day hourly | now | hourly | pregame forecast | free, keyless | same | high | prospective capture | C | low | sidecar |
| NWS `api.weather.gov` | points/gridpoints forecast (runner OK) | US forecasts | now | hourly | pregame | free, keyless | polite rate | good | prospective only; no archive | C | low | alternative sidecar source |
| ESPN core API — events, odds | `sports.core.api.espn.com/v2/.../events/{id}/competitions/{id}/odds` (runner OK; `site.api.espn.com` returns Akamai 403 from datacenter IPs) | per-book lines (ESPN BET, etc.), event details, venue | 2024 sample returned 2 providers; movement/history endpoint returned empty | live | market | free, keyless, undocumented | undocumented; may change | medium | market-informed research / line-movement sidecar | C (history endpoint empty) | medium | sidecar candidate only |
| ESPN core API — team athletes / injuries | `.../seasons/{Y}/teams/{id}/athletes`, `.../teams/{id}/injuries` (runner OK) | current roster refs; injuries list (empty in offseason) | current | live | live only; no archive | free, keyless | undocumented | medium | injuries/depth for prospective capture | **C** | medium | sidecar candidate |
| ESPN depth charts | `.../teams/{id}/depthcharts` | — | — | — | "Depth charts not supported for college-football" (HTTP 400) | — | — | — | — | **E** | — | reject |
| CFB Depth (cfbdepth.com), College Football Network injury tracker, GunslingerBuzz | HTML pages | depth charts / injuries | current | live | live only; HTML scraping, terms unverified | free | robots/terms not verified | low | — | C/E | high | not used; no scraping |
| footballscoop OC/DC trackers | HTML articles (`robots.txt` permits general crawling; disallows /api, /search) | coordinator changes per cycle | 2018–2026 by article | per cycle | announcements are dated events | free | scraping editorial HTML; fragile | medium | coordinator continuity | B (would need manual/LLM extraction) | high | **documented, not integrated** — no structured historical feed |
| collegefootballpoll.com coaching changes | HTML (runner OK, 320 KB page) | head-coach changes by year | multi-year | per cycle | dated | free | terms unverified | medium | duplicates CFBD `/coaches` | B | medium | not needed (CFBD covers HC) |
| Wikipedia season pages (REST HTML) | `en.wikipedia.org/api/rest_v1/page/html/{Y}_NCAA_Division_I_FBS_football_season` (runner OK) | coaching changes tables incl. coordinators in some years, season notes | 2014–2026 | continuous | current page = revised history (**D**); revision API could give as-of snapshots (B) | free, CC-BY-SA | API etiquette | medium | coordinator changes | B/D | high (table parsing per year) | future work |
| Kaggle "College Football Game Stats 2002–2025" | kaggle.com (auth required) | box scores | 2002–2025 | ad hoc | provenance unverified | account required | Kaggle ToS | unknown | duplicates NCAA/ESPN data | E (provenance) | — | reject |
| sports-statistics.com CFB passing box scores / weather | blocked by egress; terms unknown | per-QB game lines; game weather | 2004+ | ad hoc | unknown | free download claimed | unverified | unknown | duplicates cfbfastR pbp | E (unverified) | — | reject |
| 247Sports / On3 / Rivals portal & recruiting pages | HTML, JS-rendered | ratings, portal timing | 2021+ | live | current pages revised; no archive | free to view; scraping restricted by terms | prohibited-scrape risk | — | — | **E** | — | reject; CFBD `/player/portal` mirrors their ratings |
| CFBD `/roster`, `/stats/player/season` | metered | roster by season (revised), season totals | 2014+ | — | roster D; player season stats POSTGAME (A as S−1) | metered — **0 budget** | — | — | — | B/D | — | not fetched (quota); NCAA rosters substitute |

### Future paid data candidates (documented, not purchased)

| Source | What it would add | Why it may matter |
|---|---|---|
| CFBD Patreon tier (paid) | unlimited metered calls; `/games/players`, `/roster` history, weekly `/ratings` | removes the quota constraint that currently threatens production, not new information per se |
| Odds archives with timestamps (e.g. odds-history vendors, OddsJam/SBR archives) | timestamped opening → closing paths, per-book | Phase 14 line-movement research needs timestamps CFBD `/lines` lacks |
| Injury/availability feeds (e.g. Rotowire/FantasyPros-style CFB availability, beat-writer aggregators) | dated injury and suspension statuses | the one category the market visibly prices (lame-duck bowls, QB availability) with no free historical archive |
| Coordinator database (e.g. commercial staff directories) | OC/DC tenure with dates | coordinator continuity; only editorial HTML exists free |
