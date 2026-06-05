# Mobile Landing Review — 2026-06-05

> Fresh-eyes audit of 13 mobile screenshots captured against
> https://oddsintel.app after GROWTH-MOBILE-FIRST-AUDIT P0/P1 +
> GROWTH-LANDING-REFACTOR shipped. Device: ~393px-wide Android
> (Chrome, with WC promo banner at top). Tone: honest. Counter-audit
> to the static `mobile-audit.md` — looking for what the static pass
> missed.

---

## 1. Snapshot summary

The landing is fundamentally **in good shape** — value prop is crisp,
sections flow, no broken layouts, no horizontal scroll, no
text-too-small. The mobile fixes from P0/P1 are visibly working
(hamburger present, competitor matrix flattens cleanly, no badge
clipping, no quintuple-stacked filter chips).

But the **first viewport is wasted**. The user opens the page and
sees: URL bar, WC promo banner, nav row, then **a giant empty black
band of ~30% viewport height before the "Beat the bookmakers."
headline appears**. There is no above-the-fold CTA, no headline, no
value prop — just black space (see screenshot 1 — the `pt-20`
top-padding on the hero section combined with the WC banner pushes
everything down). That is the single biggest mobile finding.

Secondary findings: the persistent green **"Feedback"** floating
button overlaps important content in 9 of 13 screenshots, the
**competitor-matrix mobile column glyphs are unlabeled** (5 anonymous
✓/✗ glyphs in a row with the legend tucked underneath — users have to
mentally map columns to names from a separate "Columns:" line), and
the **"Featured on"** badge row renders inconsistently (twelve.tools
shown full-width on its own row above the other two — looks like a
broken grid).

Overall: **B-minus on phone.** A few targeted fixes get this to a
solid A.

---

## 2. First-fold review (screenshot 1)

**What the user sees (top-to-bottom, ~800px above the keyboard area):**

1. Chrome URL bar (system)
2. WC promo banner — "World Cup 2026 starts in 7 days — play the bracket challenge"
3. Sticky nav — `ODDS INTEL [Beta]` logo + `Sign Up Free` button + hamburger
4. **~280px of pure black space** (the `pt-20 pb-16` on the hero)
5. Pill: "TRACKING 280+ LEAGUES WORLDWIDE · FOOTBALL / SOCCER"
6. Hero H1: "Beat the bookmakers." (cut off mid-headline at the fold)

**File ref:** `odds-intel-web/src/app/page.tsx:128` —
`<section className="relative overflow-hidden pt-20 pb-16 text-center">`

### What works
- The **`Sign Up Free`** button in the nav is in the right-hand
  thumb-zone and is the only green CTA up there — visually loud
- Headline copy is great ("Beat the bookmakers." is the strongest
  short prop the product has)
- "BETA" tag next to the logo is nicely sized and disambiguates that
  this isn't a Bet365 lookalike scam
- WC promo banner is genuinely useful — adds urgency without being a
  popup

### What doesn't work
- **The H1 sits below the fold on a 393px-wide Android with system
  chrome.** First-time visitors land on a black page with a sticky
  nav and a 7-day-WC banner. The single most important asset on the
  site — the value prop — requires scrolling.
- **No primary CTA in the first viewport other than the nav button.**
  The nav `Sign Up Free` is small (size=sm) and shares attention
  with the BETA tag and hamburger. The big green `Start Free` button
  is ~1100px down the page (screenshot 1, bottom).
- The "TRACKING 280+ LEAGUES" pill feels like a label looking for a
  product. With no headline visible above it, it reads as a banner
  ad header rather than supporting copy.
- **No thumb-zone CTA anywhere on the page.** The user never sees a
  fixed bottom-of-screen primary action. The persistent
  **Feedback** pill is in that thumb-zone slot, but it's not the CTA
  we want them tapping.

### Fix sketch
- Reduce `pt-20` to `pt-8 sm:pt-20` so the hero starts closer to
  the nav on mobile
- Consider a mobile-only **`fixed bottom-4 right-4`** Start Free
  pill that lives in the thumb-zone after the user scrolls past the
  in-hero CTA
- The trust pill ("TRACKING 280+ LEAGUES") can collapse to just
  "FOOTBALL · 280+ LEAGUES" — same info, half the width, won't be
  the first thing visible

