# VALUE-BETS-DENSITY-PASS — Design Proposal (2026-06-06)

**Purpose.** Audit `/value-bets` row layout (operator flagged as visually noisy per 2026-06-06 screenshots) and propose 2-3 compressed alternatives. Also identifies where the deferred PUBLIC-MATURITY-BADGE slots in without compounding noise.

**Status.** Doc only — no code changes shipped. Pick one of the alternatives (or ask for a 4th iteration) and I'll implement.

---

## Current row carries 10 distinct items

From the 2026-06-06 screenshots, each mobile row shows:

1. **Edge %** (left, large) — e.g. `+19.0%`
2. **Match name** — e.g. "Athletic Club MG U20 — Itabirito U20"
3. **Status chip** — `PRE-MATCH` or `LIVE`
4. **Selection + market** — e.g. `x2 · DC`, `home -0.5 · AH`, `Home win`
5. **PRO badge** — on some rows
6. **Percentage chip** — `45%`, `57%`, `52%` (consensus / probability — varies)
7. **Best odds + book** — `2.78 Marathonbet`
8. **Suggested stake** — `4.4u`
9. **Time to kickoff** — `9m`, `1h 39m`, `LIVE`
10. **Down arrow + dash** — collapse / expand affordance

Plus the vertical colored strip on the left (red/yellow/green by urgency or strength).

The screenshot covers ~9 rows in one viewport. With 10 items per row, that's **90 micro-decisions per scroll**.

---

## What's actually load-bearing for the user decision?

The question every user asks scanning a value-bets row is:

> "Is THIS bet worth my time?"

That decomposes into 3 sub-questions:

| Sub-question | Today's signal | Load-bearing? |
|---|---|---|
| "How big is the edge?" | edge % (item 1) | **YES** — primary discriminator |
| "What's the bet?" | match + selection (items 2, 4) | **YES** — required for action |
| "How urgent?" | kickoff time (item 9) + status chip (item 3) | **YES** — drives "act now" |
| "What odds / where?" | best odds + book (item 7) | Important — but only when ready to act |
| "How much to stake?" | suggested stake (item 8) | Important — but stake-sizing is a separate motion |
| "Tier-gated?" | PRO badge (item 5) | Mostly relevant on hover/click |
| "Confidence signal?" | percentage chip (item 6) | Adds noise — meaning unclear |
| "Expand for detail?" | down arrow (item 10) | Affordance — small visual cost |

**Promotion**: items 1, 2, 4, 9 are the primary 4.
**Secondary** (collapse to expand-on-tap): items 5, 7, 8.
**Demote** (move to expanded view only): item 6 (confusing) + item 3 (status, secondary).

---

## Three proposed alternatives

### Option A — "Glance" (most aggressive compression)

Strip to the 4 primary signals + 1 maturity dot.

```
┌────────────────────────────────────────────────────────────────────┐
│ +19% │ Athletic Club MG U20 — Itabirito U20         ●    9m │ ⌄  │
│      │ DC · x2                                                    │
├────────────────────────────────────────────────────────────────────┤
│ +17% │ Næsby — FA 2000                              ●   LIVE │ ⌄ │
│      │ AH · home -0.5                                             │
├────────────────────────────────────────────────────────────────────┤
│ +14% │ MyPa — PPJ                                   ●   1h 39m │ ⌄│
│      │ 1X2 · home                                                 │
└────────────────────────────────────────────────────────────────────┘
```

**What you see at a glance:** edge, match, market, maturity dot, time.

**Tap row to expand:** odds + book, stake, status (PRE-MATCH/LIVE), PRO badge if applicable, model + bot details, full maturity tooltip.

**Maturity dot legend:**
- 🟢 = Mature / Established calibrated model + proven bot
- 🟡 = Calibrating (model fit recent or partial)
- ⚪ = Experimental (no Platt fit OR new bot)

**Pros:** Massively cleaner. 4 signals × 9 rows = 36 micro-decisions instead of 90.

**Cons:** Power users lose the at-a-glance odds + stake. Users have to tap to commit to a bet.

---

### Option B — "Two-row hybrid" (moderate compression)

Primary signals on row 1, action info on row 2 (smaller, muted).

```
┌────────────────────────────────────────────────────────────────────┐
│ +19% │ Athletic Club MG U20 — Itabirito U20            ●   9m  │  │
│      │ DC · x2  ·  2.78 Marathonbet · 4.4u                          │
├────────────────────────────────────────────────────────────────────┤
│ +17% │ Næsby — FA 2000                                 ●   LIVE │  │
│      │ AH · home -0.5  ·  1.98 Bet365 · 4.8u  PRO                   │
├────────────────────────────────────────────────────────────────────┤
│ +14% │ MyPa — PPJ                                      ●  1h 39m │  │
│      │ 1X2 · home  ·  3.25 Unibet · 4.2u                            │
└────────────────────────────────────────────────────────────────────┘
```

**What you see at a glance:** edge, match, time + maturity dot (primary line). Selection / market / odds / book / stake / tier (secondary line, smaller font, muted).

