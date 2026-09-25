-- 439 — VIP FIRST: free picks that a VIP bot holds (or would take) are HELD BACK until kickoff
-- ([[#164]] VIP-PICKS-LEAK-TO-PUBLIC-2026-09-25, owner decision 2026-09-25).
--
-- WHY. Paid VIP picks reached the public /picks feed and the public Telegram channel before
-- kickoff: bot_v10_1x2_newplus_v1 took 2 picks after VIP EV5 held them and the forward-test
-- consensus arm published 3 (2 sent). The pipeline's `vip_exclude` RE-DERIVED the VIP rule at
-- the public bot's current price, so it passed after a price move, and three other public paths
-- had no check at all.
--
-- THE RULE (one module: workers/utils/vip_guard.py). VIP bots never give up a pick. A FREE pick
-- is RECORDED normally (own record + the pre-registered forward test unchanged) but HELD BACK —
-- not on /picks, not sent, not pending on any public view — until kickoff when it is VIP-HELD
-- (a VIP / hide_pending bot has a pending pick on the same match+market+selection) or IN VIP'S
-- RANGE at the free pick's decision time and price. The writers (store_bet, the forward-test
-- claim) stamp the row; when the VIP pick comes later, vip_guard.hold_back_followers stamps the
-- free rows already written. Every public surface filters on the DATA below — no surface
-- re-derives the rule.
--
--   held_back_until   = kickoff for a held-back pick (NULL = publish normally). A surface hides
--                       a row while held_back_until > now(); at kickoff it simply appears.
--   held_back_reason  = vip_held | vip_range_1x2 | vip_range_ou | guard_error (fails closed).
--   vip_rule_breach   = the pick broke the rule BEFORE this fix and was already public (sent to
--                       the channel, or settled). Kept and counted — never deleted, never unsent.

ALTER TABLE simulated_bets
    ADD COLUMN IF NOT EXISTS held_back_until timestamptz,
    ADD COLUMN IF NOT EXISTS held_back_reason text,
    ADD COLUMN IF NOT EXISTS vip_rule_breach boolean NOT NULL DEFAULT false;
ALTER TABLE picks_forward_test
    ADD COLUMN IF NOT EXISTS held_back_until timestamptz,
    ADD COLUMN IF NOT EXISTS held_back_reason text,
    ADD COLUMN IF NOT EXISTS vip_rule_breach boolean NOT NULL DEFAULT false;
ALTER TABLE picks_board
    ADD COLUMN IF NOT EXISTS held_back_until timestamptz,
    ADD COLUMN IF NOT EXISTS held_back_reason text;

COMMENT ON COLUMN simulated_bets.held_back_until IS
  '#164 VIP FIRST: kickoff when this free pick is held back (VIP-held or in VIP range at pick time). Public surfaces hide the row while held_back_until > now(). Written by store_bet / vip_guard.';
COMMENT ON COLUMN simulated_bets.vip_rule_breach IS
  '#164: broke the VIP-first rule before the fix and was already public (sent or settled). Kept and counted; a data flag only.';
COMMENT ON COLUMN picks_forward_test.held_back_until IS
  '#164 VIP FIRST: kickoff when this published-arm pick is held back. Still counts in the pre-registered test (prereg deviation: it publishes at kickoff). Never sent while held.';
COMMENT ON COLUMN picks_board.held_back_until IS
  '#164 VIP FIRST: kickoff when this watchlist leg is VIP-held / in VIP range; picks_board_public hides it until then.';

CREATE INDEX IF NOT EXISTS idx_simulated_bets_held_back
    ON simulated_bets (held_back_until) WHERE held_back_until IS NOT NULL;

-- ── Public surface 1: /picks and /api/v1/upcoming (picks_public_all) ────────────────────────
-- Columns unchanged (CREATE OR REPLACE); each branch gains the hold-back filter.
CREATE OR REPLACE VIEW picks_public_all AS
 SELECT p.id,
    'sharp'::text AS edge_kind,
        CASE
            WHEN p.arm = 'consensus_anchor'::text AND p.grade = 'D'::text THEN 'bot_consensus_d_v1'::text
            WHEN p.arm = 'consensus_anchor'::text AND p.grade = 'C'::text THEN 'bot_consensus_c_v1'::text
            WHEN p.arm = 'consensus_anchor'::text THEN 'bot_consensus_b_v1'::text
            WHEN p.arm = 'live'::text AND p.market = 'over_under_25'::text THEN 'bot_sharp_ou_v1'::text
            ELSE 'bot_sharp_1x2_v1'::text
        END AS bot,
    p.match_id,
    p.market,
    p.selection,
    p.odds,
    p.bookmaker,
    p.edge,
    p.p_sharp AS fair_prob,
    p.rule_version,
    p.alignment_gap_minutes,
    p.published_at,
    p.outcome,
    p.clv,
    m.date AS kickoff_utc,
    l.name AS league,
    l.country,
    ht.name AS home_team,
    at.name AS away_team,
    p.arm,
    p.anchor_bookmaker,
    p.grade
   FROM picks_forward_test p
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE (p.arm = ANY (ARRAY['live'::text, 'consensus_anchor'::text])) AND m.status <> 'postponed'::match_status
    AND NOT (p.grade = 'D'::text AND p.telegram_message_id IS NULL)
    AND NOT COALESCE(p.held_back_until > now(), false)
UNION ALL
 SELECT s.id,
    'model'::text AS edge_kind,
    b.name AS bot,
    s.match_id,
    s.market,
    s.selection,
    s.odds_at_pick AS odds,
    s.recommended_bookmaker AS bookmaker,
        CASE
            WHEN s.calibrated_prob IS NOT NULL AND s.odds_at_pick > 0::numeric THEN round(s.calibrated_prob - 1.0 / s.odds_at_pick, 6)
            ELSE s.edge_percent::numeric
        END AS edge,
    s.calibrated_prob AS fair_prob,
    s.model_version AS rule_version,
    NULL::numeric AS alignment_gap_minutes,
    s.pick_time AS published_at,
    NULLIF(s.result::text, 'pending'::text) AS outcome,
    s.clv,
    m.date AS kickoff_utc,
    l.name AS league,
    l.country,
    ht.name AS home_team,
    at.name AS away_team,
    NULL::text AS arm,
    NULL::text AS anchor_bookmaker,
    NULL::text AS grade
   FROM simulated_bets s
     JOIN bots b ON b.id = s.bot_id
     JOIN matches m ON m.id = s.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE b.show_on_picks AND b.retired_at IS NULL AND s.combo_legs IS NULL AND s.match_minute_at_pick IS NULL
    AND m.status <> 'postponed'::match_status
    AND NOT COALESCE(s.held_back_until > now(), false);

-- ── Public surface 2: the /picks watchlist (picks_board_public) ─────────────────────────────
CREATE OR REPLACE VIEW picks_board_public AS
 SELECT b.match_id,
    b.market,
    b.selection,
    b.odds,
    b.bookmaker,
    b.p_sharp,
    b.edge,
    b.odds_breakeven,
    b.odds_grade_b,
    b.odds_grade_a,
    b.anchor_overround,
    b.updated_at,
    b.first_seen_at,
    b.best_odds_seen,
    b.best_odds_book,
    b.best_odds_at,
    b.target_b_met_at,
    b.target_a_met_at,
    b.outcome,
    b.settled_at,
    m.date AS kickoff_utc,
    l.name AS league,
    l.country,
    ht.name AS home_team,
    at.name AS away_team
   FROM picks_board b
     JOIN matches m ON m.id = b.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE NOT COALESCE(b.held_back_until > now(), false);

-- ── Public surface 3: the forward-test ledger view (anon-readable) ──────────────────────────
CREATE OR REPLACE VIEW picks_forward_test_public AS
 SELECT p.id,
    p.match_id,
    p.market,
    p.selection,
    p.odds,
    p.bookmaker,
    p.edge,
    p.p_sharp,
    p.anchor_bookmaker,
    p.anchor_overround,
    p.alignment_gap_minutes,
    p.rule_version,
    p.kickoff_at,
    p.published_at,
    p.outcome,
    p.pnl,
    p.closing_odds,
    p.closing_bookmaker,
    p.clv,
    p.clv_margin_corrected,
    p.settled_at,
    m.date AS kickoff_utc,
    l.name AS league,
    l.country,
    ht.name AS home_team,
    at.name AS away_team,
    p.arm,
    p.grade,
    COALESCE(r.record_rule_version, p.rule_version) AS record_rule_version,
    r.record_state
   FROM picks_forward_test p
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
     LEFT JOIN picks_forward_test_record_leg r ON r.id = p.id
  WHERE (p.arm = ANY (ARRAY['live'::text, 'consensus_anchor'::text]))
    AND NOT (NOT p.grade IS DISTINCT FROM 'D'::text AND p.telegram_message_id IS NULL)
    AND NOT COALESCE(p.held_back_until > now(), false);

-- ── Public surface 4: raw simulated_bets for anon (RLS) ─────────────────────────────────────
-- Was: pending rows of VIP / hide_pending bots hidden. Now also: pending held-back free rows.
DROP POLICY IF EXISTS "Public read" ON simulated_bets;
CREATE POLICY "Public read" ON simulated_bets FOR SELECT USING (
    result IS DISTINCT FROM 'pending'::bet_result
    OR (NOT EXISTS (SELECT 1 FROM bots b
                     WHERE b.id = simulated_bets.bot_id AND (b.vip OR b.hide_pending))
        AND NOT COALESCE(held_back_until > now(), false))
);

-- ── Private surface the web's pending views read (service_role): bot_ledger_display ─────────
-- APPENDS `held_back` (true while a sim / forward-test leg is held back). /performance's
-- history and /api/performance/bot-legs drop pending legs where it is true.
CREATE OR REPLACE VIEW bot_ledger_display AS
 SELECT l.source,
    l.pick_id,
    l.bot_name,
    l.bot_id,
    l.match_id,
    l.kickoff,
    l.pick_time,
    l.market,
    l.selection,
    l.odds,
    l.bookmaker,
    l.result,
    l.pnl_unit,
    l.clv_raw,
    l.clv_mc,
    l.clv_pinnacle,
    l.is_inplay,
    l.model_version,
    l.rule_version,
    ht.name AS home_team,
    at.name AS away_team,
    l.odds_public,
    l.public_basis,
    l.pnl_unit_public,
    l.odds_own,
    l.own_basis,
    l.pnl_unit_own,
    l.stake,
    l.pnl_staked_public,
    l.clv_anchor_public,
    l.clv_anchor_own,
    l.clv_anchor_source,
    l.in_record,
    l.record_state,
    l.record_rule_version,
    l.model_prob,
    l.edge,
    l.strategy_profile,
    lg.name AS league,
    lg.country,
    COALESCE(
        CASE l.source
            WHEN 'sim' THEN (SELECT s.held_back_until > now() FROM simulated_bets s WHERE s.id = l.pick_id)
            WHEN 'forward_test' THEN (SELECT p.held_back_until > now() FROM picks_forward_test p WHERE p.id = l.pick_id)
        END, false) AS held_back
   FROM bot_ledger l
     LEFT JOIN matches m ON m.id = l.match_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
     LEFT JOIN leagues lg ON lg.id = m.league_id;
