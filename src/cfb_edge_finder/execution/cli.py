"""The live workflow, as commands.

    python -m cfb_edge_finder.execution collect-context --catalog-dir data/live
    python -m cfb_edge_finder.execution prepare-live
    python -m cfb_edge_finder.execution batches
    python -m cfb_edge_finder.execution evaluate --batch early_b1 --handicaps <file>
    python -m cfb_edge_finder.execution candidates --batch early_b1
    python -m cfb_edge_finder.execution status --shard early
    python -m cfb_edge_finder.execution report --shard early

Every command is idempotent and every intermediate result is a file on
disk, because the failure this workflow must survive is "the handicapping
session died halfway through a 100-game slate".

*** THE UNIT OF WORK IS A BATCH, NOT A WINDOW ***
`--batch early_b1` is five to eight games' worth of reasoning, and the
commands that take it are designed to be run one batch at a time: handicap
a batch, evaluate it, read its candidates, act on them, move on. Nothing
waits for the rest of the window, and `evaluate` reuses every game that is
already finished against the packet currently on disk.

`--shard early` still works everywhere `--batch` does, and means the whole
kickoff window.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cfb_edge_finder.execution.batching import (
    DEFAULT_BATCH_COST_BUDGET,
    DEFAULT_MAX_CONTEXT_BYTES,
    batch_document,
    build_batches,
)
from cfb_edge_finder.execution.candidates import reduce_candidates
from cfb_edge_finder.execution.disposition import (
    DEFAULT_MAX_CAPTURE_AGE_MINUTES,
    DispositionConfig,
)
from cfb_edge_finder.execution.evaluator import (
    CANDIDATE_STATUSES,
    DEFAULT_MIN_NET_EDGE,
    GameEvaluation,
    evaluate_game,
    ledger_document,
)
from cfb_edge_finder.execution.handicap import (
    HandicapValidationError,
    load_handicaps,
    parse_handicap,
    template_for_packet,
)
from cfb_edge_finder.execution.report import (
    ShardGateError,
    build_candidate_artifact,
    build_reduction_ledger,
    build_report,
    render_report,
)
from cfb_edge_finder.execution.shards import (
    DEFAULT_MAX_ANALYSIS_BYTES,
    DEFAULT_MAX_SHARD_CONTRACTS,
    build_shards,
    write_shards,
)
from cfb_edge_finder.execution.slate import build_slate
from cfb_edge_finder.execution.state import StateStore, eligible_ticker_hash, next_incomplete
from cfb_edge_finder.execution.windows import DEFAULT_TIMEZONE, WINDOW_ORDER

DEFAULT_CATALOG_DIR = Path("data/live")
DEFAULT_OUT_DIR = Path("data/execution/latest")
DEFAULT_CONTEXT_DIR = Path("data/live/context")


def _write(path: Path, payload: Any, *, compact: bool = False) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str) + "\n"
    else:
        encoded = json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n"
    path.write_text(encoded, encoding="utf-8")
    return len(encoded.encode("utf-8"))


def _read(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _as_of(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _local_today(moment: datetime, tz_name: str) -> str:
    from zoneinfo import ZoneInfo

    try:
        return moment.astimezone(ZoneInfo(tz_name)).date().isoformat()
    except Exception:  # noqa: BLE001
        return moment.date().isoformat()


def _batch_manifest(out_dir: Path) -> dict[str, Any]:
    path = out_dir / "batch_manifest.json"
    if not path.exists():
        return {"batches": []}
    return _read(path)


def _resolve_scope(out_dir: Path, args: argparse.Namespace) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """(label, packets, the shard document those packets came from).

    A BATCH is a subset of a shard's games, so it resolves through the shard
    file on disk rather than through a second copy of the contracts. That is
    not an optimisation: two files holding the same contracts is two files
    that can disagree about what the eligible universe was, and the whole
    reconciliation rests on there being one answer.
    """
    batch = getattr(args, "batch", None)
    if batch:
        manifest = _batch_manifest(out_dir)
        entry = next((b for b in manifest.get("batches") or [] if b["batch"] == batch), None)
        if entry is None:
            available = ", ".join(b["batch"] for b in manifest.get("batches") or [])
            raise SystemExit(f"no batch {batch!r}. Available: {available or '(none)'}")
        shard_doc = _read(_shard_path(out_dir, entry["shard"]))
        wanted = set(entry["game_keys"])
        packets = [p for p in shard_doc["games"] if str(p["game_key"]) in wanted]
        found = {str(p["game_key"]) for p in packets}
        if found != wanted:
            # A batch that cannot find its own games is a slate that moved
            # under it. Refused rather than evaluated short: a partial batch
            # reconciles perfectly and silently covers fewer games than the
            # manifest says it does.
            raise SystemExit(
                f"batch {batch!r} names {len(wanted)} game(s) and the shard on disk holds "
                f"{len(found)}; re-run prepare-live. Missing: {', '.join(sorted(wanted - found))}"
            )
        return batch, packets, shard_doc
    shard_doc = _read(_shard_path(out_dir, args.shard))
    return args.shard, list(shard_doc["games"]), shard_doc


def _shard_path(out_dir: Path, shard: str) -> Path:
    path = out_dir / "shards" / f"{shard}.json"
    if not path.exists():
        available = sorted(
            p.name[: -len(".json")]
            for p in (out_dir / "shards").glob("*.json")
            if not p.name.endswith(".analysis.json")
        )
        raise SystemExit(f"no shard {shard!r} at {path}. Available: {', '.join(available) or '(none)'}")
    return path


# ------------------------------------------------------------ prepare


def cmd_prepare_live(args: argparse.Namespace) -> int:
    catalog_dir = Path(args.catalog_dir)
    out_dir = Path(args.out_dir)
    as_of = _as_of(args.as_of)

    if args.refresh:
        # Shell out to the production builder rather than re-driving
        # MarketDiscovery here. That script owns the guards this command
        # must not lose: it refuses to publish an empty catalog over a
        # good one, it writes the status/fingerprint file, and it labels a
        # partial capture INCOMPLETE. A second in-process discovery path
        # would inherit none of that and would drift from it.
        import subprocess

        builder = Path(__file__).resolve().parents[3] / "scripts" / "build_kalshi_cfb_catalog.py"
        if not builder.exists():
            builder = Path.cwd() / "scripts" / "build_kalshi_cfb_catalog.py"
        if not builder.exists():
            print(
                f"FATAL: --refresh needs scripts/build_kalshi_cfb_catalog.py and it is not at "
                f"{builder}. Run the builder yourself and re-run without --refresh.",
                file=sys.stderr,
            )
            return 2
        print(f"refreshing the Kalshi catalog into {catalog_dir} via {builder.name} ...", flush=True)
        completed = subprocess.run(
            [
                sys.executable,
                str(builder),
                "--out-dir",
                str(catalog_dir),
                "--horizon-days",
                str(args.horizon_days),
            ],
            check=False,
        )
        if completed.returncode == 1:
            print(
                "FATAL: catalog refresh produced nothing usable; refusing to build an execution "
                "slate from whatever was on disk before it",
                file=sys.stderr,
            )
            return 2

    index = _read(catalog_dir / "cfb_market_catalog.json")
    captured_at = (index.get("capture") or {}).get("captured_at")
    captured = _as_of(captured_at) if captured_at else None

    config = DispositionConfig(
        as_of=as_of,
        captured_at=captured,
        max_capture_age_minutes=args.max_capture_age_minutes,
        max_quote_age_minutes=args.max_quote_age_minutes,
        require_pregame=not args.include_started,
        min_seconds_before_kickoff=args.min_seconds_before_kickoff,
    )

    slate_date = None if args.date == "all" else (args.date or _local_today(as_of, args.timezone))

    build = build_slate(
        catalog_dir,
        config,
        tz_name=args.timezone,
        slate_date=slate_date,
        context_dir=Path(args.context_dir) if args.context_dir else None,
    )
    slate = build.slate

    slate_bytes = _write(out_dir / "cfb_execution_slate.json", slate, compact=True)
    shards = build_shards(
        slate,
        max_bytes=args.max_analysis_bytes,
        max_contracts=args.max_shard_contracts,
    )
    manifest = write_shards(slate, shards, out_dir, args.timezone)

    # HANDICAP BATCHES: the primary unit of work.
    #
    # Written from the SLATE rather than from the shards, so a batch is a run
    # of games in kickoff order regardless of how the byte budget happened to
    # cut the analysis artifacts. Each batch records which shard holds its
    # contracts, which is how `evaluate --batch` finds them without a second
    # copy of the universe.
    batches = build_batches(
        slate,
        budget=args.batch_cost_budget,
        max_bytes=args.max_context_bytes,
        window_order=WINDOW_ORDER,
    )
    shard_for_game = {
        str(packet["game_key"]): shard.name for shard in shards for packet in shard.packets
    }
    batch_dir = out_dir / "batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    for stale in batch_dir.glob("*.context.json"):
        stale.unlink()
    batch_entries: list[dict[str, Any]] = []
    for batch in batches:
        shard_names = sorted({shard_for_game[key] for key in batch.game_keys})
        if len(shard_names) != 1:
            # Impossible today (shards and batches both split on kickoff order
            # and neither splits a game), and fail-closed rather than trusted:
            # a batch spanning two shards would have to be evaluated from two
            # files and could silently lose the games in the second.
            print(
                f"FATAL: batch {batch.name} spans shards {shard_names}; refusing to publish a "
                "batch manifest whose games cannot be evaluated from one shard file",
                file=sys.stderr,
            )
            return 2
        document = batch_document(slate, batch)
        path = batch_dir / f"{batch.name}.context.json"
        size = _write(path, document)
        batch_entries.append(
            {
                "batch": batch.name,
                "shard": shard_names[0],
                "kickoff_window": batch.window,
                "games": len(batch.packets),
                "game_keys": batch.game_keys,
                "reasoning_cost": batch.cost,
                "eligible_contracts": batch.eligible,
                "context_file": f"batches/{batch.name}.context.json",
                "context_bytes": size,
                "earliest_kickoff": batch.kickoffs[0] if batch.kickoffs else None,
                "latest_kickoff": batch.kickoffs[-1] if batch.kickoffs else None,
            }
        )
    batched_games = sum(e["games"] for e in batch_entries)
    batch_manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "slate_date": slate.get("slate_date"),
        "budget": args.batch_cost_budget,
        "totals": {
            "batches": len(batch_entries),
            "games": batched_games,
            "eligible_contracts": sum(e["eligible_contracts"] for e in batch_entries),
            "context_bytes": sum(e["context_bytes"] for e in batch_entries),
        },
        "reconciles": batched_games == len(slate["games"]),
        "batches": batch_entries,
    }
    _write(out_dir / "batch_manifest.json", batch_manifest)

    store = StateStore(out_dir / "state")
    state_index = store.write_index(slate["games"], out_dir / "state_index.json")

    reconciliation = slate["reconciliation"]
    print(
        f"execution slate {slate_date or 'all dates'}: "
        f"{reconciliation['games_published']} games / "
        f"{reconciliation['contracts_discovered']} discovered / "
        f"{reconciliation['contracts_eligible']} eligible / "
        f"{reconciliation['contracts_excluded']} excluded "
        f"({slate_bytes / 1e6:.2f} MB)"
    )
    print(f"  balanced={reconciliation['balanced']} unaccounted={reconciliation['unaccounted_contracts']}")
    for status, count in reconciliation["exclusions_by_status"].items():
        print(f"    {status:32} {count:6}")
    print(f"\n  shards ({manifest['totals']['shards']}), reconciles={manifest['reconciles']}")
    oldest_allowed = _oldest_allowed_quote(captured, config.max_capture_age_minutes)
    for entry in manifest["shards"]:
        print(f"\n  {entry['shard'].upper()}")
        print(f"    games:                    {entry['game_count']}")
        print(f"    eligible contracts:       {entry['contracts_eligible']}")
        print(f"    in analysis artifact:     {entry['contracts_in_analysis_artifact']}")
        print(f"    analysis artifact size:   {entry['analysis_bytes'] / 1e3:.0f} KB  "
              f"({entry['analysis_file']})")
        print(f"    freshest quote:           {_age(entry['freshest_quote_age_seconds'])}")
        print(f"    oldest allowed quote:     {oldest_allowed}")
        print(f"    mechanical exclusions:    {entry['contracts_excluded']}")
        print(f"    unaccounted:              {entry['unaccounted_contracts']}")
        print(f"    kickoffs:                 {str(entry['earliest_kickoff'])[:16]} -> "
              f"{str(entry['latest_kickoff'])[:16]}")
    coverage = slate.get("factual_context_coverage") or {}
    print(
        f"\n  factual context: {coverage.get('games', 0) - coverage.get('games_with_no_context', 0)}"
        f" / {coverage.get('games', 0)} games enriched"
    )
    for ceiling, count in (coverage.get("confidence_ceilings") or {}).items():
        print(f"    confidence ceiling {ceiling:14} {count:5}")
    print(
        f"\n  handicap batches ({batch_manifest['totals']['batches']}), "
        f"reconciles={batch_manifest['reconciles']}"
    )
    for entry in batch_entries:
        print(
            f"    {entry['batch']:16} games={entry['games']:2} cost={entry['reasoning_cost']:5.2f} "
            f"contracts_priced_later={entry['eligible_contracts']:5} "
            f"context={entry['context_bytes'] / 1e3:6.1f} KB"
        )
    print(
        f"  state: {state_index['counts']['complete']} complete / "
        f"{state_index['counts']['pending_handicap']} pending handicap / "
        f"{state_index['counts']['stale']} stale / "
        f"{state_index['counts'].get('handicap_needs_review', 0)} handicap needs review"
    )
    if not batch_manifest["reconciles"]:
        print(
            f"FATAL: handicap batches cover {batched_games} games but the slate has "
            f"{len(slate['games'])}; refusing to report this slate as usable",
            file=sys.stderr,
        )
        return 2
    if not reconciliation["balanced"] or not manifest["reconciles"]:
        print("FATAL: reconciliation failed -- refusing to report this slate as usable", file=sys.stderr)
        return 2
    if reconciliation["contracts_eligible"] == 0:
        # Published with its diagnostics -- every exclusion is in the file
        # -- but exit non-zero, because a slate nobody can bet is not a
        # success and a scripted caller must not treat it as one. The
        # commonest cause by far is a catalog older than the freshness
        # bar, which is exactly the fail-closed case working.
        print(
            "WARNING: zero eligible contracts. The slate and its exclusions were still written. "
            "Check --max-capture-age-minutes and whether the catalog is fresh (--refresh).",
            file=sys.stderr,
        )
        return 4
    first_batch = batch_entries[0] if batch_entries else None
    if first_batch:
        print(f"\nSTART HERE:              {out_dir}/{first_batch['context_file']}")
        print(f"WITH THIS PROMPT:        {HANDICAP_PROMPT}")
        print(
            f"THEN:                    python -m cfb_edge_finder.execution evaluate "
            f"--batch {first_batch['batch']} --handicaps <file>"
        )
        print(
            f"AND:                     python -m cfb_edge_finder.execution candidates "
            f"--batch {first_batch['batch']}"
        )
    first = manifest["shards"][0] if manifest["shards"] else None
    if first:
        print(
            f"\n(the full-market analysis artifact is still written, at "
            f"{out_dir}/{first['analysis_file']}, for the one-pass workflow)"
        )
    return 0


HANDICAP_PROMPT = (
    "Run CFB. Handicap every game in this file ONCE, from its factual context. Return one "
    "handicap payload per game: a score distribution per period, an uncertainty block on each "
    "one, explicit probabilities (with ranges) for anything a distribution cannot price, and "
    "your thesis and strongest opposing case. Do not price individual contracts."
)

CHATGPT_PROMPT = (
    "Run CFB. Bankroll $1,400. Independently handicap every game in this file, evaluate every "
    "available Kalshi market, and return every bet you believe has positive EV. Do not use repo "
    "projections."
)


def _age(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)):
        return "unknown"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min old"
    return f"{seconds / 3600:.1f} h old"


def _oldest_allowed_quote(captured: datetime | None, max_age_minutes: float) -> str:
    """The capture timestamp is the freshness bound for the whole file --
    a contract's own updated_time measures something else entirely."""
    if captured is None:
        return "unknown (no capture timestamp)"
    from datetime import timedelta

    return (captured - timedelta(minutes=max_age_minutes)).isoformat()


