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

## AFTER

*(filled in when the methods are implemented and the panel is re-priced)*