---

## 3. Section-by-section review (scroll order)

### Section A — Hero (screenshot 1)

**Source:** `page.tsx:128-239`

What's shown: pill → headline → subhead with Telegram blue link →
trust micro-line (75% / +9.8% CLV / 21,831 matches) → Start Free +
See Today's Picks → "No credit card required" → mockup browser
chrome with Manchester City vs Arsenal odds table + 3 signal pills

**Works:**
- Headline is huge, balanced, doesn't wrap awkwardly
- "Telegram" highlighted in sky-blue is a strong visual hook for the
  product's differentiating delivery channel
- The 75% / +9.8% / 21,831 trust line is dense but readable — good
- Buttons are full-width on mobile (good thumb targets)
- "No credit card required. Free forever. Cancel any time on paid
  plans." is the right de-risk line

**Doesn't work:**
- "Beat the **bookmakers.**" — the period after bookmakers is
  visually noisy in green at this size. Minor.
- The "See Today's Picks →" secondary CTA bumps against the
  Feedback floater badge (screenshot 1, bottom-right) — they're
  ~30px apart. If the user goes for the secondary CTA with their
  right thumb, they'll mis-tap Feedback.

### Section B — Sample-match mockup (screenshot 2)

**Source:** `page.tsx:182-238`

What's shown: faux browser chrome → Premier League badge + High
market activity badge → Manchester City vs Arsenal → 3-bookmaker
odds table → 3 signal pills (injury, spread wide, odds shifted) →
"Sample data — illustrative"

**Works:**
- The browser-chrome framing makes it obvious this is the product,
  not a live odds widget
- The "best odds in green" pattern is immediately legible
- "Sample data — illustrative" subscript is honest and visible

**Doesn't work:**
- The mockup card is **really tall on mobile** — ~900px from the
  top of the chrome bar to the bottom of the signal pills. By
  this point we're already 1500px+ down the page. A user has
  scrolled past two "headlines worth of content" without seeing
  any social proof yet.
- The 3 signal pills wrap to 3 lines with varying widths — minor
  visual rag right edge. The two longer ones ("Bookmakers
  disagree — spread wide" and "Key player — injury doubt") have
  awkward em-dash line breaks.
- Manchester City / Arsenal text size feels a touch small relative
  to the odds numbers below it. The team name should be the
  visual anchor on a sports product mockup.

### Section C — CLV trust banner (screenshot 2 bottom, screenshot 3 top)

**Source:** `clv-trust-banner.tsx:117-168` (landing variant)

What's shown: huge `+9.5%` in green-emerald → "AVG CLV · LAST 30
DAYS" → "on 1,170 settled picks · All active AI bots" → Closing
Line Value explainer → "Win rate 46.0% · ROI +12.3%
(variance-confounded — CLV is the honest scoreboard)"

**Works:**
- This is the single strongest section on the page on mobile. The
  number is hero-scale, the framing is honest, the explainer
  earns its keep. CLV-first positioning lands cleanly.
- Amber tone on the gradient border is restrained, not gaudy
- "variance-confounded — CLV is the honest scoreboard" in
  parentheses is the kind of voice this product should use everywhere

**Doesn't work:**
- The text below the CLV number wraps awkwardly:
  > Win rate 46.0% · ROI +12.3% (variance-confounded — CLV is
  > the honest scoreboard).
  The closing parenthesis hangs on its own line. Cosmetic.
- The hero number `+9.5%` is great, but it's centered on its own
  line — on a narrow screen the left/right gutter could be tighter
  to make the number even more prominent.

### Section D — SEO structured-data block (screenshot 3)

**Source:** `page.tsx:249-289`

What's shown: 2-column grid of `COVERAGE / 280+ leagues`,
`BOOKMAKERS COMPARED / 13`, `30-DAY CLV / +9.5%`, `PICKS TRACKED
(30D) / 1,170`

**Works:** Clean, dense, machine-readable.

**Doesn't work:**
- **This block is a duplicate of the hero trust micro-line.**
  Screenshot 1 already showed `75% accuracy on O/U 1.5 · +9.8% CLV
  (30-day) · 21,831 matches tracked`. Now the user sees `+9.5%`
  CLV and `1,170` picks tracked — **different numbers in the same
  section.** The hero shows `+9.8%` CLV, the structured-data shows
  `+9.5%` CLV. The hero is hardcoded (`page.tsx:155`), the
  structured-data pulls from `heroCache`. These will diverge for
  most users.
- This is a real **content bug**: the user can see the discrepancy
  side by side if they scroll back up. Marketing-credibility hit.

### Section E — "One screen. Two seconds." (screenshots 3 & 4)

**Source:** `one-screen-proof.tsx`

What's shown on mobile: H2 → "Same workflow. Different speed." →
**stacked** 8-tabs card (red border, dim X icons next to
SoccerStats/Transfermarkt/WhoScored/etc) → "~90s — and the odds may
have already moved." → OddsIntel one-screen card (green border,
checkmarks next to: Best odds, Confirmed lineup, Key injury, Live
xG, Odds drift, Model edge) → "2s — everything, before the odds
move." → "Animation loops. Replace with a real screen recording
later — same point, more proof."

