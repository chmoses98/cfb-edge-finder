# CFB Data Sources

Research conducted via web search in August 2026. **Confidence caveat:**
the research agent's sandbox could not directly fetch `collegefootballdata.com`,
`docs.kalshi.com`, `sports-reference.com`, or `patreon.com` -- everything
below on those domains comes from search-result snippets and third-party
guides, not a primary fetch of live docs/pricing pages. Every unverified
claim is flagged explicitly. **Re-verify exact pricing, rate limits, and
endpoint parameters directly against each source before writing production
code against them** -- this document is a starting map, not a locked spec.

Machine-readable companion: `src/cfb_edge_finder/data/sources.py`.

---

## Schedules / Scores / Games

**Primary: CollegeFootballData.com (CFBD) REST API v2**
(`api.collegefootballdata.com`, docs at `collegefootballdata.com`).
Free API key, self-serve signup. Free tier reported ~1,000 calls/month;
academic/.edu tier ~3,000/month; paid Patreon tiers reported $10-$30/mo for
75k-500k calls/month (**figures vary across secondhand sources, unverified
against the live pricing page**). GraphQL API exists, gated behind a higher
Patreon tier. 41 documented REST endpoints, FBS+FCS coverage nominally back
to 1869 (realistically structured/usable from ~1990s+, deep stats from
2000s+). Commercial use reportedly permitted under the tiered quota;
**reselling/redistributing raw API data is prohibited** -- fine for
internal model use, matters if any output surfaces raw CFBD data
externally. Single-maintainer project (no enterprise SLA), but widely used
in the open-source CFB analytics community (cfbfastR, `cfbd` Python
client) -- reasonable reliability signal for that reason.

**Fallback: ESPN hidden/unofficial API.** No key required.
`https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?groups=80&dates=YYYYMMDD`
(groups=80 = FBS); deeper box scores via `.../summary?event={id}`.
Explicitly unofficial -- can change or break without notice, no ToS grant
for automated commercial use. Appropriate as a cross-check/secondary feed,
not a sole production dependency.

## Play-by-Play

**Primary: CFBD `/plays`.** Community-standard (wrapped by `cfbfastR` and
the `cfbd` Python client). Includes derived EPA/PPA and win-probability
fields. Commonly cited as available from ~2001-forward for FBS, though the
exact start year needs direct confirmation via the `year` param before
relying on it. Call-heavy: a full-season pull for the whole FBS slate
likely requires the paid tier.

**Fallback:** ESPN's per-game `summary?event={id}` play arrays (unofficial,
no bulk endpoint -- requires looping over event IDs from the scoreboard).

## Rosters / Depth Charts / Transfers

**Primary: CFBD `/roster`** (reported from 2009) and `/teams/talent`.
CFBD also has a transfer-portal endpoint and a `player/returning`
continuity-metric endpoint (not injury status). **Depth charts are a gap**
-- no first-class CFBD endpoint was confirmed.

**Fallback for depth charts:** ESPN's unofficial team depth-chart endpoint
(`site.api.espn.com/.../teams/{id}/depthchart`) -- coverage for CFB is
inconsistent, verify per team. Team-site-published PDFs are a last resort
(fragile, not automatable at scale, ToS varies per athletic department).

**Fallback for transfers/recruiting: On3 and 247Sports.** Best trackers for
this, but **no public commercial API was found** for either -- 247Sports'
data rights sit with CBS Sports. Automated scraping is a ToS risk; a real
license conversation would be required for production automation. CFBD's
own 247Sports-composite-talent endpoint is the defensible route since it's
CFBD's licensed re-aggregation.

## Injuries / Availability / QB Starter Status

**This is the weakest category, structurally, not just a sourcing gap.**
College football has no NFL-style mandatory injury report, and no CFBD
`/injuries` endpoint was found. **There is no reliable structured API for
this category.** Treat it as a news/text-extraction problem: beat-writer
coverage, team announcements, On3/247Sports news (same access caveats as
above) are the only realistic sources, and none of them are subscribable
feeds. Post-hoc QB-starter identification is possible from play-by-play
(who took snaps), but pre-game starter prediction is a build-your-own
NLP/news-ingestion pipeline, not an API integration.

**Explicit recommendation to whoever scopes the projection engine:**
budget for a news-ingestion pipeline here, not an API key, if QB-starter
status or injury availability is meant to be a first-class model input in
the near term. `UncertaintyProfile.qb_status_confirmed`
(`src/cfb_edge_finder/schemas/projection.py`) exists precisely so the
system can represent "we don't actually know" honestly until that pipeline
exists.

## Coaching Changes

**Primary: CFBD `/coaches`** -- coaching records/tenure by team/year, low
frequency and low volume, easy to stay within the free tier.

**Fallback:** Wikipedia's FBS-coaches list pages (accurate, manually
curated, not an API) or news search for real-time hire/fire detection.

## Team Efficiency / EPA / SP+ / FPI / Success Rate / Explosiveness / Pace

**Primary: CFBD `/ppa`** (its EPA analogue: season-level PPA, success
rate, explosiveness, havoc-rate splits by rush/pass and offense/defense)
**and `/ratings/sp`** (SP+, now published through CFBD after Bill Connelly
moved from ESPN). This is the closest thing to a one-stop shop for
opponent-adjusted efficiency in CFB. SP+ historical depth wasn't
independently confirmed via CFBD's own API this session -- check
`/ratings/sp` with old `year` values directly. Pace/possession data is not
a dedicated field; expect to derive it yourself from `/plays` or box-score
endpoints (plays run, time of possession).

