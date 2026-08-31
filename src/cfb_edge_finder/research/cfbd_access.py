"""CFBD quota observability + automatic recovery detection.

*** THE PROBLEM (live, 2026-08-29 -> 2026-08-31) ***
The free CFBD quota (1,000 metered calls/month, shared CFB+CBB pool) was
exhausted, and the football-state artifact had never bootstrapped -- so
every 5-minute capture run attempted a full live build, whose first
request retried 429 four times: ~1,150 pointless requests/day hammering
a provider that had already said no, while the operator had to guess
when access would return.

*** WHAT THE EVIDENCE ESTABLISHED (run 33349348575 + server source) ***
CFBD's `GET /info` is authenticated, UNMETERED (quota middleware
ignoredPaths; live-proven: remainingCalls did not decrease across
consecutive calls), answers HTTP 200 even while every metered endpoint
is 429, and reports {tierName, monthlyLimit, remainingCalls, usedCalls,
resetAt} where resetAt is authoritative: the first of the next calendar
month at 00:00:00 UTC. The quota-429 itself carries no Retry-After.

*** THE DESIGN ***
A tiny durable state file (data/research/cfbd_access/state.json, on the
research-data branch alongside every other durable artifact) records the
last known access state:

  CFBD_ACCESS_OK        -- normal operation; NO probing at all. A real
                           metered call failing with 429 is what flips
                           the state (no extra requests spent watching a
                           healthy account).
  CFBD_QUOTA_EXHAUSTED  -- metered CFBD calls are GATED OFF entirely.
                           At most one unmetered /info probe per
                           NEXT_PROBE window: scheduled for just after
                           the authoritative resetAt, capped at
                           PROBE_MAX_INTERVAL_HOURS so a wrong resetAt
                           cannot park the system forever.
  CFBD_ACCESS_UNKNOWN   -- the probe itself failed or its payload was
                           unusable: telemetry is reported unavailable
                           (never guessed) and the probe retries on the
                           shorter error interval. Metered calls stay
                           gated -- a 5xx or timeout is never treated as
                           quota recovery.

The first probe that reports remainingCalls > 0 flips the state to OK,
which un-gates the very same run: the existing slow-lane
`resolve_football_state` then performs its normal full build -- the
bootstrap is the EXISTING build path, never a duplicate. If that build
fails, the failure is handled exactly as before (fail closed, degraded
state retained, retried next run).

Fail-closed invariants: no football state is ever fabricated; recovery
is recognized ONLY from an /info payload positively showing
remainingCalls > 0; missing/malformed quota fields are reported as
unavailable, never invented; the API key is never logged or persisted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import requests

CFBD_ACCESS_OK = "CFBD_ACCESS_OK"
CFBD_QUOTA_EXHAUSTED = "CFBD_QUOTA_EXHAUSTED"
CFBD_ACCESS_UNKNOWN = "CFBD_ACCESS_UNKNOWN"

CFBD_USAGE_SOURCE = "cfbd GET /info (authenticated, unmetered)"

PROBE_MAX_INTERVAL_HOURS = 6.0
"""While locked out, never wait longer than this between /info probes --
the probe is free (unmetered), so this cap exists purely so a wrong or
missing resetAt cannot delay recovery detection indefinitely. 4 tiny
requests/day while exhausted, vs the ~1,150/day the gate replaces."""

PROBE_ERROR_RETRY_HOURS = 1.0
"""A probe that itself failed (5xx/timeout/parse) proves nothing about
quota -- retry sooner than the full interval, but never hot-loop."""

POST_RESET_PROBE_INTERVAL_HOURS = 0.5
"""If the last authoritative resetAt has just PASSED and CFBD still
reports zero remaining calls (a lagging reset job server-side), probe
every 30 minutes rather than parking for the full interval: recovery is
imminent by CFBD's own stated schedule."""

RESET_MARGIN_SECONDS = 120
"""Probe slightly AFTER the stated reset instant, never at it."""

STATE_RELPATH = ("data", "research", "cfbd_access", "state.json")
"""Lives under data/research/ so git_durable_store's staging allowlist
covers it unchanged -- account-level (quota is per key, not per season),
hence one file, not one per season."""


