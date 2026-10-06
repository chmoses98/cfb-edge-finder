"""The market-blind football builder: packet in, frozen-ready content out. PURE.

`FootballPacket` is the ENTIRE input to the football artifact. Its fields
are football facts -- the league game log, the two teams, kickoff, site,
availability, identity resolution and freshness. It has no field that could
carry a price, a line, a total or an implied probability, and
`build_content` reads nothing else.

That is the market-blindness guarantee in one sentence: a quantity that is
not an input cannot be an influence. `tests/test_script_engine_market_blindness.py`
checks both that the packet has no such field and that the module graph
below cannot reach one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from cfb_edge_finder.scripting import (
    ADJUSTMENT_VERSION,
    METHODOLOGY_VERSION,
    SCRIPT_ARTIFACT_SCHEMA_VERSION,
)
from cfb_edge_finder.scripting.adjust import DEFAULT_CONFIG, AdjustConfig, LeagueFit, fit_league
from cfb_edge_finder.scripting.confidence import assess
from cfb_edge_finder.scripting.findings import CATEGORY_DATA, CATEGORY_MATCHUP, derive_findings
from cfb_edge_finder.scripting.gamelog import TeamGame, before, data_cutoff
from cfb_edge_finder.scripting.matchup import GameIdentity, matchup_vector
from cfb_edge_finder.scripting.scripts import build_scripts


@dataclass(frozen=True)
class FootballPacket:
    identity: GameIdentity
    game_key: str | None
    rows: tuple[TeamGame, ...]
    availability: dict[str, Any] = field(default_factory=dict)
    identity_check: dict[str, Any] = field(default_factory=dict)
    freshness: dict[str, Any] = field(default_factory=dict)


def _fp(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class LeagueFitCache:
    """One league fit per distinct set of pre-kickoff games.

    Every Saturday game kicking off after the same set of completed games
    shares one fit; the key is that set, so a fit can never be reused for a
    game whose history differs."""

    def __init__(self, config: AdjustConfig = DEFAULT_CONFIG):
        self.config = config
        self._fits: dict[frozenset[str], LeagueFit] = {}

    def get(self, rows: tuple[TeamGame, ...], cutoff: str) -> LeagueFit:
        history = before(rows, cutoff)
        key = frozenset(r.game_id for r in history)
        fit = self._fits.get(key)
        if fit is None:
            fit = fit_league(history, cutoff, self.config)
            self._fits[key] = fit
        return fit


def sift_read(
    names: dict[str, str],
    findings: list[dict[str, Any]],
    scripts: dict[str, Any],
    confidence: dict[str, Any],
) -> dict[str, Any]:
    """The concise read, assembled from structured findings only.

    Every sentence is a finding's own statement or a script's own summary;
    nothing is written here that the evidence does not already say."""
    matchup = [f for f in findings if f["category"] == CATEGORY_MATCHUP]
    points = [{"text": f["statement"], "findings": [f["code"]]} for f in matchup[:3]]
    primary = next((s for s in scripts.get("scripts") or [] if s["role"] == "PRIMARY"), None)
    if primary is not None:
        headline = primary["summary"]
        headline_findings = primary["required_findings"]
    elif matchup:
        headline = matchup[0]["statement"]
        headline_findings = [matchup[0]["code"]]
    else:
        headline = (
            f"No opponent-adjusted matchup difference between {names['home']} and {names['away']} clears its "
            f"evidence threshold; no script is published."
        )
        headline_findings = []
    caveats = [f["statement"] for f in findings if f["category"] == CATEGORY_DATA]
    return {
        "headline": headline,
        "headline_findings": headline_findings,
        "points": points,
        "caveats": caveats,
        "data_confidence": confidence["level"],
        "generated_from": "structured findings and script summaries only; no market input",
    }


def build_content(packet: FootballPacket, cache: LeagueFitCache | None = None) -> dict[str, Any]:
    ident = packet.identity
    cutoff = data_cutoff(ident.kickoff_utc)
    cache = cache or LeagueFitCache()
    league = cache.get(packet.rows, cutoff)
    history = before(packet.rows, cutoff)
    names = {"home": ident.home_name, "away": ident.away_name}

    vector = matchup_vector(league, list(history), ident)
    findings = derive_findings(vector, names, packet.availability)
    confidence = assess(
        vector,
        names=names,
        availability=packet.availability,
        identity_check=packet.identity_check,
        freshness=packet.freshness,
    )
    scripts = build_scripts(ident.event_id, findings, names, vector["scoring_baseline"], confidence["level"])

    team_games = {
        side: sorted(r.game_id for r in history if r.team_id == (ident.home_id if side == "home" else ident.away_id))
        for side in ("home", "away")
    }
    return {
        "schema_version": SCRIPT_ARTIFACT_SCHEMA_VERSION,
        "methodology_version": METHODOLOGY_VERSION,
        "adjustment_version": ADJUSTMENT_VERSION,
        "event_id": ident.event_id,
        "game_key": packet.game_key,
        "season": ident.season,
        "teams": {
            "home": {"team_id": ident.home_id, "name": ident.home_name},
            "away": {"team_id": ident.away_id, "name": ident.away_name},
        },
        "kickoff_utc": ident.kickoff_utc,
        "neutral_site": ident.neutral_site,
        "market_blind": True,
        "football_data_cutoff": cutoff,
        "inputs": {
            "rows_fingerprint": league.rows_fingerprint,
            "games_in_window": league.games_in_window,
            "team_games": team_games,
            "availability_fingerprint": _fp(packet.availability),
            "identity_fingerprint": _fp(packet.identity_check),
            "freshness_status": packet.freshness.get("status"),
            "sources": sorted({r.source for r in history} | {r.pbp_source for r in history if r.pbp_source}),
        },
        "data_confidence": confidence["level"],
        "confidence": confidence,
        "matchup_profile": vector,
        "matchup_findings": findings,
        "game_scripts": scripts,
        "sift_read": sift_read(names, findings, scripts, confidence),
    }
