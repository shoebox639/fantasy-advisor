"""Strategy layer on a fetched league: matchup odds, playoff odds, trade ideas, stashes.

Everything here is an estimate built from Sleeper projections and this season's
weekly scores. The report should present the numbers as estimates.
"""

import itertools
import math
import random
from statistics import NormalDist, mean, pvariance

from . import nfl

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
OUT_STATUSES = {"Out", "IR", "PUP", "Sus", "Suspended", "OUT", "INJURY_RESERVE", "SUSPENSION"}
SIM_TEAM_SD_FLOOR = 18.0


def pos(p):
    return "DEF" if p.get("position") in ("D/ST", "DEF") else p.get("position")


def _brief(p, **extra):
    return {"name": p["name"], "position": pos(p), "team": p.get("team"), **extra}


class Values:
    __slots__ = ("week", "sd", "ros", "playoff", "playoff_by_week", "by_week")

    def __init__(self, week, sd, ros, playoff, playoff_by_week, by_week):
        self.week, self.sd, self.ros, self.playoff, self.playoff_by_week = week, sd, ros, playoff, playoff_by_week
        self.by_week = by_week  # {week: projected points} for this week through the fantasy playoffs


class Context:
    """Per-league projection lookups. Sleeper leagues use their own scoring; ESPN uses half-PPR."""

    def __init__(self, league, players):
        self.league = league
        self.players = players
        self.week = league["week"]
        self.reg_end = league["regular_season_end"]
        self.playoff_weeks = league["playoff_weeks"]
        self.scoring = league.get("scoring_settings")
        season = league["season"]
        last = max([self.reg_end] + self.playoff_weeks)
        self.proj = {w: nfl.weekly("projections", season, w) for w in range(self.week, last + 1)}
        self.hist = [nfl.weekly("stats", season, w) for w in range(1, self.week)]
        self.index = nfl.NameIndex(players)
        self.future_weeks = list(range(self.week + 1, self.reg_end + 1)) or [self.week]
        self._cache = {}

    def sid(self, p):
        return p.get("sleeper_id") or self.index.lookup(p["name"], p.get("position"), p.get("team"))

    def proj_points(self, sid, week):
        return nfl.points(self.proj.get(week, {}).get(sid), self.scoring) or 0.0

    def history(self, sid):
        out = []
        for wk in self.hist:
            row = wk.get(sid)
            stats = (row or {}).get("stats") or {}
            if row and stats.get("gp", 1):
                pts = nfl.points(row, self.scoring)
                if pts is not None:
                    out.append(pts)
        return out

    def value(self, p):
        key = (p.get("id"), p.get("name"))
        if key in self._cache:
            return self._cache[key]
        sid = self.sid(p)
        if p.get("injury_status") in OUT_STATUSES:
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
        v = Values(week, sd, ros, playoff, playoff_by_week, by_week)
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
    var = lambda p: v(p).sd ** 2

    opp = best_lineup(opp_roster, slots, week_val)
    opp_mu, opp_var = _total(opp, week_val), _total(opp, var)
    opp_current = sum(week_val(p) for p in opp_roster if p.get("slot") == "starter")

    def win_prob(lineup):
        mu, s2 = _total(lineup, week_val), _total(lineup, var)
        return PHI((mu - opp_mu) / math.sqrt(max(s2 + opp_var, 1.0))), mu, s2

    mean_lineup = best_lineup(my_roster, slots, week_val)
    p_mean, mu_mean, _ = win_prob(mean_lineup)
    best = (p_mean, 0.0, mean_lineup)
    # Shift every player by z standard deviations: z > 0 favors upside, z < 0 favors a safe floor.
    # Keep whichever lineup gives the best chance of beating this opponent.
    for z in (-1.0, -0.6, -0.3, 0.3, 0.6, 1.0):
        lineup = best_lineup(my_roster, slots, lambda p, z=z: v(p).week + z * v(p).sd)
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
        "lineup": [{"slot": s, **_brief(p, projected=round(v(p).week, 1), sd=round(v(p).sd, 1))}
                   for s, p in chosen if p],
    }


def _team_strengths(ctx, league, slots):
    out = {}
    for t in league["teams"]:
        ros_lineup = best_lineup(t["roster"], slots, lambda p: ctx.value(p).ros)
        wk_lineup = best_lineup(t["roster"], slots, lambda p: ctx.value(p).week)
        games = t["wins"] + t["losses"] + t["ties"]
        ros = _total(ros_lineup, lambda p: ctx.value(p).ros)
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
    """Where my starters rank at each slot type, by rest-of-season projection."""
    by_team = {}
    for t in league["teams"]:
        lineup = best_lineup(t["roster"], slots, lambda p: ctx.value(p).ros)
        groups = {}
        for slot, p in lineup:
            groups.setdefault(slot, 0.0)
            groups[slot] += ctx.value(p).ros if p else 0.0
        by_team[t["team_id"]] = groups
    me = league["team_id"]
    out = []
    for slot in sorted(by_team[me], key=lambda s: list(dict.fromkeys(slots)).index(s)):
        values = sorted((g.get(slot, 0.0) for g in by_team.values()), reverse=True)
        mine = by_team[me][slot]
        out.append({"slot": slot, "mine": round(mine, 1), "league_avg": round(mean(values), 1),
                    "rank": values.index(mine) + 1, "teams": len(values)})
    return out


