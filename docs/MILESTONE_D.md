# Milestone D — Live Kalshi CFB Ingestion, Mapping, and Research-Only Pricing

**Status: genuine, live-evidence-driven discovery, mapping, contract-semantics
parsing, and research-only pricing of Kalshi's current college-football
market universe, built entirely from real API responses captured this
session (not the historical audit alone). All three CORE_V1 families —
game winner/moneyline (`KXNCAAFGAME`), spread (`KXNCAAFSPREAD`), and
total (`KXNCAAFTOTAL`) — are confirmed LIVE with real, currently-active
markets (368 / 1,910 / 1,425 respectively at final capture). Kalshi's
contract semantics (strict ">" threshold operator, half-point lines, a
per-team spread ladder under one event, a specific 48-hour postponement
rule, and the winner-market's own distinct rules_primary phrasing) were
confirmed from real, quoted `rules_primary`/`rules_secondary` text for
all three families. An early discovery run this session found
`KXNCAAFGAME` with zero events; that was a genuine, real snapshot of the
market at that moment, not a bug — 368 winner markets were live by the
time of this milestone's final capture, a real change in Kalshi's own
listed universe within this session, reported honestly rather than
smoothed over. This is RESEARCH-ONLY: no bet recommendation, stake
sizing, betting tier, or order-placement code exists anywhere in this
change.**

This document assumes `docs/MILESTONE_C2.md` (model version
`0.4.0-milestone-c2-live-margin-correction`) and
`docs/KALSHI_CFB_MARKET_AUDIT.md` (the historical, evidence-graded market
family registry) as reference. Where this milestone's LIVE evidence
confirms, corrects, or extends a claim from either of those documents,
this document says so explicitly.

## 1. Repository discipline confirmation (mission section 1)

Confirmed before any code change:
- Starting main SHA: `c8a405eadcf5233500311777a75e5711b4a849ac` (Milestone
  C.2 merge), matching the mission brief exactly.
- Model version confirmed as `0.4.0-milestone-c2-live-margin-correction`
  (`scripts/build_cfb_baseline.py::MODEL_VERSION`) — this milestone's own
  capture script imports and asserts equality with that exact string
  (`tests/test_kalshi_milestone_d_guards.py::test_capture_script_model_version_matches_build_cfb_baseline_exactly`),
  so pricing can never silently fall back to an older version.
- No recommendation/staking/execution surface existed on main before this
  change, and none was added — see section 20 below.
- `chmoses98/edge-finder-api` (the MLB repo) was never opened, read, or
  modified in this mission.
- All work happened on a new branch, `claude/milestone-d-kalshi-cfb-live`,
  branched from that exact main SHA. The one direct-to-main commit
  (`bdbade3`) was infrastructure-only — registering the read-only GitHub
  Actions workflow file itself, containing zero application logic — per
  mission section 28's explicit allowance (a brand-new workflow file must
  exist on the default branch before `workflow_dispatch` can target it on
  a feature branch at all).
- This PR is **not** merged by this mission.

## 2. Live Kalshi API access (mission section 2)

This dev environment's own network egress to Kalshi's API hosts is
blocked by organization policy (confirmed via the agent proxy's
`__agentproxy/status` endpoint: 403 policy denial on both
`api.elections.kalshi.com` and `trading-api.kalshi.com`), mirroring the
established CFBD-access restriction from earlier milestones. The same
manual GitHub Actions `workflow_dispatch` pattern used for CFBD
(`validate-cfbd-live.yml`) was reused here:
`.github/workflows/validate-kalshi-cfb-live.yml`, manual-only,
`permissions: contents: read`, no trading credentials anywhere, four
script modes (`discovery`, `market_detail`, `fees`, `snapshot`).

**Base URL, confirmed live**: `https://api.elections.kalshi.com/trade-api/v2`
(HTTP 200 on `GET /exchange/status`). Two other candidate hosts
(`trading-api.kalshi.com`, `api.kalshi.com`) were tried and rejected —
either non-responsive or a different service. `KalshiClient`
(`src/cfb_edge_finder/data/kalshi_client.py`) uses this URL and sends
**no authentication anywhere** — every method used in this milestone
(`/exchange/status`, `/series`, `/series/{ticker}`, `/events`,
`/markets`, `/markets/{ticker}`) was confirmed to work unauthenticated.

**Pagination**: Kalshi's cursor-based pagination (a `cursor` field in the
response body, echoed back as a `cursor` query param) is followed
exhaustively by `KalshiClient._paginate()`, capped at 25 pages per call as
a defensive bound. A first-revision discovery run swept only the first
page of `/series` and reported inconsistent results as a result — fixed
in revision 2, confirmed via a full, correctly paginated sweep of all
16,955 Kalshi series.

