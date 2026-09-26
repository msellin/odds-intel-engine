-- 462 — [[#175]] ONE PROVEN STATUS: BETA + CALIBRATED merged into ACTIVE (owner decision 2026-09-26).
--
-- WHY. BETA and CALIBRATED differed in nothing a reader saw or received — same /picks, same
-- /performance listing, same headline totals, same public Telegram. Only a badge colour, one
-- legacy dashboard stat and the operator's Coolbet summary told them apart. Two words for one
-- meaning made /performance harder to read, and the owner wants readers to see plainly which
-- bots count toward the totals. The lifecycle is now
--     EXPERIMENTAL → TESTING → ACTIVE → RETIRED      (⭐ VIP a channel on top)
--   ACTIVE  = /picks + own record + COUNTS IN THE HEADLINE TOTALS + public Telegram
--   TESTING = /picks + own record, NOT in the totals (Telegram only at EV >= 5%, #174)
-- Promotion TESTING → ACTIVE: 50 settled picks with sharp-anchor CLV > 0 (docs/SYSTEM_MAP.md).
--
-- HISTORY (so the relabel stays readable): before this migration the two public-headline bots were
--     bot_v10_1x2             maturity_label = 'calibrated'
--     bot_high_roi_global_v2  maturity_label = 'beta'
-- Both become 'active'. 'active' was NOT in use (checked 2026-09-26: 0 rows); the pre-taxonomy
-- 'active' label was retired by migration 279 (MOVE-ACTIVE-TO-BETA) and dropped from the CHECK by
-- MATURITY-LABEL-CANONICAL, so this reuses the word with the new meaning above.
--
-- ORDER MATTERS: the derive-distribution trigger (442) recomputes show_on_picks/show_on_performance
-- from bot_public_status() on every UPDATE, so the predicate is widened to 'active' BEFORE rows move.
-- Lock taken up front (migration 442 deadlocked against the live scheduler).

BEGIN;

LOCK TABLE bots IN SHARE ROW EXCLUSIVE MODE;

ALTER TABLE bots DROP CONSTRAINT IF EXISTS bots_maturity_label_check;

-- 1. The one predicate: public = TESTING or ACTIVE, not retired.
CREATE OR REPLACE FUNCTION public.bot_public_status(p_label text, p_retired_at timestamptz)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT p_retired_at IS NULL AND coalesce(p_label, 'experimental') IN ('testing','active')
$$;
COMMENT ON FUNCTION public.bot_public_status(text, timestamptz) IS
  '#155/#175: TRUE when a bot''s STATUS puts it in public (TESTING / ACTIVE, not retired). The one predicate behind bot_distribution, the simulated_bets public-read policy, the bots distribution trigger and the public pick views.';

-- 2. Relabel.
UPDATE bots SET maturity_label = 'active' WHERE maturity_label IN ('beta', 'calibrated');

-- 3. The CHECK now allows only the merged lifecycle.
ALTER TABLE bots ADD CONSTRAINT bots_maturity_label_check CHECK (
  maturity_label IS NULL OR maturity_label IN ('experimental', 'testing', 'active', 'retired'));

COMMENT ON COLUMN bots.maturity_label IS
  'THE bot status (#155, merged #175 2026-09-26): experimental (admins only) → testing (/picks + own record, not in totals) → active (/picks + own record + headline totals) → retired. VIP is a channel on top (bots.vip). show_on_picks/show_on_performance are derived from it by trigger. beta/calibrated were merged into active by migration 462.';

-- 4. Headline = ACTIVE only (columns unchanged).
CREATE OR REPLACE VIEW public.bot_distribution AS
WITH s AS (
         SELECT b.name AS bot_name,
            b.display_name,
                CASE
                    WHEN b.retired_at IS NOT NULL OR b.maturity_label = 'retired'::text THEN 'retired'::text
                    ELSE COALESCE(b.maturity_label, 'experimental'::text)
                END AS status,
            COALESCE(b.vip, false) AS vip,
            COALESCE(b.hide_pending, false) AS hide_pending,
            b.is_active,
            bot_public_status(b.maturity_label, b.retired_at) AS public_status,
            bot_pending_public(b.maturity_label, b.retired_at, b.vip, b.hide_pending) AS pending_ok
           FROM bots b
        )
 SELECT bot_name,
    display_name,
    status,
    vip,
    is_active,
        CASE
            WHEN vip THEN 'VIP · '::text || upper(status)
            ELSE upper(status)
        END AS label,
    public_status AS on_performance,
    public_status AND NOT vip AS sent_public,
    public_status AND vip AS vip_channel,
    pending_ok AS pending_public,
    status = 'active'::text AND NOT vip AS in_headline,
    public_status AS in_active_record,
    status <> 'retired'::text AND NOT pending_ok AND NOT (EXISTS ( SELECT 1
           FROM pg_policy pp
          WHERE pp.polrelid = 'simulated_bets'::regclass::oid AND pp.polname = 'Public read'::name AND pg_get_expr(pp.polqual, pp.polrelid) ~~ '%bot_pending_public%'::text)) AS pending_exposed
   FROM s;

-- 5. /performance "work done" counts on the same two public statuses.
CREATE OR REPLACE VIEW public.bot_public_work_done AS
WITH r AS MATERIALIZED (
         SELECT bot_public_record.bot_name,
            bot_public_record.display_name,
            bot_public_record.public_group,
            bot_public_record.public_group_title,
            bot_public_record.is_retired,
            bot_public_record.retired_at,
            bot_public_record.retired_reason,
            bot_public_record.maturity_label,
            bot_public_record.vip,
            bot_public_record.markets,
            bot_public_record.picks_total,
            bot_public_record.pending,
            bot_public_record.settled,
            bot_public_record.won,
            bot_public_record.lost,
            bot_public_record.void,
            bot_public_record.pnl_units_public,
            bot_public_record.roi_public,
            bot_public_record.n_public_recorded,
            bot_public_record.clv_public,
            bot_public_record.clv_n,
            bot_public_record.clv_n_pinnacle,
            bot_public_record.clv_n_consensus,
            bot_public_record.first_pick_at,
            bot_public_record.last_pick_at,
            bot_public_record.n_swap_window,
            bot_public_record.n_ou_calbug,
            bot_public_record.n_pre_mid_july,
            bot_public_record.is_representative
           FROM bot_public_record
        ), sel AS (
         SELECT count(DISTINCT ROW(bot_ledger.match_id, bot_ledger.market, bot_ledger.selection)) AS n
           FROM bot_ledger
          WHERE bot_ledger.in_record
        )
 SELECT count(*) AS strategies,
    count(*) FILTER (WHERE r.is_retired) AS retired,
    count(*) FILTER (WHERE NOT r.is_retired AND (r.maturity_label = ANY (ARRAY['testing'::text, 'active'::text]))) AS public_active,
    count(*) FILTER (WHERE NOT r.is_retired AND (r.maturity_label <> ALL (ARRAY['testing'::text, 'active'::text]))) AS not_public_active,
    sum(r.picks_total) AS picks_total,
    sum(r.settled) AS settled,
    sum(r.picks_total) FILTER (WHERE r.is_retired) AS retired_picks,
    max(sel.n) AS distinct_selections,
    min(r.first_pick_at) AS since,
    sum(r.clv_public * r.clv_n::numeric) FILTER (WHERE r.public_group = 'trigger_model'::text AND r.clv_n > 0) / NULLIF(sum(r.clv_n) FILTER (WHERE r.public_group = 'trigger_model'::text), 0::numeric) AS lesson_model_clv,
    sum(r.clv_n) FILTER (WHERE r.public_group = 'trigger_model'::text) AS lesson_model_n,
    sum(r.clv_public * r.clv_n::numeric) FILTER (WHERE r.public_group = 'trigger_sharp'::text AND r.clv_n > 0) / NULLIF(sum(r.clv_n) FILTER (WHERE r.public_group = 'trigger_sharp'::text), 0::numeric) AS lesson_sharp_clv,
    sum(r.clv_n) FILTER (WHERE r.public_group = 'trigger_sharp'::text) AS lesson_sharp_n
   FROM r
     CROSS JOIN sel;

COMMIT;
