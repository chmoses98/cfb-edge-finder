"""The live workflow, as commands.

    python -m cfb_edge_finder.execution prepare-live
    python -m cfb_edge_finder.execution handicap-template --shard early
    python -m cfb_edge_finder.execution evaluate --shard early --handicaps <file>
    python -m cfb_edge_finder.execution status --shard early
    python -m cfb_edge_finder.execution report --shard early

Every command is idempotent and every intermediate result is a file on
disk, because the failure this workflow must survive is "the handicapping
session died halfway through a 100-game slate".
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cfb_edge_finder.execution.disposition import (
    DEFAULT_MAX_CAPTURE_AGE_MINUTES,
    DispositionConfig,
)
from cfb_edge_finder.execution.evaluator import (
    DEFAULT_MIN_NET_EDGE,
    EvaluationStatus,
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
from cfb_edge_finder.execution.report import ShardGateError, build_report, render_report
from cfb_edge_finder.execution.shards import (
    DEFAULT_MAX_SHARD_BYTES,
    DEFAULT_MAX_SHARD_CONTRACTS,
    build_shards,
    write_shards,
)
from cfb_edge_finder.execution.slate import build_slate
from cfb_edge_finder.execution.state import StateStore, eligible_ticker_hash, next_incomplete
from cfb_edge_finder.execution.windows import DEFAULT_TIMEZONE

DEFAULT_CATALOG_DIR = Path("data/live")
DEFAULT_OUT_DIR = Path("data/execution/latest")


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


def _shard_path(out_dir: Path, shard: str) -> Path:
    path = out_dir / "shards" / f"{shard}.json"
    if not path.exists():
        available = sorted(p.stem for p in (out_dir / "shards").glob("*.json") if not p.name.endswith(".brief.json"))
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
    )
    slate = build.slate

    slate_bytes = _write(out_dir / "cfb_execution_slate.json", slate, compact=True)
    shards = build_shards(
        slate,
        max_bytes=args.max_shard_bytes,
        max_contracts=args.max_shard_contracts,
    )
    manifest = write_shards(slate, shards, out_dir)

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
    print(f"  shards ({manifest['totals']['shards']}), reconciles={manifest['reconciles']}:")
    for entry in manifest["shards"]:
        print(
            f"    {entry['shard']:14} games={entry['game_count']:4} "
            f"eligible={entry['contracts_eligible']:6} "
            f"{entry['bytes'] / 1e6:.2f} MB (brief {entry['brief_bytes'] / 1e6:.2f} MB) "
            f"{str(entry['earliest_kickoff'])[:16]} -> {str(entry['latest_kickoff'])[:16]}"
        )
    print(
        f"  state: {state_index['counts']['complete']} complete / "
        f"{state_index['counts']['pending_handicap']} pending handicap / "
        f"{state_index['counts']['stale']} stale"
    )
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
    first = manifest["shards"][0]["shard"] if manifest["shards"] else None
    if first:
        print(f"\nnext: send {out_dir}/shards/{first}.brief.json to the handicapper")
    return 0


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
    )
    return evaluation


def cmd_evaluate(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    shard = _read(_shard_path(out_dir, args.shard))
    packets = {str(p["game_key"]): p for p in shard["games"]}
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
            f"FATAL: handicaps supplied for games that are not in shard {args.shard!r}: "
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
                if r.get("status") == EvaluationStatus.POSITIVE_EV.value
            ],
        )

    ledger = ledger_document(
        evaluations,
        shard=args.shard,
        min_net_edge=args.min_edge,
        as_of=datetime.now(UTC).isoformat(),
    )
    ledger["games_pending_handicap"] = pending
    ledger["games_reused_from_state"] = reused
    ledger["shard_complete"] = not pending and all(e.complete for e in evaluations)
    size = _write(out_dir / "ledgers" / f"{args.shard}.json", ledger, compact=True)

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
        f"\nledger {out_dir}/ledgers/{args.shard}.json ({size / 1e6:.2f} MB): "
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
    prepare.add_argument("--max-shard-bytes", type=int, default=DEFAULT_MAX_SHARD_BYTES)
    prepare.add_argument("--max-shard-contracts", type=int, default=DEFAULT_MAX_SHARD_CONTRACTS)
    prepare.set_defaults(func=cmd_prepare_live)

    template = sub.add_parser("handicap-template", help="emit blank handicap payloads for a shard")
    common(template)
    template.add_argument("--shard", required=True)
    template.add_argument("--game", default=None)
    template.add_argument("--out", default=None)
    template.set_defaults(func=cmd_handicap_template)

    evaluate = sub.add_parser("evaluate", help="price EVERY eligible contract against the handicaps")
    common(evaluate)
    evaluate.add_argument("--shard", required=True)
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

    report = sub.add_parser("report", help="final shortlist; refuses unless the shard gate passes")
    common(report)
    report.add_argument("--shard", required=True)
    report.add_argument("--top", type=int, default=None)
    report.add_argument("--min-edge", type=float, default=DEFAULT_MIN_NET_EDGE)
    report.set_defaults(func=cmd_report)

    nxt = sub.add_parser("next", help="print the handicap template for the next incomplete game")
    common(nxt)
    nxt.add_argument("--shard", required=True)
    nxt.set_defaults(func=cmd_next)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
