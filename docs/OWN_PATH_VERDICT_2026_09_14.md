# 🤖 OWN path — verdict, 2026-09-14

**The automated-betting product on EMTA-legal books is CLOSED.** The kill
criterion the OWN-path audit pre-specified was met decisively, and it was met
today rather than in two weeks, because the blocker the audit identified turned
out to be our own query artefact.

## The test

From the OWN-path audit: on fixtures all three **self-scraped** books price, take
the best price per selection across the three, and measure the residual
overround.

> **Kill criterion: if the median best-of-3 overround stays above ~2%, no line
> shopping strategy on Estonian books can pay its own margin, and the automated
> betting product should be closed.**

`scripts/own_path_kill_criterion.py`. Self-scraped books only — Coolbet,
Epicbet, Unibet-Site. No AF-fed book is used as a price, because three of three
AF-fed books checked against their own site were unfaithful.

## The result

359 time-aligned fixtures (≤15 min), 1X2, same fixtures, same moment:

| | median overround |
|---|---|
| Coolbet | 7.71% |
| Epicbet | 7.92% |
| Unibet-Site | 8.03% |
| Coolbet + Epicbet | 6.22% |
| Coolbet + Unibet-Site | 6.63% |
| Epicbet + Unibet-Site | 6.53% |
| **best of all three** | **5.66%** |

**Line shopping across every book Estonia allows recovers 2.05pp of a 7.71pp
margin.** Residual per-outcome margin ≈ **1.89pp** — every bet starts ~1.9%
under water before any skill is applied.

Robust to the alignment tolerance:

| tolerance | n | best-of-3 | verdict |
|---|---|---|---|
| ≤5 min | 28 | 5.33% | ❌ met |
| ≤15 min | 359 | 5.66% | ❌ met |

The audit's estimate was 6.0–6.5% from a biased 5.9% slice. The clean number is
**5.66%** — slightly better than predicted, and still **2.8× the kill threshold**.

## The correction: the two-week wait was unnecessary

The audit reported that only **5.9%** of co-priced market-fixtures have quotes
within 15 minutes of each other, concluded *"today you cannot compute a
trustworthy cross-book comparison at all, let alone act on one"*, and
recommended two weeks of synchronised polling before the test could run.

**That 5.9% was our own query artefact, not a property of the data.**

`odds_snapshots` timestamps each **row** individually. Coolbet's 1X2 triple
lands across ~100 milliseconds:

```
00a41a30  home  09:20:15.928829+00
00a41a30  draw  09:20:15.975788+00
00a41a30  away  09:20:16.026222+00
```

So grouping on exact timestamp equality finds a complete triple in **0.1%** of
Coolbet timestamp-groups, against **99.9%** for Epicbet and **100%** for
Unibet-Site. Every cross-book query that grouped this way was measuring Coolbet's
write granularity.

Assemble each book's triple from a ±2 min window and the picture changes:

| | fixtures | aligned ≤15 min |
|---|---|---|
| exact-timestamp grouping | 17 | 5.9% |
| ±2 min assembly | **1,073** | **33.5%** |

Coverage was never the problem. **Nothing needed collecting.**

**The schedule "fix" would also not have worked.** The three feeds are
nominally on different minutes (Epicbet :02/:32 VPS, Coolbet :03/:33 Mac,
Unibet-Site :15/:45 Mac), but none of them writes at its cron minute: each sweep
smears across 10–25 minutes, and Coolbet's Mac daemon writes near-continuously
(~309 distinct write-minutes/day vs the 48 a :03/:33 cron would produce).
Aligning the start minutes would not have aligned the per-fixture write times.

**Generalise this.** `ANALYSIS_GOTCHAS` §10 says an unpaired cross-book
comparison measures coverage, not price. This adds the sibling trap: **an
exact-timestamp join across books measures write granularity, not
simultaneity.** Assemble per-book quotes from a window before comparing.

## Why this is structural, not a modelling failure

* De-vigged 1X2 probabilities across EMTA-legal books agree to a median of
  **0.62–1.01pp** while the books charge 7–9pp. There is nothing to harvest.
