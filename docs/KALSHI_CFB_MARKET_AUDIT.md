# Milestone B.5 — Historical Kalshi CFB Market Audit

**Status: research/documentation only. No projection engine, probability
model, or betting recommendation logic is implemented in this milestone.**
The machine-readable output of this audit is
`src/cfb_edge_finder/kalshi/cfb_market_family_registry.py`; this document
is the full evidence trail behind every entry in it.

## Why this exists

Milestone A seeded a generic, sportsbook-shaped `MarketFamily` enum
(`schemas/common.py`) before any Kalshi-specific research had been done.
This milestone replaces that assumption with genuine evidence about what
Kalshi has actually, historically listed for college football, so that
Milestone C's projection engine is designed around real Kalshi demand —
not a copy of what a sportsbook happens to offer.

## Method and a real environment constraint

This Claude execution environment's network egress to `kalshi.com` and
Kalshi's API domains (`api.elections.kalshi.com`, `trading-api.kalshi.com`)
is blocked (`EGRESS_BLOCKED`), the same restriction encountered against
CFBD/ESPN in Milestone B and against `docs.kalshi.com` in Milestone A's fee
research (see `kalshi/executable_price.py`). Direct API calls and direct
page fetches were both attempted and both failed. Unlike CFBD, there is no
GitHub Actions secret or workflow set up for a Kalshi API key in this
mission, so the GitHub-Actions-runner workaround used in Milestone B was
not available here either.

What *did* work: web search returned real, quoted excerpts from Kalshi's
own market pages and from press coverage that directly quotes Kalshi's CFTC
self-certification filings and live market prices. Every finding below is
sourced to a specific URL. Nothing here is invented — where a question
could not be answered from available evidence, it is marked **UNVERIFIED**
rather than guessed at, per the mission's explicit instruction.

**Confidence levels used throughout:**
- **CONFIRMED** — direct, dated primary evidence: a real Kalshi ticker/URL,
  a directly-quoted CFTC filing or contract template, or a directly-quoted
  live price.
- **PROBABLE** — evidence exists but is indirect: generic-to-football
  rather than CFB-specific, inferred from a template's grammar rather than
  a concrete example, or reported only by secondary sources.
- **UNVERIFIED** — searched for directly; no confirming *or* denying
  evidence was found. This is explicitly not the same as "confirmed
  absent."

## Historical game-level Kalshi markets

### Game winner (moneyline-style) — CONFIRMED, CORE_V1

- **Series:** `KXNCAAFGAME` ("College Football Game")
- **Ticker pattern:** `kxncaafgame-{yy}{mon}{dd}{away_code}{home_code}`
- **Real examples found:** `kxncaafgame-26jan09oreind` (Oregon at Indiana),
  `kxncaafgame-25dec31michtex` (Michigan at Texas),
  `kxncaafgame-25dec30ccarlt` (Coastal Carolina at Louisiana Tech),
  `kxncaafgame-25dec19kennwmu` (Kennesaw St. at Western Michigan),
  `kxncaafgame-26jan02ricetxst` (Rice at Texas St.),
  `kxncaafgame-26aug29unctcu` (North Carolina at TCU)
- **Contract semantics:** binary YES/NO per team, settling at $1.00 or
  $0.00; cent price is read directly as implied win probability, "with no
  vig folded in" (directly quoted).
- **Push/tie handling:** a tie settles both team contracts at 50¢ (general
  football rule; a CFB game reaching a true tie is essentially impossible
  under current NCAA overtime rules, so this is largely a moot edge case).
- **Alternate lines:** not applicable — a winner market has no threshold.
- **Required probability primitive for Milestone C:**
  `P(home_score > away_score)`

### Point spread — CONFIRMED (self-certification), CORE_V1

- **Regulatory record:** CFTC self-certification filed **2025-08-18**, for
  football contracts covering *both* college and pro games. Kalshi stated
  the markets would "initially be listed after close of business on
  August 18, 2025" (directly quoted).
- **Contract template (directly quoted from the filing):** *"Will `<team>`
  win `<game>` by `<above/below/between/exactly/at least>` `<count>`
  points?"*
- **Live corroboration:** a football spread example was found with a
  line of −4.5, "Yes" at 50¢ and "No" at 51¢ — confirms the template is
  actually listed, not merely filed.
