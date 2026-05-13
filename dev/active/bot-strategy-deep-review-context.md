# BOT-STRATEGY-DEEP-REVIEW — Context

**Status:** ⬜ Not started (filed 2026-05-13)

## What triggered this

Investigating "only 5 inplay bets across 100s of live matches today" surfaced a structural question: across all 36 active bots, are the filter chains calibrated correctly, or have ad-hoc tweaks drifted them away from where edge actually lives?

Concrete prompt: `inplay_j` has 0 settled bets in 14 days. Funnel diagnostic showed the `prematch_o25 ≥ 0.62` gate clears only 0.24% of mid-game snapshots — far tighter than sibling strategies (Strategy L uses 0.55, Strategy I was loosened from 0.54 → 0.50 in `LOOSEN-THRESHOLDS` on 2026-05-08). User explicitly asked not to change J without a bug — but the question generalizes: which other gates are similarly out-of-range, and is there a coherent retuning that improves the system as a whole?

## Key prior context (don't re-derive)

- **Inplay bot daily fire rate since launch:** 1, 10, 193, 32, 7, 15, 6 (May 7–13). The May 9 spike was a bug — `inplay_e` firing 189 times on broken proxy data; 179 were voided. The 5–15/day post-fix rate is the *real* baseline, not a regression. User initially read this as "we changed something and broke it" — corrected.
- **Inplay vs prematch search-space intuition:** prematch wins on volume (20K+ opportunities/day × hours of price drift). Inplay strategies need 5 conjoined conditions to align in narrow time windows + 17% live OU odds coverage. Filter conjunction kills most scenes.
- **Strategy retirement convention:** `retired_at IS NOT NULL` is the authoritative marker. `is_active` is intentionally left true so pending bets settle. Three inplay bots are retired (`inplay_a2`, `inplay_c_home`, `inplay_f`) — exclude from audit.

## Files that will need reading

- `workers/jobs/inplay_bot.py` — all `_check_strategy_*` functions (one per inplay bot)
- `workers/jobs/daily_pipeline_v2.py` — `BOT_CONFIGS` dict for prematch bots
- `scripts/bot_perf_report.py` — existing ROI/CLV reporting; extend or fork
- `dev/active/inplay-bot-plan.md`, `inplay-bot-tasks.md` — 8-AI review history
- `dev/active/inplay-backfill-summary.txt` — recent backfill replay results

## Decisions already made

- **Don't change J unilaterally.** User asked only for bug check; J is not a bug, it's a tight intentional gate. Any change to J goes through this audit's ranked-list process.
- **Don't retire any bot from the audit.** Even bots with zero fires stay in scope — knowing *why* a bot doesn't fire is part of the deliverable.
- **No A/B tests during audit.** Replay-based estimates are sufficient for ranking. Live A/B comes when a ranked item gets implemented.

## Next session start

Pick a thread (1, 2, or 3). For Thread 1, the fastest first move is to extend `bot_perf_report.py` with a `--funnel BOT_NAME` flag that prints the gate-by-gate survival rate over the last 14 days. Build that for one prematch bot + one inplay bot, verify the output reads cleanly, then iterate across the full list.
