"""Assemble one game's research packet from already-loaded inputs. PURE (no network, no clock, no file IO).

The packet is a SET of small files per game (see ``FILES``) so a connector that reads files under 1 MB, and a model that
reads them one at a time, can take in everything without truncation:

  packet.json          identity, data quality + missing-data flags, matchup comparison, baseline, QB, availability,
                       environment, game scripts, the analyst worksheet, and pointers to the other files
  team_profiles.json   both teams' opponent-adjusted metrics (raw, adjusted, SE, rank, games, quality)
  schedules.json       both teams' exact results with opponent quality, strength of schedule, similar-opponent record
  distributions.json   scoring-baseline accuracy and the baseline-conditioned historical reference class
  markets.json         every listed Kalshi contract with meanings, all-in costs and research inputs

The four evidence tiers are never mixed inside one field. ANALYST_JUDGMENT is deliberately empty here: it is ChatGPT's.
"""

from __future__ import annotations

from typing import Any

from cfb_edge_finder.modeling.forbidden_features import FORBIDDEN_FEATURE_TOKENS
from cfb_edge_finder.research_packet import football as fb
from cfb_edge_finder.research_packet import history as hist
from cfb_edge_finder.scripting.adjust import LeagueFit
from cfb_edge_finder.scripting.gamelog import TeamGame

PACKET_SCHEMA = "cfb_research_packet/1.0.0"
FILES = ("packet.json", "team_profiles.json", "schedules.json", "distributions.json", "markets.json")

EVIDENCE_TIERS = {
    "VERIFIED_FACT": "an observed result, count, schedule or listing, with its source and observation time",
    "HISTORICAL_ESTIMATE": "a statistic computed from past games (opponent-adjusted rates, splits, reference-class "
    "frequencies)",
    "MODEL_PROJECTION": "this repository's descriptive scoring baseline: uncalibrated, with its error evidence beside "
    "it",
    "EXTERNAL_MODEL_REFERENCE": "another party's model (ESPN's predictor), shown separately and never blended in",
    "ANALYST_JUDGMENT": "yours: probabilities, ranges, the script you believe, the bets. Nothing in the packet "
    "supplies it",
}

ANALYST_STEPS = [
    "1. Read data_quality.missing_data_flags first. Resolve every flag whose `needs_analyst_research` is true by "
    "web research, citing the source and its timestamp (confirmed QB, injuries, late weather).",
    "2. Read football.matchup_comparison and schedules.json: weigh opponent-adjusted offense vs the ACTUAL "
    "opposing defense, discount stats built on weak schedules, and do not over-weight last-3 form.",
    "3. Read game_script: the dominant, competitive and upset scripts, and what evidence would flip each.",
    "4. Use distributions.json for shape and spread (reference-class frequencies, closest games, the baseline's "
    "real error). Do not invent a score and assume a normal curve.",
    "5. Write YOUR probability and an uncertainty range for each thesis you will price (winner, cover, total, team "
    "totals, halves, quarters, ladders). Keep them football-derived: never copy Kalshi's implied probability.",
    "6. Compare every listed contract of the relevant families (markets.json) on the side you would buy, at the NO "
    "ask for NO, with the all-in cost. Use bet-up-to prices from your LOWER bound. A stake is optional, advisory "
    "and yours: leave it unsized unless you can justify it (max 5U, 1U = $75).",
    "7. Reconcile correlated exposure across contracts and games, then return one ranked card. Passing is a "
    "correct answer.",
]

OWNER_RULES = [
    "1U = $75. Maximum 5U per individual bet.",
    "Never recommend betting against Auburn (includes the Under on Auburn's own team total; game/1Q/1H total "
    "Unders are independent).",
    "Recommendations are allowed until the actual kickoff, never at or after it (no pre-kickoff buffer); they still "
    "need a recent captured quote, valid liquidity and a fee-inclusive Bet Up To.",
    f"No {FORBIDDEN_FEATURE_TOKENS[0]} in handicapping (owner ruling 2026-11-02): the metric is excluded from "
    f"every table here.",
    "No fabricated probabilities, pricing advantages or stakes. No forced bets or loss-chasing.",
    "Compare every relevant market before choosing. Keep analyst recommendations separate from automated "
    "qualification/execution gates.",
]


def _flag(
    code: str, severity: str, detail: str, *, research: bool = False, action: str | None = None
) -> dict[str, Any]:
    return {"code": code, "severity": severity, "detail": detail, "needs_analyst_research": research, "action": action}