- **Push/tie handling — PROBABLE, not CFB-specific-quoted:** reporting on
  this same football contract family states that a push-like scenario
  settles at "the last fair market price before the start of play," rather
  than a sportsbook-style refund. The direct quote found was in an NFL
  context; it was not independently confirmed word-for-word for a CFB
  spread market. Treated as PROBABLE for CFB by strong analogy (same
  contract family, same self-certification), not CONFIRMED.
- **Alternate lines — PROBABLE, ladder-shaped:** the
  above/below/between/exactly/at-least modifier set implies several
  distinct threshold contracts can coexist for one game (similar in spirit
  to the confirmed win-total ladder — see Futures section below), but no
  concrete example of several simultaneous spread rungs on one specific CFB
  game was found. This is inferred from template grammar, not observed
  directly.
- **Note on "winning margin"/"exact score":** the `exactly` and `between`
  modifiers mean a sportsbook-style "winning margin" or "score band" market
  is already covered by this same family, not a separate one. No evidence
  of a distinct winning-margin family was found (see registry entry
  `winning_margin_exact_score`).
- **Required probability primitive for Milestone C:** margin distribution,
  `P(home_score − away_score > threshold)` (and the below/between/exactly
  variants).

### Game total (over/under) — CONFIRMED (self-certification), CORE_V1

- Same 2025-08-18 filing. **Contract template (directly quoted):** *"Will
  `<game>` have `<over/under>` `<count>` points in `<time_period>` of
  `<game>`?"*
- **Live corroboration:** a total example was found with a line of 45.5,
  both sides ~51¢.
- **Push handling:** PROBABLE by analogy to the spread family's settlement
  mechanism; not independently quoted for totals specifically.
- **Alternate lines:** PROBABLE (same reasoning as spread).
- **`<time_period>` parameter:** the template itself generalizes beyond
  full-game totals — see First-half total below.
- **Required probability primitive for Milestone C:** total-score
  distribution, `P(home_score + away_score > threshold)`.

### First-half total — PROBABLE, LATER_GAME_MODEL

A live Kalshi category page (`kalshi.com/sports/football/all/1st-half-total`)
confirms this market *type* exists for "football." The game-total
contract template's `<time_period>` parameter explicitly supports
sub-periods. Not independently confirmed as CFB-specific versus NFL-only
at the time evidence was gathered — kept as a real, secondary target for
after CORE_V1 is validated, not a first-wave family.

### First-half spread, first-quarter markets — UNVERIFIED

The point-spread template's `<time_period>` parameter implies these are
technically self-certifiable, but no direct example, page, or explicit CFB
confirmation was found for either. **Explicitly not assumed present.**

### Team totals (a single team's own point total) — UNVERIFIED

No direct evidence found of a Kalshi CFB team-total market distinct from
the game total. Searches surfaced only generic sportsbook-comparison
content, nothing Kalshi-specific.

### Touchdown scorer props — CONFIRMED self-certified, CONFIRMED not actually offered for CFB

- CFTC self-certification filed **2025-08-25**, for both college and pro
  football. **Contract template (directly quoted):** *"Will
  `<player/team>` score `<first/last/any/count>` touchdown(s) `<count>` in
  `<time_period>` of `<game>`?"* Explicitly does not count passing
  touchdowns credited to a QB (attributed to the receiver instead).
- **Directly quoted reporting states the actual rollout is not happening
  for college players this season:** *"Kalshi does not appear likely to
  offer prop bets on college players this season... the actual rollout for
  college props appears limited."* Current/former players, coaches, and
  staff of the involved teams are barred from trading CFB contracts
  (enforced via an IC360 partnership) — a compliance detail relevant to any
  future props rollout, not itself evidence the props exist yet.
- **Conclusion:** legally self-certified for CFB, but not confirmed
  actually listed. Do not build.

## Coverage

### FBS-vs-FBS availability — CONFIRMED, broad but not exhaustively verified

Real tickers were found spanning marquee and clearly non-marquee games:
Oregon–Indiana, Michigan–Texas, North Carolina–TCU (Power-conference level)
alongside Coastal Carolina–Louisiana Tech, Kennesaw St.–Western Michigan,
and Rice–Texas St. (Group-of-Five level). This is direct evidence coverage
is not limited to Power-conference-only games, but no source states or
implies a claim of covering the *entire* weekly FBS slate — see breadth
estimate below.

