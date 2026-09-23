-- 389 — FEED CONTROLS: pause / resume / run now from /admin/feeds ([[#107]] phase B, 2026-09-23)
--
-- Owner: "I want to discover such things much faster and be able to act on them, via web."
--
-- The web app only WRITES REQUESTS here (superadmin API route, service key). The
-- engine enforces them: `_run_job` skips a paused feed's job, and a 30-second drain
-- job turns a run-now request into a one-off run of that feed's own scheduler
-- wrapper. The web never executes anything on the VPS.
--
-- Pause is the remedy the Coolbet runbook prescribes for a bot-protection flag
-- (§7: back off, let it decay) — #108 had to be handled by editing a systemd unit
-- over SSH; this makes it one click, reversible, and logged.

CREATE TABLE IF NOT EXISTS feed_controls (
    feed_id               text        PRIMARY KEY,
    paused                boolean     NOT NULL DEFAULT false,
    paused_reason         text,
    paused_by             text,
    paused_at             timestamptz,
    run_now_requested_at  timestamptz,
    run_now_requested_by  text,
    run_now_started_at    timestamptz,
    updated_at            timestamptz NOT NULL DEFAULT now()
);

-- Audit trail: every operator action and what the engine did with it.
CREATE TABLE IF NOT EXISTS feed_actions (
    id          bigserial   PRIMARY KEY,
    feed_id     text        NOT NULL,
    action      text        NOT NULL CHECK (action IN ('pause', 'resume', 'run_now')),
    reason      text,
    actor       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    handled_at  timestamptz,
    result      text
);
CREATE INDEX IF NOT EXISTS feed_actions_feed ON feed_actions (feed_id, created_at DESC);

-- What the page needs to render the controls and the paused state.
ALTER TABLE feed_status ADD COLUMN IF NOT EXISTS controls        text[];
ALTER TABLE feed_status ADD COLUMN IF NOT EXISTS paused          boolean NOT NULL DEFAULT false;
ALTER TABLE feed_status ADD COLUMN IF NOT EXISTS paused_reason   text;
ALTER TABLE feed_status ADD COLUMN IF NOT EXISTS paused_by       text;
ALTER TABLE feed_status ADD COLUMN IF NOT EXISTS paused_at       timestamptz;
ALTER TABLE feed_status ADD COLUMN IF NOT EXISTS run_now_pending boolean NOT NULL DEFAULT false;