**Fallback/complement: ESPN FPI**
(`sports.core.api.espn.com/v2/sports/football/leagues/college-football/seasons/{year}/powerindex`,
unofficial). Best used as an independent second opinion against SP+/PPA
(different methodology) rather than a primary feed -- good for ensembling
or sanity-checking the system's own ratings once they exist.

## Red-Zone / Situational / Home-Field Context

No dedicated CFB red-zone-efficiency endpoint was confirmed to exist as a
first-class CFBD field -- **this is a derived metric**, computed from
`/plays` (filter by field position, compute scoring rate), not a
subscribable feed. Home-field context (venue, neutral-site flags,
attendance) comes from CFBD's `/games` and `/venues`, which include venue
lat/long -- doubling as the geocoding input for weather lookups below.

## Weather

**Primary (forecast): NOAA/NWS `api.weather.gov`.** Free, no API key,
requires only a descriptive `User-Agent` header (rejected without one).
Reported rate limit ~5,000 requests/hour. Forecast/current-conditions
oriented -- **not the right tool for historical reconstruction.**

**Primary (historical): Visual Crossing Timeline Weather API.** Free tier
reported at 1,000 records/day, notably usable **commercially** on the free
tier (unusual -- most competitors gate commercial use behind a paid plan).
Paid tiers reported from ~$0.0001/record pay-as-you-go up to ~$35-150/mo
subscription tiers. 50+ years of historical daily/hourly data by
lat/long -- pairs directly with CFBD's venue coordinates for pulling
exact-kickoff-hour conditions on past games, which is what backtesting
needs.

## Betting Lines (context data)

**CFBD `/lines`** aggregates spread/total/moneyline across multiple
sportsbooks per game, inside the same CFBD quota -- useful as a
market-consensus cross-check feature independent of Kalshi itself.

## Kalshi Market Data (event/series discovery)

Base API reported as `https://api.elections.kalshi.com/trade-api/v2`
(also seen referenced as `trading-api.kalshi.com/trade-api/v2` in some
SDKs/docs -- **confirm which is current before building**). Full docs at
`docs.kalshi.com`, with a machine-readable `openapi.yaml` reportedly
available. These docs were not directly fetchable this session; treat the
following as third-party-sourced, not primary-verified:

- **Auth:** Public read endpoints (`/series`, `/events`, `/markets`,
  orderbooks) reportedly work unauthenticated; trading requires signed
  requests (`KALSHI-ACCESS-KEY`/`-TIMESTAMP`/`-SIGNATURE`, RSA
  key-pair) -- irrelevant to this foundation phase since order placement
  is explicitly out of scope.
- **Rate limits:** Token-bucket; a throttled request returns HTTP 429 with
  no `Retry-After` header, so any client needs its own bounded backoff.
- **CFB series structure (confirmed via live market URLs):**
  - `KXNCAAFGAME` -- single-game markets. Event tickers look like
    `KXNCAAFGAME-26AUG29UNCTCU` (date + matchup-coded).
  - `KXNCAAFWINS` -- team regular-season win-total markets.
  - `KXNCAAF` -- national championship market
    (`KXNCAAF-26`/`KXNCAAF-27`, season-tagged).
  - Discovery flow: `GET /series` (sports category) → series tickers →
    `GET /events?series_ticker=KXNCAAFGAME` → event tickers per
    game/week → `GET /markets?event_ticker=...` for live prices. Exact
    query-param names were not directly verified against the live spec --
    pull `openapi.yaml` first when Milestone E starts.
- No ToS concern for read-only market data at reasonable poll rates; this
  is the right approach (Kalshi's own REST API), not scraping the site.

---

## Summary Table

| Category | Primary | Fallback |
|---|---|---|
| Schedules/scores | CFBD `/games` | ESPN hidden scoreboard API |
| Play-by-play | CFBD `/plays` | ESPN per-game `summary` endpoint |
| Rosters | CFBD `/roster` | ESPN roster endpoint |
| Depth charts | ESPN hidden depth-chart endpoint | Team-site scraping (fragile) |
| Transfers/recruiting | CFBD talent/portal endpoints | On3/247Sports (needs license for automation) |
| Injuries/QB status | **None reliable** -- build a news pipeline | Beat-writer/On3/247 monitoring |
| Coaching changes | CFBD `/coaches` | Wikipedia coach-list pages |
| EPA/SP+/efficiency | CFBD `/ppa`, `/ratings/sp` | ESPN FPI hidden endpoint |
| Red zone/pace | Derive from CFBD `/plays` | -- |
| Weather (forecast) | NWS `api.weather.gov` | -- |
| Weather (historical) | Visual Crossing Timeline API | NWS (weaker for past dates) |
| Betting lines | CFBD `/lines` | Kalshi itself as another market signal |
| Kalshi markets | Kalshi REST API `/series`,`/events`,`/markets` | -- |

## Unresolved data risks (carry into Milestone B planning)

1. **Injury/QB-availability has no clean API** -- it's an editorial-content
   pipeline, not a subscription. This is the single biggest data-source
   risk in the whole system and should be sized honestly before Milestone C
   commits to using QB-status as a hard model input.
2. **CFBD is a single-maintainer, Patreon-funded project.** Fine for
   research/backtesting now; confirm current tier pricing/quota directly
   at `collegefootballdata.com` before committing to production call
   volume, since the figures here are secondhand.
3. **ESPN and Kalshi's exact base-URL/param details need re-verification**
   against their live docs/OpenAPI spec at build time, not trusted from
   these research notes.
4. **247Sports/On3 automation is a licensing question, not a technical
   one** -- do not scrape without resolving this first.
