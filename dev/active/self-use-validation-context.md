# SELF-USE-VALIDATION — Context

> Read first when resuming.

## State summary

Created 2026-05-10 in response to a strategic conversation about pivoting from B2C SaaS to personal-use real-money betting. **Phase 0 not yet started.** No code written yet.

## Why this exists

Margus dislikes B2C marketing. SaaS is hard to grow at small numbers. Engine + ML + signals already exist. The ROI math favours self-use *if the bot edge is real*. Open question: can paper-trading edge survive real-world execution (slippage, limits, accessible-bookmaker-only constraint)?

## Key decisions made in conversation

- **Books:** Coolbet (preferred) + Bet365 (secondary). Both accessible from Estonia.
- **Manual placement only** — no third-party tool automates Coolbet, custom auto-placement violates ToS.
- **Stakes during validation:** €1–3
- **Audience:** superadmin only — gate via `profiles.is_superadmin`
- **Coexist with SaaS** during validation; don't drop SaaS yet
- **Validation budget:** ~6 weeks, 200–250 real bets

## Critical context the next session needs

1. **Kambi scraper was dropped 2026-05-06** (KAMBI-DROP). We don't fetch Kambi public feed anymore. **But** Unibet odds (via API-Football, 443K rows fresh today) are a good proxy for Coolbet — both books run on the Kambi B2B platform.
2. **Bet365 is already in our DB** — 422K rows, fresh, via API-Football. No new integration needed.
3. **Coolbet is NOT directly in our DB.** Phase 0 will determine whether Unibet proxy is good enough or whether we need The Odds API ($0–30/mo).
4. **Avoid duplicating prior cleanup mistakes:** the OU 1.5 high-odds bug we found this morning came from MAX-across-books promoting mislabelled rows. The new OU-PINNACLE-CAP gate filters most of that, but real-money validation should treat any odds >2× Pinnacle as automatically suspicious.

## Files in this set

- `self-use-validation-plan.md` — full plan, decisions, phase breakdown
- `self-use-validation-tasks.md` — checklist (started: ⬜ across all phases)
- `self-use-validation-context.md` — this file

## Next concrete step

**Phase 0.1**: Pick 20 settled paper bets from the last 2–3 days. Mix of bots and league tiers. Build the Unibet vs Bet365 vs Coolbet comparison sheet. Document gaps. ~1 evening of manual work, no code required.

If Phase 0 is delegated to the next agent: hand them the plan + tasks files plus this context. The starting work is a SQL query against `simulated_bets` joined to `odds_snapshots`, plus manual coolbet.ee lookups, plus a results markdown.

## Decision log

- 2026-05-10: Plan created. PRIORITY_QUEUE entry filed.
- 2026-05-10: **Phases 0.1, 2.1, 2.2, 2.3, 2.4, 2.5 all shipped in one session.** Sampling script + migrations applied + settlement wired + backend writer + 3 admin pages (`/admin/place`, `/admin/real-bets`, bot-dashboard columns) + 2 API routes + 3 backend smoke tests. Engine pushed: ef2a671. Web pushed: d26ed3e + 7352858.
- 2026-05-10: Phase 0.3 is the next blocker — user manual step. Open `dev/active/self-use-validation-phase0-worksheet.csv`, fill in `coolbet_actual` column from coolbet.ee for the 26 sampled rows.
- 2026-05-10: **Plan simplified per user.** Phase 0.3/0.4/0.5 (CSV worksheet) marked SUPERSEDED. The `/admin/place` modal captures captured_odds + actual_odds on every real bet, so `real_bets.slippage_pct` IS the proxy-quality measurement. User's working ritual: Coolbet tab → place at displayed price → `/admin/place` tab → click Place → enter actual Coolbet odds + stake → submit. One workflow handles both validation and real betting. CSV preserved for future unbiased sampling but not required.
