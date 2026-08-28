# Phase 0 — Measurement integrity: findings

**Date:** 2026-08-28 · No money spent. Free path only.

## 0.1 — Can we fix the anchor for free? **No.**

Tested a de-vigged multi-book consensus (median and mean across the 15 books we
already collect) against our current Pinnacle anchor, on **4,207 paired settled
fixtures** with Pinnacle plus ≥5 books:

| anchor | Brier (lower = better) |
|---|---|
| Pinnacle | **0.58196** |
| Consensus (median) | 0.58218 |
| Consensus (mean) | 0.58221 |
| Coolbet | 0.59613 |

**A free consensus anchor is statistically identical to our Pinnacle** — no improvement.
Both are clearly better forecasters than Coolbet (0.582 vs 0.596), so the anchor points
the right way, but the only route to a genuinely *sharper* anchor remains paid (real
Pinnacle / exchange prices, ~$99/mo).

This **softens Task 2's Finding 1**: the anchor is not broken, it is merely not sharp.
Its de-vigged probabilities are the best forecast available to us for free.

## 0.2 — Re-scoring our picks against a neutral benchmark

Grading our own picks with our own anchor is circular. Instead: **CLV against the
de-vigged consensus closing price across all books, excluding the book we bet at** (the
sweep bots pick the best price, so leaving that book in the benchmark would guarantee
positive CLV by construction).

**Aggregate: n=431, mean CLV +1.64%, sd 8.5%, t=+3.98, beat the close on 59%.**

The sd of 8.5% matches the ~9% that ANALYSIS_GOTCHAS #8 records as normal — unlike the
bogus AH CLV measurement, whose 24.3% sd was the tell. Against Task 1's benchmarks
("55–60% beating the close looks promising", "1–2% sustained CLV indicates a real
advantage"), this is a genuine if modest edge.

### Per bot — and this is the finding that matters

| bot | n | CLV | t | beat% | realised ROI |
|---|---|---|---|---|---|
| `bot_sweep_ou25_v1` | 132 | **+3.93%** | **5.20** | 70% | — |
| `bot_pin_1x2_home_v1` | 104 | **+2.90%** | **3.24** | 66% | — |
| `bot_sweep_ou35_v1` | 99 | +1.37% | 1.64 | 63% | — |
| **`bot_coolbet_value_v1`** ← **real money** | 44 | **+0.50%** | **0.48** | 59% | −2.5% |
| `bot_sweep_1x2_draw_v1` | 52 | **−5.24%** | **−11.68** | **8%** | — |

**Two conclusions, both uncomfortable:**

1. **We are placing real money on the weakest positive bot.** `bot_coolbet_value_v1`
   sits at +0.50% CLV, t=0.48 — statistically indistinguishable from zero — while
   `bot_sweep_ou25_v1` (t=5.20, n=132, past the n≈78 threshold) and
   `bot_pin_1x2_home_v1` (t=3.24, n=104) both clear the bar comfortably.
2. **`bot_sweep_1x2_draw_v1` is decisively bad.** −5.24% CLV at **t=−11.68**, beating
   the close on **8%** of picks. That is not noise; it is a bot reliably taking worse
   prices than the market close. It should be retired.

### The Coolbet-placeable subsets

Both good bots also pick Coolbet sometimes, and those subsets are placeable today:

| bot @ Coolbet | n | CLV | t | ROI | won |
|---|---|---|---|---|---|
| `bot_sweep_ou25_v1` | 30 | +11.09% | 5.95 | **+18.5%** | 19/31 |
| `bot_pin_1x2_home_v1` | 27 | +4.57% | 2.77 | **+20.0%** | 12/27 |

**Treat the +11% with caution.** It is far above the 1–2% Task 1 calls a real advantage,
and today produced four separate too-good numbers that turned out to be artifacts. Two
checks were run:

* **Circularity** — excluding the picked book from its own benchmark barely moved it
  (10.76% → 11.09%), so that is not the cause.
* **Mislabelled lines** — the documented COOLBET-OU-LINE-SHIFT failure would produce
  good CLV with *bad* ROI. Here ROI is **+18.5%**, so the two metrics agree, which
  argues against an artifact.

Still: **n=30 and n=27 are below the n≈78 CLV threshold**, and the same bots at Unibet
post *negative* ROI (−18.2%, −6.3%). The Coolbet outperformance may be real or may be
small-sample luck. It is a lead, not a conclusion.

---

## What changes as a result

**Immediate, no cost, high confidence:**
* Retire `bot_sweep_1x2_draw_v1` (t=−11.68 across 52 settled picks).

**Operator decision:**
* Whether to move real-money placement from `bot_coolbet_value_v1` to the
  Coolbet-priced subset of `bot_sweep_ou25_v1` / `bot_pin_1x2_home_v1`. Both show
  stronger CLV *and* positive ROI, but on n≈30. `EXECUTE_ALLOWED_BOTS` currently
  permits only `bot_coolbet_value_v1`, deliberately.

**Roadmap revision:**
* 0.1's free path is exhausted — a sharper anchor now requires the paid feed. But 0.2
  shows the current anchor already produces *statistically significant positive CLV*,
  so the anchor upgrade is no longer the blocking prerequisite it looked like. It moves
  from "blocks everything" to "worthwhile improvement".
* The higher-value move is **Phase 2.1 (second venue)**: Unibet is the most-recommended
  book for both high-CLV bots.