* The bolt-on markets are not soft: Coolbet prices corners, cards and team
  totals at a flat **8.00%**, the same as its 1X2. Epicbet's 1H 1X2 (6.19%) is
  *tighter* than its own 1X2.
* Cards looked like the exception and is a trap — the books price **different
  quantities** (implied P(over) on `cards_ou_65`: Coolbet 0.176, Epicbet 0.270,
  Pinnacle 0.346, Bet365 0.482). Building the cards bot the opportunity count
  invited would have meant betting a units mismatch.
* Corners are settleable on only **16.7%** of finished fixtures — a corners bot
  cannot be *evaluated* on 5 of every 6 bets it places.
* Confirmed real money: **139 settled placements, €1,390 staked, −€97.50,
  ROI −7.01%.**
* Confirming a true +3% ROI at 80% power needs **≈15,600 settled bets** ≈ 9
  years at the current rate.

The two cheap books in the world (Pinnacle, Betfair) are both EMTA-blocked. Our
own residual test found the model adds **α = 0.0000** over a market price. There
is no combination of these facts that produces a positive expectation.

## What stays, what stops

**STOPS:**
* Building new bolt-on market bots (corners, cards, team totals, 1H) on the
  current evidence.
* Any further work premised on finding cross-book dispersion among EMTA books.

**STAYS:**
* **Ingestion of all three self-scraped feeds.** They are now the only
  price source we can verify, and they are what makes AF feed infidelity
  measurable. This verdict is about what we BET, not what we store.
* The existing real-money Coolbet placement path, **paused, not deleted.** If
  the economics change (a new licensed book with real dispersion, a promotional
  regime), the machinery is there.
* `ACCESSIBLE_BOOKMAKERS` and its new feed-alive guard — still correct, still
  needed for honest published prices.

**The one thing with a positive expected value at this scale**, per the OWN
audit and not disputed here: **bonuses, free bets, odds boosts, acca
insurance.** Closing-line-independent, requires no model, and at Estonian retail
is realistically low four figures per year for a few hours a month — more than
any figure this system can currently justify. That is a product decision, not an
engineering one.

## Reproduce

```bash
python3 scripts/own_path_kill_criterion.py --days 30 --align-min 15
```

Exit code 1 = kill criterion met. Read-only.

---

## CORRECTION (same day) — real money was live until 2026-09-13, not dormant

Migration `343_own_path_pause_real_money.sql` justifies the pause partly on the
claim that *"no real bet has been placed since 2026-09-03"*. **That claim is
wrong.** It came from reading the tail of a `ORDER BY ... DESC` result and
mistaking the oldest rows for the newest. The correction does not weaken the
case for pausing — it strengthens it considerably.

Real money placed in the days immediately before the pause:

| date | bets | staked | P&L |
|---|---|---|---|
| 2026-09-13 | 3 | €30.00 | −€30.00 |
| 2026-09-12 | 7 | €70.00 | −€9.10 |
| 2026-09-11 | 8 | €80.00 | −€20.50 |
| 2026-09-10 | 1 | €10.00 | −€10.00 |
| 2026-09-09 | 6 | €60.00 | −€60.00 |

**22 real bets, €220 staked, −€99.60, ROI −45.27%** (`placed_real = TRUE`).

Set against the all-time settled real-money record — **142 bets, €1,420 staked,
−€97.50, ROI −6.87%** — the arithmetic is stark: **essentially the entire
all-time loss was incurred in those final five days.** Before 2026-09-09 the
real-money book was approximately break-even.

That window is exactly the O/U calibrator's last days of operation before
migration 335 removed it. The bug's real-money damage was heavily concentrated
at the end, and the pause landed one day after the worst of it.

**Two lessons worth keeping:**

1. **The placer was never dormant.** It looked dormant only because of the
   reading error above. Anyone reasoning about whether this system is "live"
   should query it, not infer it — the same reflex `ENGINE-DEPLOY-2026-08-24`
   records for deploy state ("assume nothing about what is live on the box").
2. **A concentrated loss window is a signal, not noise.** −45% over 22 bets is
   not variance around a −7% mean; it is a different regime, and it lines up
   exactly with the defect's final days. When a ledger's damage clusters in
   time, look for a cause with the same timestamps.