### FBS-vs-FCS availability — UNVERIFIED

Multiple targeted searches (for specific known 2025 FBS-vs-FCS matchups,
and for the `kxncaafgame` ticker pattern combined with FCS-program names)
found **no direct Kalshi ticker either confirming or denying** an
individual-game market for an FBS-vs-FCS matchup. Separately, Kalshi does
run its own **FCS national champion futures market** (`KXNCAAFCS`,
confirmed — see Futures section), which shows Kalshi tracks FCS as a
distinct universe at the futures level, but that does not itself answer
whether individual FBS-vs-FCS *games* get single-game markets.

**This is stated as clearly unverified, not assumed absent, per the
mission's explicit instruction.** Practical implication for Milestone C:
do not assume all 888 FBS-involved games (per the genuine Milestone B live
count) are Kalshi-covered by default; do not assume FBS-vs-FCS games are
excluded either. This should be re-checked directly against a live Kalshi
feed once one is reachable, before Milestone C finalizes its default game
universe.

### Lower-profile game coverage — CONFIRMED present, breadth not exhaustively quantified

See the Group-of-Five tickers above. One source states 32 games were
listed for winner markets across the 2025 season-opening weekend, and
another states that on one specific day of that weekend (Saturday,
August 29, 2025) at least 8 FBS games had listings ("five of eight FBS
games on that card have carried a price... since May 20. The other three
did not get listed until August 11"). No source provides a complete
week-by-week census, so this is reported as sample-based evidence, not a
verified completeness percentage.

### Market-opening timing — CONFIRMED, highly variable

Directly quoted, dated evidence for the August 29, 2025 opening weekend:
five of eight FBS games on that day's card had carried a price **since May
20, 2025** (~101 days before kickoff); the other three were not listed
until **August 11, 2025** (~18 days before kickoff). The same source
frames this as markets that "can grow up eleven weeks apart for games on
the same day" — i.e., timing is not uniform even within one week's slate.
Season-long futures (win totals) open earlier still: the win-total ladder
had over 462,000 contracts traded "before a single kickoff" of the season.

**Design implication for prospective snapshot timing:** do not assume a
fixed "N days before kickoff" market-open rule. Evidence supports treating
market existence itself as variable and event-driven rather than
schedule-driven; a snapshot design should poll/discover rather than assume
a fixed lead time.

## Futures / season-long markets (confirmed distinct from single-game markets)

All of the following require season-simulation, polling, or
award-prediction machinery — explicitly **not** part of the single-game
score-distribution engine Milestone C should build first.

| Family | Confidence | Evidence |
|---|---|---|
| National champion | CONFIRMED | `KXNCAAF` series (`kxncaaf-27`); 9M+ contracts across 50 teams as of 2026-08-01 |
| Conference champion | CONFIRMED (ACC/SEC/C-USA); PROBABLE (Big Ten/Big 12) | Real tickers: `kxncaafacc-26`, `kxncaafsec-26`, `kxncaafcusa-26`. Big Ten/Big 12 described generically by secondary sources only. |
| CFP qualifier | CONFIRMED | `kxncaafplayoff-26` |
| Heisman Trophy winner | CONFIRMED | `kxheisman-27` |
| AP Poll rank (No. 1 weekly, Top-25 weekly) | CONFIRMED | Two distinct series: `kxncaafaprank-{season}w{week}r1`, `kxncaaftopaprank-{season}w{week}t25`; graded weekly against the real AP release |
| Regular-season win total | CONFIRMED | `KXNCAAFWINS` series — **the strongest concrete ladder evidence found in this whole audit**: Alabama's board directly quoted as listing 8+, 9+, 10+, 11+, and 12 wins as *separate simultaneous contracts*, not one line. 69 teams, 462,000+ contracts traded pre-kickoff. |
| Undefeated season | PROBABLE | Named repeatedly in aggregate descriptions across independent sources; no specific ticker independently captured |
| Coach fired / next coach | CONFIRMED | Real examples: "which coach fired before Week 1" (Bill Belichick at UNC, quoted 12%); "next Michigan head coach" market with named live-priced candidates |
| FCS national champion | CONFIRMED | `kxncaafcs-25` |

## Market-family registry

The full, machine-readable, tested classification lives in
`src/cfb_edge_finder/kalshi/cfb_market_family_registry.py`
(`KALSHI_CFB_MARKET_FAMILIES`, validated by `validate_registry()` and
covered by `tests/test_kalshi_cfb_market_family_registry.py`). It encodes
every family above plus the fields the mission spec required: evidence
sources, ticker pattern, contract semantics, boundary handling, alternate-
line support, required probability primitive, and Milestone C priority.
That module's own docstring explains its relationship to the pre-existing,
generic `schemas.common.MarketFamily` enum (Milestone A) — this audit adds
a Kalshi-evidenced classification layer; it does not replace or modify
that enum.

## Milestone C recommendation

| Market family | Historical confidence | Milestone C priority | Required probability primitive | Reason |
|---|---|---|---|---|
| Game winner | CONFIRMED | **CORE_V1** | `P(home_score > away_score)` | Directly confirmed series, real tickers across P5/G5, clean binary semantics |
| Point spread | CONFIRMED | **CORE_V1** | Margin distribution `P(margin > threshold)` | Confirmed self-certification + live example; the standard sportsbook-equivalent family Kalshi genuinely offers |
| Game total | CONFIRMED | **CORE_V1** | Total-score distribution `P(total > threshold)` | Confirmed self-certification + live example |
| First-half total | PROBABLE | LATER_GAME_MODEL | First-half total-score distribution | Real market type confirmed for "football," CFB-specificity and rollout unconfirmed — build after CORE_V1 |
| First-half spread, first-quarter markets | UNVERIFIED | UNSUPPORTED_UNVERIFIED | — | No direct evidence either way |
| Team total | UNVERIFIED | UNSUPPORTED_UNVERIFIED | — | No evidence found |
| Winning margin / exact score | PROBABLE (folded into spread) | UNSUPPORTED_UNVERIFIED | — | Not a distinct family from point_spread |
| Touchdown props | CONFIRMED self-cert / CONFIRMED not rolled out | UNSUPPORTED_UNVERIFIED | — | Legally permitted but directly reported as not actually offered for CFB this season |
| National champion, conference champion, playoff qualifier, Heisman, AP poll rank, win totals, undefeated season, coach market, FCS champion | mostly CONFIRMED, one PROBABLE | FUTURES_SEPARATE_ENGINE | (season/award/polling models, not single-game) | Genuine markets, but require an entirely different modeling approach than a per-game score distribution |

This is the audit-evidenced outcome: **the CORE_V1 target surface is
winner / spread / total**, consistent with what the mission spec flagged
as a *likely* (not forced) outcome — reached here because the evidence
actually supports it, not because it was assumed going in. First-half
totals and touchdown props were both genuinely investigated and
deliberately excluded from CORE_V1 on the evidence, not by default.

## Scope exclusions — what Milestone C should NOT build yet

- **First-half spread, first-quarter markets, team totals** — evidence is
  UNVERIFIED, not merely thin; do not build until directly confirmed.
- **Touchdown/player props for CFB** — confirmed self-certified but
  confirmed *not* actually rolled out this season; building a props model
  now would be pricing a market that does not exist.
- **All futures/season-long families** (national champion, conference
  champion, playoff qualifier, Heisman, AP poll, win-total ladders,
  undefeated season, coach markets, FCS champion) — genuinely confirmed to
  exist, but require season-simulation, polling, or award-prediction
  machinery that is architecturally separate from a single-game
  score-distribution engine. Building these now would blur Milestone C's
  scope before the core game engine is validated.
- **A firm default game universe assumption for FBS-vs-FCS games** —
  because coverage is UNVERIFIED either way, Milestone C should not
  hard-code an assumption in either direction; this should be resolved
  against a live Kalshi feed before the default universe is finalized.

## Genuine historical data artifacts

No machine-readable historical Kalshi payload (raw API JSON) could be
captured this session — Kalshi's API and web domains are blocked from this
execution environment, and unlike CFBD in Milestone B, no GitHub Actions
secret/workflow exists for a Kalshi API key in this mission. Per the
mission's own fallback instruction ("If no useful fixture can be
legally/technically preserved, documentation-only evidence is acceptable"),
**no fixture file was fabricated.** Every ticker, price, and quote above is
sourced to a real URL rather than represented as a captured payload. If a
live Kalshi connection becomes available in a future mission (analogous to
how the CFBD live-validation workflow was built in Milestone B), that would
be the moment to capture a genuine, sanitized fixture the same way.
