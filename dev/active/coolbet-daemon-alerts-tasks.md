# COOLBET-DAEMON-ALERTS — Tasks

- [ ] `diagnose_cdp_jwt_state()` in `coolbet_browser_sync.py` — returns `{state, detail}` for chrome_down / no_coolbet_tab / logged_out / jwt_expired / valid
- [ ] `coolbet_mac_daemon.py` — consecutive-error counter in `run_forever()`, classify on 2nd error, send Telegram with dedup_key per hour
- [ ] `workers/jobs/coolbet_prekickoff_alert.py` — new file. Queries calibrated-bot sim_bets in pre-KO window, joined with coolbet_session_state.mac_daemon_last_tick_at + result. Sends Telegram per bet
- [ ] `workers/scheduler.py` — register `job_coolbet_prekickoff_alert` every 5 min
- [ ] `scripts/smoke_test.py` — pin both behaviors (source-inspection tests, no live API)
- [ ] Targeted smoke run: `python3 scripts/smoke_test.py --filter COOLBET-DAEMON-ALERTS` and `--filter COOLBET-PREKICKOFF-CATCHNET`
- [ ] `PRIORITY_QUEUE.md` flip to ✅ Done
- [ ] `COOLBET_ARCHITECTURE.md` failure-modes table updated
- [ ] Commit + push to main
