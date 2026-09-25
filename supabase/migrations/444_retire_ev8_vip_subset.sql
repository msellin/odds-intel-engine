-- 444 — owner 2026-09-25: retire bot_combined_1x2_ev8_v1 ("1x2 NEW+ EV8").
-- It ran the VIP bot's exact rule with EV >= 8% instead of 5%, so its picks were a strict subset of
-- bot_combined_1x2_ev5_v1's (34 VIP picks, 16 of them EV >= 8%), and /admin/bots showed two rows with
-- identical numbers. Every EV8 pick is already in the VIP ledger carrying its EV8 tag; the EV8-vs-EV5
-- comparison is now a split of that one ledger in the VIP detail view. Its rows stay (retired section, #157).
UPDATE bots SET is_active = false, retired_at = now()
 WHERE name = 'bot_combined_1x2_ev8_v1' AND retired_at IS NULL;
