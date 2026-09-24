# De-vig bake-off per market — #106 (2026-09-24)

**Question.** Every published edge is `p_fair × odds − 1`, and `p_fair` comes from removing the
bookmaker's margin. Today that is **Shin's method for every market**, chosen once, globally, from two
candidates. Different methods split the same margin differently — mostly between favourites and
longshots — so a published edge can depend on the choice. Which method is right per market, and how
much of what we publish survives the least favourable reasonable method?

Direction: 👥 PICKS first — a published edge should not hang on one arbitrary formula. 🤖 OWN second,
because the same fair price feeds our own placement gates.

## Pre-registration (written before any new method was implemented)

**Methods.** Shin (current), proportional (multiplicative), additive, power, odds-ratio, and
margin-weighted-proportional-to-odds (WPO), all in `workers/model/devig.py` with unit tests. Each
returns probabilities summing to 1.

**Test 1 — which method is right, per market** (the decision test):
* Take Pinnacle's EARLIER complete price, de-vig it by each method, and score it against the
  Shin-independent target: the realised outcome (log-loss). Secondary: against Pinnacle's own de-vigged
  close under the same method, which is internally consistent.
* Markets: 1x2 (scored per outcome class — home / draw / away), O/U 2.5, O/U 3.5, 1H 1x2.
* Paired bootstrap of per-fixture LL differences vs Shin; Holm across (markets × methods).

**Test 2 — sensitivity of what we publish** (the before/after panel):
* The frozen panel is `dev/active/devig-panel-2026-09-24.json`: 125 published legs plus 372 sampled pool
  markets, with the RAW quotes each publish run saw.
* It is re-priced under every method on identical inputs. Report the share of published legs whose edge
  stays ≥ 3% under the WORST method, by arm, grade and odds band.

**Expected:**
* Test 1: methods differ by < 0.3pp on Pinnacle (≈ 3.5% margin); no method beats Shin significantly on
  majors; the largest differences are on draws and longshots.
* Test 2: the live (Pinnacle-anchored) arm barely moves. The consensus arm, which de-vigs soft books at
  ~8-10% margins, moves more.
* Grade B (short favourites, odds 1.20-1.60): Shin is the MOST generous to favourites, so B's edges should
  shrink under proportional/additive. Some B legs may fall under 3%.
* Grade B's evidence is REALISED profit at the taken odds, which no de-vig method changes. This test can
  tighten selection; it cannot undo B.

**Decision rule, stated now:**
* A per-market method switch requires a Holm-significant LL improvement over Shin.
* The WORST-METHOD GATE (publish only if edge ≥ 3% under every method) is proposed as a new
  `rule_version`. The owner switches it on — never silently.

**Correction found while implementing (before any scoring):** WPO reduces algebraically to additive
(p_i = 1/o_i − M/n), so it was dropped as a duplicate. **Five distinct methods are tested.** The
Holm family shrinks accordingly.

## BEFORE — the frozen panel (Shin everywhere)

CHECK P: replaying the live rule at each past publish run reproduces 92 of 157 published legs exactly,
and finds 125 in the replayed pool. The rest differ because `odds_snapshots` today does not hold exactly
what the rule saw then (later-arriving and thinned rows). That does NOT affect the before/after: both
sides of the comparison use the same frozen quotes, so any change is the method alone.

| arm | market | set | markets | mean anchor margin | legs with edge ≥ 3% (Shin) |
|---|---|---|---|---|---|
| consensus | 1x2 | published | 43 | 10.68% | 45 |
| consensus | O/U 2.5 | published | 19 | 8.39% | 17 |
| consensus | 1x2 | pool sample | 137 | 10.34% | 4 |
| consensus | O/U 2.5 | pool sample | 137 | 8.09% | 1 |
| live (Pinnacle) | 1x2 | published | 45 | 3.61% | 33 |
| live (Pinnacle) | O/U 2.5 | published | 18 | 3.38% | 12 |
| live (Pinnacle) | 1x2 | pool sample | 46 | 3.75% | 0 |
| live (Pinnacle) | O/U 2.5 | pool sample | 52 | 3.67% | 0 |

The consensus arm de-vigs books carrying 8-10% margins, against ~3.5% for Pinnacle. That is where the
method choice should bite.

## AFTER — results (2026-09-24)

### Test 1 — which method is right: **keep Shin.** No alternative beats it anywhere.

Data: every finished match with a complete Pinnacle price in our DB (football-data ingests plus live
collection), scored against realised outcomes.

