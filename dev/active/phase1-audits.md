# Phase 1.3 + 1.4 — match-failure audit and retry analysis

**Date:** 2026-08-29 · Database-only; the browser was left alone so scheduled
placement passes ran undisturbed.

---

## 1.3 — Match failures were mostly the block, not a matcher bug

| class | attempts | distinct fixtures |
|---|---|---|
| **search returned nothing** | **173** | 21 |
| not offered by Coolbet | 48 | 8 |
| legacy (pre reason-split) | 44 | 13 |

At face value that says our search is broken. It is not. **All 173 "search
returned nothing" failures fall inside a single 08:00–10:00Z window on
2026-08-28** — the degradation leading into the Imperva block — and stop dead
afterwards.

I nearly acted on the raw counts. Two fixtures looked like obvious matcher bugs
— `Laval v Grenoble` and `Clermont Foot v Sochaux`, both French Ligue 2, which
Coolbet certainly offers — but both were only ever attempted during that window,
and their generated queries (`Laval`, `Clermont`) are perfectly sensible.
**There was no bug to fix.**

**Genuinely un-priceable** (never once returned a price, across many attempts):

* `22 de Julio v Santo Domingo` — 32 attempts. Coolbet lists 22 de Julio against
  Vargas Torres, a different club. Correctly refused.
* `Toronto II v New York City II`, `Gomora United v Venda`, `US Biskra v JS
  Saoura` — reserve and minor-league fixtures Coolbet does not carry.

**The real remaining signal — intermittent fixtures:**

| fixture | failed | priced |
|---|---|---|
| Racing Santander v Elche | 18 | 7 |
| AL Nasr v Haras El Hodood | 12 | 9 |
| Struga v Bregalnica Štip | 12 | 8 |
| Slovan Ljubljana v Ilirija | 6 | 4 |

These resolve *sometimes*, so the market exists and we are losing it to
flakiness rather than absence. Worth re-measuring over a clean week now that
block-period noise can be identified and excluded.

---

## 1.4 — The retry loop earns its keep, and suggests a cheaper cadence

**Does a below-floor pick ever clear later? Yes, and it is worth real money.**

* picks ever rejected below floor: **35**
* of those, later placed: **4**
* placed on first look: 24
* **→ 14% of all placements came from picks already rejected once**

**And the retries were better bets, not desperate ones.** Every one was taken at
a materially better price than when first rejected:

| pick | rejected | placed | gain | waited |
|---|---|---|---|---|
| Estudiantes draw | 3.00 | 3.20 | +6.7% | 0.5h |
| Larne over 3.5 | 3.25 | 3.51 | +8.0% | 1.8h |
| Ferencvaros away | 2.15 | 2.30 | +7.0% | 3.3h |
| Austria Vienna draw | 3.60 | 3.75 | +4.2% | 5.3h |

Mean improvement **+6.5%**. This is the min-odds gate working exactly as
designed: it refuses a price, keeps watching, and takes it only once the market
comes to us.

### The cadence implication

Recovery times were **0.5h, 1.8h, 3.3h and 5.3h** — nothing recovered inside
half an hour, and the spread runs to five hours. Two consequences:

1. **A once- or twice-daily pass would miss most of these.** Retrying through
   the day is justified.
2. **30 minutes is more often than the evidence requires.** An hourly cadence
   would have caught all four, since the fastest recovery took 30 minutes and
   the rest hours.

Given that **the placer's own traffic contributed to an Imperva block on
2026-08-28** (36 passes plus diagnostics, ~7h of downtime), halving the request
rate for no measured loss of bets looks like a straight win. Recommend moving
the launchd schedule from :00/:30 to hourly, and re-measuring after a week.

Caveat: n=4 recoveries. This is directional, not decisive — but the direction
costs nothing to follow, and the block risk it reduces is real and observed.

---

## Standing results

28 real bets since 2026-08-27, 10 won, **P&L +€39.10**. Treat as noise: Task 1's
benchmark is 500–1,000 bets before ROI means anything.
