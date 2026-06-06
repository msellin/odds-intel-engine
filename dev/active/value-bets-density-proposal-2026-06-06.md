# VALUE-BETS-DENSITY-PASS — Expanded Design Proposal (2026-06-06 v2)

**Purpose.** Operator flagged 2026-06-06 that `/value-bets` is too dense — initially focused on row layout but the bigger issue is **page-level** stacking. Above the fold on mobile, the user sees 5-7 chunks of secondary content (CLV banner, tier explainer, Telegram CTA, live section, CLV trust banner, today's picks preview) BEFORE reaching the value bets they came for.

This v2 covers three tiers of density problems:

- **Tier 1 — Page layout** (highest impact): what sections appear above the fold and how to compress them
- **Tier 2 — /live duplication** (medium impact): `ValueBetsLiveSection` renders on BOTH `/value-bets` and `/live` — same data shown twice depending on entry point
- **Tier 3 — Row layout** (original scope): the per-row chip soup

**Status.** Doc only — no code shipped. v1 of this proposal was row-only; v2 extends to page-level + /live.

---

## Tier 1 — Page layout audit

### What renders on `/value-bets` (top-down, Pro/Elite view)

| # | Section | Component | Lines on mobile | What it does |
|---|---|---|---:|---|
| 1 | Page title + subtitle | `<h1>` + `<p>` | 3-5 | "Today's value bets — CLV-tracked…" + "Why CLV beats ROI →" link |
| 2 | Tier explainer | inline `<section>` | 3-5 | "You're seeing the calibrated/full feed" + 3-tier description |
| 3 | Telegram CTA | inline `<Link>` | 2 | "Get these picks in Telegram — sent the moment they're identified" |
| 4 | CLV trust banner | `<CLVTrustBanner>` | 5-7 | "Closing Line Value · last 30 days · +10.0% CLV · +13.3% ROI · 48.3% win · 1,023 settled · {explanation paragraph}" |
| 5 | **Live now** section | `<ValueBetsLiveSection>` | 2 + N rows | In-play picks auto-refreshing every 60s |
| 6 | Today's picks preview | `<TodayPicksPreview>` | 3-4 | The free-tier teaser surface (shown to all tiers) |
| 7 | **The actual value bets** | `<ValueBetsScan>` | 80 rows | What the user came for |

**Mobile reality:** ~600-700px of pre-list content before section #7. That's the entire above-the-fold viewport on a 600px-tall mobile screen. The user has to scroll past 6 sections to reach what the page is titled after.

### Why this is happening (charitable read)

Each section solves a real problem:
- **#1 title + subtitle** — CLV-first messaging is our differentiator (operator-confirmed)
- **#2 tier explainer** — answers "what am I seeing here?" for new users
- **#3 Telegram CTA** — drives Telegram-bot signups (engagement metric)
- **#4 CLV trust banner** — social proof / honest scoreboard
- **#5 live now** — surfaces in-play picks before they expire
- **#6 today's picks preview** — free-tier teaser conversion hook

The problem is they're all on the SAME page competing for prime real estate. Each section is justified in isolation. As a sequence they bury the primary content.

### Proposed compression

**Move #4 (CLV trust banner) to `/performance` or `/about`.** It's a stats card — context, not action. Users who care about CLV will navigate there. Anyone scanning value bets just needs the headline number, which can sit in a compact pill in the page header (item #1).

**Collapse #2 (tier explainer) to a one-line pill** for the current user's tier only. Free users see all 3 tiers' descriptions today; we only need to show the relevant one + "→ upgrade" if applicable.

**Move #3 (Telegram CTA) to a corner pill** — full-width banner is overkill for a "you already have Telegram? open settings" link. A small chip in the page header next to the title accomplishes the same job.

**Decide on #5 (live now) — see Tier 2 below.** Either keep on /value-bets and remove from /live, or move to /live only.

**Keep #6 (today's picks preview)** — it's the free-tier conversion hook and only renders for free users. Already gated.

### Compressed page (proposed)

```
┌─────────────────────────────────────────────────────────────────┐
│  Today's value bets · CLV-tracked   [+10.0% CLV ·30d]  📲 [tg]  │
│  ⓘ Showing 80 bets · all 39 strategies · Why CLV?              │
├─────────────────────────────────────────────────────────────────┤
│ {actual value bets list starts immediately here}                │
│  +19% │ Athletic Club MG U20 — Itabirito U20  ●  9m   │ ⌄      │
│       │ DC · x2 ·  2.78 Marathonbet · 4.4u                       │
│ ... etc                                                         │
└─────────────────────────────────────────────────────────────────┘
```

Two-line page header + immediate list. CLV stat is a pill, not a card. Telegram is a small icon chip. Tier explainer collapses to "Showing 80 bets · all 39 strategies" with a `ⓘ` info icon that expands the original explainer on tap (for first-time users only — could store dismiss state in localStorage).

**Mobile gain:** ~500-600px of vertical space recovered = at least 4 more rows visible above the fold.

---

## Tier 2 — `/live` duplication

### The current state

Both pages render the SAME `<ValueBetsLiveSection>` component:

- **`/value-bets`** (Pro+, when in-play bets exist): renders the live section ABOVE the pre-match list
- **`/live`** (Pro+ dedicated page): renders the live section as the primary content + a Free-tier teaser for anonymous/Free users

Same data, two surfaces. Users moving from one to the other see identical content.

### Why both exist

- **`/live`** was filed as `GROWTH-LIVE-PAGE-BUILD` (2026-06-05, Tier B #2) per the file header — a marketing/SEO surface for in-play picks
- **`/value-bets` live section** was added earlier as an inline live-picks visibility because users on the value-bets page want to see what's live RIGHT NOW

Both have legitimate motivations. The duplication is the side effect.

### Two ways to resolve

**Option DUP-A — Move live ONLY to /live, replace inline section on /value-bets with a link**

```
┌─────────────────────────────────────────────────────────────────┐
│  ● 3 live picks right now → /live                               │
└─────────────────────────────────────────────────────────────────┘
```

- **Pro:** /value-bets becomes pre-match-only (clear scope). /live owns in-play. Honest separation.
- **Con:** Users on /value-bets have to click to see live picks. Live picks expire fast — extra friction = missed picks.

**Option DUP-B — Keep live on /value-bets, drop /live page (or repurpose)**

- **Pro:** Live picks stay visible on the page users naturally land on. One less SEO/marketing page to maintain.
- **Con:** Loses the SEO surface that GROWTH-LIVE-PAGE-BUILD was created for. /live URL becomes a redirect.

**Option DUP-C — Keep both, but visually differentiate**

- Make `/value-bets` live section a COMPACT one-row strip ("● 3 live: Criciuma -€2.10, Aris +€1.50, Botafogo even").
- Make `/live` the FULL grid view with detail.
- Page-purpose stays distinct; visual treatment makes the duplication obvious as "preview vs full."

### Recommendation: DUP-C (preserves SEO + reduces noise)

Compact 1-row strip on /value-bets ("● 3 live: ..."). Click to expand into the full /live page. Best of both — no friction on /value-bets, dedicated surface on /live.

---

## Tier 3 — Row layout (original v1 scope, lightly revised)

### Row carries 10 distinct items today (unchanged from v1)

(See screenshots from 2026-06-06.) Edge %, match name, status chip, selection+market, PRO badge, percentage chip, best odds + book, stake, kickoff, chevron.

### Three layout alternatives

#### Option A — "Glance" (most aggressive)

```
+19% │ Athletic Club MG U20 — Itabirito U20    ●    9m │ ⌄
     │ DC · x2
```
4 primary signals + maturity dot. Tap row for odds/stake/details.

#### Option B — "Two-row hybrid" (RECOMMENDED, unchanged from v1)

```
+19% │ Athletic Club MG U20 — Itabirito U20       ●   9m │
     │ DC · x2  ·  2.78 Marathonbet · 4.4u
```
Primary line: edge + match + maturity dot + time. Secondary line: selection + market + odds + book + stake.

#### Option C — "Filter-out" (no row redesign)

Add "Calibrated only (12)" filter chip. Default view filters to ~12 from 80.

---

## 4 universal density wins (apply to whichever Tier 3 option you pick)

1. **Drop the standalone percentage chip** (45%, 57%, 52%) — meaning unclear.
2. **Drop the `PRE-MATCH` chip when time is "9m"** — implied. Keep `LIVE` (distinct).
3. **Combine selection + market** using `fmtSelShort()` ("1X2 · home" → "1X2 H").
4. **Bookmaker name to tooltip** — show price as primary, book on hover.

---

## Open questions before I implement

1. **Page layout (Tier 1):** approve the compressed page header + moving CLV trust banner to `/performance`?
2. **/live duplication (Tier 2):** DUP-A (link only), DUP-B (drop /live), or DUP-C (compact strip)?
3. **Row layout (Tier 3):** A, B, or C? (My pick: B)
4. **Left colored strip on rows:** what does it mean today? drop or label?
5. **Percentage chip:** what does it mean today? confirm before dropping.
6. **PRO badge:** info or gate? Affects whether it can move to hover.

---

## Effort estimate (per tier)

| Tier | Effort | What it ships |
|---|---|---|
| **Tier 1 — Page layout** | ~2-3h | New compact page header, move CLV trust banner, collapse tier explainer, Telegram pill |
| **Tier 2 — /live (DUP-C)** | ~1h | Compact live strip on /value-bets, keep /live full grid |
| **Tier 3 — Rows (Option B)** | ~2-3h | Two-row layout in `value-bets-scan.tsx`, maturity dot integration, universal density wins |
| **Smokes + screenshots** | ~1h | Pin invariants, before/after screenshots |
| **Total** | **~6-8h** | Material density improvement across the page |

---

## Recommendation

**Ship all three tiers in one batch.** They reinforce each other:
- Tier 1 alone leaves the live section + rows still dense
- Tier 3 alone leaves the page-header still cluttered
- Tier 2 alone doesn't fix the underlying scan-density problem
- Together: page becomes scan-friendly, rows are compact, duplication resolved

Sequence: **Tier 1 first** (biggest single-step win), then Tier 2 (small change, big clarity), then Tier 3 (row work). Total ~6-8h, could ship Monday-Tuesday alongside the PUBLIC-MATURITY-BADGE work.

---

## What I need from you to proceed

Answer the 6 open questions above. Or pick: "ship Tier 1+2+3 with my recommendations, ask me when in doubt" → I start implementing.
