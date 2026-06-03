# Edge-drift analyses — morning report

**Date**: 2026-06-02 → 03 (overnight run)
**Source data**: 30/60/90-day windows from `simulated_bets` × `odds_snapshots`
**Scripts**: `analyze_inplay_edge_drift.py`, `analyze_prematch_closing_drift.py`, `analyze_inplay_pick_followup_drift.py`, `per_bot_clv_audit.py`
**Per-bet CSVs**: `dev/active/*-drift-*.csv` and `dev/active/per-bot-clv-*.csv`

---

## REVISED 2026-06-03 (Pinnacle-coverage bias correction)

The original report's "+30.2% Pinnacle CLV for bot_v10_all" and "+5.0% ALL MARKETS ROI vs Pinnacle close" were biased by Pinnacle coverage. The script only counted bets that had a Pinnacle T-0 snapshot, which silently dropped picks on markets/leagues Pinnacle doesn't price. **Honest total ROI** (no Pinnacle filter, same 60d window, n=full):

```
bot                  nTot  roiTotal  nPin   roiPin  nNoPin  roiNoPin  biasPP
bot_v10_all           157   +17.5%    76   +30.2%     81   +4.9%    +12.7
bot_lower_1x2          56   +18.8%    27   +20.1%     29  +17.8%     +1.3
bot_aggressive        682    −2.2%   251   +4.8%     431  −6.1%      +7.0
bot_high_alignment    228    −6.1%    95   +5.2%    133  −14.2%     +11.4
bot_ou35_attacking     33   −40.9%    16  −51.0%      17 −31.0%     −10.1
bot_ou15_defensive     20   +57.8%     0   —          20 +57.8%       —
bot_opt_home_lower     20   +51.9%     3  +17.1%      17 +56.5%     −34.9
```

Three revised conclusions:

- **bot_v10_all** still has real edge: **+17.5% ROI total**, n=157. The Pinnacle-only +30.2% overstated by ~13pp because Pinnacle-covered markets (1x2, big leagues) happen to be where the model is strongest.
- **bot_lower_1x2** looks legitimately profitable on the 60d window: **+18.8% ROI on n=56**. Its retirement reason (2026-06-01, migration 156) cited "-7.58% on 44 bets" — different time slice. **Worth reviewing the retirement.** Investigate before unretiring.
- **bot_aggressive retirement was correct**: total ROI **-2.2% on n=682**. The Pinnacle-matched +4.8% was misleading.

Bias direction varied by bot — Pinnacle-matched ROI was higher for bot_v10_all / bot_aggressive / bot_high_alignment / bot_aggressive_v2 (positive coverage bias), but LOWER for bot_proven_leagues / bot_high_roi_global / bot_opt_home_lower / bot_ou25_global (negative — they bet markets Pinnacle skips but our model happens to nail).

The headline "+5.0% ALL MARKETS vs Pinnacle close" is therefore not an honest portfolio CLV number — it's the ROI on the Pinnacle-covered subset. A true portfolio CLV would need a sharp benchmark that prices every market we touch (which doesn't exist for niche markets).

Also fixed today: **AH `recommended_bookmaker` 100% NULL bug**. 276/276 Asian Handicap simulated_bets in the prior 60d had NULL bookmaker — the AH branch in `_load_today_from_db` only wrote to `ah_best`, never to `best_bookmaker`. Fix shipped. Smoke test NULL-BOOKMAKER-AH-FIX guards.

Remaining real follow-ups (filed as MODEL-CLV-FOLLOWUP P1 + a new BOOKMAKER-NULL-NON-AH P2):
- ~~Investigate bot_lower_1x2 retirement~~ — **DONE 2026-06-03.** Retirement stands. Weekly decomposition: 2026-05-04 ROI +63.6% (n=10), 2026-05-11 +215% (n=1, single outlier), 2026-05-25 -4.3% (n=45) — only the last week is statistically meaningful and confirms the retirement decision. The "+18.8% / 60d" total was outlier-dominated by 11 small-sample early-period bets. CLV +6.0% in the retirement window matches the cited +6.16% so retirement is well-grounded on the actual data, not a stale finding.
- 1x2 and o/u also have ~33-36% NULL recommended_bookmaker (not 100% like AH). Separate root cause — likely accessible-book filter dropping picks, or strategy_profile rewrites bypassing the bookmaker join.

## Pinnacle cohort split — confirmed real per-bot signal

The morning-report "narrowed +12.3% / widened -24.2%" finding wasn't bot-mix confound. Per-bot decomposition (2026-06-03):

