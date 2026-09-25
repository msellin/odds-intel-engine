-- 454 — #162 W6.3 + W5.4 (audits C-K3, C-K4, B-R5), 2026-09-26:
--   ONE sharp-anchor CLV rule, and ONE forward-test arm registry.
--
-- WHY (W6.3). "Which close is a forward-test leg judged against?" was answered by the same CASE
-- copied into ten places: views 430 (picks_forward_test_anchor_clv), 431 (_bot_record), 433/446
-- (bot_ledger, three branches x two CASEs) and scripts/picks_forward_test_checkpoint.py (twice).
-- The rule — the de-vigged Pinnacle close when leg_clv_sharp.status = 'ok', else the >=5-book
-- consensus close when cons_status = 'ok', the 3-4-book thin consensus NEVER — is the
-- pre-registered #156 AMENDMENT 1 stop rule. A copy that drifted would quietly re-cut a running
-- pre-registered test. It now lives in anchor_source(); anchor_clv() and anchor_p_close() only
-- pick the matching column. Two value functions and not one because the copies really compute
-- two different things and both must stay bit-identical:
--   * anchor_clv    — the STORED clv_sharp / clv_cons (430, 431, the checkpoint). Stored values are
--                     rounded by the writer, so recomputing odds x p_close - 1 differs in the
--                     16th digit on 742 of 750 legs and would move the published averages.
--   * anchor_p_close — the anchor close PROBABILITY, which bot_ledger multiplies by the price it
--                     re-bases on (public / own / published odds).
--
-- WHY (W5.4). "Which arms are published, which is the control, which bot owns a leg" was a
-- hard-coded ARRAY['live','consensus_anchor'] in eight views, a third member in bot_ledger, three
-- copies of the arm -> bot CASE (clv_sharp_legs had a fourth, divergent one), the arm CHECK on the
-- ledger, and six Python maps. Adding the two #161 twin arms needed edits in all of them. Now:
--   forward_test_arms      — one row per arm: role, published, in_bot_ledger, rule_version.
--   forward_test_arm_bots  — arm (+ optional grade / market) -> bot name. NULL = any.
--   forward_test_leg_arm   — per ledger row: its arm's flags + its bot (most specific match wins).
-- Every view below reads those; picks_forward_test.arm gets an FK to the registry, replacing the
-- CHECK list, so an arm cannot exist without a row here. The Python maps
-- (publish_picks_forward_test PUBLISHED_ARMS / TWIN_ARMS / ARM_RULE_VERSION, vip_guard,
-- bot_status.forward_test_bot, the checkpoint's TWINS / CONTROL_ARM, export_bot_config) are
-- PINNED equal to the seed below by smoke FORWARD-TEST-ARM-REGISTRY rather than rewritten to read
-- it — the publisher is pre-registered and its behaviour must not depend on a DB read.
--
-- PRE-REGISTERED: every published number is unchanged. The seed is EXACTLY today's values, the
-- views are CREATE OR REPLACE from their live pg_get_viewdef text with only the copies swapped, and
-- the migration was dry-run in BEGIN ... ROLLBACK against production with every recreated view
-- and every view downstream of one hashed row-for-row before and after (0 differences). Grants and
-- options are kept by CREATE OR REPLACE.
--
-- ONE deliberate semantic change, zero rows affected: clv_sharp_legs labelled a consensus leg with
-- a NULL grade 'consensus_ungraded' while every other copy (and the publisher's own
-- bot_status.forward_test_bot, which decides whether it is SENT) calls it bot_consensus_b_v1 — the
-- D7 mismatch in the audit. One map now; grade is NOT NULL on every consensus row today.
-- The twin arms are NOT given bot rows: clv_sharp_legs labels unpublished arms by the arm name
-- (junk_anchor, sharp_own_book_aligned, ...) and that is kept as is.

SET lock_timeout = '3s';

-- One transaction: migrate.yml runs psql per file without --single-transaction, and a half-applied
-- set of these views (some reading the registry, some not) must never be live.
BEGIN;

-- ── W6.3: the sharp-anchor rule ──────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.anchor_source(p_status text, p_cons_status text)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN p_status = 'ok' THEN 'pinnacle'::text
                WHEN p_cons_status = 'ok' THEN 'consensus'::text END
$$;
COMMENT ON FUNCTION public.anchor_source(text, text) IS
    'The sharp anchor a leg is judged against: pinnacle (leg_clv_sharp.status=ok) else consensus (>=5 books, cons_status=ok) else NULL. Thin consensus never. #156 AMENDMENT 1; migration 454.';

CREATE OR REPLACE FUNCTION public.anchor_clv(p_status text, p_cons_status text,
                                             p_clv_sharp numeric, p_clv_cons numeric)
RETURNS numeric LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE public.anchor_source(p_status, p_cons_status)
                WHEN 'pinnacle' THEN p_clv_sharp
                WHEN 'consensus' THEN p_clv_cons END
$$;
COMMENT ON FUNCTION public.anchor_clv(text, text, numeric, numeric) IS
    'Stored sharp-anchor CLV of a leg (clv_sharp or clv_cons per anchor_source). Used by picks_forward_test_anchor_clv, _bot_record and scripts/picks_forward_test_checkpoint.py. Migration 454.';

CREATE OR REPLACE FUNCTION public.anchor_p_close(p_status text, p_cons_status text,
                                                 p_close numeric, p_close_cons numeric)
RETURNS numeric LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE public.anchor_source(p_status, p_cons_status)
                WHEN 'pinnacle' THEN p_close
                WHEN 'consensus' THEN p_close_cons END
$$;
COMMENT ON FUNCTION public.anchor_p_close(text, text, numeric, numeric) IS
    'Sharp-anchor close probability of a leg (p_close or p_close_cons per anchor_source); bot_ledger multiplies it by the price it re-bases on. Migration 454.';

GRANT EXECUTE ON FUNCTION public.anchor_source(text, text),
                          public.anchor_clv(text, text, numeric, numeric),
                          public.anchor_p_close(text, text, numeric, numeric) TO service_role;

-- ── W5.4: the arm registry ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.forward_test_arms (
    arm            text PRIMARY KEY,
    role           text NOT NULL CHECK (role IN ('published', 'control', 'twin')),
    published      boolean NOT NULL,   -- reaches Telegram, /picks, /performance, the public views
    in_bot_ledger  boolean NOT NULL,   -- carried by bot_ledger (published arms + the control)
    rule_version   text NOT NULL,      -- the rule the publisher writes today (ARM_RULE_VERSION)
    parent_arm     text REFERENCES public.forward_test_arms(arm),  -- a twin's parent
    note           text,
    CHECK (published = (role = 'published')),
    CHECK ((role = 'twin') = (parent_arm IS NOT NULL))
);
-- exactly one negative control: bot_ledger's in_record rule looks it up by role
CREATE UNIQUE INDEX IF NOT EXISTS forward_test_arms_one_control
    ON public.forward_test_arms ((true)) WHERE role = 'control';

CREATE TABLE IF NOT EXISTS public.forward_test_arm_bots (
    arm       text NOT NULL REFERENCES public.forward_test_arms(arm),
    grade     text,          -- NULL = any grade
    market    text,          -- NULL = any market
    bot_name  text NOT NULL,
    UNIQUE NULLS NOT DISTINCT (arm, grade, market)
);

INSERT INTO public.forward_test_arms (arm, role, published, in_bot_ledger, rule_version, parent_arm, note) VALUES
    ('live',                    'published', true,  true,  'sharp_edge_v4_2026_09_15',                NULL,
     'the sharp-anchor picks (bot_sharp_1x2_v1 / bot_sharp_ou_v1)'),
    ('consensus_anchor',        'published', true,  true,  'consensus_edge_v2_2026_09_24',            NULL,
     'the consensus-anchor picks, split by grade (bot_consensus_{b,c,d}_v1)'),
    ('junk_anchor',             'control',   false, true,  'sharp_edge_v4_2026_09_15',                NULL,
     'negative control: the live rule priced against a random anchor; never sent'),
    ('sharp_own_book_aligned',  'twin',      false, false, 'sharp_edge_v4_ownbook_align5_2026_09_25', 'live',
     '#161 twin: live + own-book quote alignment <= 5 min; recorded, never published'),
    ('consensus_pin_confirmed', 'twin',      false, false, 'consensus_edge_v2_pinconf_2026_09_25',    'consensus_anchor',
     '#161 twin: consensus + EV >= 0 against a tight Pinnacle anchor; recorded, never published')
ON CONFLICT (arm) DO NOTHING;

INSERT INTO public.forward_test_arm_bots (arm, grade, market, bot_name) VALUES
    ('live',             NULL, 'over_under_25', 'bot_sharp_ou_v1'),
    ('live',             NULL, NULL,            'bot_sharp_1x2_v1'),
    ('consensus_anchor', 'D',  NULL,            'bot_consensus_d_v1'),
    ('consensus_anchor', 'C',  NULL,            'bot_consensus_c_v1'),
    ('consensus_anchor', 'B',  NULL,            'bot_consensus_b_v1'),
    ('consensus_anchor', NULL, NULL,            'bot_consensus_b_v1'),
    ('junk_anchor',      NULL, NULL,            'control_junk_anchor')
ON CONFLICT DO NOTHING;

-- The ledger's arm list is the registry now (was the CHECK picks_forward_test_arm_check).
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'picks_forward_test_arm_registered') THEN
        ALTER TABLE public.picks_forward_test ADD CONSTRAINT picks_forward_test_arm_registered
            FOREIGN KEY (arm) REFERENCES public.forward_test_arms(arm);
    END IF;
