"""ESPN rules applied to Sleeper stat lines."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from advisor import espn_scoring  # noqa: E402

ITEMS = [
    {"statId": 3, "points": 0.04}, {"statId": 4, "points": 4.0}, {"statId": 20, "points": -3.0},
    {"statId": 53, "points": 0.5}, {"statId": 42, "points": 0.1},
    {"statId": 99, "points": 0.0, "pointsOverrides": {"16": 1.0}},
    {"statId": 96, "points": 0.0, "pointsOverrides": {"16": 2.0}},
    {"statId": 103, "points": 6.0, "pointsOverrides": {"16": 6.0}},
    {"statId": 91, "points": 0.0, "pointsOverrides": {"16": 3.0}},
    {"statId": 129, "points": 0.0, "pointsOverrides": {"16": 3.0}},
]
RULES = espn_scoring.rules(ITEMS)


class EspnScoringTest(unittest.TestCase):
    def test_qb_interceptions_use_league_penalty(self):
        row = {"stats": {"pass_yd": 250, "pass_td": 2, "pass_int": 1}}
        self.assertAlmostEqual(espn_scoring.points(row, RULES), 10 + 8 - 3)

    def test_receiver_half_ppr(self):
        row = {"stats": {"rec": 6, "rec_yd": 80}}
        self.assertAlmostEqual(espn_scoring.points(row, RULES), 3 + 8)

    def test_dst_uses_overrides_tiers_and_special_teams_recoveries(self):
        row = {"stats": {"sack": 3, "fum_rec": 1, "def_st_fum_rec": 1, "def_td": 1,
                         "pts_allow": 10, "yds_allow": 150}}
        # 3 sacks + 2 recoveries x2 + TD 6 + 7-13 allowed (3) + 100-199 yards (3)
        self.assertAlmostEqual(espn_scoring.points(row, RULES, is_dst=True), 3 + 4 + 6 + 3 + 3)

    def test_empty_row_is_none(self):
        self.assertIsNone(espn_scoring.points({}, RULES))


if __name__ == "__main__":
    unittest.main()
