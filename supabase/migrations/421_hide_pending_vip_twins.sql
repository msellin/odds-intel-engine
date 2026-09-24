-- 421 — #148 follow-up (2026-09-24): close the VIP back door.
-- bot_combined_1x2_ev8_v1 runs unlisted as the clean EV8-only measure, but its picks are EXACTLY the
-- VIP bot's EV8 picks — so after migration 420 an anon read of ITS pending rows still gave the VIP
-- pick away (verified with SET ROLE anon). `hide_pending` hides a bot's pending rows from
-- anon/authenticated without making it a VIP card (`vip` stays the VIP bot only).
ALTER TABLE bots ADD COLUMN IF NOT EXISTS hide_pending boolean NOT NULL DEFAULT false;
UPDATE bots SET hide_pending = true WHERE name IN ('bot_combined_1x2_ev5_v1', 'bot_combined_1x2_ev8_v1');

DROP POLICY IF EXISTS "Public read" ON simulated_bets;
CREATE POLICY "Public read" ON simulated_bets FOR SELECT USING (
    result IS DISTINCT FROM 'pending'
    OR NOT EXISTS (SELECT 1 FROM bots b WHERE b.id = simulated_bets.bot_id AND (b.vip OR b.hide_pending))
);