```
bot                  narrowed_n  narrow_ROI   widened_n   wide_ROI
bot_v10_all              59         +36.6%        17         +9.1%
bot_aggressive          179         +10.3%        72         -9.8%
bot_aggressive_v2        26          +3.4%         8        -25.9%
bot_high_alignment       60         +11.3%        35         -4.8%
bot_ah_home_fav          10         +18.5%        12        -22.6%
bot_ou25_global          19         +34.9%        13        -63.2%
```

6 of 8 bots with both cohorts show narrowed > widened, often by 20-90pp. The two exceptions (bot_ah_away_dog, bot_lower_1x2) have widened-cohort n=5-6 so not robust.

**Implication**: when Pinnacle's line moves *toward* our pick by close (the bet's "edge widens" against the close), ROI drops. When Pinnacle moves *against* our pick (edge narrows), ROI improves. This is potentially tradeable as a **delayed-placement filter** — wait N minutes after pick, check if Pinnacle has moved against us, only place if it has. But the win is conditional on detecting Pinnacle movement within a small window, which requires reliable inplay Pinnacle snapshots that we don't currently have (Pinnacle's market depth on niche fixtures is thin).

**Not in scope for action right now.** Filing as PINNACLE-COHORT-FILTER (P3 / research) for re-evaluation once we have better Pinnacle snapshot coverage.

---

## TL;DR — three things worth acting on

1. **The model DOES beat Pinnacle's closing line.** ALL-MARKETS ROI vs Pinnacle close is **+5.0%** (n=517, 60d). The earlier all-books finding (-2.2% ROI) was contaminated by inaccessible bookmakers (SBO/Dafabet/etc) inflating reported edge. When you measure against the sharp book on the markets it actually covers, we are net positive.

2. **bot_v10_all and bot_lower_1x2 are the +EV survivors.** Both clear Pinnacle CLV at +30.2% and +20.1% ROI respectively over 60 days. bot_aggressive is marginal (+2.1%) but on a huge sample (n=259). **Everything else loses money** — the DC bots especially, with -13% to -37% ROI and zero Pinnacle coverage to validate against.

3. **Counter-intuitive Pinnacle cohort split deserves investigation.** Picks where Pinnacle's line moved *toward* our model by close (edge "widened") returned **-24.2% ROI**; picks where Pinnacle moved *away from* us (edge "narrowed") returned **+12.3% ROI**. The intuition that "beating the close = +EV" is inverted here. Most likely bot-mix confound (the widened cohort is dominated by losing bots, the narrowed by bot_v10_all) but worth decomposing per-bot before acting.

---

## Findings by surface

### 1. Prematch → Pinnacle close (60d, the headline)

The strictest CLV test. Restrict snapshot lookup to Pinnacle only — the sharpest book — and re-run the closing-drift script.

```
ALL MARKETS  T-6h   pickEdge +8.36%  closeEdge +7.04%   drift -0.75pp   ROI -4.4%
             T-2h            +8.38%            +6.81%   drift -1.31pp   ROI +2.8%
             T-30m           +8.09%            +6.77%   drift -1.03pp   ROI -9.5%
             T-0             +8.52%            +7.13%   drift -1.22pp   ROI +5.0%

1x2          T-0   n=300   pickEdge +9.04%   closeEdge +7.08%   ROI +7.3%
o/u          T-0   n=202   pickEdge +8.14%   closeEdge +6.97%   ROI +1.3%
asian_h.     T-0   n=15    pickEdge +10.61%  closeEdge +8.34%   ROI +15.6%

COHORT SPLIT AT T-0:
  widened   (n=112)   winRate 34.8%   ROI -24.2%
  narrowed  (n=405)   winRate 44.9%   ROI +12.3%
```

