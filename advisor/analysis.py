"""Strategy layer on a fetched league: matchup odds, playoff odds, trade ideas, stashes.

Everything here is an estimate built from Sleeper projections and this season's
weekly scores. The report should present the numbers as estimates.
"""

import itertools
import math
import random
from statistics import NormalDist, mean, median, pvariance

from . import espn_scoring, nfl

PHI = NormalDist().cdf

# Prior spread of weekly scores relative to the projection, by position. Blended with
# each player's own week-to-week variance this season (see Context.value).
CV = {"QB": 0.35, "RB": 0.55, "WR": 0.6, "TE": 0.7, "K": 0.45, "DEF": 0.6, "DL": 0.6, "LB": 0.5, "DB": 0.55}
PRIOR_GAMES = 3

ELIGIBLE = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"}, "DEF": {"DEF"},
    "DL": {"DL"}, "LB": {"LB"}, "DB": {"DB"},
    "WRRB_FLEX": {"RB", "WR"}, "REC_FLEX": {"WR", "TE"}, "FLEX": {"RB", "WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"}, "IDP_FLEX": {"DL", "LB", "DB"},
}
# Any injury tag at all: such players are kept out of trade ideas on both sides. Trading for
# or away a hurt player is a judgment call the news has to make, not the projections.
def injured(p):
    return bool(p.get("injury_status")) or p.get("slot") == "IR"


OUT_STATUSES = {"Out", "IR", "PUP", "Sus", "Suspended", "OUT", "INJURY_RESERVE", "SUSPENSION"}
SIM_TEAM_SD_FLOOR = 18.0
# This week's stats change as games finish, so don't trust a cached copy for long.
CURRENT_STATS_MAX_AGE = 15 * 60
LOCK_BONUS = 1e6
# Trades where the other team loses up to this many points per week still get listed (flagged
# win_win=False): managers often judge by player value, not lineup math.
NEAR_FAIR = 0.5


# Sleeper lists defensive players by exact spot (DT, DE, CB, S...); lineup slots use DL/LB/DB.
POSITION_ALIASES = {"D/ST": "DEF", "DT": "DL", "DE": "DL", "NT": "DL", "EDGE": "DL",
                    "OLB": "LB", "ILB": "LB", "MLB": "LB", "CB": "DB", "S": "DB", "SS": "DB", "FS": "DB"}


def pos(p):
    return POSITION_ALIASES.get(p.get("position"), p.get("position"))


def _played(row):
    """True if a weekly stat line shows the player (or team defense) actually played."""
    stats = (row or {}).get("stats") or {}
    # Rows without "gp" are players who were active but didn't record a snap.
    return bool(row) and (stats.get("gp", 0) > 0 or "pts_allow" in stats)


def _brief(p, **extra):
    return {"name": p["name"], "position": pos(p), "team": p.get("team"), **extra}


class Values:
    __slots__ = ("week", "sd", "ros", "playoff", "playoff_by_week", "by_week", "locked")

    def __init__(self, week, sd, ros, playoff, playoff_by_week, by_week, locked=False):
        self.week, self.sd, self.ros, self.playoff, self.playoff_by_week = week, sd, ros, playoff, playoff_by_week
        self.by_week = by_week  # {week: projected points} for this week through the fantasy playoffs
        self.locked = locked  # his game this week is over; week is then his actual score