END $$;
ALTER TABLE public.picks_forward_test DROP CONSTRAINT IF EXISTS picks_forward_test_arm_check;

-- Per ledger row: its arm's flags and the bot that owns it (most specific registry row wins).
CREATE OR REPLACE VIEW public.forward_test_leg_arm AS
SELECT p.id AS leg_id,
       p.arm,
       fa.role,
       fa.published,
       fa.in_bot_ledger,
       ( SELECT ab.bot_name
           FROM public.forward_test_arm_bots ab
          WHERE ab.arm = p.arm
            AND (ab.grade IS NULL OR ab.grade = p.grade)
            AND (ab.market IS NULL OR ab.market = p.market)
          ORDER BY (ab.grade IS NOT NULL) DESC, (ab.market IS NOT NULL) DESC
          LIMIT 1) AS bot
  FROM public.picks_forward_test p
  JOIN public.forward_test_arms fa ON fa.arm = p.arm;

-- The registry is changed by migration only. The default privileges would hand `authenticated`
-- write access to the new tables; a signed-in user must not be able to publish an arm.
REVOKE ALL ON public.forward_test_arms, public.forward_test_arm_bots, public.forward_test_leg_arm
    FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.forward_test_arms, public.forward_test_arm_bots
    FROM service_role;
GRANT SELECT ON public.forward_test_arms, public.forward_test_arm_bots, public.forward_test_leg_arm
    TO service_role;

