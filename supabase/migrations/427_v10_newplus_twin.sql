-- 427 — #152: bot_v10_1x2's new-model twin, shown on /performance (2026-09-25, owner).
-- bot_v10_1x2 is unchanged (live CLV ~ +4.7% Jul-Sep); the twin runs NEW+ at EV >= 3%, never a
-- VIP-held pick, and the two are compared live after 50-100 settled picks.
-- `show_on_performance`: list a bot on /performance WITHOUT pretending it has live evidence —
-- its maturity stays 'testing' and the page shows that chip. (calibrated/beta bots and VIP bots are
-- listed by their own rules; this flag is only for owner-chosen testing bots.)
ALTER TABLE bots ADD COLUMN IF NOT EXISTS show_on_performance boolean NOT NULL DEFAULT false;
INSERT INTO bots (name, display_name, strategy, starting_bankroll, current_bankroll,
                  is_active, maturity_label, show_on_picks, show_on_performance)
VALUES ('bot_v10_1x2_newplus_v1', 'Match result — new model',
        'Twin of bot_v10_1x2 on the NEW+ 1X2 model (r1x2_comb_v1): EV >= 3%, never a VIP-held pick. #152.',
        1000.00, 1000.00, true, 'testing', false, true)
ON CONFLICT (name) DO NOTHING;