class Context:
    """Per-league projection lookups. Sleeper leagues use their own scoring; ESPN uses half-PPR."""

    def __init__(self, league, players):
        self.league = league
        self.players = players
        self.week = league["week"]
        self.reg_end = league["regular_season_end"]
        self.playoff_weeks = league["playoff_weeks"]
        self.scoring = league.get("scoring_settings")
        # ESPN leagues: score Sleeper's stat lines with the league's own ESPN rules.
        self.espn_rules = espn_scoring.rules(league["espn_scoring_items"]) if league.get("espn_scoring_items") else None
        season = league["season"]
        last = max([self.reg_end] + self.playoff_weeks)
        self.proj = {w: nfl.weekly("projections", season, w) for w in range(self.week, last + 1)}
        self.hist = [nfl.weekly("stats", season, w) for w in range(1, self.week)]
        # This week's games so far. Anyone whose team has played is locked: he can't be
        # moved into or out of a lineup, and his week value is what he actually scored.
        self.current = nfl.weekly("stats", season, self.week, max_age=CURRENT_STATS_MAX_AGE)
        self.played_teams = {r.get("team") for r in self.current.values() if r.get("team") and _played(r)}
        self.index = nfl.NameIndex(players)
        self.future_weeks = list(range(self.week + 1, self.reg_end + 1)) or [self.week]
        self._cache = {}

    def sid(self, p):
        return p.get("sleeper_id") or self.index.lookup(p["name"], p.get("position"), p.get("team"))

    def score(self, sid, row):
        """Fantasy points for one Sleeper stat line under this league's scoring."""
        if self.espn_rules is not None:
            is_dst = self.players.get(sid, {}).get("position") == "DEF"
            return espn_scoring.points(row, self.espn_rules, is_dst=is_dst)
        return nfl.points(row, self.scoring)

    def proj_points(self, sid, week):
        return self.score(sid, self.proj.get(week, {}).get(sid)) or 0.0

    def locked(self, p):
        return nfl.TEAM_ALIASES.get(p.get("team"), p.get("team")) in self.played_teams

    def actual_points(self, sid):
        return self.score(sid, self.current.get(sid)) or 0.0

    def season_ppg(self, p):
        """Points per game played this season, in this league's scoring."""
        sid = self.sid(p)
        games = self.history(sid) if sid else []
        return mean(games) if games else 0.0

    def replacement(self, position):
        """Best rest-of-season value among unrostered players at a position: what's free on waivers."""
        if not hasattr(self, "_replacement"):
            if "rostered_sleeper_ids" in self.league:
                rostered = set(self.league["rostered_sleeper_ids"])
            else:
                rostered = {self.sid(q) for t in self.league["teams"] for q in t["roster"]}
            best = {}
            for sid in set().union(*(self.proj[w].keys() for w in self.future_weeks)):
                info = self.players.get(sid, {})
                pos_ = info.get("position")
                if sid in rostered or not info.get("team") or not pos_:
                    continue
                v = mean(self.proj_points(sid, w) for w in self.future_weeks)
                best[pos_] = max(best.get(pos_, 0.0), v)
            self._replacement = best
        return self._replacement.get("DEF" if position == "D/ST" else position, 0.0)

    def market_value(self, p):
        """What other managers will think a player is worth: half season so far, half projection,
        measured above what's free on waivers at his position (so spare QBs are worth little)."""
        v = self.value(p)
        return max(0.0, 0.5 * self.season_ppg(p) + 0.5 * v.ros - self.replacement(pos(p)))

    def history(self, sid):
        out = []
        for wk in self.hist:
            row = wk.get(sid)
            if _played(row):
                pts = self.score(sid, row)
                if pts is not None:
                    out.append(pts)
        return out

    def value(self, p):
        key = (p.get("id"), p.get("name"))
        if key in self._cache:
            return self._cache[key]
        sid = self.sid(p)
        locked = self.locked(p)
        if locked:
            # Prefer the platform's own score: ESPN scores QBs and defenses differently from Sleeper.
            if p.get("actual_points") is not None:
                week = p["actual_points"]
            else:
                week = self.actual_points(sid) if sid else 0.0
        elif p.get("injury_status") in OUT_STATUSES:
            week = 0.0
        elif p.get("projected_points") is not None:
            week = p["projected_points"]  # the platform's own number for this week
        else:
            week = self.proj_points(sid, self.week) if sid else 0.0
        by_week = {w: (self.proj_points(sid, w) if sid else 0.0) for w in self.proj}
        by_week[self.week] = week
        future = self.future_weeks
        ros = mean(by_week[w] for w in future)
        playoff_by_week = [(w, round(by_week[w], 1), (self.proj.get(w, {}).get(sid) or {}).get("opponent"))
                           for w in self.playoff_weeks]
        playoff = mean(by_week[w] for w in self.playoff_weeks) if self.playoff_weeks else 0.0
        hist = self.history(sid) if sid else []
        prior = CV.get(pos(p), 0.6) * max(week, ros, 3.0)
        if len(hist) >= 2:
            sd = math.sqrt((len(hist) * pvariance(hist) + PRIOR_GAMES * prior ** 2) / (len(hist) + PRIOR_GAMES))
        else:
            sd = prior
        v = Values(week, sd, ros, playoff, playoff_by_week, by_week, locked)
        self._cache[key] = v
        return v


def best_lineup(roster, slots, value):
    """Greedy fill, most restrictive slots first; optimal for standard slot structures."""
    pool = [p for p in roster if p.get("slot") != "IR"]
    used, lineup = set(), []
    for slot in sorted((s for s in slots if s in ELIGIBLE), key=lambda s: len(ELIGIBLE[s])):
        cands = [p for p in pool if id(p) not in used and pos(p) in ELIGIBLE[slot]]
        best = max(cands, key=value, default=None)
        if best is not None:
            used.add(id(best))
        lineup.append((slot, best))
    return lineup


def _total(lineup, fn):
    return sum(fn(p) for _, p in lineup if p is not None)


def week_lineup(ctx, roster, slots, fn):
    """This week's best lineup, keeping anyone whose game is over exactly where he is."""
    # Locked starters stay in; locked bench players can't come in.
    pool = [p for p in roster if not ctx.value(p).locked or p.get("slot") == "starter"]
    return best_lineup(pool, slots, lambda p: fn(p) + (LOCK_BONUS if ctx.value(p).locked else 0.0))