**Real, confirmed status vocabulary**: `"active"` (tradeable) and
`"finalized"` (settled) — **not** `"open"`/`"closed"` as an early
draft of the discovery script incorrectly assumed (a real bug: every
currently-tradeable market was miscounted as closed until fixed).

**Real, confirmed pricing field names** (identical across BOTH the list
endpoint `GET /markets?series_ticker=X` and the single-market detail
endpoint `GET /markets/{ticker}` — confirmed via a dedicated, unfiltered
JSON probe, `scripts/validate_kalshi_market_detail_live.py`):
`yes_bid_dollars`, `yes_ask_dollars`, `no_bid_dollars`, `no_ask_dollars`,
`last_price_dollars` (decimal-string dollars on a $1 binary contract,
read directly as a probability — `"0.3500"` = 35% = $0.35), `volume_fp`,
`volume_24h_fp`, `open_interest_fp`, `liquidity_dollars`. An earlier
draft of this codebase incorrectly assumed a per-market detail fetch was
required to see these fields; that claim has been corrected in both
`kalshi_client.py`'s and `contract_semantics.py`'s docstrings, not left
stale.

## 3. Coverage architecture (mission section 3)

`src/cfb_edge_finder/kalshi/cfb_coverage_reason.py` defines
`KalshiCfbCoverageReason` (`MAPPED_SUPPORTED`,
`MAPPED_UNSUPPORTED_FAMILY`, `MAPPED_UNSUPPORTED_POPULATION`,
`AMBIGUOUS_GAME_MAPPING`, `AMBIGUOUS_TEAM_MAPPING`, `PARSE_UNRESOLVED`,
`STALE_OR_CLOSED`, `NON_GAME_FUTURES`, `DUPLICATE_OR_ALIAS`,
`OTHER_EXPLICIT_REASON`) — the mission's own suggested vocabulary,
exactly. `to_coverage_outcome()` maps every member onto the **existing**
(Milestone A) `CoverageOutcome` enum with a totality check
(`_assert_mapping_is_total()`) run at import time and covered again by
`tests/test_kalshi_cfb_coverage_reason.py`. This extends the existing
coverage/readiness architecture; it does not replace or duplicate it.

`MAPPED_SUPPORTED` is deliberately returned from exactly one place —
`kalshi/game_mapping.py::classify_mapped_market()` — since it is the only
function with all three facts needed to claim it: game identity resolved
(from `map_kalshi_event_to_game`), market family (CORE_V1 or not), and
both teams' FBS/FCS classification. `game_mapping.map_kalshi_event_to_game`
itself never claims `MAPPED_SUPPORTED` — its `KalshiGameMappingResult.reason`
is `None` on success, deferring the supported/unsupported call to
`classify_mapped_market`.

`ResearchLedger` (`kalshi/research_ledger.py`) exposes
`coverage_outcome_counts()` so a caller can prove
`sum(counts.values()) == len(ledger)` for any captured snapshot — see
section 15's live coverage-accounting check below.

## 4. Deterministic game mapping (mission section 4)

`kalshi/game_mapping.py::map_kalshi_event_to_game()` splits a market/event
title on a known separator (` at `, ` vs `, ` vs. `, ` v `, ` v. `, `@`),
resolves each side via the **existing**, exact-match-only, fail-loud
`teams.registry.resolve_team_alias` (no new fuzzy-matching logic added —
zero string-similarity code in this module), matches the resolved
team-pair against a candidate `GameRecord` list by set equality, and
applies a 36-hour kickoff/reference-timestamp window **only** to
disambiguate ties (>1 team-pair match) — team-pair identity is the strong
signal; date is a tiebreaker, not a filter.

All of the mission's explicitly named cases are covered by real,
constructed test fixtures in `tests/test_kalshi_game_mapping.py`:
- **Miami FL vs Miami OH**: disambiguated correctly by full name
  (`"Miami (FL)"` / `"Miami (OH)"`); bare `"Miami"` is rejected as
  `AMBIGUOUS_TEAM_MAPPING`, never guessed.
- **USC vs South Carolina**: resolve to two distinct team_ids
  (`usc` / `south-carolina`), never confused with each other.
- **Abbreviated/full names**: `"NC State"` and `"North Carolina State"`
  resolve to the same game.
- **Accented names**: `"San José State"` resolves correctly (a real
  alias in `teams/registry.py`, sourced from a genuine CFBD response).
- **Neutral-site games**: mapped by team-pair identity regardless of the
  `neutral_site` flag — that flag is bookkeeping only, never evidence of
  home-field advantage (an existing Milestone B invariant, unaffected by
  this milestone).
