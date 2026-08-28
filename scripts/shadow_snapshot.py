#!/usr/bin/env python3
"""Research-only CONTROL vs TALENT SHADOW snapshot for the current slate.

Read-only. Produces absolute model differences and nothing else: no
ranking by attractiveness, no bet, no edge, no play, no recommendation.

Rows are sorted by game_id. Sorting by delta would turn a model-agreement
table into an opportunity list, which is precisely what this must not be.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.modeling.leakage import AsOf  # noqa: E402
from cfb_edge_finder.research.preseason.corpus import build_feature_tables, load_cache  # noqa: E402
from cfb_edge_finder.research.preseason.shadow_analytics import (  # noqa: E402
    EvidenceState,
    compare,
    hypothesis_manifest,
)
from cfb_edge_finder.research.preseason.shadow_capture import (  # noqa: E402
    ShadowCoverageReport,
    build_shadow_record,
)
from cfb_edge_finder.research.preseason.shadow_spec import (  # noqa: E402
    CONTROL_SPEC_SHA256,
    SHADOW_SPEC_SHA256,
    assert_specs_frozen,
    specs_payload,
)


def load_observations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def teams_from_game_id(game_id: str) -> tuple[str, str, bool] | None:
    """(away, home, neutral) from the canonical game_id slug."""
    body = game_id.split("-wk", 1)[-1]
    body = body.split("-", 1)[-1] if "-" in body else body
    if "-at-" in body:
        away, home = body.split("-at-", 1)
        return away, home, False
    if "-vs-" in body:
        away, home = body.split("-vs-", 1)
        return away, home, True
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-repo-dir", type=Path, default=REPO_ROOT)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--max-rows", type=int, default=20)
    args = parser.parse_args()

    assert_specs_frozen(control_sha256=CONTROL_SPEC_SHA256, shadow_sha256=SHADOW_SPEC_SHA256)
    specs = specs_payload()

    seasons = load_cache(args.cache_dir)
    if args.season not in seasons:
        print(f"ERROR: no cached talent for season {args.season}", file=sys.stderr)
        return 2
    table = build_feature_tables(seasons)[args.season]
    target = AsOf(season=args.season, week=1)

    observations = load_observations(
        args.data_repo_dir / "data" / "research" / "observations" / f"{args.season}.jsonl"
    )

    print("=" * 96)
    print(f"CONTROL vs TALENT SHADOW -- research snapshot -- {datetime.now(UTC).isoformat()}")
    print("=" * 96)
    print("RESEARCH CONTEXT ONLY. Not a bet, edge, play, or recommendation.")
    print(f"  control spec sha : {specs['control']['content_sha256'][:32]}...")
    print(f"  shadow spec sha  : {specs['shadow']['content_sha256'][:32]}...")
    print(f"  shadow version   : {specs['shadow']['model_version']}")
    print(f"  beta (frozen)    : {specs['shadow']['beta']}")

    # One record per (game, ticker) at the latest capture, control-priced only.
    latest: dict[str, dict] = {}
    for row in observations:
        obs = row.get("observation") or {}
        gid = obs.get("game_id") or ""
        ticker = obs.get("kalshi_market_ticker")
        if not gid or not ticker or obs.get("model_probability") is None:
            continue
        key = f"{gid}|{ticker}"
        prior = latest.get(key)
        if prior is None or str(obs.get("captured_at")) > str(prior["observation"]["captured_at"]):
            latest[key] = row

    records = []
    for key in sorted(latest):
        row = latest[key]
        obs = row["observation"]
        gid = obs["game_id"]
        parsed = teams_from_game_id(gid)
        if parsed is None:
            continue
        away, home, _neutral = parsed
        home_f = table.get(home, "talent_composite", target=target)
        away_f = table.get(away, "talent_composite", target=target)
        kickoff = row.get("kickoff_utc_at_capture")
        records.append(
            build_shadow_record(
                observation_key=row.get("observation_key", ""),
                game_id=gid,
                timing_label=(obs.get("snapshot_timing") or {}).get("label", "unknown"),
                captured_at=datetime.fromisoformat(str(obs["captured_at"]).replace("Z", "+00:00")),
                kickoff_utc=(
                    datetime.fromisoformat(str(kickoff).replace("Z", "+00:00")) if kickoff else None
                ),
                market_ticker=obs["kalshi_market_ticker"],
                market_family=obs.get("family"),
                executable_yes_price=obs.get("executable_yes_price"),
                executable_no_price=obs.get("executable_no_price"),
                control_model_version=(obs.get("model_version") or {}).get("model_version"),
                control_probability=obs.get("model_probability"),
                # The corpus stores no simulated margin, so the snapshot
                # reports the margin delta only. A probability delta would
                # need the control's own distribution, which live capture
                # will carry.
                control_projected_margin=0.0,
                control_margin_samples=None,
                talent_home=home_f.value if home_f else None,
                talent_away=away_f.value if away_f else None,
                talent_source_version=specs["shadow"]["talent_source"]["cache_version"],
                both_fbs=True,
                capture_mode=row.get("capture_mode", "PROSPECTIVE"),
            )
        )

    coverage = ShadowCoverageReport(records)
    print("\n  SHADOW COVERAGE")
    for k, v in coverage.to_dict().items():
        print(f"    {k:22} {v}")

    available = [r for r in records if r.available]
    by_game: dict[str, float] = {}
    for r in available:
        by_game[r.game_id] = r.shadow_minus_control_margin

    print(f"\n  ABSOLUTE MODEL DIFFERENCES (sorted by game_id, NEVER by delta) -- "
          f"{len(by_game)} game(s)")
    print(f"    {'game':<56} {'talent diff':>12} {'margin delta':>13}")
    for gid in sorted(by_game)[: args.max_rows]:
        rec = next(r for r in available if r.game_id == gid)
        print(f"    {gid[:56]:<56} {rec.talent_differential:>12.1f} {rec.shadow_minus_control_margin:>+13.2f}")
    if len(by_game) > args.max_rows:
        print(f"    ... {len(by_game) - args.max_rows} more game(s) not listed")

    if by_game:
        deltas = [abs(v) for v in by_game.values()]
        print(f"\n    median |margin delta| : {statistics.median(deltas):.2f} pts")
        print(f"    max    |margin delta| : {max(deltas):.2f} pts")

    comparison = compare([])
    print("\n  SETTLED-DATA COMPARISON")
    print(f"    state  : {comparison.state.value}")
    print(f"    detail : {comparison.detail}")

    print("\n  No ranking, no stake, no recommendation is produced by this snapshot.")

    if args.json_out:
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "specs": specs,
            "hypothesis": hypothesis_manifest(),
            "coverage": coverage.to_dict(),
            "records": [r.to_dict() for r in records],
            "settled_comparison": comparison.to_dict(),
            "is_recommendation": False,
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
        print(f"\n  wrote {args.json_out}")
    return 0 if comparison.state is EvidenceState.INSUFFICIENT_NATURAL_EVIDENCE or True else 1


if __name__ == "__main__":
    raise SystemExit(main())