def weekly_total(ctx, roster, slots, weeks):
    """Average points per week if the best lineup is set every week (byes get covered by the bench)."""
    if not weeks:
        return 0.0
    total = 0.0
    for w in weeks:
        fn = lambda p, w=w: ctx.value(p).by_week.get(w, 0.0)
        total += _total(best_lineup(roster, slots, fn), fn)
    return total / len(weeks)


def matchup(ctx, my_roster, opp_roster, slots):
    v = ctx.value
    week_val = lambda p: v(p).week
    var = lambda p: 0.0 if v(p).locked else v(p).sd ** 2  # a finished game has no uncertainty left

    opp = week_lineup(ctx, opp_roster, slots, week_val)
    opp_mu, opp_var = _total(opp, week_val), _total(opp, var)
    opp_current = sum(week_val(p) for p in opp_roster if p.get("slot") == "starter")

    def win_prob(lineup):
        mu, s2 = _total(lineup, week_val), _total(lineup, var)
        return PHI((mu - opp_mu) / math.sqrt(max(s2 + opp_var, 1.0))), mu, s2

    mean_lineup = week_lineup(ctx, my_roster, slots, week_val)
    p_mean, mu_mean, _ = win_prob(mean_lineup)
    best = (p_mean, 0.0, mean_lineup)
    # Shift every player by z standard deviations: z > 0 favors upside, z < 0 favors a safe floor.
    # Keep whichever lineup gives the best chance of beating this opponent.
    for z in (-1.0, -0.6, -0.3, 0.3, 0.6, 1.0):
        lineup = week_lineup(ctx, my_roster, slots, lambda p, z=z: v(p).week + z * v(p).sd)
        p_win = win_prob(lineup)[0]
        if p_win > best[0] + 0.005:
            best = (p_win, z, lineup)
    p_best, z_best, chosen = best

    mean_ids = {id(p) for _, p in mean_lineup if p}
    chosen_ids = {id(p) for _, p in chosen if p}
    swaps = {
        "in": [_brief(p, projected=round(v(p).week, 1), ceiling=round(v(p).week + 1.28 * v(p).sd, 1))
               for _, p in chosen if p and id(p) not in mean_ids],
        "out": [_brief(p, projected=round(v(p).week, 1), ceiling=round(v(p).week + 1.28 * v(p).sd, 1))
                for _, p in mean_lineup if p and id(p) not in chosen_ids],
    }
    mode = "underdog" if p_mean < 0.4 else "favorite" if p_mean > 0.65 else "toss-up"
    return {
        "win_probability": round(p_best, 3),
        "win_probability_max_projection": round(p_mean, 3),
        "mode": mode,
        "strategy": "upside" if z_best > 0 else "floor" if z_best < 0 else "max projection",
        "my_projection": round(mu_mean, 1),
        "opponent_projection_best_lineup": round(opp_mu, 1),
        "opponent_projection_current_lineup": round(opp_current, 1),
        "upside_or_floor_swaps": swaps,
        "lineup": [{"slot": s, **_brief(p, projected=round(v(p).week, 1), sd=round(v(p).sd, 1),
                                        locked=v(p).locked)}
                   for s, p in chosen if p],
        "note": "Players with locked=true already played this week; projected is their actual score.",
    }


def _team_strengths(ctx, league, slots):
    out = {}
    for t in league["teams"]:
        wk_lineup = week_lineup(ctx, t["roster"], slots, lambda p: ctx.value(p).week)
        games = t["wins"] + t["losses"] + t["ties"]
        ros = weekly_total(ctx, t["roster"], slots, ctx.future_weeks)  # re-picked weekly, so byes are covered
        ppg = t["points_for"] / games if games else ros
        sd = max(math.sqrt(_total(wk_lineup, lambda p: ctx.value(p).sd ** 2)), SIM_TEAM_SD_FLOOR)
        # Half projections for the current roster, half what the team has actually scored.
        out[t["team_id"]] = {"future": 0.5 * ros + 0.5 * ppg,
                             "this_week": _total(wk_lineup, lambda p: ctx.value(p).week),
                             "sd": sd, "ros_lineup": ros, "ppg": ppg}
    return out