-- ── The views: live definitions, copies swapped. Dependency order (arm_rule -> record_leg ->
-- bot_record / public / bot_ledger). Column lists are unchanged, so CREATE OR REPLACE keeps every
-- grant and every dependent view (bot_performance, bot_public_record, ... are untouched).

-- picks_forward_test_arm_rule — 431: the newest rule_version per PUBLISHED arm
CREATE OR REPLACE VIEW public.picks_forward_test_arm_rule AS
 SELECT DISTINCT ON (arm) arm,
    rule_version AS current_rule_version
   FROM picks_forward_test
  WHERE arm IN (SELECT fta.arm FROM forward_test_arms fta WHERE fta.published)
  ORDER BY arm, published_at DESC;

-- picks_forward_test_record_leg — 431: which rule a published leg counts under
CREATE OR REPLACE VIEW public.picks_forward_test_record_leg AS
 SELECT p.id,
    p.arm,
    p.grade,
    p.market,
    p.rule_version,
    p.published_at,
    p.outcome,
    p.pnl,
    p.clv_margin_corrected,
    cur.current_rule_version,
        CASE
            WHEN p.rule_version = cur.current_rule_version THEN 'native'::text
            WHEN rc.passes IS TRUE THEN 'rechecked_pass'::text
            WHEN rc.passes IS FALSE THEN 'rechecked_fail'::text
            ELSE 'earlier'::text
        END AS record_state,
        CASE
            WHEN p.rule_version <> cur.current_rule_version AND rc.passes IS TRUE THEN cur.current_rule_version
            ELSE p.rule_version
        END AS record_rule_version
   FROM picks_forward_test p
     JOIN picks_forward_test_arm_rule cur ON cur.arm = p.arm
     LEFT JOIN pick_rule_recheck rc ON rc.pick_id = p.id AND rc.checked_rule_version = cur.current_rule_version
  WHERE (p.arm IN (SELECT fta.arm FROM forward_test_arms fta WHERE fta.published)) AND NOT (NOT p.grade IS DISTINCT FROM 'D'::text AND p.telegram_message_id IS NULL);

-- picks_forward_test_anchor_clv — 430: sharp-anchor CLV per published arm/grade/market/rule
CREATE OR REPLACE VIEW public.picks_forward_test_anchor_clv AS
 WITH legs AS (
         SELECT p.arm,
            p.grade,
            p.market,
            p.rule_version,
            p.published_at,
                anchor_clv(c.status, c.cons_status, c.clv_sharp, c.clv_cons) AS clv_anchor,
                anchor_source(c.status, c.cons_status) AS anchor_source,
            p.clv_margin_corrected
           FROM picks_forward_test p
             LEFT JOIN leg_clv_sharp c ON c.ledger = 'picks_forward_test'::text AND c.leg_id = p.id
          WHERE (p.arm IN (SELECT fta.arm FROM forward_test_arms fta WHERE fta.published)) AND NOT (NOT p.grade IS DISTINCT FROM 'D'::text AND p.telegram_message_id IS NULL) AND (p.outcome = ANY (ARRAY['won'::text, 'lost'::text]))
        )
 SELECT arm,
    grade,
    market,
    rule_version,
    min(published_at) AS started_at,
    count(*) AS settled,
    count(clv_anchor) FILTER (WHERE abs(clv_anchor) <= 1::numeric) AS n_anchor,
    count(*) FILTER (WHERE anchor_source = 'pinnacle'::text AND abs(clv_anchor) <= 1::numeric) AS n_pinnacle,
    count(*) FILTER (WHERE anchor_source = 'consensus'::text AND abs(clv_anchor) <= 1::numeric) AS n_consensus,
    avg(clv_anchor) FILTER (WHERE abs(clv_anchor) <= 1::numeric) AS clv_anchor,
    stddev_samp(clv_anchor) FILTER (WHERE abs(clv_anchor) <= 1::numeric) AS clv_anchor_sd,
    count(clv_margin_corrected) FILTER (WHERE abs(clv_margin_corrected) <= 1::numeric) AS n_clv_mc,
    avg(clv_margin_corrected) FILTER (WHERE abs(clv_margin_corrected) <= 1::numeric) AS clv_margin_corrected
   FROM legs
  GROUP BY arm, grade, market, rule_version;

