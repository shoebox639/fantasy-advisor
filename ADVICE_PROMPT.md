# Fantasy advice run

This is the prompt a scheduled session follows. It runs with no memory of
earlier sessions, so everything it needs is here or in the repo.

The report lives at https://claude.ai/artifact/A784KAyk3CYXZ7qJYydRpW. Always
update that page; never create a new one.

## Steps

1. Run `python3 fetch.py`. If it reports errors (blocked host, expired ESPN
   cookie), put them at the top of the report and continue with the leagues
   that did load. Don't fill in missing data with guesses.
2. Read `snapshot.json` and decide the recommendations from the numbers:
   lineup changes, pickups/drops (compare `best_projected_free_agents` and
   `analysis.stashes` against current starters, not just trending lists),
   and up to 3 trade ideas per league from `analysis.trade_ideas`.
3. News check, mandatory, with a budget. Every player named in a
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
4. Write the report by updating the page:
   - Read it first (Artifact `read` with the URL above) and keep its layout
     and styling. Replace the content; don't redesign it.
   - Per league, this week: win chance (`analysis.matchup`), moves, lineup
     table, watch list (with when to recheck: Friday report, inactives).
   - Per league, season plan: playoff odds and this week's swing
     (`analysis.playoffs`), strengths and needs (`analysis.positional_ranks`),
     trade ideas with a one-line pitch, stash and stream
     (`analysis.stashes`), fantasy-playoff matchups
     (`analysis.playoff_schedule`).
   - Present odds and gains as estimates. Cite sources at the bottom.
   - Put the update time at the top, and a short "What changed since last
     run" line when something did (injury news, a new top pickup, odds moved
     5+ points).
5. Publish with the Artifact tool using `url` set to the page above.
6. If a push-notification tool is available, send one line per league only
   when there's something to act on (a starter's status changed, a lineup
   change is needed, a waiver claim is worth making). Otherwise stay quiet.

Keep the run lean: don't re-read files you already have, don't run fetch.py
more than once, and don't explore the codebase beyond what these steps need.
