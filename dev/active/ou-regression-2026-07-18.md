# OU regression investigation — 2026-07-18

Follow-up to MODEL-VERSION-FLIP-2026-07-18. v20260712 regressed +1.8%
log-loss on OU 2.5 in the offline eval; we kept v20260621 as the OU
override (`MODEL_VERSION_OU=v20260621`). This doc looks at the actual
market data to understand what's happening and where to focus.

## Training-window comparison

Both bundles train the same 3.5-year window; v20260712 just extends
the end date by 6 days:

```
version    | train_start  | train_end    | rows
v20260705  | 2023-01-26   | 2026-07-05   | 62,502
v20260712  | 2023-01-26   | 2026-07-11   | 63,910
```

The extra 1,408 rows are summer/friendlies/lower-tier data. Same hit
rate (0.5875) on offline eval → new model still finds winners at the
same rate. Worse log_loss + brier → probabilities are worse-calibrated.
Overconfident on losers.

Whitepaper §3.1b says `train.py` has `OU_EXCLUDED_LEAGUE_COUNTRIES`
filter (14 countries) that's default-TRUE. Registry `filters_tierc = f`
suggests the filter DIDN'T fire for either bundle — worth verifying in
train.py. If the filter is off, that's the same regression driver as
mid-May.

## Live OU market — 42 days by selection

| Selection | Picks | Wins | ROI | Avg edge | Reading |
|---|---|---|---|---|---|
| **over 2.5** | 98 | 60 | **+14.2%** | 0.11 | Working |
| **under 2.5** | 56 | 23 | **-21.8%** | 0.15 | Broken |
| **over 1.5** | 53 | 15 | **-27.0%** | 0.18 | Broken |

The regression is **not uniform across OU**. It's concentrated on the
lower-goal sub-lines (under 2.5, over 1.5) — predicting fewer goals.
Current summer window has higher scoring than model expects.

## By tier (OU 2.5 only)

| Tier | Picks | ROI | Avg CLV | Reading |
|---|---|---|---|---|
| 0 | 18 | +10.7% | +13.1% | Real edge |
| 1 | 97 | -15.2% | +5.7% | Marginal edge, unlucky |
| 2 | 43 | -13.4% | +3.7% | Marginal |
| 3 | 22 | +33.8% | +11.7% | Real edge (small n) |
| 4 | 28 | -12.0% | +14.5% | Real edge (CLV) but ROI bad |

Tier 4 OU-2.5 CLV is +14.5% but ROI -12%. That's variance, not model
failure — different from the tier-4 1X2/AH story where CLV is near zero.
The CALIBRATION-VETO (VETO_TIER_MAX=3) still helps here because tier-4
bots hallucinate on the 1X2/AH side, but the OU sub-model actually has
real edge on tier 4 — it's just being dragged by the wider bot config.

## Worst OU leagues (last 42d, ≥3 picks)

| League | Tier | Picks | ROI | Note |
|---|---|---|---|---|
| Friendlies Clubs | 1 | 10 | -55% | Chaotic by nature |
| World Cup | 1 | 20 | -34% | WC over; done firing |
| Torneo Federal A | 4 | 8 | -68% | Vetoed now |
| Superettan | 2 | 12 | -25% | Mid-tier Sweden |
| Serie C | 1 | 9 | -54% | Italy L3 |
| MLS Next Pro | 2 | 3 | -67% | Small sample |
| Serie D | 4 | 11 | -19% | Vetoed now |

## Actionable proposals (post-vacation)

**High confidence:**
1. **Selection-specific tightening for under 2.5 + over 1.5.** They're
   both losers over 42d at wide edge tolerance. Options: (a) raise the
   edge threshold ×1.5 for those two selections; (b) veto them entirely
   until the summer window ends (Sep 2026); (c) require Pinnacle
   consensus > 60% for those two.
2. **Verify `OU_EXCLUDED_LEAGUE_COUNTRIES` actually fires.** The
   registry says `filters_tierc = f` for both v20260705 and v20260712.
   Whitepaper claims default TRUE. Reconcile — if the filter is dead,
   fixing it should recover OU calibration.
3. **Friendlies + WC filter for OU.** Friendlies -55% on 10 picks and
   WC -34% on 20 are directly attributable to competition type.
   Add league-name-based OU-only filter.

**Medium confidence:**
4. **Sunday retrain with tighter goal-corpus curation.** Exclude
   friendly matches from the goal-regressor training even if 1X2
   trains on them.
5. **Per-league OU override.** Pin OU 2.5 to v20260621 as we already
   do, but add per-league `MODEL_VERSION_OU_LEAGUE_<X>` overrides for
   the worst offenders.

**Skip:** don't touch anything before re-verifying with next Sunday's
retrain (2026-07-19). One more data point + shadow_model predictions
will inform whether v20260712 was an outlier retrain or a trend.

## Related tasks

- MODEL-VERSION-FLIP-2026-07-18 (done — this doc explains the OU carve-out)
- SHADOW-MODEL-VERSION-ON-2026-07-18 (done — shadow v20260705 for 2wk validation)
- CALIBRATION-VETO-2026-07-18 (done — tier-4 + edge>0.25 vetoed globally)
- OU-LONGTERM-EXCLUDE-TIERC (whitepaper §3.1b — verify actually firing)
- OU-CLV-OPTION-B-RE-EVAL (whitepaper §3.1b — per-tier routing already lives)
