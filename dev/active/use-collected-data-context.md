# #195 context
- Source audit: collected-vs-used audit 2026-09-26 (summarised in the #195 row).
- Optibet capability audit 2026-09-26: live/ended events in the listing (`scoreboard.extraStats`,
  `matchingEventId`, status 5 = ended), `betRadarMatchId` on ~90%, `betCount` per event/game;
  skipped at optibet_feed.py ~line 172. Sample archive responses: scratchpad/ob/.
- Tonybet writers to reuse: tonybet_feed.run_live (book_live_stats), run_results (book_match_results).
- Settlement reads corners from match_stats only; cards_ou deliberately not settled.
- anchor.py reads exchange_quotes for 1x2 / over_under_25 / btts only (_EX_MARKETS).
## State
2026-09-27: workstreams A–D launched as isolated worktree agents.