# ---------------------------------------------------------- templates


def cmd_handicap_template(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    shard = _read(_shard_path(out_dir, args.shard))
    packets = shard["games"]
    if args.game:
        packets = [p for p in packets if p["game_key"] == args.game]
        if not packets:
            raise SystemExit(f"game {args.game!r} is not in shard {args.shard!r}")

    document = {
        "schema_version": shard["schema_version"],
        "artifact": "handicap_template",
        "shard": args.shard,
        "handicaps": [template_for_packet(p) for p in packets],
    }
    target = Path(args.out) if args.out else out_dir / "templates" / f"{args.shard}.handicap_template.json"
    size = _write(target, document)
    print(f"wrote {target} ({len(packets)} games, {size} bytes)")
    return 0


# ---------------------------------------------------------- evaluate


def _game_ledger_path(out_dir: Path, game_key: str) -> Path:
    return out_dir / "ledgers" / "games" / f"{game_key}.json"


def _load_game_evaluation(path: Path) -> GameEvaluation:
    payload = _read(path)
    summary = payload["summary"]
    evaluation = GameEvaluation(
        game_key=summary["game_key"],
        packet_hash=summary.get("packet_hash"),
        market_universe_hash=summary.get("market_universe_hash"),
        eligible=int(summary["eligible_contracts"]),
        rows=list(payload["rows"]),
        status_counts=dict(summary.get("status_counts") or {}),
        evaluated_at=summary.get("evaluated_at") or "",
        missing_tickers=list(summary.get("missing_tickers") or []),
        duplicate_tickers=list(summary.get("duplicate_tickers") or []),
        unexpected_tickers=list(summary.get("unexpected_tickers") or []),
        context_hash=summary.get("context_hash"),
        handicap_schema_version=summary.get("handicap_schema_version"),
        uncertainty_stated=bool(summary.get("uncertainty_stated")),
    )
    return evaluation


def cmd_evaluate(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    label, packet_list, _shard_doc = _resolve_scope(out_dir, args)
    packets = {str(p["game_key"]): p for p in packet_list}
    store = StateStore(out_dir / "state")

    supplied: dict[str, Any] = {}
    if args.handicaps:
        try:
            for payload in load_handicaps(Path(args.handicaps)):
                supplied[payload.game_key] = payload
        except HandicapValidationError as exc:
            print(f"FATAL: handicap payload rejected: {exc}", file=sys.stderr)
            return 2

    unknown = sorted(set(supplied) - set(packets))
    if unknown:
        print(
            f"FATAL: handicaps supplied for games that are not in {label!r}: "
            f"{', '.join(unknown)}",
            file=sys.stderr,
        )
        return 2

    evaluations: list[GameEvaluation] = []
    pending: list[str] = []
    reused: list[str] = []

    for game_key, packet in packets.items():
        state = store.reconcile_with_packet(packet)

        handicap = supplied.get(game_key)
        if handicap is not None:
            expected = (packet.get("game_metadata") or {}).get("teams") or {}
            if not _teams_match(expected, handicap.teams):
                print(
                    f"FATAL: {game_key}: handicap teams {handicap.teams} do not match the packet's "
                    f"home={expected.get('home')!r} away={expected.get('away')!r}. Refusing to price "
                    f"a game whose sides may be flipped.",
                    file=sys.stderr,
                )
                return 2
            if handicap.packet_hash and handicap.packet_hash != packet.get("packet_hash"):
                if not args.allow_hash_mismatch:
                    print(
                        f"FATAL: {game_key}: handicap was produced against packet_hash "
                        f"{handicap.packet_hash[:12]} but the packet on disk is "
                        f"{str(packet.get('packet_hash'))[:12]}. Re-run prepare-live or pass "
                        f"--allow-hash-mismatch (the handicap is about the game, the prices are not).",
                        file=sys.stderr,
                    )
                    return 2
            state = store.record_handicap(packet, handicap)

        if not state.has_usable_handicap:
            pending.append(game_key)
            continue

        ledger_path = _game_ledger_path(out_dir, game_key)
        if (
            not args.force
            and state.is_complete
            and ledger_path.exists()
            and state.packet_hash == packet.get("packet_hash")
        ):
            cached = _load_game_evaluation(ledger_path)
            if cached.eligible == len(packet.get("contracts") or []):
                evaluations.append(cached)
                reused.append(game_key)
                continue

        payload = parse_handicap(state.handicap_payload)
        evaluation = evaluate_game(packet, payload, min_net_edge=args.min_edge)
        evaluations.append(evaluation)
        _write(
            ledger_path,
            compact=True,
            payload={
                "summary": evaluation.summary(),
                "eligible_ticker_hash": eligible_ticker_hash(packet),
                "rows": evaluation.rows,
            },
        )
        store.record_evaluation(
            packet,
            evaluation,
            selected=[
                {"ticker": r["ticker"], "net_edge": r.get("net_edge"), "side": r.get("best_side")}
                for r in evaluation.rows
                if r.get("status") in CANDIDATE_STATUSES
            ],
        )

    ledger = ledger_document(
        evaluations,
        shard=label,
        min_net_edge=args.min_edge,
        as_of=datetime.now(UTC).isoformat(),
    )
    ledger["games_pending_handicap"] = pending
    ledger["games_reused_from_state"] = reused
    ledger["shard_complete"] = not pending and all(e.complete for e in evaluations)
    size = _write(out_dir / "ledgers" / f"{label}.json", ledger, compact=True)

    store.write_index(list(packets.values()), out_dir / "state_index.json")

    for evaluation in evaluations:
        summary = evaluation.summary()
        print(
            f"{summary['game_key']:20} eligible={summary['eligible_contracts']:5} "
            f"evaluated={summary['evaluated_contracts']:5} "
            f"unpriceable={summary['unpriceable_contracts']:5} "
            f"unaccounted={summary['unaccounted_contracts']:3}  {summary['game_status']}"
        )
        if summary["missing_tickers"]:
            print(f"    missing: {', '.join(summary['missing_tickers'][:10])}")
    print(
        f"\nledger {out_dir}/ledgers/{label}.json ({size / 1e6:.2f} MB): "
        f"eligible={ledger['totals']['eligible_contracts']} "
        f"evaluated={ledger['totals']['evaluated_contracts']} "
        f"unpriceable={ledger['totals']['unpriceable_contracts']} "
        f"unaccounted={ledger['totals']['unaccounted_contracts']}"
    )
    if reused:
        print(f"  reused {len(reused)} already-complete game(s) from durable state")
    if pending:
        print(f"  PENDING HANDICAP ({len(pending)}): {', '.join(pending[:12])}")
        print("  the shard gate stays closed until every game has one")
    return 0 if ledger["shard_complete"] else 3


def _teams_match(expected: dict[str, Any], supplied: dict[str, str]) -> bool:
    def norm(value: Any) -> str:
        return "".join(ch for ch in str(value or "").lower() if ch.isalnum())

    return norm(expected.get("home")) == norm(supplied.get("home")) and norm(
        expected.get("away")
    ) == norm(supplied.get("away"))


# ------------------------------------------------------------- status


def cmd_status(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    store = StateStore(out_dir / "state")
    if args.shard:
        shard = _read(_shard_path(out_dir, args.shard))
        packets = shard["games"]
    else:
        packets = _read(out_dir / "cfb_execution_slate.json")["games"]

    rows = []
    for packet in packets:
        state = store.reconcile_with_packet(packet)
        rows.append((packet, state))

    complete = [r for r in rows if r[1].is_complete]
    print(f"{len(complete)} / {len(rows)} games COMPLETE")
    for packet, state in rows:
        print(
            f"  {packet['game_key']:20} {state.state:28} "
            f"eligible={state.eligible_contracts} evaluated={state.evaluated_contracts} "
            f"unpriceable={state.unpriceable_contracts} unaccounted={state.unaccounted_contracts}"
            + (f"  [{state.invalidation_reason}]" if state.invalidation_reason else "")
        )
    resume = next_incomplete([p for p, _ in rows], store)
    if resume is not None:
        print(f"\nresume at: {resume['game_key']} ({resume['title']})")
    else:
        print("\nevery game in scope is COMPLETE")
    return 0


# ------------------------------------------------------------- report


def cmd_report(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    shard_doc = _read(_shard_path(out_dir, args.shard))
    packets = {str(p["game_key"]): p for p in shard_doc["games"]}
    store = StateStore(out_dir / "state")

    evaluations: list[GameEvaluation] = []
    handicaps = {}
    missing_games = []
    for game_key, packet in packets.items():
        ledger_path = _game_ledger_path(out_dir, game_key)
        state = store.reconcile_with_packet(packet)
        if not ledger_path.exists() or not state.is_complete:
            missing_games.append(game_key)
            continue
        evaluations.append(_load_game_evaluation(ledger_path))
        if state.handicap_payload:
            handicaps[game_key] = parse_handicap(state.handicap_payload)

    if missing_games:
        print(
            f"SHARD GATE CLOSED: {len(missing_games)} of {len(packets)} games in shard "
            f"{args.shard!r} are not COMPLETE. No shortlist is produced.\n"
            f"  {', '.join(missing_games[:20])}",
            file=sys.stderr,
        )
        return 3

    try:
        report = build_report(
            args.shard,
            evaluations,
            packets,
            handicaps,
            min_net_edge=args.min_edge,
            games_discovered=len(packets),
            contracts_discovered=int(shard_doc["counts"]["contracts_discovered"]),
            mechanical_exclusions=int(shard_doc["counts"]["contracts_excluded"]),
            top_n=args.top,
        )
    except ShardGateError as exc:
        print(f"SHARD GATE CLOSED: {exc}", file=sys.stderr)
        return 3

    _write(out_dir / "reports" / f"{args.shard}.json", report)
    text = render_report(report)
    (out_dir / "reports" / f"{args.shard}.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


# ------------------------------------------------------- candidates


def cmd_candidates(args: argparse.Namespace) -> int:
    """The small artifact a final review reads, for ONE batch or one shard.

    Runs immediately after a batch's evaluation, which is what makes the
    workflow streamable: batch one's candidates are actionable while batch two
    is still being handicapped.
    """
    out_dir = Path(args.out_dir)
    label, packet_list, _shard_doc = _resolve_scope(out_dir, args)
    packets = {str(p["game_key"]): p for p in packet_list}
    store = StateStore(out_dir / "state")

    evaluations: list[GameEvaluation] = []
    handicaps = {}
    not_ready: list[tuple[str, str]] = []
    for game_key, packet in packets.items():
        ledger_path = _game_ledger_path(out_dir, game_key)
        state = store.reconcile_with_packet(packet)
        if not ledger_path.exists():
            not_ready.append((game_key, "no evaluation on disk"))
            continue
        if not state.is_complete:
            not_ready.append((game_key, state.state))
            continue
        evaluations.append(_load_game_evaluation(ledger_path))
        if state.handicap_payload:
            handicaps[game_key] = parse_handicap(state.handicap_payload)

    if not_ready:
        print(
            f"GATE CLOSED: {len(not_ready)} of {len(packets)} games in {label!r} are not "
            f"COMPLETE. No candidate artifact is produced.",
            file=sys.stderr,
        )
        for game_key, why in not_ready[:20]:
            print(f"  {game_key:20} {why}", file=sys.stderr)
        return 3

    try:
        artifact = build_candidate_artifact(
            _shard_doc.get("shard"),
            evaluations,
            packets,
            handicaps,
            min_net_edge=args.min_edge,
            batch=label,
            top_n=args.top,
        )
    except ShardGateError as exc:
        print(f"GATE CLOSED: {exc}", file=sys.stderr)
        return 3

    path = out_dir / "candidates" / f"{label}.candidates.json"
    size = _write(path, artifact)
    # THE AUDIT IS WRITTEN IN THE SAME BREATH AS THE SHORTLIST, ALWAYS.
    # A reduction the operator can act on and cannot inspect is the thing this
    # whole file exists to avoid, so the ledger is not an option and not a
    # flag: it lands beside the artifact on every run, and the artifact names
    # it.
    rows_again = [
        row
        for evaluation in evaluations
        for row in evaluation.rows
        if row.get("status") in CANDIDATE_STATUSES
    ]
    ledger_size = _write(
        out_dir / "candidates" / artifact["reduction_ledger_file"],
        build_reduction_ledger(
            _shard_doc.get("shard"), label, reduce_candidates(rows_again)
        ),
        compact=True,
    )
    reconciliation = artifact["reconciliation"]
    reduction = artifact["reduction"]
    print(f"{label}: {reconciliation['games']} games, "
          f"{reconciliation['eligible_contracts']} eligible contracts")
    print(f"  evaluated              {reconciliation['evaluated_contracts']}")
    print(f"  explicitly unpriceable {reconciliation['explicitly_unpriceable_contracts']}")
    print(f"  unaccounted            {reconciliation['unaccounted_contracts']}")
    for status, count in reconciliation["status_counts"].items():
        print(f"    {status:34} {count:6}")
    print(f"\n  candidates before reduction {reduction['candidate_rows_before_reduction']}")
    print(f"  surviving candidates        {reduction['surviving_candidates']}")
    for reason, count in reduction["removed_by_reason"].items():
        print(f"    removed {reason:32} {count:5}")
    extreme = [
        row for row in artifact["market_disagreement_by_game"] if row["level"] == "extreme"
    ]
    if extreme:
        print(f"\n  MARKET DISAGREEMENT EXTREME on {len(extreme)} game(s):")
        for row in extreme:
            print(f"    {row['game_key']:20} {row['reason']}")
    print(f"\n  {path} ({size / 1e3:.1f} KB)")
    print(
        f"  {out_dir}/candidates/{artifact['reduction_ledger_file']} "
        f"({ledger_size / 1e3:.1f} KB, every removal and its reason)"
    )
    return 0


# ---------------------------------------------------------- batches


def cmd_batches(args: argparse.Namespace) -> int:
    """What is left to handicap, in the order it kicks off."""
    out_dir = Path(args.out_dir)
    manifest = _batch_manifest(out_dir)
    entries = manifest.get("batches") or []
    if not entries:
        print("no batch manifest; run prepare-live first", file=sys.stderr)
        return 2
    store = StateStore(out_dir / "state")
    slate_games = {
        str(p["game_key"]): p for p in _read(out_dir / "cfb_execution_slate.json")["games"]
    }

    print(
        f"{manifest['totals']['batches']} batches / {manifest['totals']['games']} games / "
        f"{manifest['totals']['eligible_contracts']} eligible contracts "
        f"(reconciles={manifest['reconciles']})"
    )
    for entry in entries:
        states = [
            store.reconcile_with_packet(slate_games[key])
            for key in entry["game_keys"]
            if key in slate_games
        ]
        done = sum(1 for state in states if state.is_complete)
        review = sum(1 for state in states if state.handicap_status == "complete_needs_review")
        print(
            f"  {entry['batch']:16} games={entry['games']:2} cost={entry['reasoning_cost']:5.2f} "
            f"complete={done}/{entry['games']}"
            + (f"  needs_review={review}" if review else "")
            + f"  {entry['context_file']}"
        )
    return 0


# ---------------------------------------------------------------- next


def cmd_next(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    shard = _read(_shard_path(out_dir, args.shard))
    store = StateStore(out_dir / "state")
    packet = next_incomplete(shard["games"], store)
    if packet is None:
        print(f"shard {args.shard}: every game is COMPLETE")
        return 0
    print(json.dumps(template_for_packet(packet), indent=1, sort_keys=True))
    return 0


# ----------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cfb_edge_finder.execution", description=__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))

    prepare = sub.add_parser("prepare-live", help="discover, compact, shard and reconcile the slate")
    common(prepare)
    prepare.add_argument("--catalog-dir", default=str(DEFAULT_CATALOG_DIR))
    prepare.add_argument(
        "--refresh",
        action="store_true",
        help="re-run Kalshi discovery into --catalog-dir first (needs network egress to Kalshi)",
    )
    prepare.add_argument("--horizon-days", type=float, default=10.0)
    prepare.add_argument("--as-of", default=None, help="ISO timestamp; defaults to now")
    prepare.add_argument(
        "--date",
        default=None,
        help="local slate date (YYYY-MM-DD), or 'all' for every game in the catalog horizon",
    )
    prepare.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    prepare.add_argument(
        "--max-capture-age-minutes",
        type=float,
        default=DEFAULT_MAX_CAPTURE_AGE_MINUTES,
        help="older than this and EVERY contract is stale_quote (fail closed)",
    )
    prepare.add_argument("--max-quote-age-minutes", type=float, default=None)
    prepare.add_argument("--min-seconds-before-kickoff", type=float, default=0.0)
    prepare.add_argument(
        "--include-started",
        action="store_true",
        help="do not exclude games that have kicked off (diagnostics only)",
    )
    prepare.add_argument(
        "--max-analysis-bytes",
        type=int,
        default=DEFAULT_MAX_ANALYSIS_BYTES,
        help=(
            "byte budget for one <shard>.analysis.json. A shard over budget is split into "
            "subshards BETWEEN games -- a game's contracts are never split, and no contract is "
            "ever dropped to meet it."
        ),
    )
    prepare.add_argument("--max-shard-contracts", type=int, default=DEFAULT_MAX_SHARD_CONTRACTS)
    prepare.add_argument(
        "--context-dir",
        default=str(DEFAULT_CONTEXT_DIR),
        help=(
            "collected factual context, one JSON per game. An absent directory is an EMPTY store, "
            "not an error: the slate still builds, every domain is marked missing, and every "
            "confidence ceiling falls to `insufficient`."
        ),
    )
    prepare.add_argument(
        "--batch-cost-budget",
        type=float,
        default=DEFAULT_BATCH_COST_BUDGET,
        help=(
            "reasoning cost per handicap batch, in average games. A batch is packed until adding "
            "the next game would exceed it, and a game is never split."
        ),
    )
    prepare.add_argument(
        "--max-context-bytes",
        type=int,
        default=DEFAULT_MAX_CONTEXT_BYTES,
        help="encoding safeguard for one batch context artifact",
    )
    prepare.set_defaults(func=cmd_prepare_live)

    template = sub.add_parser("handicap-template", help="emit blank handicap payloads for a shard")
    common(template)
    template.add_argument("--shard", required=True)
    template.add_argument("--game", default=None)
    template.add_argument("--out", default=None)
    template.set_defaults(func=cmd_handicap_template)

    evaluate = sub.add_parser(
        "evaluate",
        help=(
            "price EVERY eligible contract in a batch (or a whole shard) against the handicap "
            "payloads supplied for it, with sensitivity across each handicap's stated uncertainty "
            "region. Games already complete against the packet on disk are reused."
        ),
    )
    common(evaluate)
    scope = evaluate.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--batch",
        default=None,
        help="one handicap batch (5-8 games). The normal unit of work.",
    )
    scope.add_argument("--shard", default=None, help="a whole kickoff window")
    evaluate.add_argument("--handicaps", default=None, help="file of filled handicap payloads")
    evaluate.add_argument(
        "--min-edge",
        type=float,
        default=DEFAULT_MIN_NET_EDGE,
        help=(
            "operator's required fee-adjusted edge. NOT a validated threshold -- this repository "
            "has never established one."
        ),
    )
    evaluate.add_argument("--force", action="store_true", help="re-price games already complete")
    evaluate.add_argument("--allow-hash-mismatch", action="store_true")
    evaluate.set_defaults(func=cmd_evaluate)

    status = sub.add_parser("status", help="per-game completion state")
    common(status)
    status.add_argument("--shard", default=None)
    status.set_defaults(func=cmd_status)

    report = sub.add_parser(
        "report",
        help=(
            "OPTIONAL repo-side verification. The live workflow is prepare-live -> upload the "
            "analysis artifact -> ChatGPT answers; this path exists to re-check a handicap "
            "arithmetically and is not required to get bets."
        ),
    )
    common(report)
    report.add_argument("--shard", required=True)
    report.add_argument(
        "--top",
        type=int,
        default=None,
        help=(
            "DEBUG DISPLAY ONLY. Truncates what this command prints; it has no place in the live "
            "workflow, changes nothing in the ledger, and is off by default. The live path returns "
            "EVERY qualifying bet -- 0, 6 or 100 -- and there is no target number."
        ),
    )
    report.add_argument("--min-edge", type=float, default=DEFAULT_MIN_NET_EDGE)
    report.set_defaults(func=cmd_report)

    candidates = sub.add_parser(
        "candidates",
        help=(
            "the small final-review artifact for one batch: survivors of the dominance and "
            "correlation reduction, each with its sensitivity, robustness, bet-up-to price, "
            "correlation group and the alternatives it beat."
        ),
    )
    common(candidates)
    cscope = candidates.add_mutually_exclusive_group(required=True)
    cscope.add_argument("--batch", default=None)
    cscope.add_argument("--shard", default=None)
    candidates.add_argument("--min-edge", type=float, default=DEFAULT_MIN_NET_EDGE)
    candidates.add_argument(
        "--top",
        type=int,
        default=None,
        help=(
            "DISPLAY TRUNCATION ONLY, and it is recorded in the artifact when used. The reduction "
            "itself has no cap: every removed contract lost to a named survivor for a "
            "deterministic reason and is in the reduction ledger."
        ),
    )
    candidates.set_defaults(func=cmd_candidates)

    batches = sub.add_parser("batches", help="list handicap batches and what is still open")
    common(batches)
    batches.set_defaults(func=cmd_batches)

    nxt = sub.add_parser("next", help="print the handicap template for the next incomplete game")
    common(nxt)
    nxt.add_argument("--shard", required=True)
    nxt.set_defaults(func=cmd_next)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
