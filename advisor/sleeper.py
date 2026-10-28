"""Sleeper public API: https://docs.sleeper.com (no auth needed)."""

import json
import math
import os
import time

from . import nfl
from .http import get_json

BASE = "https://api.sleeper.app/v1"
PLAYERS_CACHE = os.path.join(os.path.dirname(__file__), "..", "cache", "sleeper_players.json")
PLAYERS_MAX_AGE = 24 * 3600  # Sleeper asks callers to fetch the full player list at most daily.


def nfl_state():
    return get_json(f"{BASE}/state/nfl")


def load_players():
    path = os.path.abspath(PLAYERS_CACHE)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < PLAYERS_MAX_AGE:
        with open(path) as f:
            return json.load(f)
    players = get_json(f"{BASE}/players/nfl", timeout=120)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(players, f)
    return players


def projections(season, week):
    return nfl.weekly("projections", season, week)


def _player(pid, players, starters=(), proj=None, scoring=None):
    p = players.get(pid, {})
    name = p.get("full_name") or " ".join(filter(None, [p.get("first_name"), p.get("last_name")])) or pid
    r = (proj or {}).get(pid)
    pts = nfl.points(r, scoring)
    return {
        "id": pid,
        "sleeper_id": pid,
        "name": name,
        "position": p.get("position"),
        "team": p.get("team"),
        "injury_status": p.get("injury_status"),
        "injury_note": p.get("injury_body_part"),
        "depth_chart_order": p.get("depth_chart_order"),
        "projected_points": round(pts, 1) if pts is not None else None,
        "opponent": (r or {}).get("opponent"),
        "starter": pid in starters,
    }


def _best_free_agents(proj, players, rostered, scoring, per_position=5):
    """Highest-projected unrostered players at each position, so no one gets missed."""
    by_pos = {}
    for pid in proj:
        if pid in rostered:
            continue
        entry = _player(pid, players, proj=proj, scoring=scoring)
        if entry["projected_points"]:
            by_pos.setdefault(entry["position"], []).append(entry)
    return {pos: sorted(entries, key=lambda e: -e["projected_points"])[:per_position]
            for pos, entries in by_pos.items()}


def _roster(roster, players, proj=None, scoring=None):
    starters = set(roster.get("starters") or [])
    reserve = set(roster.get("reserve") or [])
    out = []
    for pid in roster.get("players") or []:
        entry = _player(pid, players, starters, proj, scoring)
        entry["slot"] = "starter" if pid in starters else ("IR" if pid in reserve else "bench")
        out.append(entry)
    out.sort(key=lambda e: (e["slot"] != "starter", e["slot"] == "IR", e["position"] or ""))
    return out


def _team_name(owner):
    return (owner.get("metadata") or {}).get("team_name") or owner.get("display_name")


def _points_for(settings):
    return settings.get("fpts", 0) + settings.get("fpts_decimal", 0) / 100


def fetch(username, season=None, league_ids=None, free_agent_limit=25):
    state = nfl_state()
    season = str(season or state["league_season"])
    week = state.get("display_week") or state.get("week")

    user = get_json(f"{BASE}/user/{username}")
    if not user:
        raise RuntimeError(f"Sleeper user '{username}' not found")
    user_id = user["user_id"]

    leagues = get_json(f"{BASE}/user/{user_id}/leagues/nfl/{season}") or []
    if league_ids:
        leagues = [lg for lg in leagues if lg["league_id"] in set(map(str, league_ids))]

    players = load_players()
    trending = get_json(f"{BASE}/players/nfl/trending/add?lookback_hours=48&limit=100")
    proj = projections(season, week)

    results = []
    for lg in leagues:
        lid = lg["league_id"]
        rosters = get_json(f"{BASE}/league/{lid}/rosters")
        users = {u["user_id"]: u for u in get_json(f"{BASE}/league/{lid}/users")}
        scoring = lg.get("scoring_settings") or {}
        lg_settings = lg.get("settings") or {}

        mine = next((r for r in rosters if r.get("owner_id") == user_id
                     or user_id in (r.get("co_owners") or [])), None)
        if mine is None:
            continue

        teams = []
        for r in rosters:
            s = r.get("settings") or {}
            owner = users.get(r.get("owner_id"), {})
            teams.append({
                "team_id": r["roster_id"],
                "team_name": _team_name(owner) or f"Team {r['roster_id']}",
                "wins": s.get("wins", 0), "losses": s.get("losses", 0), "ties": s.get("ties", 0),
                "points_for": round(_points_for(s), 2),
                "roster": _roster(r, players, proj, scoring),
            })
        by_id = {t["team_id"]: t for t in teams}

        playoff_start = lg_settings.get("playoff_week_start") or 15
        playoff_teams = lg_settings.get("playoff_teams") or 4
        schedule = []
        for wk in range(week, playoff_start):
            pairs = {}
            for m in get_json(f"{BASE}/league/{lid}/matchups/{wk}") or []:
                if m.get("matchup_id") is not None:
                    pairs.setdefault(m["matchup_id"], []).append(m["roster_id"])
            schedule += [{"week": wk, "teams": ids} for ids in pairs.values() if len(ids) == 2]

        opponent = None
        this_week = next((g for g in schedule if g["week"] == week and mine["roster_id"] in g["teams"]), None)
        if this_week:
            opp = by_id[next(t for t in this_week["teams"] if t != mine["roster_id"])]
            opponent = {"team_id": opp["team_id"], "team_name": opp["team_name"], "roster": opp["roster"]}

        rostered = {pid for r in rosters for pid in (r.get("players") or [])}
        free_agents = []
        for t in trending:
            if t["player_id"] in rostered:
                continue
            entry = _player(t["player_id"], players, proj=proj, scoring=scoring)
            entry["trending_adds_48h"] = t["count"]
            free_agents.append(entry)
            if len(free_agents) >= free_agent_limit:
                break

        settings = mine.get("settings") or {}
        me = by_id[mine["roster_id"]]
        results.append({
            "platform": "sleeper",
            "league_id": lid,
            "league_name": lg.get("name"),
            "season": season,
            "week": week,
            "points_per_reception": scoring.get("rec", 0),
            "scoring_settings": scoring,
            "roster_positions": lg.get("roster_positions"),
            "team_id": me["team_id"],
            "team_name": me["team_name"],
            "record": f"{me['wins']}-{me['losses']}" + (f"-{me['ties']}" if me["ties"] else ""),
            "waiver_position": settings.get("waiver_position"),
            "waiver_budget_left": lg_settings.get("waiver_budget", 0) - settings.get("waiver_budget_used", 0)
                                  if lg_settings.get("waiver_type") == 2 else None,
            "roster": me["roster"],
            "opponent": opponent,
            "teams": teams,
            "schedule": schedule,
            "regular_season_end": playoff_start - 1,
            "playoff_teams": playoff_teams,
            "playoff_weeks": list(range(playoff_start, playoff_start + math.ceil(math.log2(max(playoff_teams, 2))))),
            "rostered_sleeper_ids": sorted(rostered),
            "free_agents": free_agents,
            "free_agents_note": "Unrostered players most added across Sleeper in the last 48h.",
            "best_projected_free_agents": _best_free_agents(proj, players, rostered, scoring),
        })
    return results