def playoff_odds(ctx, league, slots, sims=10000, seed=8):
    strength = _team_strengths(ctx, league, slots)
    me = league["team_id"]
    rng = random.Random(seed)
    made = {t["team_id"]: 0 for t in league["teams"]}
    seeds = {t["team_id"]: 0 for t in league["teams"]}
    cond = {"win": [0, 0], "loss": [0, 0]}
    n_playoff = league["playoff_teams"]
    for _ in range(sims):
        wins = {t["team_id"]: t["wins"] + 0.5 * t["ties"] for t in league["teams"]}
        pf = {t["team_id"]: t["points_for"] for t in league["teams"]}
        my_result = None
        for g in league["schedule"]:
            a, b = g["teams"]
            key = "this_week" if g["week"] == ctx.week else "future"
            sa = rng.gauss(strength[a][key], strength[a]["sd"])
            sb = rng.gauss(strength[b][key], strength[b]["sd"])
            pf[a] += sa
            pf[b] += sb
            wins[a if sa > sb else b] += 1
            if g["week"] == ctx.week and me in (a, b):
                my_result = "win" if (sa > sb) == (a == me) else "loss"
        ranked = sorted(wins, key=lambda t: (wins[t], pf[t]), reverse=True)
        for i, t in enumerate(ranked):
            if i < n_playoff:
                made[t] += 1
            seeds[t] += i + 1
        if my_result:
            cond[my_result][0] += 1
            cond[my_result][1] += me in ranked[:n_playoff]
    teams = {t["team_id"]: t for t in league["teams"]}
    table = sorted(({
        "team_name": teams[tid]["team_name"],
        "record": f"{teams[tid]['wins']}-{teams[tid]['losses']}",
        "points_for": teams[tid]["points_for"],
        "playoff_odds": round(made[tid] / sims, 3),
        "average_finish": round(seeds[tid] / sims, 1),
        "strength_per_week": round(strength[tid]["future"], 1),
        "is_me": tid == me,
    } for tid in made), key=lambda r: -r["playoff_odds"])
    rate = lambda c: round(c[1] / c[0], 3) if c[0] else None
    return {
        "playoff_spots": n_playoff,
        "my_odds": round(made[me] / sims, 3),
        "my_odds_if_win_this_week": rate(cond["win"]),
        "my_odds_if_lose_this_week": rate(cond["loss"]),
        "standings": table,
    }


def positional_ranks(ctx, league, slots):
    """Where my starters rank at each slot type, re-picking the best lineup each remaining week."""
    weeks = ctx.future_weeks
    by_team = {}
    for t in league["teams"]:
        groups = {}
        for w in weeks:
            fn = lambda p, w=w: ctx.value(p).by_week.get(w, 0.0)
            for slot, p in best_lineup(t["roster"], slots, fn):
                groups[slot] = groups.get(slot, 0.0) + (fn(p) if p else 0.0) / len(weeks)
        by_team[t["team_id"]] = groups
    me = league["team_id"]
    order = list(dict.fromkeys(slots))
    out = []
    for slot in sorted(by_team[me], key=order.index):
        values = sorted((g.get(slot, 0.0) for g in by_team.values()), reverse=True)
        mine = by_team[me][slot]
        out.append({"slot": slot, "mine": round(mine, 1), "league_avg": round(mean(values), 1),
                    "rank": values.index(mine) + 1, "teams": len(values)})
    return out