**Reading**: the model's pick edge collapses by ~1.22pp on average by Pinnacle close. That's *less* than the all-books 2.46pp drop — confirming a chunk of the apparent edge in the all-books picture was synthetic (driven by books we can't actually place at). But the headline ALL MARKETS T-0 ROI of **+5.0%** is the cleanest "do we have edge?" number we've ever produced.

**Counter-intuitive cohort split**: picks where Pinnacle's line moved toward our model (i.e., the closing edge was *larger* than our pick edge) lost 24.2%. Picks where Pinnacle stayed conservative (edge narrowed) made +12.3%. Probable explanations:
- Bot-mix: bot_v10_all dominates the "narrowed" cohort (its calibrated edge tends to be tighter than pick edge by close), while DC bots — which have inflated pick edges that books slowly catch up on — dominate "widened".
- Investigated 2026-06-03 morning.

### 2. Prematch → all-books close (60d, baseline)

```
ALL MARKETS  T-0   n=772   pickEdge +8.98%   closeEdge +6.38%   drift -2.46pp   ROI -2.2%

COHORT SPLIT AT T-0:
  widened   (n=57)    winRate 42.1%   ROI -3.4%
  narrowed  (n=715)   winRate 44.1%   ROI -2.1%
```

Drift more negative (-2.46pp) and cohorts both unprofitable — the all-books-priced edges look much worse than Pinnacle-priced. This is direct evidence that **at least 1.2pp of our "edge" comes from inaccessible bookmakers**, not real value.

(90d ran identically to 60d — `simulated_bets` history caps out around 30 days back so the lookback window doesn't grow the dataset for prematch bets either. Worth confirming on the 6-month bet-cycle.)

### 3. Per-bot CLV audit (60d, the most actionable)

Bots sorted by ROI ascending — losers first.

**Against all four books (n=772 matched):**

```
bot                       n   pickEdge  closeEdge  drift   %clsPos  winRate     ROI
─────────────────────────────────────────────────────────────────────────────────
bot_ou35_attacking       16   +10.46%    +7.46%   -2.30   100.0%   25.0%    -51.0%
bot_dc_strong_fav        22   +16.74%   +13.66%   -2.40   100.0%   54.5%    -36.6%
bot_dc_specialist        29    +9.81%    +5.87%   -4.74    93.1%   41.4%    -23.8%
bot_dc_value             74   +15.19%   +11.88%   -3.49    95.9%   55.4%    -19.2%
bot_high_alignment       65    +8.49%    +6.49%   -2.56    92.3%   41.5%    -13.0%
bot_btts_all             63    +7.59%    +5.15%   -2.62    95.2%   41.3%    -12.4%
bot_aggressive_v2        36    +9.24%    +6.00%   -3.96    91.7%   41.7%    -10.6%
bot_btts_conservative    19    +8.89%    +5.35%   -3.76    94.7%   52.6%     -4.7%
bot_ou25_global          32    +8.46%    +5.50%   -3.25    93.8%   46.9%     -1.4%
bot_aggressive          259    +7.26%    +5.46%   -1.67    92.7%   37.8%     +2.1%
bot_lower_1x2            29   +10.10%    +7.88%   -1.91   100.0%   48.3%    +21.0%
bot_v10_all              78   +10.26%    +7.38%   -3.24   100.0%   53.8%    +30.2%
```

**Against Pinnacle only (n=517 matched):**

```
bot                       n   pickEdge  closeEdge  drift   %clsPos  winRate     ROI
─────────────────────────────────────────────────────────────────────────────────
bot_ou35_attacking       16   +10.46%    +8.50%   -1.54   100.0%   25.0%    -51.0%
bot_aggressive_v2        34    +9.19%    +7.57%   -1.48    94.1%   44.1%     -3.4%
bot_ou25_global          32    +8.46%    +7.51%   -0.65    96.9%   46.9%     -1.4%
bot_aggressive          249    +7.14%    +6.41%   -0.75    97.6%   38.6%     +4.7%
bot_high_alignment       41    +8.34%    +6.75%   -3.16    82.9%   48.8%     +6.4%
bot_lower_1x2            27    +9.91%    +7.17%   -1.91    96.3%   48.1%    +20.1%
bot_v10_all              76   +10.18%    +7.44%   -2.50    97.4%   53.9%    +30.2%
```

**Observations:**
- **bot_v10_all** beats both benchmarks consistently. 100% (all-books) and 97% (Pinnacle) of its picks have positive closing edge. This is the model's flagship signal.
- **bot_lower_1x2** also clears. Smaller sample (~28 bets) but +20-21% ROI across both audits.
- **bot_aggressive** has the largest sample. Marginal vs all-books (+2.1%) but improves to +4.7% on Pinnacle-only — same pattern as the aggregate. Likely real, small edge.
- **bot_high_alignment** flips sign between audits: -13% all-books, +6.4% Pinnacle. This bot's pick-edge calculation is using inflated bookmaker data; its Pinnacle-only performance is the truer number.
- **DC bots and bot_btts_all** all show negative ROI AND have zero Pinnacle coverage (they don't appear in the Pinnacle table). These bots are betting markets the sharp book doesn't list — meaning either Pinnacle thinks the market isn't worth offering (low liquidity, high uncertainty), or the books we *are* using are charging extra margin on niche markets. Either way: the apparent edge is suspect.
- **bot_ou35_attacking** is a clear loser: -51% ROI both benchmarks, n=16. Should retire.

### 4. Inplay edge drift — prematch picks measured after kickoff (90d)

Sample-bound: only n=55 picks have a matched snapshot at any of +5/+10/+15/+20 min. Coolbet/Unibet/Bet365/Pinnacle don't push reliable inplay snapshots in our DB. Only the +15' window had hits; the others were all empty.

```
ALL MARKETS  +15'   n=55   pickEdge +8.83%   liveEdge +7.39%   drift -1.33pp   ROI -21.3%
```

90d run produced *identical* numbers to 30d — the bookmaker-set filter is the bottleneck, not the calendar lookback. **Action item**: add API-Football live odds to the snapshot lookup in this script (currently restricted to placeable books) so we can revisit at proper coverage.

### 5. Inplay → post-pick follow-up (90d)

Anchored to pick_time, not kickoff. Tests inplay-bot timing.

```
ALL INPLAY  +2m   n=30   pickEdge +18.46%  windowEdge +24.00%  drift +7.54pp   ROI -19.7%   synROI +86.7%
            +5m   n=28            +15.37%             +16.52%        -0.92    +77.2%        +36.4%
            +10m  n=30            +11.65%              +9.70%        -1.57     -3.9%        -22.9%
            +15m  n=28            +21.55%             +20.86%        -0.87    -13.4%        +47.0%
```

90d data is identical to 30d — most inplay history we have is from the last 30 days; the bot family is new.

**Interpretation pending more data**: the +2m drift signal (+7.54pp, 53% of picks widen) is potentially interesting — suggests inplay bots may pick slightly too early. But n=30 is way too small for confidence. Revisit at 90 days of inplay history (so around 2026-08-02).

---

## Recommendations

### Immediately actionable

1. **`bot_v10_all` and `bot_lower_1x2` should be the priority candidates for real-money placement.** Both clear Pinnacle CLV (+30.2% and +20.1% ROI on n=76 and n=27 vs Pinnacle close, n=78 and n=29 all-books). They are the bots whose edge survives a sharp benchmark.
   - Action: if not already, add both to the `COOLBET_RECORD_ALLOWED_MATURITY=calibrated` cohort for placement. Check current `maturity_label` values in `bots` table.

2. **`bot_ou35_attacking` should retire.** -51% ROI on n=16 across both benchmarks. Add to RETIRE list.
   - Filed: see PRIORITY_QUEUE entry MODEL-CLV-FOLLOWUP.

3. **DC family (bot_dc_value / bot_dc_specialist / bot_dc_strong_fav) needs audit.** All show -13 to -37% ROI on all-books, and Pinnacle doesn't list double-chance markets so we have no sharp benchmark. The edge they're picking up is probably book-margin in niche markets, not real value.
   - Action: look at which bookmakers their picks use in `recommended_bookmaker`. If mostly SBO/Dafabet/etc → these aren't placeable anyway and should be removed from the active feed.

### Worth investigating before acting

4. **Decompose the Pinnacle cohort-split anomaly.** "Narrowed" +12.3%, "widened" -24.2%. Almost certainly bot-mix confound (bot_v10_all dominates narrowed, DC bots dominate widened) but should confirm with one query.

5. **Calibration audit for DC and BTTS markets.** Their pick edges (+15% / +8% medians) collapse to +12% / +5% by Pinnacle/best-book close — that's a 3-4pp collapse, twice the all-markets average. Suggests Platt parameters for these markets may be miscalibrated (Platt fit on too few samples → over-confident predictions).

6. **Add API-Football live odds to the inplay snapshot query.** The +5/+10/+20 windows are blank in run #4 because our snapshot bookmaker filter doesn't include the live sources. Until fixed we can't say anything about early-minute drift on the +5/+10 windows.

### Defer until more data

7. **Inplay post-pick timing analysis.** The "+2m drift +7.54pp" signal is intriguing but n=30 is too small to act on. Revisit 2026-08 when inplay history reaches 90+ days.

---

## Open questions

- **Are the Pinnacle T-0 snapshots truly "closing"?** The slack window is ±5 min around kickoff. If Pinnacle's last update is T-10m, that's what we're reading — not the actual closing odds. Worth checking the median (kickoff - latest_snap_timestamp) for Pinnacle.
- **calibrated_prob vs model_probability**: we used calibrated_prob with model_probability as fallback. If Platt is what's tightening the edge unrealistically for DC/BTTS, the raw model_probability would show a different story. Worth a side-by-side.
- **simulated_bets history depth**: 30d, 60d, 90d windows produced the same row counts — table may only have ~30 days of data. Confirm: `SELECT MIN(pick_time) FROM simulated_bets` and see whether the rolling-90d archive is being cleaned aggressively.

---

## What didn't work / what's noise

- The "narrowed cohort makes money" finding is probably *not* a tradeable signal — it almost certainly reflects bot-mix, not market microstructure. Listed as investigation, not action.
- Per-market drift breakdowns are too noisy at n=15-30 per cell to use for filter rules. The per-bot view is far more informative.
- The original prematch-after-kickoff drift analysis (in the +5/+10/+15/+20 inplay script) is essentially unusable without API-Football live odds — only +15' has data and the sample is tiny.