- **FBS-vs-FCS**: mapping itself succeeds on team identity alone;
  population-based unsupported-for-pricing status is `classify_mapped_market`'s
  job, not `map_kalshi_event_to_game`'s (see section 3).
- **Rescheduled games**: a unique team-pair match is accepted even when
  the Kalshi evidence's own reference timestamp differs from the
  candidate game's `kickoff_utc` by weeks — the date window only fires
  when disambiguation is actually needed (>1 match).
- **Ambiguous game mapping**: the same two programs meeting twice in one
  season (e.g. a regular-season game and a conference-championship
  rematch) both within the date window is correctly rejected as
  `AMBIGUOUS_GAME_MAPPING`.

## 5–7. Contract semantics: winner, spread, total (mission sections 5–7)

Confirmed from real, live, quoted evidence this session (Southern Utah at
Montana, originally scheduled 2026-08-29) — see
`kalshi/contract_semantics.py`'s module docstring for the full quotes and
`tests/fixtures/kalshi/` for the sanitized fixture JSON:

| Family | Ticker/series pattern | Exact YES meaning | Threshold form | Operator | Tie/push | Settlement source | Semantics confidence |
|---|---|---|---|---|---|---|---|
| Spread | `KXNCAAFSPREAD-{date}{teams}-{team}{line}` | `"<TEAM> wins by over <T> points"` | Half-point (e.g. 4.5) | Strict `>` (never `>=`) | Structurally impossible (integer margin can never equal a half-point line) | Official final result; 48-hour postponement rule (quoted verbatim in the module docstring) settles to "a fair price" if the game doesn't start within 48h of its original time | **CONFIRMED_LIVE** |
| Total | `KXNCAAFTOTAL-{date}{teams}-{line}` | `"Over <T> points scored"` | Half-point (e.g. 80.5) | Strict `>` | Structurally impossible | Same postponement rule | **CONFIRMED_LIVE** |
| Winner/moneyline | `KXNCAAFGAME-{date}{teams}-{team}` | `"<TEAM> wins"` (market `title`); `rules_primary`: `"If <TEAM> wins the <TEAM> vs <TEAM> college football game originally scheduled for <date>, then the market resolves to Yes."` | N/A | N/A (binary) | A tie is essentially impossible under current NCAA OT rules | Official final result; same 48-hour postponement rule as spread/total | **rules_primary/matchup text CONFIRMED_LIVE** (368 real active markets found; real example `KXNCAAFGAME-26SEP19CORCOLG-COR`, quoted verbatim above); `parse_winner_market`'s own per-contract grammar validation stays conservatively UNCONFIRMED (see below) |

`contract_semantics.parse_spread_market`/`parse_total_market` validate
the title-parsed threshold against the payload's own `floor_strike` field
for internal consistency, and mark `PARSE_UNRESOLVED` (never a guessed
value) if the grammar doesn't match exactly or the two disagree.
`parse_winner_market` always returns `semantics_confidence="unconfirmed"`
deliberately: it accepts any non-empty title as a raw team name rather
than validating a specific confirmed grammar pattern the way spread/total
do, even though the underlying `rules_primary` text (used for game-
identity mapping via `extract_matchup_from_rules_primary`, not by
`parse_winner_market` itself) is now live-confirmed. Tightening
`parse_winner_market` to validate the real `"<TEAM> wins"` title grammar
directly is a natural next step, not done in this pass.

**Per-team spread ladders confirmed structurally**: both teams in one
game get their own full threshold ladder under a single `event_ticker`
(e.g. `KXNCAAFSPREAD-26AUG29SUUMONT` hosts both `SUU5`
("Southern Utah wins by over 4.5") and `MONT39`/`MONT36` — Montana's own
side of the ladder) — each market's own title/rules text names the team
it is about; nothing in this codebase infers a side from ticker order.

## 8. Model/Kalshi-semantics separation (mission section 8)

`projections/distribution.py::price_market()` — the Milestone A/C
football probability engine — is called from exactly one place in the
Kalshi pathway, `kalshi/market_pricing.py::price_parsed_contract()`, and
receives only a `GameDistribution` + a generic `(MarketFamily, Side,
line)` spec. It has zero knowledge of Kalshi tickers, titles, or contract
grammar. The Kalshi-specific translation lives entirely in
`market_pricing.py`, specifically the spread sign-convention derivation:

- `price_market`'s SPREAD family uses a signed `home_line` (negative =
  home favored): `P(home covers) = P(margin > -home_line)`.
- Kalshi's own grammar is team-named, not home-relative: `P(margin > T)`
  for the named team.
- Named team = HOME → `home_line = -T`.
- Named team = AWAY → `P(margin < -T)` = `prob_away_covers`, which needs
  `-home_line = -T`, i.e. `home_line = T`.