**Tap row to expand:** full detail (model/bot breakdown, signal explanations, history).

**Demoted entirely:** the standalone percentage chip (item 6 — its semantic was unclear) + the PRE-MATCH/LIVE chip (status implied by time string already: "9m" = pre-match, "LIVE" = live).

**Pros:** Compromise between Option A's clean scan and information density. Power users can still see odds without tapping. Eliminates the ambiguous percentage chip.

**Cons:** Two-line rows = fewer visible per viewport (~6-7 instead of 9).

---

### Option C — "Filter-out" (no row redesign, smarter filtering)

Keep current row layout. Add a "Maturity ≥ Calibrated" filter chip and a "Pro tier only" toggle. Default view: only show ≥ Calibrated bets (5-15 visible rows instead of 80).

```
┌────────────────────────────────────────────────────────────────────┐
│ All 80 · ● 49 strong · ● 29 moderate · ★ 28 strong leagues          │
│ NEW: ◯ Calibrated only (12)  ◯ Pro-eligible (45)                    │
└────────────────────────────────────────────────────────────────────┘
```

The 80 current rows become ~12 by default ("Calibrated only" gate). Users can opt-in to see the experimental tail.

**Pros:** Minimum code change. Users still see all data on demand. Honest framing — "here's our most-tested 12, the other 68 are research-grade."

**Cons:** Doesn't actually solve row noise — it just shows fewer rows. If users always toggle "show all" they're back to 80-row scroll. Filter chips add their own UI weight.

---

## Where PUBLIC-MATURITY-BADGE slots in

| Option | Where the badge lives |
|---|---|
| **A** | The single colored dot on row 1 (current row → dot replaces the percentage chip). Hover/tap = breakdown. |
| **B** | The single colored dot on the primary line (between time and chevron). Selection line stays text-only. |
| **C** | The filter chips themselves ARE the badge — clicking "Calibrated only" filters by maturity. Individual rows don't need a per-bet badge. |

Option C is most consistent with "don't compound noise" but loses per-bet transparency. Option A and B keep per-bet visibility.

---

## Other density wins regardless of which option

These apply to any of the three:

1. **Drop the standalone percentage chip (45%, 57%, etc.)** — meaning unclear, two semantics overlap (consensus vs. model). If it stays, label it ("M:45%" or "C:57%") so the user can disambiguate.

2. **Drop the PRE-MATCH chip when time string is "9m"** — time already implies pre-match. Keep `LIVE` (it's a distinct state).

3. **Combine "1X2 · home" into "1X2 H"** — already done on `/admin/place` via `fmtSelShort()`. Apply consistently here.

4. **Collapse the bookmaker name on hover** — show `2.78` as primary; bookmaker name in tooltip. The book matters when you're about to bet; the price is what drives the scan.

5. **The left colored strip carries information I can't decode from the screenshot** — what does each color mean? If it's redundant with the edge %, remove it. If it carries unique signal (urgency, alignment, consensus strength), label it.

---

## Recommendation

**Option B + the density wins (1-4) above.**

**Why B:**
- Aggressive enough to materially reduce noise (per-row visual weight ~50% lower)
- Preserves power-user info (odds, stake) without tapping
- Mobile-friendly (already a two-line layout; just compressing what's there)
- Maturity-dot integration is clean — single chip on the primary line, hover reveals model + bot facts
- Reversible — if power users complain, expand row 2 back into 3 lines

**Why not A:** Too aggressive. Users WILL want odds + stake visible to decide whether to act. Tap-to-expand for that is a step backward.

**Why not C:** Hides bets behind a filter the user must remember to use. Doesn't solve the underlying row-noise problem — just papers over it. Better as a complement to Option B, not a replacement.

---

## Effort estimate (if you pick Option B)

- **~1.5h** row layout refactor (`src/components/value-bets-scan.tsx`)
- **~0.5h** maturity dot component (reuse `real-money-tier-badge.tsx` math, render as dot instead of pill)
- **~0.5h** density wins (drop PRE-MATCH chip when implied, "1X2 · home" → "1X2 H", bookmaker in tooltip)
- **~0.5h** smoke pinning the new row contract
- **~0.5h** screenshots for review before push

**Total: ~3.5h.** Could ship Monday/Tuesday next week alongside the PUBLIC-MATURITY-BADGE implementation.

---

## Open questions before implementation

1. **What does the left colored strip actually mean today?** If it's tier urgency, the maturity dot supersedes it — drop. If it's something else, keep it but label it.

2. **What does the standalone percentage chip mean today?** Confirm before deletion — if it's load-bearing for some users, keep with a label instead of dropping.

3. **Is the "PRO" badge on selected rows a gate (hide bet for non-Pro) or just an info chip?** If gate, the chip is required. If info, it can move to hover.

4. **Should the maturity dot ever be RED (visible warning) or always green/yellow/gray (passive info)?** Public framing argues for passive — never tell users "don't bet" — but a red dot on demonstrably losing bots could be useful operator signal.

---

## Next step

You react to this doc. I implement whichever option (or modified variant) you pick. If none of the three feel right, I do another design pass.
