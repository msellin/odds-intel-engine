# PER-BOT-SWEEP-2026-08-24 — Tasks

- [x] Audit live shadow_bets (done 2026-08-24, verified by sub-agent)
- [x] Locate CONFIG-SWEEP-2026-08-19 artifacts + read report
- [x] Establish which bots were actually backtested (3 of 8)
- [x] Build point-in-time extraction (odds_snapshots → CSV)
- [x] Build replay harness + settlement
- [x] Sweep: per bot × edge threshold × tier × window
- [x] Compare backtest vs live per bot
- [x] Write report (context doc) — config changes NOT shipped, awaiting operator decision
- [x] Ship config changes (migration 281 + engine + frontend) — 2026-08-24
- [x] Record config history for rollback (`bot_config_history`)
- [x] Update /admin/shadow-bots (retired bots, active-only aggregates, flags 5→1)
- [x] Fix pre-existing failing smoke test BOT-PIN-OU-SHADOW
- [ ] **Review 2026-08-31** — one week of forward data, judge on CLV not ROI
- [ ] Drop `scratch_pit_odds_3h` from the prod DB when re-runs are done
