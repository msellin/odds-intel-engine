-- 469 — PICKS-PAGE-GATE (owner 2026-09-26): /picks shows fewer, better picks; every pick still counts.
--
-- WHY. /picks listed 124 picks on Saturday 2026-09-26 — 60 from bot_v10_1x2_newplus_v1 and 31 from
-- bot_v10_ou_comb_v1 (both TESTING since 09-25, EV >= 3% rules on a big slate), 21 from the consensus C
-- grade. Owner: "this is not a volume users want — keep the volume on /performance, but start filtering
-- /picks". Evidence on settled picks (sharp-anchor CLV): bot_v10_1x2 +4.9% (n 169), the pre-registered
-- sharp arm +2.2% (n 75), bot_high_roi_global_v2 +2.3% (n 23); consensus C -0.4% (n 49), D -2.0%; the
-- two new twins too young to judge. So the gate is by EVIDENCE, not one EV cut (a uniform 5% EV cut
-- would have dropped the sharp arm, whose pre-registered floor is 3%).
--
-- RULE (what /picks and /api/v1/upcoming list — records, /performance, Telegram and the pre-registered tests
-- are untouched):
--   * always shown: the pre-registered sharp arm (arm = 'live') and ACTIVE bots;
--   * every other bot (TESTING: the consensus grades, the new-model twins, …): only when
--     EV = fair_prob x odds - 1 >= picks_page_rule.testing_min_ev (0.07 at launch);
--   * one GATED pick per match when one_per_match, and none where an always-shown pick exists: the
--     always-shown picks are never capped; among the rest the EARLIEST published wins, so a pick never
--     disappears because a later one arrived.
-- Launch numbers (Saturday 09-26 slate, dry run): 124 -> 34. Tune with ONE UPDATE of picks_page_rule, no deploy.
-- Known mismatch until the owner decides: Telegram still sends TESTING picks at EV >= 5% (#174 rule,
-- which also governs the pre-registered consensus arm's sends), so a 5-7% EV pick can be on Telegram
-- but not on /picks.
SET lock_timeout = '3s';

CREATE TABLE IF NOT EXISTS public.picks_page_rule (
  id             boolean PRIMARY KEY DEFAULT true CHECK (id),
  testing_min_ev numeric NOT NULL DEFAULT 0.07,
  one_per_match  boolean NOT NULL DEFAULT true,
  note           text,
  updated_at     timestamptz NOT NULL DEFAULT now()
);
INSERT INTO public.picks_page_rule (id, testing_min_ev, one_per_match, note)
VALUES (true, 0.07, true, 'launch 2026-09-26 (owner: fewer, better picks on /picks); retune after the weekend + #154')
ON CONFLICT (id) DO NOTHING;
REVOKE ALL ON public.picks_page_rule FROM anon, authenticated;
GRANT SELECT, UPDATE ON public.picks_page_rule TO service_role;

CREATE OR REPLACE VIEW public.picks_page AS
WITH r AS (SELECT testing_min_ev, one_per_match FROM public.picks_page_rule WHERE id),
e AS (
  SELECT p.*,
         (p.arm = 'live' OR b.maturity_label = 'active') AS always_shown,
         p.fair_prob * p.odds - 1 AS ev
    FROM public.picks_public_all p
    LEFT JOIN public.bots b ON b.name = p.bot
),
eligible AS (
  SELECT e.* FROM e, r
   WHERE e.always_shown OR (e.ev IS NOT NULL AND e.ev >= r.testing_min_ev - 1e-9)
),
ranked AS (
  SELECT eligible.*,
         row_number() OVER (PARTITION BY match_id ORDER BY always_shown DESC, published_at, id) AS match_rank
    FROM eligible
)
SELECT id, edge_kind, bot, match_id, market, selection, odds, bookmaker, edge, fair_prob, rule_version,
       alignment_gap_minutes, published_at, outcome, clv, kickoff_utc, league, country, home_team, away_team,
       arm, anchor_bookmaker, grade
  FROM ranked, r
 WHERE NOT r.one_per_match OR ranked.always_shown OR ranked.match_rank = 1;
COMMENT ON VIEW public.picks_page IS
  'PICKS-PAGE-GATE (2026-09-26): what /picks lists — picks_public_all filtered by picks_page_rule (display only; every pick still counts on /performance).';
GRANT SELECT ON public.picks_page TO anon, authenticated;
