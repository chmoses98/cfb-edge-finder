# RUN CFB hand-off evidence — 2026-11-07 (real data, real connector reads, real GitHub Actions)

This is evidence that the evidence is reachable and complete enough to handicap. **It contains no betting recommendation.**
Probabilities below were supplied by testers for the arithmetic, no web research (confirmed QB, injuries) was possible in the
test environments, and several games had kicked off.

## 1. A real GitHub Actions run of the packet workflow

`CFB Research Packets` run **42343863326** on branch `claude/run-cfb-handicapping-system-xs4rkz` (head `5767132`), all steps green:

| Step | Result |
|---|---|
| Refresh the game log into a scratch copy | success: `633 games / 1266 rows … fetched 9 this run; missing 0; failures 0; availability OBSERVED 143` |
| Build the research packets | success: `117 games, 384 files written, 84 unchanged` |
| Verify every expected packet (strict) | success: `verified 117 games: 0 errors, 0 warnings` (contract counts equal the execution index, `markets.json` fingerprints equal `quotes.json`, sizes, owner rules, every game inside the 120 h horizon has a packet) |
| Commit the packets only if they changed | success: bot commit `4f3c057` (only `data/research_packets/**`), rebase-retry push |

Every changed file in that commit changed in content (the league fit moved because four more games finished); none was a
timestamp-only rewrite, so an unchanged slate commits nothing.

**Why it is a separate workflow.** The first design ran inside `script-engine.yml`. Run **42340958366** (this branch) and every
recent `main` run of that workflow (Nov 5–7) were **cancelled at the 45-minute job timeout inside `script_engine.py build`**:
all later steps skipped, nothing committed, the ledger step never reached. That is a pre-existing problem on `main` (also why
`data/football` went four days stale) and is not changed here; `script-engine.yml` is restored to `main` and the prospective
ledger is untouched.

**Visible failure.** The packet workflow has no `continue-on-error` on build, verify or commit: a failure turns the run red and
commits nothing, so the last good packets stay. A failed game-log refresh is a `::warning` annotation and a step-summary line.
A first push of the workflow was itself rejected by GitHub as invalid YAML (an unquoted `: ` in a step name); that is fixed and a
guard test added.

## 2. Saturday November 7 hand-off, read through the GitHub connector

An independent tester session read **only through `mcp__github__get_file_contents`**, following `RUN_CFB.md` literally
(commit `a9d1092`, before the later fixes):

* Slate 2026-11-07: **111 games** (early 42 / afternoon 46 / evening 23); index, slate and execution index agreed on counts and
  fingerprints; no game had blocking flags.
* Three FBS games, one per window (Clemson at Syracuse, Alabama at LSU, Louisville at Georgia Tech): contract counts equal the
  slate row and the execution index (249 / 300 across two market parts / 260); `markets.json` fingerprints equal `quotes.json`;
  moneyline sides match the packet's home/away; PARTIAL and GAP families (1H/2H/quarters/overtime/first-TD partial; game stat
  prop and unlabelled combos GAP) were named; QB, injuries and weather research flags were listed.
* The tester wrote its own football view from the packet evidence **before** looking at asks, then derived fee-inclusive
  break-evens and bet-up-to ceilings from its **lower bounds** (hand arithmetic from `RUN_CFB.md` §4 and the lookup table).
  Cross-checked against `scripts/cfb_price_limits.py`: **all six contracts matched exactly**. Result: five of six points and all six
  lower bounds sat below break-even at the captured asks — correctly reported as passes, stakes unsized, nothing called validated.
* Tester findings (all addressed in the final head): misleading "recommendation" label on a pass → `PASS_AT_THIS_ASK`; ambiguous
  part naming and stale file sizes in `RUN_CFB.md`; missing freshness rule; ticker suffix is the rounded-up line (use `line`);
  undecoded weather codes and null `indoor`; no FBS-vs-FBS field → `football_history`; a mixed hard/soft schedule mislabelled soft →
  `SCHEDULE_MIXED`; no guidance when the baseline, ESPN and the reference class disagree.

Slate coverage (`scripts/report_research_coverage.py --date 2026-11-07`): 12,390 contracts, **12,124 priced on both buy sides with an
all-in cost**, 266 with an empty or one-sided book (reported); 11,425 carry a reference-class frequency; matchup, schedules and
distributions available for 93 of 111 games — the other **18 are non-FBS vs non-FBS** (`FOOTBALL_HISTORY_ABSENT`, results-only
schedule, no baseline). Market coverage by contract: 5,382 `SUPPORTED`, 6,937 `PARTIAL`, 71 `GAP`.

## 3. Integrity checks run

* **Time cutoffs:** every stored baseline record has `cutoff < kickoff`; 244 stored 2026 baselines reproduce **exactly** (max difference
  0.0) when rebuilt today from only the games before each cutoff, and no history row is at or after its cutoff or is the game itself.
* **Misattribution found and fixed:** two 2024 neutral-site bowl linescores were attached with home and away swapped
  (UTSA/Coastal Carolina, USC/Texas A&M); linescores now attach only when they reconcile to the row's final score, re-oriented by
  score if swapped (2 re-oriented, 0 dropped).
* **Identity/orientation:** all 123 identities resolved by team identity, none swapped on this slate; contract sides follow the football
  game's orientation even when a swap occurs (unit-tested); every one of 246 moneyline contracts names the team its `side` says.
* **Adjustment:** across 117 teams, adjusted-minus-raw scoring correlates negatively with the rank of the defenses faced (−0.36), as
  opponent adjustment should.

## 4. Not demonstrated

* A live ChatGPT session in this PR's final state (ChatGPT read the TCU–Arizona packet through the connector in an earlier session,
  per the owner; the tester above used the same GitHub connector tool from Claude Code).
* The injury `$ref` resolution against live ESPN core data: the real run reported `availability OBSERVED 143` but ESPN's feed is nearly
  empty for CFB; the packets still raise `NO_STRUCTURED_INJURY_SOURCE` on every game.
