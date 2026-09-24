-- DEAD-DATA ([[#087]], 2026-09-24, owner "yes", then "do A" to apply it).
-- Drop tables that nothing reads.
--
-- Checked the same day, for each table below:
--   * code references in BOTH repos (engine workers/scripts/deploy, odds-intel-web/src): none
--     left after engine 8272ff26 removed every team_transfers writer and reader;
--   * dependent views and foreign keys: none;
--   * present in the 2026-09-24 03:33 UTC nightly dump
--     (/var/backups/pg/nightly/oddsintel-2026-09-24.dump, verified with pg_restore -l), with
--     90-day copies on the Storage Box — restorable until ~2026-12-23.
--
--   team_transfers        883 MB  its signal (squad_disruption_*) is excluded from every
--                                  model; both writers removed (fetch_enrichment.fetch_transfers,
--                                  already off, AND job_backfill_transfers, still scheduled every
--                                  25 min — missed by the 2026-09-21 retirement)
--   scratch_pit_odds_3h    80 MB  zero references anywhere, including migrations
--   8 empty tables        <1 MB   zero references: World Cup, CS2 news, never-used in-play/xG
--                                  snapshot tables, manager_tenures, and the unused
--                                  `placement_attempts` (the real one is coolbet_placement_attempts)
--
-- DELIBERATELY NOT DROPPED, although the #087 audit listed them as dead:
--   live_match_snapshots (650 MB) — written by the LivePoller, freshness-alerted,
--     weekly-pruned, and the xG-overperformance fallback when match_stats lacks xG.
--   inplay_book_quotes (365 MB) — the live Epicbet in-play collector service's table.
--   coolbet_market_inventory — still written by the scheduled Coolbet explorer.
--
-- No CASCADE on purpose: if anything did depend on one of these, the migration must
-- FAIL loudly rather than silently take a view with it.
DROP TABLE IF EXISTS team_transfers;
DROP TABLE IF EXISTS scratch_pit_odds_3h;
DROP TABLE IF EXISTS coolbet_inplay_snapshots;
DROP TABLE IF EXISTS cs2_hltv_news;
DROP TABLE IF EXISTS live_xg_snapshots;
DROP TABLE IF EXISTS manager_tenures;
DROP TABLE IF EXISTS placement_attempts;
DROP TABLE IF EXISTS wc_match_tweets;
DROP TABLE IF EXISTS wc_user_achievements;
DROP TABLE IF EXISTS wc_user_picks;
