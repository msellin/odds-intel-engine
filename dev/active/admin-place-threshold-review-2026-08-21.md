# /admin/place Threshold Review — 2026-08-21

**Task:** PERF-CONFIG-THRESHOLDS-REVIEW (filed 2026-08-21, P2)
**Scope:** Are the per-market edge floors used by `/admin/place` and `coolbet_placer.py` still correctly calibrated given 60-90 days of accumulated data?
**Method:** Ran `scripts/edge_threshold_backtest.py` against live `simulated_bets` (n=4,572 settled since 2026-05-01) + a per-bot sweep for the highest-volume bots. Data pulled 2026-08-21.

**Output:** proposal only. No config changed yet — this doc → your decision → shipping.

---

## TL;DR

| Market | Current floor | Recommended | Δ ROI (all bots) | Sample @ new floor |
|--------|--------------|-------------|------------------|---------------------|
| 1x2 | **10%** | **12%** | +8.6% → **+20.4%** | n=769 (was 1,072) |
| o/u | **3%** | **8%** | +1.6% → **+5.1%** | n=979 (was 1,341) |
| asian_handicap | **5%** | **retire OR raise to 15%** | −4.0% → +1.4% @ 15% | n=210 (was 535) |
| btts | **10%** | **10%** (keep) | +11.6% (unchanged) | n=122 |
| double_chance | **retired** | keep retired | — | — |
| combo | **10%** | keep (thin data) | — | — |
| draw_no_bet | **5%** | re-review after Aug 15 season restart | starved by summer | — |

Two headline moves: raise **1x2 to 12%** (+11.7pp ROI on 40% less volume) and raise **o/u to 8%** (+3.5pp ROI on 27% less volume). Together they lift portfolio ROI meaningfully at moderate volume cost.

---

## The data

Source: `simulated_bets` where `result <> 'pending'` and `edge_percent IS NOT NULL`, since 2026-05-01, n=4,572. The prior calibration (PER-MARKET-EDGE-V2, 2026-06-06) used n=3,086 — we now have ~50% more data.

Reading these tables:
- `thresh` — take all bets where `edge_percent >= thresh`
- `n` — bets that pass
- `cov` — % of the market's bets that pass
- `ROI%` — cumulative pnl / stake at that gate
- `CLV%` — mean CLV (sharpness signal)

### 1x2 (n=1,849)

```
thresh     n  cov   win%    ROI%       PnL    CLV%
    3%  1849 100%   31.8   +0.80    +80.57   +2.96
    5%  1768  96%   32.2   +1.90   +184.12   +3.06
    7%  1556  84%   32.8   +3.46   +292.57   +4.36
    8%  1393  75%   32.8   +3.71   +278.96   +5.49
   10%  1072  58%   32.9   +8.61   +489.66   +8.85    ← current
   12%   769  42%   33.0  +20.35   +810.51  +15.55    ← recommended
   15%   493  27%   29.2  +12.75   +325.37  +18.85
   20%   234  13%   31.8  +22.26   +264.22  +72.21
```

**Key insight:** the 10–12% edge band is a drag. Bets in that band have ~0% ROI marginally, so filtering them out lifts the aggregate from +8.61% to +20.35%. CLV also nearly doubles (8.85% → 15.55%), meaning the picks that survive a 12% gate are visibly sharper.

**Cost:** ~30% fewer 1x2 bets. In practice ~2 fewer picks/day on average.

**Recommended: `_MIN_EDGE_BY_MARKET["1x2"] = 0.12`**

---

### o/u (n=1,341)

```
thresh     n  cov   win%    ROI%       PnL    CLV%
    3%  1341 100%   51.1   +1.63   +125.68  +10.72    ← current
    5%  1247  93%   51.3   +2.42   +173.27  +11.44
    7%  1072  80%   51.3   +2.19   +133.20  +10.32
    8%   979  73%   53.0   +5.14   +283.31  +10.31    ← recommended
   10%   747  56%   54.6   +7.12   +278.52  +13.99
   12%   590  44%   57.4   +8.86   +262.63  +22.56
   15%   484  36%   56.9   +5.21   +126.13  +33.84
```

**Key insight:** o/u has been on the 3% floor since forever — the reasoning was "already profitable at floor". But the 3–8% band contributes basically no marginal ROI. The 8% gate more than triples the return with only 27% of bets dropped.

