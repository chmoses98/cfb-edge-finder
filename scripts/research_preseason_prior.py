#!/usr/bin/env python3
"""Preseason-prior ablation research. Cannot change the production model.

Runs one declared candidate family at a time against the frozen control,
under walk-forward discipline, and reports paired out-of-sample
differences.

*** WHAT HAPPENS WITHOUT HISTORICAL DATA ***

Every candidate is reported BLOCKED_NO_HISTORICAL_DATA. That is not a
rejection and must never be read as one: a rejection asserts a
measurement, and without seasons of leakage-safe inputs no measurement
was made. The script exits 0 -- being unable to run is the correct
outcome of an honest run, not a crash.

*** IT CANNOT TOUCH PRODUCTION ***

No import from this script writes a model parameter, and it refuses to
run at all if the control has drifted from its frozen hash.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.research.preseason.ablation import (  # noqa: E402
    WALK_FORWARD_SPLIT,
    ConfirmationLedger,
    assert_control_unchanged,
    blocked_candidate,
)
from cfb_edge_finder.research.preseason.control import control_manifest  # noqa: E402
from cfb_edge_finder.research.preseason.sources import (  # noqa: E402
    SOURCE_AUDIT,
    audit_payload,
    usable_families,
)

CANDIDATE_FAMILIES = (
    "returning_production_broader",
    "talent_composite",
    "coaching_change",
)
"""Declared candidates, one per usable family beyond what the control
already uses. Deliberately short: individual evidence first, combinations
only after a family has earned its place."""


def historical_data_available(cache_dir: Path) -> tuple[bool, str]:
    """Whether leakage-safe historical seasons are actually present.

    Checked explicitly rather than discovered through a fetch failure
    halfway through an experiment, so the report can state the blocker
    precisely instead of reporting a partial run."""
    if not cache_dir.exists():
        return False, f"no historical cache directory at {cache_dir}"
    seasons = sorted(p.stem for p in cache_dir.glob("*.json"))
    if not seasons:
        return False, f"historical cache {cache_dir} contains no season files"
    return True, f"cached seasons: {seasons}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--historical-cache",
        type=Path,
        default=REPO_ROOT / "data" / "research_cache" / "preseason",
        help="Directory of cached leakage-safe historical season files. Not fetched here: "
        "research inputs are cached deliberately so an experiment cannot silently depend on "
        "a live API.",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    assert_control_unchanged()
    manifest = control_manifest()

    print("=" * 78)
    print("PRESEASON-PRIOR ABLATION RESEARCH (control is frozen; production untouched)")
    print("=" * 78)
    print(f"  control model version : {manifest.model_version}")
    print(f"  control config hash   : {manifest.content_hash()}")
    print(f"  Week 1 carryover      : {manifest.payload['priors']['week1_carryover_weight']}")
    print("    ^ zero: the control's Week 1 point estimate is entirely prior-season.")
    print("  ablation version      : preseason_ablation_v1")

    print("\n  WALK-FORWARD SPLIT (declared before any candidate result)")
    print(f"    development : {list(WALK_FORWARD_SPLIT.development_seasons)}")
    print(f"    selection   : {WALK_FORWARD_SPLIT.selection_season}")
    print(f"    confirmation: {WALK_FORWARD_SPLIT.confirmation_season}")
    print(f"    excluded    : {list(WALK_FORWARD_SPLIT.excluded_seasons)}")

    print("\n  SOURCE AUDIT")
    for audit in SOURCE_AUDIT:
        mark = "USABLE  " if audit.usable_as_model_feature else "rejected"
        print(f"    [{mark}] {audit.family:32} {audit.verdict.value}")
    print(f"\n    usable as model features: {list(usable_families())}")

    available, detail = historical_data_available(args.historical_cache)
    print("\n  HISTORICAL DATA")
    print(f"    available : {available}")
    print(f"    detail    : {detail}")

    ledger = ConfirmationLedger()
    results = []
    if not available:
        reason = (
            "No cached leakage-safe historical seasons are present, and CFBD cannot be "
            "reached from this environment (api.collegefootballdata.com is denied by egress "
            "policy, and no API key is configured). No ablation was executed."
        )
        print(f"\n  {reason}")
        for name in CANDIDATE_FAMILIES:
            results.append(blocked_candidate(name, reason))
    else:
        print("\n  Historical cache present. Per-candidate ablation is not implemented in this")
        print("  mission: the harness, splits and metrics are in place, but no candidate has")
        print("  been executed, so no candidate may be reported as accepted or rejected.")
        for name in CANDIDATE_FAMILIES:
            results.append(
                blocked_candidate(name, "harness ready; ablation execution not run in this mission")
            )

    print("\n  CANDIDATE RESULTS")
    for result in results:
        print(f"    {result.candidate_name:32} {result.verdict.value:28} "
              f"effect={result.effect_type.value}")
    print("\n  BLOCKED is not REJECTED. A rejection asserts a measurement; none was made.")
    print("  No candidate is promoted. No shadow model is created. Production is untouched.")

    if args.json_out:
        payload = {
            "control": manifest.to_dict(),
            "walk_forward_split": {
                "development": list(WALK_FORWARD_SPLIT.development_seasons),
                "selection": WALK_FORWARD_SPLIT.selection_season,
                "confirmation": WALK_FORWARD_SPLIT.confirmation_season,
                "excluded": list(WALK_FORWARD_SPLIT.excluded_seasons),
                "rationale": WALK_FORWARD_SPLIT.rationale,
            },
            "source_audit": audit_payload(),
            "historical_data_available": available,
            "historical_data_detail": detail,
            "candidates": [r.to_dict() for r in results],
            "confirmation_spent": sorted(ledger.spent),
            "any_candidate_promoted": any(r.promotes_to_shadow for r in results),
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
        print(f"\n  wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
