"""League-independent NFL data from Sleeper: weekly projections and actual stats for every player.

Both leagues are valued with these (ESPN players are matched by name), so
rest-of-season and playoff numbers are on one scale.
"""

import json
import os
import re
import time

from .http import get_json

BASE = "https://api.sleeper.app"
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB")
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache")
PROJECTION_MAX_AGE = 6 * 3600
STATS_MAX_AGE = 24 * 3600

# ESPN team abbreviations that differ from Sleeper's.
TEAM_ALIASES = {"WSH": "WAS"}


def _cached(name, url, max_age):
    path = os.path.abspath(os.path.join(CACHE_DIR, name))
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        with open(path) as f:
            return json.load(f)
    data = get_json(url, timeout=90) or []
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def weekly(kind, season, week):
    """Raw stat lines for one week, keyed by player id. kind is "projections" or "stats"."""
    # Unofficial endpoints (not under /v1); callers should survive an empty result.
    url = (f"{BASE}/{kind}/nfl/{season}/{week}?season_type=regular"
           + "".join(f"&position[]={p}" for p in POSITIONS))
    max_age = PROJECTION_MAX_AGE if kind == "projections" else STATS_MAX_AGE
    try:
        rows = _cached(f"{kind}_{season}_{week}.json", url, max_age)
    except RuntimeError:
        return {}
    return {r["player_id"]: r for r in rows}


def points(row, scoring=None, fallback_key="pts_half_ppr"):
    """Fantasy points for one stat line, using a league's scoring settings when given."""
    stats = (row or {}).get("stats") or {}
    if not stats:
        return None
    if scoring:
        total = sum(stats.get(k, 0) * v for k, v in scoring.items() if isinstance(v, (int, float)))
        # Team defenses' points-allowed buckets aren't in projections; trust Sleeper's own total there.
        if total or fallback_key not in stats:
            return total
    return stats.get(fallback_key)


def _norm(name):
    name = re.sub(r"[.'’\-]", "", (name or "").lower())
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", name)
    return " ".join(name.split())


class NameIndex:
    """Maps another platform's players (name, position, team) to Sleeper player ids."""

    def __init__(self, players):
        self.by_name_pos = {}
        self.by_name = {}
        for pid, p in players.items():
            if not p.get("team") and p.get("position") != "DEF":
                continue
            key = _norm(p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}")
            self.by_name_pos.setdefault((key, p.get("position")), []).append(pid)
            self.by_name.setdefault(key, []).append(pid)

    def lookup(self, name, position, team):
        if position in ("D/ST", "DEF"):
            return TEAM_ALIASES.get(team, team)
        key = _norm(name)
        for ids in (self.by_name_pos.get((key, position)), self.by_name.get(key)):
            if ids and len(ids) == 1:
                return ids[0]
        return None
