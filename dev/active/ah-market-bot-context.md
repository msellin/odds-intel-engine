Parent row: PRIORITY_QUEUE.md #187.

# AH market bot — context (update as you go)

## State
2026-09-26: started. Measured Pinnacle AH freshness (see plan). History usable from 2026-07-01 only.

## Key files
* `workers/jobs/settlement.py:381` `_r_asian_handicap` — selection string "home -1.25" (team + ITS OWN line);
  quarter lines graded full win/loss (bug). All 3 AH shadow bots are retired (bot_ah_home_fav,
  bot_ah_away_dog, bot_high_alignment), so the fix changes no live record.
* `odds_snapshots` AH rows: market 'asian_handicap', selection 'home'/'away', `handicap_line` = the HOME
  team's line on BOTH rows (e.g. Bet365 home 0.5 @1.95 / away 0.5 @1.85 = home +0.5 vs away −0.5).
* `bet_result` enum = won/lost/void/pending — no half outcomes; `bot_ledger` computes pnl from the result.
* Analysis scripts: `scripts/analysis/ah_market/`.

## Next steps
See ah-market-bot-tasks.md.
