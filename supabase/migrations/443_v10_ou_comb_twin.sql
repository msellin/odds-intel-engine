-- 443 — #152 (owner 2026-09-25: "unretire bot_v10_ou — it gets the new ou model").
-- The v10 O/U bot returns on the combined O/U model (ou_comb_v1) as a TWIN, bot_v10_ou_comb_v1,
-- TESTING, sent to /picks + the public Telegram channel, own record, not in the headline.
--
-- WHY A TWIN AND NOT `UPDATE bots SET retired_at = NULL WHERE name = 'bot_v10_ou'`: bot_ledger
-- puts EVERY simulated_bets row of a bot in its record (in_record = true for that source), and
-- bot_v10_ou holds 252 settled picks from seven old ensemble versions — sharp-anchor CLV -3.9%
-- (n 138, 95% upper bound -2.65%). Re-activating that row would (a) raise the #155 'review this
-- bot' flag on day one (bot_review_flag: n >= 50 and CI below 0) and (b) show the old model's
-- record on /performance as this rule's. ANALYSIS_GOTCHAS #84: change a bot only via a twin.
-- bot_v10_ou stays retired; its picks keep counting in the totals and stay in the retired
-- section (#157). New picks carry model_version = 'ou_comb_v1' in any case.
--
-- ON CONFLICT DO UPDATE, not DO NOTHING: the scheduler restarts on the same push and
-- ensure_bots() inserts a bare row (maturity 'active', not shown) if the pipeline reaches this
-- bot before this migration is applied. The labels below must win either way.
INSERT INTO bots (name, display_name, strategy, starting_bankroll, current_bankroll,
                  is_active, maturity_label, show_on_picks, show_on_performance)
VALUES ('bot_v10_ou_comb_v1', 'Goals over/under — new model',
        'Successor of bot_v10_ou on the combined O/U model (ou_comb_v1, served = Pinnacle where priced else combined): EV >= 3% flat, O/U 1.5/2.5/3.5, odds 1.30-3.00, one pick per match; VIP-held / VIP-range picks held back until kickoff (#164). #152.',
        1000.00, 1000.00, true, 'testing', true, true)
ON CONFLICT (name) DO UPDATE
   SET display_name        = EXCLUDED.display_name,
       strategy            = EXCLUDED.strategy,
       is_active           = true,
       retired_at          = NULL,
       maturity_label      = 'testing',
       show_on_picks       = true,
       show_on_performance = true;

-- bot_v10_ou stays retired — a guard, not a change (it already is, migration 399).
UPDATE bots SET retired_reason = COALESCE(retired_reason, '') ||
       ' | 2026-09-25: succeeded by bot_v10_ou_comb_v1 on ou_comb_v1 (#152, migration 443)'
 WHERE name = 'bot_v10_ou' AND retired_at IS NOT NULL
   AND COALESCE(retired_reason, '') NOT LIKE '%bot_v10_ou_comb_v1%';
