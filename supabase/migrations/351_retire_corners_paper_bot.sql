-- CORNERS-RETIREMENT (2026-09-14). Retire bot_corners_paper_shadow_v1 on
-- margin-corrected own-book CLV: -4.86 pct, CI [-5.59,-4.12], t=-12.93, n=467.
--
-- It was carried as "unjudgeable" for months because its settler believed no
-- corners closing anchor existed. That was false -- Pinnacle prices corners on
-- 2,045 fixtures / 43 lines in 30 days, covering 95 pct of Coolbet's corners
-- slate and 87 pct of Epicbet's, and the bot already de-vigs that same line to
-- SELECT its bets. The closes were in odds_snapshots the whole time.
--
-- Consequence of the gap: 568 settled picks with no CLV, an unanchored +9.60 pct
-- ROI displayed as though it were evidence, and 17 picks/day of noise in the
-- operator's upcoming-picks table. Once the anchor was actually used, the answer
-- was immediate and decisive.
--
-- It also priced against 'Unibet' -- API-Football's feed, 33.1 pct phantom-high
-- and dead since 2026-09-12 -- rather than our 'Unibet-Site' scrape. Fixed in
-- the same commit for this bot and the two others carrying the same list, so the
-- defect cannot recur when a future bot is written from one of them.

UPDATE bots
   SET is_active = FALSE, retired_at = now(), maturity_label = 'retired'
 WHERE name = 'bot_corners_paper_shadow_v1' AND retired_at IS NULL;
