"""Historical archetype validation: leakage, reuse of production, determinism, research-only boundaries."""

from __future__ import annotations

import ast
import copy
import dataclasses
from datetime import timedelta
from pathlib import Path

import pytest
from script_engine_fakes import synthetic_season

from cfb_edge_finder.archetype_research import analysis as A
from cfb_edge_finder.archetype_research.replay import (
    CFBD_FILES,
    GAME_META_FIELDS,
    FrozenRecordMismatch,
    LeakageError,
    Target,
    _identity,
    assert_research_output_path,
    build_pregame_content,
    history_for,
    pregame,
    reveal,
    schedule_targets,
    sha256,
    target_rows,
)
from cfb_edge_finder.archetype_research.study import run_targets
from cfb_edge_finder.archetype_research.taxonomy import label_match, realize, score_game
from cfb_edge_finder.scripting import findings as production_findings
from cfb_edge_finder.scripting import realized as production_realized
from cfb_edge_finder.scripting import scripts as production_scripts
from cfb_edge_finder.scripting.football import FootballPacket, LeagueFitCache, build_content
from cfb_edge_finder.scripting.gamelog import FINAL_AFTER, parse_utc

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "cfb_edge_finder" / "archetype_research"


def _games(rows):
    out = {}
    for r in rows:
        g = out.setdefault(
            r.game_id,
            {
                "id": r.game_id,
                "season": r.season,
                "week": r.week,
                "seasonType": "regular",
                "startDate": r.kickoff_utc,
                "completed": True,
                "neutralSite": False,
                "conferenceGame": None,
            },
        )
        g["homeId" if r.site == "home" else "awayId"] = r.team_id
    return list(out.values())


def _shift(rows, game_id, delta):
    """Move one game's kickoff (both rows) by `delta`."""
    out = []
    for r in rows:
        if r.game_id == game_id:
            moved = parse_utc(r.kickoff_utc) + delta
            r = dataclasses.replace(r, kickoff_utc=moved.strftime("%Y-%m-%dT%H:%M:%SZ"))
        out.append(r)
    return out


@pytest.fixture(scope="module")
def season():
    rows = synthetic_season()
    # Stagger one week-5 game to the evening of the same slate day: a "later game the same day".
    week5 = sorted({r.game_id for r in rows if r.week == 5})
    rows = _shift(rows, week5[-1], timedelta(hours=5))
    targets, excluded = schedule_targets(_games(rows), rows)
    assert not excluded
    return rows, targets


def _target(targets, week, index=0):
    return [t for t in targets if t.week == week][index]


def test_target_game_and_every_later_game_cannot_reach_the_pregame_record(season):
    rows, targets = season
    target = _target(targets, 5)
    frozen = pregame(rows, target)
    cutoff = parse_utc(frozen["pregame"]["football_data_cutoff"])
    doctored = [
        dataclasses.replace(
            r,
            points_for=(r.points_for or 0) + 50,
            points_against=0.0,
            box={k: (v * 3 if isinstance(v, float) else v) for k, v in r.box.items()},
        )
        if r.kickoff() + FINAL_AFTER >= cutoff
        else r
        for r in rows
    ]
    assert pregame(doctored, target)["pregame_hash"] == frozen["pregame_hash"]
    # non-vacuous: the same doctoring DOES change a game played after those results were in
    later = _target(targets, 8)
    assert pregame(doctored, later)["pregame_hash"] != pregame(rows, later)["pregame_hash"]


def test_a_later_game_on_the_same_slate_day_cannot_reach_an_earlier_game(season):
    rows, targets = season
    week5 = sorted({r.game_id for r in rows if r.week == 5})
    late = week5[-1]
    target = next(t for t in targets if t.week == 5 and t.game_id != late)
    frozen = pregame(rows, target)
    doctored = [dataclasses.replace(r, points_for=99.0) if r.game_id == late else r for r in rows]
    assert pregame(doctored, target)["pregame_hash"] == frozen["pregame_hash"]
    # ... and an EARLIER game the same day is not history either: the slate shares one 04:00 ET cutoff
    _, history = history_for(rows, target)
    assert all(r.week < 5 for r in history)


def test_appending_future_games_never_changes_an_earlier_pregame_artifact(season):
    rows, targets = season
    early = [_target(targets, w) for w in (2, 4, 6)]
    first = {t.game_id: pregame(rows, t)["pregame_hash"] for t in early}
    future = [
        dataclasses.replace(r, game_id=f"FUT{r.game_id}", kickoff_utc=r.kickoff_utc.replace("2026-", "2027-"))
        for r in rows
    ]
    again = {t.game_id: pregame(rows + future, t)["pregame_hash"] for t in early}
    assert first == again


def test_history_is_strictly_finished_before_the_production_cutoff(season):
    rows, targets = season
    for target in targets[::7]:
        cutoff, history = history_for(rows, target)
        assert all(r.kickoff() + FINAL_AFTER < parse_utc(cutoff) for r in history)
        assert target.game_id not in {r.game_id for r in history}