-- picks_forward_test_bot_record — 431: the per-bot record /performance reads
CREATE OR REPLACE VIEW public.picks_forward_test_bot_record AS
 WITH legs AS (
         SELECT r.id,
            r.arm,
            r.grade,
            r.market,
            r.rule_version,
            r.published_at,
            r.outcome,
            r.pnl,
            r.clv_margin_corrected,
            r.current_rule_version,
            r.record_state,
            r.record_rule_version,
                anchor_clv(c.status, c.cons_status, c.clv_sharp, c.clv_cons) AS clv_anchor,
                anchor_source(c.status, c.cons_status) AS anchor_source,
            r.outcome = ANY (ARRAY['won'::text, 'lost'::text]) AS is_settled
           FROM picks_forward_test_record_leg r
             LEFT JOIN leg_clv_sharp c ON c.ledger = 'picks_forward_test'::text AND c.leg_id = r.id
        )
 SELECT arm,
    grade,
    market,
    record_rule_version,
    record_state,
    record_rule_version = current_rule_version AS is_current,
    min(published_at) AS started_at,
    count(*) AS published,
    count(*) FILTER (WHERE outcome IS NULL) AS pending,
    count(*) FILTER (WHERE is_settled) AS settled,
    count(*) FILTER (WHERE outcome = 'won'::text) AS won,
    COALESCE(sum(pnl) FILTER (WHERE is_settled), 0::numeric) AS pnl_units,
    count(clv_anchor) FILTER (WHERE is_settled AND abs(clv_anchor) <= 1::numeric) AS n_anchor,
    count(*) FILTER (WHERE is_settled AND anchor_source = 'pinnacle'::text AND abs(clv_anchor) <= 1::numeric) AS n_pinnacle,
    count(*) FILTER (WHERE is_settled AND anchor_source = 'consensus'::text AND abs(clv_anchor) <= 1::numeric) AS n_consensus,
    avg(clv_anchor) FILTER (WHERE is_settled AND abs(clv_anchor) <= 1::numeric) AS clv_anchor,
    count(clv_margin_corrected) FILTER (WHERE is_settled AND abs(clv_margin_corrected) <= 1::numeric) AS n_clv_mc,
    avg(clv_margin_corrected) FILTER (WHERE is_settled AND abs(clv_margin_corrected) <= 1::numeric) AS clv_margin_corrected
   FROM legs
  GROUP BY arm, grade, market, record_rule_version, record_state, current_rule_version;

-- picks_forward_test_summary — public (anon): per rule/arm/grade
CREATE OR REPLACE VIEW public.picks_forward_test_summary AS
 SELECT rule_version,
    min(published_at) AS started_at,
    count(*) AS published,
    count(*) FILTER (WHERE outcome IS NULL) AS pending,
    count(*) FILTER (WHERE outcome = ANY (ARRAY['push'::text, 'void'::text])) AS refunded,
    count(*) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS settled,
    count(*) FILTER (WHERE outcome = 'won'::text) AS won,
    sum(pnl) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS pnl_units,
    avg(pnl) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS roi,
    stddev_samp(pnl) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS roi_sd,
    count(clv) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS n_clv,
    avg(clv) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS clv_raw,
    count(clv_margin_corrected) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS n_clv_mc,
    avg(clv_margin_corrected) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS clv_margin_corrected,
    stddev_samp(clv_margin_corrected) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS clv_mc_sd,
    arm,
    grade
   FROM picks_forward_test
  WHERE (arm IN (SELECT fta.arm FROM forward_test_arms fta WHERE fta.published)) AND NOT (NOT grade IS DISTINCT FROM 'D'::text AND telegram_message_id IS NULL)
  GROUP BY rule_version, arm, grade;

-- picks_forward_test_summary_by_market — public (anon): per rule/arm/grade/market
CREATE OR REPLACE VIEW public.picks_forward_test_summary_by_market AS
 SELECT rule_version,
    min(published_at) AS started_at,
    count(*) AS published,
    count(*) FILTER (WHERE outcome IS NULL) AS pending,
    count(*) FILTER (WHERE outcome = ANY (ARRAY['push'::text, 'void'::text])) AS refunded,
    count(*) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS settled,
    count(*) FILTER (WHERE outcome = 'won'::text) AS won,
    sum(pnl) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS pnl_units,
    avg(pnl) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS roi,
    stddev_samp(pnl) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS roi_sd,
    count(clv) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS n_clv,
    avg(clv) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS clv_raw,
    count(clv_margin_corrected) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS n_clv_mc,
    avg(clv_margin_corrected) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS clv_margin_corrected,
    stddev_samp(clv_margin_corrected) FILTER (WHERE outcome = ANY (ARRAY['won'::text, 'lost'::text])) AS clv_mc_sd,
    arm,
    grade,
    market
   FROM picks_forward_test
  WHERE (arm IN (SELECT fta.arm FROM forward_test_arms fta WHERE fta.published)) AND NOT (NOT grade IS DISTINCT FROM 'D'::text AND telegram_message_id IS NULL)
  GROUP BY rule_version, arm, grade, market;