@dataclass(frozen=True)
class QuotaTelemetry:
    """Parsed /info evidence. Every field is None unless the payload
    actually carried it -- nothing is inferred or defaulted."""

    tier_name: str | None
    monthly_limit: int | None
    remaining_calls: int | None
    used_calls: int | None
    reset_at: datetime | None
    checked_at: datetime

    def as_state_dict(self) -> dict:
        return {
            "cfbd_tier_name": self.tier_name,
            "cfbd_quota_limit": self.monthly_limit,
            "cfbd_quota_remaining": self.remaining_calls,
            "cfbd_quota_used": self.used_calls,
            "cfbd_quota_resets_at": self.reset_at.isoformat() if self.reset_at else None,
            "cfbd_usage_checked_at": self.checked_at.isoformat(),
            "cfbd_usage_source": CFBD_USAGE_SOURCE,
        }


@dataclass
class AccessAssessment:
    """What this run decided about CFBD access before touching any
    metered endpoint."""

    access_state: str
    allow_cfbd: bool
    probe_ran: bool = False
    probe_error: str | None = None
    recovery_detected: bool = False
    quota: QuotaTelemetry | None = None
    next_probe_at: datetime | None = None
    prior_state: dict | None = None


def state_path(repo_dir: Path) -> Path:
    return repo_dir.joinpath(*STATE_RELPATH)


