# Week 1 Football-Input Freshness Audit

**Audit only.** Nothing in this document changes a probability, a
coefficient, or a projection. It establishes what the live 2026 model
actually knows before Week 1, so that model-market disagreement can be
interpreted honestly rather than mistaken for edge.

Audited against `main` at `55e9e6e`, 2026-08-28.

---

## The headline finding

> **In Week 1, the model's point estimate contains no 2026 information at all.**

`blend_team_rating` weights the current season against the prior season by
`season_carryover_weight(games_played_this_season, k=4)`. Verified by
execution:

| Games played this season | Weight on current season |
|---|---|
| **0 (all of Week 1)** | **0.0000** |
| 1 | 0.2000 |
| 2 | 0.3333 |
| 4 | 0.5000 |
| 8 | 0.6667 |

At `games_played = 0` the weight is exactly zero, so
`offense = 0 × current + 1 × prior_season`. **Every Week 1 projected
margin and total is a function of 2025 end-of-season ratings.**

This is correct by design — there is no 2026 on-field evidence yet, and
inventing one would be worse. But its consequence must be stated plainly:

> A large Week 1 model-market disagreement is **more likely** to be the
> market pricing 2026 information the model structurally cannot see
> (a transfer QB, a new coach, an injury) than to be an edge.

The single most valuable Week 1 research question is therefore not
"where is the edge" but "**do the disagreements concentrate on teams with
known 2026 roster churn?**" That is a preregistered ablation question
(see `docs/PROSPECTIVE_RESEARCH_PROTOCOL.md`), not a reason to adjust
anything now.

---

## Input-by-input audit

| Input | Used in model? | Captured? | Source | Timestamped? | Leakage-safe history? | Live source available? | Risk |
|---|---|---|---|---|---|---|---|
| **Starting QB identity** | **NO** | NO | — | — | — | Not without a depth-chart feed | **HIGH** |
| QB continuity (proxy) | Uncertainty only | Yes, via ratings build | CFBD `/player/returning` | Season-level | Yes (preseason publication) | Yes | MEDIUM |
| QB injury | **NO** | NO | — | — | — | No structured API | **HIGH** |
| Transfer portal | **NO** (except passing proxy) | NO | — | — | — | CFBD licensed endpoints | **HIGH** |
| Returning production (non-passing) | **NO** | NO | CFBD `/player/returning` | Season-level | Yes | Yes | MEDIUM |
| Recruiting / talent | **NO** | NO | — | — | — | CFBD talent endpoint | MEDIUM |
| Head coach change | **NO** | NO | — | — | — | CFBD `/coaches` | **HIGH** |
| Coordinator change | **NO** | NO | — | — | — | Partial at best | MEDIUM |
| Injuries / suspensions (non-QB) | **NO** | NO | — | — | — | No structured API | MEDIUM |
| Weather | **NO** | NO | — | — | — | NWS/NOAA (free), Visual Crossing | MEDIUM |
| Venue | Indirect (HFA only) | Yes (game record) | CFBD `/games` | Yes | Yes | Yes | LOW |
| Neutral site | **YES** — HFA forced to 0 | Yes | CFBD `/games` | Yes | Yes | Yes | LOW |

### The specific questions asked

**Does the model know who the expected 2026 starting quarterback is?**
**No.** There is no QB-identity field anywhere in the codebase
(`starting_qb` and `depth_chart` appear in zero source files). The only
QB-adjacent input is `percent_passing_ppa` — a *team-level* share of last
season's passing production returning — and `qb_continuity.py` says so
itself: *"There is no field that directly says 'the starting QB is the
same person as last year'."*

**Could a transfer QB or new starter be effectively invisible?**
**Yes, in both directions.** A team returning its full receiving corps
around a brand-new transfer QB scores high `percent_passing_ppa` and is
classified `returning_starter` (uncertainty multiplier 1.00) — maximum
confidence, new quarterback. The module's own docstring anticipates this
exact failure. And even when the proxy fires, it only widens the
distribution; it never moves the point estimate.

**Could a known QB injury be invisible?** **Yes, completely.** Nothing
ingests injury information. A starter ruled out on Friday changes the
market and does not change the model by one thousandth of a point.

**How does preseason roster turnover enter team strength?** Only through
the 2025 rating itself — i.e. it does not. Turnover between January and
August is invisible to the point estimate.

**Are coaching changes represented?** **No.** A new head coach and staff
inherit the previous regime's rating unchanged.

**Is live weather represented?** **No.** Not fetched, not captured, not
used. A 30 mph wind game is projected identically to a dome game.

**Is neutral-site handling correct?** **Yes.** `is_neutral_site` sets
`home_indicator = 0.0` and `away_indicator = 0.0`, so the `ratings.hfa`
term contributes exactly zero to both sides. This is the one contextual
input that is correctly wired, and Part 11's diagnostics assert it.

---

## What uncertainty *is* widened by

Not nothing — the model is appropriately humble in Week 1, just not
specifically informed:

| Mechanism | Effect |
|---|---|
| `uncertainty_multiplier(qb_state)` | 1.00 returning / 1.10 mixed / **1.20 new-or-unknown** |
| `EARLY_SEASON_UNCERTAINTY_SCALE × (1 − carryover_weight)` | Maximal at Week 1, since the weight is 0 |
| `FCS_OPPONENT_UNCERTAINTY_SCALE` | Applied to both sides when either is FCS |

`UNKNOWN` continuity is given the **same 1.20 multiplier as
`NEW_STARTER`**, never the 1.00 of a returning starter — missing data is
not treated as good news. That is the right default and it is tested.

---

## Risk summary

| Severity | Finding |
|---|---|
| **HIGH** | Week 1 point estimate carries zero 2026 information; QB identity, injuries, transfers and coaching changes are all structurally invisible. |
| **HIGH** | The QB proxy can report maximum confidence for a team starting a brand-new quarterback. |
| MEDIUM | Weather absent; a genuine and freely-available source (NWS) exists but is not wired. |
| MEDIUM | Non-passing returning production and talent are fetched-capable but unused. |
| LOW | Venue and neutral-site handling are correct. |

**None of these is a code defect.** They are documented scope limits, and
this audit's purpose is to keep them visible rather than to fix them
under time pressure the night before a slate.

---

## What this audit explicitly does NOT license

- No probability, coefficient, or threshold changes.
- No "the model is missing QB info, so fade large disagreements."
  That is an untested hypothesis; it is preregistered as a research
  question, not applied as a rule.
- No new model input added on the strength of sounding predictive.

The correct response is **research-only contextual capture**
(`docs/CONTEXT_CAPTURE.md`): record the context prospectively, alongside
the observation, with provenance — so that after enough real games we can
*measure* whether these gaps explain model-market error, instead of
assuming they do.