-- picks_forward_test_public — public (anon): the published forward-test legs
CREATE OR REPLACE VIEW public.picks_forward_test_public AS
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
     JOIN forward_test_leg_arm fb ON fb.leg_id = p.id
     LEFT JOIN bot_distribution bd ON bd.bot_name = fb.bot
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
     LEFT JOIN picks_forward_test_record_leg r ON r.id = p.id
  WHERE fb.published AND (COALESCE(bd.sent_public, false) OR p.telegram_message_id IS NOT NULL) AND NOT COALESCE(p.held_back_until > now(), false);

-- picks_public_all — public (anon): /picks feed, sharp + model edges
CREATE OR REPLACE VIEW public.picks_public_all AS
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
     JOIN forward_test_leg_arm fb ON fb.leg_id = p.id
     LEFT JOIN bot_distribution bd ON bd.bot_name = fb.bot
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN leagues l ON l.id = m.league_id
     LEFT JOIN teams ht ON ht.id = m.home_team_id
     LEFT JOIN teams at ON at.id = m.away_team_id
  WHERE fb.published AND m.status <> 'postponed'::match_status AND (COALESCE(bd.sent_public, false) OR p.telegram_message_id IS NOT NULL) AND NOT COALESCE(p.held_back_until > now(), false)
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
  WHERE bd.sent_public AND (s.result IS DISTINCT FROM 'pending'::bet_result OR bd.pending_public) AND s.combo_legs IS NULL AND s.match_minute_at_pick IS NULL AND m.status <> 'postponed'::match_status AND NOT COALESCE(s.held_back_until > now(), false);

-- clv_sharp_legs — admin: every leg with a sharp close; unpublished arms keep their arm name as bot
CREATE OR REPLACE VIEW public.clv_sharp_legs AS
 WITH legs AS (
         SELECT c.ledger,
            c.leg_id,
            c.match_id,
            c.market,
            c.selection,
            c.odds,
            c.odds_basis,
            c.p_close,
            c.close_age_min,
            c.clv_sharp,
                CASE
                    WHEN fb.published THEN fb.bot
                    ELSE p.arm
                END AS bot,
            p.arm,
            p.grade,
            p.rule_version AS version,
            p.bookmaker,
            p.published_at AS decided_at,
            EXTRACT(epoch FROM p.published_at - p.odds_quoted_at) / 60.0 AS quote_age_min
           FROM leg_clv_sharp c
             JOIN picks_forward_test p ON p.id = c.leg_id
             JOIN forward_test_leg_arm fb ON fb.leg_id = p.id
          WHERE c.ledger = 'picks_forward_test'::text AND c.status = 'ok'::text
        UNION ALL
         SELECT c.ledger,
            c.leg_id,
            c.match_id,
            c.market,
            c.selection,
            c.odds,
            c.odds_basis,
            c.p_close,
            c.close_age_min,
            c.clv_sharp,
            b.name,
            NULL::text AS text,
            NULL::text AS text,
            s.model_version,
            s.recommended_bookmaker,
            s.pick_time,
            s.decision_quote_age_min
           FROM leg_clv_sharp c
             JOIN shadow_bets s ON s.id = c.leg_id
             JOIN bots b ON b.id = s.bot_id
          WHERE c.ledger = 'shadow_bets'::text AND c.status = 'ok'::text
        UNION ALL
         SELECT c.ledger,
            c.leg_id,
            c.match_id,
            c.market,
            c.selection,
            c.odds,
            c.odds_basis,
            c.p_close,
            c.close_age_min,
            c.clv_sharp,
            b.name,
            NULL::text AS text,
            NULL::text AS text,
            s.model_version,
            s.recommended_bookmaker,
            s.pick_time,
            NULL::numeric AS "numeric"
           FROM leg_clv_sharp c
             JOIN simulated_bets s ON s.id = c.leg_id
             JOIN bots b ON b.id = s.bot_id
          WHERE c.ledger = 'simulated_bets'::text AND c.status = 'ok'::text
        )
 SELECT l.ledger,
    l.leg_id,
    l.match_id,
    l.market,
    l.selection,
    l.odds,
    l.odds_basis,
    l.p_close,
    l.close_age_min,
    l.clv_sharp,
    l.bot,
    l.arm,
    l.grade,
    l.version,
    l.bookmaker,
    l.decided_at,
    l.quote_age_min,
    m.date AS kickoff,
    l.decided_at::date AS decided_day,
        CASE
            WHEN l.bookmaker = ANY (ARRAY['Coolbet'::text, 'Epicbet'::text, 'Unibet-Site'::text, 'Tonybet'::text, 'Optibet'::text, 'Paf'::text, 'Olybet'::text]) THEN 'scraped'::text
            WHEN l.bookmaker = 'Unibet-Kambi'::text THEN 'kambi'::text
            WHEN l.bookmaker = 'Pinnacle'::text THEN 'sharp'::text
            WHEN l.bookmaker IS NULL THEN 'unknown'::text
            ELSE 'af_fed'::text
        END AS book_feed,
        CASE
            WHEN l.market = '1x2'::text THEN '1x2'::text
            WHEN l.market ~~ 'corners%'::text THEN 'corners'::text
            WHEN l.market ~~ 'over_under_1h%'::text OR l.market ~~ '%_1h%'::text THEN 'first_half'::text
            WHEN l.market ~~ 'over_under_%'::text THEN 'over_under'::text
            WHEN l.market = ANY (ARRAY['double_chance'::text, 'draw_no_bet'::text]) THEN l.market
            WHEN l.market ~~ 'team_total%'::text THEN 'team_total'::text
            WHEN l.market ~~ 'corners%'::text THEN 'corners'::text
            ELSE 'other'::text
        END AS market_group,
        CASE
            WHEN l.odds < 1.60 THEN '<1.60'::text
            WHEN l.odds < 2.00 THEN '1.60-2.00'::text
            WHEN l.odds < 2.50 THEN '2.00-2.50'::text
            WHEN l.odds < 3.00 THEN '2.50-3.00'::text
            WHEN l.odds < 4.00 THEN '3.00-4.00'::text
            ELSE '4.00+'::text
        END AS odds_band,
        CASE
            WHEN (m.date - l.decided_at) < '01:00:00'::interval THEN '<1h'::text
            WHEN (m.date - l.decided_at) < '03:00:00'::interval THEN '1-3h'::text
            WHEN (m.date - l.decided_at) < '06:00:00'::interval THEN '3-6h'::text
            WHEN (m.date - l.decided_at) < '12:00:00'::interval THEN '6-12h'::text
            WHEN (m.date - l.decided_at) < '24:00:00'::interval THEN '12-24h'::text
            ELSE '24h+'::text
        END AS ttk_bucket,
        CASE
            WHEN l.quote_age_min IS NULL THEN 'unknown'::text
            WHEN l.quote_age_min <= 60::numeric THEN 'fresh'::text
            ELSE 'stale'::text
        END AS quote_freshness,
    lg.tier AS league_tier,
    row_number() OVER (PARTITION BY l.ledger, l.bot, l.match_id, l.market, l.selection ORDER BY l.decided_at, l.leg_id) AS dup_rank
   FROM legs l
     JOIN matches m ON m.id = l.match_id
     LEFT JOIN leagues lg ON lg.id = m.league_id
  WHERE l.bookmaker IS DISTINCT FROM 'api-football-live'::text;

