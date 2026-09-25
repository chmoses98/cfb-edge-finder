# Independent External Scheduling — HIBERNATED

> ## ⛔ STATUS: HIBERNATED / DISABLED (since the 2026-09-17 market-discovery pivot)
>
> **The cron-job.org job described below must NOT be enabled while the old
> research collector is retired.** Everything after this box is a
> historical record of how the scheduler *was* set up, kept so the
> research infrastructure stays recoverable. It is **not** a to-do list.
>
> - **What happened.** The pivot (docs/MODEL_RETIREMENT_2026.md) commented
>   out the GitHub cron on `research-capture.yml` and
>   `research-collection-conductor.yml`, but this independent job was left
>   running. Through 2026-09-25 it dispatched Research Capture every 5
>   minutes with `trigger_source=EXTERNAL_SCHEDULE`; every run failed
>   (`CFBD_QUOTA_EXHAUSTED`, quota remaining 0,
>   `FOOTBALL_STATE_STALE_HARD`, `DEADLINE_AT_RISK`, exit 1) and emailed a
>   failure notification — ~288 a day, for a model nothing reads.
> - **Current state.** The cron-job.org job is paused/disabled by the
>   operator outside GitHub. Inside the repository, Research Capture's
>   `hibernation-gate` job now **refuses** any dispatch declaring
>   `trigger_source=EXTERNAL_SCHEDULE` (and any conductor/bot or
>   non-manual run) while `src/cfb_edge_finder/research/hibernation.py`
>   lists it `HIBERNATED`. A refused run exits green with a
>   `::warning::` annotation, so a scheduler that was never switched off
>   still shows up in the Actions tab without re-creating the email storm.
>   CI (`tests/test_hibernated_workflow_triggers.py`) fails if any
>   automatic trigger or dispatch path to a hibernated workflow is
>   reintroduced.
> - **The live CFB path does not use this.** It is
>   `kalshi-market-catalog.yml` (keyless, scheduled by GitHub, independent
>   of the research collector). Nothing about the catalog depends on this
>   scheduler.
> - **Manual runs still work.** Actions → *Research Capture (scheduled
>   scanner)* → Run workflow, with `trigger_source` blank or `MANUAL`.
>
> ### Reactivation requires an explicit operator decision
>
> Do **not** re-enable the external job on its own — with the registry
> still `HIBERNATED` every dispatch is refused, and with the checks below
> skipped every run fails. Reactivation is one deliberate, reviewed
> change plus a checklist, in this order:
>
> 1. **Decide** that prospective research collection is wanted again, and
>    record why (the model it fed is retired; docs/MODEL_RETIREMENT_2026.md
>    says CLV research, if revived, should be built on the catalog
>    instead).
> 2. **CFBD quota and credentials first.** Confirm `CFBD_API_KEY` is still
>    valid and that quota is available: read
>    `data/research/cfbd_access/state.json` on the `research-data` branch
>    (`access_state`, `cfbd_quota_remaining`, `cfbd_quota_resets_at`), or
>    run *Validate CFBD Live (manual)*. At 2026-09-25 the Free-tier quota was
>    1000/1000 used, resetting 2026-10-01T00:00Z. Do not reactivate into
>    `CFBD_QUOTA_EXHAUSTED` — every run will fail closed.
> 3. **Football-state freshness.** Run Research Capture once **manually**
>    with `refresh_football_state=true` and confirm the run reports a
>    fresh football state (not `FOOTBALL_STATE_STALE_HARD`) and an
>    operational state that is not `DEADLINE_AT_RISK`.
> 4. **Flip the registry** in `src/cfb_edge_finder/research/hibernation.py`
>    to `ACTIVE` for the workflow(s) being revived, and update the tests
>    that fail naming them, in the same PR.
> 5. **Only then** resume the cron-job.org job (or uncomment the GitHub
>    `schedule:` block). Watch the first few runs.

---

## Historical record (pre-pivot, 2026-08-28) — do not act on this

GitHub's schedule service was, at the time, no longer the primary clock
for prospective collection. The rest of this document explains why, what
replaced it, and the setup that was used.

## The measured problem

Over a 573-minute window after the `*/10` cadence went live, the collector
should have received ~57 scheduled runs. It received **one**.

| | |
|---|---|
| Expected `*/10` firings | ~57 |
| Actual | **1** |
| Realised delivery | **1.7%** |
| Observed gaps | 95, 144, 171, 296, 653, **777** minutes |
| Conductor scheduled runs, all time | **0** of 77 total runs |

Everything downstream is healthy — collector runtime is 3.3s idle / ~55s
full, due-label resolution works, `research_corpus_v2` capture works,
CLOSING semantics work, persistence and dedup work. **Only the clock is
broken.**

A 14-minute CLOSING window under a ~573-minute mean interval has roughly a
**2%** chance of being covered unattended. That is the whole reason this
exists.

## Architecture

```
independent external scheduler   (every 5 min)
        │  authenticated POST, fine-grained PAT
        ▼
GitHub REST: workflow_dispatch
        │
        ▼
Research Capture  ← THE canonical workflow, unchanged
        │
        ├── existing due-label resolver
        ├── existing mapping / pricing
        └── existing research-data persistence + dedup
```

**No second implementation of anything.** The external scheduler only
decides *when*; every decision about *what* stays in the canonical
collector. GitHub cron remains a fallback, manual dispatch the emergency
path.

### What "independent" does and does not mean