def trade_ideas(ctx, league, slots, max_ideas=6, per_team=2, shortlist=10):
    """1-for-1 and 2-for-1 deals that improve both teams' rest-of-season starting lineups."""
    rv = lambda p: ctx.value(p).ros
    weeks = ctx.future_weeks
    total = lambda roster: weekly_total(ctx, roster, slots, weeks)
    playoff_total = lambda roster: weekly_total(ctx, roster, slots, ctx.playoff_weeks)
    teams = {t["team_id"]: t for t in league["teams"]}
    me = teams[league["team_id"]]
    base_me, base_me_playoff = total(me["roster"]), playoff_total(me["roster"])
    mine = [p for p in me["roster"] if p.get("slot") != "IR" and rv(p) > 0]
    gives = [(p,) for p in mine] + list(itertools.combinations(mine, 2))

    # Cheap screen first (ROS-average lineups), then score the shortlist week by week.
    quick = lambda roster: _total(best_lineup(roster, slots, rv), rv)
    q_me = quick(me["roster"])
    candidates = []
    for t in teams.values():
        if t is me:
            continue
        q_t = quick(t["roster"])
        for get in (p for p in t["roster"] if p.get("slot") != "IR" and rv(p) > 0):
            for give in gives:
                give_ids = {id(p) for p in give}
                g_me = quick([p for p in me["roster"] if id(p) not in give_ids] + [get]) - q_me
                if g_me < 0.5 or sum(rv(p) for p in give) < 0.9 * rv(get):
                    continue
                g_t = quick([p for p in t["roster"] if p is not get] + list(give)) - q_t
                if g_t >= 0:
                    candidates.append((g_me + 0.5 * g_t, t, get, give))
    candidates.sort(key=lambda c: -c[0])

    ideas, base_t_cache = [], {}
    for _, t, get, give in candidates[:shortlist * 6]:
        give_ids = {id(p) for p in give}
        new_me = [p for p in me["roster"] if id(p) not in give_ids] + [get]
        gain_me = total(new_me) - base_me
        if gain_me < 1.0:
            continue
        if t["team_id"] not in base_t_cache:
            base_t_cache[t["team_id"]] = total(t["roster"])
        gain_t = total([p for p in t["roster"] if p is not get] + list(give)) - base_t_cache[t["team_id"]]
        # They have to come out ahead in lineup terms too, or they won't say yes.
        if gain_t < 0.3:
            continue
        ideas.append({
            "partner": t["team_name"],
            "give": [_brief(p, ros=round(rv(p), 1), playoff=round(ctx.value(p).playoff, 1)) for p in give],
            "get": [_brief(get, ros=round(rv(get), 1), playoff=round(ctx.value(get).playoff, 1))],
            "my_gain_per_week": round(gain_me, 1),
            "their_gain_per_week": round(gain_t, 1),
            "my_playoff_gain_per_week": round(playoff_total(new_me) - base_me_playoff, 1),
            "overpay": round(sum(rv(p) for p in give) - rv(get), 1),
        })
    # Prefer deals where I'm not throwing in extra value just to make the math work.
    ideas.sort(key=lambda i: -(i["my_gain_per_week"] + 0.5 * i["their_gain_per_week"] - 0.25 * max(i["overpay"], 0)))
    picked, count, seen = [], {}, set()
    for idea in ideas:
        key = (idea["partner"], idea["get"][0]["name"])
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
        if v.ros <= 0:
            continue
        out.append(_brief(p, slot=slot, ros=round(v.ros, 1), playoff=round(v.playoff, 1),
                          change_pct=round(100 * (v.playoff - v.ros) / v.ros),
                          weeks=[{"week": w, "projected": pts, "opponent": opp} for w, pts, opp in v.playoff_by_week]))
    return sorted(out, key=lambda r: r["change_pct"])


def analyze(league, players):
    ctx = Context(league, players)
    slots = league["roster_positions"]
    me = next(t for t in league["teams"] if t["team_id"] == league["team_id"])
    result = {
        "playoffs": playoff_odds(ctx, league, slots),
        "positional_ranks": positional_ranks(ctx, league, slots),
        "trade_ideas": trade_ideas(ctx, league, slots),
        "stashes": stash_candidates(ctx, league, slots),
        "playoff_schedule": playoff_schedule(ctx, league, slots),
        "notes": [
            "Win and playoff odds are estimates: projections plus each player's week-to-week spread this season.",
            "Rest-of-season (ROS) values are average projected points per week through the regular season, byes count as 0.",
        ],
    }
    if league.get("opponent"):
        opp = next(t for t in league["teams"] if t["team_id"] == league["opponent"]["team_id"])
        result["matchup"] = matchup(ctx, me["roster"], opp["roster"], slots)
    return result
