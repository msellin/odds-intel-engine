-- 398 — DATA QUALITY FINDINGS ([[#120]] / [[#121]] observatory phase 1, 2026-09-24)
--
-- One row per detection by a data-quality check (board_guard at write time, board_audit
-- read-back, and the #121 checks that follow). The rows a check refused or moved are in
-- odds_snapshots_quarantined; this table says WHAT was found, WHERE and by WHICH check,
-- so /admin can show it and Telegram can count it.
CREATE TABLE IF NOT EXISTS data_quality_findings (
    id          bigserial   PRIMARY KEY,
    check_name  text        NOT NULL,          -- wrong_fixture_board | mirrored_1x2 | ...
    match_id    uuid,
    bookmaker   text,
    detail      jsonb,
    rows_moved  integer     NOT NULL DEFAULT 0,
    found_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS data_quality_findings_time ON data_quality_findings (found_at DESC);
CREATE INDEX IF NOT EXISTS data_quality_findings_check ON data_quality_findings (check_name, bookmaker, found_at DESC);

-- Review 2026-09-24: a quarantine MOVE (board_audit) must be fully reversible — keep the
-- original row id and its is_opening flag, which odds_snapshots_quarantined lacked.
ALTER TABLE odds_snapshots_quarantined ADD COLUMN IF NOT EXISTS is_opening boolean;
ALTER TABLE odds_snapshots_quarantined ADD COLUMN IF NOT EXISTS original_id uuid;
