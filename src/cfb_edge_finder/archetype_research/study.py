"""Season-level orchestration: replay every target, freeze, reveal, score. RESEARCH ONLY."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from cfb_edge_finder.archetype_research.replay import Target, pregame, reveal, schedule_targets, target_rows
from cfb_edge_finder.archetype_research.taxonomy import score_game
from cfb_edge_finder.scripting.football import LeagueFitCache
from cfb_edge_finder.scripting.gamelog import FINAL_AFTER, TeamGame, parse_utc, rows_fingerprint


def run_targets(rows: list[TeamGame], targets: Iterable[Target], *, reveal_outcomes: bool = True) -> list[dict]:
    """Frozen pregame records, each followed (optionally) by its revealed outcome."""
    data = tuple(rows)
    cache = LeagueFitCache()
    out = []
    for target in targets:
        frozen = pregame(data, target, cache)
        record: dict[str, Any] = {**frozen, "outcome": None}
        if reveal_outcomes and frozen["pregame"]["eligibility"] is None:
            pre, home, away = reveal(frozen, *target_rows(data, target))
            record["outcome"] = score_game(pre, home, away)
        out.append(record)
    return out


def coverage(rows: list[TeamGame], games: list[dict], records: list[dict], schedule_exclusions: list[dict]) -> dict:
    """The per-season data-coverage table (protocol section 2/3)."""
    by_game: dict[str, list[TeamGame]] = {}
    for r in rows:
        by_game.setdefault(r.game_id, []).append(r)
    scheduled = len(games)
    completed = sum(1 for g in games if g.get("completed"))

    def both(gid: str, pred) -> bool:
        rs = by_game.get(gid) or []
        return len(rs) == 2 and all(pred(r) for r in rs)

    ids = [str(g["id"]) for g in games if g.get("completed")]
    box = sum(both(g, lambda r: r.box.get("plays") is not None and r.box.get("total_yards") is not None) for g in ids)
    pbp = sum(both(g, lambda r: r.pbp is not None and r.pbp.get("success_plays") is not None) for g in ids)
    drives = sum(both(g, lambda r: r.pbp is not None and r.pbp.get("drives") is not None) for g in ids)
    explosive = sum(both(g, lambda r: r.pbp is not None and r.pbp.get("explosive_rush") is not None) for g in ids)
    eligible = [r for r in records if r["pregame"]["eligibility"] is None]
    reasons = Counter(e["reason"] for e in schedule_exclusions)
    reasons.update(r["pregame"]["eligibility"] for r in records if r["pregame"]["eligibility"])
    return {
        "scheduled_fbs_involved": scheduled,
        "completed": completed,
        "with_box_score_both_teams": box,
        "with_success_rate_play_data_both_teams": pbp,
        "with_drive_data_both_teams": drives,
        "with_explosive_play_counts_both_teams": explosive,
        "identity_resolved_targets": len(records),
        "eligible_with_prior_history": len(eligible),
        "excluded": sum(reasons.values()),
        "exclusion_reasons": dict(reasons.most_common()),
        "season_type": dict(Counter(r["pregame"]["season_type"] for r in eligible)),
        "fcs_involved_eligible": sum(1 for r in eligible if r["pregame"]["fcs_involved"]),
        "confidence_eligible": dict(Counter(r["pregame"]["confidence"] for r in eligible)),
        "leakage_checks_passed": sum(
            1
            for r in records
            if r["pregame"]["history_latest_kickoff"] is None
            or parse_utc(r["pregame"]["history_latest_kickoff"]) + FINAL_AFTER
            < parse_utc(r["pregame"]["football_data_cutoff"])
        ),
        "rows": len(rows),
        "rows_fingerprint": rows_fingerprint(rows),
    }


def run_season(season_data: dict[str, Any]) -> dict[str, Any]:
    rows, games = season_data["rows"], season_data["games"]
    targets, excluded = schedule_targets(games, rows)
    records = run_targets(rows, targets)
    return {
        "records": records,
        "schedule_exclusions": excluded,
        "coverage": coverage(rows, games, records, excluded),
        "digests": season_data["digests"],
    }