def test_a_target_inside_its_own_history_is_refused(season):
    rows, targets = season
    early = _target(targets, 1)
    impostor = dataclasses.replace(early, kickoff_utc="2026-12-31T19:00:00Z")
    with pytest.raises(LeakageError):
        history_for(rows, impostor)


def test_season_final_statistics_are_never_substituted_for_cutoff_statistics(season):
    """The record from the full season equals the record from rows truncated at the cutoff."""
    rows, targets = season
    target = _target(targets, 3)
    cutoff = parse_utc(pregame(rows, target)["pregame"]["football_data_cutoff"])
    truncated = [r for r in rows if r.kickoff() + FINAL_AFTER < cutoff]
    assert pregame(truncated, target)["pregame_hash"] == pregame(rows, target)["pregame_hash"]


def test_the_production_builder_is_reused_exactly(season):
    """Replaying from history equals production `build_content` on the whole log (byte for byte)."""
    rows, targets = season
    for target in (_target(targets, 4), _target(targets, 7, 1)):
        cutoff, history = history_for(rows, target)
        replay = build_pregame_content(history, target, LeagueFitCache())
        packet = FootballPacket(
            identity=_identity(target),
            game_key=None,
            rows=tuple(rows),
            availability={
                "home": {"status": "OBSERVED", "qb_uncertain": False},
                "away": {"status": "OBSERVED", "qb_uncertain": False},
            },
            identity_check={"status": "PASS", "orientation_swapped": False},
            freshness={"status": "FRESH"},
        )
        production = build_content(packet, LeagueFitCache())
        assert sha256(replay) == sha256(production)
        assert pregame(rows, target)["pregame"]["content_hash"] == sha256(production)


def test_replay_is_deterministic(season):
    rows, targets = season
    sample = targets[-30:]
    a = run_targets(rows, sample)
    b = run_targets(rows, sample)
    assert [r["pregame_hash"] for r in a] == [r["pregame_hash"] for r in b]
    assert sha256([r["outcome"] for r in a]) == sha256([r["outcome"] for r in b])


def test_a_frozen_record_cannot_be_altered_before_reveal(season):
    rows, targets = season
    target = _target(targets, 6)
    frozen = pregame(rows, target)
    tampered = copy.deepcopy(frozen)
    tampered["pregame"]["status"] = "SINGLE_SCRIPT"
    with pytest.raises(FrozenRecordMismatch):
        reveal(tampered, *target_rows(rows, target))


def test_realized_classification_reuses_production_and_is_reproducible(season):
    rows, targets = season
    target = _target(targets, 7)
    frozen = pregame(rows, target)
    pre, home, away = reveal(frozen, *target_rows(rows, target))
    first, second = score_game(pre, home, away), score_game(pre, home, away)
    assert first == second
    features = production_realized.realized_features(home, away)
    prod = production_realized.classify(features, pre["baseline"], pre["reference_lead"])
    assert first["realized"]["primary"] == prod["primary"]
    assert [x for x in prod["labels"] if x != "AMBIGUOUS"] == first["realized"]["labels"]


def _pair(rows, home_pts, away_pts, home_sr, away_sr):
    gid = rows[0].game_id
    home = next(r for r in rows if r.game_id == gid and r.site == "home")
    away = next(r for r in rows if r.game_id == gid and r.site == "away")

    def fix(r, pts, opp, sr):
        pbp = dict(r.pbp)
        pbp["success_plays"] = round(sr * pbp["scrim_plays"])
        return dataclasses.replace(r, points_for=pts, points_against=opp, pbp=pbp)

    return fix(home, home_pts, away_pts, home_sr), fix(away, away_pts, home_pts, away_sr)


def _pre(lead="home"):
    return {
        "reference_lead": lead,
        "baseline_lead": lead,
        "baseline": {"home_points": 28.0, "away_points": 24.0, "total_points": 52.0, "home_margin": 4.0},
        "league_norms": {"plays_per_game": 70.0, "success_rate": 0.42, "yards_per_play": 5.6, "points_per_game": 27},
        "scripts": [],
    }


def test_multi_label_and_ambiguous_games_are_handled(season):
    rows, _ = season
    # one-score game with a reference lead: a competitive label AND hangs-around (multi-label)
    home, away = _pair(rows, 24.0, 27.0, 0.40, 0.41)
    rz = realize(_pre("home"), home, away)
    assert "UNDERDOG_HANGS_AROUND" in rz["labels"] and len(rz["labels"]) >= 2
    assert rz["sides"]["UNDERDOG_HANGS_AROUND"] == "away"
    assert label_match("UNDERDOG_HANGS_AROUND", "away", rz) and not label_match("UNDERDOG_HANGS_AROUND", "home", rz)
    # a 30-point win by the side that LOST the efficiency battle fits nothing: AMBIGUOUS
    home, away = _pair(rows, 44.0, 14.0, 0.35, 0.50)
    rz = realize(_pre("home"), home, away)
    assert rz["ambiguous"] and rz["primary"] == "AMBIGUOUS" and rz["labels"] == []


