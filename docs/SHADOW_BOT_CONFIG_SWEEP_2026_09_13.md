# Shadow-bot configuration sweep — 2026-09-13

**Question (owner):** *"analyze all shadow bots and their bets… see if each shadow
bot has some winning configuration — bet type, odds range, edge range, sharp edge
range, anything. These are quite soft books, I'm sure we find something."*

**Answer: yes, there are winning configurations — and the single biggest factor is
not the bet type or the odds band. It is WHICH LEDGER the pick came from.**

---

## How this was measured, and why you can trust the shape of it

Run with the repo's own `scripts/floor_grid_sweep.py`, not a new script, so the
guards already built into it apply:

* **Fold-robust only.** A cell qualifies only if it is positive in **every** one
  of 3 walk-forward folds. A cell that is positive overall but negative in a fold
  is discarded.
* **`#robust` is the anti-noise column.** It counts how many of the 90
  (edge × odds) cells around a group survived. **Many robust cells = a broad,
  stable frame. One or two = almost certainly selection noise.** Read this column
  before believing any headline number.
* **Executable basis** — `COALESCE(odds_at_pick_live, odds_at_pick)`, never a
  best-of-books price we could not have taken.
* **CLV vs Pinnacle is the primary metric.** It converges ~30× faster than ROI
  (~334 settled bets vs ~9,300). ROI is reported second and must not be quoted
  without its n.
* **Edge kinds are never pooled.** A 3% sharp edge and a 13% model edge are not
  the same quantity (ANALYSIS_GOTCHAS).

⚠️ **The honest caveat: this is a wide scan.** Many cells were examined, so some
will look good by chance. That is exactly what `#robust` is for — and it is why
the fragile rows below are listed as *not* actionable rather than quietly dropped.

---

## Finding 1 — the ledger matters more than any gate

The same strategy, same market, same edge floor:

| Ledger | group | n | CLV | #robust |
|---|---|---|---|---|
| `sim/calibrated` (pipeline picks) | model / home-dog ≥13% | 127 | **+12.6%** | **56** |
| `sim/cohort` | model / home-dog ≥15% | 83 | **+15.4%** | **63** |
| `shadow/all-prematch` (broad bots) | model / home-dog | 1,435 | **none robust** | **0** |

**The selective pipeline ledger wins; the broad shadow ledger fails — with 11×
the sample.** This is the same effect found separately the same day: the
published `/picks` set (n=638) has CLV **+5.34%, t=+7.7**, while the broad
shadow/trigger population (n=10,861) has CLV **−3.47%, t=−35.0**.

**Implication, and it cuts against recent work:** widening the candidate source
degrades the signal. The `prob_source='predictions'` change made for ~100× more
candidates is pushing in the wrong direction on this evidence.

---

## Finding 2 — the strongest single configuration

**`bot_v10_all` · 1x2 · odds 3.2–4.0 · edge ≥13%**

| | |
|---|---|
| n | 59 |
| win rate | 42.4% |
| **CLV vs Pinnacle** | **+12.00%  (t = +7.64)** |
| ROI | **+51.9%  (t = +2.22)** |
| span | 2026-05-16 → 2026-09-13 (4 months, not one burst) |
| `#robust` | **56 of 90** |

Top of the table on **both** metrics independently, with a broad robust
neighbourhood and a 4-month span. The CLV t-stat is the number to trust; the ROI
is encouraging but n=59 carries a very wide interval.

---

## Finding 3 — the adoptable frames, ranked by evidence

| Rank | Configuration | n | CLV | #robust | Read |
|---|---|---|---|---|---|
| 1 | `sim/cohort` 1x2 **home-dog, edge ≥15%** | 83 | **+15.4%** | **63** | strongest frame in the data |
| 2 | `sim/calibrated` 1x2 **home-dog, edge ≥13%** | 127 | **+12.6%** | **56** | the current real-money gate — validated |
| 3 | `bot_v10_all` 1x2 3.2–4.0, ≥13% | 59 | +12.0% | 56 | same frame, sharper slice |
| 4 | `sim` O/U 2.5 **under, ≥10%, odds ≥2.20** | 102 | +7.4% | 29 | second market that holds up |
| 5 | `sim` 1x2 **home-mid, ≥10%** | 150 | +8.0% | 20 | moderate |
| 6 | `shadow` O/U 3.5 **line-shop over, ≥8%** | 125 | +6.6% | 27 | the line-shop anchor working |

**Fragile — do NOT act on these** (`#robust` ≤ 7, i.e. an isolated cell):
`model / home-fav ≥8%` (+11.0%, n=93, #robust 7) — notable because FAVLONG-CUTS
excluded home-favs, so it would be tempting; the breadth is not there.
`line-shop / home-fav` (+8.8%, n=207, #robust 6) and
`line-shop / home-mid` (+5.2%, n=219, #robust 2) likewise.

---

## Finding 4 — the anchor split, on 90 days of settled bets

| Anchor | n | win% | CLV vs Pinnacle | t | ROI |
|---|---|---|---|---|---|
| model | 10,861 | 48.1% | **−3.47%** | −35.0 | −5.6% |
| **sharp / de-vig** | **1,055** | 41.6% | **+5.64%** | **+14.2** | −0.3% |

n=1,055 clears the ~334-bet threshold for a useful CLV read. **The Shin-de-vigged
Pinnacle signal beats the closing line; the broad model signal loses to it.**
Per-bot, the sharp twins are the two highest CLV bots in the entire system:
`bot_coolbet_trigger_sharp_1x2_v1` **+10.9%** (n=96) and
`bot_unibet_trigger_sharp_1x2_v1` **+10.0%** (n=74).

**Both are paper-only.**

---

## Finding 5 — what is definitively not salvageable by configuration

The model-anchored trigger bots produce **zero robust cells** across the full
90-cell grid:

| bot | n | robust cells |
|---|---|---|
| `bot_coolbet_trigger_1x2_v1` | 262 | **0** |
| `bot_trigger_1x2_model_v1` | 247 | **0** |
| `bot_unibet_trigger_1x2_v1` | 245 | **0** |
| `bot_aggressive` / 4.0+ | 138 | **0** |

No odds band and no edge floor rescues them. That is a stronger statement than
"they are losing" — it says **there is no configuration of them that works**, so
tuning their floors is wasted effort. `bot_coolbet_trigger_1x2_v1` at −24.6% ROI
and −10.1% CLV on n=397 is the clearest case.

---

## What follows (owner decisions — all live-money, none of them implementation details)

1. **Do not widen the candidate source further.** Finding 1 says selectivity is
   carrying the signal; the recent `prob_source='predictions'` widening works
   against it. Worth measuring the wide twins against their narrow parents before
   going further.
2. **The current real-money 1x2 gate is validated** — home-dog at ≥13% on the
   calibrated ledger is rank 2 here (+12.6%, 56 robust cells). The earlier
   reduction to a 10% selection-aware floor is *not* supported by this scan:
   ≥13% and ≥15% are both stronger.
3. **The sharp-anchored bots are the best CLV in the system and are paper-only.**
   Promoting them is the single highest-expected-value change available.
4. **Retire or stop tuning the model trigger bots** (Finding 5).

## Reproduce

```bash
python3 scripts/floor_grid_sweep.py --group-by bot,odds_band --metric clv_pinnacle --min-n 60 --folds 3 --no-idealized
python3 scripts/floor_grid_sweep.py --group-by edge_kind,sel_band --metric clv_pinnacle --min-n 80 --folds 3 --no-idealized
python3 scripts/floor_grid_sweep.py --group-by bot,odds_band --metric roi --min-n 60 --folds 3 --no-idealized
```
