# PER-MARKET-EDGE-V2 — per-market placement thresholds

**Status:** 🔄 In Progress (2026-06-06)
**Scope:** Coolbet auto-placer + `/admin/place` badges + `/admin/real-bets` era marker

## Why

Backtest of 3,086 settled simulated_bets since 2026-05-01 (see `scripts/edge_threshold_backtest.py`) shows edge predictiveness varies wildly by market.

| market | n | ROI ≥3% | ROI ≥5% | ROI ≥10% | verdict |
|---|---|---|---|---|---|
| 1x2 | 1207 | +2.1% | +2.9% | **+14.1%** | edge highly predictive — raise to 10% |
| o/u | 999 | +3.1% | +4.7% | +13.2% | profitable at all floors — lower to 3% |
| asian_handicap | 350 | +4.6% | +4.7% | +5.7% | flat — keep ~5% |
| btts | 244 | -5.0% | -7.6% | +2.7% | edge doesn't fix it — raise to 10%, watch |
| double_chance | 238 | -10.8% | -10.1% | -17.2% | **retire** — losing at every floor |

Current placer uses a single global floor: `COOLBET_MIN_EDGE = 0.03`. UI badge gates at 0.05. Neither matches the per-market reality.

## What changes

### Placer (`workers/automation/coolbet_placer.py`)
- New constant `_MIN_EDGE_BY_MARKET: dict[str, float | None]`
- New helper `_min_edge_for(market) -> float` — returns per-market floor, `inf` for retired (DC), `_MIN_EDGE` global fallback for unknown markets
- Keep `_MIN_EDGE = 0.03` as the SQL prefilter (cheapest candidates pass SQL, Python applies the tighter per-market gate after)
- Apply `_min_edge_for(market)` after fetch in `select_bets_to_place` and `load_qualified_combo_bets`
- Apply `_min_edge_for(market)` to the live-edge check (replacing `_MIN_REMAINING_EDGE` in the per-bet loop)

### Frontend badge (`/admin/place`)
- Mirror `COOLBET_AUTO_MIN_EDGE_BY_MARKET` in `engine-data.ts`
- Badge logic uses per-market floor for `below_min` + `edge_eroded`
- Badge label tells the operator which market floor was missed

### Real-bets era marker (`/admin/real-bets`)
- Constant `MARKET_THRESHOLDS_V2_EPOCH = "2026-06-06T17:00:00Z"`
- `StatRow` shows "Era v1 (pre)" and "Era v2 (post)" side by side
- Daily breakdown table marks the epoch row

## Phase plan

1. **Single atomic commit** — placer, frontend badge, era marker, smoke test, docs all together. Pushed to main per project Git convention.
2. **Observe 4-6 weeks** of real_bets at v2 thresholds. Re-run `edge_threshold_backtest.py` and compare era v1 vs era v2.
3. **Port to `/value-bets`** if v2 ROI lift holds — add per-market filter toggle for Pro tier.

## Risks

- DC retirement leaves DC bots' simulated_bets ungated (paper still tracks). Real placement stops. Reversible by setting `_MIN_EDGE_BY_MARKET['double_chance']` back to a float.
- 1x2 threshold lift to 10% drops ~half of 1x2 real bets (1167→658). Volume drop is expected and the point of the change.
- Sample size in 3-5% / 25%+ buckets is thin in some markets. Re-evaluate after era v2 has 500+ bets.

## Not in scope (yet)

- /value-bets filter (Phase 3)
- Lowering MIN_EDGE below 3% (no data — bots don't log sub-3% picks)
- Per-bot thresholds (per-market is coarser but covers the variance)