def _records(season):
    rows, targets = season
    return run_targets(rows, targets)


@pytest.fixture(scope="module")
def records(season):
    return _records(season)


def test_no_script_games_are_an_abstention_not_a_miss(records):
    elig = A.eligible(records)
    abstain = [r for r in elig if r["pregame"]["status"] == "NO_SCRIPT_CLEARED_EVIDENCE"]
    scripted = [r for r in elig if r["pregame"]["scripts"]]
    assert all(r["outcome"]["scripts"] == [] for r in abstain)
    ns = A.no_script(records)
    assert ns["rate"]["hits"] == len(abstain) and ns["rate"]["n"] == len(elig)
    assert ns["scripted"]["n"] == len(scripted)
    ident = A.identification(records)
    generated = sum(ident[a]["generated"] for a in production_scripts.ARCHETYPE_ORDER)
    assert generated == sum(len(r["pregame"]["scripts"]) for r in elig)


def _production_rules():
    return copy.deepcopy(
        {
            "findings": {k: getattr(production_findings, k) for k in dir(production_findings) if k.isupper()},
            "scripts": {
                k: getattr(production_scripts, k)
                for k in dir(production_scripts)
                if k.isupper() and not callable(getattr(production_scripts, k))
            },
            "realized": {"ONE_SCORE": production_realized.ONE_SCORE},
        }
    )


def test_analysis_and_ablation_cannot_mutate_production_rules_or_frozen_records(records):
    before = _production_rules()
    hashes = [r["pregame_hash"] for r in records]
    for fn in (
        A.taxonomy_quality,
        A.identification,
        A.confusion,
        A.no_script,
        A.ablation,
        A.margins,
        A.scoring_environments,
        A.rolling_origin,
        A.ambiguous_anatomy,
    ):
        fn(records)
    assert _production_rules() == before
    assert [sha256(r["pregame"]) for r in records] == hashes


def test_research_publishes_no_probability(records):
    scripts = [s for r in records for s in r["pregame"]["scripts"]]
    assert scripts and all(s["probability"] is None for s in scripts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def test_research_package_imports_nothing_market_side_and_no_ledger_or_publisher():
    forbidden = (
        "cfb_edge_finder.scripting.market_map",
        "cfb_edge_finder.scripting.expressions",
        "cfb_edge_finder.scripting.ledger",
        "cfb_edge_finder.scripting.publish",
        "cfb_edge_finder.catalog",
        "cfb_edge_finder.kalshi",
        "cfb_edge_finder.execution",
        "cfb_edge_finder.modeling",
        "cfb_edge_finder.projections",
        "cfb_edge_finder.recommendation",
        "cfb_edge_finder.decision",
        "cfb_edge_finder.sizing",
        "cfb_edge_finder.accounting",
    )
    for path in sorted(PACKAGE.glob("*.py")):
        for name in _imports(path):
            assert not name.startswith(forbidden), f"{path.name} imports {name}"


def test_the_loader_never_opens_market_lines_or_rating_fields():
    assert not any("line" in f or "rating" in f or "ranking" in f for f in CFBD_FILES)
    assert not any(k for k in GAME_META_FIELDS if "Elo" in k or "WinProbability" in k or "excitement" in k)


@pytest.mark.parametrize(
    "path",
    [
        "data/scripting/live/frozen/x.json.gz",
        "data/scripting/ledger/2026/publications.jsonl",
        "data/live/cfb_market_catalog.json",
        "data/football/2026/team_games.jsonl",
    ],
)
def test_research_output_may_not_touch_live_publication_or_the_prospective_ledger(path):
    with pytest.raises(PermissionError):
        assert_research_output_path(path)


def test_research_output_path_allows_the_validation_directory():
    assert_research_output_path("data/scripting/validation")


def test_report_sections_run_on_a_replayed_season(records):
    ident = A.identification(records)
    assert set(production_scripts.ARCHETYPE_ORDER) <= set(ident)
    tq = A.taxonomy_quality(records)
    assert tq["production_labels_only"]["at_least_one"]["n"] == len(A.eligible(records))
    assert "EXPLOSIVE_UPSET" in A.ablation(records)
    clusters = A.exploratory_clusters(records, k_max=5)
    assert clusters["label"].startswith("EXPLORATORY")


def test_targets_with_missing_rows_are_excluded_with_a_reason(season):
    rows, _ = season
    gid = rows[0].game_id
    games = _games(rows)
    targets, excluded = schedule_targets(games, [r for r in rows if not (r.game_id == gid and r.site == "away")])
    assert {"game_id": gid, "reason": "NO_BOX_SCORE_ROWS"}.items() <= next(e for e in excluded).items()
    assert gid not in {t.game_id for t in targets}
    assert isinstance(targets[0], Target)
