-- 411 — #139 UNIFIED-BOT-MODEL phase 1, /admin/bots redesign data (2026-09-24).
--
-- WHY. The redesigned /admin/bots (dev/active/bots-board-ux-spec.md §9) needs two things
-- migration 410 does not give it:
--   D1  bot_weekly          — a 12-week activity strip per bot: picks, settled, and the sign
--                             of that week's admissible CLV, so the operator sees activity
--                             and direction at a glance.
--   D3  bot_market_stats    — per (bot, market) settled / won / break-even and mc-CLV
--                             stats. Two uses: (a) the forward test is compared with the
--                             junk control on the SAME market mix (the control is 1x2 + O/U;
--                             bot_sharp_ou_v1 is O/U only, so the pooled control mean is the
--                             wrong reference), and (b) in-play bots show hit rate next to
--                             the break-even hit rate (mean 1/odds) — a 78% hit rate at
--                             odds <= 2.20 is not, by itself, good.
--   D2  bot_ledger_display  — bot_ledger + home/away team names for the drawer's recent-picks
--                             table (it showed an 8-char match UUID). PostgREST cannot embed
--                             across a view (views carry no foreign keys), so the join lives
--                             here. The bot_ledger contract itself is NOT changed.
--
-- RULES (same as bot_scoreboard, migration 410 — the strip must never disagree with the row):
--   * PRE-REGISTRATION: a forward-test / control bot counts ONLY rows of its CURRENT
--     rule_version (the one on its latest pick). Earlier versions are a different population
--     and are never pooled (migration 346, genesis ledgers.md invariant 5).
--   * CLV aggregates skip void rows and |clv| > 1 (ANALYSIS_GOTCHAS §9), exactly as the
--     scoreboard does.
--   * window: the current ISO week plus the 11 before it (date_trunc('week') = Monday UTC).
--   * in-play bots carry CLV columns here like any bot; the page never renders CLV for the
--     in-play family (honesty rule 2) — that is a display rule, not a data rule.
--
-- ACCESS: admin only — service_role, NOT anon / authenticated (#072, migrations 404/405/410).
-- Idempotent: CREATE OR REPLACE + REVOKE/GRANT.

BEGIN;

-- ── bot_weekly ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW public.bot_weekly AS
WITH cur AS (
    SELECT DISTINCT ON (bot_name) bot_name, rule_version
      FROM public.bot_ledger
     WHERE source = 'forward_test'
     ORDER BY bot_name, pick_time DESC
)
SELECT l.bot_name,
       date_trunc('week', l.pick_time)                                                      AS week,
       count(*)                                                                             AS picks,
       count(*) FILTER (WHERE l.result IN ('won', 'lost'))                                  AS settled,
       count(l.clv_mc)       FILTER (WHERE l.result <> 'void' AND abs(l.clv_mc) <= 1)       AS clv_mc_n,
       avg(l.clv_mc)         FILTER (WHERE l.result <> 'void' AND abs(l.clv_mc) <= 1)       AS clv_mc_mean,
       count(l.clv_pinnacle) FILTER (WHERE l.result <> 'void' AND abs(l.clv_pinnacle) <= 1) AS clv_pin_n,
       avg(l.clv_pinnacle)   FILTER (WHERE l.result <> 'void' AND abs(l.clv_pinnacle) <= 1) AS clv_pin_mean,
       sum(l.pnl_unit)                                                                      AS pnl_unit
  FROM public.bot_ledger l
  LEFT JOIN cur c ON c.bot_name = l.bot_name
 WHERE l.pick_time >= date_trunc('week', now()) - interval '11 weeks'
   -- pre-registered bots: current rule_version only, same as bot_scoreboard (never pooled)
   AND (l.source <> 'forward_test' OR l.rule_version = c.rule_version)
 GROUP BY l.bot_name, date_trunc('week', l.pick_time);

COMMENT ON VIEW public.bot_weekly IS
  '#139: per-bot ISO-week series over bot_ledger for the /admin/bots 12-week strip. Forward-test / control bots: current rule_version only (as bot_scoreboard). CLV means skip void and |clv|>1.';

-- ── bot_market_stats ───────────────────────────────────────────────────────────────────────
-- Same population rules as bot_scoreboard: current rule_version only for pre-registered bots;
-- CLV skips void and |clv| > 1. sum_inv_odds / odds_n over settled picks = break-even hit rate.
CREATE OR REPLACE VIEW public.bot_market_stats AS
WITH cur AS (
    SELECT DISTINCT ON (bot_name) bot_name, rule_version
      FROM public.bot_ledger
     WHERE source = 'forward_test'
     ORDER BY bot_name, pick_time DESC
)
SELECT l.bot_name,
       l.market,
       count(*) FILTER (WHERE l.result IN ('won', 'lost'))                                  AS settled,
       count(*) FILTER (WHERE l.result = 'won')                                             AS won,
       count(*) FILTER (WHERE l.result IN ('won', 'lost') AND l.odds > 1)                   AS odds_n,
       sum(1 / l.odds) FILTER (WHERE l.result IN ('won', 'lost') AND l.odds > 1)            AS sum_inv_odds,
       count(l.clv_mc)       FILTER (WHERE l.result <> 'void' AND abs(l.clv_mc) <= 1)       AS clv_mc_n,
       avg(l.clv_mc)         FILTER (WHERE l.result <> 'void' AND abs(l.clv_mc) <= 1)       AS clv_mc_mean,
       stddev_samp(l.clv_mc) FILTER (WHERE l.result <> 'void' AND abs(l.clv_mc) <= 1)       AS clv_mc_sd
  FROM public.bot_ledger l
  LEFT JOIN cur c ON c.bot_name = l.bot_name
 WHERE (l.source <> 'forward_test' OR l.rule_version = c.rule_version)
 GROUP BY l.bot_name, l.market;

COMMENT ON VIEW public.bot_market_stats IS
  '#139: per (bot, market) settled/won/break-even (sum_inv_odds/odds_n) and mc-CLV n/mean/sd, same population rules as bot_scoreboard. Feeds the same-market junk-control comparison and the in-play break-even hit rate on /admin/bots.';

-- ── bot_ledger_display ─────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW public.bot_ledger_display AS
SELECT l.*,
       ht.name AS home_team,
       at.name AS away_team
  FROM public.bot_ledger l
  LEFT JOIN matches m ON m.id = l.match_id
  LEFT JOIN teams ht  ON ht.id = m.home_team_id
  LEFT JOIN teams at  ON at.id = m.away_team_id;

COMMENT ON VIEW public.bot_ledger_display IS
  '#139: bot_ledger + home_team / away_team names for the /admin/bots drawer. Display only; bot_ledger stays the contract.';

-- ── access: admin only ─────────────────────────────────────────────────────────────────────
REVOKE ALL ON public.bot_weekly, public.bot_market_stats, public.bot_ledger_display
  FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON public.bot_weekly, public.bot_market_stats, public.bot_ledger_display TO service_role;

COMMIT;
