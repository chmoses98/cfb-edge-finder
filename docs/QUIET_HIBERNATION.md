# Quiet hibernation

**Status as of 2026-09-06.** Active model development on this project has
stopped. There is no defensible betting edge, nothing is being tuned, and no
recommendation path is open. The only reason scheduled infrastructure still
runs is to keep accumulating **clean prospective evidence** — observations that
exist only before kickoff and cannot be reconstructed later — cheaply enough
that it costs the owner no attention, for a possible end-of-season review.

This document states the operating policy that follows from that, and what was
changed to make the automation actually obey it.

## The policy

> A scheduled run exits non-zero **only when a human must intervene now to
> preserve data integrity.**

"Degraded" is a *status*, not an *incident*. Degraded conditions are recorded
durably (`data/research/operational_state/state.json`, the heartbeat ledger) and
surfaced in the Actions job summary, where someone can look at them
deliberately. They do not page anyone.

This is deliberately **not** implemented with `continue-on-error`, and
deliberately **not** by broadening exception handlers to swallow errors. Every
run still computes exactly the same verdicts it did before; the change is which
question the exit code answers.

### Still red, always, never suppressed

- any HIGH-severity health diagnostic — durable-store corruption, append-only
  violation, persistence/write failure, observation rewrite, canonical identity
  corruption, mapping collapse
- `DEADLINE_AT_RISK`: a checkpoint is due, or a kickoff is inside the 8h
  deadline window, and trustworthy schedule evidence for it does not exist
- `closing_capture_shortfall`: a CLOSING was due and did not land, for any
  reason — closing lines cannot be recovered after kickoff
- an essential collector unable to collect across a materially important period
  with no safe fallback
- entering a degraded state, or a **material change** to what is blocking

### Not red (recorded, reported, green)

- CFBD quota remains exhausted while a healthy fallback carries the schedule
- one ESPN host fails and another works
- nothing is due; no games need settlement
- an optional sidecar or enrichment source is unavailable
- an expected provider rate limit, or a transient network failure that the next
  scheduled run retries for free
- V2 shadow unavailable while canonical collection stays healthy
- an already-known degraded state repeating

## What was changed (2026-09-06)

The audit of GitHub Actions history found **10 failed runs in the 46 hours to
2026-09-06T21:10Z (~5.2 emails/day), all of them `Research Capture`**, in three
distinct causes. No other workflow failed at all.

**1. A reset socket killed the collector (6 of 10).**
`requests.ConnectionError` is a *sibling* of `requests.HTTPError`, not a
subclass. The Kalshi client retried only failures that produced a response, and
the collector's per-series guard caught only `HTTPError` — so a connection reset
mid-sweep raised straight past both and killed the process with a traceback
*before* it could classify its run, write operational state, or emit a
heartbeat. The whole quiet-hibernation design was bypassed by an exception type.

Fixed in `data/kalshi_client.py` (transport errors now get the same bounded
backoff as 429/5xx) and `scripts/capture_kalshi_cfb_snapshot.py` (the guard
catches the shared base class). Exhausting the retries still **raises**: a
failed series must stay distinguishable from an empty one.

**2. `api_failures` was unconditionally HIGH (1 of 10).**
One transient blip on one series made a run red even when nothing was due and
nothing was lost. It is now HIGH only when `closing_due > 0` — the case where a
dropped series can silently cost an unrecoverable closing line — and a WARNING
otherwise. This narrows the *severity*, not the *detection*: the failure is
still counted, still emitted as a diagnostic, still in the heartbeat, and every
guard that made the unconditional HIGH worth having (`zero_markets_scanned`,
`supported_market_collapse`, `closing_capture_shortfall`) is untouched and still
fails loudly on its own.

**3. Alert suppression was reset by state flapping (3 of 10).**
A terminal run wrote its own name into `last_alerted_state`. So a transient
`INTEGRITY_FAILURE` erased the record that we had already alerted about the
long-running `DEGRADED_SAFE(quota)` state — and the *next* run, five minutes
later, read as a fresh state entry and went red too. Every blip cost two emails.
Terminal states never *read* that bookkeeping, so they no longer *write* it. A
genuine change of blocker still re-alerts, because `_should_alert` compares the
blocker as well as the state name.

**4. Re-alert window 24h → 168h.** Bounded re-reporting is what separates
"suppressed" from "forgotten", so it is kept — but a daily email restating a
permanent, acknowledged, deliberately-unactioned condition is the same
train-the-operator-to-ignore-it failure at 1/288th the rate. Weekly matches the
cadence at which a hibernating project is actually looked at. Nothing
time-critical depends on this window: deadline risk and integrity failures are
classified above it and are never rate-limited at all.

## Schedules

**Kept** — each contributes prospective evidence that cannot be reconstructed:

| Workflow | Trigger | Why |
|---|---|---|
| Research Capture | conductor dispatch (~5 min in band) + `*/10` cron backstop | Kalshi observations, closing prices, V2 shadow, talent shadow |
| Research Collection Conductor | `17 * * * *` restarter, self-chaining | drives the tight cadence that protects CLOSING |
| Research Settlement | `0 */6 * * *` | settlements and observation-level attribution |
| Live Info Sidecar | `17 */6 * * *` | prospective forecasts/odds/injuries; keyless, zero CFBD, isolated orphan branch |

**Disabled** (schedule/trigger only — code, history and artifacts untouched):

| Workflow | Was | Why |
|---|---|---|
| Research Weekly Report | `0 9 * * 1` | reports on evidence, collects none; regenerable on demand from a strictly more complete corpus |
| Capture Resilience Probe | push to a merged branch | read-only diagnostic; can only fire by accident |
| Settlement Source Probe | push to a merged branch | same |

### Why the ~5-minute cadence stays

It is not a cost-free default and it was re-examined, not grandfathered. CLOSING
is **14 minutes wide, strictly pre-kickoff, and gone forever once the ball is
kicked** — the one checkpoint with no recovery path. At a ~5-minute effective
cadence that window is hit two to three times and tolerates GitHub scheduler
drift; at 10 minutes it is hit about once with roughly four minutes of slack.

Crucially the cadence is **adaptive, not constant**: `collection_conductor.py`
uses the tight interval only inside an active kickoff band, sleeps up to 30
minutes when idle, and exits entirely when no supported kickoff is within the
horizon. The tight rate is spent only where it buys a closing line.

Reducing it would have hidden failures rather than fixed them, so it was left
alone and the failures were fixed instead.

## Cost

Zero metered CFBD calls are spent by any of this. The collector has been running
at `cfbd_requests: 0` under `CFBD_QUOTA_EXHAUSTED`, capturing Kalshi prices and
refreshing the schedule through the keyless ESPN fallback; the sidecar is
keyless by construction. None of the changes above add a call.

## If it gets noisy again

The prospective dataset is explicitly **not** valuable enough to justify ongoing
owner attention. If keeping the collector alive starts requiring real
maintenance, the correct move is to disable all scheduled CFB automation rather
than to keep tuning it. Disabling is one edit per workflow — remove the
`schedule:` block, as `research-weekly-report.yml` now shows — and loses nothing
already collected.
