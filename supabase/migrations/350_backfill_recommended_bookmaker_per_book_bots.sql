-- RECBOOK-BACKFILL-PER-BOOK-BOTS (2026-09-14). Recover recommended_bookmaker
-- where the bot is bound to ONE book by construction.
--
-- WHY IT MATTERS NOW. Since the own-book CLV fixes, a row with no
-- recommended_bookmaker yields no own-book close and therefore no clv at all —
-- it drops out of the only verdict basis we have. The two bots that decide the
-- whole OWN question are being judged at n=66 and n=67, the thinnest evidence
-- in the project, and 42 + 23 of their rows are sitting out for want of a label
-- we can establish with certainty.
--
-- WHY THIS IS A FACT, NOT A GUESS. workers/jobs/pick_trigger_matcher.py maps
-- (book, market, strategy) -> bot:
--     ("Coolbet",     "1x2", "sharp_1x2") -> bot_coolbet_trigger_sharp_1x2_v1
--     ("Unibet-Site", "1x2", "sharp_1x2") -> bot_unibet_trigger_sharp_1x2_v1
-- Each of these bots only ever sees ONE book's price. The book is in the bot's
-- name because the matcher put it there.
--
-- WHAT IS DELIBERATELY NOT TOUCHED. Genuinely multi-book bots
-- (bot_trigger_*_sharp_v1, bot_acca_leg_shadow, the wide/mirror bots) are left
-- NULL. Their odds_at_pick is a MAX across books, so the winning book varies per
-- pick. Tested rather than assumed: on a 400-row sample, matching odds_at_pick
-- back to a book quoting exactly that price within +/-90 min recovers exactly
-- one book on only 27.0 pct of rows, is ambiguous on 14.5 pct, and fails on
-- 58.5 pct -- the last because odds_snapshots retention has pruned the intraday
-- rows (Coolbet 1x2 volume falls from ~34k/week in September to ~2.3k/week in
-- mid-August). Backfilling those would be inventing the number that decides
-- whether a bot lives, which is the exact failure mode this whole day was spent
-- removing.
--
-- Only NULLs are written, so re-running is a no-op.

UPDATE shadow_bets sb
   SET recommended_bookmaker = 'Coolbet'
  FROM bots b
 WHERE b.id = sb.bot_id
   AND sb.recommended_bookmaker IS NULL
   AND b.name IN ('bot_coolbet_trigger_1x2_v1', 'bot_coolbet_trigger_ou_v1',
                  'bot_coolbet_trigger_sharp_1x2_v1', 'bot_coolbet_trigger_sharp_ou_v1');

UPDATE shadow_bets sb
   SET recommended_bookmaker = 'Unibet-Site'
  FROM bots b
 WHERE b.id = sb.bot_id
   AND sb.recommended_bookmaker IS NULL
   AND b.name IN ('bot_unibet_trigger_1x2_v1', 'bot_unibet_trigger_ou_v1',
                  'bot_unibet_trigger_sharp_1x2_v1', 'bot_unibet_trigger_sharp_ou_v1');