-- bot_ledger — 433/446: every pick of every bot; forward-test branch = published arms + the control
CREATE OR REPLACE VIEW public.bot_ledger AS
 SELECT 'sim'::text AS source,
    s.id AS pick_id,
    b.name AS bot_name,
    s.bot_id,
    s.match_id,
    m.date AS kickoff,
    s.pick_time,
    s.market,
    s.selection,
    s.odds_at_pick AS odds,
    s.recommended_bookmaker AS bookmaker,
    s.result::text AS result,
        CASE s.result::text
            WHEN 'won'::text THEN s.odds_at_pick - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit,
    s.clv AS clv_raw,
    NULL::numeric AS clv_mc,
    s.clv_pinnacle_devig::numeric AS clv_pinnacle,
    s.match_minute_at_pick IS NOT NULL OR s.xg_source IS NOT NULL OR b.name ~~ 'inplay\_%'::text AS is_inplay,
    s.model_version,
    NULL::text AS rule_version,
    x.odds_public,
    x.public_basis,
        CASE s.result::text
            WHEN 'won'::text THEN x.odds_public - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit_public,
    x.odds_own,
    x.own_basis,
        CASE s.result::text
            WHEN 'won'::text THEN x.odds_own - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit_own,
    s.stake::numeric AS stake,
        CASE s.result::text
            WHEN 'won'::text THEN (x.odds_public - 1::numeric) * s.stake
            WHEN 'lost'::text THEN - s.stake
            ELSE 0::numeric
        END AS pnl_staked_public,
    x.odds_public * a.p_close - 1::numeric AS clv_anchor_public,
    x.odds_own * a.p_close - 1::numeric AS clv_anchor_own,
    a.anchor_source AS clv_anchor_source,
    true AS in_record,
    'counted'::text AS record_state,
    NULL::text AS record_rule_version,
    COALESCE(s.calibrated_prob, s.model_probability)::numeric AS model_prob,
    s.edge_percent::numeric AS edge,
    s.strategy_profile
   FROM simulated_bets s
     JOIN bots b ON b.id = s.bot_id
     JOIN matches m ON m.id = s.match_id
     CROSS JOIN LATERAL ( SELECT s.match_minute_at_pick IS NOT NULL OR s.xg_source IS NOT NULL OR b.name ~~ 'inplay\_%'::text AS inplay) ip
     CROSS JOIN LATERAL ( SELECT
                CASE
                    WHEN ip.inplay THEN s.odds_at_pick
                    WHEN s.odds_at_pick_available > 1::numeric THEN s.odds_at_pick_available
                    WHEN s.odds_at_pick_live > 1::numeric THEN s.odds_at_pick_live
                    ELSE s.odds_at_pick
                END AS odds_public,
                CASE
                    WHEN ip.inplay THEN 'inplay'::text
                    WHEN s.odds_at_pick_available > 1::numeric THEN 'available'::text
                    WHEN s.odds_at_pick_live > 1::numeric THEN 'our_books'::text
                    ELSE 'recorded'::text
                END AS public_basis,
                CASE
                    WHEN ip.inplay THEN s.odds_at_pick
                    WHEN s.odds_at_pick_live > 1::numeric THEN s.odds_at_pick_live
                    ELSE s.odds_at_pick
                END AS odds_own,
                CASE
                    WHEN ip.inplay THEN 'inplay'::text
                    WHEN s.odds_at_pick_live > 1::numeric THEN 'our_books'::text
                    ELSE 'recorded'::text
                END AS own_basis) x
     LEFT JOIN leg_clv_sharp c ON c.ledger = 'simulated_bets'::text AND c.leg_id = s.id
     CROSS JOIN LATERAL ( SELECT
                CASE
                    WHEN s.match_minute_at_pick IS NOT NULL OR s.xg_source IS NOT NULL OR b.name ~~ 'inplay\_%'::text THEN NULL::numeric
                    ELSE anchor_p_close(c.status, c.cons_status, c.p_close, c.p_close_cons)
                END AS p_close,
                CASE
                    WHEN s.match_minute_at_pick IS NOT NULL OR s.xg_source IS NOT NULL OR b.name ~~ 'inplay\_%'::text THEN NULL::text
                    ELSE anchor_source(c.status, c.cons_status)
                END AS anchor_source) a
  WHERE s.combo_legs IS NULL
