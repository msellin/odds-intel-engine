-- BET-VOID-INTEGRITY-2026-08-24
--
-- A bet is voided for one of three very different reasons, and until now the
-- row recorded none of them:
--
--   (a) the market genuinely pushed        — AH whole line, DNB draw
--   (b) the fixture was postponed/cancelled — stake returned, correct
--   (c) somebody deliberately quarantined it — INPLAY-O-QUARANTINE 2026-06-06,
--       the OU pinnacle-cap and OU quality-fix sweeps (May-June 2026)
--
-- Without (c) being distinguishable, an automatic re-settler cannot safely
-- reopen wrongly-voided bets: it would resurrect the quarantined rows and
-- re-inject the fake PnL those cleanups were written to remove.
--
-- Backfill rule: every simulated_bets void created before 2026-07-01 belongs to
-- a documented cleanup. Verified against the data — the only simulated_bets
-- voids after that date are 4 bot_v10_all 1x2 rows on matches that were
-- postponed and then actually played, which are exactly what we want repaired.
-- shadow_bets has no deliberate quarantines at all, so nothing there is marked.

ALTER TABLE shadow_bets    ADD COLUMN IF NOT EXISTS void_reason TEXT;
ALTER TABLE simulated_bets ADD COLUMN IF NOT EXISTS void_reason TEXT;

COMMENT ON COLUMN shadow_bets.void_reason IS
  'Why result=''void''. NULL = unknown/legacy (eligible for auto-re-settle). '
  '''push'' = genuine market push. ''postponed'' = fixture postponed/cancelled. '
  '''quarantine'' = deliberately voided by a cleanup — never auto-re-settle.';
COMMENT ON COLUMN simulated_bets.void_reason IS
  'Why result=''void''. See shadow_bets.void_reason.';

UPDATE simulated_bets
   SET void_reason = 'quarantine'
 WHERE result = 'void'
   AND void_reason IS NULL
   AND created_at < '2026-07-01';

-- Voids on fixtures that are still postponed are correct as they stand; label
-- them so the re-settler's logs stay readable and so a future flip to
-- 'finished' is attributable.
UPDATE shadow_bets sb
   SET void_reason = 'postponed'
  FROM matches m
 WHERE m.id = sb.match_id
   AND sb.result = 'void'
   AND sb.void_reason IS NULL
   AND m.status = 'postponed';

UPDATE simulated_bets sb
   SET void_reason = 'postponed'
  FROM matches m
 WHERE m.id = sb.match_id
   AND sb.result = 'void'
   AND sb.void_reason IS NULL
   AND m.status = 'postponed';

-- SHADOW-DEDUPE: the odds refresh writes one shadow_bets row per 30-min
-- shadow_cohort, by design (BET-TIMING-MONITOR compares the same bot's ROI
-- across timing windows). That makes raw row counts a poor basis for ROI and
-- for the n>=50 graduation gates — Esteghlal v Sepahan alone carries 31
-- identical rows. /admin/shadow-bots already dedupes client-side on
-- (bot_id, match_id, market, selection); this view gives engine-side analysis
-- the same basis instead of every script reinventing it.
CREATE OR REPLACE VIEW shadow_bets_unique AS
SELECT DISTINCT ON (bot_id, match_id, market, selection) *
  FROM shadow_bets
 ORDER BY bot_id, match_id, market, selection, pick_time DESC;

COMMENT ON VIEW shadow_bets_unique IS
  'One row per (bot_id, match_id, market, selection) — the latest pick_time. '
  'Use this, not shadow_bets, for ROI/hit-rate/graduation-gate counts.';
