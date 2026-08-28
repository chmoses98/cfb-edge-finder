# Independent External Scheduling

GitHub's schedule service is no longer the primary clock for prospective
collection. This document explains why, what replaces it, and the exact
setup required.

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
| External scheduler (5 min) | **primary clock** | pending setup |
| GitHub cron `*/10` | fallback | live but ~1.7% delivery |
| Conductor `17 * * * *` | closing guard | never self-started |
| Manual dispatch | emergency | live, proven |

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

## Until this is live-proven

The manual plan for 2026-08-29 stands. For the 15:46–16:00Z window,
dispatch **Research Capture** (not the conductor) with `no_push: false` at
approximately **15:44Z, 15:51Z, 15:57Z**. Duplicates are impossible.
