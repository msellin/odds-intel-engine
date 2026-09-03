-- INPLAY-RETIRED-2026-09-03
--
-- Four months of in-play paper trading produced no edge. Measured on all
-- settled in-play bets (n=1,246, 2026-05-08 .. 2026-08-21):
--
--   fleet ROI  -0.31%   t = -0.07     indistinguishable from zero
--
-- Only 2 of the 12 bots with n>=20 cleared |t| > 1.65, and with 12 bots tested
-- that is exactly what chance predicts. The one "winner" -- inplay_o, +186.50%
-- -- is a 25-bet sample whose recorded odds sit above the live price on most
-- of its picks.
--
-- The recorded figures flattered the fleet throughout. Re-priced at odds that
-- were live at pick time, the repriceable subset moves -7.43% -> -20.76%,
-- because 76% of in-play picks recorded a price no book was showing. That is
-- far worse than the 48% seen on prematch, and expected: an in-play line moves
-- every few seconds, so a max-over-history read (STALE-BEST-ODDS) is at its
-- most wrong exactly here.
--
-- Nor can any of it be validated. `clv` is populated on 1 of 1,246 rows, and
-- Pinnacle has ZERO live-market coverage ever (0 of 695,708 is_live rows), so
-- there is no sharp anchor for an in-play bet even in principle.
--
-- The polling that fed these bots was already gated off on 2026-08-21 by
-- AF-QUOTA-REALLOCATION, which is why no in-play pick has been raised since.
-- This makes the retirement explicit rather than an accident of a quota cut.
--
-- NOT retired: LivePoller. It is not an in-play-betting component --
-- _probe_finishing_matches + settle_finished_matches are how F/T is detected
-- and every bet in the product is settled. Historical in-play bets are also
-- kept: they are evidence of due diligence and the answer to "how do you know
-- your bots are not curve-fit?" (see OUT-OF-BETA-CUTOFF, which says never
-- delete historical bets).

UPDATE bots
   SET is_active      = false,
       retired_at     = COALESCE(retired_at, NOW()),
       maturity_label = 'retired'
 WHERE name LIKE 'inplay\_%' ESCAPE '\'
   AND (is_active = true OR retired_at IS NULL);

COMMENT ON TABLE inplay_bot_stats IS
  'In-play strategy heartbeat counters. Bots retired 2026-09-03 '
  '(INPLAY-RETIRED) after n=1,246 settled at ROI -0.31%, t=-0.07. Table kept '
  'for history; no new rows expected while INPLAY_STRATEGIES_ENABLED is unset.';