def trade_ideas(ctx, league, slots, max_ideas=6, per_team=2, shortlist=400):
    """Deals of up to two players each way that improve both teams' rest-of-season starting lineups."""
    rv = lambda p: ctx.value(p).ros
    weeks = ctx.future_weeks
    total = lambda roster: weekly_total(ctx, roster, slots, weeks)
    playoff_total = lambda roster: weekly_total(ctx, roster, slots, ctx.playoff_weeks)
    teams = {t["team_id"]: t for t in league["teams"]}
    me = teams[league["team_id"]]
    base_me, base_me_playoff = total(me["roster"]), playoff_total(me["roster"])
    mine = [p for p in me["roster"] if not injured(p) and rv(p) > 0]
    gives = [(p,) for p in mine] + list(itertools.combinations(mine, 2))

    # Cheap screen first (ROS-average lineups), then score the shortlist week by week.
    quick = lambda roster: _total(best_lineup(roster, slots, rv), rv)
    q_me = quick(me["roster"])
    candidates = []
    for t in teams.values():
        if t is me:
            continue
        q_t = quick(t["roster"])
        theirs = [p for p in t["roster"] if not injured(p) and rv(p) > 0]
        for get in [(p,) for p in theirs] + list(itertools.combinations(theirs, 2)):
            get_ids = {id(p) for p in get}
            get_val = sum(rv(p) for p in get)
            for give in gives:
                give_ids = {id(p) for p in give}
                # Raw value they receive vs give: a bit of a discount is fine if their lineup improves.
                if sum(rv(p) for p in give) < 0.85 * get_val:
                    continue
                # Managers trade on season stats and reputation, not our projections: skip offers
                # where what they'd get is clearly worth less on the market than what they give up.
                if sum(ctx.market_value(p) for p in give) < 0.9 * sum(ctx.market_value(p) for p in get):
                    continue
                kept = [p for p in me["roster"] if id(p) not in give_ids]
                g_me = quick(kept + list(get)) - q_me
                if g_me < 0.5:
                    continue
                # Skip throw-ins: each player I receive has to add something on his own.
                if len(get) == 2 and g_me - max(quick(kept + [p]) - q_me for p in get) < 0.5:
                    continue
                g_t = quick([p for p in t["roster"] if id(p) not in get_ids] + list(give)) - q_t
                if g_t >= -NEAR_FAIR:
                    candidates.append((g_me + 0.5 * g_t, t, get, give))
    candidates.sort(key=lambda c: -c[0])
    # Keep a few versions of each target package so near-duplicates don't crowd the shortlist.
    versions, trimmed = {}, []
    for c in candidates:
        key = (c[1]["team_id"], frozenset(id(p) for p in c[2]))
        versions[key] = versions.get(key, 0) + 1
        if versions[key] <= 5:
            trimmed.append(c)
    candidates = trimmed

    ideas, base_t_cache = [], {}
    for _, t, get, give in candidates[:shortlist]:
        give_ids, get_ids = {id(p) for p in give}, {id(p) for p in get}
        new_me = [p for p in me["roster"] if id(p) not in give_ids] + list(get)
        gain_me = total(new_me) - base_me
        if gain_me < 1.0:
            continue
        if t["team_id"] not in base_t_cache:
            base_t_cache[t["team_id"]] = total(t["roster"])
        gain_t = total([p for p in t["roster"] if id(p) not in get_ids] + list(give)) - base_t_cache[t["team_id"]]
        # They can't come out meaningfully behind in lineup terms, or they won't say yes.
        if gain_t < -NEAR_FAIR:
            continue
        ideas.append({
            "partner": t["team_name"],
            "give": [_brief(p, ros=round(rv(p), 1), playoff=round(ctx.value(p).playoff, 1)) for p in give],
            "get": [_brief(p, ros=round(rv(p), 1), playoff=round(ctx.value(p).playoff, 1)) for p in get],
            "my_gain_per_week": round(gain_me, 1),
            "their_gain_per_week": round(gain_t, 1),
            "my_playoff_gain_per_week": round(playoff_total(new_me) - base_me_playoff, 1),
            "overpay": round(sum(rv(p) for p in give) - sum(rv(p) for p in get), 1),
            "market_give": round(sum(ctx.market_value(p) for p in give), 1),
            "market_get": round(sum(ctx.market_value(p) for p in get), 1),
            "roster_change": len(get) - len(give),  # +1 means I'd need to drop a bench player
            "win_win": gain_t >= 0,
        })
    # Win-win deals first; within each group, prefer ones where I'm not overpaying to make the math work.
    ideas.sort(key=lambda i: (not i["win_win"],
                              -(i["my_gain_per_week"] + 0.5 * i["their_gain_per_week"] - 0.25 * max(i["overpay"], 0))))
    picked, count, seen = [], {}, set()
    for idea in ideas:
        key = (idea["partner"], frozenset(p["name"] for p in idea["get"]))
        if key in seen or count.get(idea["partner"], 0) >= per_team:
            continue
        seen.add(key)
        count[idea["partner"]] = count.get(idea["partner"], 0) + 1
        picked.append(idea)
        if len(picked) >= max_ideas:
            break
    return picked


def stash_candidates(ctx, league, slots, n=6, per_position=25):
    """Unrostered players ranked by how much they'd add to my lineup, now and in the fantasy playoffs."""
    if "rostered_sleeper_ids" in league:
        rostered = set(league["rostered_sleeper_ids"])
    else:
        rostered = {ctx.sid(p) for t in league["teams"] for p in t["roster"]}
    usable = set().union(*(ELIGIBLE[s] for s in slots if s in ELIGIBLE))
    future = ctx.future_weeks
    pool = {}
    for sid in set().union(*(ctx.proj[w].keys() for w in future)):
        p = ctx.players.get(sid, {})
        if sid in rostered or p.get("position") not in usable or not p.get("team"):
            continue
        cand = {"id": sid, "sleeper_id": sid, "name": p.get("full_name") or sid, "position": p.get("position"),
                "team": p.get("team"), "injury_status": p.get("injury_status"), "slot": "bench"}
        if ctx.value(cand).ros > 0:
            pool.setdefault(cand["position"], []).append(cand)
    # Only the best few at each position can matter; skip scoring hundreds of fringe players.
    shortlist = [c for cands in pool.values()
                 for c in sorted(cands, key=lambda c: -ctx.value(c).ros)[:per_position]]

    me = next(t for t in league["teams"] if t["team_id"] == league["team_id"])
    base = weekly_total(ctx, me["roster"], slots, future)
    base_playoff = weekly_total(ctx, me["roster"], slots, ctx.playoff_weeks)
    rows = []
    for c in shortlist:
        v = ctx.value(c)
        gain = weekly_total(ctx, me["roster"] + [c], slots, future) - base
        gain_playoff = weekly_total(ctx, me["roster"] + [c], slots, ctx.playoff_weeks) - base_playoff
        if gain <= 0.1 and gain_playoff <= 0.1:
            continue
        rows.append({"name": c["name"], "position": c["position"], "team": c["team"],
                     "injury_status": c["injury_status"], "this_week": round(v.by_week.get(ctx.week, 0.0), 1),
                     "ros": round(v.ros, 1), "playoff": round(v.playoff, 1),
                     "lineup_gain_per_week": round(gain, 1), "playoff_lineup_gain_per_week": round(gain_playoff, 1)})
    by_gain, per_pos = [], {}
    for r in sorted(rows, key=lambda r: -r["lineup_gain_per_week"]):
        if per_pos.get(r["position"], 0) < 2:  # keep the list varied: two per position at most
            per_pos[r["position"]] = per_pos.get(r["position"], 0) + 1
            by_gain.append(r)
        if len(by_gain) >= n:
            break
    names = {r["name"] for r in by_gain}
    playoff_specials = sorted((r for r in rows if r["name"] not in names),
                              key=lambda r: -r["playoff_lineup_gain_per_week"])[:3]
    return {"best_rest_of_season": by_gain, "playoff_specials": playoff_specials}


