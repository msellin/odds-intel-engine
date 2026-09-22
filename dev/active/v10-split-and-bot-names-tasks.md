# V10 SPLIT + BOT NAMES — checklist

Parent rows: **[[#040]]** and **[[#069]]** in `PRIORITY_QUEUE.md`.
Plan: `dev/active/v10-split-and-bot-names-plan.md`.

## Phase 1 — migration 375
- [x] `bots.display_name` column (display only, never a join key)
- [x] `bots_maturity_label_check` admits `testing`
- [x] create `bot_v10_1x2` (calibrated) + `bot_v10_ou` (beta)
- [x] re-attribute `simulated_bets` by market, with a count assertion
- [x] re-attribute `shadow_bets` by market, with a count assertion
- [x] retire `bot_v10_all`
- [x] backfill `display_name` for the active fleet
- [x] `testing` label on `bot_sharp_forward_test_v1` + `bot_consensus_anchor_v1`

## Phase 2 — engine
- [x] `daily_pipeline_v2.BOTS_CONFIG` split
- [x] `BOT_TIMING_COHORTS` split
- [x] `coolbet_feed_watchdog.PICKS_BOT` -> tuple
- [x] `bot_registry.py` two specs + `display` field
- [x] `docs/SYSTEM_MAP.md`

## Phase 3 — web
- [x] `engine-data.ts` selects `display_name`
- [x] `bot-aggregates.ts` carries `displayName`
- [x] `performance-leaderboard.tsx` renders it
- [x] `labels.ts` prefers it

## Phase 4 — promotion rule
- [x] written beta->calibrated threshold with a number, in `docs/SYSTEM_MAP.md`

## Smoke tests
- [x] `V10-SPLIT-BY-MARKET` — the two bots exist, `bot_v10_all` is retired and owns no rows
- [x] `BOT-DISPLAY-NAME-NOT-THE-KEY` — display_name is never used as a join key
- [x] `MATURITY-LABEL-HAS-A-DB-FIELD` — every label the page renders is a DB-legal value

---
**All phases complete 2026-09-22.** Both parent rows closed in the same commit.
Nothing unticked, so nothing to promote back to the master list.
Archive this doc set to `dev/archive/` once migration 375 has applied on the VPS
and `V10-SPLIT-BY-MARKET`'s live-DB half is green in CI.

## Corrections made during the work (kept — they are the reusable part)
* **"No promotion rule was written" was wrong.** `docs/BETA_PROMOTION_BAR.md`
  specifies experimental->beta. Only beta->calibrated was missing. SYSTEM_MAP now
  references that doc instead of forking a second bar.
* **"Identity and labels only" was wrong.** `maturity_label` is a GATE in four
  places, including the public Telegram channel. Demoting the O/U half to `beta`
  IS a publication change. It costs zero picks today only because that half has
  published nothing since 2026-09-13 — the migration header now says so plainly.
* **The drift test was over-permissive and then over-strict.** See below.
