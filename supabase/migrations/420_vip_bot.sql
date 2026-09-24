-- 420 — #148 VIP bot (2026-09-24, owner).
-- bot_combined_1x2_ev5_v1 is the paid-tier bot: live picks go only to Pro/Elite users (Telegram) and
-- the private VIP channel; the public sees its picks only once SETTLED on /performance.
-- Mirrors workers/registry/bot_registry.VIP_BOTS (smoke VIP-BOT).

ALTER TABLE bots ADD COLUMN IF NOT EXISTS vip boolean NOT NULL DEFAULT false;
UPDATE bots SET vip = true WHERE name = 'bot_combined_1x2_ev5_v1';

-- The pending-pick leak: simulated_bets had ONE policy, "Public read" USING (true), for every role,
-- and anon holds SELECT (migration 404) — so a VIP pick was readable before kickoff with the public
-- key and was shipped to logged-in browsers by /performance. Now non-bypass roles (anon,
-- authenticated) see every row EXCEPT a VIP bot's pending ones. service_role and the engine's
-- table owner bypass RLS, so the pipeline, settlement and server-side admin reads are unchanged.
DROP POLICY IF EXISTS "Public read" ON simulated_bets;
CREATE POLICY "Public read" ON simulated_bets FOR SELECT USING (
    result IS DISTINCT FROM 'pending'
    OR NOT EXISTS (SELECT 1 FROM bots b WHERE b.id = simulated_bets.bot_id AND b.vip)
);

-- Rows written before the pipeline tagged rating/combined bots with their own model.
UPDATE simulated_bets sb SET model_version = CASE WHEN b.name = 'bot_rating_1x2_v1' THEN 'r1x2_d8plus_v1' ELSE 'r1x2_comb_v1' END
  FROM bots b
 WHERE b.id = sb.bot_id
   AND b.name IN ('bot_rating_1x2_v1', 'bot_combined_1x2_v1', 'bot_combined_1x2_ev5_v1', 'bot_combined_1x2_ev8_v1');
