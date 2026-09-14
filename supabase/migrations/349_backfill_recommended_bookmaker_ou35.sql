-- OU35-RECBOOK-BACKFILL (2026-09-14). `bot_ou35_model_v1` carries
-- recommended_bookmaker = NULL on 100pct of its rows, which since
-- SIMULATED-CLV-OWN-BOOK / SHADOW-CLV-NO-ARBITRARY-FALLBACK means it can no
-- longer produce a CLV at all: no book, no own-book close, no clv.
--
-- That is the correct new behaviour (a biased number is worse than none), but
-- it leaves a live bot permanently unmeasurable, and the book is recoverable
-- WITH CERTAINTY rather than inferred: workers/jobs/ou35_model_shadow.py:99
-- queries `WHERE o.market = 'over_under_35' AND o.bookmaker = 'Coolbet'`. The
-- bot only ever sees one book's price, so every one of its picks was priced at
-- Coolbet by construction.
--
-- This is a backfill of a FACT, not an estimate. It is safe precisely because
-- the bot is single-book; the same backfill must NOT be applied to multi-book
-- bots, where the book that set the best price varies per pick and guessing it
-- would manufacture exactly the kind of number today's work spent the day
-- removing.
--
-- Only NULLs are touched, so re-running is a no-op.

UPDATE shadow_bets
   SET recommended_bookmaker = 'Coolbet'
 WHERE bot_id = (SELECT id FROM bots WHERE name = 'bot_ou35_model_v1')
   AND recommended_bookmaker IS NULL;

UPDATE simulated_bets
   SET recommended_bookmaker = 'Coolbet'
 WHERE bot_id = (SELECT id FROM bots WHERE name = 'bot_ou35_model_v1')
   AND recommended_bookmaker IS NULL;
