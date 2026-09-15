-- 353_publishing_pause_separate_from_placement.sql
-- PICKS-PUBLISH-DECOUPLED-FROM-OWN-PAUSE (2026-09-15)
--
-- WHY. `coolbet_session_state.placement_paused` was doing two unrelated jobs:
-- halting real-money placement (what its name, and the Telegram /pause help
-- text, both promise) and silencing the customer-facing @oddsintelpicks
-- Telegram channel (an undocumented side effect of an early `return` in
-- workers/jobs/betting_pipeline.py::_run_coolbet_signal).
--
-- On 2026-09-14 12:05 UTC the OWN-path verdict (migration 343,
-- docs/OWN_PATH_VERDICT_2026_09_14.md) set placement_paused to close the
-- automated-betting product. That is a 🤖 OWN decision about what WE stake, and
-- the verdict doc says in as many words: "This verdict is about what we BET,
-- not what we store." It nonetheless armed a 👥 PICKS outage nobody chose: from
-- that moment the public channel was muted.
--
-- HOW MUCH THIS HAS COST SO FAR: nothing, by luck of timing. The last pick was
-- generated 2026-09-13 13:06 UTC, ~23h BEFORE the pause landed, and the slate
-- has been flat since (2026-09-15: bot_v10_all's best of 594 candidates missed
-- its edge floor by 0.12pp — a genuinely flat day, not a fault). So no
-- published pick has been lost yet. The next qualifying pick would have been,
-- silently — `signaled_at` is only stamped when a send lands, so a muted pick
-- is indistinguishable from a day with no picks on every surface we have.
--
-- This is the second time the same coupling bit: SIGNAL-PAUSE-DECOUPLE
-- (2026-08-27) fixed it for *daemon self-pauses* and left the operator-pause
-- branch coupled. Fixing one branch of a two-branch conflation is how it came
-- back five weeks later.
--
-- WHAT. Publishing gets its own flag. `placement_paused` now governs placement
-- only — matching its name and its /pause help text. The public channel makes
-- zero Coolbet API calls and writes no real_bets row, so it is safe to run
-- while placement is down; that is already proven by the daemon-self-pause
-- branch, which has published through every Coolbet outage since August.
--
-- Defaults FALSE: the OWN-path pause must NOT carry over into publishing,
-- which is the whole point. The operator keeps an explicit kill switch on the
-- customer feed via Telegram /pausepicks and /resumepicks (odds-intel-web
-- src/app/api/telegram/webhook/route.ts).

ALTER TABLE coolbet_session_state
    ADD COLUMN IF NOT EXISTS publishing_paused        BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS publishing_paused_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS publishing_paused_reason TEXT;

COMMENT ON COLUMN coolbet_session_state.publishing_paused IS
    'Operator kill switch for the PUBLIC @oddsintelpicks Telegram channel only. '
    'Independent of placement_paused on purpose: a decision to stop staking our '
    'own money (OWN) is not a decision to stop publishing picks (PICKS). '
    'Set/cleared by Telegram /pausepicks and /resumepicks.';

COMMENT ON COLUMN coolbet_session_state.placement_paused IS
    'Halts real-money placement ONLY. Does NOT mute the public picks channel — '
    'see publishing_paused (migration 353, 2026-09-15).';
