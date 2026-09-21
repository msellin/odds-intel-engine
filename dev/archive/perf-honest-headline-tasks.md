# PERF-HONEST-HEADLINE — Tasks

## Commit 1 — Engine + migration

- [ ] Write migration `104_perf_honest_headline.sql`
  - [ ] `ALTER TABLE bots ADD COLUMN retired_reason TEXT`
  - [ ] Backfill `retired_reason` for 4 BOTS-RETIRE-1X2 bots
  - [ ] Retire `bot_aggressive` with full reason
  - [ ] `ALTER TABLE dashboard_cache` for new columns
- [ ] Update `BOTS_CONFIG["bot_aggressive"]` description to `[RETIRED 2026-05-17] ...`
- [ ] Update `settlement.write_dashboard_cache`:
  - [ ] Add active-only headline query
  - [ ] Add retired_bot_breakdown query
  - [ ] Extend INSERT
- [ ] Smoke tests:
  - [ ] `PERF-HONEST-HEADLINE-ACTIVE-FIELDS`
  - [ ] `PERF-RETIRED-REASON-REQUIRED`
  - [ ] `BOT-AGGRESSIVE-RETIRE`
- [ ] Mark task `🔄 In Progress` in PRIORITY_QUEUE.md (then ✅ Done at commit time)
- [ ] Update ROADMAP system state
- [ ] Commit + push

## Commit 2 — Inplay €5 normalization

- [ ] `inplay_bot.py:352`: `"stake": 1.0` → `"stake": 5.0`
- [ ] Write `scripts/normalize_inplay_stake_to_5.py` with `--dry-run` / `--apply`
- [ ] Smoke tests:
  - [ ] `INPLAY-STAKE-5-NEW`
  - [ ] `INPLAY-STAKE-5-NORMALIZED`
- [ ] Confirm with user before running `--apply` on prod
- [ ] Run script, verify dashboard_cache refresh
- [ ] Commit + push

## Commit 3 — Frontend

- [ ] `engine-data.ts` — expose new fields + retiredBots
- [ ] `/performance` — two-row headline
- [ ] `/performance` — collapsed retired strategies section
- [ ] Smoke test (frontend has source-inspect smokes via odds-intel-web CI)
- [ ] Commit + push (odds-intel-web)
