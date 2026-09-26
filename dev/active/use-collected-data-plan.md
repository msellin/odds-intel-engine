# #195 USE-THE-DATA-WE-COLLECT — plan (2026-09-27)

Parent row: PRIORITY_QUEUE.md #195. Owner: "do all of them, choose order yourself".

## Principle
Measure first, switch on only what the numbers support. Anything that changes settlement
or a price reference is pre-registered (rule + threshold written BEFORE the measurement).

## Workstreams (parallel, isolated git worktrees, each pushes to main after rebase)
| WS | Items | Direction | Touches | Risk |
|---|---|---|---|---|
| A | (d) Optibet live/ended stats + results from the listing; betRadarMatchId (Optibet) + sr_id (Tonybet) stored; Optibet betCount; (f) thin Tonybet O/U to .5 lines in odds_snapshots | 🤖👥 BOTH + 💼 | optibet_feed, tonybet_feed, migration | low — collection only |
| B | (a) corners/cards counts from book_match_results where AF match_stats is missing — agreement study on the overlap first, then settlement fallback behind a pre-registered agreement bar | 🤖👥 BOTH | settlement.py, results tables | HIGH — grades bets |
| C | (b) Tonybet fair close as reference for BTTS / cards / corners where Pinnacle is missing; (c) Betfair Exchange anchor for AH + goal lines — calibration vs outcomes first; wire into anchor.py only if it passes the pre-registered bar; no bot switched on here | 🤖👥 BOTH | anchor.py (shared with #191/#187!) | HIGH — reference prices |
| D | (e) observatory metric Tonybet-fair vs Pinnacle gap per market/day; (g) raw-archive retention | 👥 PICKS + 💼 | observatory_metrics, backup/prune | low |

## Risks
- Shared files with live sessions (#191 OWN bots → anchor.py; #187 AH bot). WS C keeps changes additive.
- smoke_test.py / PRIORITY_QUEUE.md rebase conflicts — agents do NOT edit PRIORITY_QUEUE; the coordinator does.
- Migrations: A=482, B=483, C=484, D=485 with distinct suffixes (duplicates legal).
