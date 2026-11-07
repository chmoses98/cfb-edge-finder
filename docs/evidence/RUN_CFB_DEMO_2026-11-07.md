# RUN CFB hand-off demonstration — 2026-11-07 (real data, real connector read)

Run at ~02:50 UTC Nov 7 against branch `claude/run-cfb-handicapping-system-xs4rkz`. **This demonstrates that the evidence
is reachable and complete enough to handicap. It is not a betting recommendation**: the analyst figures in §3 are
illustrative estimates written to exercise the arithmetic, no web research (confirmed QB, injuries) was possible in this
environment, and both test games were at or minutes from kickoff.

## 1. Retrieval through the GitHub connector (what ChatGPT does)

| Read (GitHub MCP `get_file_contents`, ref = the branch) | Result |
|---|---|
| directory `data/research_packets/games/26NOV06TCUARIZ` | `packet.json` 59,506 B · `team_profiles.json` 24,831 B · `schedules.json` 18,584 B · `distributions.json` 12,115 B · `markets.json` 183,733 B — all far under the 1 MB connector limit, no ZIP, no artifact, no upload |
| `data/research_packets/games/26NOV06UNMNEV/schedules.json` | full text returned: 8 games per team with opponent adjusted rank, success rate, period scores, passer, SOS flag, similar-opponent record, home/away and 1H/1Q splits |

Local equivalent for the whole index (`scripts/verify_research_packets.py --strict`): **123 games, 0 errors, 0 warnings**; every
game's `markets.json` contract count equals the execution index's `market_count`, and its `game_fingerprint` equals the
current `quotes.json` fingerprint (TCU–Arizona `9c8f4daf8d21…` both).

## 2. What the TCU at Arizona packet gives (selected, all from the committed files)

* **Matchup, opponent-adjusted (rank of 138 FBS):** Arizona offense success rate 44.3% adj (raw 47.4%, rank 33) vs TCU defense
  38.3% allowed (rank 23); TCU offense 39.8% (raw 40.8%, rank 96) vs Arizona defense 36.5% allowed (rank **6**). Explosive-play rate:
  Arizona offense 12.2% (rank 61) vs TCU defense 11.0% (42); TCU offense 10.5% (rank 111) vs Arizona defense 9.4% (rank **6**).
  Points per scoring opportunity: TCU offense 3.65 (rank 126). Raw and adjusted differ; both are published with standard errors.
* **Schedules:** every game of both teams with opponent net-rating rank, `points_vs_adjusted_expectation`, SOS flag, record
  against the same tier as the opponent, last-3 vs season with a small-sample warning.
* **QB (projected, not confirmed):** Arizona Noah Fifita, TCU Jaden Craig, no change in the last three games; `QB_NOT_CONFIRMED`
  raised — a session must confirm both by web research.
* **Environment:** Tucson, grass false; ESPN forecast 64°F, gust 6 mph, **54% precipitation probability**.
* **Pace:** expected possessions ≈ 11.5 per team; raw 1H/1Q splits for both teams over 8 captured linescores.
* **Scoring baseline (MODEL_PROJECTION, uncalibrated):** Arizona by 9.4, total 40.7, shown beside its measured accuracy
  (total MAE 12.8 vs 13.1 for a league-average guess).
* **Historical reference class (128 past games with a similar baseline):** actual home margin median +8, p10 −17, p90 +33
  (sd 19.2); actual total median 45, p10 30, p90 66; 1H total median 23; 12 named closest games.
* **Kalshi menu:** 252 contracts across 15 families (moneyline, spread, total, team total, 1H/2H, four quarters, 1H team total,
  first-TD, overtime), each with YES/NO asks, sizes, 1U order fee, all-in cost, and the family's coverage status and gaps.

## 3. Arithmetic on illustrative analyst figures (exercise of `scripts/cfb_price_limits.py`)

| Contract | Side | Ask | All-in (1U) | Illustrative low/point/high | Break-even | Bet up to | Result |
|---|---|---|---|---|---|---|---|
| `KXNCAAFGAME-26NOV06TCUARIZ-ARIZ` | YES | 0.71 | 0.7245 | 0.68 / 0.74 / 0.79 | 72.4% | 66¢ | lower bound −4.5pp: **pass at 71¢** |
| `KXNCAAFTOTAL-26NOV06TCUARIZ-44` (over 43.5) | NO | 0.41 | 0.4270 | 0.45 / 0.52 / 0.58 | 42.7% | 43¢ | lower bound +2.3pp: 1U tier, conditional lean only (`qualified_positive_ev` false) |

Note the NO price used is the NO ask (0.41), not 1 − the YES ask (0.40); the fee is the order-level cent-rounded taker fee.

## 4. Full-slate coverage (Saturday Nov 7, 111 games, `scripts/report_research_coverage.py --date 2026-11-07`)

* 12,390 contracts; **12,124 priced on both buy sides with an all-in cost**; 266 have an empty or one-sided book (reported, not hidden).
* 11,425 contracts carry a historical reference-class frequency.
* Same depth for every game: markets 111/111 `OK`; matchup comparison + schedules + distributions available for **93 of 111**.
  The other **18 are non-FBS vs non-FBS games**: no box scores exist in the log, so they carry `FOOTBALL_HISTORY_ABSENT`, a results-only
  schedule, and no baseline/distributions. This is a remaining gap, stated in each of those packets.
* Flags on every game: `QB_NOT_CONFIRMED`, `NO_STRUCTURED_INJURY_SOURCE`, `EPA_UNAVAILABLE` — the research a session must still do.
* Market coverage by contract: 5,382 `SUPPORTED`, 6,937 `PARTIAL` (halves, quarters, stat props, first TD, OT), 71 `GAP`.

## 5. Not demonstrated (environment limits)

* A live ChatGPT session. The read path was exercised with the GitHub connector and the verifier, not with ChatGPT itself.
* The new injury `$ref` resolution against the live ESPN core API (blocked from this sandbox); it is covered by unit tests with
  recorded-shape payloads and will be exercised on the next Script Engine run.
* The workflow wiring (`script-engine.yml`) — YAML is unchanged apart from two steps and cannot be run here.
