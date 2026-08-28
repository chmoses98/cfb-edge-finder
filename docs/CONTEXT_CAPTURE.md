# Research-Only Contextual Capture

Prospective, source-attributed records of football context the model does
**not** use — captured so that a future ablation can *measure* whether
those factors explain model-market error, instead of us assuming they do.

## The invariant

> No field captured here may ever influence `GameDistribution`,
> `model_probability`, `projected_margin`, or `projected_total`.

Enforced structurally, not by intention:

- No module under `modeling/`, `projections/` or `ratings/` imports
  `research/context_capture.py` — asserted by parsing imports.
- `context_capture.py` imports none of them either, so it cannot pass a
  value in through a call.
- None of the four protected outputs appears as an assignable name
  anywhere in the module.
- A behavioural test runs the real continuity classifier before and after
  building a context record and compares.

This is the same discipline that keeps `cfb_edge_finder.sizing`
disconnected: an absent capability, not a policy.

## Why capture at all

`docs/WEEK1_FOOTBALL_INPUT_AUDIT.md` establishes that a Week 1 point
estimate carries **no 2026 information** — `season_carryover_weight(0)`
is exactly 0.0. QB identity, injuries, transfers and coaching changes are
invisible to it.

The tempting response is to start adjusting probabilities. That is
handicapping by intuition, and it destroys the only clean measurement
available: whether those factors actually explain the errors. So we
record and change nothing.

The registered question (protocol §1a):

> Do model-market disagreements concentrate on teams with known 2026
> roster, coaching, or quarterback churn?

It can only be answered later with data that had to be captured *before*
the games — which is why the plan, including its gaps, exists now.

## Field plan and honest gaps

| Field | Source | Availability | Note |
|---|---|---|---|
| `qb_continuity_proxy` | CFBD returning production | `DERIVED_PROXY` | Team-level returning passing PPA. **Not** QB identity. |
| `expected_starting_qb` | — | `SOURCE_UNAVAILABLE` | No reproducible depth-chart feed is wired. |
| `qb_new_starter_flag` | — | `SOURCE_UNAVAILABLE` | Needs QB identity. The proxy is not a substitute. |
| `material_injury_status` | — | `SOURCE_UNAVAILABLE` | CFB has no mandatory injury report and no structured API. |
| `head_coach_change` | CFBD `/coaches` | `NOT_YET_CAPTURED` | Answerable season-over-season; planned, not wired. |
| `weather_snapshot` | NWS/NOAA | `NOT_YET_CAPTURED` | Free, no key. Forecast-oriented, so it must be captured prospectively. |
| `venue` | CFBD `/games` | `OBSERVED` | From the game record. |
| `neutral_site_flag` | CFBD `/games` | `OBSERVED` | The one contextual input the model already uses correctly. |

### Availability states, and why there are four

- `OBSERVED` — a real value from a named, reproducible source.
- `DERIVED_PROXY` — computed from something adjacent. Reported, but never
  readable as the thing itself, and not usable evidence on its own.
- `NOT_YET_CAPTURED` — a source exists and is wired; this record predates
  the capture.
- `SOURCE_UNAVAILABLE` — no dependable source exists. A recorded gap.

The last two are deliberately distinct, for the same reason
`market_status` distinguishes a legacy row from a current-schema defect:
one is a scheduling fact, the other is a limitation of the world.

### Source discipline

Only reproducible, attributable sources. Explicitly **not** blogs,
aggregator scrapes, or beat-writer speculation. A value that cannot be
reproduced later is worse than a missing one, because it looks like data.

Where no dependable source exists, the gap is recorded rather than
filled. Publishing the gaps is the point: an unlisted missing field looks
like an oversight; a listed one is a known limitation with a reason.

## Prospectivity

A record is prospective only when `capture_mode == "PROSPECTIVE"` **and**
`captured_at < kickoff_utc`. Checked, not assumed — a record built after
kickoff is not prospective evidence no matter what it is labelled.