UNION ALL
 SELECT 'shadow'::text AS source,
    sh.id AS pick_id,
    b.name AS bot_name,
    sh.bot_id,
    sh.match_id,
    m.date AS kickoff,
    sh.pick_time,
    sh.market,
    sh.selection,
    sh.odds_at_pick AS odds,
    sh.recommended_bookmaker AS bookmaker,
    sh.result::text AS result,
        CASE sh.result::text
            WHEN 'won'::text THEN sh.odds_at_pick - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit,
    sh.clv AS clv_raw,
    sh.clv_margin_corrected AS clv_mc,
    sh.clv_pinnacle::numeric AS clv_pinnacle,
    sh.inplay_minute IS NOT NULL AS is_inplay,
    sh.model_version,
    NULL::text AS rule_version,
    x.odds_public,
    x.public_basis,
        CASE sh.result::text
            WHEN 'won'::text THEN x.odds_public - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit_public,
    x.odds_own,
    x.own_basis,
        CASE sh.result::text
            WHEN 'won'::text THEN x.odds_own - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit_own,
    sh.stake::numeric AS stake,
        CASE sh.result::text
            WHEN 'won'::text THEN (x.odds_public - 1::numeric) * sh.stake
            WHEN 'lost'::text THEN - sh.stake
            ELSE 0::numeric
        END AS pnl_staked_public,
    x.odds_public * a.p_close - 1::numeric AS clv_anchor_public,
    x.odds_own * a.p_close - 1::numeric AS clv_anchor_own,
    a.anchor_source AS clv_anchor_source,
    true AS in_record,
    'counted'::text AS record_state,
    NULL::text AS record_rule_version,
    COALESCE(sh.calibrated_prob, sh.model_probability)::numeric AS model_prob,
    sh.edge_percent::numeric AS edge,
    sh.strategy_profile
   FROM ( SELECT DISTINCT ON (x_1.bot_id, x_1.match_id, x_1.market, x_1.selection) x_1.id,
            x_1.shadow_run_id,
            x_1.shadow_cohort,
            x_1.bot_id,
            x_1.match_id,
            x_1.market,
            x_1.selection,
            x_1.odds_at_pick,
            x_1.pick_time,
            x_1.stake,
            x_1.model_probability,
            x_1.calibrated_prob,
            x_1.edge_percent,
            x_1.recommended_bookmaker,
            x_1.kelly_fraction,
            x_1.timing_cohort,
            x_1.model_version,
            x_1.closing_odds,
            x_1.clv,
            x_1.result,
            x_1.pnl,
            x_1.created_at,
            x_1.meta_clv_score,
            x_1.strategy_profile,
            x_1.void_reason,
            x_1.clv_pinnacle,
            x_1.closing_bookmaker,
            x_1.pair_gap_hours,
            x_1.odds_at_pick_live,
            x_1.odds_at_pick_available,
            x_1.clv_live,
            x_1.clv_pinnacle_live,
            x_1.closing_margin,
            x_1.clv_margin_corrected,
            x_1.decision_quote_age_min,
            x_1.inplay_minute,
            x_1.inplay_score_home,
            x_1.inplay_score_away,
            x_1.closing_minutes_before_ko
           FROM shadow_bets x_1
          WHERE NOT (EXISTS ( SELECT 1
                   FROM simulated_bets s2
                  WHERE s2.bot_id = x_1.bot_id))
          ORDER BY x_1.bot_id, x_1.match_id, x_1.market, x_1.selection, x_1.pick_time) sh
     JOIN bots b ON b.id = sh.bot_id
     JOIN matches m ON m.id = sh.match_id
     CROSS JOIN LATERAL ( SELECT
                CASE
                    WHEN sh.inplay_minute IS NOT NULL THEN sh.odds_at_pick
                    WHEN sh.odds_at_pick_available > 1::numeric THEN sh.odds_at_pick_available
                    WHEN sh.odds_at_pick_live > 1::numeric THEN sh.odds_at_pick_live
                    ELSE sh.odds_at_pick
                END AS odds_public,
                CASE
                    WHEN sh.inplay_minute IS NOT NULL THEN 'inplay'::text
                    WHEN sh.odds_at_pick_available > 1::numeric THEN 'available'::text
                    WHEN sh.odds_at_pick_live > 1::numeric THEN 'our_books'::text
                    ELSE 'recorded'::text
                END AS public_basis,
                CASE
                    WHEN sh.inplay_minute IS NOT NULL THEN sh.odds_at_pick
                    WHEN sh.odds_at_pick_live > 1::numeric THEN sh.odds_at_pick_live
                    ELSE sh.odds_at_pick
                END AS odds_own,
                CASE
                    WHEN sh.inplay_minute IS NOT NULL THEN 'inplay'::text
                    WHEN sh.odds_at_pick_live > 1::numeric THEN 'our_books'::text
                    ELSE 'recorded'::text
                END AS own_basis) x
     LEFT JOIN leg_clv_sharp c ON c.ledger = 'shadow_bets'::text AND c.leg_id = sh.id
     CROSS JOIN LATERAL ( SELECT
                CASE
                    WHEN sh.inplay_minute IS NOT NULL THEN NULL::numeric
                    ELSE anchor_p_close(c.status, c.cons_status, c.p_close, c.p_close_cons)
                END AS p_close,
                CASE
                    WHEN sh.inplay_minute IS NOT NULL THEN NULL::text
                    ELSE anchor_source(c.status, c.cons_status)
                END AS anchor_source) a
