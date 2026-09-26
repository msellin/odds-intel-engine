Parent row: [[#154]] MODEL-INPUTS-ROUND-2-BOTH-MARKETS-2026-09-25 (PRIORITY_QUEUE.md)

# #154 round R2-A — power de-vig in NEW+ (pre-registration)

Written 2026-09-26 BEFORE any combiner run on the evaluation window. Nothing below may be tuned on
that window's output. Companion: `dev/active/model-inputs-round2-brief.md` (baseline, rules).

## 0. Research-first answers (CLAUDE.md "Research before you train")

1. **Structural shape.** Not a feature change: the 1X2 market is a difference-shaped outcome and the
   consensus / Pinnacle inputs are the market's own view of it. This round changes only how each book's
   margin is removed. Proportional normalisation ignores the favourite-longshot bias; Shin (Štrumbelj 2014,
   "On determining probability forecasts from betting odds", IJF) and the power method (Clarke, Kovalchik &
   Ingram 2017, "Adjusting bookmaker's odds to allow for overround") correct it. ANALYSIS_GOTCHAS §78
   already says never proportional; O/U's `combined_ou` uses power.
2. **Size.** Zero new features. Same combiner, same groups, same logit.
3. **Known negatives.** Score-only inputs saturated (round 3a); lineups an artefact (3c); α vs Pinnacle
   close = 0 for ratings arms. None bear on de-vig.
4. **Measured on the INPUTS first (no holdout spent)** — `scripts/analysis/devig_consensus_1x2.py`,
   kickoffs 2026-07-15..08-30 (before the 08-31..09-24 rounds window; inputs have no fitted parameters):

   | input | n | proportional | power | Shin |
   |---|---|---|---|---|
   | consensus (equal log-odds mean) | 10,076 | 0.97122 | **0.96923** (+0.00200, 95% [+0.0009, +0.0031]) | 0.96969 (+0.00153) |
   | Pinnacle alone | 8,419 | 0.98184 | **0.98037** (+0.00147, 95% [+0.0003, +0.0026]) | 0.98057 (+0.00127) |

   Power beats Shin on both inputs, so power is the ONE arm tested (no multi-arm search → no Holm needed
   for the de-vig choice itself; the family below is R2-A only).
   **W8.6 (Unibet counted twice) is moot:** the AF 'Unibet' feed stopped 2026-09-12 (2 matches in 14 days);
   0 matches carried both feeds in the input window. Nothing to collapse — leave CONSENSUS_BOOKS as is.

## 1. The arm

`r1x2_comb_v2` = `r1x2_comb_v1` with `power_devig` (workers/model/devig.py) instead of proportional in
`market_consensus_1x2._triples` — for the consensus books AND the Pinnacle input. Walk-forward refit exactly
as v1 (same schedule, same availability groups, same features). v1 is unchanged and keeps serving.

## 2. Evaluation (fixed)

* **Window:** kickoffs from 2026-09-25 00:00 UTC (after every earlier round's data), matches where BOTH v1
  and v2 have a gated prediction at the same decision time.
* **Stop:** first run on or after the day the window holds **n ≥ 4,000** settled matches with a gated NEW+ row (279 on 2026-09-26 after ~1.3 days, ≈ 210/day →
  ~2026-10-14; `ops/verify/154-r2a-devig-round-due.yml` pages when due). Run once.
* **Primary:** mean 3-way log-loss, v1 − v2, paired bootstrap over matches (10,000, seed 1542).
  **Pass = gain ≥ +0.0010 AND one-sided p < 0.05.**
* **Expected:** smaller than the input gain (+0.0015–0.0020) because the combiner's per-group logit already
  rescales part of a uniform bias — **+0.0005 … +0.0012**. A result under +0.0010 is the likely FAIL and is
  a clean answer, not a reason to search further.
* **Secondary (only if primary passes):** the VIP rule (EV ≥ 5%, Pinnacle required, odds 1.30–6.00, one per
  match) re-run on v2 vs v1 over the same window — pick count and sharp-anchor CLV (§85/§86 definitions).
  A v2 that passes log-loss but moves VIP CLV down is NOT shipped.

## 3. Shipping (owner policy)

Pass → v2 written beside v1 in `rating_1x2_predictions` (own model_version), a twin of the VIP bot on v2
(EXPERIMENTAL), compare live; owner promotes. Never in place (ANALYSIS_GOTCHAS §84).

## 4. Still open in #154 (not this round)

* Idea 3 (exchange / Tonybet fair probs): 214 / 344 finished fixtures as of 2026-09-26 — pre-register when
  each has ≥ 2,000 (~mid-October at the current ~100/day).
* Ideas 4–9 per the brief; the forward check `scripts/ab_1x2_rating_arms.py --forward` due ~2026-09-27.
