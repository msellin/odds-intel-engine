# Value Bets UI Redesign — Task List
_Created 2026-05-29. Input from 3 agents + codebase review._

## Architecture Decision (locked)

**2 layers, not 3:**
- Default view → **Scan list** (compact rows, edge-sorted, color-coded)
- Tap any row → **Expanded panel** (EdgeBar + table + signals + CTA)
- Drop rich card as standalone default — it's neither dense enough to scan nor rich enough to commit from

**Desktop:** same components; scan rows work as a dense list on wide screens too. No separate desktop-only layout needed — just responsive column sizing.

---

## Key Context Before Building

**Edge formula** (confirmed from `daily_pipeline_v2.py`):
```
edge = cal_prob - (1 / odds)   ← probability difference, NOT p*odds-1
```
So at `p=0.53, odds=2.95`: edge = `0.53 - 0.339 = +19.1%` ✓  
And at `p=0.53, best=2.70`: live edge = `0.53 - 0.370 = +16%` ✓  
Numbers on current cards ARE consistent. Design agent who computed p=42.9% assumed wrong formula.

**Fair odds in expanded panel** = `1/cal_prob` = `1/0.53 = 1.89`  
**Kelly fraction** = 0.15 (set in `improvements.py`, KELLY_FRACTION)

**Two product decisions still needed (blocker for Phase 3 expanded panel):**
1. Stake label: "suggested (¼ Kelly)" or just "suggested stake"?
2. Fair odds framing in panel: show `1/model_prob` (e.g. 1.89) or just the probability gap (53% vs 37%)?

---

## Phase 1 — Quick Wins (already done or trivial)
_No architecture change. Safe to ship any time._

- [x] Remove PENDING badge (ResultBadge returns null for pending)
- [x] "N bots agree" → "N strategies"
- [x] `@` → `post @` on placement odds
- [x] Stake: `4.8` → `4.8u`
- [x] Remove absolute time chip (koTime) from mobile BetCard — KO in Xh in meta line is enough
- [ ] **Color-code tier filter pills**: "16 strong 10%+" → green background/text, "9 moderate 5–10%" → amber. Same on desktop.
- [ ] **Red time for urgency**: when KO is <2h, color the koLabel text red. Applies to both BetCard and BetRow.
- [ ] **Fix pill soup**: "1x2 + ML + home" is 3 labels for one concept. Merge to a single "Home (1×2)" label. Remove the separate market badge and strategy profile pill; put market + selection together in one readable string. Applies to both card and row.
- [ ] **Remove competing "best book"**: `recommendedBookmaker` in the meta line ("best at Marathonbet") conflicts with the live BookOddsLine footer ("Best now Unibet 2.70"). Remove `recommendedBookmaker` from the meta line since BookOddsLine already shows the live best. Or flip: show it only when BookOddsLine is not rendered (free tier).
- [ ] **Fix line drift label**: `Line ↓ -8.5%` is ambiguous — is down good or bad? Change to explicit: `↓ drifting our way -8.5%` (green) and `↑ drifting away +8.5%` (blue). Title attribute already has the explanation — promote it to visible text on desktop, tooltip on mobile.

---

## Phase 2 — Scan View (new default, replaces current BetCard + BetRow)
_New component: `src/components/value-bets-scan.tsx`_
_This is the main restructure. Current components stay as fallback until scan view is stable._

### ValueBetRow structure (both mobile and desktop)
```
[urgency gutter] [edge] [match + pick + odds/book] [KO time + drift]
```

**Left urgency gutter** — 3px colored left border:
- Green: KO > 2h
- Amber: KO 1–2h  
- Red: KO < 1h
- Pulsing red: match is live (replaces time with "LIVE")

**Edge column** (left, ~58px fixed):
- Large font (17–18px), `font-mono font-bold`
- Green if ≥10%, amber if 5–10%
- Show live edge (computed from `bookOddsEntry.best * model_prob - 1` if bookOdds available, else fall back to `bet.edge`)
- Note: live edge = `cal_prob - (1/bestNowOdds)` to match our formula

**Middle section:**
- Line 1: match name (truncated)
- Line 2: selection + market in one readable string (e.g. "Home · 1×2") + `[N●]` bot consensus dots
- Line 3: best odds + book + stake (e.g. "2.70 Unibet · 4.8u")

**Right section:**
- KO time (relative, colored for urgency)
- Line drift icon (↓ green / ↑ blue / — neutral) — with tooltip on hover

