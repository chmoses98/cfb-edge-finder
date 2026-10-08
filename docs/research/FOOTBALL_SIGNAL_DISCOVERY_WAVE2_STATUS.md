# Football Signal Discovery Lab — Wave 2 (CFB): status

**PROSPECTIVE TRACKING. NO SETTLED SAMPLE.** The population starts with games kicking off on/after
2026-10-10T00:00:00Z. No population game had kicked off when this file was written.

| Stream | Verdict | Settled n | Next read |
|---|---|---|---|
| CFB-PROS-001 RUSHING_EDGE_SPREAD | PROSPECTIVE_TRACKING | NO SETTLED SAMPLE | EARLY_READ at 50 |
| CFB-PROS-002 STRONG_CONTROL_MARKET_SKEPTICISM | PROSPECTIVE_TRACKING | NO SETTLED SAMPLE | INTERIM at 50, PRIMARY_REVIEW at 100 |

## Where the live state is

The research conductor (`.github/workflows/cfb-research-conductor.yml`) publishes these files on the
`research-signals` branch:

| File | What it holds |
|---|---|
| `wave2/reports/2026/wave2_status.json` | Cumulative per-stream state: observation, entry and settlement status counts with reasons, verdict, next read, residual / ATS-like / economics summaries, the PROS-001 feature distribution next to the Wave-1 mean/SD, and capture health |
| `wave2/2026/ledger.jsonl` | Append-only OBSERVATION / ENTRY / SETTLEMENT rows |
| `wave2/2026/spread_attempts.jsonl`, `spread_quotes.jsonl` | Every KXNCAAFSPREAD attempt (OK / NO_MARKET / REQUEST_FAILED) and its rungs |
| `wave2/2026/cycles.jsonl` | One line per cycle that wrote something or failed, including any isolated `SYSTEM_FAILURE` |

Pushes to feature branches write to `research-signals-dev` only.

## Current-slate eligibility dry run (2026-10-08T22:43Z, no outcomes)

`scripts/signal_lab_wave2_dry_run.py` runs over the next 60 h with the main-branch catalog snapshot (captured
2026-10-08T14:06Z). Full output: `data/scripting/validation/signal_discovery_wave2/dry_run_eligibility_2026-10-08.json`.

This is **not** a checkpoint. Entries come only from the conductor's PRIMARY_60_180 capture.

| Item | Count |
|---|---|
| Population games in the horizon | 94 |
| PROS-001 qualifying (\|z\| ≥ 1) | **14** |
| PROS-001 below threshold | 62 |
| PROS-001 feature-ineligible (no prior history; mostly FCS/FCS) | 17 |
| PROS-001 identity failure | 1 |
| PROS-002 STRONG CONTROL candidates | 12 (from the latest V2 publication; FINAL_PREGAME rows arrive ~3 h before kickoff) |
| … of which the current CONTROL-side implied margin is < +3 | 1 |
| Spread ladders present / with a computable center | 91 / 91 |
| Winner-market orientation resolved | 93 |

Observed PROS-001 feature distribution on 2026 games so far: mean −0.51, SD 1.25. The Wave-1 (2014–2025) values
are mean +0.14, SD 1.42. This is the declared source difference (production ESPN game log vs CFBD box scores).
It is reported, never re-standardised.

## Verification

* `tests/test_signal_discovery_wave2_cfb.py`: 41 tests. These add the protocol §44 properties: exact 180/60 inclusive boundaries, no capture at or after kickoff, no outcome in pregame rows, a deterministic rerun across stores, duplicate captures unable to inflate n, interim results unable to alter eligibility, and closing context that stays missing. Full suite: 3,844 passed, 3 skipped. `ruff check` clean.
* The conductor's Wave-2 step runs in a try/except. A failure is logged as SYSTEM_FAILURE and the CONTROL capture,
  watchdog, H1/H2 settlement and the SIFT contract are unaffected (tested).
* SIFT's `cfb_research_signals.json` carries no Wave-2 field (tested). The CFB Value Watch is untouched.
