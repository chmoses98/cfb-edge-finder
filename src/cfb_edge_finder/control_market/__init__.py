"""2026 CONTROL x Kalshi game-winner market-pricing study. RESEARCH ONLY.

Pre-registered in `docs/CONTROL_2026_MARKET_PROTOCOL.md` and
`data/scripting/validation/control_market_2026/protocol.json`. Every constant
below restates that frozen protocol; `verify_protocol` refuses to run when the
protocol file no longer hashes to the registered value.

Nothing in this package publishes, recommends, sizes or stakes. It reads a
frozen football replay, genuinely captured pregame quotes and settled results,
and measures one research contract per CONTROL game.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

STUDY_DIR = Path("data/scripting/validation/control_market_2026")
PROTOCOL_PATH = STUDY_DIR / "protocol.json"
PROTOCOL_SHA256 = "61aa278d86582532556d0174abc2e1c5f883ebaa882285fe603d1ee2af23e1db"
PROTOCOL_COMMIT = "767ed7bc8e35c911e6dd90d6e8b33829cedefc05"
BASE_MAIN_SHA = "be483103e9c6f895047f0a410d64906ba59c00ae"
RESEARCH_DATA_SHA = "292f3aacafe4b892d56ed7c1446225ce85afbc81"
SCRIPT_LEDGER_SHA = "5e0e1d60c4b7a501cab351da8b8fa50737913ff4"

METHODOLOGY_VERSION = "cfb-script-engine/2.0.0"
STUDY_VERSION = "cfb-control-market-study/1.0.0"

POP_REPLAY = "RETROSPECTIVE_FOOTBALL_REPLAY_WITH_PROSPECTIVELY_CAPTURED_MARKET_PRICE"
POP_PROSPECTIVE = "PROSPECTIVE_V2_CONTROL"
POPULATIONS = (POP_REPLAY, POP_PROSPECTIVE)
#: First V2 ledger write; replay covers kickoffs strictly before it.
V2_LEDGER_START = "2026-10-08T01:39:58Z"

TIERS = ("HOME_CONTROL_MODERATE", "HOME_CONTROL_STRONG", "AWAY_CONTROL_MODERATE", "AWAY_CONTROL_STRONG")
POOLS = {
    "ALL_MODERATE": ("HOME_CONTROL_MODERATE", "AWAY_CONTROL_MODERATE"),
    "ALL_STRONG": ("HOME_CONTROL_STRONG", "AWAY_CONTROL_STRONG"),
    "ALL_CONTROL": TIERS,
}

#: Game-winner series only. No spread, total, team total, half, margin or ladder.
SERIES = "KXNCAAFGAME"

#: (name, lower minutes before kickoff, upper minutes before kickoff, lower inclusive, upper inclusive)
PRIMARY_CHECKPOINT = "PRIMARY_60_180"
CHECKPOINT_WINDOWS: dict[str, tuple[float, float, bool, bool]] = {
    # minutes BEFORE kickoff: a quote at m minutes before kickoff is inside when lo <= m <= hi (per flags)
    "W_180_360": (180.0, 360.0, False, True),  # kickoff-360 <= t < kickoff-180
    PRIMARY_CHECKPOINT: (60.0, 180.0, True, True),  # kickoff-180 <= t <= kickoff-60
    "W_15_60": (15.0, 60.0, True, False),  # kickoff-60 < t <= kickoff-15
    "CLOSING": (0.0, 60.0, False, False),  # kickoff-60 < t < kickoff
}
CHECKPOINTS = ("EARLY_OPEN", "W_180_360", PRIMARY_CHECKPOINT, "W_15_60", "CLOSING")

#: Entry-price buckets in whole cents, inclusive.
PRICE_BUCKETS: tuple[tuple[int, int], ...] = (
    (1, 49),
    (50, 59),
    (60, 69),
    (70, 79),
    (80, 84),
    (85, 89),
    (90, 94),
    (95, 99),
)
SEASON_BLOCKS: dict[str, tuple[int, int]] = {
    "BLOCK_1_WEEKS_1_2": (1, 2),
    "BLOCK_2_WEEKS_3_4": (3, 4),
    "BLOCK_3_WEEKS_5_PLUS": (5, 99),
}

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261008
BOOTSTRAP_MIN_N = 5
VERDICT_MIN_N = 20
EVIDENCE_MIN_N = 50
EVIDENCE_MIN_CLV_N = 10

#: One research contract, always. A sizing parameter appearing anywhere fails a test.
RESEARCH_UNIT_CONTRACTS = 1

EXCLUSIONS = (
    "NO_GAME_WINNER_MARKET",
    "ORIENTATION_UNRESOLVED",
    "ORIENTATION_CONFLICT",
    "ENTRY_QUOTE_UNAVAILABLE",
    "FEE_UNAVAILABLE",
    "SETTLEMENT_UNAVAILABLE",
    "SETTLEMENT_MISMATCH",
)

VERDICTS = (
    "EVIDENCE_OF_UNDERPRICING",
    "POSSIBLE_UNDERPRICING",
    "APPROXIMATELY_EFFICIENT",
    "OVERPRICED",
    "INSUFFICIENT_DATA",
)

#: Words no conclusion field may contain (whole-word, case-insensitive).
BANNED_CONCLUSION_WORDS = ("bet", "play", "hammer", "stake", "+ev")

#: Paths this study must never write under (live publication, prospective ledger, production inputs).
FORBIDDEN_OUTPUT_PREFIXES = (
    "data/scripting/live",
    "data/scripting/ledger",
    "data/scripting/calibration",
    "data/live",
    "data/football",
    "app",
)


class ProtocolMismatch(RuntimeError):
    """The protocol file is not the registered one."""


def canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(payload: Any) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def load_protocol(root: Path) -> dict[str, Any]:
    return json.loads((root / PROTOCOL_PATH).read_text(encoding="utf-8"))


def verify_protocol(protocol: dict[str, Any]) -> dict[str, Any]:
    if protocol.get("status") != "PRE-REGISTERED":
        raise ProtocolMismatch("protocol is not marked PRE-REGISTERED")
    digest = sha256(protocol)
    if digest != PROTOCOL_SHA256:
        raise ProtocolMismatch(f"protocol hash {digest} != registered {PROTOCOL_SHA256}")
    if [list(b) for b in PRICE_BUCKETS] != protocol["price_buckets_cents"]:
        raise ProtocolMismatch("price buckets differ from the protocol")
    if protocol["uncertainty"]["seed"] != BOOTSTRAP_SEED or protocol["uncertainty"]["resamples"] != BOOTSTRAP_RESAMPLES:
        raise ProtocolMismatch("bootstrap settings differ from the protocol")
    if {k: list(v) for k, v in SEASON_BLOCKS.items()} != protocol["season_blocks_by_log_week"]:
        raise ProtocolMismatch("season blocks differ from the protocol")
    return protocol


def assert_output_path(path: Path | str) -> None:
    text = Path(path).as_posix()
    for prefix in FORBIDDEN_OUTPUT_PREFIXES:
        if f"/{prefix}/" in f"/{text}/" or text.startswith(prefix + "/") or text == prefix:
            raise PermissionError(f"control-market research may not write under {prefix}: {text}")