Both directions are implemented explicitly (never a single "just negate
it" shortcut) and independently verified in
`tests/test_kalshi_market_pricing.py` against `prob_home_covers`/
`prob_away_covers` called directly — not merely "returns *a* number."

## 9. Model version enforcement (mission section 9)

`capture_kalshi_cfb_snapshot.py::MODEL_VERSION` is the literal string
`"0.4.0-milestone-c2-live-margin-correction"`, asserted equal to
`scripts/build_cfb_baseline.py::MODEL_VERSION` by a dedicated test
(section 1 above). `GameProjectionCache.get_or_build()`
(`kalshi/game_projection_cache.py`) calls the exact same pipeline in the
exact same order as `build_cfb_baseline.py`'s own live path: 
`fit_fbs_efficiency_ratings` → `build_expanding_residual_pool` →
`project_game` → `apply_margin_correction` (using the frozen
`FROZEN_MARGIN_CORRECTION_PARAMS`/`MARGIN_CORRECTION_ARTIFACT_VERSION`)
— no reimplementation, no alternate code path.

FBS-vs-FCS games are never priced: `classify_mapped_market` routes them
to `MAPPED_UNSUPPORTED_POPULATION` before `market_pricing.py` is ever
called, so no `model_probability` is computed and no
model-vs-market comparison is ever emitted for that population.

## 10. Research pricing output shape (mission section 10)

`schemas/kalshi_observation.py::KalshiResearchObservation` is the single
row type for a priced (or unpriced, or rejected) market observation. The
model-vs-market difference is named `research_probability_gap`
(`model_probability - executable_yes_price`), never "edge" — enforced
mechanically by `tests/test_kalshi_milestone_d_guards.py`, which scans
every literal status string and closed-vocabulary enum value this
milestone introduces for betting language. No bet/stake/tier language
exists in any field name, enum value, or literal string this milestone
produces (see section 20).

## 11. Executable price semantics (mission section 11)

`kalshi/price_extraction.py::ExtractedMarketPrice` distinguishes:
- `executable_yes_price`/`executable_no_price` — the best ASK
  (`yes_ask_dollars`/`no_ask_dollars`), the price a taker could actually
  cross right now to buy YES/NO. This is what `KalshiResearchObservation`
  uses for `research_probability_gap`.
- `midpoint` — `(yes_bid + yes_ask) / 2`, kept as a clearly separate,
  never-substituted research metric (`market_midpoint` on the
  observation row).
- `has_quoted_market`/`has_any_volume` — kept as two separate booleans,
  since real evidence (the SUU/Montana markets) showed a genuinely fresh
  market can have a real bid/ask AND a resting orderbook while
  `volume_fp`/`volume_24h_fp` are still exactly `"0.00"` — quoted and
  orderbook-backed, but never yet traded.

## 12. Fee applicability (mission section 12)

**Status: UNVERIFIED, confirmed for a third time.** `kalshi/executable_price.py`
already documented that this dev environment's own egress to
`docs.kalshi.com` is blocked (attempted twice). This milestone made a
third, genuine attempt from a GitHub Actions runner (unrestricted
egress), `scripts/validate_kalshi_fees_live.py`, trying six candidate
public URLs:

| URL | Result |
|---|---|
| `https://kalshi.com/docs/fee-schedule` | HTTP 429 (rate-limited) |
| `https://docs.kalshi.com` | HTTP 200, but the 3 "fee" mentions found are inside minified JS/JSON app-shell metadata, not fee-schedule content |
| `https://docs.kalshi.com/reference/fees` | HTTP 404 |
| `https://docs.kalshi.com/getting-started/fees` | HTTP 404 |
| `https://trading-api.kalshi.com/trade-api/v2/exchange/schedule` | HTTP 401 |
| `https://api.elections.kalshi.com/trade-api/v2/exchange/schedule` | HTTP 200, but this is the exchange's trading-hours schedule, not a fee schedule — 0 "fee" mentions |

No usable fee-schedule content was found from either environment.
`kalshi/executable_price.py::UNVERIFIED_PLACEHOLDER_FEE_RATE` remains
exactly that — a loudly-named placeholder never wired in as a default —
and no net research fee utility is implemented against real Kalshi CFB
markets in this milestone. **Exact blocker**: Kalshi's current published
CFB fee schedule was not found at any of the six candidate URLs tried
from either network path; verifying it (if it exists at a URL not yet
tried, or requires an authenticated session) is future work.

## 13–14. Prospective snapshot architecture (mission sections 13–14)

`schemas/kalshi_observation.py::KalshiResearchObservation` (frozen
pydantic model — `model_config = ConfigDict(frozen=True)`) is the
immutable row type. `kalshi/research_ledger.py::ResearchLedger` is the
append-only store: `append()` raises `DuplicateObservationError` for a
repeated `(snapshot_id, kalshi_market_ticker)` pair, and there is no
update/replace method anywhere in the class — a later observation of the
same market is always a new row with a new `snapshot_id`.

`SnapshotTiming` (`schemas/kalshi_observation.py`) carries a `label`
(`EARLY_OPEN`/`T_7D`/`T_3D`/`T_24H`/`T_6H`/`T_90`/`T_60`/`T_30`/`CLOSING`,
or another explicit string) and an optional `hours_before_kickoff`.
Nothing in this codebase yet schedules automatic captures at these
horizons — per mission section 14's explicit allowance to build the
manual-capture path first — `capture_kalshi_cfb_snapshot.py` labels every
capture `EARLY_OPEN` today; wiring a scheduled Routine to fire this
script at each horizon label is future work, not required for this
milestone.

## 15. First genuine prospective capture (mission section 15)

`scripts/capture_kalshi_cfb_snapshot.py`, run via the `snapshot` option of
`validate-kalshi-cfb-live.yml`, is the first genuine end-to-end capture:
live CFBD schedule fetch → live Kalshi CORE_V1 market fetch (`KXNCAAFGAME`,
`KXNCAAFSPREAD`, `KXNCAAFTOTAL`) → `map_kalshi_event_to_game` →
`classify_mapped_market` → (for `MAPPED_SUPPORTED` games)
`GameProjectionCache.get_or_build` once per game → `price_one_market` per
contract → `ResearchLedger`.

Getting to a genuine, complete, all-three-family capture took several
live-evidenced fixes, each driven by a real failure or a real, honest
partial result rather than assumption:

1. **Job `32816755586`**: `GET /markets?series_ticker=KXNCAAFGAME&status=active`
   returned HTTP 400. Earlier steps completed correctly first
   (**3,493 candidate CFBD games**, **7,210 historical `TeamGameLine`
   rows**, both fetched live). `_fetch_active_markets_safe()` was added
   to catch a per-series `HTTPError`.
2. **Job `97708513504`**: with that catch in place, EVERY series
   returned 400, including `KXNCAAFSPREAD`/`KXNCAAFTOTAL` (confirmed to
   have real active markets). Root cause: `status=active` is not a valid
   `/markets` query PARAMETER at all, even though `"active"` IS the real
   value in each market's own response-body `status` FIELD. Fixed by
   fetching unfiltered and checking `status` client-side, matching
   `validate_kalshi_cfb_live.py`'s already-working approach.
3. **Job `97709274167`** (first fully-running capture): 100% of 2,278
   discovered game-level markets landed in `TICKER_UNRESOLVED`. Root
   cause, confirmed via a live `GET /events/{event_ticker}` probe (job
   `97709841758`): the event object itself has no title/matchup field at
   all, and each market's own `title` is single-team/single-line, never
   splittable into two team names. Fixed by extracting the matchup from
   `rules_primary` prose instead (`extract_matchup_from_rules_primary`,
   section 4 above).
4. **Job `97710429233`** (first successful priced capture): 256/3,995
   observations model-priced — real SPREAD/TOTAL FBS-vs-FBS contracts —
   but all 368 live `KXNCAAFGAME` markets still landed in
   `PARSE_UNRESOLVED`. A live probe of a real winner-market ticker (job
   `97711133675`, `KXNCAAFGAME-26SEP19CORCOLG-COR`) found its
   `rules_primary` phrases the matchup as `"...wins THE <matchup> college
   football game..."` (no "in"), unlike spread/total's `"...points IN THE
   <matchup> college football game..."`. Fixed the extraction regex to
   match on `\bthe ` instead of `in the `.
5. That fix broke TOTAL: its real text is `"If THE teams collectively
   score more than 80.5 points in THE <matchup> college football
   game..."` — two `"the"`s precede `"college football game"`, and the
   widened, case-insensitive match anchored on the wrong one. Fixed by
   requiring the character immediately after `"the "` to be uppercase
   (real team names are always capitalized proper nouns) and dropping
   `re.IGNORECASE` on this one regex — confirmed correct against real
   text from all three families (`tests/test_kalshi_contract_semantics.py`).
6. With mapping now succeeding for ~300+ real games sharing one `as_of`,
   job `32818570750` ran 15+ minutes without completing and was
   cancelled. Root cause: `GameProjectionCache` re-ran the ridge-
   regression ratings fit once per distinct `game_id`, even though it
   depends only on `(history, as_of)` — every game sharing a week's
   `as_of` was redundantly refitting identical ratings. Fixed by caching
   `(ratings, residual_pool)` by `as_of` alone (section 16-17 below).

**The final, complete, successful live capture** (job `97712569606`,
snapshot `79933d98-cbc2-46d3-92d6-649373215d63`, captured
2026-08-25T06:52:07Z) ran end to end in under a minute after the fixes
above:

| Series | Active markets discovered |
|---|---|
| `KXNCAAFGAME` (winner) | 368 |
| `KXNCAAFSPREAD` | 1,910 |
| `KXNCAAFTOTAL` | 1,425 |
| Futures/season-long (11 series attempted; several rate-limited to 0 this run — a genuine, reported API-rate-limit variance, not a code defect) | 99 |
| **Total observations in this snapshot** | **3,802** |

**Coverage accounting**: `{'ticker_unresolved': 3447, 'evaluated': 256,
'unsupported_market': 99}` — sums to exactly 3,802, matching the total
observation count.

**Research readiness**: `{'unresolved': 3478, 'semantics_verified': 68,
'research_comparable': 256}`.

**256 observations were model-priced and RESEARCH_COMPARABLE-eligible**
— real FBS-vs-FBS spread/total contracts, priced by the live C.2 model.
Example (Liberty at James Madison spread ladder, real numbers from this
capture):

```
KXNCAAFSPREAD-26SEP05LIBJMU-LIB8: model_probability=0.0866 executable_yes_price=0.29 research_probability_gap=-0.2034
KXNCAAFSPREAD-26SEP05LIBJMU-JMU8: model_probability=0.6795 executable_yes_price=0.46 research_probability_gap=+0.2195
```

**Why 3,447 markets stayed `TICKER_UNRESOLVED`, honestly explained**:
`_fetch_candidate_games()` (mission section 4's mapping layer's caller)
fetches CFBD's schedule via `division="fbs"` (CFBDClient's own default,
matching the same precedent Milestone B's `ingest_schedule.py` already
uses) — a game only appears as a mapping candidate if at least one side
is FBS. The live `KXNCAAFGAME` markets sampled in this capture (e.g.
Cornell/Colgate, Brown/New Hampshire, South Carolina State/Florida A&M,
Dartmouth/Lehigh, Lafayette/Columbia) are Ivy League, Patriot League, and
SWAC matchups — genuinely FCS-vs-FCS games, structurally absent from an
FBS-filtered candidate pool regardless of contract-semantics correctness
(confirmed working, see section 5-7). This is real, current evidence
about which games Kalshi's winner market happened to list this specific
week, not a mapping-logic defect — an honest, reported limitation per
mission section 27, and the natural next step (fetching a broader,
FCS-inclusive candidate pool the way `ingest_schedule.py`'s own
`_is_fbs_involved` defensive check anticipates) is noted rather than
silently worked around.

