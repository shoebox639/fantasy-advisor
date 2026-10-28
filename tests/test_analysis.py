"""Strategy math on a tiny made-up league; projections are faked, no network."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from advisor import analysis  # noqa: E402

SLOTS = ["QB", "RB", "WR", "FLEX", "BN", "BN"]
PLAYERS = {
    "q1": {"full_name": "Mine QB", "position": "QB", "team": "AAA"},
    "q2": {"full_name": "Spare QB", "position": "QB", "team": "BBB"},
    "r1": {"full_name": "Mine RB", "position": "RB", "team": "CCC"},
    "w1": {"full_name": "Mine WR", "position": "WR", "team": "DDD"},
    "q3": {"full_name": "Their QB", "position": "QB", "team": "EEE"},
    "r2": {"full_name": "Their RB", "position": "RB", "team": "FFF"},
    "r3": {"full_name": "Their RB2", "position": "RB", "team": "GGG"},
    "w2": {"full_name": "Their WR", "position": "WR", "team": "HHH"},
    "w3": {"full_name": "Their WR2", "position": "WR", "team": "JJJ"},
    "fa": {"full_name": "Free WR", "position": "WR", "team": "III"},
}
# Weekly projections (weeks 8-11; 10 is the last regular-season week, 11 the playoff).
PROJ = {
    "q1": 20, "q2": 18, "r1": 12, "w1": 10,
    "q3": 8, "r2": 16, "r3": 14, "w2": 11, "w3": 11,
    "fa": 9,
}


def fake_weekly(kind, season, week):
    if kind == "stats":
        return {}
    return {pid: {"player_id": pid, "stats": {"pts_half_ppr": pts}} for pid, pts in PROJ.items()}


def player(pid, slot="bench"):
    p = PLAYERS[pid]
    return {"id": pid, "sleeper_id": pid, "name": p["full_name"], "position": p["position"], "team": p["team"],
            "injury_status": None, "projected_points": PROJ[pid], "slot": slot}


def league():
    return {
        "week": 8, "season": "2026", "regular_season_end": 10, "playoff_weeks": [11], "playoff_teams": 1,
        "roster_positions": SLOTS, "team_id": 1,
        "teams": [
            {"team_id": 1, "team_name": "Me", "wins": 4, "losses": 3, "ties": 0, "points_for": 300,
             "roster": [player("q1"), player("q2"), player("r1"), player("w1")]},
            {"team_id": 2, "team_name": "Them", "wins": 4, "losses": 3, "ties": 0, "points_for": 300,
             "roster": [player("q3"), player("r2"), player("r3"), player("w2"), player("w3")]},
        ],
        "schedule": [{"week": w, "teams": [1, 2]} for w in (8, 9, 10)],
        "opponent": {"team_id": 2},
        "rostered_sleeper_ids": ["q1", "q2", "r1", "w1", "q3", "r2", "r3", "w2", "w3"],
    }


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(analysis.nfl, "weekly", side_effect=fake_weekly)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lg = league()
        self.ctx = analysis.Context(self.lg, PLAYERS)

    def test_best_lineup_fills_flex_with_best_leftover(self):
        roster = self.lg["teams"][1]["roster"]
        lineup = analysis.best_lineup(roster, SLOTS, lambda p: self.ctx.value(p).week)
        self.assertEqual({s: p["name"] for s, p in lineup},
                         {"QB": "Their QB", "RB": "Their RB", "WR": "Their WR", "FLEX": "Their RB2"})

    def test_ir_players_never_start(self):
        roster = [player("q1"), player("r1", slot="IR"), player("w1")]
        lineup = analysis.best_lineup(roster, SLOTS, lambda p: self.ctx.value(p).week)
        self.assertNotIn("Mine RB", [p["name"] for _, p in lineup if p])

    def test_matchup_odds_favor_higher_projection(self):
        me, them = self.lg["teams"][0]["roster"], self.lg["teams"][1]["roster"]
        m = analysis.matchup(self.ctx, me, them, SLOTS)
        # Me: 20 + 12 + 10 + flex 0 (only a QB left) = 42; them: 8 + 16 + 11 + 14 = 49.
        self.assertEqual(m["my_projection"], 42)
        self.assertEqual(m["opponent_projection_best_lineup"], 49)
        self.assertLess(m["win_probability"], 0.5)
        self.assertEqual(m["mode"], "underdog")

    def test_trade_moves_my_spare_qb_to_the_team_that_needs_one(self):
        ideas = analysis.trade_ideas(self.ctx, self.lg, SLOTS)
        self.assertTrue(ideas)
        best = ideas[0]
        self.assertIn("Spare QB", [p["name"] for p in best["give"]])
        self.assertTrue({p["name"] for p in best["get"]} <= {"Their RB", "Their RB2"})
        self.assertGreater(best["my_gain_per_week"], 0)
        self.assertGreater(best["their_gain_per_week"], 0)

    def test_stash_counts_lineup_gain_not_raw_points(self):
        stash = analysis.stash_candidates(self.ctx, self.lg, SLOTS)["best_rest_of_season"]
        self.assertEqual(stash[0]["name"], "Free WR")
        # Free WR fills my empty flex: +9 per week.
        self.assertEqual(stash[0]["lineup_gain_per_week"], 9)

    def test_playoff_odds_are_probabilities_and_condition_on_this_week(self):
        odds = analysis.playoff_odds(self.ctx, self.lg, SLOTS, sims=2000)
        self.assertEqual(len(odds["standings"]), 2)
        self.assertAlmostEqual(sum(r["playoff_odds"] for r in odds["standings"]), 1.0, places=2)
        self.assertGreater(odds["my_odds_if_win_this_week"], odds["my_odds_if_lose_this_week"])


if __name__ == "__main__":
    unittest.main()
