# Calibration Ceiling — Findings + Fix Options

> **Status:** Findings filed 2026-06-02 (read-only audit). No code changes yet.
> **Decision needed:** ship Option A (display cap) or defer until after WC?

## The Problem

Every "high confidence" prediction we show users is overclaiming by 20+pp. The model says 90% probability; actual hit rate is 65-67%. This is a STRUCTURAL property, not a measurement artefact — it persists across the 90d production sample (n=2,631 settled bets).

This is the same disease that killed the original "Top 5" Pro pitch (see `pro-tier-rethink-findings.md`) and the "95% bankers" idea. It affects:
- `/value-bets` Pro + Elite views (model% displayed)
- `/matches/[id]` prediction widgets
- `/world-cup` prediction slots (now also populated by national-team model)
- Any "Top picks" or "AI confidence" surface

## The Data

Production `simulated_bets` last 90d, all bots, all markets:

| Predicted prob bucket | Actual hit rate (raw) | Gap (raw) | Actual hit rate (post-Platt) | Gap (post-Platt) |
|---|---|---|---|---|
| 0.0-0.4 | 25.7% | +2pp (close) | 19.5% | +13pp |
| 0.4-0.5 | 31.9% | +13pp | 34.8% | +10pp |
| 0.5-0.6 | 41.8% | +13pp | 47.4% | +7pp |
| 0.6-0.7 | 51.0% | +14pp | **60.1%** | **+4pp ✓ calibrated** |
| 0.7-0.8 | 55.5% | +19pp | 53.3% | +21pp |
| 0.8-0.9 | 63.0% | +22pp | 60.7% | +22pp |
| 0.9-1.0 | 65.4% | +26pp | 67.1% | +23pp |

**Platt fixes the 0.6 bucket, fails everywhere else.** The 0.7+ tail is uncalibratable by parametric sigmoid.

## Why Platt Can't Fix This

Platt scaling is a logistic regression on (raw_prob, hit). It can shift + stretch but not bend sharply at one extreme without distorting the rest. With most training mass around 0.5, the fit minimises loss in the middle and leaves the tails uncorrected.

The 0.7+ tail also has FEWER training samples (most predictions cluster mid-range), so even if Platt could shape-fit, it'd be under-supported.

## Why Our 0.9-Prob Picks Only Hit 67%

Three structural reasons:
1. **Limit of model expressiveness.** Poisson + XGBoost capture goal rates well but not match-specific intangibles (motivation, fatigue, weather, refereeing). When model finds many edges aligning to "this is a lock," it stacks them additively when in reality they're correlated.
2. **Adverse selection by bot triggers.** Bots fire when edge is large. Large edge = market and model disagree strongly = market is often right.
3. **Soft-market regularisation absent.** Markets are most efficient in popular leagues (where bots find fewer picks), least efficient in obscure leagues (where bots find lots of "high confidence" picks). But obscure-league outcomes are noisier.

## Existing Infrastructure

- `CALIBRATION-ISOTONIC-IMPL (2026-05-25)` already exists per `workers/model/storage.py` + `workers/model/improvements.py`
- `.pkl` files per market (`isotonic_1x2_home.pkl`, etc.) in the model bundle
- Gated on env var `STAGE2_CALIBRATOR=isotonic` (default `platt`)
- **Likely NOT active in production today** — needs verification

Isotonic regression is non-parametric — it CAN bend at the tail. Should fix this in principle.

## Three Fix Options

### Option A: Display-layer cap (LOWEST RISK)

Cap displayed `model_prob` at 0.70 in the UI. When model says 85%, page shows "70%+ (high confidence)" or a bucketed label.

- **Effort:** ~1 hour. Touch `<ValueBetsScan>`, `<TodayPicksPreview>`, anything that renders `modelProb`.
- **Risk:** Zero — internal model unchanged, bot decisions untouched. Fully reversible.
- **Trade-off:** Hides real confidence variation in the 70-95% band. Some users prefer raw numbers.
- **What changes:** Trust narrative — we never overclaim. "Sustained CLV +14%" stays the headline; raw prob disappears from view above 0.70.

### Option B: Activate isotonic in production (MODERATE RISK)

Set `STAGE2_CALIBRATOR=isotonic` on Railway. Existing `.pkl` files do the work.

- **Effort:** ~30 min to set env + monitor for 24h.
- **Risk:** Moderate — calibration changes bot decisions (edge math depends on prob). Could affect ROI either direction.
- **Validation:** Compare daily bot ROI vs prior 7d. If improves or matches with better calibration, keep.
- **What changes:** ALL surfaces benefit, not just display. Internal model gets the calibration the codebase was built for.

### Option C: Train tail-specific Platt (HIGHEST EFFORT, MOST PRINCIPLED)

Fit a second Platt model on just `prob >= 0.7` predictions, using different functional form (e.g., monotonic spline). Apply only when raw prediction is in tail.

- **Effort:** 2-3 days. Train, validate, integrate, deploy.
- **Risk:** Moderate-high. Calibration logic gets more complex; debugging harder.
- **Trade-off:** Best long-term solution. Probably overkill for v1.

## Recommendation

**Ship Option A this week** — display cap with honest "70%+" label. Removes credibility risk during WC ramp. Reversible if users prefer raw numbers.

**Try Option B after WC** — controlled validation. If isotonic improves or matches, that's the structural fix; we can remove the display cap.

**Defer Option C** unless A+B both fail.

## What Option A Looks Like

```ts
// src/lib/probability-display.ts
const CONFIDENCE_CEILING = 0.70;

export function displayProb(prob: number): string {
  if (prob >= CONFIDENCE_CEILING) return "70%+";  // high-confidence band
  return `${(prob * 100).toFixed(0)}%`;
}

export function displayProbLabel(prob: number): "low" | "med" | "high" {
  if (prob >= CONFIDENCE_CEILING) return "high";
  if (prob >= 0.55) return "med";
  return "low";
}
```

Then sweep places that render `modelProb`:
- `src/components/value-bets-scan.tsx` (the picks table)
- `src/components/today-picks-preview.tsx`
- Match-detail prediction widgets
- World Cup `<PredictionTriple>`

Add a small tooltip on the "70%+" label: "Capped at 70% — our model's high-confidence picks historically hit ~66%, so we never display higher to avoid overclaiming."

## Open Questions for Operator

1. Pick Option A, B, or both?
2. If A: should we cap at 0.70 or higher (0.75)? 0.70 matches the empirical breakpoint.
3. If A: keep the raw prob visible to Elite (transparency tier) or cap universally?
4. If B: schedule a 24h validation window — pick a day with low match volume so reversal is cheap?

## Where this fits in the Pro/Elite tier story

Calibration affects truth-claim integrity. Pro tier currently leads with CLV (+14%), which is honest. The DISPLAY of individual pick probabilities is the secondary surface. If we ship Option A, Pro/Elite users see "70%+" for high-conf picks but the underlying ranking + edge math is unchanged.

If we ship Option B, the calibrated_prob column changes meaningfully — bets that were "edge +8%" might become "edge +3%" (because the calibrated probability is lower). This could affect bot triggering thresholds. Worth a 1-day validation window before going live.