**8% is the "obvious" pick** (sharpest ROI jump between adjacent rows: +2.19% → +5.14%). 10% is stronger but the marginal PnL gain (+283 → +278) is essentially zero at that step — 10% just wins by concentration. 8% keeps more picks per euro of PnL.

**Recommended: `_MIN_EDGE_BY_MARKET["o/u"] = 0.08`** (10% if you want maximum concentration)

---

### asian_handicap (n=543)

```
thresh     n  cov   win%    ROI%       PnL    CLV%
    3%   543 100%   49.6   -3.98   -113.11   +5.63
    5%   535  99%   49.4   -3.99   -111.79   +5.62    ← current
    7%   491  90%   49.5   -5.53   -140.51   +5.39
    8%   458  84%   50.0   -4.06    -96.16   +5.53
   10%   378  70%   51.3   -3.64    -70.75   +5.71
   12%   308  57%   52.4   -1.78    -27.72   +5.63
   15%   210  39%   54.2   +1.38    +14.42   +5.36
   20%    57  10%   57.7   +3.50     +9.34   +5.68
```

**Key insight:** AH is losing money at every threshold below 15%. The current 5% floor has us at −4.0% ROI on 535 bets — that's ~€110 of paper losses over 60d, and it would be real money if any AH bot was in the `calibrated` cohort.

**Two options:**
1. **Retire AH** like `double_chance` was — set `_MIN_EDGE_BY_MARKET["asian_handicap"] = None`. Clean cut.
2. **Raise to 15%** and monitor. At 15% the sample is 210 bets with +1.4% ROI and +5.4% CLV — marginal but non-zero. The CLV being positive means the model isn't completely blind on AH, it's just noisy.

**My lean:** Option 2 (raise to 15%). CLV is consistent across all thresholds — the model does have some AH signal, we're just paying too much variance at loose gates. This preserves AH as a lever if edge distributions shift. But **retiring is defensible** given the DC precedent.

**Recommended: `_MIN_EDGE_BY_MARKET["asian_handicap"] = 0.15`** (or `None` if you prefer clean-cut)

---

### btts (n=355)

```
thresh     n  cov   win%    ROI%       PnL    CLV%
    3%   355 100%   46.6   -1.55    -35.60   +4.30
    5%   321  90%   45.3   -4.89   -101.94   +4.29
    7%   249  70%   47.4   +0.51     +8.07   +4.30
    8%   205  58%   46.6   -0.81    -10.42   +4.90
   10%   122  34%   50.8  +11.59    +81.19   +5.22    ← current, keep
   12%    59  17%   39.0  -13.87    -42.10   +4.38
   15%    33   9%   36.4   -9.58    -15.57   +0.00
   20%    23   6%   39.1   -2.32     -2.61   +0.00
```

**Key insight:** BTTS has a sharp single-peak profile at exactly **10%**. Above 12% ROI craters. Current setting is precisely right.

**Recommended: no change.** Keep at 0.10.

---

### double_chance (n=274)

Losing at every threshold from 3% to 20%. `_MIN_EDGE_BY_MARKET["double_chance"] = None` (retired) confirmed correct.

**Recommended: no change.**

---

## Per-bot findings

The market-level analysis above is the primary recommendation. But some bots deserve individual notes:

### bot_v10_all (n=467, calibrated) — the workhorse

Shows extreme lift at higher edges on **both** markets it plays:

```
1x2  n=309:    3% → +16.6%   10% → +23.5%   12% → +35.5%   15% → +34.9%
o/u  n=158:    3% →  +7.8%   10% → +16.4%   12% → +50.5%*  (*thin, n=18)
```

At the recommended new floors (1x2 @ 12%, o/u @ 8%), bot_v10_all volume drops from ~467 → ~250 settled/60d but ROI roughly doubles. Portfolio-positive trade.

### bot_high_roi_global_v2 (n=35, beta) — 1x2 specialist

Already fires at ≥10% edge by design (only 1 of 35 bets was in the 3-10% band).
- @ 10%: +34.91% ROI, n=34, CLV +17.54%
- @ 12%: +28.54% ROI, n=22, CLV +19.96%

Raising 1x2 to 12% drops ~35% of this bot's volume. Volume is already thin (35 bets in 60d ≈ 4/week). Trade-off is real but the survivors are sharper. Acceptable.

### bot_btts_all (n=194, beta) — the underperformer

The single-market bot for BTTS. In the 60-day window this is the *only* bot with material BTTS volume that clears the 10% floor.

