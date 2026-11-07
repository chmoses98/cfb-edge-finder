# RUN CFB audit — why a session stopped before producing useful recommendations (2026-11-07)

**Question:** when the operator says RUN CFB, why did the session repeatedly end with prices but no football evidence, no
defensible bet-up-to price and no stakes (Nov 6, 2026: New Mexico at Nevada, TCU at Arizona, and the evening slate)?

Evidence below is from the repository at `5738ddfae` (2026-11-07 00:25 UTC), the committed data files, and live requests
made from the audit environment. Nothing is assumed from older documents; where a document and the data disagree the data is
reported.

## 1. What football information already existed

| Asset | What it holds | Reachable by a GitHub-connector session? |
|---|---|---|
| `data/football/2026/team_games.jsonl` | ESPN box score + play-log counts per team-game (1,244 rows at audit start) | **No** — 1.74 MB; the connector returns empty content above 1 MB |
| `data/football/2026/schedule.json` | every event, scores | **No** — 0.72 MB and not indexed anywhere a session is told to look |
| `data/scripting/live/frozen/<key>.json.gz` | the Script Engine's opponent-adjusted matchup profile, findings, scripts (~124 KB each) | **No** — gzip binary; a text connector cannot read it |
| `data/scripting/live/sift/*.json.gz` | same, merged with the market map | **No** — gzip |
| `cfb-handicap-batches` / `cfb-analysis-artifacts` | factual context batches + the full market menu | **No** — exist only as **workflow-run artifacts** (`cfb-execution-slate.yml` runs `contents: read` and uploads; nothing is committed). Retrieval needs the Actions artifact API and ZIP download |
| `app/latest/event_detail/*.json` | exported Kalshi markets | prices only: `context` holds identity/completeness, `model_prices` is empty, `event_research` is `{}` |
| `data/live/execution/games/<key>/quotes.json` | every contract's asks, sizes, fees | **Yes** — and this is the only thing a connector session reliably got |

**Root cause #1 (access):** the one path a session could read — the execution index — carries market data only. Every
football asset that existed was either over the connector's 1 MB limit, gzipped, or an Actions artifact. RUN CFB was told to
fetch the artifacts ("`cfb-handicap-batches` … through the GitHub connector"), and when that failed the only honest outcome
was prices without football context — i.e. the failure observed.

## 2. What was wrong or missing, with evidence

