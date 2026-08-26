# SHADOW-BOT-FIXES-2026-08-26 — plan

Origin: operator asked for a profitability + config review of `/admin/shadow-bots`
after a week of config changes (2026-08-19 → 08-24). The review found the live
numbers are honest but statistically empty (active-6 pooled: n=433, ROI +2.4%
± 6.4%, every per-bot |t| < 0.7), and surfaced three real defects plus three
structural problems.

## Phases

| # | Task ID | What | Backtest? |
|---|---------|------|-----------|
| 1 | SHADOW-CLV-BOOKMAKER-FIX | `get_closing_odds()` has no bookmaker filter → CLV = max(13 books) / arbitrary book. Structurally positive, measures nothing. | Yes — CLV→ROI predictive power, old vs new, on full settled history |
| 2 | LINESHOP-SHIN-DEVIG | Multiplicative de-vig on 3-way 1X2 overstates longshot true prob → manufactured edge on draws/dogs. | Yes — Shin vs multiplicative on 3 seasons of Pinnacle 1X2 closes |
| 3 | SHADOW-PROMOTION-GATE | `MIN_SETTLED_FOR_DECISION = 50` → SE ±19%. Gate is a coin flip. | Yes — Monte-Carlo false-promote / false-retire rates |
| 4 | SHADOW-DISCRETION-BLEED | Real-money marked picks −18.2% (n=147) vs unmarked +7.7% (n=415). Repeat of Phase-3 selection bias. | Yes — split-half + per-bot stability |
| 5 | SHADOW-OU-EDGE-AUDIT | Persistent +13% "edge" vs Pinnacle close on OU that never closes → line mislabel suspect. | Yes — line-integrity sweep over OU history |
| 6 | SHADOW-BETS-UNIQUE-VIEW | Multi-cohort writes 48 rows/pick/day. 7 scripts query raw. | No — mechanical |

## Risks

- Changing de-vig changes which picks fire. Must NOT retune thresholds on
  backtest ROI — PER-BOT-SWEEP-2026-08-24 showed that is anti-predictive
  (-9.2% OOS). Shin is a mechanism fix, judged on calibration, not ROI.
- CLV backfill rewrites historical `clv` on shadow_bets + simulated_bets.
  Keep the old value in a new column rather than destroying it.
