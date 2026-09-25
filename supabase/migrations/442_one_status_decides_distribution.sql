-- 442 — [[#155]] ONE STATUS DECIDES DISTRIBUTION (owner decisions 2026-09-25).
--
-- WHY. A bot's public reach was decided by FOUR per-bot inputs that drifted independently:
-- maturity_label (/performance + the Telegram "calibrated" gate), show_on_picks (/picks),
-- show_on_performance (#152's extra /performance switch) and vip. Verified 2026-09-25: a BETA bot
-- that was not sent, a TESTING bot not sent, VIP bots reading only "VIP", and 17 EXPERIMENTAL
-- bots whose PENDING picks the anon role could read (bot_distribution.pending_exposed, #162).
--
-- THE RULE (dev/active/bots-session-handover-2026-09-25.md §3.1, docs/SYSTEM_MAP.md "Lifecycle"):
--   EXPERIMENTAL  admins only · nothing sent · nothing public, not even pending rows
--   TESTING       /performance row marked TESTING · SENT (/picks + public Telegram) · own record · NOT headline
--   BETA / CALIBRATED   sent · own record · headline totals
--   VIP           a channel on top of a public status ("VIP · TESTING"): never sent publicly,
--                 settled picks public, own record, never the headline
-- After this migration the status is the ONLY per-bot input:
--   * bot_public_status(label, retired_at) — the one predicate (bot_distribution, the RLS policy,
--     the trigger and the public views all call it).
--   * bots.show_on_picks / bots.show_on_performance are DERIVED by a trigger; an UPDATE that sets
--     either against the status is REJECTED (so the old /admin "Show on /picks" switch can no
--     longer drift from the status — the web replaces it with a read-only line).
--   * picks_public_all / picks_forward_test_public gate on bot_distribution, not a switch.
--   * simulated_bets "Public read": pending rows only for bots whose status sends pending picks.
-- Engine senders read the same view (coolbet_signaler, the forward-test publisher —
-- workers/utils/bot_status.py). #164's held_back_until filters are kept unchanged.

BEGIN;

-- ── 1. The one predicate ──────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.bot_public_status(p_label text, p_retired_at timestamptz)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT p_retired_at IS NULL AND coalesce(p_label, 'experimental') IN ('testing','beta','calibrated')
$$;
COMMENT ON FUNCTION public.bot_public_status(text, timestamptz) IS
  '#155: TRUE when a bot''s STATUS puts it in public (TESTING / BETA / CALIBRATED, not retired). The one predicate behind bot_distribution, the simulated_bets public-read policy, the bots distribution trigger and the public pick views.';

CREATE OR REPLACE FUNCTION public.bot_pending_public(p_label text, p_retired_at timestamptz,
                                                     p_vip boolean, p_hide_pending boolean)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT public.bot_public_status(p_label, p_retired_at)
         AND NOT coalesce(p_vip, false) AND NOT coalesce(p_hide_pending, false)
$$;
COMMENT ON FUNCTION public.bot_pending_public(text, timestamptz, boolean, boolean) IS
  '#155: may the PUBLIC see this bot''s PENDING picks? Public status, not VIP (paid channel), not a VIP twin (hide_pending).';

GRANT EXECUTE ON FUNCTION public.bot_public_status(text, timestamptz) TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.bot_pending_public(text, timestamptz, boolean, boolean) TO anon, authenticated, service_role;

-- A new bot starts admin-only. (The old default 'active' is not even an allowed value.)
ALTER TABLE bots ALTER COLUMN maturity_label SET DEFAULT 'experimental';

-- ── 2. bot_distribution on the one predicate (columns unchanged) ───────────────────────────────
-- pending_exposed: this bot's pending picks must not be public, AND the anon policy no longer
-- routes through bot_pending_public (someone replaced it) — i.e. they may be readable. With the
-- policy below it is FALSE for every bot; smoke RLS-EXPERIMENTAL-PENDING-HIDDEN also proves it
-- live by counting pending rows AS the anon role.
CREATE OR REPLACE VIEW public.bot_distribution AS
WITH s AS (
    SELECT b.name AS bot_name,
           b.display_name,
           CASE WHEN b.retired_at IS NOT NULL OR b.maturity_label = 'retired' THEN 'retired'
                ELSE coalesce(b.maturity_label, 'experimental') END AS status,
           coalesce(b.vip, false) AS vip,
           coalesce(b.hide_pending, false) AS hide_pending,
           b.is_active,
           public.bot_public_status(b.maturity_label, b.retired_at) AS public_status,
           public.bot_pending_public(b.maturity_label, b.retired_at, b.vip, b.hide_pending) AS pending_ok
      FROM bots b
)
SELECT s.bot_name,
       s.display_name,
       s.status,
       s.vip,
       s.is_active,
       CASE WHEN s.vip THEN 'VIP · ' || upper(s.status) ELSE upper(s.status) END AS label,
       s.public_status                                   AS on_performance,
       (s.public_status AND NOT s.vip)                   AS sent_public,
       (s.public_status AND s.vip)                       AS vip_channel,
       s.pending_ok                                      AS pending_public,
       (s.status IN ('beta','calibrated') AND NOT s.vip) AS in_headline,
       s.public_status                                   AS in_active_record,
       (s.status <> 'retired' AND NOT s.pending_ok
        AND NOT EXISTS (SELECT 1 FROM pg_policy pp
                         WHERE pp.polrelid = 'public.simulated_bets'::regclass
                           AND pp.polname = 'Public read'
                           AND pg_get_expr(pp.polqual, pp.polrelid) LIKE '%bot_pending_public%')) AS pending_exposed
  FROM s;

-- ── 3. show_on_picks / show_on_performance are DERIVED from status ─────────────────────────────
CREATE OR REPLACE FUNCTION public.bots_derive_distribution() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_pub   boolean := public.bot_public_status(NEW.maturity_label, NEW.retired_at);
    v_picks boolean := v_pub AND NOT coalesce(NEW.vip, false);
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.show_on_picks IS DISTINCT FROM OLD.show_on_picks AND NEW.show_on_picks IS DISTINCT FROM v_picks THEN
            RAISE EXCEPTION 'bots.show_on_picks is derived from the bot''s status (#155): % is %, so show_on_picks = %. Change the status (maturity_label) instead.',
                NEW.name, coalesce(NEW.maturity_label, 'experimental'), v_picks USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.show_on_performance IS DISTINCT FROM OLD.show_on_performance AND NEW.show_on_performance IS DISTINCT FROM v_pub THEN
            RAISE EXCEPTION 'bots.show_on_performance is derived from the bot''s status (#155): % is %, so show_on_performance = %. Change the status (maturity_label) instead.',
                NEW.name, coalesce(NEW.maturity_label, 'experimental'), v_pub USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    NEW.show_on_picks := v_picks;
    NEW.show_on_performance := v_pub;
    RETURN NEW;
END $$;

-- "zz": fires AFTER bots_maturity_retired_invariant (alphabetical), so a retirement's label is set first.
DROP TRIGGER IF EXISTS bots_zz_derive_distribution ON bots;
CREATE TRIGGER bots_zz_derive_distribution BEFORE INSERT OR UPDATE ON bots
    FOR EACH ROW EXECUTE FUNCTION public.bots_derive_distribution();

COMMENT ON COLUMN bots.show_on_picks IS
  '#155 DERIVED (trigger bots_zz_derive_distribution): TRUE iff status is TESTING/BETA/CALIBRATED, not retired, not VIP (= bot_distribution.sent_public). Setting it against the status raises. Change maturity_label instead.';
COMMENT ON COLUMN bots.show_on_performance IS
  '#155 DERIVED (trigger bots_zz_derive_distribution): TRUE iff status is TESTING/BETA/CALIBRATED, not retired (= bot_distribution.on_performance). Setting it against the status raises.';

-- ── 4. Owner statuses (#155, 2026-09-25) ──────────────────────────────────────────────────────
-- Verified before: bot_consensus_b_v1 was 'beta' (owner: TESTING, n 3), bot_consensus_d_v1
-- 'testing' but never sent (owner: EXPERIMENTAL), the two VIP bots 'experimental' (owner:
-- "VIP · TESTING"). The rest already matched and are restated so the migration is the record.
UPDATE bots SET maturity_label = 'calibrated'   WHERE name = 'bot_v10_1x2'             AND retired_at IS NULL;
UPDATE bots SET maturity_label = 'beta'         WHERE name = 'bot_high_roi_global_v2'  AND retired_at IS NULL;
UPDATE bots SET maturity_label = 'testing'
 WHERE name IN ('bot_sharp_1x2_v1', 'bot_sharp_ou_v1', 'bot_consensus_c_v1', 'bot_consensus_b_v1',
                'bot_v10_1x2_newplus_v1', 'bot_combined_1x2_ev5_v1', 'bot_ou_sharp_early_v1')
   AND retired_at IS NULL;
UPDATE bots SET maturity_label = 'experimental' WHERE name = 'bot_consensus_d_v1'      AND retired_at IS NULL;

-- Re-derive every row (no explicit show_* in the SET, so nothing raises).
UPDATE bots SET maturity_label = maturity_label;

-- ── 5. anon RLS: pending picks only for bots whose status sends them ───────────────────────────
DROP POLICY IF EXISTS "Public read" ON simulated_bets;
CREATE POLICY "Public read" ON simulated_bets FOR SELECT USING (
    result IS DISTINCT FROM 'pending'::bet_result
    OR (EXISTS (SELECT 1 FROM bots b
                 WHERE b.id = simulated_bets.bot_id
                   AND public.bot_pending_public(b.maturity_label, b.retired_at, b.vip, b.hide_pending))
        AND NOT COALESCE(held_back_until > now(), false))
);

-- ── 6. Public pick views gate on the status (columns unchanged; #164 hold-back kept) ───────────
-- Forward-test rows: the arm → bot mapping is the one below (workers/utils/bot_status.py
-- forward_test_bot mirrors it). A row is public when its bot's status sends picks, OR it was
-- already sent (telegram_message_id) — "anything sent is counted", so a pick that reached the
-- channel before a demotion stays in the record (grade D posts published as C before #098).
CREATE OR REPLACE VIEW picks_public_all AS
 SELECT p.id,
    'sharp'::text AS edge_kind,
    fb.bot,
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
     CROSS JOIN LATERAL (SELECT CASE
            WHEN p.arm = 'consensus_anchor'::text AND p.grade = 'D'::text THEN 'bot_consensus_d_v1'::text
            WHEN p.arm = 'consensus_anchor'::text AND p.grade = 'C'::text THEN 'bot_consensus_c_v1'::text
            WHEN p.arm = 'consensus_anchor'::text THEN 'bot_consensus_b_v1'::text
            WHEN p.arm = 'live'::text AND p.market = 'over_under_25'::text THEN 'bot_sharp_ou_v1'::text
            ELSE 'bot_sharp_1x2_v1'::text
        END AS bot) fb
     LEFT JOIN bot_distribution bd ON bd.bot_name = fb.bot
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE (p.arm = ANY (ARRAY['live'::text, 'consensus_anchor'::text])) AND m.status <> 'postponed'::match_status
    AND (COALESCE(bd.sent_public, false) OR p.telegram_message_id IS NOT NULL)
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
     JOIN bot_distribution bd ON bd.bot_name = b.name
     JOIN matches m ON m.id = s.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE bd.sent_public
    AND (s.result IS DISTINCT FROM 'pending'::bet_result OR bd.pending_public)
    AND s.combo_legs IS NULL AND s.match_minute_at_pick IS NULL
    AND m.status <> 'postponed'::match_status
    AND NOT COALESCE(s.held_back_until > now(), false);

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
     CROSS JOIN LATERAL (SELECT CASE
            WHEN p.arm = 'consensus_anchor'::text AND p.grade = 'D'::text THEN 'bot_consensus_d_v1'::text
            WHEN p.arm = 'consensus_anchor'::text AND p.grade = 'C'::text THEN 'bot_consensus_c_v1'::text
            WHEN p.arm = 'consensus_anchor'::text THEN 'bot_consensus_b_v1'::text
            WHEN p.arm = 'live'::text AND p.market = 'over_under_25'::text THEN 'bot_sharp_ou_v1'::text
            ELSE 'bot_sharp_1x2_v1'::text
        END AS bot) fb
     LEFT JOIN bot_distribution bd ON bd.bot_name = fb.bot
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
     LEFT JOIN picks_forward_test_record_leg r ON r.id = p.id
  WHERE (p.arm = ANY (ARRAY['live'::text, 'consensus_anchor'::text]))
    AND (COALESCE(bd.sent_public, false) OR p.telegram_message_id IS NOT NULL)
    AND NOT COALESCE(p.held_back_until > now(), false);

COMMIT;
