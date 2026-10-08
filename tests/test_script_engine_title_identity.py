"""The Script Engine finds the physical game for a title whose school name contains the separator.

Kalshi's milestone for UAlbany's trip to Stony Brook is "University at Albany at Stony Brook". The
Script Engine reads a game's two teams from its title alone (it is market-blind: it never sees the
contracts), so the title parser reports that title ambiguous, and the game failed identity
("no football-schedule event matched both teams near kickoff") while the explorer export, which has
the contracts, already named both schools. The football schedule is the Script Engine's own evidence:
the one reading whose both teams play a scheduled game near kickoff is the game.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import script_engine  # noqa: E402

from cfb_edge_finder.execution.semantics import title_readings  # noqa: E402

KICKOFF = "2026-10-17T18:30:00Z"


def _event(eid, home_names, away_names, date=KICKOFF):
    """A schedule.json event as `load_football` leaves it (name keys built from ESPN's names)."""

    def side(home_away, names):
        keys: set[str] = set()
        for n in names:
            keys |= script_engine.names_util.name_variants(n) | script_engine.names_util.name_variants(
                script_engine._fold(n)
            )
        return {"home_away": home_away, "names": names, "name_keys": sorted(keys), "abbreviation": names[-1]}

    return {
        "id": eid,
        "date": date,
        "neutral_site": False,
        "competitors": [side("home", home_names), side("away", away_names)],
    }


STBK_ALB = _event(
    "401866666",
    ["Stony Brook Seawolves", "Stony Brook", "Seawolves", "STBK"],
    ["UAlbany Great Danes", "Great Danes", "UAlbany", "ALB"],
)
TOW_STBK = _event(
    "401866659",
    ["Towson Tigers", "Towson", "Tigers", "TOW"],
    ["Stony Brook Seawolves", "Stony Brook", "Seawolves", "STBK"],
    date="2026-10-10T20:00:00Z",
)


def test_title_readings_lists_every_split():
    assert title_readings("University at Albany at Stony Brook") == [
        ("University", "Albany at Stony Brook"),
        ("University at Albany", "Stony Brook"),
    ]
    assert title_readings("Iowa St. at BYU") == [("Iowa St.", "BYU")]
    assert title_readings("LSU vs Ole Miss") == [("LSU", "Ole Miss")]
    assert title_readings(None) == []


def test_ualbany_at_stony_brook_resolves_to_the_scheduled_game():
    request, event, _, _ = script_engine.resolve_request(
        "26OCT17ALBYSTON", "University at Albany at Stony Brook", KICKOFF, [TOW_STBK, STBK_ALB]
    )
    assert event is not None and event["id"] == "401866666"
    assert (request.kalshi_away, request.kalshi_home) == ("University at Albany", "Stony Brook")
    check = script_engine.identity_check(request, event, *script_engine.match_event(request, [STBK_ALB])[1:])
    assert check["status"] == "PASS"


def test_no_scheduled_game_for_any_reading_still_fails_identity():
    request, event, _, _ = script_engine.resolve_request(
        "26OCT17ALBYSTON", "University at Albany at Stony Brook", KICKOFF, [TOW_STBK]
    )
    assert event is None
    assert request.kalshi_home is None and request.kalshi_away is None


def test_two_readings_both_scheduled_is_not_guessed():
    # Contrived: if "University" and "Albany at Stony Brook" were ALSO a scheduled game, the schedule
    # could not decide, and the request stays ambiguous rather than picking one.
    other = _event("x2", ["Albany at Stony Brook"], ["University"])
    request, event, _, _ = script_engine.resolve_request(
        "26OCT17ALBYSTON", "University at Albany at Stony Brook", KICKOFF, [STBK_ALB, other]
    )
    assert event is None and request.kalshi_home is None


def test_an_ordinary_title_is_matched_exactly_as_before():
    request, event, _, _ = script_engine.resolve_request(
        "26OCT10TOWSTBK", "Stony Brook at Towson", "2026-10-10T20:00:00Z", [TOW_STBK]
    )
    assert event is not None and event["id"] == "401866659"
    assert (request.kalshi_away, request.kalshi_home) == ("Stony Brook", "Towson")