def playoff_schedule(ctx, league, slots):
    """My likely starters' projected points in the fantasy playoff weeks vs their usual level."""
    me = next(t for t in league["teams"] if t["team_id"] == league["team_id"])
    lineup = best_lineup(me["roster"], slots, lambda p: ctx.value(p).ros)
    out = []
    for slot, p in lineup:
        if not p:
            continue
        v = ctx.value(p)
        played = [v.by_week[w] for w in ctx.future_weeks if v.by_week.get(w, 0) > 0]
        usual = mean(played) if played else 0.0  # typical week, ignoring byes
        if usual <= 0:
            continue
        out.append(_brief(p, slot=slot, usual=round(usual, 1), playoff=round(v.playoff, 1),
                          change_pct=round(100 * (v.playoff - usual) / usual),
                          weeks=[{"week": w, "projected": pts, "opponent": opp} for w, pts, opp in v.playoff_by_week]))
    return sorted(out, key=lambda r: r["change_pct"])


def _box(stats):
    """The handful of box-score numbers that explain a fantasy score."""
    snp, tm = stats.get("off_snp"), stats.get("tm_off_snp")
    return {
        "snap_share": round(snp / tm, 2) if snp and tm else None,
        "targets": stats.get("rec_tgt", 0), "receptions": stats.get("rec", 0),
        "carries": stats.get("rush_att", 0), "pass_attempts": stats.get("pass_att", 0),
        "yards": round(stats.get("pass_yd", 0) + stats.get("rush_yd", 0) + stats.get("rec_yd", 0)),
        "touchdowns": round(stats.get("pass_td", 0) + stats.get("rush_td", 0) + stats.get("rec_td", 0), 1),
        "interceptions": stats.get("pass_int", 0),
    }


def usage_history(ctx, sid, before_week, n=4):
    """A player's last n games before a week: snaps, volume, touchdowns, points and the score."""
    out = []
    for w in range(before_week - 1, 0, -1):
        stats_rows = ctx.hist[w - 1] if w - 1 < len(ctx.hist) else nfl.weekly("stats", ctx.league["season"], w)
        row = stats_rows.get(sid)
        if not _played(row):
            continue
        box = _box(row["stats"])
        out.append({"week": w, "points": round(ctx.score(sid, row) or 0.0, 1),
                    "snap_share": box["snap_share"], "targets": box["targets"], "carries": box["carries"],
                    "touchdowns": box["touchdowns"], "game": game_context(ctx, sid, w, stats_rows)})
        if len(out) >= n:
            break
    return out[::-1]


def game_context(ctx, sid, week, stats_rows=None):
    """Final score from the player's team's point of view, for game-script reasoning."""
    team = ctx.players.get(sid, {}).get("team")
    proj_rows = ctx.proj.get(week) or nfl.weekly("projections", ctx.league["season"], week)
    opp = (proj_rows.get(sid) or proj_rows.get(team) or {}).get("opponent")
    if not team or not opp:
        return None
    stats_rows = stats_rows if stats_rows is not None else nfl.weekly("stats", ctx.league["season"], week)
    # A team defense's points allowed is the other team's score.
    scored = ((stats_rows.get(opp) or {}).get("stats") or {}).get("pts_allow")
    allowed = ((stats_rows.get(team) or {}).get("stats") or {}).get("pts_allow")
    if scored is None or allowed is None:
        return {"opponent": opp}
    return {"opponent": opp, "team_score": int(scored), "opponent_score": int(allowed),
            "result": "W" if scored > allowed else "L" if scored < allowed else "T"}


