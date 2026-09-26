Parent row: PRIORITY_QUEUE.md #187.

# AH market bot — context (update as you go)

## State
2026-09-26: BUILT. Pooled rule passed discovery + holdout on independent-close CLV; bot_ah_sharp_v1 runs
EXPERIMENTAL (:18/:48). Next = owner's free/VIP call, then public labels. Backtest: `scripts/analysis/ah_market/`
(extract.py → backtest.py --phase discovery / holdout); outputs in data/models/_research/ah_market/ (gitignored),
holdout printout saved as holdout_output.txt there.

## Decisions made on the way (and why)
* Window 07-01 → 09-25: before July the AH history is ~1 snapshot/match from 4–5 books.
* Independent close needs ≥ 3 other books (AMENDMENT 1): AH has ~6–9 publishable books per line.
* Ladder guard (AMENDMENT 2): Betano quarter ladder broken on 9.4% of same-fetch triples.
* AF books only: Epicbet (direct feed) −8.1% CLV in the holdout and absent from discovery.
* Live pairs a book quote with Pinnacle's SAME fetch (backtest parity); rungs absent from Pinnacle's newest fetch
  are not priced but kept for the ladder check.
* Public price = the pick's own price (a higher refused quote on another book must not inflate the record).
* Quarter-line half results: result word stays won/lost, settle_fraction = 0.5 halves pnl and bot_ledger P&L.

## Key files
* `workers/jobs/settlement.py:381` `_r_asian_handicap` — selection string "home -1.25" = side + the HOME-perspective line (same as odds_snapshots.handicap_line; "away -1" = away +1);
  quarter lines graded full win/loss (bug). All 3 AH shadow bots are retired (bot_ah_home_fav,
  bot_ah_away_dog, bot_high_alignment), so the fix changes no live record.
* `odds_snapshots` AH rows: market 'asian_handicap', selection 'home'/'away', `handicap_line` = the HOME
  team's line on BOTH rows (e.g. Bet365 home 0.5 @1.95 / away 0.5 @1.85 = home +0.5 vs away −0.5).
* `bet_result` enum = won/lost/void/pending — no half outcomes; `bot_ledger` computes pnl from the result.
* Analysis scripts: `scripts/analysis/ah_market/`.

## Next steps
See ah-market-bot-tasks.md.