It removes dependence on **GitHub's schedule service**, which is the
component that is failing. The collector still executes on **GitHub
Actions runners**, so an Actions-wide outage takes every dispatch path
down with it — external, cron, conductor and manual alike. Any
GitHub-dispatch design shares that floor; only a collector running
somewhere else would not, and that is a far larger change than this
problem warrants.

## Options evaluated

| Option | Min cadence | Independent of GitHub cron | Cost | Auth support | Operator burden |
|---|---|---|---|---|---|
| **cron-job.org** | 1 min | Yes | Free | Custom headers + POST body | **Lowest — web form, no deploy** |
| Cloudflare Workers Cron | 1 min | Yes | Free (100k req/day; ~288 needed) | Encrypted Worker secret | Moderate — write + deploy a Worker |
| Vercel Cron (Hobby) | **1 per day** | Yes | Free | Yes | **Disqualified — cadence** |
| More GitHub cron entries | n/a | **No** | Free | n/a | Rejected — same broken service |

**Selected: cron-job.org**, because the binding constraint is getting an
independent clock live before the first closing window, and it needs no
deployment step. Cloudflare Workers is the documented hardening upgrade:
its advantage is that the PAT lives in an encrypted Worker secret rather
than a third-party job's header field, which is a real if modest
improvement in credential handling.

*Verified via web search on 2026-08-28; primary vendor documentation was
unreachable from the audit environment (egress-blocked), so confirm the
current free-tier cadence when you sign up.*

## Credential

A **fine-grained personal access token**, not a classic PAT.

- **Resource owner:** `chmoses98`
- **Repository access:** *Only select repositories* → **`chmoses98/cfb-edge-finder`**
- **Repository permissions:** **Actions → Read and write**
  (Metadata → Read is added automatically and is required)
- **Expiration:** set one, and calendar a renewal

That grants dispatching workflows in one repository and nothing else. No
contents write, no other repo, no organisation scope.

> If the dispatch returns `403 Resource not accessible by personal access
> token`, add **Contents → Read**. Reports differ on whether it is
> required; start without it and only add it if GitHub demands it.

**Never** commit the token, echo it in a workflow, or paste it into a
public issue. It lives only in the scheduler's secret/header field.

## Setup (one time, ~5 minutes)

1. **Create the token** — GitHub → Settings → Developer settings →
   Personal access tokens → Fine-grained tokens → Generate new token, with
   the settings above. Copy it once.
2. **Create the job** at cron-job.org (free account):
   - **URL**
     ```
     https://api.github.com/repos/chmoses98/cfb-edge-finder/actions/workflows/research-capture.yml/dispatches
     ```
   - **Method:** `POST`
   - **Headers**
     ```
     Accept: application/vnd.github+json
     Authorization: Bearer <YOUR_FINE_GRAINED_TOKEN>
     X-GitHub-Api-Version: 2022-11-28
     Content-Type: application/json
     ```
   - **Body**
     ```json
     {"ref":"main","inputs":{"schedule_season":"2026","no_push":"false","trigger_source":"EXTERNAL_SCHEDULE"}}
     ```
   - **Schedule:** every 5 minutes
3. **Test once** with the service's "run now" button. A success is
   **HTTP 204 No Content** with an empty body — GitHub returns no payload
   for a dispatch.
4. **Confirm** a new *Research Capture* run appears in the Actions tab.

### Payload notes

- `no_push` **must** be `"false"`, or the run computes everything and
  persists nothing.
- `trigger_source: EXTERNAL_SCHEDULE` is what makes a dead external
  scheduler visible. Without it the dispatch reads as `MANUAL`, because a
  PAT dispatch carries the token owner as the actor — so an occasional
  human run would mask a scheduler that had stopped days ago.
- A caller **cannot** declare `GITHUB_SCHEDULE`. Cron provenance is
  something only GitHub can establish; letting a caller assert it would
  make the staleness signal unfalsifiable.
- Nothing in the payload can bypass due-label resolution, fabricate a
  label, or force CLOSING. The collector decides what is due.

## Why every 5 minutes is safe to run continuously

The collector is lazy: when nothing is due it loads the ledger once,
discovers markets, finds zero due labels, and exits in ~3.3 seconds
without running the model. Redundant triggering is proven safe at two
independent layers — the capture-state ledger makes a repeated label
not-due (so a second trigger prices nothing), and the canonical-key check
catches the genuine race where two runs both resolve a label as due before
either writes.

At 5 minutes, a 14-minute window normally receives **2–3** opportunities.
That is not a guarantee: it still depends on GitHub Actions accepting and
starting dispatched runs promptly.

## Redundancy

| Layer | Role | Status |
|---|---|---|
| External scheduler (5 min) | ~~primary clock~~ | **HIBERNATED — must stay disabled** |
| GitHub cron `*/10` | fallback | **HIBERNATED** (commented out) |
| Conductor `17 * * * *` | closing guard | **HIBERNATED** (cron commented out; gate refuses live/successor runs) |
| Manual dispatch | emergency | still available |

If the external scheduler stops, cron and manual remain. If GitHub Actions
is down, all four are down together.

## Health

`scripts/week1_readiness.py` reports last success **per trigger**, so a
stalled external scheduler is visible even while cron happens to have
fired recently:

```
last success [EXTERNAL_SCHEDULE]: ...
last success [GITHUB_SCHEDULE  ]: ...
last success [MANUAL           ]: ...
```

## Until this is live-proven (historical, 2026-08-29)

The manual plan for 2026-08-29 stands. For the 15:46–16:00Z window,
dispatch **Research Capture** (not the conductor) with `no_push: false` at
approximately **15:44Z, 15:51Z, 15:57Z**. Duplicates are impossible.
