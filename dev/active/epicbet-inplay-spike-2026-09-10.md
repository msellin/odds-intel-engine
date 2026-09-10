# EPICBET-INPLAY-COLLECTION — spike findings (2026-09-10, read-only probe)

Probed `foCategory.getByCountry {isLiveBet:true}` + `match.getFoByLeague {isLiveBet:true}`
via epicbet_explorer's own session/_get (works from residential + VPS-via-FS).

## Confirmed
- **16 live football leagues** at probe time (Oman, Qatar, Zanzibar, Azerbaijan, Israel,
  UAE, …). Live matches carry the SAME marketGroups shape as prematch.
- Sample live match markets: **1x2, Match Total Goals, Goals Handicap, Next to Score
  (in-play-only), Draw No Bet**. The existing `_GROUP_MARKET_MAP` parser handles these
  (Next-to-Score is new/live-only → skipped unless added).
- So the collector = the EXISTING scraper with `isLiveBet=True`, reusing the parser. Minimal
  new code.

## Recommended design
- **Storage: `odds_snapshots` with `is_live=true`** (the column already exists) — no new
  table; keyed by (match, market, selection, bookmaker='Epicbet', is_live).
- **Fetcher: a live-only sweep** — enumerate live leagues (isLiveBet=true) → getFoByLeague
  (+ getSidebets if depth wanted) per live match → parse → store is_live=true.

## THE decision to make before building (owner / search DATA-ANCHOR-GROWTH + DB-RETENTION)
- **Cadence + retention.** In-play odds move every few seconds; polling many live matches ×
  many markets every 1-3 min will add a LOT of rows to `odds_snapshots`, which is ALREADY the
  biggest table (DB-ANCHOR-GROWTH / DB-RETENTION-POLICY flag its growth). So the build needs a
  bounded cadence (e.g. 2-3 min, main markets only) AND a retention rule for is_live=true rows
  (e.g. keep last-N per match or prune after settlement) decided up front — otherwise it bloats
  the DB. This is the one real decision; the fetch/parse/store is trivial.
- Also: only run while live matches exist (skip the sweep when none), to add no idle footprint.

## Verdict
Feasible + cheap to build; the gating question is cadence/retention vs DB growth, not capability.
Recommend: bounded 2-3 min main-markets-only sweep + a is_live retention rule, then build.
