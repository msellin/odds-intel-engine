# COOLBET-INGEST-REWORK — context (started 2026-09-08)

## Decisions locked (owner)
- Anchored-leagues-first for the coolbet_league_mapping.json growth.
- Scope filter = Pinnacle-anchored (∪ any bot-picked) fixtures.

## Baselines captured 2026-09-08 (before/after proof)
- STALENESS (upcoming Coolbet quotes, median age): 0-3h→782min, 3-6h→780, 6-12h→774,
  12-24h→826. ~13h — far worse than the task's 197min; the 13.7h-serial-pass symptom, live.
- SCOPE: 552 upcoming/48h; Coolbet-priced 61; Pinnacle-anchored 124 → 78% reduction.
- WRONG-FIXTURE: 1 favourite-flip / 532 co-priced = 0.19% (task baseline 0.3%).
- Feed health today: tarpit LIFTED, 41k rows/24h, placement active, FS healthy.

## Plan (sequence)
1. [done] baselines.
2. Bulk-listing rewrite = fuzzy-match core fix + sweep-architecture, merged:
   switch bulk sweep OFF cross-league search/v2 ONTO league-scoped path
   (fo-tree/et -> fo-category?categoryId=), batched + re-sorted by current TTKO each run.
   Grow coolbet_league_mapping.json for Pinnacle-anchored leagues first.
3. Scope filter (Pinnacle-anchored set).
4. Ingest-hardening (silence-proofing + output health + canonical vocab).
5. Negative-cache layer 2 (deferred retry queue).

## Key files / symbols (to verify next)
- workers/jobs/coolbet_explorer.py: load_matches_in_window (~1371), run_bulk,
  run_league_sweep (DEAD, 0 callers), search_coolbet_event, _do_search, fuzzy_match_event, _parse_event
- coolbet_league_mapping.json (117 entries)
- endpoints: GET /s/sbgate/category/fo-tree/et?country=EE ; GET /s/sbgate/sports/fo-category/?categoryId=<id>&country=EE&language=et&layout=EUROPEAN&limit=<n>&matchTypeFilter=all

## KEYSTONE FIX shipped 2026-09-08: FO-CATEGORY-ENVELOPE
- Root cause of "league-scoped path is dead": fetch_events_for_league (coolbet_placer.py)
  read `.matches` off the top-level dict, but the endpoint now returns
  {"categories":[{...,"matches":[...]}], "filterUsed":..., "availableFilters":...}.
  -> 0 events for EVERY league -> run_league_sweep useless -> sweep fell back to
  cross-league search (the FP source). Fixed: descend into `categories`.
- Verified live: dry-run league sweep (require_pinnacle=True) went 0 -> 32 matched
  pairs / 4,076 odds rows across 14 anchored leagues. FPs impossible by construction
  (within-league matching). Only caller is run_league_sweep (dead in prod) -> fix is safe.
- Smoke FO-CATEGORY-ENVELOPE. Endpoints confirmed live: fo-category?categoryId= (200),
  fo-tree/et (200, tree keyed by `children`).

## COVERAGE GAP measured (the next work)
- Upcoming 48h Pinnacle-anchored fixtures: 124 across 31 leagues.
  MAPPED 37 (29%) / UNMAPPED 87 (70%). coolbet_league_mapping.json = 117 entries.
