"""ESPN fantasy API (unofficial). Private leagues need the espn_s2 and SWID cookies."""

import json
import math

from .http import get_json

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
SLOTS = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K",
         20: "bench", 21: "IR", 23: "FLEX"}
FREE_AGENT_POOL = 400

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


# ESPN lineup slot ids -> the slot names analysis.py understands.
LINEUP_SLOTS = {0: "QB", 2: "RB", 3: "WRRB_FLEX", 4: "WR", 5: "REC_FLEX", 6: "TE", 7: "SUPER_FLEX",
                16: "DEF", 17: "K", 23: "FLEX"}


def _roster_positions(settings):
    counts = (settings.get("rosterSettings") or {}).get("lineupSlotCounts") or {}
    out = []
    for slot_id, name in LINEUP_SLOTS.items():
        out += [name] * counts.get(str(slot_id), 0)
    return out


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
    raw_teams = {t["id"]: t for t in data.get("teams") or []}

    if team_id is None and swid:
        team_id = next((t["id"] for t in raw_teams.values()
                        if swid.upper() in [o.upper() for o in t.get("owners") or []]), None)
    if team_id is None:
        raise RuntimeError(f"ESPN league {league_id}: set team_id in config (or provide ESPN_SWID)")
    mine = raw_teams[int(team_id)]

    settings = data.get("settings") or {}
    budget = (settings.get("acquisitionSettings") or {}).get("acquisitionBudget")
    teams = []
    for t in raw_teams.values():
        rec = (t.get("record") or {}).get("overall") or {}
        teams.append({
            "team_id": t["id"],
            "team_name": _team_name(t),
            "wins": rec.get("wins", 0), "losses": rec.get("losses", 0), "ties": rec.get("ties", 0),
            "points_for": round(rec.get("pointsFor", 0), 2),
            "roster": _roster(t, week),
        })
    by_id = {t["team_id"]: t for t in teams}
    me = by_id[mine["id"]]

    # Matchup periods are single NFL weeks in this league type (playoffMatchupPeriodLength 1).
    schedule = []
    for m in data.get("schedule") or []:
        home, away = (m.get("home") or {}).get("teamId"), (m.get("away") or {}).get("teamId")
        if m.get("matchupPeriodId", 0) >= matchup_period and home in by_id and away in by_id:
            schedule.append({"week": m["matchupPeriodId"], "teams": [home, away]})

    opponent = None
    this_week = next((g for g in schedule if g["week"] == matchup_period and mine["id"] in g["teams"]), None)
    if this_week:
        opp = by_id[next(t for t in this_week["teams"] if t != mine["id"])]
        opponent = {"team_id": opp["team_id"], "team_name": opp["team_name"], "roster": opp["roster"]}

    fa_filter = {"players": {
        "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
        "filterSlotIds": {"value": [0, 2, 4, 6, 16, 17]},
        "limit": FREE_AGENT_POOL,
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
    }}
    fa_data = get_json(f"{url}?view=kona_player_info&scoringPeriodId={week}",
                       headers={"X-Fantasy-Filter": json.dumps(fa_filter)}, cookies=cookies)
    pool = [_player(p["player"], week) for p in fa_data.get("players") or []]
    free_agents = pool[:free_agent_limit]
    # Also rank the whole pool by projection, so a high-projected but little-owned player isn't missed.
    best = {}
    for p in pool:
        if p["projected_points"]:
            best.setdefault(p["position"], []).append(p)
    best = {pos: sorted(ps, key=lambda p: -p["projected_points"])[:5] for pos, ps in best.items()}

    sched = settings.get("scheduleSettings") or {}
    reg_end = sched.get("matchupPeriodCount") or 14
    playoff_teams = sched.get("playoffTeamCount") or 4
    scoring_items = (settings.get("scoringSettings") or {}).get("scoringItems") or []
    ppr = next((i.get("points") for i in scoring_items if i.get("statId") == 53), 0)  # 53 = reception

    return [{
        "platform": "espn",
        "league_id": str(league_id),
        "league_name": (settings.get("name") or "").strip(),
        "season": str(season),
        "week": week,
        "points_per_reception": ppr,
        "roster_positions": _roster_positions(settings),
        "team_id": me["team_id"],
        "team_name": me["team_name"],
        "record": f"{me['wins']}-{me['losses']}" + (f"-{me['ties']}" if me["ties"] else ""),
        "waiver_position": mine.get("waiverRank"),
        "waiver_budget_left": budget - (mine.get("transactionCounter") or {}).get("acquisitionBudgetSpent", 0)
                              if budget else None,
        "roster": me["roster"],
        "opponent": opponent,
        "teams": teams,
        "schedule": schedule,
        "regular_season_end": reg_end,
        "playoff_teams": playoff_teams,
        "playoff_weeks": list(range(reg_end + 1, reg_end + 1 + math.ceil(math.log2(max(playoff_teams, 2))))),
        "free_agents": free_agents,
        "free_agents_note": "Most-rostered free agents/waiver players in this league.",
        "free_agent_pool": pool,
        "best_projected_free_agents": best,
    }]
