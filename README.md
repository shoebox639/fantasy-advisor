# fantasy-advisor

Pulls my Sleeper and ESPN rosters, matchups and free agents into one snapshot.
A scheduled Claude session then checks news and writes start/sit, waiver and
trade advice (see `ADVICE_PROMPT.md`).

## Setup

1. `cp config.example.json config.json` and fill in:
   - `sleeper.username`: every league you're in this season is included
     (narrow it with `league_ids`).
   - `espn`: one entry per league. `league_id` is in the league URL
     (`leagueId=...`). `team_id` is optional when `ESPN_SWID` is set.
2. Private ESPN leagues: copy the `espn_s2` and `SWID` cookies from a logged-in
   browser (DevTools > Application > Cookies > espn.com) into the environment
   secrets `ESPN_S2` and `ESPN_SWID`. The `espn_s2` cookie expires after
   roughly a year.
3. Allow these hosts in the environment's network settings:
   `api.sleeper.app`, `lm-api-reads.fantasy.espn.com`.

## Run

    python3 fetch.py               # writes snapshot.json and prints a summary
    python3 -m unittest discover -s tests

Python 3.9+ standard library only, no dependencies.
