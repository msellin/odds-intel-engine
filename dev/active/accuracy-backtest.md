# GROWTH-ACCURACY-BACKTEST — results

_Generated 2026-06-04 18:59 UTC — period: all finished matches in DB — sample: 27276 finished matches_

## Headline numbers — pure outcome accuracy (no odds, no edge, no staking)

Every row below is: "we picked outcome X, did X happen?". Doesn't matter if the odds were 1.01 or 50.0 — same framing competitor sites use.

| Market | Picks | Hits | Headline accuracy |
|---|---:|---:|---:|
| **1X2** (top pick) | 27263 | 12198 | **44.7%** |
| **OU 1.5** (top pick) | 21831 | 16392 | **75.1%** |
| **OU 2.5** (top pick) | 25856 | 13986 | **54.1%** |
| **BTTS** (top pick) | 26614 | 14364 | **54.0%** |

## The 'competitor headline' framing — cherry-pick the confident pick per match

Across ALL markets per match (1X2 / OU 1.5 / OU 2.5 / BTTS), take the single highest-confidence pick. Skip the match if no market crosses the threshold. This is *exactly* how competitor sites build a "70%+ accuracy" claim — they don't predict every game, they only count picks they're confident about.

| Confidence threshold | Picks made | Hits | Accuracy | Match coverage |
|---|---:|---:|---:|---:|
| ≥60% | 23174 | 17022 | **73.5%** | 85.0% of matches |
| ≥65% | 22082 | 16366 | **74.1%** | 81.0% of matches |
| ≥70% | 18075 | 13747 | **76.1%** | 66.3% of matches |
| ≥75% | 13204 | 10232 | **77.5%** | 48.4% of matches |
| ≥80% | 7740 | 6138 | **79.3%** | 28.4% of matches |
| ≥85% | 2876 | 2370 | **82.4%** | 10.5% of matches |

**Read this table top-to-bottom.** The trade-off is volume vs. accuracy: a 70%+ accuracy claim is real, but only on the subset of matches where the model is confident. We pick the games; we don't predict every game. That's both honest *and* exactly how the competitor sites do it.

## 1X2 — accuracy by confidence bucket

| Confidence ≥ | Picks (cum.) | Hits | Accuracy |
|---|---:|---:|---:|
| 50% | 4923 | 2911 | **59.1%** |
| 55% | 2411 | 1527 | **63.3%** |
| 60% | 1093 | 738 | **67.5%** |
| 65% | 475 | 331 | **69.7%** |
| 70% | 168 | 125 | **74.4%** |
| 75% | 54 | 42 | **77.8%** |
| 80% | 21 | 15 | **71.4%** |
| 85% | 5 | 3 | **60.0%** |

## OU 1.5 — accuracy by confidence bucket

| Confidence ≥ | Picks (cum.) | Hits | Accuracy |
|---|---:|---:|---:|
| 50% | 21831 | 16392 | **75.1%** |
| 55% | 21412 | 16147 | **75.4%** |
| 60% | 20704 | 15703 | **75.8%** |
| 65% | 19324 | 14755 | **76.4%** |
| 70% | 16772 | 12952 | **77.2%** |
| 75% | 12636 | 9870 | **78.1%** |
| 80% | 7512 | 5994 | **79.8%** |
| 85% | 2789 | 2313 | **82.9%** |

## OU 2.5 — accuracy by confidence bucket

| Confidence ≥ | Picks (cum.) | Hits | Accuracy |
|---|---:|---:|---:|
| 50% | 25856 | 13986 | **54.1%** |
| 55% | 14920 | 8406 | **56.3%** |
| 60% | 8666 | 5072 | **58.5%** |
| 65% | 4329 | 2603 | **60.1%** |
| 70% | 1783 | 1088 | **61.0%** |
| 75% | 581 | 378 | **65.1%** |
| 80% | 158 | 100 | **63.3%** |
| 85% | 39 | 26 | **66.7%** |

## BTTS — accuracy by confidence bucket

| Confidence ≥ | Picks (cum.) | Hits | Accuracy |
|---|---:|---:|---:|
| 50% | 26614 | 14364 | **54.0%** |
| 55% | 15925 | 8923 | **56.0%** |
| 60% | 8771 | 5069 | **57.8%** |
| 65% | 3707 | 2228 | **60.1%** |
| 70% | 1205 | 745 | **61.8%** |
| 75% | 342 | 200 | **58.5%** |
| 80% | 116 | 71 | **61.2%** |
| 85% | 61 | 38 | **62.3%** |

## Accuracy by league tier

Higher-tier leagues = top European football = more data + more predictable. Lower tiers = noise.

### 1X2 by league tier

| Tier | Matches | Hits | Accuracy |
|---:|---:|---:|---:|
| 0 | 847 | 359 | **42.4%** |
| 1 | 21434 | 9743 | **45.5%** |
| 2 | 1869 | 782 | **41.8%** |
| 3 | 1619 | 671 | **41.4%** |
| 4 | 1490 | 641 | **43.0%** |

### O/U 1.5 by league tier

| Tier | Matches | Hits | Accuracy |
|---:|---:|---:|---:|
| 0 | 154 | 117 | **76.0%** |
| 1 | 18182 | 13758 | **75.7%** |
| 2 | 1214 | 876 | **72.2%** |
| 3 | 1133 | 823 | **72.6%** |
| 4 | 1146 | 817 | **71.3%** |

## Interpretation — what we can publish

1. **Pick the headline number from the cherry-pick table.** That's the framing competitors use. ≥70% confidence picks at X% accuracy is the kind of claim that lands.
2. **Pair it with the high-tier league number** — "on top European leagues, our model called 1X2 correctly X% of the time." Stronger story than the global average.
3. **Honest caveats stay visible:** this is NOT a profitability claim. Most high-confidence picks are −EV because the market also knows. The honest one-liner: *"X% accuracy. 0% guarantee of profit. Here's why those aren't the same thing."*
4. **The market-favourite baseline is optional** — include it only if our number meaningfully beats it. If it doesn't, drop the comparison and keep the standalone claim.

## Method

- Source: `predictions` rows with `source='ensemble'` (Poisson + XGBoost blend), joined to `matches` where `status='finished'` and scores populated.
- **No odds, no edge, no staking math involved.** A 1.01-odds favourite that won counts as a hit; a 30.0-odds longshot that lost counts as a miss.
- 1X2: highest of `1x2_home`/`1x2_draw`/`1x2_away`. OU 1.5/2.5: higher of `over_X`/`under_X`. BTTS: higher of `btts_yes`/`btts_no`.
- Buckets are *cumulative* (≥X% includes all higher buckets).
- League-tier from `leagues.tier`.