def missing_data_flags(
    *,
    content: dict[str, Any] | None,
    identity_status: str,
    extras: dict[str, Any] | None,
    rosters: dict[str, dict[str, Any]],
    availability_listed: dict[str, int | None],
    comparables_status: str,
    markets_usable: bool,
    markets_complete: bool,
    schedules: dict[str, dict[str, Any]],
    period: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    if not markets_usable:
        flags.append(
            _flag(
                "MARKET_DISCOVERY_INCOMPLETE",
                "BLOCKING",
                "the game's native market menu is not proven complete; do not recommend from it",
            )
        )
    if identity_status not in ("PASS", "RESOLVED") or content is None:
        flags.append(
            _flag(
                "FOOTBALL_IDENTITY_UNRESOLVED",
                "BLOCKING",
                f"the Kalshi game could not be matched to an ESPN game ({identity_status}); no football sections "
                f"were built",
                research=True,
                action="resolve the teams manually before handicapping",
            )
        )
    if content is not None:
        conf = content.get("confidence") or {}
        fresh = conf.get("freshness") or {}
        if fresh.get("status") not in (None, "FRESH") or fresh.get("missing_game_ids"):
            flags.append(
                _flag(
                    "FOOTBALL_LOG_INCOMPLETE",
                    "MAJOR",
                    f"the game log may be missing finished games: {fresh.get('rule') or fresh}",
                    action="treat adjusted numbers as stale; check the teams' latest results",
                )
            )
        absent = [side for side in ("home", "away") if not (conf.get("games_observed") or {}).get(side)]
        if absent:
            flags.append(
                _flag(
                    "FOOTBALL_HISTORY_ABSENT",
                    "MAJOR",
                    f"no completed games with box scores in the log for: {', '.join(absent)} "
                    "(the log collects FBS-involved games only; a non-FBS team's schedule appears as final scores in "
                    "schedules.json when the scoreboard sweep saw them). Matchup comparison, baseline and reference "
                    "class cannot be built from this repository for this game",
                    research=True,
                    action=(
                        "research both teams' results, opponents and style independently; widen every range; "
                        "pass if the evidence is thin"
                    ),
                )
            )
        for side in ("home", "away"):
            n = (conf.get("games_observed") or {}).get(side)
            if n is not None and n < 4 and side not in absent:
                flags.append(
                    _flag(
                        "THIN_SAMPLE",
                        "MAJOR",
                        f"{side} team has only {n} completed games with box scores; adjusted values lean on a prior",
                    )
                )
            sch = schedules.get(side) or {}
            sos = (sch.get("strength_of_schedule") or {}).get("flag")
            if sos:
                flags.append(_flag("SCHEDULE_QUALITY", "INFO", f"{side}: {sos}"))
        if comparables_status != "CONDITIONAL":
            flags.append(
                _flag(
                    "REFERENCE_CLASS_THIN_OR_MISSING",
                    "MAJOR",
                    f"historical comparables are {comparables_status}; spread/total shape is less reliable",
                )
            )
    flags.append(
        _flag(
            "QB_NOT_CONFIRMED",
            "MAJOR",
            "no keyless source publishes a confirmed starting quarterback; the packet shows the PROJECTED starter "
            "inferred from box scores",
            research=True,
            action="search for the confirmed starter for both teams (team/beat reports, depth chart, injury report); "
            "record source + time",
        )
    )
    for side in ("home", "away"):
        passers = ((content or {}).get("_qb") or {}).get(side) if content else None
        if passers and passers.get("changed_in_last_3_games"):
            flags.append(
                _flag(
                    "QB_CHANGE_RECENT",
                    "MAJOR",
                    f"{side} team used more than one primary passer in its last three games",
                    research=True,
                    action="establish who starts and why the change happened",
                )
            )
    flags.append(
        _flag(
            "NO_STRUCTURED_INJURY_SOURCE",
            "MAJOR",
            "ESPN's keyless feeds carry no usable CFB injury report; athlete rows in the repository's injury "
            "listing are unresolved references with no name or status. The roster feed flags "
            + ", ".join(f"{s}: {len(rosters.get(s, {}).get('flagged_athletes') or [])}" for s in ("home", "away"))
            + " athletes. Absence of a listing is not health.",
            research=True,
            action="search for injuries/suspensions/OL and defensive-front absences for both teams; record source + "
            "time",
        )
    )
    flags.append(
        _flag(
            "EPA_UNAVAILABLE",
            "INFO",
            "expected points added is not available keylessly; opponent-adjusted success rate, explosive rate, "
            "points per drive and per scoring opportunity are provided",
        )
    )
    thin = [s_ for s_ in ("home", "away") if ((period or {}).get(s_) or {}).get("games", 0) < 4]
    flags.append(
        _flag(
            "PERIOD_SPLITS_RAW_ONLY"
            if not thin
            else "PERIOD_SPLITS_THIN"
            if len(thin) < 2
            else "PERIOD_TEAM_SPLITS_UNAVAILABLE",
            "INFO",
            "first-half / first-quarter team splits are RAW and unadjusted (football.period_splits_raw); "
            + (f"fewer than 4 captured linescores for: {', '.join(thin)}. " if thin else "")
            + "opening-drive tendencies are not captured. 1H/1Q reference classes are conditioned on "
            "the full-game baseline only",
        )
    )
    weather = (extras or {}).get("weather") or {}
    venue = (extras or {}).get("venue") or {}
    if venue.get("indoor") is True:
        pass
    elif weather.get("status") != "FORECAST":
        flags.append(
            _flag(
                "WEATHER_UNAVAILABLE",
                "INFO",
                weather.get("reason") or "no weather source answered",
                research=True,
                action="check the game-time forecast if the venue is outdoors",
            )
        )
    if not markets_complete:
        flags.append(
            _flag(
                "MARKET_FILE_PARTIAL",
                "BLOCKING",
                "markets.json does not hold every contract the execution index lists for this game",
            )
        )
    return flags


def build_packet_files(
    *,
    game: dict[str, Any],
    content: dict[str, Any] | None,
    league: LeagueFit | None,
    history: list[TeamGame],
    identity: dict[str, Any],
    home: dict[str, Any] | None,
    away: dict[str, Any] | None,
    extras: dict[str, Any] | None,
    rosters: dict[str, dict[str, Any]],
    availability: dict[str, Any],
    reference_games: list[dict[str, Any]],
    unconditional_summary: dict[str, Any],
    baseline_acc: dict[str, Any],
    markets: dict[str, Any],
    markets_paths: list[str],
    markets_usable: bool,
    inputs: dict[str, Any],
    schedule_events: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return {filename: payload} for one game. ``home``/``away`` are {team_id, name} or None when identity failed."""
    key = game["game_key"]
    files: dict[str, dict[str, Any]] = {}
    header = {
        "game_key": key,
        "title": game.get("title"),
        "kickoff_utc": game.get("kickoff"),
        "kickoff_et": game.get("kickoff_et"),
        "slate_date": game.get("slate_date"),
        "window": game.get("window"),
    }

    schedules: dict[str, Any] = {}
    profiles: dict[str, Any] = {}
    matchup: list[dict[str, Any]] = []
    form: dict[str, Any] = {}
    splits: dict[str, Any] = {}
    period: dict[str, Any] = {}
    rest: dict[str, Any] = {}
    pace: dict[str, Any] = {}
    qb: dict[str, Any] = {}
    baseline: dict[str, Any] = {"status": "UNAVAILABLE", "reason": "no football content"}
    reference: dict[str, Any]
    chosen: list[dict[str, Any]] = []
    script: dict[str, Any] = {"status": "UNAVAILABLE"}
    comp_status = "UNAVAILABLE"

    if content is not None and league is not None and home and away:
        cutoff = content["football_data_cutoff"]
        ranks = fb.net_rating_ranks(league)
        for side, team, opp in (("home", home, away), ("away", away, home)):
            if not any(r.team_id == team["team_id"] for r in history):
                schedules[side] = fb.results_from_schedule(
                    schedule_events or [], team["team_id"], content["football_data_cutoff"]
                )
                rest[side] = fb.rest_and_situation(history, team["team_id"], content["kickoff_utc"])
                continue
            sched = fb.team_schedule(history, team["team_id"], league, ranks, opp["team_id"])
            schedules[side] = sched
            form[side] = fb.recent_form(sched)
            splits[side] = fb.split_table(history, team["team_id"])
            period[side] = fb.period_splits(history, team["team_id"])
            rest[side] = fb.rest_and_situation(history, team["team_id"], content["kickoff_utc"])
            profiles[side] = {
                "team_id": team["team_id"],
                "name": team["name"],
                "net_adjusted_points_rating": fb.net_rating(league, team["team_id"]),
                "net_rating_rank_among_fbs": (ranks.get(team["team_id"]) or (None, None))[0],
                "fbs_universe": len(ranks),
                "metrics": fb.team_metric_table(league, team["team_id"]),
            }
            qb[side] = fb.passers_summary(history, team["team_id"])
        matchup = fb.matchup_table(league, home["team_id"], away["team_id"])
        pace = fb.pace_environment(league, home["team_id"], away["team_id"], history)
        vector = content["matchup_profile"]
        sb = vector["scoring_baseline"]
        baseline = {
            "evidence_tier": "MODEL_PROJECTION",
            "label": sb.get("label"),
            "home_points": sb.get("home_points"),
            "away_points": sb.get("away_points"),
            "home_margin": sb.get("home_margin"),
            "total_points": sb.get("total_points"),
            "home_effect_applied": sb.get("home_effect_applied"),
            "uncertainty_points": sb.get("uncertainty_points"),
            "refs": sb.get("refs"),
            "accuracy": baseline_acc,
            "use": (
                "a descriptive, opponent-adjusted scoring summary. It is uncalibrated and has been only slightly "
                "better than "
                "guessing the league-average total; treat it as one input, not as the answer"
            ),
        }
        reference, chosen = hist.comparables(reference_games, sb.get("home_margin"), sb.get("total_points"))
        comp_status = reference.get("status", "UNAVAILABLE")
        script = {
            "evidence_tier": "MODEL_PROJECTION",
            "data_confidence": content.get("data_confidence"),
            "confidence": content.get("confidence"),
            "findings": content.get("matchup_findings"),
            "scripts": content.get("game_scripts"),
            "read": content.get("sift_read"),
            "note": "scripts are the deterministic engine's evidence-gated game scripts (dominant / competitive / "
            "upset). "
            "They are football context, not predictions; what would flip each is in its causal chain.",
        }
        files["team_profiles.json"] = {
            "schema_version": PACKET_SCHEMA,
            **header,
            "adjusted": True,
            "adjustment": {
                "method": "ridge-regularised offense/defense/home-field effects per metric, fitted on every completed "
                "game "
                "before the data cutoff; opponent-adjusted values are what a team would do against an average "
                "opponent",
                "version": content.get("adjustment_version"),
                "config": (vector.get("adjustment") or {}).get("config"),
                "stability": (vector.get("adjustment") or {}).get("stability"),
                "football_data_cutoff": cutoff,
                "fbs_universe": len(ranks),
            },
            "excluded_by_owner_rule": sorted(fb.EXCLUDED_METRIC_IDS),
            "efficiency_gaps": {"epa": fb.EPA_GAP},
            "teams": profiles,
            "raw_defensive_counts_unadjusted": {
                side: _raw_defense(history, (home if side == "home" else away)["team_id"]) for side in ("home", "away")
            },
        }
        files["schedules.json"] = {
            "schema_version": PACKET_SCHEMA,
            **header,
            "football_data_cutoff": cutoff,
            "teams": schedules,
            "recent_form": form,
            "home_away_splits": splits,
            "period_splits_raw": period,
            "rest_and_situation": rest,
        }
    else:
        reference = {"status": "UNAVAILABLE", "reason": "no football content for this game"}

    flags = missing_data_flags(
        content=({**content, "_qb": qb} if content is not None else None),
        identity_status=(identity or {}).get("status", "FAIL"),
        extras=extras,
        rosters=rosters,
        availability_listed={s: ((availability.get(s) or {}).get("listed")) for s in ("home", "away")},
        comparables_status=comp_status,
        markets_usable=markets_usable,
        markets_complete=markets["contract_count"] == game.get("market_count"),
        schedules=schedules,
        period=period,
    )

    files["distributions.json"] = {
        "schema_version": PACKET_SCHEMA,
        **header,
        "tiers": {
            "baseline_accuracy": "HISTORICAL_ESTIMATE",
            "reference_class": "HISTORICAL_ESTIMATE",
            "league_shape": "HISTORICAL_ESTIMATE",
        },
        "baseline_accuracy": baseline_acc,
        "reference_class": reference,
        "league_wide_shape": unconditional_summary,
        "how_to_use": (
            "p_*_greater_than tables give P(outcome > threshold) among past games whose pre-kickoff baseline "
            "resembled this one. "
            "They show spread and skew, including how often a favourite fails a spread. They are NOT calibrated "
            "probabilities for "
            "this game: shift them for the matchup, QB, injuries and weather evidence, and widen your range when "
            "the reference class is thin."
        ),
    }
    files["markets.json"] = markets

    files["packet.json"] = {
        "schema_version": PACKET_SCHEMA,
        **header,
        "evidence_tiers": EVIDENCE_TIERS,
        "owner_rules": OWNER_RULES,
        "kickoff_rule": "recommendations are allowed until the actual kickoff and never at or after kickoff_utc; "
        "require a recent captured Kalshi quote, valid liquidity and a fee-inclusive Bet Up To; "
        "compute against your own clock",
        "files": {name: f"data/research_packets/games/{key}/{name}" for name in FILES}
        | {"markets_parts": markets_paths},
        "teams": {"home": home, "away": away, "identity": identity},
        "inputs": inputs,
        "data_quality": {
            "football_data_confidence": (content or {}).get("data_confidence"),
            "missing_data_flags": flags,
            "blocking_flags": [f["code"] for f in flags if f["severity"] == "BLOCKING"],
            "sections": {
                "matchup_comparison": "OK" if matchup else "UNAVAILABLE",
                "schedules": (
                    "UNAVAILABLE"
                    if not schedules
                    else "RESULTS_ONLY"
                    if any(s_.get("results_only") for s_ in schedules.values())
                    else "OK"
                ),
                "qb": "PROJECTED_ONLY" if qb else "UNAVAILABLE",
                "injuries": "NO_STRUCTURED_SOURCE",
                "environment": (extras or {}).get("status", "UNAVAILABLE"),
                "distributions": comp_status,
                "markets": "OK" if markets["contract_count"] else "EMPTY",
            },
        },
        "football": {
            "football_data_cutoff": (content or {}).get("football_data_cutoff"),
            "matchup_comparison": matchup,
            "recent_form": form,
            "pace_and_tendencies": pace,
            "baseline": baseline,
            "home_away_splits": splits,
            "period_splits_raw": period,
            "rest_and_situation": rest,
        },
        "qb": {
            "evidence_tier": "VERIFIED_FACT",
            "status": "PROJECTED_ONLY",
            **{side: _qb_block(qb.get(side), rosters.get(side)) for side in ("home", "away") if qb},
        },
        "availability": {
            "status": "NO_STRUCTURED_SOURCE",
            "observed_at": {s: (availability.get(s) or {}).get("observed_at") for s in ("home", "away")},
            "espn_core_injury_listing": {s: availability.get(s) for s in ("home", "away")},
            "roster_feed": {
                s: {
                    k: rosters.get(s, {}).get(k)
                    for k in ("status", "observed_at", "flagged_athletes", "flagged_meaning")
                }
                for s in ("home", "away")
            },
            "head_coaches": {s: rosters.get(s, {}).get("head_coach") for s in ("home", "away")},
            "interpretation": "an empty or null-valued list is NOT evidence of health. Confirmed injuries, "
            "suspensions, OL and "
            "defensive-front absences must come from analyst research with a cited source and timestamp.",
            "absence_effect_guide": "for each confirmed absence: which unit (QB, OL, front seven, secondary, skill) "
            "-> which row of "
            "football.matchup_comparison it touches -> widen your range even if you do not shift the mean",
        },
        "environment": {"evidence_tier": "VERIFIED_FACT", **(extras or {"status": "UNAVAILABLE"})},
        "game_script": script,
        "market_summary": {
            "contract_count": markets["contract_count"],
            "families": {
                f: {
                    "contracts": v["contracts"],
                    "coverage": v["coverage"],
                    "estimate": v["estimate"],
                    "gaps": v.get("gaps", []),
                }
                for f, v in markets["families"].items()
            },
            "price_provenance": markets["price_provenance"],
        },
        "analyst_worksheet": {
            "steps": ANALYST_STEPS,
            "your_output_per_game": {
                "evidence_tier": "ANALYST_JUDGMENT",
                "projected_score": "home/away points with a range (not a point estimate)",
                "winner_probability": {"point": None, "low": None, "high": None},
                "script_probabilities": {"dominant": None, "competitive": None, "upset": None},
                "family_estimates": "for every family you price: the probability (low/point/high) of each thesis, and "
                "the contracts "
                "that express it",
                "confirmed_inputs": {"qb": None, "injuries": None, "weather": None, "sources": []},
            },
        },
    }
    return files


def _qb_block(passers: dict[str, Any] | None, roster: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = dict(passers or {})
    if roster:
        out["roster_quarterbacks"] = roster.get("quarterbacks")
    return out


def _raw_defense(history: list[TeamGame], team_id: str) -> dict[str, Any]:
    rs = [r for r in history if r.team_id == team_id]
    n = len(rs)
    if not n:
        return {"games": 0}

    def per(key: str) -> float | None:
        vals = [(r.box or {}).get(key) for r in rs if (r.box or {}).get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        "games": n,
        "sacks_per_game": per("def_sacks"),
        "qb_hurries_per_game": per("def_qb_hurries"),
        "tackles_for_loss_per_game": per("def_tfl"),
        "passes_defended_per_game": per("def_passes_defended"),
        "adjusted": False,
        "caveat": "raw counts, not opponent-adjusted; shown for pass-rush / defensive-front context only. Sack rate "
        "allowed "
        "(adjusted) is in metrics.pressure_and_protection",
    }