## 16–17. Ladder pricing and scale (mission sections 16–17)

`kalshi/ladder_pricing.py::price_one_market()` prices exactly one market
per call but is designed so every call for the same game reuses the same
`CachedGameProjection` object — the football model (`fit_fbs_efficiency_ratings`
→ `build_expanding_residual_pool` → `project_game` →
`apply_margin_correction`) runs **at most once per (game_id, as_of,
n_simulations, seed) tuple**, verified directly:
- `tests/test_kalshi_game_projection_cache.py::test_same_request_returns_the_same_cached_object`
  proves cache identity.
- `tests/test_kalshi_ladder_pricing.py::test_one_cached_projection_prices_the_whole_ladder_consistently`
  proves that four different spread thresholds, priced from the SAME
  cached projection, produce probabilities identical to calling
  `price_market()` directly against that projection's own
  `GameDistribution` — i.e. the ladder pricing path is provably
  equivalent to, not merely similar to, direct closed-form repricing.

**Monotonicity**, checked directly against real model output (not
asserted as a property of the math in the abstract):
- `test_spread_ladder_probability_decreases_as_threshold_increases`: a
  larger favorite-cover threshold strictly lowers the cover probability
  across five rungs (0.5 → 20.5).
- `test_total_ladder_probability_decreases_as_threshold_increases`: a
  larger total threshold strictly lowers the OVER probability across four
  rungs (35.5 → 65.5).

