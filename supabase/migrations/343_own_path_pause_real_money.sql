-- OWN-PATH-VERDICT (2026-09-14) — pause real-money placement.
--
-- docs/OWN_PATH_VERDICT_2026_09_14.md closed the automated-betting path on
-- EMTA-legal books. The kill criterion the OWN audit pre-specified was met by a
-- wide margin: best-of-3 de-vigged 1X2 overround across Coolbet, Epicbet and
-- Unibet-Site is 5.66pct on 359 time-aligned fixtures against a 2pct threshold.
-- Line shopping across every book Estonia allows recovers 2.05pp of a 7.71pp
-- margin, leaving ~1.89pp of residual margin per outcome. Every bet starts
-- ~1.9pct under water before any skill is applied.
--
-- WHY THIS MIGRATION EXISTS AT ALL, given that no real bet has been placed
-- since 2026-09-03: the system is ARMED, not stopped. `placement_paused` is
-- FALSE and `bot_coolbet_1x2_model_v1.ui_place_enabled` is TRUE. Placement
-- stopped only because GENERATION died when the O/U calibrator was deleted
-- (mig 335) and nothing cleared the old model floors. That is a side effect,
-- not a decision. The moment any generator starts producing picks again, this
-- configuration places real money on a strategy we have just closed.
--
-- Two specific things this fixes:
--
--  1. `bot_coolbet_1x2_model_v1` carries the note "OFF pending dry-run" from
--     2026-09-08 while `ui_place_enabled` reads TRUE. The note and the flag
--     disagree, and the flag is what the placer reads. It is also precisely the
--     1X2 model-anchored staking the replication referee said to stop: the
--     residual test fits alpha = 0.0000 against four independent benchmarks,
--     including our own self-scraped books, and the blend is WORSE than the
--     market out of sample on every one.
--
--  2. `placement_paused_reason` still reads "Imperva recovery 2026-09-09" — a
--     transport incident. Leaving a strategic closure recorded as a stale
--     infrastructure note is how a pause gets casually cleared by whoever next
--     fixes the transport.
--
-- REVERSIBLE BY DESIGN. Nothing is deleted: the placer, the account-verify
-- gate, the control surface and every bot row stay exactly as they are. If the
-- economics change -- a newly licensed book with real dispersion, or a
-- promotional regime -- this is one UPDATE away from running again. What is NOT
-- reversible by a single UPDATE is the evidence: re-enabling requires a fresh
-- measurement against the same kill criterion, not a hunch.

UPDATE coolbet_session_state
   SET placement_paused = TRUE,
       placement_paused_reason =
         'OWN-PATH-VERDICT 2026-09-14: kill criterion met. Best-of-3 de-vigged '
         'overround 5.66pct vs a 2pct threshold (n=359 time-aligned fixtures); '
         'line shopping recovers only 2.05pp of a 7.71pp margin. This is a '
         'STRATEGIC closure, not a transport incident -- do NOT clear it when '
         'fixing Imperva/CDP/JWT. Re-enable only on a fresh run of '
         'scripts/own_path_kill_criterion.py that comes in under 2pct. '
         'See docs/OWN_PATH_VERDICT_2026_09_14.md.'
 WHERE id = 1;

-- The flag now matches the note it has contradicted since 2026-09-08.
UPDATE coolbet_placer_bots
   SET ui_place_enabled = FALSE,
       note = 'OFF 2026-09-14 OWN-PATH-VERDICT. Was TRUE while its own note '
              'read "OFF pending dry-run" -- the dry-run never happened. Also '
              'the 1X2 model-anchored staking the replication referee closed: '
              'residual test fits alpha=0.0000 against four benchmarks '
              '(AF Pinnacle, Coolbet, Epicbet, own best-of) and the blend is '
              'WORSE than the market out of sample on every one.',
       updated_at = now()
 WHERE bot_name = 'bot_coolbet_1x2_model_v1';
