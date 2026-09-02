-- SHADOW-BOTS-DETAIL-TRUNCATION-2026-09-02
--
-- The per-bot detail page (/admin/shadow-bots/[bot]) fetched raw shadow_bets
-- with `.limit(500)` and deduplicated in JavaScript AFTERWARDS. Shadow bots
-- persist one row per (cohort x match x market x selection) and the cohorts
-- fire every 30 minutes, so a single pick accumulates ~10 rows. The newest 500
-- raw rows therefore cover only a fraction of the bot's picks, and every
-- number on the page -- picks, settled, hit rate, CLV, ROI -- was computed
-- from that recency-biased sliver.
--
-- Measured before the fix: 24 of 41 bots truncated.
--   bot_dc_value          3,081 unique picks -> 48 shown  (1.6%)
--   bot_dc_specialist     2,962 -> 48
--   bot_dc_strong_fav     1,555 -> 39
--   bot_pin_1x2_home_v1     304 -> 61   (the reported case: card +12.7%
--                                        vs detail -11.8%)
--
-- The list page hit the same bug on 2026-08-22 and was patched by raising the
-- limit 500 -> 5000, with a note that a DISTINCT ON strategy would be needed
-- once volume grew. shadow_bets is now 143,263 rows for 13,955 unique picks,
-- so this is that change.
--
-- Dedup rule matches what the page intended: EARLIEST pick_time per
-- (bot, match, market, selection) -- the first sighting is the real record of
-- when the edge was spotted; later cohort rows are drift-tracking noise.
-- ANALYSIS_GOTCHAS #18 still applies: this is the shadow ledger only, never
-- mixed with simulated_bets.

CREATE OR REPLACE VIEW shadow_bets_deduped AS
SELECT DISTINCT ON (sb.bot_id, sb.match_id, sb.market, sb.selection)
       sb.*
  FROM shadow_bets sb
 ORDER BY sb.bot_id, sb.match_id, sb.market, sb.selection, sb.pick_time ASC;

COMMENT ON VIEW shadow_bets_deduped IS
  'One row per (bot, match, market, selection) — the earliest pick_time. '
  'Use this for any per-bot aggregate; querying shadow_bets directly '
  'multiplies every pick by its cohort re-recordings (~10x). '
  'SHADOW-BOTS-DETAIL-TRUNCATION-2026-09-02.';

GRANT SELECT ON shadow_bets_deduped TO anon, authenticated, service_role;

-- PostgREST caches the schema; without this the new view 404s until restart.
NOTIFY pgrst, 'reload schema';
