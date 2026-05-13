# BOT-STRATEGY-DEEP-REVIEW — Tasks

## Thread 1 — Audit existing strategies

- [x] Build `scripts/bot_strategy_audit.py` skeleton: takes `--bot BOT_NAME`, prints filter chain + 14d funnel + ROI/CLV
- [x] Wire funnel SQL for one prematch bot (`bot_aggressive`) — verify output reads cleanly
- [x] Wire funnel SQL for one inplay bot (`inplay_a`) — verify output reads cleanly
- [ ] Add sensitivity replay: re-evaluate last 14d with limiting gate at ±10%, project fire-rate + ROI (deferred — funnel data sufficient for ranking)
- [x] Run audit across all 23 prematch bots — save to `bot-strategy-audit-results.md`
- [x] Run audit across all 13 inplay bots — append to results
- [x] Rank bots by "loosen-this-gate-for-most-impact" (top 5)
- [x] Rank bots by "tighten-this-gate-it's-too-loose" (top 5)

## Thread 2 — Identify gaps

- [ ] Re-read `dev/active/inplay-bot-plan.md` + 8-AI review summaries; list strategies recommended but not shipped
- [ ] Survey published live-betting patterns we don't trade (AH live momentum, 2nd-half handicap, HT/FT, comeback pricing, derby discount, promoted-team volatility)
- [ ] Survey prematch gaps (corners, cards, both-halves-over, exact-score, scorecast)
- [ ] For each gap candidate: one-line rationale + expected fire-rate
- [ ] Append "Strategies we don't trade today" section to results doc

## Thread 3 — Data sufficiency

- [ ] For each Thread 2 candidate: do we have signals already? If not, what's the collection lead time + API cost?
- [ ] What's the minimum replay window before we can validate edge?
- [ ] Append "Data readiness" column to Thread 2 list

## Wrap-up

- [ ] Convert top-3 ranked adjustments + top-3 new strategies into individual `PRIORITY_QUEUE.md` tasks
- [ ] Each follow-up task must include: smoke test plan, replay-projected ROI, fire-rate delta
- [ ] Move this dev/active/ trio to `dev/archive/` once the ranked list is in PRIORITY_QUEUE