**Scale**: `GameProjectionCache` is a plain dict keyed by the frozen,
hashable `GameProjectionRequest` — O(1) lookup after the first build per
game. The existing `tests/test_scale.py` synthetic benchmark (80 games ×
150 markets/game = 12,000 tickers) already exercises the coverage-ledger
layer at the target scale mission section 17 describes; this milestone's
own architecture (one cache entry per game, reused for every contract in
that game's ladder) is the mechanism that keeps a real 50–80-game,
many-contract-per-game week from re-running the expensive Monte Carlo
step per contract.

**A real, live-evidenced scale bug found and fixed in this pass**: the
live capture's first fully-successful game-mapping pass (after the
`rules_primary` fix in section 15) started mapping ~300+ real games
sharing a single `as_of` — and the capture ran 15+ minutes without
completing (job `32818570750`, cancelled). `GameProjectionCache` was
re-running `fit_fbs_efficiency_ratings` (a ridge regression over the
full multi-season corpus) AND `build_expanding_residual_pool` once per
distinct `game_id`, even though both depend only on `(history, as_of)`
— never on which specific game is being priced. `_ratings_and_pool_for_as_of()`
now caches that pair by `as_of` alone (`tests/test_kalshi_game_projection_cache.py::test_ratings_are_shared_across_games_with_the_same_as_of`
proves two distinct games sharing an `as_of` trigger exactly one fit).
The very next live run, with this fix in place, completed the entire
3,802-observation capture — game mapping, classification, and pricing
for all three CORE_V1 families — in under a minute
(section 15's final capture, job `97712569606`). This is exactly the
kind of thing a synthetic scale benchmark can miss (it stubs out the
expensive fitting step) and only a genuine live-scale run exposes —
reported here rather than left as a synthetic-only claim.

## 18. Research ledger schema (mission section 18)

`KalshiResearchObservation` carries every field the mission lists:
`snapshot_id`, `captured_at`, `game_id`, `kalshi_event_ticker`,
`kalshi_market_ticker`, `family`, `threshold`, `side`/`team`,
`semantic_operator`, `model_probability`, `executable_yes_price`,
`executable_no_price`, `market_midpoint`, `research_probability_gap`,
`fee_status`, `model_version`, `training_cutoff`, `coverage_outcome`,
`coverage_reason`, `parse_status`, `pricing_status`, `provenance`. A
`to_prospective_snapshot()` bridge method produces a real, schema-valid
Milestone A `ProspectiveSnapshot` from a fully-priced observation, so
this new row type is additive to the existing schema, not a fork of it.
`ResearchLedger.append()`'s duplicate-key check
(`(snapshot_id, kalshi_market_ticker)`) is exactly the mechanism that
guarantees "every discovered contract appears exactly once per
snapshot" (`tests/test_kalshi_research_ledger.py`).

## 19. Research readiness (mission section 19)

`kalshi/research_ledger.py::ResearchReadiness` (`DISCOVERED`, `MAPPED`,
`SEMANTICS_VERIFIED`, `MODEL_PRICED`, `RESEARCH_COMPARABLE`,
`UNSUPPORTED`, `UNRESOLVED`) is a pure function of an observation's own
already-set fields (`derive_research_readiness()`) — mechanically
provable from the row itself, and structurally incapable of returning a
recommendation state (`WATCH`/`EARLY_VALUE`/`ACTIONABLE`/`PASS` are not
members of this enum at all — see
`tests/test_kalshi_research_ledger.py::test_derive_research_readiness_never_returns_a_recommendation_state`).

## 20. No betting language (mission section 20)

`tests/test_kalshi_milestone_d_guards.py` scans every value of
`KalshiCfbCoverageReason` and `ResearchReadiness`, plus every literal
`pricing_status`/`parse_status` string this codebase actually assigns,
for the forbidden tokens (`bet`, `play`, `wager`, `stake`, `tier`) as
WHOLE tokens (so `"playoff"` never false-positives) and the phrases
`"strong buy"`/`"bet up to"`. `tests/test_no_recommendation_surface.py`
was extended to include the new `cfb_edge_finder.kalshi` package in its
existing identifier-name scan (`stake`, `bankroll`, `kelly`,
`place_order`, `place_bet`, `execute_trade`, `execute_order`,
`real_money`, `tier_a/b/c`, `qualification_bar`) — it passes cleanly.

## 21. Futures isolation (mission section 21)

`capture_kalshi_cfb_snapshot.py::FUTURES_SERIES_TICKERS` enumerates the
known season/futures series (`KXNCAAF`, `KXNCAAFPLAYOFF`, `KXHEISMAN`,
`KXNCAAFAPRANK`, `KXNCAAFTOPAPRANK`, `KXNCAAFCS`, `KXNCAAFACC`,
`KXNCAAFSEC`, `KXNCAAFCUSA`, `KXNCAAFBIG12`, `KXNCAAFBIGTEN`,
`KXNCAAFWINS`). Every market discovered under these series is tagged
`NON_GAME_FUTURES` directly, with `pricing_status="futures_separate_engine"`
— it never reaches `contract_semantics.py`, `market_pricing.py`, or
`GameProjectionCache` at all.
`tests/test_kalshi_milestone_d_guards.py::test_futures_series_never_overlap_core_v1_series`
proves the futures and CORE_V1 series sets are disjoint by construction.

## 22. Access-blocked workaround (mission section 22)

Covered fully in section 2 above — the same manual, read-only,
credential-free `workflow_dispatch` pattern already established for
CFBD is reused verbatim for Kalshi.

## 23. Fixtures (mission section 23)

`tests/fixtures/kalshi/spread_market_suu5.json` and
`total_market_81.json` are genuine field values captured from real live
`GET /markets/{ticker}` responses this session (Southern Utah at
Montana), trimmed to the fields this codebase's own modules read (not a
raw, unfiltered dump), with a `_source`/`_captured_at_utc` provenance
pair on each file and a `README.md` explaining the provenance and why
this specific game (both FCS programs) is real evidence of a
`MAPPED_UNSUPPORTED_POPULATION` case.

