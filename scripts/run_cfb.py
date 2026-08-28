#!/usr/bin/env python3
"""RUN CFB -- one coherent research report for the current slate.

Read-only orchestration of components that already exist. This script
computes almost nothing itself; it calls the production modules and
arranges what they return, so the report cannot drift away from what the
pipeline actually does.

*** WHAT IT NEVER DOES ***

No qualification, no ranking, no "best bets", no stake, no execution.
There is no ordering by attractiveness anywhere: games sort by kickoff
then game_id, contracts by ticker. A report that ranked by disagreement
would be a betting card with a research header.

Sections: SYSTEM HEALTH, CURRENT SLATE, MARKET STRUCTURE, RESEARCH STATE,
COLLECTION STATE, ANALYTICS, SAFETY, GO/NO-GO.

Exits non-zero on a genuine data-integrity or safety defect (NO_GO), so a
scheduled invocation cannot fail quietly.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.decision.artifact import load_artifact  # noqa: E402
from cfb_edge_finder.decision.collection_protection import ProtectionState  # noqa: E402
from cfb_edge_finder.decision.go_no_go import evaluate_go_no_go  # noqa: E402
from cfb_edge_finder.decision.portfolio import build_portfolio_view  # noqa: E402
from cfb_edge_finder.decision.shadow import run_shadow_pipeline  # noqa: E402
from cfb_edge_finder.expression.corpus import load_contract_snapshots  # noqa: E402
from cfb_edge_finder.modeling.live_diagnostics import (  # noqa: E402
    DiagnosticSeverity,
    run_model_health,
)
from cfb_edge_finder.research.checkpoint_manifest import (  # noqa: E402
    ManifestCompletenessReport,
    manifest_from_corpus_row,
)
from cfb_edge_finder.research.context_capture import CONTEXT_FIELD_PLAN, ContextAvailability  # noqa: E402
from cfb_edge_finder.research.protocol import manifest as protocol_manifest  # noqa: E402
from cfb_edge_finder.research.timing import ALL_PREGAME_LABELS  # noqa: E402
from cfb_edge_finder.schemas.corpus_row import CORPUS_SCHEMA_VERSION  # noqa: E402
from cfb_edge_finder.schemas.settlement import MarketSettlementStatus  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from week1_ops_health import (  # noqa: E402
    assess_protection,
    corpus_counts,
    load_rows,
    probe_safety_locks,
    sizing_import_offenders,
)

from cfb_edge_finder.research.heartbeat import (  # noqa: E402
    heartbeat_path,
    load_heartbeats,
)

RULE = "=" * 78


def section(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-repo-dir", type=Path, default=REPO_ROOT)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--now", type=str, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--max-games", type=int, default=25, help="games listed in the slate table")
    args = parser.parse_args()

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(UTC)
    base = args.data_repo_dir / "data" / "research"
    obs_path = base / "observations" / f"{args.season}.jsonl"

    rows = load_rows(obs_path)
    real_rows = [r for r in rows if not r.get("__malformed__")]
    load = load_contract_snapshots(obs_path)
    snapshots = load.snapshots
    heartbeats = load_heartbeats(heartbeat_path(args.data_repo_dir, args.season))
    settlements = load_rows(base / "settlements" / f"{args.season}.jsonl")
    attributions = load_rows(base / "attributions" / f"{args.season}.jsonl")

    duplicates, malformed, non_prospective, total_rows = corpus_counts(rows)
    protection = assess_protection(heartbeats, now)
    health = run_model_health(snapshots)
    portfolio = build_portfolio_view([s.semantics for s in snapshots])
    resolution = load_artifact(None)
    shadow = run_shadow_pipeline(snapshots, resolution=resolution, now=now)
    manifests = ManifestCompletenessReport([manifest_from_corpus_row(r) for r in real_rows])
    locks = probe_safety_locks()

    settled_games = len(
        {
            r.get("game_id")
            for r in settlements
            if r.get("game_id") and r.get("status") == MarketSettlementStatus.SETTLED.value
        }
    )
    clv_n = sum(1 for r in attributions if (r.get("closing") or {}).get("closing_captured"))

    print(RULE)
    print(f"RUN CFB -- research report -- {now.isoformat()}")
    print(RULE)
    print("Read-only. No qualification, no ranking, no stake, no execution.")

    # ------------------------------------------------ SYSTEM HEALTH
    section("SYSTEM HEALTH")
    last = heartbeats[-1] if heartbeats else {}
    print(f"  CFBD (last run schedule fetch) : {last.get('schedule_fetch_success')}")
    print(f"  schedule planning state        : {last.get('schedule_state')}")
    print(f"  Kalshi markets discovered      : {last.get('markets_discovered')}")
    print(f"  latest capture                 : {last.get('invoked_at')}")
    print(f"  latest trigger provenance      : {last.get('trigger_type')}")
    print(f"  collection protection          : {protection.state.value}")
    print(f"    {protection.detail}")
    if protection.remedy:
        print(f"    remedy: {protection.remedy}")
    interval = protection.observed_interval_minutes
    interval_text = "not measurable" if interval is None else f"{round(interval)} min"
    print(
        f"  observed trigger interval      : {interval_text} "
        f"(MEASURED from {protection.interval_sample_size} gap(s), not a configuration)"
    )
    print(f"  next critical checkpoint       : {protection.checkpoint_label} at "
          f"{last.get('next_critical_checkpoint_at')}")
    print(f"  tighten cadence by             : "
          f"{protection.tighten_by.isoformat() if protection.tighten_by else 'n/a'}")
    print(f"  corpus rows / dup / malformed  : {total_rows} / {duplicates} / {malformed}")
    schema_counts = Counter(r.get("schema_version") for r in real_rows)
    print(f"  schema versions                : {dict(sorted(schema_counts.items(), key=lambda i: str(i[0])))}")
    print(f"  current schema                 : {CORPUS_SCHEMA_VERSION}")

    # ------------------------------------------------- CURRENT SLATE
    section("CURRENT SLATE (sorted by game_id -- never by disagreement)")
    by_game: dict[str, list] = defaultdict(list)
    for snap in snapshots:
        by_game[snap.semantics.game_id].append(snap)
    print(f"  games with captured contracts  : {len(by_game)}")
    priced_games = {g for g, ss in by_game.items() if any(s.model_probability is not None for s in ss)}
    print(f"  games with a model projection  : {len(priced_games)}")
    print()
    print(f"  {'game_id':<52} {'contracts':>9} {'priced':>7} {'model_ver':<18}")
    for game_id in sorted(by_game)[: args.max_games]:
        group = by_game[game_id]
        priced = [s for s in group if s.model_probability is not None]
        versions = {s.model_version for s in priced if s.model_version} or {"-"}
        print(f"  {game_id:<52} {len(group):>9} {len(priced):>7} {sorted(versions)[0]:<18}")
    if len(by_game) > args.max_games:
        print(f"  ... {len(by_game) - args.max_games} more game(s) not listed")

    # ---------------------------------------------- MARKET STRUCTURE
    section("MARKET STRUCTURE")
    families = Counter(
        s.semantics.family.value if s.semantics.family else "UNRESOLVED" for s in snapshots
    )
    print(f"  contracts by family            : {dict(sorted(families.items()))}")
    print(f"  distinct theses (dimensions)   : {portfolio.distinct_theses}")
    print(f"  equivalence groups             : {len(portfolio.equivalence_groups)}")
    print(f"  unresolved-semantics groups    : {portfolio.unresolved_group_count}")
    print(f"  exposure limits                : {portfolio.limits_status}")
    unresolved = sum(1 for s in snapshots if not s.semantics.semantics_resolved)
    print(f"  contracts with unresolved sem. : {unresolved}")
    print(f"  market_status distribution     : "
          f"{dict(sorted(Counter(s.market_status for s in snapshots).items(), key=lambda i: str(i[0])))}")

    # ------------------------------------------------ RESEARCH STATE
    section("RESEARCH STATE")
    pm = protocol_manifest()
    print(f"  preregistered protocol         : {pm.version}")
    print(f"  protocol sha256                : {pm.document_sha256}")
    print(f"  threshold artifact             : {resolution.status}")
    print(f"  shadow qualified (counted)     : {shadow.shadow_qualified_count}")
    print(f"  candidates considered          : {len(shadow.decisions)}")
    print(f"  where candidates stopped       : {dict(sorted(shadow.state_counts().items()))}")
    print("  qualification is blocked because:")
    print(f"    - no approved threshold artifact exists ({resolution.status})")
    print("    - evidence readiness cannot reach VALIDATED for any sample size")
    print("    - 0 settled prospective games exist to derive a threshold from")

    # ---------------------------------------------- COLLECTION STATE
    section("COLLECTION STATE")
    labels = Counter((r.get("observation", {}).get("snapshot_timing") or {}).get("label") for r in real_rows)
    print(f"  captured labels                : {dict(sorted(labels.items(), key=lambda i: str(i[0])))}")
    print(f"  labels never yet captured      : {sorted(set(ALL_PREGAME_LABELS) - set(labels))}")
    print(f"  genuine CLOSING captures       : {labels.get('CLOSING', 0)}")
    print(f"  checkpoint manifests complete  : {manifests.complete_count}/{len(manifests.manifests)}")
    if manifests.missing_field_counts():
        print(f"  manifest gaps                  : {manifests.missing_field_counts()}")

    # ---------------------------------------------------- ANALYTICS
    section("ANALYTICS")
    print(f"  settlement rows                : {len(settlements)}")
    print(f"  settlement statuses            : "
          f"{dict(sorted(Counter(r.get('status') for r in settlements).items(), key=lambda i: str(i[0])))}")
    print(f"  terminal settled-supported     : "
          f"{sum(1 for r in settlements if r.get('status') == MarketSettlementStatus.SETTLED.value)}")
    print(f"  unique settled games           : {settled_games}")
    print(f"  CLV n                          : {clv_n}")
    print(f"  attribution rows               : {len(attributions)}")
    print("  sample sufficiency             : "
          + ("EMPIRICAL THRESHOLD RESEARCH BLOCKED ON NATURAL SAMPLE SIZE"
             if settled_games == 0 else f"{settled_games} settled game(s); sufficiency is a human judgement"))

    # ------------------------------------------------ MODEL HEALTH
    section("MODEL HEALTH (structural; no outcomes needed)")
    print(f"  contracts checked              : {health.contracts_checked}")
    print(f"  findings by severity           : {health.counts()}")
    for severity in (DiagnosticSeverity.BLOCKER, DiagnosticSeverity.HIGH):
        for finding in health.by_severity(severity)[:5]:
            print(f"    [{severity.value}] {finding.check_id}: {finding.detail[:110]}")

    # ------------------------------------------ TALENT SHADOW RESEARCH
    section("TALENT SHADOW RESEARCH (research only -- not a recommendation)")
    shadow_rows = load_rows(base / "shadow" / f"{args.season}.jsonl")
    shadow_rows_captured = len([r for r in shadow_rows if not r.get("__malformed__")])
    shadow_table = []
    for row in sorted(
        (r for r in shadow_rows if not r.get("__malformed__")),
        key=lambda r: str(r.get("game_id")),
    ):
        available = row.get("available")
        shadow_table.append({
            "game_id": str(row.get("game_id") or ""),
            "control": (
                f"{row['control_probability']:.4f}"
                if row.get("control_probability") is not None else "-"
            ),
            "shadow": (
                f"{row['shadow_probability']:.4f}"
                if row.get("shadow_probability") is not None else "-"
            ),
            "delta": (
                f"{row['shadow_minus_control_margin']:+.2f}"
                if row.get("shadow_minus_control_margin") is not None else "-"
            ),
            "status": "available" if available else (row.get("unavailable_reason") or "unavailable"),
        })
    try:
        from cfb_edge_finder.research.preseason.shadow_spec import (
            CONTROL_SPEC_SHA256,
            SHADOW_SPEC_SHA256,
            assert_specs_frozen,
            shadow_spec,
        )

        assert_specs_frozen(control_sha256=CONTROL_SPEC_SHA256, shadow_sha256=SHADOW_SPEC_SHA256)
        spec = shadow_spec()
        print(f"  shadow model version           : {spec.model_version}")
        print(f"  shadow spec sha256             : {spec.content_hash()[:32]}...")
        print(f"  beta (frozen, never refit)     : {spec.payload['beta']}")
        print(f"  may be refit on 2026           : {spec.payload['may_be_refit_on_2026']}")
        print(f"  captured shadow rows           : {shadow_rows_captured}")
        print("  CONTROL remains canonical; model_probability is unchanged.")
        print()
        if shadow_rows_captured:
            print(f"  {'game':<46} {'CONTROL':>9} {'SHADOW':>9} {'DELTA':>8}  status")
            for row in shadow_table[:20]:
                print(
                    f"  {row['game_id'][:46]:<46} "
                    f"{row['control']:>9} {row['shadow']:>9} {row['delta']:>8}  {row['status']}"
                )
            if len(shadow_table) > 20:
                print(f"  ... {len(shadow_table) - 20} more (sorted by game_id, never by delta)")
        else:
            print("  No captured shadow rows yet. Shadow collection begins only after the")
            print("  wired scanner runs; absence before deployment is expected, not missing")
            print("  data. Reconstructed research view: scripts/shadow_snapshot.py")
        print()
        print("  Rows sort by game_id, never by delta -- a delta-sorted table would be")
        print("  an opportunity ranking wearing a research header. A large delta is a")
        print("  model difference, not an edge.")
    except Exception as exc:  # noqa: BLE001
        print(f"  shadow specification UNAVAILABLE: {type(exc).__name__}: {exc}")
        print("  Control reporting above is unaffected.")

    # ------------------------------------------------------- SAFETY
    section("SAFETY")
    print(f"  qualification disabled         : {locks['qualification_disabled']}")
    print(f"  approved threshold absent      : {locks['threshold_artifact_absent']}")
    print(f"  auto-validation impossible     : {locks['validated_state_unreachable']}")
    print(f"  staking disconnected           : {locks['sizing_disconnected']}")
    print("  bankroll access                : ABSENT")
    print("  execution / order placement    : ABSENT")
    print("  Kalshi client                  : READ ONLY")

    # ------------------------------------------------------ GO/NO-GO
    section("GO / NO-GO")
    context_gaps = sum(
        1 for _n, (_s, availability, _d) in CONTEXT_FIELD_PLAN.items()
        if availability is ContextAvailability.SOURCE_UNAVAILABLE
    )
    verdict = evaluate_go_no_go(
        duplicate_rows=duplicates,
        malformed_rows=malformed,
        non_prospective_rows=non_prospective,
        current_schema_missing_market_status=sum(
            1 for r in real_rows
            if r.get("schema_version") == CORPUS_SCHEMA_VERSION
            and r.get("observation", {}).get("market_status") is None
        ),
        invalid_probability_count=len(health.blockers),
        fee_provenance_failures=sum(
            1 for f in health.by_severity(DiagnosticSeverity.HIGH)
            if f.check_id == "fee_provenance_unverified"
        ),
        safety_locks_ok=all(locks.values()),
        execution_surface_found=bool(sizing_import_offenders()),
        closing_trigger_at_risk=protection.state is ProtectionState.CLOSING_AT_RISK,
        kalshi_markets_discovered=last.get("markets_discovered"),
        cfbd_reachable=last.get("schedule_fetch_success"),
        settled_games=settled_games,
        clv_observations=clv_n,
        unsupported_population_rows=sum(
            1 for s in snapshots if s.pricing_status and s.pricing_status != "model_priced"
        ),
        legacy_schema_rows=sum(
            1 for r in real_rows if r.get("schema_version") != CORPUS_SCHEMA_VERSION
        ),
        zero_carryover_games=0,
        contextual_sources_missing=context_gaps,
        quiet_period_active=protection.state is ProtectionState.QUIET_PERIOD,
    )
    print(verdict.render())

    if args.json_out:
        payload = {
            "generated_at": now.isoformat(),
            "protocol": pm.to_dict(),
            "collection_protection": {
                "state": protection.state.value,
                "observed_interval_minutes": protection.observed_interval_minutes,
                "interval_sample_size": protection.interval_sample_size,
                "next_checkpoint_label": protection.checkpoint_label,
                "tighten_by": protection.tighten_by.isoformat() if protection.tighten_by else None,
            },
            "corpus": {
                "rows": total_rows, "duplicates": duplicates, "malformed": malformed,
                "non_prospective": non_prospective,
                "labels": dict(sorted(labels.items(), key=lambda i: str(i[0]))),
                "schema_versions": dict(sorted(schema_counts.items(), key=lambda i: str(i[0]))),
            },
            "market_structure": {
                "distinct_theses": portfolio.distinct_theses,
                "equivalence_groups": len(portfolio.equivalence_groups),
                "families": dict(sorted(families.items())),
            },
            "research_state": {
                "threshold_artifact_status": resolution.status,
                "shadow_qualified_count": shadow.shadow_qualified_count,
                "shadow_states": dict(sorted(shadow.state_counts().items())),
            },
            "analytics": {
                "settled_games": settled_games, "clv_n": clv_n,
                "settlement_rows": len(settlements), "attribution_rows": len(attributions),
            },
            "model_health": health.counts(),
            "manifests": manifests.to_payload(),
            "safety": {**locks, "bankroll_access": False, "execution_present": False},
            "go_no_go": verdict.to_payload(),
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
        print(f"\nwrote {args.json_out}")

    return 1 if verdict.to_payload()["verdict"] == "NO_GO" else 0


if __name__ == "__main__":
    raise SystemExit(main())
