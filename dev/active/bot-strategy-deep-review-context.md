# BOT-STRATEGY-DEEP-REVIEW — Context

**Status:** 🔄 In Progress — Thread 1 complete 2026-05-13. Threads 2–3 pending.

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

## Thread 1 — Complete (2026-05-13)

- Script: `scripts/bot_strategy_audit.py` — all 23 prematch + 13 inplay bots, funnel + ROI/CLV
- Results: `dev/active/bot-strategy-audit-results.md` — per-bot profiles + ranked lists
- 4 follow-up tasks added to PRIORITY_QUEUE: OPT-AWAY-ODDS-FIX, INPLAY-M-LOOSEN, INPLAY-J-LOOSEN, INPLAY-LIVE-OU-COVERAGE

## Thread 2 — Next session start

Re-read `dev/active/inplay-bot-plan.md` + inplay review summaries. List strategies the 8-AI panel recommended but we didn't ship. Then survey published live patterns (AH momentum, 2H handicap, HT/FT, comeback, derby discount, promoted-team volatility) and prematch gaps (corners, cards, both-halves-over, exact-score, scorecast). For each: one-line rationale + expected fire-rate + data we need.

Output goes to the "Strategies we don't trade today" section of `dev/active/bot-strategy-audit-results.md`.

## Key findings from Thread 1 (don't re-derive)

- **Biggest prematch dead bots:** bot_opt_away_british + bot_opt_away_europe (odds 2.50-3.00 = 0 fires), bot_conservative (0 fires despite 6 Pinnacle-passing bets — Kelly=0 needs investigation)
- **New DC/AH/DNB bots (launched 2026-05-11):** only 2 days live, don't tune yet — dc_value, dc_strong_fav, ah_home_fav, dnb_home_value, dnb_away_value all at 0 fires; ah_away_dog has 13 settled +45.2% ROI (promising)
- **Best performers:** bot_opt_home_lower (+73.4% ROI, +0.333 CLV, 14.5% edge), bot_lower_1x2 (+63.6%, +0.307), inplay_m (+150%, +0.515 CLV — 1 bet)
- **Most active inplay:** inplay_e (241 fired, -2.7% ROI, CLV +0.051 — needs threshold tightening)
- **Live OU coverage: 17%** — the dominant inplay bottleneck, affects strategies e/h/a/d/j