# Who competes with whom for the same touches.
ROLE_GROUP = {"RB": ("RB",), "WR": ("WR", "TE"), "TE": ("TE", "WR"), "QB": ("QB",)}


def role_competition(ctx, sid, week, stats_rows=None, n=4):
    """Teammates fighting for the same touches, with their usage that week and injury status."""
    me = ctx.players.get(sid, {})
    team, group = me.get("team"), ROLE_GROUP.get(me.get("position"))
    if not team or not group:
        return []
    stats_rows = stats_rows if stats_rows is not None else nfl.weekly("stats", ctx.league["season"], week)
    out = []
    for pid, p in ctx.players.items():
        if pid == sid or p.get("team") != team or p.get("position") not in group:
            continue
        row = stats_rows.get(pid)
        box = _box(row["stats"]) if _played(row) else None
        if box is None and not p.get("injury_status") and (p.get("depth_chart_order") or 99) > 3:
            continue  # deep backups who didn't play aren't competition worth listing
        out.append({"name": p.get("full_name") or pid, "position": p.get("position"),
                    "injury_status": p.get("injury_status"), "depth_chart_order": p.get("depth_chart_order"),
                    "played": box is not None,
                    "snap_share": box["snap_share"] if box else None,
                    "targets": box["targets"] if box else 0, "carries": box["carries"] if box else 0,
                    "points": round(ctx.score(pid, row) or 0.0, 1) if box else 0.0})
    out.sort(key=lambda c: -((c["snap_share"] or 0) + (c["targets"] + c["carries"]) / 100))
    return out[:n]


def match_review(ctx, league, n=6):
    """Biggest over- and under-performers vs projection in the latest week with games played.

    Uses the current week if any of its games are done (Monday), otherwise last week (Tuesday on).
    Projections and actuals both come from Sleeper, scored with this league's own rules.
    """
    if ctx.played_teams:
        week, opp_id = ctx.week, (league.get("opponent") or {}).get("team_id")
        stats_rows, teams_done = ctx.current, ctx.played_teams
    else:
        prev = league.get("previous_matchup")
        if not prev:
            return None
        week, opp_id = prev["week"], prev["opponent_id"]
        stats_rows, teams_done = nfl.weekly("stats", league["season"], week), None
    proj_rows = nfl.weekly("projections", league["season"], week)

    teams = {t["team_id"]: t for t in league["teams"]}
    prev = league.get("previous_matchup") or {}
    groups = []
    for side, tid in (("me", league["team_id"]),):  # my players only; opponents' outliers don't drive my moves
        if tid not in teams:
            continue
        key = "my" if side == "me" else "opponent"
        if week != ctx.week and prev.get(f"{key}_players"):
            starters = set(prev.get(f"{key}_starters") or [])
            members = [(sid, sid in starters) for sid in prev[f"{key}_players"]]
        else:
            members = [(ctx.sid(p), p.get("slot") == "starter") for p in teams[tid]["roster"]]
        groups.append((side, teams[tid]["team_name"], members))

    rows = []
    for side, team_name, members in groups:
        for sid, started in members:
            if not sid:
                continue
            info = ctx.players.get(sid, {})
            proj = ctx.score(sid, proj_rows.get(sid))
            row = stats_rows.get(sid)
            if teams_done is not None and info.get("team") not in teams_done:
                continue  # hasn't played yet this week
            actual = ctx.score(sid, row) if _played(row) else 0.0
            if proj is None or (proj < 3 and actual < 3):
                continue
            stats, pstats = (row or {}).get("stats") or {}, (proj_rows.get(sid) or {}).get("stats") or {}
            history = usage_history(ctx, sid, week)
            # Median of his recent games played, so one game cut short by injury doesn't skew it.
            shares = [h["snap_share"] for h in history if h["snap_share"] is not None]
            box, pbox = _box(stats), _box(pstats)
            reasons = []
            if box["touchdowns"] - pbox["touchdowns"] >= 1:
                reasons.append("touchdowns above expectation (likely won't repeat)")
            elif pbox["touchdowns"] >= 0.5 and box["touchdowns"] == 0:
                reasons.append("no touchdown")
            usual = round(median(shares), 2) if shares else None
            if usual is not None and box["snap_share"] is not None and abs(box["snap_share"] - usual) >= 0.15:
                change = (f"snap share {'up' if box['snap_share'] > usual else 'down'} "
                          f"({usual:.0%} before, {box['snap_share']:.0%} this week)")
                # One earlier game (say, a first week back from injury) is too thin to call it.
                reasons.append(change + (": possible role change" if len(shares) >= 2
                                         else ": unconfirmed, only one earlier game to compare"))
            touches = box["targets"] + box["carries"]
            ptouches = pbox["targets"] + pbox["carries"]
            game = game_context(ctx, sid, week, stats_rows)
            if game and "team_score" in game:
                margin = game["team_score"] - game["opponent_score"]
                if abs(margin) >= 14:
                    reasons.append(f"lopsided game ({game['result']} {game['team_score']}-{game['opponent_score']}): "
                                   "game script likely shaped usage")
            if (usual is not None and box["snap_share"] is not None and abs(box["snap_share"] - usual) < 0.15
                    and ptouches - touches >= 4):
                reasons.append(f"snap share steady (~{usual:.0%}) but few touches: likely game-specific, not a role change")
            if touches - ptouches >= 5:
                reasons.append(f"more volume ({touches} targets+carries vs {ptouches:.0f} projected)")
            elif ptouches - touches >= 5:
                reasons.append(f"less volume ({touches} targets+carries vs {ptouches:.0f} projected)")
            if not _played(row):
                reasons.append("didn't play")
            elif not reasons and abs(actual - proj) >= 5:
                reasons.append("efficient on normal volume" if actual > proj else "inefficient on normal volume")
            rows.append({
                "side": side, "team_name": team_name, "name": info.get("full_name") or sid,
                "position": info.get("position"), "nfl_team": info.get("team"), "started": started,
                "projected": round(proj, 1), "actual": round(actual, 1), "diff": round(actual - proj, 1),
                "box": box, "projected_box": {k: (round(v, 1) if isinstance(v, float) else v) for k, v in pbox.items()},
                "usual_snap_share": usual, "reasons": reasons, "game": game,
                "competition": role_competition(ctx, sid, week, stats_rows),
                "history": history,
            })
    rows.sort(key=lambda r: -r["diff"])
    return {
        "week": week,
        "complete": not (week == ctx.week and any(
            (r.get("team") not in ctx.played_teams) for r in [ctx.players.get(sid, {}) for _, _, m in groups
                                                                for sid, _ in m if sid])),
        "over": [r for r in rows if r["diff"] > 0][:n],
        "under": [r for r in rows if r["diff"] < 0][::-1][:n],
    }


