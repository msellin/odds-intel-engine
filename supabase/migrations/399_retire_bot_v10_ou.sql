-- RETIRE bot_v10_ou (2026-09-24, owner "yes" on [[#077]]'s recommendation).
--
-- The v10 model's O/U 2.5 half. De-vigged Pinnacle CLV -3.85% (n=181, CI
-- [-5.01, -2.69]); negative in all 5 months and all 7 model versions. Its raw
-- +4.8% ROI is on the MAX() high-water price (ANALYSIS_GOTCHAS §30), not a price
-- that could be taken. Published nothing since 2026-09-13, so retiring it costs
-- no channel volume.
--
-- Why now: every O/U route measured on 2026-09-23/24 ends at the same place —
-- feature shape (#089 arms), a faithful Wheatcroft shots rating (#089), xG on
-- the top-10 xG leagues (#118), and the early-price move (#090 a): whatever our
-- ratings know, Pinnacle's price already carries. The owner still wants a
-- profitable O/U bot one day; that will be a NEW bot on a new basis, not this one.
-- History is kept: rows in simulated_bets stay, only the bot stops firing.
UPDATE bots SET retired_at = now(), is_active = FALSE
 WHERE name = 'bot_v10_ou' AND retired_at IS NULL;
