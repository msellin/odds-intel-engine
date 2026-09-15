-- 359_promo_terms_deposit_eur.sql
-- OWN Phase 2 follow-up (2026-09-15, from the rig verifier). The deposit-bonus
-- EV cannot be computed without the DEPOSIT the rollover applies to: Estonian
-- books roll over deposit+bonus at 5-10x, and the first version of promo_ev
-- priced 100%/3x at +79 while its own docstring said such offers are usually
-- negative. Migration 356 is already applied, so the column arrives here.
-- (Numbering: 354/355 carry duplicate files from a parallel session; 359 is
-- the first free number.) Re-appliable.
ALTER TABLE promo_terms ADD COLUMN IF NOT EXISTS deposit_eur NUMERIC;
COMMENT ON COLUMN promo_terms.deposit_eur IS
    'deposit_bonus: the deposit the rollover applies to. promo_ev.ev_deposit_bonus REFUSES to price without it.';
