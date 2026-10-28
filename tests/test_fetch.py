"""Offline tests: fake API responses, real parsing code."""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from advisor import espn, sleeper  # noqa: E402

SLEEPER_PLAYERS = {
    "1": {"full_name": "Joe Burrow", "position": "QB", "team": "CIN"},
    "2": {"full_name": "Bijan Robinson", "position": "RB", "team": "ATL", "injury_status": "Questionable"},
    "3": {"full_name": "Bench Guy", "position": "WR", "team": "NYJ"},
    "4": {"full_name": "Opp QB", "position": "QB", "team": "KC"},
    "5": {"full_name": "Waiver Darling", "position": "RB", "team": "DET"},
    "6": {"full_name": "Free QB", "position": "QB", "team": "DAL"},
}

SLEEPER_PROJ = {
    "1": {"player_id": "1", "opponent": "TEN", "stats": {"pts_half_ppr": 21.06, "pts_ppr": 21.06}},
    "4": {"player_id": "4", "opponent": "DEN", "stats": {"pts_half_ppr": 25.0}},
    "6": {"player_id": "6", "opponent": "ARI", "stats": {"pts_half_ppr": 22.4}},
}


def sleeper_fake(url, **_):
    routes = {
        "/state/nfl": {"league_season": "2026", "week": 8, "display_week": 8},
        "/user/me": {"user_id": "u1"},
        "/user/u1/leagues/nfl/2026": [{"league_id": "L1", "name": "Work League",
                                       "scoring_settings": {"rec": 0.5}, "roster_positions": ["QB", "RB"],
                                       "settings": {"waiver_type": 2, "waiver_budget": 100}}],
        "/league/L1/rosters": [
            {"roster_id": 1, "owner_id": "u1", "players": ["1", "2", "3"], "starters": ["1", "2"],
             "settings": {"wins": 5, "losses": 2, "waiver_budget_used": 30}},
            {"roster_id": 2, "owner_id": "u2", "players": ["4"], "starters": ["4"], "settings": {}},
        ],
        "/league/L1/users": [{"user_id": "u1", "display_name": "me", "metadata": {"team_name": "Mine"}},
                             {"user_id": "u2", "display_name": "rival"}],
        "/league/L1/matchups/8": [{"roster_id": 1, "matchup_id": 3}, {"roster_id": 2, "matchup_id": 3}],
        "/players/nfl/trending/add?lookback_hours=48&limit=100": [
            {"player_id": "4", "count": 900}, {"player_id": "5", "count": 500}],
    }
    return routes[url.replace(sleeper.BASE, "")]


class SleeperTest(unittest.TestCase):
    def test_fetch(self):
        with mock.patch.object(sleeper, "get_json", side_effect=sleeper_fake), \
             mock.patch.object(sleeper, "load_players", return_value=SLEEPER_PLAYERS), \
             mock.patch.object(sleeper, "projections", return_value=SLEEPER_PROJ):
            [lg] = sleeper.fetch("me")
        self.assertEqual(lg["team_name"], "Mine")
        self.assertEqual(lg["record"], "5-2")
        self.assertEqual(lg["waiver_budget_left"], 70)
        self.assertEqual([p["slot"] for p in lg["roster"]], ["starter", "starter", "bench"])
        self.assertEqual(lg["roster"][1]["injury_status"], "Questionable")
        self.assertEqual(lg["opponent"]["team_name"], "rival")
        # Rostered trending player (4) is skipped.
        self.assertEqual([p["name"] for p in lg["free_agents"]], ["Waiver Darling"])
        self.assertEqual(lg["roster"][0]["projected_points"], 21.1)  # half-PPR league uses pts_half_ppr
        # Highest-projected free agent shows up even though it isn't trending.
        self.assertEqual(lg["best_projected_free_agents"]["QB"][0]["name"], "Free QB")


def espn_player(pid, name, pos, team, proj=None, injury="ACTIVE", owned=50.0):
    stats = [{"scoringPeriodId": 8, "statSourceId": 1, "appliedTotal": proj}] if proj is not None else []
    return {"id": pid, "fullName": name, "defaultPositionId": pos, "proTeamId": team,
            "injuryStatus": injury, "stats": stats, "ownership": {"percentOwned": owned}}


ESPN_LEAGUE = {
    "scoringPeriodId": 8,
    "status": {"currentMatchupPeriod": 8},
    "settings": {"name": "Family League",
                 "scoringSettings": {"scoringItems": [{"statId": 53, "points": 0.5}]}},
    "teams": [
        {"id": 1, "name": "Mine", "owners": ["{ABC}"], "record": {"overall": {"wins": 4, "losses": 3}},
         "roster": {"entries": [
             {"lineupSlotId": 0, "playerPoolEntry": {"player": espn_player(10, "Jalen Hurts", 1, 21, 22.4)}},
             {"lineupSlotId": 20, "playerPoolEntry": {"player": espn_player(11, "Bench RB", 2, 9, 6.1, "OUT")}},
         ]}},
        {"id": 2, "location": "Team", "nickname": "Rival", "owners": ["{XYZ}"],
         "roster": {"entries": []}},
    ],
    "schedule": [{"matchupPeriodId": 7, "home": {"teamId": 1}, "away": {"teamId": 3}},
                 {"matchupPeriodId": 8, "home": {"teamId": 2}, "away": {"teamId": 1}}],
}


def espn_fake(url, headers=None, **_):
    if "kona_player_info" in url:
        assert json.loads(headers["X-Fantasy-Filter"])["players"]["limit"] == espn.FREE_AGENT_POOL
        return {"players": [{"player": espn_player(20, "FA WR", 3, 34, 9.0, owned=40.2)}]}
    return ESPN_LEAGUE


class EspnTest(unittest.TestCase):
    def test_fetch_finds_team_by_swid(self):
        with mock.patch.object(espn, "get_json", side_effect=espn_fake):
            [lg] = espn.fetch(999, 2026, espn_s2="s2", swid="{abc}")
        self.assertEqual(lg["team_name"], "Mine")
        self.assertEqual(lg["points_per_reception"], 0.5)
        self.assertEqual(lg["roster"][0], {**lg["roster"][0], "name": "Jalen Hurts", "position": "QB",
                                           "team": "PHI", "projected_points": 22.4, "slot": "starter"})
        self.assertEqual(lg["roster"][1]["injury_status"], "OUT")
        self.assertEqual(lg["roster"][1]["slot"], "bench")
        self.assertEqual(lg["opponent"]["team_name"], "Team Rival")
        self.assertEqual(lg["free_agents"][0]["team"], "HOU")
        self.assertEqual(lg["best_projected_free_agents"]["WR"][0]["name"], "FA WR")

    def test_needs_team_id_without_swid(self):
        with mock.patch.object(espn, "get_json", side_effect=espn_fake):
            with self.assertRaisesRegex(RuntimeError, "team_id"):
                espn.fetch(999, 2026)


if __name__ == "__main__":
    unittest.main()
