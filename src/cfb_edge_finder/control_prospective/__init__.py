"""Prospective CONTROL market research: primary-window capture, H1/H2 tracking, research signals. RESEARCH ONLY.

The frozen 2026 CONTROL market study (`control_market`, chmoses98/cfb-edge-finder#105) was retrospective on the
football side and captured only 45 of 89 primary entry prices. This package is its prospective continuation:

* `capture`   records genuinely captured KXNCAAFGAME quotes inside the frozen PRIMARY_60_180 window
              (kickoff-180 to kickoff-60 minutes) at several resilient attempts, and says WHY a game has
              no price -- the market was absent, the book was not executable, the ask was $1.00, the
              market could not be oriented, or OUR pipeline failed. Those are never blurred.
* `tracking`  joins V2 ledger FINAL_PREGAME rows (CONTROL tier copied verbatim, never recomputed), the
              captured primary-window entry and the final score into append-only settlement rows, and
              evaluates the two pre-registered hypotheses (H1, H2) at frozen review checkpoints.
* `signals`   publishes `cfb_research_signals/1.0.0`, the research-state contract SIFT renders: which
              football signal carries which research status (VALUE_WATCH, ...), the study basis read
              from the frozen artifacts, prospective progress, capture health and one compact read per
              game.

The hypotheses, windows, thresholds and the promotion gate are frozen in
`data/scripting/validation/control_prospective_2026/protocol.json`; `verify_protocol` refuses to run on any
other file. Nothing here publishes a probability, a fair price, a bet-up-to price, a stake or a
recommendation, and no status is ever promoted to EDGE_CONFIRMED by code.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cfb_edge_finder.control_market import PRIMARY_CHECKPOINT, SERIES

PROTOCOL_DIR = Path("data/scripting/validation/control_prospective_2026")
PROTOCOL_PATH = PROTOCOL_DIR / "protocol.json"
#: Pinned when the protocol was committed (before any prospective outcome existed). Never edit the file.
PROTOCOL_SHA256 = "b35c75ef64447ba6087dc83d1741e42e9d2570528f4fee5518154724a84c8021"
#: Human review decisions. The ONLY way a research status other than VALUE_WATCH / REVIEW_REQUIRED appears.
DECISIONS_PATH = PROTOCOL_DIR / "decisions.json"

SIGNALS_SCHEMA = "cfb_research_signals/1.0.0"
CAPTURE_SCHEMA = "cfb_primary_window_capture/1.0.0"
ATTEMPT_SCHEMA = "cfb_primary_window_attempt/1.0.0"
SETTLEMENT_SCHEMA = "cfb_control_prospective_settlement/1.0.0"
PROSPECTIVE_VERSION = "cfb-control-prospective/1.0.0"

#: The frozen market protocol's primary window (minutes before kickoff, both ends inclusive).
PRIMARY_WINDOW = PRIMARY_CHECKPOINT
WINDOW_OPEN_MIN = 180.0
WINDOW_CLOSE_MIN = 60.0
#: Resilient attempts inside the window (minutes before kickoff). Any attempt that is missed is caught up by
#: the next cycle while the window is still open; the entry is still the LAST valid quote in the window.
CAPTURE_SLOTS = (165, 120, 90, 70)
#: Descriptive (never an entry): a late look for closing-line context.
CLOSING_SLOTS = (30,)

# ------------------------------------------------------------------ capture health (per game)
CAPTURE_OK = "CAPTURE_OK"
CAPTURE_PENDING = "CAPTURE_PENDING"
MARKET_NOT_OFFERED = "MARKET_NOT_OFFERED"
QUOTE_NOT_EXECUTABLE = "QUOTE_NOT_EXECUTABLE"
PRICE_1_00 = "PRICE_1_00"
ORIENTATION_FAILURE = "ORIENTATION_FAILURE"
CAPTURE_SYSTEM_FAILURE = "CAPTURE_SYSTEM_FAILURE"
CAPTURE_STATUSES = (
    CAPTURE_OK,
    CAPTURE_PENDING,
    MARKET_NOT_OFFERED,
    QUOTE_NOT_EXECUTABLE,
    PRICE_1_00,
    ORIENTATION_FAILURE,
    CAPTURE_SYSTEM_FAILURE,
)
#: Statuses that are a property of the MARKET, not of our pipeline.
MARKET_SIDE_STATUSES = (MARKET_NOT_OFFERED, QUOTE_NOT_EXECUTABLE, PRICE_1_00)

# ------------------------------------------------------------------ research status (per signal)
VALUE_WATCH = "VALUE_WATCH"
EDGE_CONFIRMED = "EDGE_CONFIRMED"
NO_EDGE = "NO_EDGE"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
RESEARCH_STATUSES = (VALUE_WATCH, EDGE_CONFIRMED, NO_EDGE, INSUFFICIENT_DATA, REVIEW_REQUIRED)
#: What code may emit on its own. Every other status needs a reviewed entry in decisions.json.
AUTOMATIC_STATUSES = (VALUE_WATCH, REVIEW_REQUIRED)

MODERATE_TIERS = ("HOME_CONTROL_MODERATE", "AWAY_CONTROL_MODERATE")
STRONG_TIERS = ("HOME_CONTROL_STRONG", "AWAY_CONTROL_STRONG")
#: H2 / market disagreement: STRONG CONTROL whose CONTROL-side executable YES ask is strictly below 85 cents.
DISAGREEMENT_BELOW_CENTS = 85

REVIEW_CHECKPOINTS: dict[int, str] = {
    10: "EARLY_READ_ONLY",
    20: "INTERIM_RESEARCH_READ",
    30: "MEANINGFUL_EARLY_SAMPLE",
    50: "PRIMARY_REVIEW",
}

H1_ID = "H1_MODERATE_CONTROL_PRIMARY_ROI"
H2_ID = "H2_STRONG_CONTROL_BELOW_85_UNDERPERFORMS"
H1_WORDING = (
    "MODERATE CONTROL, home and away pooled, purchased at the frozen PRIMARY_60_180 executable game-winner ask "
    "at all available prices, has fee-adjusted ROI > 0."
)
H2_WORDING = "STRONG CONTROL priced below 85¢ underperforms its fee-adjusted break-even rate."

GAME_WINNER_SERIES = SERIES

#: Words no published field may contain (whole word, case-insensitive). The contract is research state.
BANNED_WORDS = ("bet", "bets", "lock", "hammer", "stake", "+ev", "fade", "wager", "kelly", "unit", "units")


class ProtocolMismatch(RuntimeError):
    """The prospective protocol file is not the registered one."""


def canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(payload: Any) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def load_protocol(root: Path) -> dict[str, Any]:
    return json.loads((root / PROTOCOL_PATH).read_text(encoding="utf-8"))


def verify_protocol(protocol: dict[str, Any]) -> dict[str, Any]:
    """The registered protocol, or ProtocolMismatch. Constants here must restate it exactly."""
    if protocol.get("status") != "PRE-REGISTERED":
        raise ProtocolMismatch("protocol is not marked PRE-REGISTERED")
    digest = sha256(protocol)
    if digest != PROTOCOL_SHA256:
        raise ProtocolMismatch(f"protocol hash {digest} != registered {PROTOCOL_SHA256}")
    h = protocol["hypotheses"]
    if h["H1"]["id"] != H1_ID or h["H1"]["wording"] != H1_WORDING:
        raise ProtocolMismatch("H1 differs from the protocol")
    if h["H2"]["id"] != H2_ID or h["H2"]["wording"] != H2_WORDING:
        raise ProtocolMismatch("H2 differs from the protocol")
    if h["H2"]["entry_ask_strictly_below_cents"] != DISAGREEMENT_BELOW_CENTS:
        raise ProtocolMismatch("H2 threshold differs from the protocol")
    cap = protocol["capture"]
    if list(cap["slots_minutes_before_kickoff"]) != list(CAPTURE_SLOTS):
        raise ProtocolMismatch("capture slots differ from the protocol")
    if [cap["window"]["open_minutes_before"], cap["window"]["close_minutes_before"]] != [
        WINDOW_OPEN_MIN,
        WINDOW_CLOSE_MIN,
    ]:
        raise ProtocolMismatch("primary window differs from the protocol")
    if {int(k): v for k, v in protocol["review_checkpoints"].items()} != REVIEW_CHECKPOINTS:
        raise ProtocolMismatch("review checkpoints differ from the protocol")
    return protocol


def load_decisions(root: Path) -> list[dict[str, Any]]:
    """Reviewed human decisions (append-only list). Missing file -> none."""
    path = root / DECISIONS_PATH
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    return list(doc.get("decisions") or [])
