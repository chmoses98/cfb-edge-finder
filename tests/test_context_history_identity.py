"""Regression: team nicknames do not define scoreboard history identity."""

from cfb_edge_finder.execution.context_sources import completed_games_for


def _competitor(team_id, name, nickname, *, home_away, score):
    return {
        "homeAway": home_away,
        "score": str(score),
        "team": {
            "id": team_id,
            "displayName": name,
            "name": nickname,
            "shortDisplayName": nickname,
            "abbreviation": nickname[:3].upper(),
        },
    }


def _event(event_id, team_id, name, nickname, opponent, *, date, score):
    return {
        "id": event_id,
        "date": date,
        "status": {"type": {"completed": True}},
        "competitions": [
            {
                "neutralSite": False,
                "competitors": [
                    _competitor(team_id, name, nickname, home_away="away", score=score),
                    _competitor(
                        event_id + "-opponent",
                        opponent,
                        "Opponent",
                        home_away="home",
                        score=10,
                    ),
                ],
            }
        ],
    }


def test_espn_team_id_prevents_cross_school_bulldog_contamination():
    tech = _event(
        "g1", "2348", "Louisiana Tech Bulldogs", "Bulldogs", "Old Dominion",
        date="2026-10-24T19:00:00Z", score=28,
    )
    georgia = _event(
        "g2", "61", "Georgia Bulldogs", "Bulldogs", "Florida",
        date="2026-10-24T19:00:00Z", score=49,
    )
    mississippi = _event(
        "g3", "344", "Mississippi State Bulldogs", "Bulldogs", "Texas",
        date="2026-10-24T19:00:00Z", score=21,
    )
    shared_nickname = {"Bulldogs"}
    history = completed_games_for(
        shared_nickname, [tech, georgia, mississippi, tech], team_id="2348"
    )
    assert len(history) == 1
    assert history[0]["points_for"] == 28
    assert history[0]["opponent"] == "Old Dominion"


def test_missing_espn_id_does_not_fall_back_to_nickname():
    team = _event(
        "g4", "999", "Other Tigers", "Tigers", "Opponent",
        date="2026-10-24T19:00:00Z", score=40,
    )
    assert completed_games_for({"Tigers"}, [team], team_id="11") == []


def test_existing_names_only_fixture_behavior_remains():
    team = _event(
        "g5", "999", "Other Tigers", "Tigers", "Opponent",
        date="2026-10-24T19:00:00Z", score=40,
    )
    assert len(completed_games_for({"Other Tigers"}, [team])) == 1