**Bot consensus dots** — show `●●●●` (filled dots up to 4/4) or `N/N` text beside match name. This is the OddsIntel differentiator vs generic tipsters. Do not lose it in scan view.

**Tier gating:**
- Free: show top 3 rows, rest blurred with "Unlock X more bets" CTA
- Pro/Elite: full list

**First-visit banner** (dismissible, persisted to localStorage):
- "25 bets sorted by edge · tap any row to see the math" 
- One-time, disappears after dismiss. Keeps the dense default clean for returning users.

**Desktop:** same ValueBetRow renders in a wider layout. No horizontal scroll table needed — each row is a flex row that naturally widens on desktop.

---

## Phase 3 — Expanded Panel (tap-to-expand)
_New component: `src/components/value-bet-expanded.tsx`_
_Blocker: product decisions above_

### Sub-components:

**EdgeBar** — visual progress bar:
```
Fair [1.89] ←────[+16%]────→ Best [2.70 Unibet]
```
- Shows the gap between fair odds and best available as a colored fill
- Caption: "The gap between your model's fair price and best available is your edge"

**EdgeTable** — key numbers in a small table:
| Label | Value |
|---|---|
| Model win probability | 53% |
| Market implied probability | 37% (1/2.70) |
| Expected edge | +16% |
| Suggested stake | 4.8u (¼ Kelly) |

**SupportingSignals** — 3 icon+text rows (Pro: templated, Elite: LLM via /api/bet-explain):
- Line movement: "Market moved from 2.95 → 2.70 since we posted — money is moving our way, which validates the read but shrinks the edge."
- Ensemble: "N strategies independently rate this above market."
- Timing: "Xh to kickoff — price likely to keep tightening."

**ActionRow:**
- Primary: `[Bet at Unibet ↗]` — deeplink (for now, links to bookie homepage; affiliate links later)
- Secondary: `[Track]` — saves to tracking table (Phase 4)

**Tier gating:**
- Pro: EdgeBar + EdgeTable + templated signals + ActionRow
- Elite: same + LLM narrative sentences replacing templated ones
- Free: show EdgeBar only (teaser) + "Upgrade to Pro to see the full analysis" CTA

**Desktop:** render as inline accordion below the row, or a slide-in panel on wide screens.

---

## Phase 4 — Post-bet Tracking
_Depends on new DB table or reuse of existing._

- [ ] "Track" button saves `{user_id, bet_id, tracked_at}` to `user_tracked_bets` table (new migration)
- [ ] After settlement, scan row shows result chip: `✅ +4.8u` or `❌ −1u`
- [ ] This closes the feedback loop and builds trust faster than any copy

---

## Phase 5 — Live In-Play Integration
_Depends on live tracker data being available in the value bets query._

- [ ] Pulsing LIVE chip in urgency gutter when match is in-play
- [ ] Replace KO time with `[minute]' [score]` for live matches
- [ ] Live odds update in scan row (already partially there via BookOddsLine)

---

## What Applies to Desktop vs Mobile

| Change | Mobile | Desktop |
|--------|--------|---------|
| Tier pill colors (green/amber) | ✓ | ✓ |
| Remove PENDING | ✓ (done) | ✓ (BetRow, done) |
| Pill soup fix | ✓ | ✓ |
| Remove competing best-book | ✓ | ✓ |
| Line drift label fix | ✓ | ✓ |
| Scan view as default | ✓ | ✓ (same component, wider layout) |
| Urgency gutter | ✓ | ✓ |
| Bot consensus dots | ✓ | ✓ |
| Time urgency red (<2h) | ✓ | ✓ |
| Expanded panel | ✓ (inline) | ✓ (inline or side panel) |
| EdgeBar + EdgeTable | ✓ | ✓ |
| First-visit banner | ✓ | ✓ |
| Post-bet tracking | ✓ | ✓ |
| Live in-play gutter | ✓ | ✓ |

---

## Suggested Build Order

1. **Phase 1 remaining** (1–2h) — cleanup that applies regardless of direction
2. **Phase 2 scan view** (4–6h) — new component, wire it in behind the existing one first as A/B toggle, then flip default
3. **Phase 3 expanded panel** (3–4h, after product decisions) — EdgeBar, EdgeTable, signals, action row
4. **Phase 4 tracking** (1–2h, needs migration) — Track button + result chip
5. **Phase 5 live** (later) — depends on live tracker data in value bets query

Total estimate before Phase 4/5: ~8–12h of coding.