def _mark_locked(ctx, league):
    """Flag every listed player whose game this week is already over."""
    groups = [t["roster"] for t in league["teams"]] + [league.get("free_agents") or [],
                                                      league.get("free_agent_pool") or []]
    groups += list((league.get("best_projected_free_agents") or {}).values())
    for group in groups:
        for p in group:
            v = ctx.value(p)
            p["locked"] = v.locked
            if v.locked:
                p["points_this_week"] = round(v.week, 1)


def analyze(league, players):
    ctx = Context(league, players)
    slots = league["roster_positions"]
    _mark_locked(ctx, league)
    me = next(t for t in league["teams"] if t["team_id"] == league["team_id"])
    result = {
        "playoffs": playoff_odds(ctx, league, slots),
        "positional_ranks": positional_ranks(ctx, league, slots),
        "trade_ideas": trade_ideas(ctx, league, slots),
        "stashes": stash_candidates(ctx, league, slots),
        "playoff_schedule": playoff_schedule(ctx, league, slots),
        "match_review": match_review(ctx, league),
        "notes": [
            "Win and playoff odds are estimates: projections plus each player's week-to-week spread this season.",
            "Rest-of-season (ROS) values are average projected points per week through the regular season, byes count as 0.",
            "Players with locked=true have already played this week: they can't be moved into or out of a "
            "lineup, and points_this_week is their actual score.",
        ],
        "teams_already_played": sorted(t for t in ctx.played_teams if t),
    }
    if league.get("opponent"):
        opp = next(t for t in league["teams"] if t["team_id"] == league["opponent"]["team_id"])
        result["matchup"] = matchup(ctx, me["roster"], opp["roster"], slots)
    # Depth-chart competition for my players and stash candidates, from the latest week with games.
    latest = ctx.week if ctx.played_teams else ctx.week - 1
    if latest >= 1:
        rows = ctx.current if latest == ctx.week else nfl.weekly("stats", league["season"], latest)
        for p in me["roster"]:
            sid = ctx.sid(p)
            if sid:
                p["competition"] = role_competition(ctx, sid, latest, rows)
                info = ctx.players.get(sid, {})
                if info.get("injury_status") or info.get("injury_body_part"):
                    p["injury_detail"] = {"status": info.get("injury_status"),
                                          "body_part": info.get("injury_body_part"),
                                          "notes": info.get("injury_notes")}
        for group in result["stashes"].values():
            for r in group:
                sid = ctx.index.lookup(r["name"], r["position"], r["team"])
                if sid:
                    r["competition"] = role_competition(ctx, sid, latest, rows)
        result["competition_week"] = latest
    return result