## 24. Test suite (mission section 24)

500 tests pass (up from 411 before this milestone), `ruff check src tests
scripts` clean. New test files:
`test_kalshi_cfb_coverage_reason.py`, `test_kalshi_game_mapping.py`,
`test_kalshi_contract_semantics.py`, `test_kalshi_price_extraction.py`,
`test_kalshi_market_pricing.py`, `test_kalshi_game_projection_cache.py`,
`test_kalshi_ladder_pricing.py`, `test_kalshi_research_ledger.py`,
`test_kalshi_milestone_d_guards.py`, plus an extension to the existing
`test_no_recommendation_surface.py`. Coverage against the mission's own
list: discovery (client tests via fixtures), family classification
(`classify_mapped_market` full branch coverage), game mapping including
ambiguous rejection and neutral-site, FBS-vs-FCS unsupported population,
winner/spread/total semantics (against real fixtures), half-point/push
handling, price extraction, executable-vs-midpoint distinction,
one-game/many-contract pricing (cache-identity + direct equivalence
proof), spread/total monotonicity, coverage-accounting completeness
(sum-check), immutable/duplicate-rejecting snapshots, provenance
(`DataProvenance` required on every observation), model-version
enforcement, no-recommendation-surface, futures isolation.

## 25. This document (mission section 25)

