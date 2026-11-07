# RUN CFB research packets — specification

`RUN_CFB.md` (repo root) is the procedure ChatGPT follows. This document specifies the files it reads. Everything lives
under `data/research_packets/` and is read through the GitHub connector as ordinary small JSON files.

> **No packet contains a model probability, a fair value, an edge, a stake or a recommendation.**
> `index.json` and every `markets.json` declare `contains_model_probabilities: false`;
> `scripts/verify_research_packets.py` and `tests/test_research_packet_*.py` fail if a contract record carries
> `fair_value`, `edge`, `stake`, `kelly` or `bet_up_to`. The packet carries evidence; the handicap is ChatGPT's.

## Layout

```
data/research_packets/
  index.json                      every slate + one row per game (flags, paths)            ~45 KB
  slates/<ET date>.json           the games of one US/Eastern slate date, full file paths   ≤ ~75 KB
  games/<game_key>/
    packet.json                   identity, data quality, football summary, QB, availability,
                                  environment, game scripts, analyst worksheet            ≤ ~75 KB
    team_profiles.json            both teams' opponent-adjusted metrics                    ~25 KB
    schedules.json                exact results + opponent quality, SOS, form, splits      ~20 KB
    distributions.json            baseline accuracy + historical reference class           ~12 KB
    markets.json (+ .partNNofMM)  every listed contract, both buy sides, all-in costs     ≤ 190 KB / part
  reference/
    history_games.jsonl.gz        1,821 past games: pre-kickoff baseline vs ESPN final + linescores
    bet_up_to_table.json          bet-up-to cents by lower-bound probability (fee-inclusive)
  sources/                        cached keyless ESPN lookups (event extras, rosters) with observation times
```

Built by `scripts/build_research_packets.py` inside `.github/workflows/script-engine.yml` (every 2 hours, right after the game
log is collected), verified by `scripts/verify_research_packets.py`. Unchanged files are not rewritten.
`--horizon-hours` (workflow: 120) bounds how far ahead packets exist; later games stay readable through the execution
index (`data/live/cfb_execution_index.json`) until their slate approaches.

## What each game packet contains