- Cannot replace run_bulk with run_league_sweep until the map covers the anchored set
  (else lose Coolbet coverage on 70% of anchored fixtures). Top unmapped anchored:
  Challenge Cup, National League N/S + Cup, youth/reserve leagues (some Coolbet won't carry).
- NEXT: build fo-tree map-growth tool (walk children, match unmapped anchored AF leagues
  to Coolbet categoryId by name); grow map; THEN wire run_league_sweep as primary.

## MATCHING DESIGN + TRIPLE-CHECK (2026-09-08) — decisive findings
Owner directive: skip games AF doesn't provide, but TRIPLE CHECK first.

Board reality: Coolbet ~816 open events (140 categories). Decomposition within our
AF fixture horizon (~41h): 121 virtual (exclude), 460 real BEYOND horizon (Coolbet
prices weeks ahead; no match_id), 235 within horizon. AF is far WIDER overall
(1,460 leagues / 588 active vs Coolbet ~140); Coolbet-only = bottom tier only
(Finnish Nelonen 4th / Kolmonen 3rd — AF has 0 ever; "Lower Leagues" bucket).

Coolbet-only games are UNBETTABLE by any bot: no AF features (model input) AND no
Pinnacle anchor. NB 5/13 active bots are LINE-SHOP (no model): bot_coolbet_value,
bot_pin_1x2_home, bot_sweep_ou25/35, bot_corners_paper_shadow — but they too need
Pinnacle. So skipping Coolbet-only loses nothing bettable.

MULTI-SIGNAL MATCH (owner's idea) — the signal is there: region_icon is an ISO
country code (GB-ENG, US, GB-SCT). Block on COUNTRY -> DATE-SLOT -> team names.
Country+date blocking lifted near-term coverage 77% -> 83% vs pure fuzzy.

⚠️ TRIPLE-CHECK CAUGHT A REAL PROBLEM: "unmatched != absent". Naive fuzzy
false-negatives real AF-present games because Coolbet short-names vs AF full-names:
Cardiff vs "Cardiff City", Hull vs "Hull City", West Ham vs "West Ham United".
Verified AF HAS Cardiff-Stoke, Bolton-West Ham, Sunderland-Hull tonight, yet the
naive classifier marked them skippable. => CANNOT skip-on-non-match until the
matcher is HARDENED.

FIX (build order): harden the matcher FIRST, THEN enable skipping.
- token_set_ratio / partial_ratio (subset-safe: "stoke" ⊂ "stoke city") instead of
  token_sort_ratio, applied WITHIN a country+date block (small candidate set -> safe).
- persist confirmed matches via matches.coolbet_match_id (already a column) so the
  join is one-time, and build a Coolbet-team -> AF-team-id alias table over time.
- near-term filter: drop Coolbet events with start > horizon (skips the 460 future).
- only AFTER matcher hardened is "unmatched => genuinely AF-absent => skip" safe.

## WALK-SWEEP BUILT + LIVE-VALIDATED 2026-09-08
- run_board_sweep + enumerate_coolbet_football_categories + coolbet_matching module shipped.
- --board / --horizon-hours entrypoint added (default run_bulk unchanged; nothing flipped).
- LIVE run (18h horizon): 7,306 rows / 97 matches in ~31 min. Recovered games got CORRECT
  prices (Cardiff 2.15 vs Pin 2.17; Bolton 5.25 vs 5.28; Sunderland 1.72 vs 1.71 — favourites agree).
  vs run_bulk baseline: 61 upcoming matches, 197min-13h staleness. Board sweep: +59% coverage,
  ~31min staleness, FP-free (country-blocked), no wasted calls on unmatchable fixtures.
- html.unescape fix in norm_team ('Havant &amp; Waterlooville' junk 'amp' token).

## "ABSENT" RE-VERIFIED (owner challenge — naive classifier had wrongly flagged Stoke)
- The "~43 absent" was NOT reliable. Hardened-matcher residual (~55 near-term unmatched):
  ~40 TRULY-ABSENT (bottom-tier Finnish Nelonen/Kolmonen + reserve/U21 — AF doesn't know the
  teams, no anchor, unbettable); a few genuine misses AF HAS (Havant-Chippenham, Al-Ittihad)
  recoverable via name handling; my own verification method also over-counted (Rangers->U21 false hit).
- DESIGN RULE CONFIRMED: never mark a game permanently "absent". run_board_sweep leaves unmatched
  events unstored and RETRIES next cycle; a negative-cache (layer 2) must use backoff, never permanent.

## REMAINING
- THE FLIP (real-money feed): point launchd com.oddsintel.coolbet-odds-snapshot at
  `--board --horizon-hours <N>`. Duration/horizon tradeoff: 18h~31min/97, 48h~longer. Pick a
  horizon that fits the ~30min cadence (or add batching = COOLBET-SWEEP-ARCHITECTURE later).
  CHECKPOINT: operator-facing launchd reload — get explicit go-ahead.
- Then: SWEEP-ARCHITECTURE (batch/re-sort for even fresher near-KO), INGEST-HARDENING
  (output-based health, canonical vocab), NEGATIVE-CACHE layer 2 (retry queue). FUZZY-MATCH-FP
  effectively solved by country blocking.
