"""Settlement economics: which formula a net figure came from, and how a later
contract corrects an earlier one WITHOUT touching the row it corrects.

THE DEFECT THIS EXISTS TO CORRECT
---------------------------------
Every CFB settlement filed before 2026-09-28 is on the router's economics v1
contract: ``net = gross - stake - fee_cost``. The wager's ``stake`` already
includes the entry fee the exchange charged, and Kalshi's settlement
``fee_cost`` is that same cumulative trading fee -- measured to the cent on
every established row of this ledger (89 of 89). So v1 charges one fee twice,
and every filed net is understated by exactly the wager's ``fees_paid``.

The router's v2 contract is ``net = gross - stake``, stated only once the
exchange's own fee figure proves no further fee exists.

WHAT A CORRECTION IS, AND IS NOT
--------------------------------
A settlement row is a claim about money the exchange moved, and this ledger
never rewrites one. A corrected interpretation of the same exchange facts is
therefore a SEPARATE, append-only AMENDMENT row that names the settlement it
amends, states which contract it supersedes and which it applies, carries the
corrected figures beside the original ones, and is identified deterministically
so the same correction filed twice is one row.

An amendment may only correct the ECONOMICS. The exchange facts -- market, side,
result, gross return -- must agree between the filed row and the correction, or
it is not a correction at all but a contradiction, and a contradiction is
refused for a person to look at. A correction whose own net is unestablished is
refused too: an amendment that says "we no longer know" is not an improvement
on a filed figure and would be read as one.

The original row stays byte-for-byte where it was. Reporting that wants the
corrected view applies :func:`apply_amendments` and gets NEW dicts; the raw
history is inspectable in the settlement file for as long as the file exists.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from .settlement import ECONOMICS_V1, ECONOMICS_V2, ECONOMICS_VERSIONS, SETTLED

AMENDMENT_SCHEMA_VERSION = "cfb_settlement_amendment.v1"

#: Prefix on every minted amendment id, so an amendment is identifiable as one
#: at a glance and can never be mistaken for a settlement (`stl-`) or a wager
#: (`routed-`).
AMENDMENT_ID_PREFIX = "amd"

#: The order of the contracts. An amendment may move a settlement FORWARD along
#: this list and never backward: a v1 restatement of a v2 row is a regression,
#: not a correction.
ECONOMICS_ORDER = {version: index for index, version in enumerate(ECONOMICS_VERSIONS)}

#: What makes two amendments THE SAME correction. Everything here is either
#: identity or a corrected figure. Deliberately absent: `amended_at`,
#: `provenance` and `evidence` -- the same correction derived by the router
#: from live fee evidence and by the backfill from the filed row is one
#: correction, and a store that saw two would file the figures twice.
CORRECTION_FIELDS = (
    "amendment_id",
    "source_bet_key",
    "amends_settlement_id",
    "market_ticker",
    "side",
    "result",
    "supersedes_economics_version",
    "economics_version",
    "gross_return",
    "net_profit_loss",
    "original_gross_return",
    "original_net_profit_loss",
)

#: A money comparison tolerance well below the ledger's own 4-decimal rounding.
MONEY_TOLERANCE = 0.00051

#: How a v2 net is derived, stated on every amendment so a reader of one row
#: does not have to find this module.
V2_DERIVATION = (
    "net_profit_loss = gross_return - stake. The wager's stake already contains the "
    "entry fee the exchange charged, and the exchange's settlement fee_cost is that "
    "same fee, so it is charged once. The filed v1 net subtracted it a second time."
)


class AmendmentRefused(Exception):
    """This correction cannot be filed, and filing it anyway would be worse."""


def economics_version_of(row: dict[str, Any] | None) -> str:
    """The contract a settlement row's net was computed under.

    Absence means v1: every row written before the field existed was computed
    that way, and reading absence as anything else would rewrite history by
    interpretation."""
    if not isinstance(row, dict):
        return ECONOMICS_V1
    version = row.get("economics_version")
    return version if isinstance(version, str) and version.strip() else ECONOMICS_V1


def mint_amendment_id(source_bet_key: str, economics_version: str) -> str:
    """Deterministic: the WAGER's key and the contract being applied.

    Not the corrected figure, the time, or who derived it. The same wager
    corrected to the same contract is one amendment however many times and by
    whichever route it is derived -- which is what lets the router's live v2
    run and the offline backfill agree on one row instead of filing two."""
    if not isinstance(source_bet_key, str) or not source_bet_key.strip():
        raise AmendmentRefused("source_bet_key is required to mint an amendment id")
    if economics_version not in ECONOMICS_VERSIONS:
        raise AmendmentRefused(f"unknown economics_version {economics_version!r}")
    digest = hashlib.sha256(f"{source_bet_key}|{economics_version}".encode()).hexdigest()[:24]
    return f"{AMENDMENT_ID_PREFIX}-{digest}"


def _money(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _same_money(left: Any, right: Any) -> bool:
    a, b = _money(left), _money(right)
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= MONEY_TOLERANCE


def build_amendment(
    existing: dict[str, Any],
    incoming: dict[str, Any],
    *,
    provenance: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The append-only correction of `existing` by `incoming`, or a refusal.

    `existing` is the settlement row on disk. `incoming` is a settlement row in
    the same shape carrying a LATER economics_version -- the router's v2 row
    for a wager already settled under v1, or the backfill's derivation of one.

    Refused when the two are not the same exchange event (key, market, side,
    result or gross differ), when the version does not move forward, when the
    incoming net is unestablished, or when the incoming row still carries a
    refusal. Every refusal names why; none is silent.
    """
    key = existing.get("source_bet_key")
    if not isinstance(key, str) or not key.strip():
        raise AmendmentRefused("the filed settlement has no source_bet_key")
    if incoming.get("source_bet_key") != key:
        raise AmendmentRefused("the correction is for a different wager")
    for name in ("market_ticker", "side", "result"):
        if existing.get(name) != incoming.get(name):
            raise AmendmentRefused(
                f"the correction disagrees with the filed settlement on {name}; that is a "
                "contradiction of exchange facts, not an economics correction"
            )
    if existing.get("settlement_status") != SETTLED or incoming.get("settlement_status") != SETTLED:
        raise AmendmentRefused("only a SETTLED row can be corrected, by a SETTLED row")

    was = economics_version_of(existing)
    now = economics_version_of(incoming)
    if now not in ECONOMICS_ORDER or was not in ECONOMICS_ORDER:
        raise AmendmentRefused(f"unknown economics_version ({was!r} -> {now!r})")
    if ECONOMICS_ORDER[now] <= ECONOMICS_ORDER[was]:
        raise AmendmentRefused(
            f"economics_version {now!r} does not supersede the filed {was!r}; an amendment "
            "moves a settlement forward along the contract list, never back or sideways"
        )

    incoming_gross = _money(incoming.get("gross_return"))
    incoming_net = _money(incoming.get("net_profit_loss"))
    if incoming_gross is None or incoming_net is None:
        raise AmendmentRefused(
            "the correction's own economics are unestablished; an amendment that says "
            "'unknown' is not a correction of a filed figure"
        )
    if incoming.get("refusals"):
        raise AmendmentRefused(
            f"the correction carries refusals {list(incoming.get('refusals') or [])}; a refused "
            "figure cannot amend an established one"
        )
    if existing.get("gross_return") is not None and not _same_money(
        existing.get("gross_return"), incoming_gross
    ):
        raise AmendmentRefused(
            "gross_return differs between the filed settlement and the correction; the gross "
            "is an exchange fact (contracts x settlement value) and an economics contract "
            "cannot change it"
        )
    settlement_id = existing.get("settlement_id")
    if not isinstance(settlement_id, str) or not settlement_id.strip():
        raise AmendmentRefused("the filed settlement has no settlement_id to amend")

    return {
        "amendment_id": mint_amendment_id(key, now),
        "schema_version": AMENDMENT_SCHEMA_VERSION,
        "source_bet_key": key,
        "amends_settlement_id": settlement_id,
        "market_ticker": existing.get("market_ticker"),
        "side": existing.get("side"),
        "result": existing.get("result"),
        "supersedes_economics_version": was,
        "economics_version": now,
        # The corrected accounting interpretation.
        "gross_return": round(incoming_gross, 4),
        "net_profit_loss": round(incoming_net, 4),
        # The exchange facts as originally filed, kept beside the correction
        # so a reader of this one row sees both.
        "original_gross_return": _money(existing.get("gross_return")),
        "original_net_profit_loss": _money(existing.get("net_profit_loss")),
        "original_refusals": list(existing.get("refusals") or []),
        "derivation": V2_DERIVATION if now == ECONOMICS_V2 else "",
        "evidence": dict(evidence or {}),
        "provenance": provenance,
        "amended_at": datetime.now(UTC).isoformat(),
    }