**Works:**
- The visual contrast (red dim card → green confident card) is
  the clearest "before/after" story on the page
- Stacking vertically on mobile (rather than side-by-side) is the
  right call. The static audit got this right.
- The two ~90s / 2s timestamps are the punchline. Very legible.

**Doesn't work:**
- **"Animation loops" caption is shipped to production.** That's
  internal copy ("Replace with a real screen recording later") that
  leaked through. Smells unfinished. The user shouldn't see a
  to-do note. (`one-screen-proof.tsx:123-126`)
- The 2-column grid of tab pills inside the left card has uneven
  widths — "SoccerStats / form", "Transfermarkt / lineups",
  "WhoScored / ratings" etc. The "what" label on the right
  truncates inconsistently (`Transfer...` vs `WhoScor...` vs
  `Premierl...`) — looks broken rather than intentional. The
  truncation pattern is uneven enough that on first glance I
  thought it was a layout bug, not a deliberate ellipsis.
  (`one-screen-proof.tsx:60-72`)
- The OddsIntel card's `oddsintel.app/matches/...` URL bar text is
  truncated mid-path — fine, but feels slightly cramped

### Section F — Competitor matrix (screenshots 5, 6, 7)

**Source:** `competitor-matrix.tsx:130-193`

What's shown: H2 "Most sites do one thing. We do all of them." →
intro paragraph → 4 sections (Core data / Odds intelligence /
Predictions + value / Delivery + transparency) each rendering as
a stack of rows on mobile, with 5 ✓/~/✗/⏳ glyphs per row → Killer
row "Spans all 5 competitor tiers" → "COLUMNS: SofaScore ·
OddsChecker · WinnerOdds · InPlayGuru · OddsIntel" → 4-line legend

**Works:**
- The flattened mobile layout is **much better than horizontal
  scrolling**. P0-2 fix delivered.
- The right-most green-highlighted column for OddsIntel is
  consistently visible
- "Spans all 5 competitor tiers" killer row at the bottom is the
  payoff

