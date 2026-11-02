# Fantasy advice run

This is the prompt a scheduled session follows. It runs with no memory of
earlier sessions, so everything it needs is here or in the repo.

Each league has its own report page. Always update these two pages; never
create new ones:

- True Bromance (Sleeper): https://claude.ai/artifact/A784KAyk3CYXZ7qJYydRpW
- Ardsley (ESPN): https://claude.ai/artifact/JLavHJrxv5hsX3yfdvs4rW

Each page links to the other near the top; keep those links.

## Steps

1. Run `python3 fetch.py`. If it reports errors (blocked host, expired ESPN
   cookie), put them at the top of the report and continue with the leagues
   that did load. Don't fill in missing data with guesses.
2. Prefer official numbers over computed ones. `scoreboard` in each league
   holds the platform's own live scores (and on ESPN its projected totals and
   win probability). Use those for "where the matchup stands" and win chance.
   Only use `analysis.matchup` for what the platform doesn't publish (Sleeper
   win chance, upside/floor swaps), and label it as our estimate.
3. Check which games are already over: `analysis.teams_already_played`, and
   `locked: true` on any player (with `points_this_week`, his actual score).
   A locked player can't be moved into or out of a lineup, and most leagues
   won't let you drop him until the week ends. Never recommend benching,
   starting, adding-to-start or dropping a locked player for this week, and
   never compare a locked player's old projection against anyone. Show locked
   starters in the lineup table with their actual score and a "played" tag.
   Waiver and trade ideas involving them are fine, framed as next week on.
4. Read `snapshot.json` and decide the recommendations from the numbers:
   lineup changes, pickups/drops (compare `best_projected_free_agents` and
   `analysis.stashes` against current starters, not just trending lists),
   and up to 3 trade ideas per league from `analysis.trade_ideas`.
5. News check, mandatory, with a budget. Every player named in a
   recommendation (both sides of each trade, each add and drop, each lineup
   change) and every starter with an injury tag gets checked for news from
   the last 7 days. Keep it to at most 12 web searches:
   - Start with 2 roundup searches: "NFL week N injury report fantasy" and
     "fantasy week N waiver wire". They usually cover most names.
   - Then targeted searches only for names the roundups didn't cover,
     grouping several players into one query where you can.
   - If news undercuts a recommendation (injury, lost role, suspension), drop
     it or flag it. Mark each recommendation "news checked" with its source,
     or "no recent news found".
6. Write the reports by updating each league's page:
   - Read each page first (Artifact `read` with its URL) and keep its layout
     and styling. Replace the content; don't redesign it. Each page covers
     only its own league, with its own sources list.
   - This week: win chance (`analysis.matchup`), moves, lineup
     table, watch list (with when to recheck: Friday report, inactives).
   - Season plan: playoff odds and this week's swing
     (`analysis.playoffs`), strengths and needs (`analysis.positional_ranks`),
     trade ideas with a one-line pitch, stash and stream
     (`analysis.stashes`), fantasy-playoff matchups
     (`analysis.playoff_schedule`).
   - Present odds and gains as estimates. Cite sources at the bottom.
   - Put the update time at the top, and a short "What changed since last
     run" line when something did (injury news, a new top pickup, odds moved
     5+ points).
   - Mark players on bye (no game this week) with a "Bye" tag instead of
     leaving a 0.0 unexplained.
7. Publish each page with the Artifact tool, `url` set to that league's page.
8. If a push-notification tool is available, send one line per league only
   when there's something to act on (a starter's status changed, a lineup
   change is needed, a waiver claim is worth making). Otherwise stay quiet.

Keep the run lean: don't re-read files you already have, don't run fetch.py
more than once, and don't explore the codebase beyond what these steps need.
