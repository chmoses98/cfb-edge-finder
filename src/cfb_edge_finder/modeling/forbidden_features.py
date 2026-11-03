"""Forbidden handicapping inputs, and the one explicit register of where the word still appears.

OWNER RULE (2026-11-02): a havoc metric is never a handicapping input -- not in a live handicap, not in a shadow
model, not in any model built from now on.

WHAT THIS MODULE DOES
* ``assert_feature_names_clean`` -- every new feature spec / model builder calls it; it raises on any
  forbidden token (case-insensitive substring).
* ``HAVOC_MENTION_REGISTER`` -- the COMPLETE list of repository files that may mention the token today, each with
  the reason. ``tests/test_forbidden_features.py`` scans ``src``, ``scripts`` and ``config`` and fails when a file
  not in the register mentions it, so a new model cannot pick the feature up silently.

WHAT IT DELIBERATELY DOES NOT DO
The frozen v2 shadow builder (``modeling/v2/research_features.py``, hash-pinned by ``tests/test_v2_shadow.py``)
still names ``b_havoc_def``. It is NOT edited: silently changing a pinned model would change what its recorded
predictions mean. It is registered here as LEGACY, with its sha256, and ``modeling/v3/feature_spec.py`` is the
havoc-free successor spec. Refitting a v3 shadow model needs owner approval (see docs/HAVOC_POLICY.md).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

FORBIDDEN_FEATURE_TOKENS: tuple[str, ...] = ("havoc",)
_PATTERN = re.compile("|".join(re.escape(t) for t in FORBIDDEN_FEATURE_TOKENS), re.IGNORECASE)

#: file (repo-relative) -> (kind, reason). ``LEGACY_PINNED`` files additionally carry a sha256 in ``LEGACY_PINNED_SHA256``.
HAVOC_MENTION_REGISTER: dict[str, tuple[str, str]] = {
    "src/cfb_edge_finder/modeling/v2/research_features.py": (
        "LEGACY_PINNED", "frozen v2 shadow feature builder (b_havoc_def); hash-pinned, never edited"),
    "scripts/research_export.py": (
        "RAW_DATA_EXPORT", "exports CFBD season-level columns as raw research data; not a model input by itself"),
    "src/cfb_edge_finder/scripting/metrics.py": (
        "DISPLAY_ONLY", "descriptive metric definition kept for reports; excluded from every edge/context table"),
    "src/cfb_edge_finder/scripting/matchup.py": (
        "EXCLUSION_COMMENT", "documents that havoc is removed from the edge table"),
    "src/cfb_edge_finder/archetype_research/analysis.py": (
        "LEGACY_RESEARCH", "archetype research already published (docs/ARCHETYPE_*); not used by RUN CFB"),
    "src/cfb_edge_finder/projections/discrete_scoring.py": ("EXCLUSION_COMMENT", "states the exclusion"),
    "src/cfb_edge_finder/research/multiseason_calibration.py": ("EXCLUSION_COMMENT", "states the exclusion"),
    "src/cfb_edge_finder/decision/owner_rules.py": ("RULE_DEFINITION", "defines the ban and its scanner"),
    "src/cfb_edge_finder/modeling/forbidden_features.py": ("RULE_DEFINITION", "this register"),
    "src/cfb_edge_finder/modeling/v3/feature_spec.py": ("RULE_DEFINITION", "removes the v2 havoc names"),
}

#: sha256 of the legacy files at the time of registration. A changed legacy file fails the test.
LEGACY_PINNED_SHA256: dict[str, str] = {
    "src/cfb_edge_finder/modeling/v2/research_features.py":
        "82fdf414d94c885de50744bdc9148cc888881528046415afccc971ba243bacce",
}


def forbidden_in(names: list[str] | tuple[str, ...]) -> list[str]:
    return sorted({str(n) for n in names if _PATTERN.search(str(n))})


def assert_feature_names_clean(names: list[str] | tuple[str, ...], *, spec_id: str) -> None:
    """Raise if any feature/input name in ``names`` carries a forbidden token. Call it from every new spec."""
    bad = forbidden_in(names)
    if bad:
        raise ValueError(f"{spec_id}: forbidden handicapping input(s) {bad} (owner rule: havoc is never an input)")


def file_mentions_forbidden(path: Path) -> bool:
    try:
        return bool(_PATTERN.search(path.read_text(encoding="utf-8", errors="ignore")))
    except OSError:
        return False


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan_repo(root: Path, subdirs: tuple[str, ...] = ("src", "scripts", "config")) -> list[str]:
    """Repo-relative files under ``subdirs`` that mention a forbidden token and are NOT in the register."""
    offenders: list[str] = []
    for sub in subdirs:
        base = root / sub
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts or p.suffix in {".pyc", ".parquet", ".png"}:
                continue
            rel = p.relative_to(root).as_posix()
            if rel not in HAVOC_MENTION_REGISTER and file_mentions_forbidden(p):
                offenders.append(rel)
    return offenders