| Requested input | Where | Notes |
|---|---|---|
| Opponent-adjusted offense/defense efficiency | `team_profiles.json → teams.<side>.metrics`, `packet.json → football.matchup_comparison` | each metric: `raw`, `adjusted`, `standard_error`, `rank` among FBS, `games`, `quality`. Offense vs the *actual* opposing defense in both directions |
| Success rate (overall, early-down, passing, rushing, third down) | `metrics.efficiency`, `.passing`, `.rushing` | **success rate stands in for EPA** (see gaps) |
| Explosive-play creation and prevention | `metrics.explosiveness` | offense = created, defense = allowed |
| Red-zone / scoring efficiency | `metrics.overall_scoring`, `.finishing_red_zone` | points per drive, points per scoring opportunity (a drive reaching the opponent's 40) |
| Pressure, sacks, line and front | `metrics.pressure_and_protection` (adjusted sack rate allowed, turnovers); `team_profiles → raw_defensive_counts_unadjusted` (sacks, hurries, TFL, PD per game) | the raw counts are labelled unadjusted |
| Turnovers and possession efficiency | `metrics.pressure_and_protection`, `metrics.overall_scoring`, `pace_and_tendencies.drives_per_game` | |
| Exact schedules and results | `schedules.json → teams.<side>.games[]` | every completed game: opponent, site, score, opponent adjusted net-rating rank, opponent offense/defense rank, points vs the adjusted expectation, plays, yards/play, success rate, third down, turnovers, sacks, drives, passer |
| Quality of offenses/defenses faced; strength of schedule | `…strength_of_schedule` | average opponent ranks, games vs non-FBS / bottom-third / top-third, a `flag` for soft or hard schedules |
| Performance against similar opponents | `…against_similar_opponents` | games vs opponents in the same adjusted-net-rating tier as the upcoming opponent, with `small_sample_warning` |
| Inflated stats from weak competition | `raw` vs `adjusted` on every metric; `points_vs_adjusted_expectation` per game; `strength_of_schedule.flag` | |
| Recent form without over-weighting | `schedules.json → recent_form` | last 3 vs season, in raw and opponent-adjusted terms, `small_sample_warning` |
| Pace, plays, expected possessions, tendencies | `football.pace_and_tendencies` | plays/game, seconds/play, pass rate, neutral pass rate, drives/game, `expected_possessions_per_team` |
| Home/away splits, rest | `football.home_away_splits`, `football.rest_and_situation` | raw splits with `n`; days since last game, short week, bye |
| First-half / first-quarter team splits | `football.period_splits_raw` | raw, unadjusted, over games with captured linescores (`games`) |
| Weather and stadium | `environment` | venue, surface, city/state; ESPN forecast (temperature, gust, precipitation probability) when published |
| Coaching / personnel | `availability.head_coaches`, `qb.<side>.roster_quarterbacks`, `availability.roster_feed` | head coach from ESPN; QB room; roster-flagged athletes |
| QB: projected vs confirmed | `qb.<side>` | `projected_starter` (from the most recent box score), passer log, `changed_in_last_3_games`, `season_attempts_by_passer`; `confirmed_starter` is **null** by design — a confirmed starter needs analyst research |
| Injuries, suspensions | `availability` | ESPN listing with quality note; `NO_STRUCTURED_INJURY_SOURCE` always raised; research required |
| Scoring baseline and its limits | `football.baseline` | descriptive, uncalibrated; `accuracy` shows its measured bias, MAE and the naive league-total MAE beside it |
| Historical distributions + comparables | `distributions.json` | empirical quantiles and exceedance tables for margin, total, each team's points, 1H, 2H, 1Q among past games with a similar baseline; the 12 closest games by name; league-wide shape for contrast |
| Game scripts | `packet.json → game_script` | dominant / competitive / upset scripts from the deterministic engine, with the findings and causal chain behind each, and the data confidence |
| Every Kalshi contract | `markets.json` | below |

### `markets.json`

* `contracts[]` — per contract: `ticker`, `family`, `period`, `yes_means`, `line`, `kind`/`team`/`side` (what quantity the
  YES outcome depends on), `book`, and for **each** side (`yes`, `no`): `ask`, `ask_size`, `bid`, `exec` (positive quoted size at
  capture), `contracts_1u`, `order_fee_1u`, `all_in` (= fee-inclusive break-even probability for that side), plus `upd` (when
  Kalshi last changed it), `ladder`, `alt`, and `ref_freq` (comparable-game frequency of YES — *descriptive, not a fair value*).
* `events{}` — settlement meaning once per event: `rules_primary_example`, `rules_secondary`, `settlement_sources`, `fee_model`,
  `fee_multiplier`, `close_time`, `expected_expiration_time`, `settlement_timer_seconds`.
* `families{}` — for each family present: contracts, **coverage** (`SUPPORTED` / `PARTIAL` / `GAP`), what to estimate, which
  packet inputs bear on it, and the **named gaps**.
* `ladders{}` — spread / total / team-total rungs grouped and sorted by line, with the monotonicity rule.
* `thesis_groups` — families that express the same underlying view, so every expression is compared.
* `price_provenance` — `CAPTURED_NOT_LIVE_VERIFIED`, the capture time and the `game_fingerprint` to compare with
  `data/live/execution/games/<game_key>/quotes.json`. A mismatch means the quotes file is newer.
* `pricing_basis` — the buy-side rules (NO = NO ask = 1 − YES bid), the fee formula, what the fee excludes.

Prices, sizes and fees are copied unchanged from the execution layer the catalog already publishes; nothing is re-fetched
from Kalshi and no price is modelled. `markets.json` is rebuilt with the packets (every ~2 h); the execution `quotes.json`
is refreshed more often, which is why the fingerprint check exists.

## Data-quality flags (`packet.json → data_quality.missing_data_flags`)

| Code | Severity | Meaning | ChatGPT's action |
|---|---|---|---|
| `MARKET_DISCOVERY_INCOMPLETE`, `MARKET_FILE_PARTIAL`, `FOOTBALL_IDENTITY_UNRESOLVED` | BLOCKING | do not recommend from this game as built | report unavailable |
| `QB_NOT_CONFIRMED` | MAJOR | always: only a projected QB exists | confirm both starters with sources |
| `QB_CHANGE_RECENT` | MAJOR | more than one primary passer in the last 3 games | find out why and who starts |
| `NO_STRUCTURED_INJURY_SOURCE` | MAJOR | always: no usable keyless CFB injury feed | research absences with sources |
| `FOOTBALL_HISTORY_ABSENT` | MAJOR | no box-score history for a team (non-FBS vs non-FBS) | research independently or pass |
| `FOOTBALL_LOG_INCOMPLETE`, `THIN_SAMPLE`, `REFERENCE_CLASS_THIN_OR_MISSING` | MAJOR | adjusted numbers lean on a prior / thin reference class | widen ranges |
| `WEATHER_UNAVAILABLE` | INFO | no forecast block (not for indoor venues) | check the forecast if outdoors |
| `EPA_UNAVAILABLE`, `PERIOD_SPLITS_RAW_ONLY` / `PERIOD_SPLITS_THIN` / `PERIOD_TEAM_SPLITS_UNAVAILABLE`, `SCHEDULE_QUALITY` | INFO | documented coverage limits | account for them |

`index.json` / slate rows carry `flag_codes` and `blocking_flags`, so a reader sees the picture before opening a game.

## How the opponent adjustment works (unchanged engine, now exposed)

Per metric, a ridge-regularised fit estimates league baseline, offense effect, defense effect, home-field effect and an FCS
group offset from **every completed game before the data cutoff** (`docs/SCRIPT_ENGINE.md` §4). A team's `adjusted` value is
what it would do against an average opponent; `rank` orders FBS teams on it; early-season values shrink toward the prior
(`prior_weight`). `raw` is always published beside `adjusted`; raw is never substituted for adjusted. The packet reuses this
fit (`scripting.adjust.fit_league`) — it adds no second model.

## Coverage gaps, stated plainly

* **EPA** is not available keylessly (CFBD needs a key and is unreachable from the collection environment). Success rate,
  explosive rate, points per drive and per scoring opportunity are provided; none is renamed EPA.
* **Confirmed QB and injuries** have no keyless source. ESPN's core injury list returns references that were never followed
  (every committed row was null); `collect_football_gamelog.py` now follows them (`resolve_injury_item`) — but the feed is still
  nearly empty for CFB, so the packet always raises the research flags.
* **Opening-drive tendencies, travel distance, possession splits by half** are not captured.
* **Non-FBS vs non-FBS games** have final scores only.
* **Player props**: none are listed in the current catalog; if one appears its family is `GAP`.
* **Quotes** are captured, not live.