| market | price point | n | Shin LL | best alternative | Holm |
|---|---|---|---|---|---|
| 1x2 | early | 35,490 | 0.98143 | additive +0.00012 | 1.00 |
| 1x2 | close | 26,136 | 0.97683 | additive +0.00010 | 1.00 |
| O/U 2.5 | early / close | 33,592 / 25,012 | 0.67113 / 0.67062 | all within ±0.00001 | 1.00 |
| O/U 3.5 | early / close | 17,920 / 11,648 | 0.63671 / 0.63754 | all within ±0.00002 | 1.00 |
| 1H 1x2 | early / close | 3,132 / 3,131 | 1.03153 / 1.03033 | additive +0.00018 | 1.00 |

* 32 comparisons (4 markets × 2 price points × 4 alternatives), Holm-corrected. None is significantly
  better than Shin.
* **Proportional is the worst method everywhere**: on 1x2 it loses −0.00087 to −0.00100 nats per match,
  and every bootstrap draw is worse.
* **Odds-ratio is also worse on 1x2.**
* **Additive and power tie with Shin, or edge fractionally ahead.**

**Calibration corrects my own pre-registered expectation.** I expected Shin to flatter favourites. It
does the opposite: on the Pinnacle 1x2 close, even Shin UNDER-states favourites.

| outcome's Shin band | n | actually happened | Shin | power | additive | proportional |
|---|---|---|---|---|---|---|
| 0.60-0.72 | 3,846 | **67.1%** | 65.2% | 65.9% | 65.7% | 63.7% |
| 0.72-0.84 | 1,900 | **77.6%** | 77.2% | 78.5% | 78.0% | 75.1% |
| 0.84-1.00 | 499 | **89.8%** | 87.8% | 89.7% | 88.7% | 85.1% |
| 0.00-0.20 | 13,129 | **13.4%** | 14.2% | 13.8% | 13.9% | 15.1% |

The favourite-longshot bias is STRONGER than Shin assumes. So Shin is not inflating grade B's short
favourites — if anything, their true edge is slightly larger than published. Proportional is the one
method that would manufacture edge on longshots and hide it on favourites.

### Test 2 — the before/after on the frozen panel

The same raw quotes as BEFORE, re-priced under every method. Today's Shin reproduces the frozen BEFORE
exactly (asserted per leg).

| | Pinnacle-anchored (live) | consensus (de-vig each soft book, then average) |
|---|---|---|
| edge spread across methods, median / 90th pct | **0.64 / 1.31 pp** | **2.10 / 4.53 pp** |
| published legs clearing 3% under Shin (in the replay) | 45 / 63 | 58 / 62 |
| … and under the worst of ALL five methods | 39 / 63 | 32 / 62 |
| … and under the worst CREDIBLE method (Shin, additive, power) | 42 / 63 | 54 / 62 |

* The method matters little on Pinnacle (≈ 3.5% margin) and a lot on the consensus. The consensus
  de-vigs soft books at 8-10% margins, so how the margin is split moves the fair price by several points.
* **"Worst of all five" is the wrong gate.** Its worst method is almost always proportional (42 of 62
  consensus legs), which Test 1 measured to be the least calibrated. Gating on it would reject good
  picks — mostly short favourites — for a formula we know to be wrong.
* **Grade B (consensus, 1.20-1.60)**, mean edge by method: Shin +2.9%, proportional −0.4%, additive
  +3.6%, power +5.0%. The two methods that tie Shin on calibration both put B's edge HIGHER, not lower.
* Pool sample (973 unpublished legs): 9 qualify under Shin, 14 under some method, 7 under every method.

(Some Shin mean edges in the table read just under 3% because the replay does not reproduce every
published price — see CHECK P above. The comparison across methods is exact regardless.)

### Decision

1. **Keep Shin as the de-vig everywhere.** No per-market switch clears the pre-registered bar.
2. **The CREDIBLE-METHOD GATE — switched ON for the consensus arm on 2026-09-24 (owner "yes"), as `consensus_edge_v2_2026_09_24`; the Pinnacle arm stays on its pre-registered v4.** Proposed as: Publish only if edge ≥ 3% under Shin,
   additive AND power. Measured cost on this panel: 3 of 45 live legs and 4 of 58 consensus legs, about
   7% of picks — exactly the ones whose edge depends on the formula. It would be a new `rule_version`
   on each published arm. **Owner decision.**
3. **Never use proportional de-vig for a published number** (ANALYSIS_GOTCHAS §78).

Re-run: `python3 scripts/devig_bakeoff.py score` (Test 1) · `python3 scripts/devig_bakeoff.py compare`
(Test 2, on the frozen panel).