**Doesn't work:**
- **The 5 glyph columns have no header labels visible inline.** A
  user scanning the mobile view sees 5 ✓/✗/~ icons per row, and
  has no idea which column is which competitor. The legend
  ("Columns: SofaScore · OddsChecker · WinnerOdds · InPlayGuru ·
  OddsIntel") is rendered ONCE at the bottom of the entire matrix
  (screenshot 5, bottom of matrix region). For each individual
  row, the user has to either:
  1. Long-press to see the `title` tooltip (mobile doesn't do hover)
  2. Scroll all the way to the bottom and memorize column order
- The OddsIntel column has a subtle green background tint that
  helps, but the other 4 competitors are anonymous to a thumb-
  scrolling reader.
- **Fix:** Add a tiny header row INSIDE each section card with
  abbreviated column initials (`SS · OC · WO · IP · OI`) above the
  glyphs, OR render the section as named feature → 5 labeled
  badges per row (`SofaScore: ✓  OddsChecker: ✗ …`). Either is
  better than the current "5 anonymous glyphs".
  (`competitor-matrix.tsx:140-160`)

- Screenshot 5: "Spans all 5 competitor tiers" — the text "competitor
  tiers" wraps under "Spans all 5" on narrow screens, and the green
  ✓ glyph is offset from the row baseline (`competitor-matrix.tsx:164-180`)

- Long row labels wrap awkwardly: "Multi-strategy ensemble (not one
  model)" wraps to 3 lines (screenshot 6). The vertical rhythm of
  the section becomes uneven.

- The legend at the bottom (screenshot 7) shows the symbols
  centered as a single column on mobile (Have it / On roadmap /
  Partial / Doesn't offer). Each glyph is small (3.5px size icons).
  These should be slightly larger on mobile so they're scannable.

### Section G — Honest about how this works (screenshot 8)

**Source:** `page.tsx:321-368`

What's shown: H2 "Honest about how this works" → 3 stacked cards:
**Drawdown −€398** (amber) → **Verification Self-reported** (sky) →
**Honest metric CLV, not ROI** (green) → each with explainer +
"Why we publish drawdowns →" link

**Works:**
- The 3-card stacked layout is clean
- Each card has a distinct accent color so they read as separate
  topics
- The drawdown card especially is good positioning — admits a
  weakness up front
- All three CTAs ("Why we publish drawdowns →", "Why no 'verified'
  badge yet →", "Why CLV beats ROI →") are tappable links going to
  /methodology or /learn/closing-line-value

**Doesn't work:**
- The "Self-reported" text on the verification card is **huge** —
  `text-3xl font-black`. On a mobile screen this is the same size
  as the "−€398" number on the previous card. But "Self-reported"
  is a status, not a metric. It reads as if it's a number you
  should care about. Suggest sizing this down to `text-2xl` or
  `text-xl` to differentiate metrics-vs-statuses.
  (`page.tsx:349`)
- "CLV, not ROI" in font-mono on the green card looks more like a
  config flag than a positioning statement — the mono-spaced
  styling treats the three cards inconsistently (`-€398` is mono
  because it's a number; `Self-reported` and `CLV, not ROI` are
  not numbers but use the same mono treatment).

### Section H — Compact pricing CTA (screenshot 9)

**Source:** `page.tsx:373-393`

What's shown: "Free forever for fixtures, scores, and one daily AI
pick. Pro from €4.99/mo. Elite from €14.99/mo. Cancel any time." →
"See all plans →" outlined button

**Works:**
- Compact, all the pricing facts in one line
- "Cancel any time" closes the de-risk loop
- The "See all plans →" button is the only outline-style CTA on the
  page and clearly secondary

**Doesn't work:**
- This is the only section where the **pricing is text-only**.
  After the rest of the page being visually rich, this section
  feels skipped. The user scrolls past it in 0.3s.
- "See all plans" is too modest — a user scanning fast won't
  realize this is the path to subscribe. Pricing should
  arguably get a tiny bit more visual weight on mobile (a
  badge, a card, anything).

### Section I — Common questions FAQ (screenshots 9, 10)

**Source:** `page.tsx:402-417`

What's shown: H2 "Common questions" → 5 expanded cards:
- What sports and leagues does OddsIntel cover?
- Which bookmakers are compared?
- How do the AI picks work?
- What is CLV tracking?
- Where do the picks go?

Each card shows the question + the full answer always-expanded.

**Works:**
- Always-expanded means SEO eats it and users don't tap-then-wait
- Answers are detailed and honest
- The "How do the AI picks work?" answer (screenshot 10) is the
  strongest — explicitly admits "even an 80%-accurate pick at 1.10
  odds loses money long-term. That's why we publish CLV"

**Doesn't work:**
- **No accordion behavior on mobile is a missed opportunity.**
  The FAQ section consumes ~3 full mobile viewports (screenshots
  9, 10, and most of 11) because every answer is always expanded.
  A `<details>` element with the question collapsed would
  collapse this to ~1 viewport and let the user pick what to
  expand. Right now they have to scroll through ~2400px of FAQ
  to reach the next section.
- The "How do the AI picks work?" answer (screenshot 10) is
  particularly long — 14 lines on mobile — and would benefit
  most from being collapsed by default.

### Section J — Trust strip + Telegram CTA (screenshots 11, 12)

**Source:** `page.tsx:425-484`

What's shown: green pulse dot + "Paper-bet chain unbroken since
2026-05-03" / "Open methodology — read the model" / "30-day cancel
any time on paid plans" → then dark sky-blue band → 📲 TELEGRAM
DELIVERY pill → H2 "Get tomorrow's value bets in your Telegram." →
sub-paragraph → big sky-blue **Start Free** button → outlined
"Already signed up — connect Telegram →" → "Telegram alerts
available on Pro and Elite. Free users get one daily pick on-site."

**Works:**
- The sky-blue gradient distinguishes the Telegram CTA from the
  green hero CTA — visually says "this is a different ask"
- Stacking the trust strip vertically on mobile is fine (3 short
  lines)
- "Already signed up — connect Telegram →" is a clever returning-
  user path

**Doesn't work:**
- The Telegram CTA is **the strongest CTA on the page**, and it's
  the LAST CTA before the footer. A user who scrolls quickly
  through the value prop and is sold on Telegram has to swipe
  through ~5 sections to reach this CTA. Should this section
  appear higher? Possibly mirrored higher up?
- The pulsing green dot (`shadow-[0_0_6px_rgba(34,197,94,0.7)]`) is
  cute but the "Paper-bet chain unbroken since 2026-05-03" copy is
  inside a Link to `/performance` — it's not obvious it's tappable
  on mobile (no underline, no chevron). Could use a `→` like the
  other trust-strip line.

### Section K — Featured on / partner badges (screenshots 12, 13)

**Source:** `page.tsx:492-545`

What's shown: "FEATURED ON" caption (centered, all-caps) → twelve.tools
badge (full-width-ish, on its own row) → Wired Business badge →
AIBoom.Tools badge

**Works:**
- Badges are SVGs, scale well
- The "Featured on" label sets the right expectation

**Doesn't work:**
- The 3 badges render with **uneven widths and alignments**. The
  twelve.tools badge takes the entire row on screenshot 12 (it's
  ~200×54px and looks oversized relative to AIBoom which is
  120×32px). The Wired Business badge is on its own row below.
  AIBoom is on the same row as Wired (screenshot 13). The flex-wrap
  layout has produced 3 visually different sizes that look like an
  accident, not a design.
- twelve.tools also has its own internal "FEATURED ON" text inside
  the badge — combined with our outer "FEATURED ON" caption, the
  word appears **twice**, ~20px apart. (Look at screenshot 13 — you
  can read "FEATURED ON" three times in 200 vertical pixels.)
- **Fix:** Either normalize all badges to a common height
  (`h-12` or similar) so the row reads as a deliberate carousel, or
  drop the external "FEATURED ON" caption since the badges already
  carry it themselves.
  (`page.tsx:497-499`)

### Section L — Footer (screenshot 13)

**Source:** `page.tsx:548-568`

What's shown: ODDS INTEL logo wordmark → © 2026 OddsIntel /
Methodology / Performance / Changelog / Terms of Service / Privacy
Policy → "**Responsible Gambling:** Betting involves risk. Data
provides intelligence, not certainty. 18+ Only."

**Works:**
- All the legal/transparency links present
- Responsible Gambling notice is appropriately styled and clear
- Footer links wrap reasonably

**Doesn't work:**
- The footer links wrap to multiple lines on mobile but they're
  tightly spaced. A user with a fat thumb could mis-tap between
  Performance and Changelog. Minor.

### Persistent across all screenshots — Feedback floater

A green pill with `💬 Feedback` is fixed bottom-right and overlaps
content in screenshots 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11. It hides:
- Part of "Cancel any time on paid plans" in screenshot 1
- Part of the methodology hyperlinks in screenshot 5
- Part of the "Multi-bookmaker depth (50+)" row in screenshot 6
- Part of "Closing Line Value (CLV) measures whether a..." in screenshot 9
- "Pre-kickoff. Pre-line-movement. Pre-..." in screenshot 11

This is **the most consistent friction point in the whole audit.**
Couldn't locate the source in `page.tsx` — operator will need to
grep for "Feedback" or "feedback" in the layout/global components.
Likely in `src/app/layout.tsx` or a `<FeedbackWidget />` mount.

---

## 4. P0 issues — broken / blocking

### P0-A: Hero requires scrolling on mobile to see the value prop
**Where:** `odds-intel-web/src/app/page.tsx:128`
(`<section className="... pt-20 pb-16 ...">`)
**Why broken:** With WC banner + sticky nav consuming ~120px, the
`pt-20` (80px) padding pushes the H1 below the fold on ~393px-wide
phones. First-time visitors see a black band before "Beat the
bookmakers." The single most important asset on the highest-traffic
page is below the fold.
**Fix:** `pt-8 sm:pt-20` — keep desktop unchanged, halve mobile top
padding. Estimated: H1 fully above the fold on 393px-wide phones.

### P0-B: Hero CLV claim conflicts with structured-data CLV claim
**Where:** `page.tsx:155` (hardcoded `+9.8%`) vs `page.tsx:275-277`
(`heroCLVPct` from `dashboard_cache`)
**Why broken:** Screenshot 1 shows hero claim `+9.8% CLV (30-day)`.
Screenshot 3 shows live data `+9.5%`. Both visible within ~2 scrolls
of each other. Users notice. CLV credibility — the central pillar of
the brand — gets undermined by our own page.
**Fix:** Replace the hardcoded `+9.8%` in the hero trust micro-line
with the same `heroCLVPct` value pulled from the cache. One source
of truth.

### P0-C: Persistent Feedback floater obscures content
**Where:** Couldn't locate source — likely `app/layout.tsx` or a
mounted client component. Operator: grep for "Feedback" component.
**Why broken:** Floats over real content on 11 of 13 screenshots.
Sits in the prime thumb-zone where the primary CTA should be. Most
frequent visual friction point in the whole audit.
**Fix:** Either (a) hide it on landing page entirely (`pathname ===
"/"` check), (b) make it smaller/lower-contrast, or (c) auto-hide
after 30s of inactivity. Best is (a) — the landing should be a
conversion page, not a feedback-collection page.

---

## 5. P1 issues — visible friction, not blocking

### P1-A: Competitor-matrix glyphs have no inline column labels
**Where:** `competitor-matrix.tsx:140-160` (mobile stack section)
**Why friction:** 5 anonymous ✓/✗/~/⏳ icons per row. Legend is at
the bottom of the section once. Users can't scan the table without
remembering column order.
**Fix:** Add abbreviated column initials (e.g. `SS · OC · WO · IP ·
OI`) as a sticky header inside each section card, OR render each
row as named pills (`SofaScore ✗ · OddsChecker ✓ · …`). The latter
is more space-hungry but unambiguous.

### P1-B: "Animation loops" placeholder copy is shipped to production
**Where:** `one-screen-proof.tsx:123-126`
**Why friction:** Internal to-do leaked to users: "Animation loops.
Replace with a real screen recording later — same point, more
proof." Reads like an admission the page is unfinished.
**Fix:** Delete or replace with neutral caption (e.g. "Side-by-side
comparison · representative product flow").

### P1-C: Featured-on badge row visually broken
**Where:** `page.tsx:492-545`
**Why friction:** Inconsistent badge heights (54px vs 32px) cause
twelve.tools to render full-row alone, then Wired+AIBoom together.
Plus duplicate "FEATURED ON" text (in our caption AND inside two of
the badges).
**Fix:** Constrain all three to `h-10` (40px) via wrapping div, drop
the external "FEATURED ON" caption (badges self-label).

### P1-D: FAQ section is always-expanded — consumes 3 viewports
**Where:** `page.tsx:402-417`
**Why friction:** 5 long answers always visible. The "How do the AI
picks work?" answer alone takes 14 lines on mobile. Users scroll
~2400px through FAQ to reach the Telegram CTA.
**Fix:** Use native `<details>` with first item open, rest closed —
preserves SEO indexing of all answer text, but lets users scan
questions first.

### P1-E: "Self-reported" heading too large in honest-about card
**Where:** `page.tsx:349` (`text-3xl font-black`)
**Why friction:** A status label gets the same visual weight as the
`−€398` number on the adjacent card. Reads as a metric when it's a
state.
**Fix:** Drop to `text-2xl` or `text-xl` for non-numeric headings
(`Self-reported`, `CLV, not ROI`).

### P1-F: Tabs in "8 tabs" card have uneven truncation
**Where:** `one-screen-proof.tsx:60-72`
**Why friction:** Short names ("SoccerStats", "FBref") render full;
longer ones ("Transfermarkt", "PremierInjuries", "WhoScored",
"Twitter / X") truncate inconsistently. Looks like a layout bug.
**Fix:** Shorter source names — e.g. "SoccerStats" → "Stats",
"Transfermarkt" → "Transfers", "PremierInjuries" → "Injuries",
"Twitter / X" → "X". Same point, no truncation jitter.

---

## 6. P2 polish — nice-to-have

- Trust pill "TRACKING 280+ LEAGUES WORLDWIDE · FOOTBALL / SOCCER"
  could compress to "FOOTBALL · 280+ LEAGUES" on mobile
- Manchester City / Arsenal team names in the hero mockup are
  slightly small relative to odds numbers
- Hero "Beat the bookmakers." — the trailing period in green feels
  over-styled; the green could end at `bookmakers` and the period
  stay white
- The Telegram CTA paragraph wraps oddly ("Pre-kickoff. Pre-line-
  movement. Pre-everything." has three short sentences but the
  parallelism only lands on desktop where they're inline)
- Pulsing green dot in trust strip could use `→` after the link
  text to telegraph tappability on mobile
- Footer link `Performance` is also the second word in "Open
  methodology — read the model" — small redundancy

---

## 7. Recommended next batch — ship ONE thing next

**If shipping exactly one mobile-landing fix next: ship P0-C
(hide the Feedback floater on `/`).** Rationale:

1. **Highest visual-noise reduction per line of code.** A 3-line
   conditional in the layout file removes a UX issue that affects
   11 of 13 screenshots. Nothing else has that ratio.
2. **It blocks the thumb-zone CTA slot.** Once the floater is gone
   from `/`, we can fill that slot with a `fixed bottom-4
   inset-x-4` `Start Free` button on mobile, which is the highest-
   leverage conversion change available on the page. Floater
   removal is the prerequisite.
3. **Zero risk.** The Feedback feature still works everywhere else
   in the app — we just suppress it on the conversion page.

**Suggested batch (top to bottom of priority):**
1. Hide Feedback floater on `/` (P0-C) + add mobile thumb-zone
   Start Free pill
2. Fix hero top padding (P0-A) + unify CLV number source (P0-B)
3. Label competitor-matrix columns inline (P1-A)
4. Drop "Animation loops" caption + fix tab name truncation
   (P1-B + P1-F)
5. Collapse FAQ + normalize Featured-on badges (P1-D + P1-C)

If we batch (1) and (2) together, the first-fold experience on
mobile transforms from "black space → headline you can't see" to
"headline → CLV proof → thumb-zone CTA" without touching any
content. That batch alone is probably worth a measurable conversion
lift.

---

## Cross-reference: claims & evidence

| Claim                                          | Screenshot | Source ref                                  |
|------------------------------------------------|------------|---------------------------------------------|
| Hero H1 below fold                             | 1          | `page.tsx:128` (`pt-20`)                    |
| Hero CLV mismatch (`+9.8%` vs `+9.5%`)         | 1, 3       | `page.tsx:155` vs `page.tsx:275`            |
| Feedback floater overlap                       | 1, 2, 3, 5, 6, 8, 9, 10, 11 | couldn't locate — operator grep      |
| Competitor matrix glyphs unlabeled per-row     | 5, 6, 7    | `competitor-matrix.tsx:140-160`             |
| "Animation loops" caption shipped              | 4          | `one-screen-proof.tsx:123-126`              |
| Tab name truncation inconsistent               | 3          | `one-screen-proof.tsx:60-72`                |
| Self-reported heading too large                | 8          | `page.tsx:349`                              |
| Featured-on badge row uneven                   | 12, 13     | `page.tsx:497-545`                          |
| FAQ always-expanded consumes 3 viewports       | 9, 10, 11  | `page.tsx:402-417`                          |
| Telegram CTA buried at bottom                  | 11, 12     | `page.tsx:447-484`                          |

---

## Audit method note

Read 13 screenshots end-to-end at native resolution (~922×2048),
then cross-checked each visible section against the rendered source
in `odds-intel-web/src/app/page.tsx` and component files. Static
audit `mobile-audit.md` was consulted to avoid double-flagging
already-shipped P0/P1 items. The three findings most likely to
matter — hero padding, CLV-number conflict, Feedback floater —
were not flagged in the static audit because they require visual
inspection at the actual viewport with the WC banner mounted.
That's the value of a re-audit on real screens.
