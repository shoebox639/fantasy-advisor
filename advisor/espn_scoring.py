"""Score Sleeper stat lines (actual or projected) with an ESPN league's own rules.

ESPN publishes each league's rules as scoringItems keyed by its stat ids. Sleeper
publishes the stats. This maps one onto the other, so future-week projections for
an ESPN league use that league's scoring, not generic half-PPR.
"""

# ESPN stat id -> Sleeper stat key(s), scored per unit.
UNIT_STATS = {
    3: ("pass_yd",), 4: ("pass_td",), 19: ("pass_2pt",), 20: ("pass_int",),
    24: ("rush_yd",), 25: ("rush_td",), 26: ("rush_2pt",),
    42: ("rec_yd",), 43: ("rec_td",), 44: ("rec_2pt",), 53: ("rec",),
    63: ("fum_rec_td",), 72: ("fum_lost",),
    # Kicking
    74: ("fgm_50p",), 77: ("fgm_40_49",), 80: ("fgm_0_19", "fgm_20_29", "fgm_30_39"),
    198: ("fgm_50_59",), 201: ("fgm_60p",), 85: ("fgmiss",), 86: ("xpm",), 88: ("xpmiss",),
    # Team defense / special teams
    95: ("int",), 96: ("fum_rec", "def_st_fum_rec"), 97: ("blk_kick",), 98: ("safe",), 99: ("sack",),
}
# Defense and special-teams touchdowns are scored from Sleeper's totals below, because
# its breakdown fields are sometimes missing and sometimes duplicated (pr_td, def_pr_td, st_td).
DEF_TD_ID, ST_TD_ID = 103, 101

# Points-allowed and yards-allowed tiers: (ESPN stat id, low, high), inclusive.
POINTS_ALLOWED = [(89, 0, 0), (90, 1, 6), (91, 7, 13), (92, 14, 17), (121, 18, 21), (122, 22, 27),
                  (123, 28, 34), (124, 35, 45), (125, 46, 999)]
YARDS_ALLOWED = [(127, 0, 99), (128, 0, 99), (129, 100, 199), (130, 200, 299), (131, 300, 349),
                 (132, 350, 399), (133, 400, 449), (134, 450, 499), (135, 500, 549), (136, 550, 9999)]

DST_SLOT = "16"


def rules(scoring_items):
    """{stat_id: (points for players, points for D/ST)} from an ESPN league's scoringItems."""
    out = {}
    for item in scoring_items or []:
        base = item.get("points", 0) or 0
        dst = (item.get("pointsOverrides") or {}).get(DST_SLOT, base)
        out[item["statId"]] = (base, dst)
    return out


def _tier(value, tiers, table):
    for stat_id, low, high in tiers:
        if low <= value <= high and stat_id in table:
            return table[stat_id]
    return 0.0


def points(row, league_rules, is_dst=False):
    stats = (row or {}).get("stats") or {}
    if not stats:
        return None
    col = 1 if is_dst else 0
    table = {sid: pts[col] for sid, pts in league_rules.items()}
    total = 0.0
    for stat_id, keys in UNIT_STATS.items():
        if table.get(stat_id):
            total += table[stat_id] * sum(stats.get(k, 0) for k in keys)
    if is_dst:
        def_tds = stats.get("def_td", stats.get("pass_int_td", 0) + stats.get("def_fum_td", 0))
        st_tds = stats.get("def_st_td", stats.get("st_td", 0))
        total += table.get(DEF_TD_ID, 0) * def_tds + table.get(ST_TD_ID, 0) * st_tds
        # Projections give a mean; scoring the mean's tier is an approximation of the expected tier score.
        if "pts_allow" in stats:
            total += _tier(round(stats["pts_allow"]), POINTS_ALLOWED, table)
        if "yds_allow" in stats:
            total += _tier(round(stats["yds_allow"]), YARDS_ALLOWED, table)
    return total
