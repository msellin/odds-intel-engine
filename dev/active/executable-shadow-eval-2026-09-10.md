# EXECUTABLE-SHADOW-EVAL — honest ledger (2026-09-10, last 45d settled)

`PYTHONPATH=. python3 scripts/executable_shadow_eval.py --days 45` (read-only).
recROI = stored best-of-books odds; CB/UBroi = executable book price on the covered subset.

| bot | n | recROI | CB cov | CB roi | note |
|---|---|---|---|---|---|
| **bot_v10_all** (flagship, ~customer-facing) | 1952 | **15.2%** | 1340/1952 | **7.8%** | **the headline honesty gap: published ~15% is ~2× the gettable number** |
| bot_high_roi_global_v2 | 234 | 110.5% | 123/234 | 92.8% | ⚠️ variance, not edge — high-odds bot, same longshot-mirage shape as the trigger sweeps; do NOT read as real |
| bot_coolbet_trigger_1x2_v1 | 64 | −26.6% | 64/64 | −25.4% | trigger engine loses at executable prices (as found in BOOK-AGNOSTIC) |
| bot_coolbet_trigger_ou_v1 | 53 | −38.5% | 53/53 | −38.3% | " |
| bot_ou35_model_v1 | 30 | −52.4% | 30/30 | −53.1% | " |
| bot_coolbet_ou_model_v1 | 14 | −59.0% | 14/14 | −58.3% | tiny n |
| (unibet/sharp triggers) | 1–14 | noisy | — | noisy | n too small to read |

## The finding
The only bot with real volume, **bot_v10_all**, makes **+7.8% executable-Coolbet**, not the
**+15.2%** its best-of-books record shows — the SHADOW-PAGE-ROI-INFLATED problem (gotcha §55).
The published track record is inflated ~2× vs what we could actually have staked at Coolbet.
Trigger bots are negative at executable prices (confirms BOOK-AGNOSTIC). bot_high_roi_global_v2's
92.8% is variance (high-odds), not a promotable edge.

## Next (from the task)
- (a) **Promotion gate:** no bot → real money unless it clears positive executable ROI at the book
  it would place on. Rule, run this tool before any promotion.
- (b) **/performance + landing:** surface executable ROI (7.8%) instead of/alongside best-of-books
  (15.2%). **OWNER-GATED — touches published customer numbers.** Recommended, not done.
- (c) Widen coverage (CB ~69%, UB thin) via broader sweeps.
