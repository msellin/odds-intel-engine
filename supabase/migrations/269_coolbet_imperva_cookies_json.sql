-- COOLBET-CDP-COOKIE-EXPORT (2026-07-08)
--
-- Path B fix for the recurring Imperva 403 pattern on the Mac's FS-Docker
-- Chrome. The mac_daemon's CDP-Chrome (operator's real desktop browser)
-- passes Imperva because it's device-trusted. Every :03/:33 the daemon
-- now harvests the 6 Imperva-critical cookies from CDP and stashes them
-- here; every other Coolbet-HTTP job (coolbet-odds-snapshot,
-- cs2-coolbet-scanner) reads them and hits Coolbet via plain requests
-- (COOLBET_NO_FS=true mode) — no FS-Chrome challenge to fail.
--
-- imperva_cookies_json shape:
--   {
--     "reese84":                "…",
--     "visid_incap_723517":     "…",
--     "nlbi_723517":            "…",
--     "nlbi_723517_2147483392": "…",
--     "incap_ses_1099_723517":  "…",
--     "uuid":                   "…",
--     "_harvested_at":          "2026-07-08T09:00:00Z",
--     "_source":                "cdp_chrome" | "manual_env_backfill"
--   }
--
-- Consumers must tolerate missing keys (Imperva sometimes returns fewer
-- than all 6). Only reese84 + visid_incap_* are actually load-bearing.

ALTER TABLE coolbet_session_state
    ADD COLUMN IF NOT EXISTS imperva_cookies_json JSONB;

ALTER TABLE coolbet_session_state
    ADD COLUMN IF NOT EXISTS imperva_cookies_refreshed_at TIMESTAMPTZ;

COMMENT ON COLUMN coolbet_session_state.imperva_cookies_json IS
    'CDP-Chrome harvested Imperva cookies (reese84, visid_incap_*, nlbi_*, incap_ses_*, uuid). Written by mac_daemon each tick, read by NO_FS-mode consumers.';
COMMENT ON COLUMN coolbet_session_state.imperva_cookies_refreshed_at IS
    'When imperva_cookies_json was last written from CDP-Chrome. Consumers should refuse cookies older than ~2h.';