- @ 10%: +10.35% ROI, n=48 — matches the market-level BTTS finding
- @ 5% (an earlier proposed loosening, BTTS-CALIBRATION-GAP-LOOSEN 2026-07-19 dropped BTTS bot threshold from 12% → 7%): −6.33% ROI on n=185

**Finding contradicts BTTS-CALIBRATION-GAP-LOOSEN.** That earlier tweak dropped the bot's own gate from 12% → 7%, expecting more volume without ROI harm. Data now shows the 7% floor is where losing kicks in for BTTS specifically. **Consider re-tightening bot_btts_all's own gate back toward 10%** (the market floor) — the "calibration compression" argument still holds in principle, but the empirical ROI curve says 10% is the operational sweet spot.

### bot_summer_specialist (n=13, beta) — the user's example

The bot the user asked about specifically ("🟡 Cautious · ✗ edge < 10% · bot_summer_specialist").

- Total: 13 settled bets in ~44 days
- 1x2 n=10 (too thin), o/u n=3 (too thin)

**Cannot conclude anything about the right threshold for this bot yet.** The 10% market floor may be reasonable or too tight — sample is too small either way. The Cautious label reflects "beta maturity + thin bot data" which is exactly right for a 13-bet bot.

**Recommendation:** re-run this analysis for bot_summer_specialist once n≥30 (approx. mid-September at current pace). The BOT-SUMMER-SPECIALIST-REVIEW gate (2026-08-08, from PRIORITY_QUEUE) is already overdue and depends on sample size, not just calendar.

### bot_opt_home_lower (n=56, active) — 1x2

```
1x2 n=56:    3% → +7.0%    10% → +10.2%    12% → +40.3%   15% → -50.0% (n=11, thin)
```

Massive lift at 12% but 15% collapses. A dedicated bot-specific 12% floor would concentrate this bot's picks well.

### bot_conservative (n=23, active) — 1x2

Small sample but nicely behaved: positive at every threshold, +93% at 15% on n=11. Would benefit from any 1x2 threshold raise.

---

## Rollout recommendation

**Ship both market-level changes together** as one env change:

```bash
# In VPS .env (add or modify)
COOLBET_MIN_EDGE_1X2=0.12         # was implicit 0.10
COOLBET_MIN_EDGE_OU=0.08          # was implicit 0.03
COOLBET_MIN_EDGE_AH=0.15          # was implicit 0.05 — or set to null to retire
```

...but the current code has the floors hardcoded in `_MIN_EDGE_BY_MARKET` (coolbet_placer.py:75). Changing them requires a code edit + deploy, not just an env change. Two options:

1. **Code edit** — update the dict literal, add a smoke test asserting the new values, commit + push, VPS auto-restart picks it up.
2. **Refactor first** — make each floor env-driven (`float(os.getenv("COOLBET_MIN_EDGE_1X2", "0.12"))`) so future tunes are one env-flip + restart. Small refactor, ~15 min.

I'd do (2) — it makes the *next* review a 5-min operation instead of a code deploy.

**Rollback:** if the tightened floors cause a volume collapse or an unexpected ROI drop after ~2 weeks (n≥300 new bets), revert the env vars and re-analyse.

**Re-review cadence:** every 60d of new data OR after any major model retrain (whichever comes first).

---

## Followups (not in this task, but surfaced)

1. **bot_btts_all bet-level threshold** — BTTS-CALIBRATION-GAP-LOOSEN 2026-07-19 dropped the bot's own edge gate to 7%. Data now says 10% is the empirical sweet spot. Consider reverting to 10%. File as `BTTS-BOT-THRESHOLD-RESTORE`.
2. **bot_summer_specialist review** — trigger a threshold sweep once n≥30 (approximately 2026-09-15). Depends on volume, not calendar.
3. **Per-bot floors** — the data shows bot_v10_all + bot_opt_home_lower both benefit strongly from a 12% 1X2 floor. If we ever want bot-specific gates (currently only market-specific), these two are the first candidates.
4. **draw_no_bet re-review** — DNB-ZOMBIE-DIAGNOSIS says summer-starved, expected to un-block at Aug 15 European season restart. Re-sweep DNB threshold at ~2026-09-20 after 30 days of restart data.
5. **The 25%+ edge bucket in the last-14-days data** — showed −86% ROI on n=26. Classic "fantasy odds" pattern (see FOREBET-OU-VERIFY-2026-08-01). Likely already caught by ODDS-OUTLIER-FILTER-2026-08-18 and CLV-AUTOVOID-2026-08-19 — verify next audit.