| # | Finding | Evidence |
|---|---|---|
| 1 | **Football log stale by days.** The committed log ended `2026-11-03T07:58Z` (622 games). Refreshing it from ESPN on Nov 7 added games the engine's last build never saw. The frozen UNM–NEV packet was generated `2026-11-03T08:00Z`: its adjusted numbers pre-dated the Nov 3–5 results | `data/football/2026/manifest.json` at `HEAD`; `git log -- data/football` last commit 2026-11-04 |
| 2 | **Injury data mapped incorrectly (always null).** ESPN's core injury list returns `{"items":[{"$ref": …}]}`; the collector read `athlete`/`status` off the stub. 139 teams observed, **5 listed anything, 0 rows had an athlete name**, 0 had a QB | `data/football/2026/availability.json` at `HEAD`; `scripts/research_export.py:2487` already notes "unresolved `$ref` URLs" |
| 3 | **No structured CFB injury source exists keylessly.** `site.api…/college-football/injuries` lists 3 stale entries (2020–2022); `/teams/<id>/injuries` and `/teams/<id>/depthcharts` return empty objects | live requests, 2026-11-07 |
| 4 | **Starting QB identity not tracked anywhere** (`starting_qb`, `depth_chart` appear in zero source files) — confirmed in the Week-1 audit and still true | `docs/WEEK1_FOOTBALL_INPUT_AUDIT.md`; `pit_review` note `QB_STATUS_NOT_TRACKED` |
| 5 | **Weather effectively never collected.** `open_meteo_environment()` exists but nothing calls it; the context collector only reads a weather block off the *scoreboard* event, which ESPN does not publish. ESPN's **summary** `gameInfo.weather` does carry a forecast for upcoming games and was unused | `grep` across `src/` and `scripts/`; live summary for event 401864533 |
| 6 | **First-half / first-quarter evidence nearly absent.** 94 of 1,244 team-game rows carried `period_points`, so 1H/1Q pricing stayed refused; none of the 20 live Kalshi families carried a stated mapping to football inputs (quarters, halves, team stat props, first-TD, overtime and margin bands were only ever priced if a session already knew how) | `team_games.jsonl`; family count across `data/live/execution/**/quotes.json` |
| 7 | **No historical outcome distributions reached a session.** The scoring-baseline validation, 1,800+ baseline-vs-actual records and the error tables exist under `data/scripting/validation/` (gz, large), so a session had to invent a score and a normal curve | `docs/SCORING_BASELINE_VALIDATION.md`; `records_2024..2026.jsonl.gz` |
| 8 | **The baseline's accuracy limits were not shown with it.** Total-points MAE is 12.8 vs 13.1 for guessing the league-average total; margin MAE 13.7 | recomputed over 1,821 games in `history_games.jsonl.gz` |
| 9 | **Contradictory instructions.** `RUN_CFB_CONTRACT.md` step 7 said "Handicap the game OUTSIDE the repository … Nothing in this repository helps you here" while the engine produced exactly that evidence. A session followed the contract and went looking elsewhere | `docs/RUN_CFB_CONTRACT.md` before this change |
| 10 | **No deterministic path from a probability to a price limit inside the session.** `fair_value`/`pit_review` existed only in the terminal `candidates` CLI, which the handoff says is optional and needs the batch artifacts and a handicaps file | `docs/RUN_CFB_CONTRACT.md` "ChatGPT full-slate handoff" |
| 11 | **Scoreboard collector depended on two hosts.** `site.web.api.espn.com` and `cdn.espn.com` were unreachable from a restricted network; the sweep then spent minutes in retry sleeps and wrote nothing. `site.api.espn.com` answered the same envelope | reproduced; fixed (third host) |

## 3. What is genuinely opponent-adjusted (and what is not)

* **Adjusted — real:** every `metric_row` is the Script Engine's ridge-regularised league fit
  (`scripting/adjust.py`): offense, defense, home-field and FCS-group effects estimated jointly over all completed games before
  the data cutoff, shrunk toward a prior in thin samples, with standard errors and FBS ranks. Offense and defense are adjusted
  separately, so a defense is credited for the offenses it faced. `raw` and `adjusted` differ materially (e.g. New Mexico raw
  35.0 vs adjusted 30.2 points per game against its soft schedule).
* **Not adjusted (and labelled so):** pass rate, neutral pass rate, explosive-yard share; raw home/away and period splits;
  raw defensive counts; last-3 form (shown beside an adjusted-expectation residual).
* **Not available:** EPA per play. No keyless source; the efficiency measures are success rate, explosive rate, points per
  drive and per scoring opportunity.
* **Strength of schedule** is the average adjusted net-points rank of opponents faced, with explicit soft/hard flags and
  counts of non-FBS / bottom-third / top-third opponents.

## 4. Which Kalshi markets lacked football inputs

Live catalog (257 games, 20 families). Before this change none had a stated football input. Now each family has a guide
(`markets.json → families`): `SUPPORTED` — game moneyline, spread, total, team total, winning margin; `PARTIAL` — first-half,
second-half and quarter families (full-game-baseline reference class + raw team period splits; no opening-drive data),
team-stat props, first-TD, overtime; `GAP` — game stat props (DST touchdown), unlabelled combos, any player prop (none listed
today). Every gap is named in the packet.

## 5. What was changed

See the pull request. In short: committed per-game packets under `data/research_packets/` (indexed, ≤250 KB per file, no
artifacts, no gzip); injury `$ref` resolution; ESPN venue/weather/roster/coach lookups; historical reference classes with
linescores for 1,821 games; per-contract all-in cost and break-even; `RUN_CFB.md`; a deterministic price calculator;
verifier and regression tests; a third scoreboard host; and the Script Engine workflow now builds the packets.
