Parent: PRIORITY_QUEUE.md #101 EE-SWEEPERS-2026-09-23

# Tonybet sweeper — plan

Design input: `dev/active/tonybet-af-capability-map.md` (what Tonybet can supply vs AF).
Owner decisions (2026-09-23): build Tonybet first; collect everything AF-equivalent it
offers "and more"; live polling at 120 s (phase 2).

## Phase 1 — pre-match odds + fair probabilities (this build)
- `workers/automation/tonybet_feed.py`: `event/list` status 0, next 48 h, main board,
  `relations[] = odds, competitors`, ~6 requests per sweep, via the zone.ee Estonian exit
  (Tonybet serves several jurisdictions; a Finnish IP could get a different line).
- Match to DB fixtures with the production matcher (`fuzzy_match_event`), after
  appending " W" / age group to names from `competitors.gender/ageGroup` so the squad
  guard works (Tonybet names women's teams WITHOUT a marker). Skip side-flipped matches.
- Markets by Sportradar `vendorMarketId`: 1 → `1x2`; 18 → `over_under_XX` (.5 lines
  0.5–4.5 only, same as other books); 16 → `asian_handicap` (home-perspective `hcp`);
  29 → `btts`; 10 → `double_chance`; 11 → `draw_no_bet`.
- Store: `store_book_odds_snapshots('Tonybet', …)`; `record_book_events('Tonybet', …)`.
- **Fair probabilities** (Sportradar `probabilities` on each outcome) → new table
  `book_fair_probs`, latest value per (match, book, market, selection, line) — upsert,
  so the last pre-kickoff value is the fair CLOSE with no extra volume.
- Scheduler job `tonybet_odds_snapshot` at :01/:31 via `_run_job`; staleness covered by
  `health_alerts` direct-feed check.
- NOT in `ACCESSIBLE_BOOKMAKERS` yet — promotion needs the site-price check (acceptance).

## Acceptance (before promotion to placeable)
1. Prices match tonybet.com (EE) for the same fixtures (browser spot check ≥ 10 prices).
2. Sanity vs other books: 1X2 favourite agrees with Pinnacle/Epicbet on ≥ 95% of shared fixtures (catches flips / AH sign errors).
3. Runs on the VPS on schedule, rows land every sweep, `book_event_map` filled.

## Later phases
- 1b deep board (corners/cards/team totals/1H) near kickoff for matched fixtures.
- 2 live score/clock/corners/cards every 120 s + results (FT/HT/2H) within 24 h.
- 3 Sportradar id as cross-book fixture key (Optibet shares it).

## Risks
- Matcher false positives (production matcher has a known error rate) → acceptance #2.
- Tonybet could change its API; failures raise so `_run_job` records them.
