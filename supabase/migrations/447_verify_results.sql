-- #168a VERIFY-QUEUE (2026-09-25): results of the background verification queue.
--
-- WHY. Agents used to finish a task and then WAIT — for a migration to apply, for the next
-- :05/:35 betting refresh, for a bot's first picks — to check the live effect. The owner's
-- rule: "quality and validation, but we don't want to keep waiting". So a task now commits its
-- post-deploy checks to ops/verify/<task>.yml (or a `-- verify:` line in its migration), and the
-- scheduler job `verify_queue` (workers/jobs/verify_queue.py, every 30 min) runs each check once
-- it is due, read-only, and Telegram-alerts the operator chat ONLY on a mismatch, an error, or a
-- check that expires without passing.
--
-- WHY A TABLE, NOT "DELETE THE FILE WHEN IT PASSES". The first proposal (#167 audit) had the job
-- delete passing files in a bot commit. The engine checkout on the VPS is a deploy target that is
-- `git pull --ff-only`-ed on every push and drift-checked daily for uncommitted changes; a bot that
-- commits from it would make the drift check and the next pull fight it. The spec files stay in
-- git (small, append-only, one per task) and the STATE lives here, where /admin/ops can show it.
--
-- One row per check. check_id = '<source path>::<check name>'. spec_hash lets an edited check
-- start over instead of inheriting the old verdict. Terminal statuses: passed, expired.
-- 'holding' = an invariant check that held on its last run and is still being watched.

CREATE TABLE IF NOT EXISTS verify_results (
    check_id        text PRIMARY KEY,
    task            text,
    source          text NOT NULL,
    name            text NOT NULL,
    spec_hash       text NOT NULL,
    mode            text NOT NULL DEFAULT 'now' CHECK (mode IN ('now', 'eventually', 'invariant')),
    expect          text NOT NULL,
    status          text NOT NULL DEFAULT 'waiting'
                    CHECK (status IN ('waiting', 'pending', 'holding', 'passed', 'failed', 'error', 'expired')),
    last_value      text,
    detail          text,
    runs            integer NOT NULL DEFAULT 0,
    first_run_at    timestamptz,
    last_run_at     timestamptz,
    passed_at       timestamptz,
    expires_at      timestamptz,
    alerted_status  text,            -- the status we last alerted on (one alert per transition)
    alerted_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_verify_results_status ON verify_results (status, last_run_at DESC);

COMMENT ON TABLE verify_results IS
  '#168a background verification queue: one row per post-deploy check (ops/verify/*.yml or a migration `-- verify:` line). Written only by workers/jobs/verify_queue.py; checks themselves run on a read-only session. Private (no anon grant).';

-- verify: (SELECT count(*) FROM information_schema.tables WHERE table_name = 'verify_results') = 1
