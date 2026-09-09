-- COOLBET-DAEMONS-PAUSE-2026-09-09 — a global "calm Imperva" switch, distinct
-- from placement_paused (the real-money kill switch).
--
-- Why: when Coolbet's Imperva bot-detection escalates (the "STAY COOL" wall),
-- the fix is to REDUCE our request footprint from the flagged IP and let the
-- flag decay. placement_paused only stops PLACEMENT — the footprint daemons
-- (odds-snapshot = coolbet_explorer --board, feed-watchdog, mac-daemon ticks)
-- keep hammering Coolbet. This flag lets the operator pause ALL Coolbet daemon
-- HTTP activity from the /admin/shadow-bots dashboard (one click, no SSH): the
-- Mac daemons poll it at the start of each run/tick and skip their Coolbet work.
--
-- The web dashboard runs on the VPS and the daemons on the operator's Mac, so a
-- button cannot launchctl the Mac — this DB flag is the seam the daemons read.
ALTER TABLE coolbet_session_state
  ADD COLUMN IF NOT EXISTS daemons_paused BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS daemons_paused_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS daemons_paused_reason TEXT;

COMMENT ON COLUMN coolbet_session_state.daemons_paused IS 'Global pause for ALL Coolbet footprint daemons (odds-snapshot, feed-watchdog, mac-daemon). Set from /admin/shadow-bots to calm Imperva. Distinct from placement_paused (real-money kill switch).';