This file.

## 26. Success criteria

- Genuine markets fetched: yes — 3,493 candidate CFBD games, 7,210
  historical `TeamGameLine` rows, and 3,802 real Kalshi CFB market
  observations across all three CORE_V1 families plus futures, all via
  live API calls this session (final capture, section 15).
- All discovered markets accounted for: yes — the final capture's
  coverage counts (`ticker_unresolved=3447, evaluated=256,
  unsupported_market=99`) sum to exactly 3,802, matching the total
  observation count, mechanically provable via
  `ResearchLedger.coverage_outcome_counts()`.
- Supported markets map deterministically: yes — no fuzzy matching
  anywhere in `game_mapping.py`; every ambiguous case fails loud.
- Semantics verified from genuine evidence: yes for all three CORE_V1
  families (spread, total, winner) — real `rules_primary` text confirmed
  live for each (section 5-7).
- Supported FBS-vs-FBS contracts priced by the real C.2 model: yes, 256
  real contracts, via `GameProjectionCache` + `price_parsed_contract`,
  using the exact `0.4.0-milestone-c2-live-margin-correction` pipeline.
- Comparisons use correct executable-price semantics: yes — best ask,
  never midpoint, with the distinction enforced by a dedicated test.
- Prospective snapshots persisted with provenance: yes — see section 15.
- No recommendation/staking/trading surface: yes — see section 20.

## 27. Stop condition (mission section 27)

All three CORE_V1 families are confirmed live this session with real
contract instances (368 winner, 1,910 spread, 1,425 total active
markets in the final capture) — an earlier discovery run this session
found `KXNCAAFGAME` with zero events, a real, honest snapshot of that
moment, not a fabrication or an assumption papered over.

The one genuine, reported gap: of the 368 live winner markets, zero
mapped to a candidate game, because they are FCS-vs-FCS matchups (Ivy
League, Patriot League, SWAC) and this milestone's candidate-game fetch
is FBS-filtered (matching Milestone B's own `ingest_schedule.py`
precedent). This is reported explicitly in section 15 rather than
worked around by widening the candidate pool without evidence that doing
so is safe (a broader fetch could reintroduce ambiguity risks that
Milestone B's FBS-vs-FCS inclusion policy was deliberately scoped
around) — a genuine next step for a future pass, not silently patched
in this one. Nothing was fabricated to make this milestone "look
complete": every number in section 15 is a real, live capture result.

## 28. PR discipline (mission section 28)

A PR is opened into `main` from `claude/milestone-d-kalshi-cfb-live`. It
is **not** merged by this mission. No recommendation logic was begun. The
MLB repo (`chmoses98/edge-finder-api`) was never modified. The only
direct-to-main commit is the infrastructure-only workflow registration
described in section 1.