def load_state(repo_dir: Path) -> dict:
    path = state_path(repo_dir)
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_state(repo_dir: Path, state: dict) -> None:
    path = state_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_iso(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _int_or_none(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_account_info(raw: dict, *, checked_at: datetime) -> QuotaTelemetry:
    tier = raw.get("tierName")
    return QuotaTelemetry(
        tier_name=tier if isinstance(tier, str) else None,
        monthly_limit=_int_or_none(raw.get("monthlyLimit")),
        remaining_calls=_int_or_none(raw.get("remainingCalls")),
        used_calls=_int_or_none(raw.get("usedCalls")),
        reset_at=_parse_iso(raw.get("resetAt")),
        checked_at=checked_at,
    )


def probe_account_info(cfbd_client, *, now: datetime) -> tuple[QuotaTelemetry | None, str | None]:
    """One unmetered /info request -> (telemetry, error). Never raises;
    never logs the key (the client owns the Authorization header)."""
    try:
        raw = cfbd_client.fetch_account_info()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return None, f"{type(exc).__name__}" + (f" HTTP {status}" if status is not None else "")
    except (ValueError, TypeError) as exc:
        return None, f"unusable /info payload: {type(exc).__name__}: {exc}"
    if not isinstance(raw, dict):
        return None, f"unusable /info payload: {type(raw).__name__}"
    return parse_account_info(raw, checked_at=now), None


def next_probe_time(quota: QuotaTelemetry | None, *, now: datetime) -> datetime:
    """When to probe again while still locked out. Uses the authoritative
    resetAt when it is in the future (probe just after it); caps at
    PROBE_MAX_INTERVAL_HOURS; and if the stated reset has already passed
    yet quota is still exhausted, probes on the short post-reset interval
    instead of parking."""
    cap = now + timedelta(hours=PROBE_MAX_INTERVAL_HOURS)
    reset_at = quota.reset_at if quota else None
    if reset_at is None:
        return cap
    if reset_at > now:
        return min(reset_at + timedelta(seconds=RESET_MARGIN_SECONDS), cap)
    return now + timedelta(hours=POST_RESET_PROBE_INTERVAL_HOURS)


def assess(repo_dir: Path, cfbd_client, *, now: datetime, force_allow: bool = False) -> AccessAssessment:
    """The gate decision, made BEFORE any metered CFBD request.

    OK (or forced)         -> allow, zero probes.
    exhausted/unknown, not
    yet probe time         -> gated, zero network of any kind.
    probe time reached     -> one unmetered /info; only a payload
                              positively showing remainingCalls > 0
                              recovers -- probe failure or missing
                              fields stay fail-closed.
    no state recorded yet  -> probe first, so the very first gated-era
                              run never spends a metered attempt just to
                              discover the quota is still gone."""
    prior = load_state(repo_dir)
    prior_access = prior.get("access_state")

    if force_allow:
        return AccessAssessment(
            access_state=prior_access or CFBD_ACCESS_UNKNOWN,
            allow_cfbd=True,
            prior_state=prior,
        )

    if prior_access == CFBD_ACCESS_OK:
        return AccessAssessment(access_state=CFBD_ACCESS_OK, allow_cfbd=True, prior_state=prior)

    if prior_access in (CFBD_QUOTA_EXHAUSTED, CFBD_ACCESS_UNKNOWN):
        next_probe_at = _parse_iso(prior.get("cfbd_next_probe_at"))
        if next_probe_at is not None and now < next_probe_at:
            return AccessAssessment(
                access_state=prior_access,
                allow_cfbd=False,
                next_probe_at=next_probe_at,
                prior_state=prior,
            )

    # Probe time (or no recorded state at all).
    quota, error = probe_account_info(cfbd_client, now=now)
    if error is not None:
        # The probe itself failed: proves nothing about quota. If we were
        # already exhausted we stay exhausted; otherwise state is UNKNOWN.
        state = CFBD_QUOTA_EXHAUSTED if prior_access == CFBD_QUOTA_EXHAUSTED else CFBD_ACCESS_UNKNOWN
        allow = prior_access not in (CFBD_QUOTA_EXHAUSTED, CFBD_ACCESS_UNKNOWN)
        return AccessAssessment(
            access_state=state if not allow else CFBD_ACCESS_UNKNOWN,
            allow_cfbd=allow,
            probe_ran=True,
            probe_error=error,
            next_probe_at=None if allow else now + timedelta(hours=PROBE_ERROR_RETRY_HOURS),
            prior_state=prior,
        )

    assert quota is not None
    if quota.remaining_calls is not None and quota.remaining_calls > 0:
        return AccessAssessment(
            access_state=CFBD_ACCESS_OK,
            allow_cfbd=True,
            probe_ran=True,
            recovery_detected=prior_access in (CFBD_QUOTA_EXHAUSTED, CFBD_ACCESS_UNKNOWN),
            quota=quota,
            prior_state=prior,
        )
    if quota.remaining_calls == 0:
        return AccessAssessment(
            access_state=CFBD_QUOTA_EXHAUSTED,
            allow_cfbd=False,
            probe_ran=True,
            quota=quota,
            next_probe_at=next_probe_time(quota, now=now),
            prior_state=prior,
        )
    # 200 but no usable remainingCalls: telemetry unavailable, never
    # guessed -- and never treated as recovery.
    return AccessAssessment(
        access_state=CFBD_ACCESS_UNKNOWN if prior_access != CFBD_QUOTA_EXHAUSTED else CFBD_QUOTA_EXHAUSTED,
        allow_cfbd=prior_access not in (CFBD_QUOTA_EXHAUSTED, CFBD_ACCESS_UNKNOWN),
        probe_ran=True,
        probe_error="/info payload carried no usable remainingCalls",
        quota=quota,
        next_probe_at=now + timedelta(hours=PROBE_ERROR_RETRY_HOURS),
        prior_state=prior,
    )


def record_outcome(
    assessment: AccessAssessment,
    refresh_outcome,
    cfbd_client,
    *,
    now: datetime,
) -> dict:
    """The durable state this run leaves behind, derived from what
    actually happened: the gate decision plus the real slow-lane outcome.

    Only a REAL quota signal moves the state:
      - a live refresh that succeeded            -> OK
      - a metered attempt rejected with HTTP 429 -> QUOTA_EXHAUSTED
        (with one free /info to stamp the authoritative resetAt)
      - any other failure (5xx/timeout/...)      -> state unchanged;
        transient provider trouble is not quota exhaustion.
    """
    access_state = assessment.access_state
    quota = assessment.quota
    next_probe_at = assessment.next_probe_at
    probe_error = assessment.probe_error

    attempted_live = getattr(refresh_outcome, "cfbd_requests", 0) > 0 and assessment.allow_cfbd
    refresh_error = getattr(refresh_outcome, "refresh_error", None)
    refresh_status = getattr(refresh_outcome, "refresh_http_status", None)

    if attempted_live and refresh_error is None:
        access_state = CFBD_ACCESS_OK
        next_probe_at = None
        probe_error = None
    elif attempted_live and refresh_status == 429:
        access_state = CFBD_QUOTA_EXHAUSTED
        if quota is None:
            quota, _probe_err = probe_account_info(cfbd_client, now=now)  # unmetered; best-effort telemetry
        next_probe_at = next_probe_time(quota, now=now)

    state: dict = {
        "schema_version": "cfbd_access_v1",
        "access_state": access_state,
        "cfbd_next_probe_at": next_probe_at.isoformat() if next_probe_at else None,
        "last_updated_at": now.isoformat(),
        "last_probe_error": probe_error,
    }
    if quota is not None:
        state.update(quota.as_state_dict())
    elif assessment.prior_state:
        # Carry forward the last real telemetry rather than dropping it --
        # its own checked_at timestamp says how old it is.
        for key in (
            "cfbd_tier_name",
            "cfbd_quota_limit",
            "cfbd_quota_remaining",
            "cfbd_quota_used",
            "cfbd_quota_resets_at",
            "cfbd_usage_checked_at",
            "cfbd_usage_source",
        ):
            if key in assessment.prior_state:
                state[key] = assessment.prior_state[key]
    return state


def read_state_from_git(repo_dir: Path, branch: str) -> dict:
    """Read the durable access state from origin/{branch} WITHOUT a
    checkout -- for read-only planners (the conductor). Any failure
    returns {} (callers treat that as 'no gate recorded')."""
    import subprocess

    rel = "/".join(STATE_RELPATH)
    try:
        subprocess.run(
            ["git", "fetch", "origin", branch, "--depth=1"],
            cwd=repo_dir, capture_output=True, text=True, timeout=120, check=True,
        )
        show = subprocess.run(
            ["git", "show", f"origin/{branch}:{rel}"],
            cwd=repo_dir, capture_output=True, text=True, timeout=120,
        )
        if show.returncode != 0:
            return {}
        loaded = json.loads(show.stdout)
        return loaded if isinstance(loaded, dict) else {}
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return {}


def gate_says_exhausted(state: dict, *, now: datetime) -> bool:
    """True when the durable state says metered CFBD calls are currently
    gated off (exhausted/unknown AND the next probe window has not yet
    arrived). Used by read-only planners to skip a doomed live fetch."""
    if state.get("access_state") not in (CFBD_QUOTA_EXHAUSTED, CFBD_ACCESS_UNKNOWN):
        return False
    next_probe_at = _parse_iso(state.get("cfbd_next_probe_at"))
    return next_probe_at is not None and now < next_probe_at


def summary_lines(assessment: AccessAssessment, refresh_outcome=None) -> list[str]:
    """Concise operator-facing lines (workflow logs / step summary)."""
    lines = [f"cfbd_access_state={assessment.access_state}"]
    if assessment.quota is not None:
        q = assessment.quota
        lines.append(
            f"cfbd_quota: remaining={q.remaining_calls} used={q.used_calls} limit={q.monthly_limit} "
            f"tier={q.tier_name} resets_at={q.reset_at.isoformat() if q.reset_at else 'unknown'}"
        )
    if assessment.next_probe_at is not None:
        lines.append(f"next_probe_at={assessment.next_probe_at.isoformat()}")
    if assessment.probe_error:
        lines.append(f"probe_error={assessment.probe_error}")
    if assessment.recovery_detected:
        lines.append("CFBD_RECOVERED: quota restored -- football-state bootstrap runs NOW via the normal slow lane")
    if refresh_outcome is not None and getattr(refresh_outcome, "source", "") == "live_full_refresh":
        lines.append("FOOTBALL_STATE_READY: full artifact rebuilt and saved this run")
    return lines
