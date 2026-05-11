# SELF-USE-VALIDATION — Context

> Read first when resuming.

## State summary

Created 2026-05-10. **Phases 0, 2, and 2.8 fully shipped. Phase 3 is the only remaining work.**

- Phase 0: sampling script done, CSV worksheet at `dev/active/self-use-validation-phase0-worksheet.csv`
- Phase 2: DB (migrations 091–092), settlement integration, backend writer, 3 admin pages, 2 API routes all live
- ACCESSIBLE-BM (2026-05-11): engine only uses accessible-bookmaker odds; `recommended_bookmaker` stored per bet; `scripts/daily_picks.py`
- Phase 2.8 (2026-05-11): all 3 remaining tasks shipped:
  - `scripts/real_perf_report.py` — paper vs real P&L with slippage, by-bookmaker, by-market, recent bets
  - Bookmaker display on value-bets page (Elite only): "Bet365: 2.10 · Unibet: 2.05 ← Bet365" per bet
  - Freshness indicator: "Odds verified Xm ago" chip (green <45m, amber <90m, red ≥90m)
- Next blocker: user manual step — start Phase 3 daily ritual (open `/admin/place`, place bets, log)

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

**Phase 3 daily ritual** — manual user action:
1. Run `python3 scripts/daily_picks.py` each morning to see today's picks with bookmaker
2. Open `/admin/place` → check Coolbet/Bet365 for each pick → click Place → log actual odds + stake
3. Results settle automatically at 21:00 UTC; check `/admin/real-bets` for running P&L

After ~250 bets (~6 weeks), generate cohort report and evaluate Phase 4 pivot decision.

**Remaining engine work before Phase 3 begins:**
- REAL-MONEY-TRACKER: `scripts/real_perf_report.py` (paper vs real P&L comparison)
- BOOKMAKER-DISPLAY: frontend value-bets page showing per-book odds
- FRESHNESS-INDICATOR: "odds verified Xmin ago" on value-bets page

## Decision log

- 2026-05-10: Plan created. PRIORITY_QUEUE entry filed.
- 2026-05-10: **Phases 0.1, 2.1, 2.2, 2.3, 2.4, 2.5 all shipped in one session.** Sampling script + migrations applied + settlement wired + backend writer + 3 admin pages (`/admin/place`, `/admin/real-bets`, bot-dashboard columns) + 2 API routes + 3 backend smoke tests. Engine pushed: ef2a671. Web pushed: d26ed3e + 7352858.
- 2026-05-10: Phase 0.3/0.4/0.5 (CSV worksheet) marked SUPERSEDED — `/admin/place` modal captures captured_odds + actual_odds on every real bet, so `real_bets.slippage_pct` IS the proxy-quality measurement.
- 2026-05-11: **ACCESSIBLE-BM shipped.** Core measurement fix: engine now only aggregates odds from EU/Estonia-accessible bookmakers (Bet365, Unibet, Betano, Marathonbet, 10Bet, 888Sport, Pinnacle). Previous reported CLV of +12.56% was inflated by SBO/Dafabet/1xBet odds the user can never reach. `recommended_bookmaker` stored on every new `simulated_bets` row (migration 094). `scripts/daily_picks.py` for morning ritual. Engine pushed: 0b05d3b.
- 2026-05-11: **Strategic context:** Betfair Exchange blocked for Estonia (Dec 2025). Pinnacle API closed (July 2025). No automatable book available to Estonian residents. Both automation-era tasks (Super Elite tier) deferred until 500+ users. Current focus: validate real edge via manual betting at Coolbet + Bet365.
- 2026-05-11: **Phase 2.8 complete.** REAL-MONEY-TRACKER (`real_perf_report.py`), BOOKMAKER-DISPLAY (Elite value-bets page, server-side `getValueBetBookOdds`), FRESHNESS-INDICATOR (header chip, `getOddsVerifiedAt`). All Phase 2 work is done. Phase 3 (manual betting) is the only next step.