def same_correction(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Whether two amendments state the same correction of the same settlement.

    Money is compared to MONEY_TOLERANCE; identity fields exactly. When this is
    true a second filing is a harmless repeat; when it is false for the same
    amendment_id, two derivations disagree about the money and NEITHER may be
    written over the other."""
    for name in CORRECTION_FIELDS:
        if name in ("gross_return", "net_profit_loss", "original_gross_return",
                    "original_net_profit_loss"):
            if not _same_money(left.get(name), right.get(name)):
                return False
        elif left.get(name) != right.get(name):
            return False
    return True


def validate_amendment(row: dict[str, Any]) -> list[str]:
    """Every reason this amendment row may not be written. Empty means it may."""
    problems: list[str] = []
    if row.get("schema_version") != AMENDMENT_SCHEMA_VERSION:
        problems.append(f"schema_version must be {AMENDMENT_SCHEMA_VERSION!r}")
    for name in ("amendment_id", "source_bet_key", "amends_settlement_id", "market_ticker",
                 "side", "supersedes_economics_version", "economics_version", "provenance"):
        value = row.get(name)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{name} is required and must be a non-empty string")
    was, now = row.get("supersedes_economics_version"), row.get("economics_version")
    if was in ECONOMICS_ORDER and now in ECONOMICS_ORDER and ECONOMICS_ORDER[now] <= ECONOMICS_ORDER[was]:
        problems.append(f"economics_version {now!r} does not supersede {was!r}")
    elif was not in ECONOMICS_ORDER or now not in ECONOMICS_ORDER:
        problems.append("supersedes_economics_version and economics_version must be known contracts")
    for name in ("gross_return", "net_profit_loss"):
        if _money(row.get(name)) is None:
            problems.append(f"{name} must be an established number on an amendment")
    if row.get("side") not in ("YES", "NO"):
        problems.append(f"side must be YES or NO; got {row.get('side')!r}")
    if isinstance(row.get("source_bet_key"), str) and now in ECONOMICS_ORDER:
        expected = mint_amendment_id(row["source_bet_key"], now)
        if row.get("amendment_id") != expected:
            problems.append("amendment_id is not the deterministic id for this wager and contract")
    if not isinstance(row.get("evidence"), dict):
        problems.append("evidence must be an object, even when empty")
    if not isinstance(row.get("original_refusals"), list):
        problems.append("original_refusals must be a list, even when empty")
    return problems


def index_amendments(amendments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """settlement_id -> the amendment applying the MOST ADVANCED contract to it.

    One settlement can in principle carry one amendment per later contract;
    the canonical view is the furthest one along the list."""
    out: dict[str, dict[str, Any]] = {}
    for row in amendments:
        if not isinstance(row, dict):
            continue
        target = row.get("amends_settlement_id")
        if not isinstance(target, str):
            continue
        current = out.get(target)
        if current is None or ECONOMICS_ORDER.get(economics_version_of(row), -1) > ECONOMICS_ORDER.get(
            economics_version_of(current), -1
        ):
            out[target] = row
    return out


def apply_amendments(
    settlements: list[dict[str, Any]], amendments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The CANONICAL view: each settlement with its most advanced correction applied.

    Returns NEW dicts and mutates nothing. A corrected row carries the
    amendment's figures and economics_version plus `canonical_amendment_id`,
    and keeps the filed figures under `as_filed_*` so the raw history is one
    field away rather than one file away. A settlement with no amendment is
    returned as a copy, unchanged."""
    by_target = index_amendments(amendments)
    out: list[dict[str, Any]] = []
    for row in settlements:
        if not isinstance(row, dict):
            continue
        amendment = by_target.get(str(row.get("settlement_id")))
        if amendment is None or amendment.get("source_bet_key") != row.get("source_bet_key"):
            out.append(dict(row))
            continue
        corrected = dict(row)
        corrected["as_filed_net_profit_loss"] = row.get("net_profit_loss")
        corrected["as_filed_gross_return"] = row.get("gross_return")
        corrected["as_filed_refusals"] = list(row.get("refusals") or [])
        corrected["as_filed_economics_version"] = economics_version_of(row)
        corrected["gross_return"] = amendment.get("gross_return")
        corrected["net_profit_loss"] = amendment.get("net_profit_loss")
        corrected["refusals"] = []
        corrected["economics_version"] = economics_version_of(amendment)
        corrected["canonical_amendment_id"] = amendment.get("amendment_id")
        out.append(corrected)
    return out
