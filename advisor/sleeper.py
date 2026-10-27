"""Sleeper public API: https://docs.sleeper.com (no auth needed)."""

import json
import os
import time

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


def _player(pid, players, starters=()):
    p = players.get(pid, {})
    name = p.get("full_name") or " ".join(filter(None, [p.get("first_name"), p.get("last_name")])) or pid
    return {
        "id": pid,
        "name": name,
        "position": p.get("position"),
        "team": p.get("team"),
        "injury_status": p.get("injury_status"),
        "injury_note": p.get("injury_body_part"),
        "depth_chart_order": p.get("depth_chart_order"),
        "starter": pid in starters,
    }


def _roster(roster, players):
    starters = set(roster.get("starters") or [])
    reserve = set(roster.get("reserve") or [])
    out = []
    for pid in roster.get("players") or []:
        entry = _player(pid, players, starters)
        entry["slot"] = "starter" if pid in starters else ("IR" if pid in reserve else "bench")
        out.append(entry)
    out.sort(key=lambda e: (e["slot"] != "starter", e["slot"] == "IR", e["position"] or ""))
    return out


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

    results = []
    for lg in leagues:
        lid = lg["league_id"]
        rosters = get_json(f"{BASE}/league/{lid}/rosters")
        users = {u["user_id"]: u for u in get_json(f"{BASE}/league/{lid}/users")}
        matchups = get_json(f"{BASE}/league/{lid}/matchups/{week}") or []

        mine = next((r for r in rosters if r.get("owner_id") == user_id
                     or user_id in (r.get("co_owners") or [])), None)
        if mine is None:
            continue

        opponent = None
        my_m = next((m for m in matchups if m["roster_id"] == mine["roster_id"]), None)
        if my_m and my_m.get("matchup_id") is not None:
            opp_m = next((m for m in matchups if m.get("matchup_id") == my_m["matchup_id"]
                          and m["roster_id"] != mine["roster_id"]), None)
            if opp_m:
                opp_roster = next(r for r in rosters if r["roster_id"] == opp_m["roster_id"])
                owner = users.get(opp_roster.get("owner_id"), {})
                opponent = {
                    "team_name": (owner.get("metadata") or {}).get("team_name") or owner.get("display_name"),
                    "roster": _roster(opp_roster, players),
                }

        rostered = {pid for r in rosters for pid in (r.get("players") or [])}
        free_agents = []
        for t in trending:
            if t["player_id"] in rostered:
                continue
            entry = _player(t["player_id"], players)
            entry["trending_adds_48h"] = t["count"]
            free_agents.append(entry)
            if len(free_agents) >= free_agent_limit:
                break

        settings = mine.get("settings") or {}
        my_owner = users.get(user_id, {})
        results.append({
            "platform": "sleeper",
            "league_id": lid,
            "league_name": lg.get("name"),
            "season": season,
            "week": week,
            "points_per_reception": (lg.get("scoring_settings") or {}).get("rec", 0),
            "roster_positions": lg.get("roster_positions"),
            "team_name": (my_owner.get("metadata") or {}).get("team_name") or my_owner.get("display_name"),
            "record": f"{settings.get('wins', 0)}-{settings.get('losses', 0)}"
                      + (f"-{settings['ties']}" if settings.get("ties") else ""),
            "waiver_position": settings.get("waiver_position"),
            "waiver_budget_left": (lg.get("settings") or {}).get("waiver_budget", 0) - settings.get("waiver_budget_used", 0)
                                  if (lg.get("settings") or {}).get("waiver_type") == 2 else None,
            "roster": _roster(mine, players),
            "opponent": opponent,
            "free_agents": free_agents,
            "free_agents_note": "Unrostered players most added across Sleeper in the last 48h.",
        })
    return results
