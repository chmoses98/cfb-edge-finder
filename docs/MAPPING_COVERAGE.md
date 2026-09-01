# Kalshi Mapping Coverage

What the ~1,400 unresolved markets actually are, and why the number is
not the metric.

Live audit 2026-08-28T00:43Z against main `505c6ef`.

## The question

A live run reports ~1,400 genuinely-unresolved markets out of ~4,570
discovered — a 31% "mapping failure rate" that trips a health warning.
On its own that number says nothing about whether anything is being lost:
an unresolved FCS-vs-FCS market is a population we decline on purpose,
while an unresolved FBS-vs-FBS market is a missed research opportunity.

**The metric is the FBS-vs-FBS count, not the headline.**

## Live universe

| | |
|---|---|
| Schedule games (CFBD) | 3,550 |
| Core single-game markets | 4,570 |
| Mapped FBS-vs-FBS | 1,158 |
| Classified `fcs_vs_fcs` (declined, not "failed") | 2,014 |
| Unresolved | 1,398 |
| API failures | 0 |
| Families | moneyline 680 · spread 2,286 · total 1,604 |

## Unresolved classification (reconciles exactly)

| Category | Markets |
|---|---|
| `FCS_VS_FCS_UNSUPPORTED` | 706 |
| `FBS_VS_FCS_UNSUPPORTED` | 504 |
| `DETERMINISTIC_ALIAS_MISSING` | 144 |
| `FBS_VS_FBS_POTENTIAL_LEAK` | 44 |
| **Total** | **1,398** ✓ |

`scripts/audit_mapping_coverage.py` asserts this reconciliation and exits
non-zero if it fails, so no market can quietly fall out of the accounting.

**1,210 of 1,398 (87%) are populations we deliberately do not price.**
They are unresolved only in the sense that the mapper declined them.

## The 144 "alias missing" are not FBS

Every unknown token the live run surfaced is an FCS/D2/D3 school:

```
University at Albany · St. Thomas · LIU · Winona St. · Grambling St.
Southern University · Tennessee-Martin · Kentucky State · Chicago State
Central State (OH) · Clark Atlanta · Central Connecticut St. · Nicholls St.
Southeastern Louisiana
```

They fall outside CFBD's FCS name list (152 entries, which does not cover
D2/D3) *and* outside our FBS registry — hence the label. But the label
overstates: none is an FBS team.

That is provable, not observed. The registry is the **complete 2026 FBS
universe (138 teams)**, so a name that fails to resolve cannot be an FBS
team. A test pins the count of 138, because if the registry ever drifts
from the real universe this argument stops holding and the leak figure
would understate.

**Adding aliases for these would be a defect, not a fix** — it would pull
unsupported populations into pricing.

## The one FBS-vs-FBS flag: Stanford / Miami (FL)

44 markets across three events:

```
KXNCAAFGAME-26SEP04MIASTAN     Miami (FL) / Stanford   parse_unresolved
KXNCAAFSPREAD-26SEP04MIASTAN   Miami (FL) / Stanford   parse_unresolved
KXNCAAFTOTAL-26SEP04MIASTAN    Miami (FL) / Stanford   parse_unresolved
```

Root cause, established from the schedule itself rather than inferred:

- Both raw names resolve **cleanly**: `Miami (FL)` → `miami-fl`,
  `Stanford` → `stanford`. Not an alias defect.
- The title splits correctly via the production `_split_title`. Not a
  parser defect.
- The mapper requires a candidate game whose team pair is exactly
  `{stanford, miami-fl}`. **Zero** such game exists.
- Of 11 scheduled games involving either team, **all 11 are Stanford's**
  (Hawai'i, Duke, Georgia Tech, Wake Forest, Notre Dame, Elon, NC State,
  Louisville, Virginia Tech, California, SMU). `miami-fl` appears in no
  candidate game at all, and Stanford's schedule contains no Miami
  fixture.

So Kalshi lists a market for a fixture CFBD's 2026 schedule does not
carry. **This is a schedule-source discrepancy, not a mapping defect.**

### Why it is left unresolved

Mapping it would mean inventing a `GameRecord` our schedule source does
not have. There would be no kickoff, no week, and no context for the
projection — the model cannot price a game it has no record of, and
fabricating one would corrupt the same schedule provenance every
observation depends on. The mapper's refusal is correct behaviour.

Several Stanford games also carry `kickoff: None` (TBD), so the 2026
schedule is still provisional; the fixture may appear later. It costs
nothing to wait, and the audit will reclassify it automatically once the
schedule carries it.

## Verdict

**MINOR EXPLICITLY-JUSTIFIED GAPS REMAIN.**

Zero deterministic mapping or alias defects. One FBS-vs-FBS matchup (44
markets, 3 events) unmapped for a documented schedule-source reason,
outside our code.

## What would change this

Re-run the audit when CFBD's 2026 schedule firms up:

```
python scripts/audit_mapping_coverage.py --schedule-season 2026 --json out.json
```

If `FBS_VS_FBS_POTENTIAL_LEAK` shows a matchup whose fixture **is** in the
schedule, that is a real mapping defect and should be fixed. If the count
rises while every entry is still schedule-absent, the schedule source is
the thing to investigate — not the mapper.

## 2026-09-01 follow-up: the 45% HIGH alarm and `NON_FBS_PARTICIPANT`

The scenario this document predicted arrived: Week 0's completed games
left the denominator while the declined populations persisted, and the
scheduled collector's `mapping_failures / markets_scanned` crossed the
40% HIGH threshold (1,775 / 3,923 markets; 185 / 439 events — GH Actions
run 33556291244, replayed with
`scripts/audit_production_mapping_replay.py`, which maps against the
production not-started pool with zero CFBD requests). Decomposition:

| Bucket | Markets |
|---|---|
| FBS-vs-known-FCS (`AMBIGUOUS_TEAM_MAPPING` mislabel) | 1,485 |
| Non-FBS programs under variant/D2/D3/mascot names | 246 |
| Miami (FL)/Stanford schedule-source discrepancy | 44 |

Zero mapping defects — the numerator, not the mapper, was wrong: the
`FCS_VS_FCS` carve-out requires **both** sides to be FCS, so an
FBS-vs-FCS fixture counted as a genuine failure.

The fix generalizes the carve-out: `NON_FBS_PARTICIPANT` (one side
deterministically identified as a non-FBS program via CFBD /teams, exact
match only — `teams.fcs_identity.build_non_fbs_school_name_set`, which
also carries a small table of live-verified Kalshi spelling variants such
as "Grambling St." for CFBD "Grambling"). These markets are accounted as
`markets_unsupported_population`, and the health report now also carries
`events_scanned` / `events_mapping_failed` so a single unresolved
ladder's fan-out is visible. The HIGH threshold, denominator, and
fail-closed behavior for genuinely unidentifiable names (and for the
Miami/Stanford-style schedule-absent FBS pair) are unchanged.