UNION ALL
 SELECT 'forward_test'::text AS source,
    p.id AS pick_id,
        fb.bot AS bot_name,
    NULL::uuid AS bot_id,
    p.match_id,
    m.date AS kickoff,
    p.published_at AS pick_time,
    p.market,
    p.selection,
    p.odds,
    p.bookmaker,
        CASE
            WHEN p.outcome IS NULL THEN 'pending'::text
            ELSE p.outcome
        END AS result,
        CASE p.outcome
            WHEN 'won'::text THEN p.odds - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit,
    p.clv AS clv_raw,
    p.clv_margin_corrected AS clv_mc,
    NULL::numeric AS clv_pinnacle,
    false AS is_inplay,
    NULL::text AS model_version,
    p.rule_version,
    p.odds AS odds_public,
    'published'::text AS public_basis,
        CASE p.outcome
            WHEN 'won'::text THEN p.odds - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit_public,
    p.odds AS odds_own,
    'published'::text AS own_basis,
        CASE p.outcome
            WHEN 'won'::text THEN p.odds - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_unit_own,
    1::numeric AS stake,
        CASE p.outcome
            WHEN 'won'::text THEN p.odds - 1::numeric
            WHEN 'lost'::text THEN - 1::numeric
            ELSE 0::numeric
        END AS pnl_staked_public,
    p.odds *
        anchor_p_close(c.status, c.cons_status, c.p_close, c.p_close_cons) - 1::numeric AS clv_anchor_public,
    p.odds *
        anchor_p_close(c.status, c.cons_status, c.p_close, c.p_close_cons) - 1::numeric AS clv_anchor_own,
        anchor_source(c.status, c.cons_status) AS clv_anchor_source,
        CASE
            WHEN fb.role = 'control'::text THEN p.rule_version = jr.rule_version
            ELSE COALESCE(r.record_state = ANY (ARRAY['native'::text, 'rechecked_pass'::text]), false)
        END AS in_record,
        CASE
            WHEN fb.role = 'control'::text THEN
            CASE
                WHEN p.rule_version = jr.rule_version THEN 'native'::text
                ELSE 'earlier'::text
            END
            ELSE COALESCE(r.record_state, 'unsent'::text)
        END AS record_state,
        CASE
            WHEN fb.role = 'control'::text THEN jr.rule_version
            ELSE r.current_rule_version
        END AS record_rule_version,
    p.p_sharp AS model_prob,
    p.edge,
    NULL::text AS strategy_profile
   FROM picks_forward_test p
     JOIN forward_test_leg_arm fb ON fb.leg_id = p.id AND fb.in_bot_ledger
     JOIN matches m ON m.id = p.match_id
     LEFT JOIN picks_forward_test_record_leg r ON r.id = p.id
     LEFT JOIN leg_clv_sharp c ON c.ledger = 'picks_forward_test'::text AND c.leg_id = p.id
     LEFT JOIN LATERAL ( SELECT j.rule_version
           FROM picks_forward_test j
          WHERE j.arm = (( SELECT fc.arm FROM forward_test_arms fc WHERE fc.role = 'control'::text))
          ORDER BY j.published_at DESC
         LIMIT 1) jr ON true;

COMMIT;
