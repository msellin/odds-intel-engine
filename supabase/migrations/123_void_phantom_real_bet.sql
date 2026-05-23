-- DUPE-CLEAN (2026-05-23): void one phantom real_bets row created when
-- coolbet_placer's auto path bailed (no odds_uuid) but still wrote a
-- "ticket=None" record. The actual placement at Coolbet was the
-- subsequent manual /admin/place entry; this auto row is dataset noise.
--
-- Match: Joondalup City vs Inglewood United, bot_aggressive O/U under 2.5.
-- Two real_bets exist for the same (match, market, selection) today:
--   c3acf4e7-...  06:57:41 UTC  notes="auto ticket=None edge=+11.00%"  ← phantom
--   93a1613b-...  06:58:37 UTC  notes=NULL                              ← real (manual)
--
-- coolbet_placer.py is fixed in the same push to stop writing the
-- ticket=None phantom going forward (DUPE-FIX-2). /api/admin/real-bet
-- now also has a NOT EXISTS guard so manual + auto can't race.
--
-- Voiding (not deleting) so the row stays in the audit log; settlement
-- ignores result='void' rows and the new admin page filters voids out
-- of the active counts.

UPDATE real_bets
SET    result      = 'void',
       pnl         = 0,
       resolved_at = NOW(),
       notes       = COALESCE(notes, '') ||
                     ' [voided 2026-05-23: phantom record — auto placer could not place at '
                     || 'Coolbet (no odds_uuid); manual placement made on the same selection]'
WHERE  id = 'c3acf4e7-e6da-40f5-8386-fcd921af7415'
  AND  result = 'pending'  -- safety: don't touch if already settled
  AND  notes LIKE '%ticket=None%';  -- safety: only the phantom, not the real bet
