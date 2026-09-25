-- 424 — #149 / #148: O/U EARLY is the second VIP bot (owner 2026-09-25: "one 1x2 and the other ou").
-- VIP = live picks only to Pro/Elite + the private channel, public only once settled (migration 420).
-- TWO-ANCHOR shares many of O/U EARLY's picks, so its pending rows are hidden too (the migration-421
-- back door, again) without making it a VIP card.
UPDATE bots SET vip = true, hide_pending = true WHERE name = 'bot_ou_sharp_early_v1';
UPDATE bots SET hide_pending = true WHERE name = 'bot_ou_sharp_2anchor_v1';
