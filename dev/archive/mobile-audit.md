# GROWTH-MOBILE-FIRST-AUDIT — 2026-06-05

> Static code audit at 375px viewport. Findings prioritised P0 (broken) /
> P1 (significant friction) / P2 (polish). Fixes ship in follow-up commits;
> this doc is the punch-list.

## Audit method

Static analysis of every public-surface page + key components in
`odds-intel-web/src/`. We grep for known mobile-hostile patterns
(fixed widths, `hidden sm:block` without alternative, `text-xs` on
clickable elements, tables without overflow-x-auto, etc.) and read
each page file. No browser used — but the static signals are
strong enough to populate a prioritised fix list.

Pages audited:
- `/`, `/pricing`, `/matches`, `/matches/[id]`, `/value-bets`,
  `/live` (new), `/accuracy` (new), `/methodology`, `/performance`,
  `/vs/[competitor]` (new)
- Plus key components: `nav.tsx`, `pricing-cards.tsx`,
  `competitor-matrix.tsx`, `one-screen-proof.tsx`,
  `value-bets-scan.tsx`, `login-modal.tsx`

---

## What's working (don't regress)

- **Body text 14-16px** across all pages — within iOS guidelines, no
  text-too-small issues
- **`text-balance` on most headings** prevents awkward line breaks
  on narrow viewports (introduced during GROWTH-LANDING-REFACTOR)
- **Input fields default to `text-base` (16px)** — avoids iOS auto-zoom
  on focus, which is a classic mobile-hostile pattern we sidestepped
- **Tables use `overflow-x-auto` + `min-w-[N]px`** on /accuracy, /live,
  /performance, /predictions — they scroll horizontally cleanly rather
  than breaking layout

---

## P0 — Broken or critical (ship fixes ASAP)

### P0-1: Landing nav links inaccessible on mobile

**File:** `src/app/page.tsx` lines 100-115
**Issue:** Nav links `/matches`, `/live`, `/accuracy`, `/pricing` all use
`hidden sm:block` → they disappear entirely on mobile. **There is NO
hamburger menu on the landing page** to replace them. A mobile visitor
literally cannot navigate to `/pricing` from the landing page nav.

**Impact:** Severe — the entire navigation graph from the highest-traffic
page is broken on mobile.

**Fix:** Two options:
- (A) Move the landing-page nav links into a mobile hamburger drawer
- (B) Show a compact "More ↓" dropdown on mobile that surfaces them

Option A is more conventional; B is faster to ship. Going with A
(small client-component hamburger that toggles a sheet).

### P0-2: Competitor matrix requires 265px horizontal scroll at 375px

**File:** `src/components/competitor-matrix.tsx` line ~131
**Issue:** Table uses `min-w-[640px]` and `overflow-x-auto`. At 375px
viewport (minus padding), users must swipe ~265px sideways to see the
last 2 columns. The whole "We beat them on these features" punchline
sits in the rightmost OddsIntel column, which is the last thing visible
without scrolling.

**Impact:** High — first-impression conversion piece on the landing
becomes friction instead of proof.

**Fix:** Add a mobile-only stacked layout (`md:hidden` for stacked,
`hidden md:block` for the table). Stack: one card per feature row
showing "Feature name → competitor values → OddsIntel value" so the
OddsIntel column is always visible.

### P0-3: "Most Popular" pricing badge can clip behind sticky nav

**File:** `src/components/pricing-cards.tsx` line ~98
**Issue:** Pro card has a `absolute -top-3` "Most Popular" badge.
On mobile, when the user scrolls and the Pro card's top edge reaches
the sticky nav (`top-0 z-50` 56px tall), the badge slides UP behind
the nav and visibly clips.

**Impact:** Medium-high — the most important "buy this one" signal
gets visually broken on scroll.

**Fix:** Reduce `-top-3` to `-top-2` on mobile + add `z-10` so the
badge sits above adjacent content (but the sticky nav's `z-50` still
covers it when overlapping). Acceptable behaviour: the badge slides
under the nav cleanly without being clipped halfway. Better fix:
`relative` positioning on mobile so it stays inside the card.

---

## P1 — Significant friction

### P1-1: Tiny tap targets on small Buttons

**File:** `src/components/ui/button.tsx` — `size="xs"` produces `h-6`
(24px) which is well below the 44×44px tap-target guideline.

**Where it's used:** Tier preview buttons in admin (`nav.tsx:280-294`),
small status badges in `match-pick-button.tsx:147`, and a few other
`size="xs"` usages on chips.

**Impact:** Mostly admin-tier and chip surfaces; the customer-facing
pricing/signup CTAs use `size="lg"` (h-12) which is fine.

