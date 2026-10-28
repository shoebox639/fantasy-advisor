#!/usr/bin/env python3
"""Pull every configured roster into one snapshot for the advice step.

Usage: python3 fetch.py [--config config.json] [--out snapshot.json]

Writes the snapshot JSON and prints a readable summary. ESPN cookies come from
the ESPN_S2 and ESPN_SWID environment variables, never from the config file.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from advisor import analysis, espn, sleeper  # noqa: E402


def summarize(league):
    lines = [f"## {league['league_name']} ({league['platform']}) - week {league['week']}",
             f"Team: {league['team_name']}  Record: {league['record']}  PPR: {league['points_per_reception']}"]

    def fmt(p):
        bits = [f"{p['name']} ({p['position']}, {p['team']})"]
        if p.get("projected_points") is not None:
            bits.append(f"proj {p['projected_points']}")
        if p.get("injury_status"):
            bits.append(f"**{p['injury_status']}**")
        return "  ".join(bits)

    for slot in ("starter", "bench", "IR"):
        players = [p for p in league["roster"] if p["slot"] == slot]
        if players:
            lines.append({"starter": "\nStarters:", "bench": "\nBench:", "IR": "\nIR:"}[slot])
            lines += [f"- {fmt(p)}" for p in players]
    if league.get("opponent"):
        lines.append(f"\nOpponent: {league['opponent']['team_name']}")
    lines.append(f"\nTop free agents: " + ", ".join(fmt(p) for p in league["free_agents"][:10]))

    a = league.get("analysis") or {}
    if a.get("matchup"):
        m = a["matchup"]
        lines.append(f"\nWin chance: {m['win_probability']:.0%} ({m['my_projection']} vs "
                     f"{m['opponent_projection_best_lineup']}), {m['mode']}")
    if a.get("playoffs"):
        po = a["playoffs"]
        lines.append(f"Playoff odds: {po['my_odds']:.0%} (win this week: {po['my_odds_if_win_this_week']:.0%}, "
                     f"lose: {po['my_odds_if_lose_this_week']:.0%})")
    for t in a.get("trade_ideas", [])[:3]:
        lines.append(f"Trade idea with {t['partner']}: give " + " + ".join(p["name"] for p in t["give"])
                     + " for " + " + ".join(p["name"] for p in t["get"]) + f" (+{t['my_gain_per_week']}/wk for you, "
                     f"+{t['their_gain_per_week']}/wk for them)")
    return "\n".join(lines)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(here, "config.json"))
    ap.add_argument("--out", default=os.path.join(here, "snapshot.json"))
    args = ap.parse_args()

    with open(args.config) as f:
        config = json.load(f)

    leagues, errors = [], []
    sl = config.get("sleeper")
    if sl and sl.get("username"):
        try:
            leagues += sleeper.fetch(sl["username"], sl.get("season"), sl.get("league_ids"))
        except Exception as e:
            errors.append(f"sleeper: {e}")

    for lg in config.get("espn") or []:
        try:
            leagues += espn.fetch(lg["league_id"], lg.get("season") or datetime.date.today().year,
                                  lg.get("team_id"), os.environ.get("ESPN_S2"), os.environ.get("ESPN_SWID"))
        except Exception as e:
            errors.append(f"espn {lg.get('league_id')}: {e}")

    players = sleeper.load_players() if leagues else {}
    for lg in leagues:
        try:
            lg["analysis"] = analysis.analyze(lg, players)
        except Exception as e:
            errors.append(f"analysis {lg['league_name']}: {e}")

    snapshot = {
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "leagues": leagues,
        "errors": errors,
    }
    with open(args.out, "w") as f:
        json.dump(snapshot, f, indent=2)

    for lg in leagues:
        print(summarize(lg), end="\n\n")
    for err in errors:
        print(f"ERROR {err}", file=sys.stderr)
    print(f"Wrote {args.out}", file=sys.stderr)
    sys.exit(1 if errors and not leagues else 0)


if __name__ == "__main__":
    main()
