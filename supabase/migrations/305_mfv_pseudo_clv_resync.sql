-- 305_mfv_pseudo_clv_resync.sql
-- META-MFV-TARGET-INVERTED-2026-09-06 (follow-up — migration 303 was incomplete)
--
-- Migration 303 corrected the pseudo-CLV sign inversion in `matches`. It missed
-- that `match_feature_vectors` holds its OWN COPY of the same three columns,
-- populated from `matches` when the feature vector is built
-- (supabase_client.py:1254). The meta model trains on the MFV copy, not on
-- `matches` — so 303 fixed the source and left the training label inverted.
--
-- Measured after 303 applied: of 19,593 overlapping rows only 3,063 agreed.
-- matches mean +0.00060 against MFV mean +0.05733, and the label's correlation
-- with real clv_pinnacle_devig was still NEGATIVE (r=-0.264 whole period,
-- r=-0.315 since 2026-08-01) — i.e. exactly the defect 303 was written to fix,
-- still live in the only copy that matters.
--
-- RE-COPY RATHER THAN RE-INVERT. Applying `1/(1+x)-1` a second time returns a
-- value to its original, so a transform-based fix would silently re-break every
-- row 303 had already corrected, and there is no reliable way to tell the two
-- populations apart after the fact. `matches` is the source of truth and is now
-- correct, so copying from it is idempotent: running this twice changes
-- nothing, and it cannot double-invert.
--
-- Rows where `matches` is NULL are left alone rather than nulled: a NULL there
-- means the pseudo-CLV was never computable for that match, which is not the
-- same claim as "the feature vector's value is wrong".

UPDATE match_feature_vectors f
   SET pseudo_clv_home = m.pseudo_clv_home,
       pseudo_clv_draw = m.pseudo_clv_draw,
       pseudo_clv_away = m.pseudo_clv_away
  FROM matches m
 WHERE m.id = f.match_id
   AND (m.pseudo_clv_home IS NOT NULL
     OR m.pseudo_clv_draw IS NOT NULL
     OR m.pseudo_clv_away IS NOT NULL)
   AND (f.pseudo_clv_home IS DISTINCT FROM m.pseudo_clv_home
     OR f.pseudo_clv_draw IS DISTINCT FROM m.pseudo_clv_draw
     OR f.pseudo_clv_away IS DISTINCT FROM m.pseudo_clv_away);
