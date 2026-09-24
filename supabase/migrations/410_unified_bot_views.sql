-- 410 — #139 UNIFIED-BOT-MODEL phase 1 (2026-09-24): one ledger, one scoreboard, one config
-- table and one capability view across every bot, whatever table it happens to write.
--
-- WHY. A bot today is one of three kinds by the table it writes — simulated_bets (the model
-- bots behind /picks), shadow_bets (paper bots, plus the pipeline's timing-cohort copies) and
-- picks_forward_test (the pre-registered sharp / consensus test and its junk-anchor control).
-- Every page read a different subset, so the same bot showed a different n / ROI / CLV on
-- each (docs/BOTS_AUDIT_2026_09_24.md). The contract for these objects is
-- docs/UNIFIED_BOT_MODEL_DESIGN_2026_09_24.md § "Data contract — phase 1"; this migration
-- implements it. No writer changes, no behaviour change: read-only views + one config table
-- written by scripts/export_bot_config.py (daily job_export_bot_config, 03:40 UTC).
--
-- RULES (contract):
--   * sim rows exclude combos (combo_legs IS NOT NULL).
--   * ONE LEDGER PER BOT (ANALYSIS_GOTCHAS §18). A bot that writes simulated_bets contributes
--     NO shadow rows. Every other bot contributes ALL its shadow rows, whatever the cohort
--     tag, deduped per (bot_id, match_id, market, selection) with the earliest pick_time
--     winning (shadow_bets_unique's key and order). The HHMM timing cohorts are NOT excluded:
--     for shadow-only bots (sweep, pin, coolbet_value, the dc family, ...) they are the bot's
--     ONLY record — excluding them lost 11,447 unique picks across 37 bots (genesis research,
--     dev/active/unified-bot-model-genesis/ledgers.md). For sim-writing bots the timing
--     copies are already gone through the rule below.
--   * a bot that writes simulated_bets contributes NO shadow rows. Before 2026-05-20 the
--     pipeline's timing copies were tagged 'morning' / 'midday' / 'pre_ko' rather than HHMM,
--     and 398 of the 531 such rows are exact copies of the bot's own sim pick (review of this
--     migration, 2026-09-24: bot_aggressive read 1,006 picks for 713 real ones). 'morning'
--     cannot simply join the regex — it is also the ONLY record of shadow-only bots (no_pin,
--     sweep, pin_1x2, coolbet_value; 2,278 rows) — so the exclusion is by bot, not by tag.
--   * is_inplay for sim rows: match minute, OR xg_source set (only ever set by the in-play
--     bots — verified 2026-09-24: 0 of 3,183 non-inplay rows carry it), OR an inplay_* bot;
--     865 retired inplay_* rows carry no match minute.
--   * CLV statistics skip |clv| > 1 (a price more than double / less than nothing against
--     the close is a mislabelled line or a stale quote, not an edge — ANALYSIS_GOTCHAS §9);
--     the skipped count is shown as clv_outlier_n so nothing disappears silently.
--   * forward-test arms map to bot names exactly as picks_public_all does (migration 402);
--     arm 'junk_anchor' -> 'control_junk_anchor'. ALL rows are kept (the ledger is what the
--     bot did) — picks_public_all's display filters (postponed, unsent grade D) are not
--     applied here. Pre-registered rows are read, never re-labelled or re-scored.
--   * void rows stay (counted as void). pnl_unit is a flat 1-unit stake so every bot is
--     comparable regardless of its own stake scheme.
--
-- ACCESS: admin only — service_role, NOT anon / authenticated (#072, migrations 404/405).

BEGIN;

-- ── bot_config ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.bot_config (
    bot_name            text PRIMARY KEY,
    family              text NOT NULL,
    description         text,
    ledger              text,
    writer_job          text,
    cadence             text,
    markets             text[],
    prob_source         text,
    edge_floor          text,
    edge_floor_source   text,
    odds_min            numeric,
    odds_max            numeric,
    gates               jsonb NOT NULL DEFAULT '[]'::jsonb,
    books               text[],
    books_source        text,
    anchor              text,
    placeable           boolean NOT NULL DEFAULT false,
    published           boolean NOT NULL DEFAULT false,
    telegram            boolean NOT NULL DEFAULT false,
    admissible_metric   text,
    exported_at         timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.bot_config IS
  '#139: one row per bot (active, retired, forward-test arms, control) exported daily from the code that actually runs by scripts/export_bot_config.py. gates = [{name, value, source}]. family = ''unknown'' when unresolvable — never silently missing.';

-- ── bot_ledger ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW public.bot_ledger AS
SELECT 'sim'::text                                   AS source,
       s.id                                          AS pick_id,
       b.name                                        AS bot_name,
       s.bot_id                                      AS bot_id,
       s.match_id                                    AS match_id,
       m.date                                        AS kickoff,
       s.pick_time                                   AS pick_time,
       s.market                                      AS market,
       s.selection                                   AS selection,
       s.odds_at_pick                                AS odds,
       s.recommended_bookmaker                       AS bookmaker,
       s.result::text                                AS result,
       CASE s.result::text
            WHEN 'won'  THEN s.odds_at_pick - 1
            WHEN 'lost' THEN -1::numeric
            ELSE 0::numeric END                      AS pnl_unit,
       s.clv                                         AS clv_raw,
       NULL::numeric                                 AS clv_mc,
       s.clv_pinnacle_devig::numeric                 AS clv_pinnacle,
       (s.match_minute_at_pick IS NOT NULL OR s.xg_source IS NOT NULL OR b.name LIKE 'inplay\_%')
                                                     AS is_inplay,
       s.model_version                               AS model_version,
       NULL::text                                    AS rule_version
  FROM simulated_bets s
  JOIN bots b    ON b.id = s.bot_id
  JOIN matches m ON m.id = s.match_id
 WHERE s.combo_legs IS NULL
UNION ALL
SELECT 'shadow'::text,
       sh.id,
       b.name,
       sh.bot_id,
       sh.match_id,
       m.date,
       sh.pick_time,
       sh.market,
       sh.selection,
       sh.odds_at_pick,
       sh.recommended_bookmaker,
       sh.result::text,
       CASE sh.result::text
            WHEN 'won'  THEN sh.odds_at_pick - 1
            WHEN 'lost' THEN -1::numeric
            ELSE 0::numeric END,
       sh.clv,
       sh.clv_margin_corrected,
       sh.clv_pinnacle::numeric,
       (sh.inplay_minute IS NOT NULL),
       sh.model_version,
       NULL::text
  FROM (SELECT DISTINCT ON (x.bot_id, x.match_id, x.market, x.selection) x.*
          FROM shadow_bets x
         WHERE NOT EXISTS (SELECT 1 FROM simulated_bets s2 WHERE s2.bot_id = x.bot_id)
         ORDER BY x.bot_id, x.match_id, x.market, x.selection, x.pick_time) sh
  JOIN bots b    ON b.id = sh.bot_id
  JOIN matches m ON m.id = sh.match_id
UNION ALL
SELECT 'forward_test'::text,
       p.id,
       CASE
            WHEN p.arm = 'junk_anchor' THEN 'control_junk_anchor'
            WHEN p.arm = 'consensus_anchor' AND p.grade = 'D' THEN 'bot_consensus_d_v1'
            WHEN p.arm = 'consensus_anchor' AND p.grade = 'C' THEN 'bot_consensus_c_v1'
            WHEN p.arm = 'consensus_anchor' THEN 'bot_consensus_b_v1'
            WHEN p.arm = 'live' AND p.market = 'over_under_25' THEN 'bot_sharp_ou_v1'
            ELSE 'bot_sharp_1x2_v1'
       END,
       NULL::uuid,
       p.match_id,
       m.date,
       p.published_at,
       p.market,
       p.selection,
       p.odds,
       p.bookmaker,
       CASE WHEN p.outcome IS NULL THEN 'pending'
            ELSE p.outcome END,   -- an unexpected outcome is shown as itself, never folded into void
       CASE p.outcome
            WHEN 'won'  THEN p.odds - 1
            WHEN 'lost' THEN -1::numeric
            ELSE 0::numeric END,
       p.clv,
       p.clv_margin_corrected,
       NULL::numeric,
       false,
       NULL::text,
       p.rule_version
  FROM picks_forward_test p
  JOIN matches m ON m.id = p.match_id
 WHERE p.arm IN ('live', 'consensus_anchor', 'junk_anchor');

COMMENT ON VIEW public.bot_ledger IS
  '#139: one row per pick across simulated_bets (no combos), shadow_bets (only for bots with no simulated_bets; every cohort, deduped per bot/match/market/selection, earliest wins) and picks_forward_test (arms mapped as picks_public_all; junk_anchor -> control_junk_anchor). pnl_unit is flat 1-unit. Contract: docs/UNIFIED_BOT_MODEL_DESIGN_2026_09_24.md.';

-- ── bot_scoreboard ─────────────────────────────────────────────────────────────────────────
-- t = mean / (sd / sqrt(n)), NULL below n = 2. CLV is taken over non-void rows only.
-- `source` is every ledger source the bot's rows come from, '+'-joined. Under the
-- one-ledger-per-bot rule it is always a single value; a '+' would mean the rule broke.
-- PRE-REGISTRATION (genesis ledgers.md invariant 5; migration 346): a rule change starts a
-- new population, never a continuation, so a forward-test / control bot is scored on its
-- CURRENT rule_version only (the one on its latest pick). Earlier versions stay in bot_ledger
-- and are counted in earlier_version_picks, never pooled. This also drops the 8
-- '+DEGENERATE_JUNK_DAY1' control rows (migration 343), which copy the live arm.
CREATE OR REPLACE VIEW public.bot_scoreboard AS
WITH cur AS (
    SELECT DISTINCT ON (bot_name) bot_name, rule_version
      FROM public.bot_ledger
     WHERE source = 'forward_test'
     ORDER BY bot_name, pick_time DESC
), older AS (
    SELECT l.bot_name, count(*) AS earlier_version_picks
      FROM public.bot_ledger l JOIN cur c ON c.bot_name = l.bot_name
     WHERE l.source = 'forward_test' AND l.rule_version IS DISTINCT FROM c.rule_version
     GROUP BY l.bot_name
), agg AS (
    SELECT l.bot_name,
           max(c.rule_version)                                                     AS scored_rule_version,
           string_agg(DISTINCT l.source, '+' ORDER BY l.source)                    AS source,
           count(*)                                                                AS picks_total,
           count(*) FILTER (WHERE l.result = 'pending')                            AS pending,
           count(*) FILTER (WHERE l.result IN ('won', 'lost'))                     AS settled,
           count(*) FILTER (WHERE l.result = 'won')                                AS won,
           count(*) FILTER (WHERE l.result = 'lost')                               AS lost,
           count(*) FILTER (WHERE l.result = 'void')                               AS void,
           sum(l.pnl_unit) FILTER (WHERE l.result IN ('won', 'lost'))              AS pnl_settled,
           count(l.clv_mc) FILTER (WHERE l.result <> 'void' AND abs(l.clv_mc) <= 1)             AS clv_mc_n,
           avg(l.clv_mc) FILTER (WHERE l.result <> 'void' AND abs(l.clv_mc) <= 1)               AS clv_mc_mean,
           stddev_samp(l.clv_mc) FILTER (WHERE l.result <> 'void' AND abs(l.clv_mc) <= 1)       AS clv_mc_sd,
           count(l.clv_pinnacle) FILTER (WHERE l.result <> 'void' AND abs(l.clv_pinnacle) <= 1) AS clv_pin_n,
           avg(l.clv_pinnacle) FILTER (WHERE l.result <> 'void' AND abs(l.clv_pinnacle) <= 1)   AS clv_pin_mean,
           stddev_samp(l.clv_pinnacle) FILTER (WHERE l.result <> 'void' AND abs(l.clv_pinnacle) <= 1) AS clv_pin_sd,
           count(*) FILTER (WHERE l.result <> 'void'
                              AND (abs(l.clv_mc) > 1 OR abs(l.clv_pinnacle) > 1))   AS clv_outlier_n,
           min(l.pick_time)                                                        AS first_pick_at,
           max(l.pick_time)                                                        AS last_pick_at,
           count(*) FILTER (WHERE l.pick_time >= now() - interval '7 days')        AS picks_7d,
           count(*) FILTER (WHERE l.result IN ('won', 'lost')
                              AND l.kickoff >= now() - interval '7 days')         AS settled_7d
      FROM public.bot_ledger l
      LEFT JOIN cur c ON c.bot_name = l.bot_name
     WHERE l.source <> 'forward_test' OR l.rule_version = c.rule_version
     GROUP BY l.bot_name
)
SELECT a.bot_name,
       b.display_name,
       a.source,
       a.scored_rule_version,
       COALESCE(o.earlier_version_picks, 0)                                        AS earlier_version_picks,
       b.is_active,
       b.retired_at,
       b.maturity_label,
       COALESCE(c.family, 'unknown')                                               AS family,
       a.picks_total,
       a.pending,
       a.settled,
       a.won,
       a.lost,
       a.void,
       CASE WHEN a.settled > 0 THEN round(a.pnl_settled / a.settled, 6) END        AS roi_unit,
       a.clv_mc_n,
       a.clv_mc_mean,
       CASE WHEN a.clv_mc_n >= 2 THEN a.clv_mc_sd / sqrt(a.clv_mc_n) END           AS clv_mc_se,
       CASE WHEN a.clv_mc_n >= 2 AND a.clv_mc_sd > 0
            THEN a.clv_mc_mean / (a.clv_mc_sd / sqrt(a.clv_mc_n)) END              AS clv_mc_t,
       a.clv_pin_n,
       a.clv_pin_mean,
       CASE WHEN a.clv_pin_n >= 2 THEN a.clv_pin_sd / sqrt(a.clv_pin_n) END        AS clv_pin_se,
       CASE WHEN a.clv_pin_n >= 2 AND a.clv_pin_sd > 0
            THEN a.clv_pin_mean / (a.clv_pin_sd / sqrt(a.clv_pin_n)) END           AS clv_pin_t,
       a.clv_outlier_n,
       a.first_pick_at,
       a.last_pick_at,
       a.picks_7d,
       a.settled_7d
  FROM agg a
  LEFT JOIN older o               ON o.bot_name = a.bot_name
  LEFT JOIN bots b                ON b.name = a.bot_name
  LEFT JOIN public.bot_config c   ON c.bot_name = a.bot_name;

COMMENT ON VIEW public.bot_scoreboard IS
  '#139: one row per bot_name over bot_ledger. roi_unit = sum(pnl_unit)/settled (flat stake); clv_*_t = mean/(sd/sqrt(n)), NULL below n=2. The verdict metric per bot is bot_config.admissible_metric (by family).';

-- ── bot_capabilities ───────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW public.bot_capabilities AS
WITH names AS (
    SELECT name AS bot_name FROM bots
    UNION
    SELECT bot_name FROM public.bot_config
), recent AS (
    -- retired bots keep writing ON PURPOSE (owner decisions 2026-05-20 SHADOW-RETIRED-OK and
    -- 2026-09-18 paper modules) — shown as "retired but still collecting", not as a bug.
    SELECT DISTINCT bot_name FROM public.bot_ledger WHERE pick_time >= now() - interval '7 days'
), fleet AS (
    SELECT bool_or(placement_paused) AS placement_paused,
           bool_or(real_money_armed) AS real_money_armed
      FROM coolbet_session_state
)
SELECT n.bot_name,
       COALESCE(b.is_active AND b.retired_at IS NULL, c.bot_name IS NOT NULL)      AS collect,
       COALESCE(c.published, b.show_on_picks, false)                               AS publish,
       COALESCE(c.telegram, false)                                                 AS telegram,
       COALESCE(c.placeable, false)                                                AS place_capable,
       COALESCE(pb.ui_place_enabled, false)                                        AS place_enabled,
       COALESCE(f.placement_paused, false)                                         AS fleet_placement_paused,
       COALESCE(f.real_money_armed, false)                                         AS fleet_real_money_armed,
       (r.bot_name IS NOT NULL)                                                    AS writing_7d
  FROM names n
  LEFT JOIN bots b                   ON b.name = n.bot_name
  LEFT JOIN public.bot_config c      ON c.bot_name = n.bot_name
  LEFT JOIN coolbet_placer_bots pb   ON pb.bot_name = n.bot_name
  LEFT JOIN recent r                 ON r.bot_name = n.bot_name
  CROSS JOIN fleet f;

COMMENT ON VIEW public.bot_capabilities IS
  '#139: what each bot is allowed to do today. collect = is_active AND retired_at IS NULL (names without a bots row, e.g. control_junk_anchor, count as collecting when exported); publish = bot_config.published else bots.show_on_picks; place_enabled = coolbet_placer_bots.ui_place_enabled; fleet_* = coolbet_session_state; writing_7d = has bot_ledger picks in the last 7 days (retired bots may keep collecting on purpose). Phase 3 moves these behind one switch.';

-- ── access: admin only ─────────────────────────────────────────────────────────────────────
-- Default privileges hand service_role ALL on new relations; revoke to read-only first.
REVOKE ALL ON public.bot_config, public.bot_ledger, public.bot_scoreboard, public.bot_capabilities
  FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON public.bot_config, public.bot_ledger, public.bot_scoreboard, public.bot_capabilities
  TO service_role;

COMMIT;
