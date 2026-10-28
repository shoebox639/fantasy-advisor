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


def projections(season, week):
    """Sleeper's weekly projections (unofficial endpoint), keyed by player id."""
    # Not under /v1, and it has no documented contract, so a failure here shouldn't sink the fetch.
    url = (f"https://api.sleeper.app/projections/nfl/{season}/{week}?season_type=regular"
           + "".join(f"&position[]={p}" for p in ("QB", "RB", "WR", "TE", "K", "DEF")))
    try:
        return {r["player_id"]: r for r in get_json(url, timeout=60) or []}
    except RuntimeError:
        return {}


def _points_key(rec):
    return {0: "pts_std", 0.5: "pts_half_ppr"}.get(rec, "pts_ppr")


def _player(pid, players, starters=(), proj=None, points_key="pts_half_ppr"):
    p = players.get(pid, {})
    name = p.get("full_name") or " ".join(filter(None, [p.get("first_name"), p.get("last_name")])) or pid
    r = (proj or {}).get(pid) or {}
    pts = (r.get("stats") or {}).get(points_key)
    return {
        "id": pid,
        "name": name,
        "position": p.get("position"),
        "team": p.get("team"),
        "injury_status": p.get("injury_status"),
        "injury_note": p.get("injury_body_part"),
        "depth_chart_order": p.get("depth_chart_order"),
        "projected_points": round(pts, 1) if pts is not None else None,
        "opponent": r.get("opponent"),
        "starter": pid in starters,
    }


def _best_free_agents(proj, players, rostered, points_key, per_position=5):
    """Highest-projected unrostered players at each position, so no one gets missed."""
    by_pos = {}
    for pid in proj:
        if pid in rostered:
            continue
        entry = _player(pid, players, proj=proj, points_key=points_key)
        if entry["projected_points"]:
            by_pos.setdefault(entry["position"], []).append(entry)
    return {pos: sorted(entries, key=lambda e: -e["projected_points"])[:per_position]
            for pos, entries in by_pos.items()}


def _roster(roster, players, proj=None, points_key="pts_half_ppr"):
    starters = set(roster.get("starters") or [])
    reserve = set(roster.get("reserve") or [])
    out = []
    for pid in roster.get("players") or []:
        entry = _player(pid, players, starters, proj, points_key)
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
    proj = projections(season, week)

    results = []
    for lg in leagues:
        lid = lg["league_id"]
        rosters = get_json(f"{BASE}/league/{lid}/rosters")
        users = {u["user_id"]: u for u in get_json(f"{BASE}/league/{lid}/users")}
        matchups = get_json(f"{BASE}/league/{lid}/matchups/{week}") or []
        points_key = _points_key((lg.get("scoring_settings") or {}).get("rec", 0))

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
                    "roster": _roster(opp_roster, players, proj, points_key),
                }

        rostered = {pid for r in rosters for pid in (r.get("players") or [])}
        free_agents = []
        for t in trending:
            if t["player_id"] in rostered:
                continue
            entry = _player(t["player_id"], players, proj=proj, points_key=points_key)
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
            "roster": _roster(mine, players, proj, points_key),
            "opponent": opponent,
            "free_agents": free_agents,
            "free_agents_note": "Unrostered players most added across Sleeper in the last 48h.",
            "best_projected_free_agents": _best_free_agents(proj, players, rostered, points_key),
        })
    return results
