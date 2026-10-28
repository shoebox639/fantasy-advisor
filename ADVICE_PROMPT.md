# Weekly fantasy advice run

This is the prompt a scheduled session follows.

1. Run `python3 fetch.py`. If it reports errors (blocked host, expired ESPN
   cookies), put them at the top of the report and continue with the leagues
   that did load. Don't fill in any missing data with guesses.
2. For every player who is a starter, on the bench, an opponent's starter, or
   one of the top free agents, search the web for news from the last 7 days:
   injury designations and practice reports, depth-chart and role changes,
   suspensions, weather for outdoor games, and Vegas lines/implied totals.
   Cite each source.
3. Before recommending pickups, compare `best_projected_free_agents` (the
   top-projected unrostered players at each position) against the current
   starters, not just the trending/most-rostered lists. A free agent who
   outprojects a starter must be mentioned, with why or why not to add him.
4. For each league, write:
   - **Start/sit:** the best legal lineup for this league's roster positions
     and scoring (PPR value), and a one-line reason for each change from the
     current lineup.
   - **Waivers:** up to 3 adds with suggested drops (and a FAAB bid range if the
     league has a budget).
   - **Watch list:** players whose status could change before kickoff, with
     when to check again (e.g. Friday injury report, 90-min inactives).
   - **Trade idea** (optional): only when the roster has a clear surplus/need.
5. Publish the report as an artifact page (reuse the same artifact URL each
   week) and send a push notification with the one-line headline for each
   league.
