-- 441_flat_stakes_restatement.sql — [[#155]] FLAT STAKES EVERYWHERE: restate every simulated_bets row to
-- the one flat unit (EUR 10), owner 2026-09-25 ("keep flat stakes everywhere, Kelly hasn't proven itself
-- in this project yet"; "fix all the bets for those bots"). Spec agreed with #162 on row #155.
--
-- WHY. Pipeline bots were sized by fractional Kelly (compute_stake), so a bot's stored stake / pnl /
-- bankroll_after told a different story from the flat record /performance publishes (bot_performance,
-- migration 433): the detail view read EV5 at -53.8% stake-weighted where flat was -3%. Readers bet flat
-- units, every backtest that decided a bot was flat and judged on CLV (stake-independent), so the record
-- is restated rather than explained. From this commit compute_stake returns the flat unit
-- (workers/model/improvements.py FLAT_STAKE_EUR) and settlement computes pnl at the same price as below.
--
-- THE RULE (restated from result + flat stake + PRICE, never from the existing pnl):
--   price = odds_at_pick_available (> 1)  — "available at pick time, all publishable books" (#159)
--           else odds_at_pick_live (> 1)  — our books
--           else odds_at_pick             — the recorded high-water mark (ANALYSIS_GOTCHAS §55), FLAGGED
--   = exactly bot_ledger.odds_public / public_basis, so stored pnl == 10 x pnl_unit_public (the leg
--   behind bot_performance.roi_public; smoke ONE-ROI-CLV-PARITY now asserts it). NOT LEAST(odds_at_pick,
--   odds_at_pick_live) — that is only #162's conservative autovoid TEST price.
--   won -> round((price - 1) x 10, 2) · lost -> -10 · void (incl. quarantined autovoids) -> 0 · pending ->
--   pnl stays NULL. bankroll_after recomputed sequentially per bot (pick_time, id) over settled rows;
--   bots.current_bankroll = starting_bankroll + SUM(pnl) of won/lost (smoke BOT-BANKROLL-DRIFT).
--   real_bets untouched. shadow_bets (all 168,920 rows already stake 10) and picks_forward_test (unit pnl,
--   no stake column) are already flat — nothing to restate there.
--
-- PRESERVED: stake_kelly_original / pnl_kelly_original (the pre-restatement values, audit + reversal);
--   pnl_price_basis ('available' | 'our_books' | 'recorded') — settlement writes it on every new row too.
--
-- DRY RUN (VPS DB, 2026-09-25, before applying): 4,783 simulated_bets rows across 53 bots, 4,729 not at
--   EUR 10 (the other 54 already flat). Settled by price basis: available 2,576 won/lost · our_books 1,104
--   (in-play legs, never priced off the pre-match board) · recorded 630 FLAGGED (bot_high_alignment 272,
--   bot_ah_home_fav 133, bot_ah_away_dog 82, and in-play 145) · void 385 · pending 88.
--   Per bot (rows · non-flat · settled P&L old -> new, EUR): bot_aggressive 713 · 708 · -82.94 -> +41.90;
--   bot_high_alignment 544 · 541 · -53.00 -> -167.04; inplay_e 429 · 429 · +41.88 -> +566.72; bot_v10_1x2
--   404 · 403 · +302.49 -> +448.32; bot_v10_ou 260 · 260 · -12.91 -> +106.81; bot_btts_all 214 · 214 ·
--   -31.30 -> -23.08; inplay_p_v2 207 · 207 · -3.69 -> -109.05; inplay_p 193 · 193 · -140.08 -> -468.54;
--   bot_ah_home_fav 148 · 148 · -90.12 -> -160.98; inplay_l 129 · 129 · +69.55 -> +539.00; bot_dc_value
--   126 · 126 · -64.28 -> -118.90; bot_high_roi_global_v2 52 · 50 · +77.61 -> +97.70;
--   bot_combined_1x2_ev5_v1 34 · 34 · -11.47 -> -16.80; bot_v10_1x2_newplus_v1 21 · 21 (all pending);
--   ... 39 more bots (full table in the #155 commit message). In-play bots move most: they staked EUR 5
--   (INPLAY-STAKE-5) and their recorded price is replaced by the our-books price.
--
-- GUARD: a BEFORE INSERT/UPDATE trigger coerces any non-flat stake to the unit and keeps what the writer
-- asked for in stake_kelly_original — so a writer still on old code (the scheduler restarts on the same
-- push, order vs this migration is not guaranteed) or a future one cannot re-open the gap silently.

BEGIN;

ALTER TABLE public.simulated_bets ADD COLUMN IF NOT EXISTS stake_kelly_original numeric(12,2);
ALTER TABLE public.simulated_bets ADD COLUMN IF NOT EXISTS pnl_kelly_original numeric(12,2);
ALTER TABLE public.simulated_bets ADD COLUMN IF NOT EXISTS pnl_price_basis text;
DO $$ BEGIN
  ALTER TABLE public.simulated_bets ADD CONSTRAINT simulated_bets_pnl_price_basis_chk
    CHECK (pnl_price_basis IS NULL OR pnl_price_basis IN ('available', 'our_books', 'recorded'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMENT ON COLUMN public.simulated_bets.stake_kelly_original IS
  '#155 migration 441: the stake before FLAT-STAKES-EVERYWHERE (fractional Kelly for pipeline bots, EUR 5 in-play). Data only — nothing sizes on it.';
COMMENT ON COLUMN public.simulated_bets.pnl_kelly_original IS
  '#155 migration 441: the pnl before the flat restatement (on the old stake and the recorded odds_at_pick).';
COMMENT ON COLUMN public.simulated_bets.pnl_price_basis IS
  '#155: the price pnl is computed at — available (odds_at_pick_available) | our_books (odds_at_pick_live) | recorded (odds_at_pick, no quote stored at pick time: FLAGGED). = bot_ledger.public_basis.';

-- 1. preserve (idempotent: only rows not yet preserved)
UPDATE public.simulated_bets
   SET stake_kelly_original = stake, pnl_kelly_original = pnl
 WHERE stake_kelly_original IS NULL;

-- 2. restate stake + pnl + basis from result + flat unit + the public price. SINGLES only: a combo /
--    system bet's pnl comes from settle_combo_bet (partial payouts, voided legs) and is linear in its
--    stake, so it is SCALED to the unit instead (2b). None exist today (dry run: 0 combo rows).
UPDATE public.simulated_bets s
   SET stake = 10,
       pnl = CASE s.result::text
               WHEN 'won'  THEN round((x.price - 1) * 10, 2)
               WHEN 'lost' THEN -10
               WHEN 'void' THEN 0
               ELSE s.pnl END,
       pnl_price_basis = CASE WHEN s.result::text IN ('won', 'lost', 'void') THEN x.basis END
  FROM (SELECT id,
               CASE WHEN odds_at_pick_available > 1 THEN odds_at_pick_available
                    WHEN odds_at_pick_live > 1 THEN odds_at_pick_live ELSE odds_at_pick END AS price,
               CASE WHEN odds_at_pick_available > 1 THEN 'available'
                    WHEN odds_at_pick_live > 1 THEN 'our_books' ELSE 'recorded' END AS basis
          FROM public.simulated_bets) x
 WHERE x.id = s.id AND s.combo_legs IS NULL;

-- 2b. combos: scale to the unit (pnl linear in stake); no single public price, so no basis
UPDATE public.simulated_bets s
   SET pnl = CASE WHEN s.pnl IS NULL OR s.stake = 0 THEN s.pnl ELSE round(s.pnl * 10 / s.stake, 2) END,
       stake = 10
 WHERE s.combo_legs IS NOT NULL AND s.stake <> 10;

-- 3. bankroll_after: sequential per bot over settled rows
UPDATE public.simulated_bets s
   SET bankroll_after = r.running
  FROM (SELECT sb.id,
               b.starting_bankroll + SUM(sb.pnl) OVER (PARTITION BY sb.bot_id ORDER BY sb.pick_time, sb.id
                                                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running
          FROM public.simulated_bets sb JOIN public.bots b ON b.id = sb.bot_id
         WHERE sb.result::text IN ('won', 'lost', 'void')) r
 WHERE r.id = s.id;

-- 4. current_bankroll = starting + sum(pnl) for every bot with a simulated_bets row
UPDATE public.bots b
   SET current_bankroll = b.starting_bankroll + t.pnl
  FROM (SELECT bot_id, COALESCE(SUM(pnl) FILTER (WHERE result::text IN ('won', 'lost')), 0) AS pnl
          FROM public.simulated_bets GROUP BY bot_id) t
 WHERE t.bot_id = b.id;

-- 5. the guard
CREATE OR REPLACE FUNCTION public.simulated_bets_flat_stake() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- #155 FLAT-STAKES-EVERYWHERE: one unit per pick for every bot. The unit is
  -- workers/model/improvements.py FLAT_STAKE_EUR (smoke FLAT-STAKES-EVERYWHERE pins both).
  IF NEW.stake IS DISTINCT FROM 10 THEN
    NEW.stake_kelly_original := COALESCE(NEW.stake_kelly_original, NEW.stake);
    NEW.stake := 10;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS simulated_bets_flat_stake ON public.simulated_bets;
CREATE TRIGGER simulated_bets_flat_stake
  BEFORE INSERT OR UPDATE OF stake ON public.simulated_bets
  FOR EACH ROW EXECUTE FUNCTION public.simulated_bets_flat_stake();

-- 6. EV-band split of a bot's record (owner 2026-09-25, for the VIP detail view). bot_combined_1x2_ev8_v1
--    is retired (migration 444) because its picks were a strict subset of the VIP bot's; this split
--    replaces it: EV8 (EV >= 8%) vs EV5 (below 8%) inside ONE bot, each with n settled, flat ROI and
--    sharp-anchor CLV. SAME legs (bot_ledger, in_record), SAME per-leg columns and SAME aggregate
--    expressions as bot_performance — only the GROUP BY gains ev_band — and smoke ONE-ROI-CLV-PARITY
--    asserts the bands sum back to the bot_performance row, so it is not a second ROI path.
--    EV = model_prob x odds_public - 1: the price the row's ROI is on, and the one the detail view's
--    EV column and EV8/EV5 chip read (bot_registry.vip_ev_label / web vipEvLabel, VIP_EV8_MIN 0.08).
CREATE OR REPLACE VIEW public.bot_performance_ev_band AS
WITH l AS (
  SELECT bot_name, result, pnl_unit_public, public_basis, clv_anchor_public, clv_anchor_source,
         CASE WHEN model_prob IS NULL OR odds_public IS NULL THEN NULL
              WHEN model_prob * odds_public - 1 >= 0.08 THEN 'EV8' ELSE 'EV5' END AS ev_band,
         result = ANY (ARRAY['won', 'lost']) AS is_settled,
         clv_anchor_public IS NOT NULL AND abs(clv_anchor_public) <= 1 AS clv_ok_public
    FROM public.bot_ledger
   WHERE in_record)
SELECT bot_name, ev_band,
       count(*) FILTER (WHERE is_settled) AS settled,
       count(*) FILTER (WHERE result = 'won') AS won,
       count(*) FILTER (WHERE result = 'lost') AS lost,
       COALESCE(sum(pnl_unit_public) FILTER (WHERE is_settled), 0) AS pnl_units_public,
       CASE WHEN count(*) FILTER (WHERE is_settled) > 0
            THEN round(avg(pnl_unit_public) FILTER (WHERE is_settled), 6) END AS roi_public,
       count(*) FILTER (WHERE is_settled AND public_basis = 'recorded') AS n_public_recorded,
       count(*) FILTER (WHERE is_settled AND clv_ok_public) AS clv_n,
       avg(clv_anchor_public) FILTER (WHERE is_settled AND clv_ok_public) AS clv_public
  FROM l
 GROUP BY bot_name, ev_band;
COMMENT ON VIEW public.bot_performance_ev_band IS
  '#155 (owner 2026-09-25): bot_performance split by EV band (EV8 >= 8% / EV5 < 8%, EV = model_prob x odds_public - 1). Same legs + expressions; bands sum to bot_performance (smoke ONE-ROI-CLV-PARITY). PRIVATE.';
REVOKE ALL ON public.bot_performance_ev_band FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.bot_performance_ev_band TO service_role;

-- 7. assertions — any drift aborts the whole migration
DO $$
DECLARE n integer;
BEGIN
  SELECT count(*) INTO n FROM public.simulated_bets WHERE stake <> 10;
  IF n > 0 THEN RAISE EXCEPTION '441: % rows still not flat', n; END IF;
  SELECT count(*) INTO n FROM public.simulated_bets WHERE stake_kelly_original IS NULL;
  IF n > 0 THEN RAISE EXCEPTION '441: % rows lost their original stake', n; END IF;
  SELECT count(*) INTO n FROM public.bot_ledger l JOIN public.simulated_bets s ON s.id = l.pick_id
   WHERE l.source = 'sim' AND l.result IN ('won', 'lost', 'void')
     AND abs(s.pnl - 10 * l.pnl_unit_public) > 0.006;
  IF n > 0 THEN RAISE EXCEPTION '441: % legs where stored pnl <> 10 x pnl_unit_public', n; END IF;
  SELECT count(*) INTO n FROM public.bots b
    JOIN (SELECT bot_id, SUM(pnl) FILTER (WHERE result::text IN ('won', 'lost')) p
            FROM public.simulated_bets GROUP BY bot_id) t ON t.bot_id = b.id
   WHERE abs(b.current_bankroll - (b.starting_bankroll + COALESCE(t.p, 0))) > 0.005;
  IF n > 0 THEN RAISE EXCEPTION '441: % bots with bankroll drift', n; END IF;
END $$;

COMMIT;
