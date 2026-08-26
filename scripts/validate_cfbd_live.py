#!/usr/bin/env python3
"""Live CFBD validation -- GitHub Actions manual-trigger only.

Runs genuine authenticated CFBD requests through this repo's PRODUCTION
ingestion/normalization code (cfb_edge_finder.data.cfbd_client,
cfb_edge_finder.ingestion.*, cfb_edge_finder.teams.*) and prints safe
aggregate diagnostics only -- counts, field names, small representative
samples. It never prints the API key, an Authorization header, or a bulk
raw payload dump. See .github/workflows/validate-cfbd-live.yml for how
this is invoked (workflow_dispatch only, no scheduled/cron trigger).

Nothing here is a rating, projection, or recommendation -- this script
only validates schedule/team DATA IDENTITY against the live source.

Exit codes: 0 = validation completed and printed (individual findings may
still show mismatches -- that's diagnostic, not a script failure). Non-zero
= the request/parsing itself failed outright (auth, network, unexpected
top-level shape) -- this is the "fail loudly" path the workflow relies on.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.config import Settings  # noqa: E402
from cfb_edge_finder.data.cfbd_client import CFBDAuthError, CFBDClient  # noqa: E402
from cfb_edge_finder.ids import assert_unique_game_ids  # noqa: E402
from cfb_edge_finder.ingestion.game_normalization import (  # noqa: E402
    GameNormalizationError,
    away_classification,
    home_classification,
    normalize_cfbd_game,
)
from cfb_edge_finder.ingestion.team_matching import (  # noqa: E402
    AmbiguousTeamAliasError,
    TeamResolutionError,
    UnknownTeamAliasError,
)
from cfb_edge_finder.ingestion.week_labels import UnclassifiablePostseasonError, derive_week_metadata  # noqa: E402
from cfb_edge_finder.teams import REGISTRY, resolve_team_alias  # noqa: E402

SEASON = 2026
HISTORICAL_CFP_SEASON = 2024  # a completed season, used only if 2026 has no populated `playoff` objects yet
KNOWN_TRANSITIONAL_TEAMS = ("Delaware", "Missouri State", "North Dakota State", "Sacramento State")


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def _is_fbs_involved(raw: dict[str, Any]) -> bool:
    return home_classification(raw) == "fbs" or away_classification(raw) == "fbs"


def _team_display_name(raw: dict[str, Any]) -> str | None:
    for key in ("school", "team", "name"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _team_conference(raw: dict[str, Any]) -> str | None:
    value = raw.get("conference")
    return value if isinstance(value, str) and value.strip() else None


def validate_teams(client: CFBDClient) -> list[dict[str, Any]]:
    section("TEAMS DIAGNOSTIC")
    teams_raw = client.fetch_teams(season=SEASON)
    print(f"/teams/fbs?year={SEASON} HTTP result count: {len(teams_raw)} teams")
    if teams_raw:
        print(f"Observed top-level fields on a team record: {sorted(teams_raw[0].keys())}")

    live_names = [_team_display_name(t) for t in teams_raw]
    unresolvable_live_names = [n for n in live_names if n]
    resolved_ids: set[str] = set()
    unresolved: list[str] = []
    ambiguous: list[str] = []
    for name in unresolvable_live_names:
        try:
            resolved_ids.add(resolve_team_alias(name))
        except AmbiguousTeamAliasError:
            ambiguous.append(name)
        except UnknownTeamAliasError:
            unresolved.append(name)

    registry_ids = {t.team_id for t in REGISTRY}
    print(f"Registry team_id count (before this run): {len(registry_ids)}")
    print(f"Live names resolved to a known team_id: {len(resolved_ids)}")
    print(f"Live names NOT resolvable against current registry/aliases ({len(unresolved)}): {unresolved}")
    print(f"Live names ambiguous against current registry ({len(ambiguous)}): {ambiguous}")
    print(f"Registry team_ids never matched by any live team name: {sorted(registry_ids - resolved_ids)}")

    section("CONFERENCE ASSIGNMENTS FOR KNOWN TRANSITIONAL TEAMS")
    for name in KNOWN_TRANSITIONAL_TEAMS:
        match = next((t for t in teams_raw if _team_display_name(t) == name), None)
        if match is None:
            print(f"{name}: NOT FOUND in live /teams/fbs response")
        else:
            print(f"{name}: live conference={_team_conference(match)!r}")

    section("CONFERENCE MISMATCHES (registry vs live, for names that DO resolve)")
    by_id = {t.team_id: t for t in REGISTRY}
    mismatches = 0
    for raw in teams_raw:
        name = _team_display_name(raw)
        if not name:
            continue
        try:
            team_id = resolve_team_alias(name)
        except (AmbiguousTeamAliasError, UnknownTeamAliasError):
            continue
        live_conf = _team_conference(raw)
        registry_conf = by_id[team_id].conference
        if live_conf and registry_conf and live_conf != registry_conf:
            mismatches += 1
            print(f"{team_id}: registry={registry_conf!r} live={live_conf!r}")
    print(f"Total conference mismatches: {mismatches}")

    conf_counts = Counter(_team_conference(t) for t in teams_raw if _team_conference(t))
    section("LIVE CONFERENCE COUNTS")
    for conf, count in sorted(conf_counts.items()):
        print(f"  {conf}: {count}")

    return teams_raw


def validate_games(client: CFBDClient) -> list[dict[str, Any]]:
    section("GAMES DIAGNOSTIC")
    games_raw = client.fetch_games(season=SEASON, season_type=None)
    print(f"/games?year={SEASON} HTTP result count: {len(games_raw)} games")
    if games_raw:
        print(f"Observed top-level fields on a game record: {sorted(games_raw[0].keys())}")
        with_playoff = [g for g in games_raw if g.get("playoff")]
        print(f"Games with a populated 'playoff' object: {len(with_playoff)}")
        if with_playoff:
            print(f"Sample playoff object keys: {sorted(with_playoff[0]['playoff'].keys())}")
    return games_raw


def run_schedule_ingestion(games_raw: list[dict[str, Any]]) -> None:
    section("SCHEDULE INGESTION DIAGNOSTIC")
    observed_at = datetime.now(UTC)
    fbs_vs_fbs = fbs_vs_fcs = filtered = 0
    neutral_site = tbd_kickoff = 0
    unresolved_aliases: list[str] = []
    validation_failures: list[str] = []
    normalized = []
    fbs_vs_fcs_example: dict[str, Any] | None = None

    for raw in games_raw:
        if not _is_fbs_involved(raw):
            filtered += 1
            continue
        home_cls, away_cls = home_classification(raw), away_classification(raw)
        if home_cls == "fbs" and away_cls == "fbs":
            fbs_vs_fbs += 1
        else:
            fbs_vs_fcs += 1
            if fbs_vs_fcs_example is None:
                fbs_vs_fcs_example = raw
        try:
            game = normalize_cfbd_game(raw, observed_at=observed_at)
        except GameNormalizationError as exc:
            cause = exc.cause
            if isinstance(cause, TeamResolutionError):
                unresolved_aliases.append(f"{cause.raw_name!r} (game={exc.raw_game_id})")
            else:
                validation_failures.append(f"game {exc.raw_game_id}: {cause}")
            continue
        normalized.append(game)
        if game.neutral_site:
            neutral_site += 1
        if game.kickoff_utc is None and raw.get("startDate"):
            tbd_kickoff += 1

    duplicate_ids: list[str] = []
    try:
        assert_unique_game_ids([g.game_id for g in normalized])
    except ValueError as exc:
        duplicate_ids.append(str(exc))

    print(f"games fetched: {len(games_raw)}")
    print(f"games filtered out entirely (no FBS side on either team): {filtered}")
    print(f"  of the {len(games_raw) - filtered} FBS-involved games by classification:")
    print(f"  FBS-vs-FBS: {fbs_vs_fbs}")
    print(f"  FBS-vs-FCS (or non-FBS-classified opponent): {fbs_vs_fcs}")
    print(f"games retained after normalization: {len(normalized)}")
    print(f"unresolved team aliases (excluded from retained): {len(unresolved_aliases)} -> {unresolved_aliases[:20]}")
    print(f"validation failures (excluded from retained): {len(validation_failures)} -> {validation_failures[:20]}")
    print("  (retained + unresolved + validation_failures should equal FBS-vs-FBS + FBS-vs-FCS above)")
    print(f"neutral-site games: {neutral_site}")
    print(f"TBD-kickoff games: {tbd_kickoff}")
    print(f"duplicate canonical game_ids: {duplicate_ids}")
    unique_teams = {g.home_team_id for g in normalized} | {g.away_team_id for g in normalized}
    print(f"unique canonical teams encountered: {len(unique_teams)}")

    section("FBS-VS-FCS REAL EXAMPLE")
    if fbs_vs_fcs_example is not None:
        try:
            g = normalize_cfbd_game(fbs_vs_fcs_example, observed_at=observed_at)
            print(f"raw: {fbs_vs_fcs_example.get('awayTeam')} @ {fbs_vs_fcs_example.get('homeTeam')}")
            print(f"canonical game_id: {g.game_id}")
            print(f"home_team_id={g.home_team_id!r} away_team_id={g.away_team_id!r}")
        except GameNormalizationError as exc:
            print(f"(the first FBS-vs-FCS example found failed to normalize: {exc})")
    else:
        print("No FBS-vs-FCS game found in this response.")


def validate_playoff_structure(client: CFBDClient, games_2026: list[dict[str, Any]]) -> None:
    section("PLAYOFF STRUCTURE VALIDATION")
    candidates = [g for g in games_2026 if g.get("playoff")]
    source_season = SEASON
    if not candidates:
        print(
            f"No populated playoff objects in {SEASON} data yet -- "
            f"fetching historical {HISTORICAL_CFP_SEASON} postseason."
        )
        historical = client.fetch_games(season=HISTORICAL_CFP_SEASON, season_type="postseason")
        candidates = [g for g in historical if g.get("playoff")]
        source_season = HISTORICAL_CFP_SEASON

    if not candidates:
        print(f"No populated playoff objects found even in {HISTORICAL_CFP_SEASON} postseason data. "
              f"Structured postseason mapping could not be validated against a real record this run.")
        return

    sample = candidates[0]
    playoff = sample["playoff"]
    print(f"Source season for this validation: {source_season}")
    print(f"Genuine playoff object keys: {sorted(playoff.keys())}")
    for field in ("competition", "round", "roundName", "round_name", "bowlName", "bowl_name",
                  "bracketSlot", "bracket_slot", "homeSeed", "home_seed", "awaySeed", "away_seed"):
        if field in playoff:
            print(f"  {field} = {playoff[field]!r}")

    try:
        meta = derive_week_metadata(
            season_type_raw=sample["seasonType"], week_raw=sample.get("week"), playoff=playoff
        )
        print(f"derive_week_metadata() result: week_label={meta.week_label!r} cfp_round={meta.cfp_round!r}")
        print("STRUCTURED POSTSEASON MAPPING: OK against this genuine record")
    except UnclassifiablePostseasonError as exc:
        print(f"STRUCTURED POSTSEASON MAPPING: FAILED against this genuine record -- {exc}")


def print_sanitized_fixture_candidates(games_raw: list[dict[str, Any]]) -> None:
    section("SANITIZED_FIXTURE_JSON_START")
    picks: list[dict[str, Any]] = []

    def add(predicate, label):
        for g in games_raw:
            if predicate(g) and g not in picks:
                picks.append(g)
                print(f"# picked for: {label} (id={g.get('id')})", file=sys.stderr)
                return

    def _is_normal_fbs_vs_fbs(g: dict[str, Any]) -> bool:
        return home_classification(g) == "fbs" and away_classification(g) == "fbs" and not g.get("neutralSite")

    def _is_fbs_vs_fcs(g: dict[str, Any]) -> bool:
        return _is_fbs_involved(g) and (home_classification(g) != "fbs" or away_classification(g) != "fbs")

    def _is_any_fbs_vs_fbs(g: dict[str, Any]) -> bool:
        return home_classification(g) == "fbs" and away_classification(g) == "fbs"

    add(_is_normal_fbs_vs_fbs, "normal FBS-vs-FBS")
    add(_is_fbs_vs_fcs, "FBS-vs-FCS")
    add(lambda g: g.get("neutralSite") is True, "neutral-site")
    add(_is_any_fbs_vs_fbs, "another FBS-vs-FBS (backup)")

    print(json.dumps(picks, indent=2, sort_keys=True))
    section("SANITIZED_FIXTURE_JSON_END")


def main() -> int:
    settings = Settings.from_env()
    if not settings.cfbd_api_key:
        print("FATAL: CFBD_API_KEY is not set in the environment.", file=sys.stderr)
        return 2

    client = CFBDClient(api_key=settings.cfbd_api_key)
    print(f"Capture timestamp (UTC): {datetime.now(UTC).isoformat()}")

    try:
        teams_raw = validate_teams(client)
    except CFBDAuthError as exc:
        print(f"FATAL: auth error on /teams/fbs: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: /teams/fbs request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    try:
        games_raw = validate_games(client)
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: /games request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4

    run_schedule_ingestion(games_raw)

    try:
        validate_playoff_structure(client, games_raw)
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: playoff structure validation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5

    print_sanitized_fixture_candidates(games_raw)

    print(f"\nFinished. teams={len(teams_raw)} games={len(games_raw)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
