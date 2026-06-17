-- COOLBET-HEAL-LOG (2026-06-17)
--
-- Audit trail for every auto_self_heal invocation: when, who triggered it
-- (auto vs operator), what state it found, what actions it tried, whether
-- it succeeded. Lets us answer questions that the per-tick logs can't:
--   • How often does auto-heal actually fire?
--   • Which failure classes recur (chrome_down / logged_out / jwt_expired)?
--   • Are operator-triggered heals more or less successful than auto ones?
--   • Is the typical heal getting faster or slower over time?
--
-- Cheap inserts — one row per heal attempt, never updated. No FK constraints
-- so a coolbet_session_state row going away doesn't strand audit rows.

CREATE TABLE IF NOT EXISTS coolbet_heal_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    triggered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    triggered_by    TEXT NOT NULL,                -- 'auto' / 'operator_tg' / 'operator_cli' / 'pipeline'
    state_before    TEXT,                          -- valid / jwt_expired / logged_out / no_coolbet_tab / chrome_down / unknown
    state_after     TEXT,
    recovered       BOOLEAN NOT NULL DEFAULT FALSE,
    actions         JSONB,                         -- array of action strings from auto_self_heal
    message         TEXT,                          -- final human-readable status
    duration_s      NUMERIC(8,2)                   -- wall-clock time of the heal attempt
);

CREATE INDEX IF NOT EXISTS idx_coolbet_heal_log_triggered_at
    ON coolbet_heal_log (triggered_at DESC);

CREATE INDEX IF NOT EXISTS idx_coolbet_heal_log_recovered
    ON coolbet_heal_log (recovered, triggered_at DESC);

COMMENT ON TABLE coolbet_heal_log IS
    'Audit trail for every auto_self_heal invocation. Lets the operator
     verify "did self-heal actually fire today?" + see longitudinal stats
     on which failure classes recur. One row per attempt; never updated.';