**Fix:** Bump the `xs` size variant to `h-8` minimum (~32px) and ensure
8px horizontal margin between adjacent xs buttons. Lower-priority because
admin is operator-only and chips are decorative.

### P1-2: One-screen-proof cramped on mobile

**File:** `src/components/one-screen-proof.tsx`
**Issue:** Two-column side-by-side at 375px means each card gets
~167px width. The 8-tab grid (2×4) at 167px width has each chip ~75px
wide — text gets tiny. The OddsIntel "one screen" mockup on the right
is similarly cramped. The animation works (we did test that) but
visual impact is reduced.

**Impact:** Medium — the demo's value (visceral contrast) gets
diluted on mobile.

**Fix options:**
- (A) Stack vertically on mobile (`grid-cols-1 md:grid-cols-2`) so each
  card gets full width and the 8-tab grid is legible
- (B) Hide the 8-tab card on mobile entirely and show only the
  OddsIntel side with the punchline "vs 8 tabs"

Going with (A) — vertical stack preserves the contrast comparison.

### P1-3: Value Bets filter dropdowns force layout break

**File:** `src/components/value-bets-scan.tsx` line ~769
**Issue:** `SelectTrigger` uses `w-[200px]` fixed. At 375px viewport
minus `px-4` padding (16px each side), available width is 343px.
A 200px dropdown takes 58% of the row, forcing adjacent filter
controls to wrap awkwardly.

**Fix:** Change to `w-full sm:w-[200px]` so on mobile the dropdown
fills the row + stacks naturally.

### P1-4: Modal close button hit-box too small

**File:** `src/components/login-modal.tsx` line ~81
**Issue:** Close button uses `p-1` (4px padding) around an X icon at
`right-4 top-4`. Effective hit-box ~20×20px. Both below the 44px
guideline AND positioned in the awkward thumb-stretch zone.

**Fix:** `p-2 sm:p-1` to bump the mobile hit-box to ~32px.

---

## P2 — Polish

### P2-1: Sticky nav doesn't compress on scroll

**File:** `src/components/nav.tsx` line ~93
**Issue:** Nav uses `sticky top-0` h-14 (56px). On a 375×667px iPhone SE
viewport, that's ~8% of vertical space gone forever even while reading.

**Fix:** Hide on scroll-down + show on scroll-up (`useScrollDirection`
hook). Lower priority because the nav is slim and we don't have
millions of users scrolling thousands of pixels yet.

### P2-2: Stat-cards grid has different aspect ratios on mobile

**File:** Landing "Built on real data" block + accuracy hero
**Issue:** 2-column or 4-column grid that collapses to 2-col on
mobile. The cards' aspect ratios shift; one feels tall, others squat.

**Fix:** Add `min-h-[N]` to ensure consistent card height. Cosmetic.

### P2-3: Footer dense on mobile

**File:** `src/app/page.tsx` footer + partner-badges row
**Issue:** Three rows of links + 3 partner badges flex-wrap into
something like 6 visual rows on mobile. Not broken, just dense.

**Fix:** Reduce padding + tighten link gaps on mobile. Cosmetic.

### P2-4: Match-detail page density

**File:** `src/app/(app)/matches/[id]/page.tsx`
**Issue:** Heavy intelligence layer (signals + lineups + odds + value
bet) with collapsible accordions but still ~5 screens tall on mobile.

**Fix:** Audit which sections are first-fold critical; consider a
"summary card → tap to expand sections" mobile-first layout. This is
larger work — file as a separate `GROWTH-MATCH-DETAIL-MOBILE` task
rather than fold into this audit.

---

## Fix sequencing

**Batch 1 (P0, ship today):**
- Landing-page mobile nav drawer (P0-1)
- Competitor matrix mobile stack (P0-2)
- Pricing "Most Popular" badge positioning fix (P0-3)

**Batch 2 (P1, ship next session):**
- one-screen-proof mobile stack (P1-2)
- Value-bets filter widths (P1-3)
- Modal close button hit-box (P1-4)
- Button xs size bump (P1-1)

**Deferred to follow-up task `GROWTH-MATCH-DETAIL-MOBILE`:**
- P2-4 (match detail density) — substantial enough to warrant its
  own task scope

**Deferred to polish backlog:**
- P2-1 (scroll-aware nav), P2-2 (stat-card heights), P2-3 (footer density)

---

## Followup tasks to file

- `GROWTH-MATCH-DETAIL-MOBILE` — mobile-first redesign of
  `/matches/[id]` density (separate scope, ~3 days)
