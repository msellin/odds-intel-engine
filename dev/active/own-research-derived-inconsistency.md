# #191 OWN research idea 2 — internal inconsistency in derived markets (2026-09-27)

Parent: `PRIORITY_QUEUE.md` [[#191]] (OWN line-up). Direction: 🤖 OWN. The edge, if any, exists only at the book that mis-priced the leg.
Script (pre-registration in its docstring, written before the first run, not amended):
`scripts/analysis/own_derived_inconsistency.py`. Outputs: `data/models/_research/own191_derived/` (gitignored).

## Question
Do Unibet-Site, Coolbet, Tonybet and Epicbet price a derived leg (DNB, DC, AH 0, AH ±0.5, the O/U .5 ladder, BTTS,
team totals) above the fair value implied by their OWN 1X2 + O/U 2.5 at the same fetch? If they do, does that leg
beat an independent close?

## Method (short; the full version is in the docstring)
* Universe: 2,784 finished matches, KO within the 6.5-day full-resolution window (§59). Pick instants are from KO−24 h
  to KO−5 min. Each book's board is split into fetch clusters (gap > 90 s), and the main line and the derived leg must
  come from the same cluster (§62).
* Own fair: the book's 1X2 is Shin-de-vigged and gives DNB, DC and AH exactly. The ladder, BTTS and team totals come from a
  Dixon-Coles fit (λh, λa, ρ) solved exactly on P(H), P(D) and P(O2.5). Flag: odds × fair_own − 1 ≥ X, first flag per leg.
  Primary X = 3%; 1% and 5% are sensitivity checks. Live-implementable guards: edge ≤ 25%, a monotone O/U ladder, and DC shorter than its legs.
* Primary judge: a ≥5-book consensus close of the derived market itself (`compute_anchor`, Pinnacle rows removed, own
  book excluded, KO−60 min). Secondary judge: the liquid Betfair-Exchange close (KO−30 min).
* The data is split by match hash into discovery and holdout. A cell is carried to the holdout if its discovery n_judged ≥ 20 and its mean CLV > 0.
  Holm is then applied across the carried cells.

## Result: NULL. No cell reached the carry bar, and the legs that fire LOSE to the independent close
**The mispricing barely exists.** At X = 3%, 169 legs fire out of roughly 71,500 distinct legs evaluated, a fire rate of about 0.24%:

| book | legs evaluated | flags at 3% (family / TT) | where they fire |
|---|---|---|---|
| Unibet-Site | 12,000 | **0 / 0** | none (2 at X = 1%) |
| Tonybet | 23,700 | 3 / 3 | BTTS, AH ±0.5, TT |
| Coolbet | 15,700 | 15 / 7 | mostly BTTS (11) |
| Epicbet | 20,100 | 80 / 61 | spread over OU ladder, BTTS, AH, DC, TT |

**The flags that do fire are worse than the market, not better.** Pooled over the six-group family:

| X | family flags | consensus-judged n | CLV vs ≥5-book close [95% CI, by match] | exchange-judged n, mean | flat ROI |
|---|---|---|---|---|---|
| 1% | 193 | 97 | **−4.5% [−6.9, −2.1]** | 21, +3.0% | −4.6% |
| 3% (primary) | 98 | 45 | **−4.8% [−8.5, −1.1]** | 8, +5.2% | −13.1% |
| 5% | 72 | 31 | −5.0% [−10.0, +0.3] | 4, +10.4% | −4.9% |

* **Holm:** no (book, group) cell had ≥ 20 consensus-judged legs in discovery, so none was carried and the family is empty.
  No cell had ≥ 30 legs in the full sample either. The largest cells are Epicbet BTTS (n 16, −3.4%), Epicbet OU ladder
  (n 8, −10.6%, CI entirely < 0) and Coolbet BTTS (n 8, −8.9%, CI entirely < 0). Every per-cell verdict is "too thin", and the
  pooled sign is negative.
* **Mechanism:** only **9%** of primary flags are "lag" type, meaning the derived leg was unchanged while the book's 1X2 moved.
  The rest show the derived leg and the main line disagreeing within one fetch. For ladder and BTTS legs that is mostly
  Poisson/Dixon-Coles mis-specification, i.e. the book's own derivation is better than ours. The consensus confirms it:
  those legs close at −9% to −11%.
* **Placeability:** these legs are rarely still available one fetch later. Only **53%** of flags still offered the price at
  the book's next fetch within 60 min. The median flag came 1–15 h before KO. Unibet-Site and Coolbet are the only books with an automated
  executor, and together they produced 15 primary flags in 6.5 days.
* The exchange column is n 4–21 with CIs spanning ±10%. It does not contradict the consensus and cannot overturn it.

## Coverage notes (so nobody re-derives them)
* **Unibet-Site quotes one main O/U line per match** (2.5 OR 3.5, not both). It has no ladder and no AH, so its ladder can only
  be fitted where 2.5 is the main line, and it carries no OU_LADDER legs at all.
* **Team totals have no ≥5-book close.** Only Pinnacle and our four books quote them, so TT stays descriptive. Epicbet TT
  (61 flags) ran a flat ROI of −32%.
* **Tonybet exists only from 2026-09-23**, which means half the window.

## Verdict
**Do not build an OWN "derived-market inconsistency" bot.** The four Estonian books derive their side markets coherently
with their own main lines. A 3% own-inconsistency is a ~0.2% event, is zero at Unibet-Site, and where it happens the
independent close says the book's derived price was right and the main line (or our fit) was the outlier:
−4.8% CLV [−8.5, −1.1]. If this is ever re-tested, it needs a longer full-resolution window and a DNB/DC/AH-only scope,
since those are the exact derivations with no model error. The expected answer is still null (DNB/AH0 fired twice across
Coolbet and Tonybet).
