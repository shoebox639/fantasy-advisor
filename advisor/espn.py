"""ESPN fantasy API (unofficial). Private leagues need the espn_s2 and SWID cookies."""

import json

from .http import get_json

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
SLOTS = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K",
         20: "bench", 21: "IR", 23: "FLEX"}
PRO_TEAMS = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET",
    9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA", 16: "MIN",
    17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC",
    25: "SF", 26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}


def _projection(player, week):
    # statSourceId 1 = projected, 0 = actual; statSplitTypeId 1 = single scoring period.
    for s in player.get("stats") or []:
        if s.get("scoringPeriodId") == week and s.get("statSourceId") == 1:
            return round(s.get("appliedTotal", 0), 1)
    return None


def _player(player, week, slot_id=None):
    slot = SLOTS.get(slot_id) if slot_id is not None else None
    return {
        "id": player.get("id"),
        "name": player.get("fullName"),
        "position": POSITIONS.get(player.get("defaultPositionId"), str(player.get("defaultPositionId"))),
        "team": PRO_TEAMS.get(player.get("proTeamId"), str(player.get("proTeamId"))),
        "injury_status": player.get("injuryStatus") if player.get("injuryStatus") != "ACTIVE" else None,
        "projected_points": _projection(player, week),
        "percent_rostered": round((player.get("ownership") or {}).get("percentOwned", 0), 1),
        "slot": None if slot is None else ("starter" if slot not in ("bench", "IR") else slot),
        "lineup_slot": slot,
    }


def _roster(team, week):
    out = []
    for e in (team.get("roster") or {}).get("entries") or []:
        out.append(_player(e["playerPoolEntry"]["player"], week, e.get("lineupSlotId")))
    out.sort(key=lambda p: (p["slot"] != "starter", p["slot"] == "IR"))
    return out


def _team_name(team):
    return team.get("name") or f"{team.get('location', '')} {team.get('nickname', '')}".strip()


def fetch(league_id, season, team_id=None, espn_s2=None, swid=None, free_agent_limit=25):
    cookies = {"espn_s2": espn_s2, "SWID": swid} if espn_s2 and swid else None
    url = f"{BASE}/seasons/{season}/segments/0/leagues/{league_id}"
    try:
        data = get_json(url + "?view=mTeam&view=mRoster&view=mMatchup&view=mSettings&view=mStatus",
                        cookies=cookies)
    except RuntimeError as e:
        if "HTTP 401" in str(e):
            raise RuntimeError(f"ESPN league {league_id} is private: set ESPN_S2 and ESPN_SWID") from e
        raise

    week = data.get("scoringPeriodId") or (data.get("status") or {}).get("currentMatchupPeriod")
    matchup_period = (data.get("status") or {}).get("currentMatchupPeriod", week)
    teams = {t["id"]: t for t in data.get("teams") or []}

    if team_id is None and swid:
        team_id = next((t["id"] for t in teams.values()
                        if swid.upper() in [o.upper() for o in t.get("owners") or []]), None)
    if team_id is None:
        raise RuntimeError(f"ESPN league {league_id}: set team_id in config (or provide ESPN_SWID)")
    mine = teams[int(team_id)]

    opponent = None
    for m in data.get("schedule") or []:
        if m.get("matchupPeriodId") != matchup_period:
            continue
        home, away = (m.get("home") or {}).get("teamId"), (m.get("away") or {}).get("teamId")
        if mine["id"] in (home, away):
            opp_id = away if home == mine["id"] else home
            if opp_id in teams:
                opponent = {"team_name": _team_name(teams[opp_id]), "roster": _roster(teams[opp_id], week)}
            break

    fa_filter = {"players": {
        "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
        "filterSlotIds": {"value": [0, 2, 4, 6, 16, 17]},
        "limit": free_agent_limit,
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
    }}
    fa_data = get_json(f"{url}?view=kona_player_info&scoringPeriodId={week}",
                       headers={"X-Fantasy-Filter": json.dumps(fa_filter)}, cookies=cookies)
    free_agents = [_player(p["player"], week) for p in fa_data.get("players") or []]

    settings = data.get("settings") or {}
    rec = (mine.get("record") or {}).get("overall") or {}
    scoring_items = (settings.get("scoringSettings") or {}).get("scoringItems") or []
    ppr = next((i.get("points") for i in scoring_items if i.get("statId") == 53), 0)  # 53 = reception

    return [{
        "platform": "espn",
        "league_id": str(league_id),
        "league_name": settings.get("name"),
        "season": str(season),
        "week": week,
        "points_per_reception": ppr,
        "team_name": _team_name(mine),
        "record": f"{rec.get('wins', 0)}-{rec.get('losses', 0)}" + (f"-{rec['ties']}" if rec.get("ties") else ""),
        "waiver_position": mine.get("waiverRank"),
        "waiver_budget_left": None,
        "roster": _roster(mine, week),
        "opponent": opponent,
        "free_agents": free_agents,
        "free_agents_note": "Most-rostered free agents/waiver players in this league.",
    }]
