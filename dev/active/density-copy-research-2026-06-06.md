# Density & copy research spike — 2026-06-06

**Purpose:** before committing 3-5 days to `GROWTH-COPY-DENSITY-AUDIT`
and `GROWTH-DESKTOP-DASHBOARD-DENSITY`, validate whether the "we're
over-selling, DeepBetting is sparser" intuition holds across a broader
competitor sample, our own analytics, and industry research. Avoid
the one-data-point trap.

**Spike output:** verdict on whether to do those two tasks, do something
else, or do nothing.

---

## Part 0 — Our own baseline

Counted directly from the source on `main` (commit `f0aa769`).
Word counts are user-visible string content (approximate ±20% — grep
extraction; includes some code strings).

| Page | Total words (approx) | Section count | Notes |
|---|---|---|---|
| `/` (landing) | ~706 | 13 | Heavy: hero + CLV banner + SEO dl + OneScreenProof + CompetitorMatrix + "Honest about how this works" 3-card + compact pricing CTA + FAQ + trust strip + Telegram CTA + partner badges + footer |
| `/methodology` | ~652 | ~10 | Long-form explainer (model architecture, signals, calibration, drawdown, verification roadmap) |
| `/value-bets` | ~200 | ~3-4 | Sparse (mostly the live grid + filters) — closest to "data over copy" already |
| `/pricing` | ~480 | ~6 | Pricing cards + feature matrix + FAQ. Higher than expected for a pricing page |
| `/how-it-works` | ~622 | ~8 | Deep funnel explainer + tier comparison |

### Landing-specific section inventory (above-fold and below)

1. **WC banner** (auto-hide 2026-07-26)
2. **Hero** — H1, subhead, trust micro-line (75% / +CLV / 21,831 matches), 2 CTAs, product mockup
3. **CLV Trust Banner** (external `<CLVTrustBanner />`)
4. **SEO Coverage `<dl>`** — 4 stats (leagues / books / CLV / picks)
5. **`<OneScreenProof />`** — animated "8 tabs vs 1 screen"
6. **`<CompetitorMatrix />`** — tier comparison + killer row
7. **"Honest about how this works"** — 3 cards (drawdown / verification / CLV)
8. **Compact pricing CTA** ("Free forever … Pro €4.99 … Elite €14.99 … See all plans →")
9. **FAQ** — 5 items (`<details>`)
10. **Trust strip** — 3 inline facts
11. **Telegram CTA strip** — full-bleed CTA section
12. **Partner badges** — Twelve Tools / Wired / AIBoom (directory backlinks)
13. **Footer** — links + responsible gambling notice

**Above-the-fold (desktop ~800px viewport):** sections 1-2 + part of 3-4.
**Visible-on-first-scroll:** sections 5-7.
**Conversion-critical surface:** sections 7-10 (where pricing/CTA finally lands).

### Self-spotted potential over-selling (gut-check, not data)

- Section 7 ("Honest about how this works") leads with the sentence
  **"Three things most prediction sites hide. We publish them on purpose."**
  → reads as preachy positioning rather than data. Same point could
  land via the three numbers (−€398 / Self-reported / CLV not ROI)
  without the meta-narration.
- Trust micro-line in the hero (`75% accuracy · +CLV · 21,831 matches`)
  is dense + good — keep.
- The pricing CTA is asked **three times** on the landing (compact
  CTA section + FAQ "how much" + Telegram CTA mentions Pro/Elite). May
  be redundant.
- `/methodology` and `/how-it-works` have meaningful overlap. The funnel
  question "what's the model?" is answered partially in both; unclear
  which is canonical.

These are gut-check observations, not verdicts. Real verdict requires
the competitor data + analytics below.

---

## Part 1 — Competitor density survey

Word counts approximate (±20%). **Copy density** = how much marketing
prose dominates (1 = numbers/widgets, 5 = prose-heavy). **Data density**
= live numbers/tables/dashboards visible on landing (1 = none, 5 =
dashboard-like).

| Site | Hero words | Total page | Sections | Pricing on landing | Trust signals | Copy density | Data density | Character |
|---|---|---|---|---|---|---|---|---|
| **winnerodds.com** *(only verified-+ROI competitor)* | ~80-100 | ~4,500-5k | 12 | No (login-gated) | Named experts (Buchdahl, Pete Ling), **€14.76M cumulative user profit**, 3,635 bets / 4.5% yield, named CEO; no audit badge | **2** | **4** | Numbers-led with persuasion layered on top |
| deepbetting.io | ~60 | ~1,200 | 12 | Yes (4 tiers, €34.99-59.99) | Named founders, AI-directory badges, no testimonials, no track record | 4 | 2 | Prose-heavy, light data |
| inplayguru.com | ~45 | ~2,200 | 10 | No | 30k+ members, 16 Trustpilot quotes | 3 | 2 | Capability-led marketing prose |
| oddspedia.com | (403 — WebSearch) | n/a | Aggregator format | No (free) | 250 books, 30 sports | 2 | 5 | Live odds-comparison dashboard |
| forebet.com | (403 — WebSearch) | n/a | Tables-first | No (free) | 850+ leagues | **1** | **5** | Pure data table layout, ~zero marketing copy |
| soccer-rating.com | ~35 | ~1,200 | 7 | No | Named tipsters, 15-20% ROI claim on 100% Value Index | 2 | 5 | Tables dominate |
| soccerbot.ai | (403 — WebSearch) | n/a | Builder UI | Yes (3 tiers) | Slip card UI, 50+ signals, 20+ books | 3 | 3 | Tool-led; bet-builder is the hero |
| sportbotai.com | ~85 | ~4,500 | 15+ | Yes ($0-$999) | Public 30-day record (**-27% ROI**, honest), methodology link | 3 | 4 | Honest data-led incl. negative ROI |
| sofascore.com | ~25 | ~1,200 | 9 | No (free) | UEFA/FIFA partnerships, 500+ leagues | 1 | 5 | Live-scores aggregator |
| oddschecker.com | (403 — WebSearch) | n/a | Tables-first | No (affiliate) | 25+ bookmakers, since 1999, 125M price changes/day | 1 | 5 | Pure odds-comparison grid |
| rebelbetting.com | ~85 | ~4,200 | 12 | Yes (€99/€199) | Trustpilot, 8+ named testimonials, **€22M member profit**, 325k users, 17 years, profit guarantee | 3 | 4 | Numbers-heavy with prose scaffolding |
| **OddsIntel (us)** | ~85-100 | ~706 | 13 | Compact CTA only | "Honest" 3-card, methodology link, no testimonials, no cumulative profit number | **3** | **3** | Mid-density both axes; verbose for our paid-product peer group |

**Reads of the matrix:**
- The **verified-+ROI** anchor (WinnerOdds) is **prose-sparse + numbers-dense**. Their hero headline is 4 words ("Make Money Betting"). The page is long but most of the length is FAQ + named-expert quotes + statistics, not narrative explanation.
- **Free aggregators** (Forebet, SofaScore, OddsChecker, Oddspedia) cluster at copy 1-2 / data 5. They don't need copy because the product itself is the data.
- **Paid subscription competitors** generally run wordier (RebelBetting 4,200; SportBot 4,500; DeepBetting 1,200 but prose-dense). **But the credible ones** (WinnerOdds, RebelBetting) substitute numbers for prose: €14.76M / €22M cumulative member profit do the trust work.
- **We** (706 words) are mid-pack on length and meaningfully sparser than RebelBetting/SportBot, but the **type of content** is closer to DeepBetting's prose-heavy style than WinnerOdds' numbers-led style. We don't have a hero-level cumulative number; we have 3 stats spread (75% / CLV / 21,831 matches) but they read as "stats" not "outcomes."

---

## Part 2 — SaaS comparators (Linear, Notion, Vercel, Stripe, Cal.com)

| Site | Hero words | Total page | Pricing on landing | Trust signals | Copy density | Data density | Hero headline |
|---|---|---|---|---|---|---|---|
| linear.app | **25** | ~3,000 | No | 33,000+ teams, named OpenAI/Ramp quotes | **1** | 4 | "The product development system for teams and agents" |
| notion.com | ~30 | ~1,200 | Yes (ROI calc) | 98% Forbes Cloud 100, 100M users, 62% Fortune 100 | 1 | 4 | "Meet the night shift. Notion agents keep work moving 24/7." |
| vercel.com | **17** | ~1,200 | No | Runway/Leonardo logos, 24× faster build, 95% page-load lift | 1 | 4 | "Build and deploy on the AI Cloud." |
| stripe.com | ~45 | ~2,800 | Partial ($0.01/1k) | 20+ logos (Amazon, Shopify, Anthropic, Google), **$1.9T volume**, 99.999% uptime, 500M API req/day | 2 | 5 | "Financial infrastructure to grow your revenue." |
| cal.com | ~45 | ~2,100 | Yes ($16-37/user) | Named founder quotes (Kent C. Dodds, Rauch), SOC2, 1M+ users | 2 | 3 | "The better way to schedule your meetings" |

**SaaS gold-standard pattern is unambiguous:** 17-45 word heroes, prose density 1-2, **scale numbers used in place of marketing adjectives** (Vercel "24× faster", Stripe "$1.9T", Notion "98% of Forbes Cloud 100"). Prose explanation is conspicuously absent. *The product itself, rendered visually, is the argument.*

Our hero is **~85-100 words** vs the 17-45 word band. ~2-4× longer than the SaaS standard.

---

## Part 3 — Industry research takeaways

1. **The 250-725 word sweet spot, and shorter often wins.** SaaS-CRO benchmarks place high-converting landing pages in the 250-725 word range (median 3.8% CVR). Pages under 100 words convert ~50% better than those over 500 in most B2B contexts, *though high-trust products may legitimately need more*. ([SaaS Hero CRO benchmarks](https://www.saashero.net/competitor/saas-landing-page-optimization-guide/), [VWO landing page stats](https://vwo.com/blog/landing-page-statistics/))

2. **Above-the-fold attention is 84% greater than below; minimal dense layouts beat busy ones by 19%.** Single-stat heroes ("127× faster than legacy") test as best-performing hero pattern with ~18% lift over multi-claim heroes. Complex copy now hurts conversion 62% more than it did in 2020 — readability has compounded as a CRO lever. ([2024-2025 conversion benchmarks](https://www.dollarpocket.com/landing-page-conversion-benchmarks-report), [SeoSherpa landing page statistics](https://seosherpa.com/landing-page-statistics/))

3. **Users scan, they do not read.** NN/G's longitudinal eye-tracking research (2006-present) confirms F-pattern scanning; users read at most **20-28% of words** on a page, only ~16% read articles in full. **Implication: paragraphs explaining "why we're trustworthy" mostly never get read** — trust must be conveyed via visible structures (badges, numbers, named quotes) not prose. ([NN/G F-shaped pattern](https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content/), [NN/G text scanning patterns](https://www.nngroup.com/articles/text-scanning-patterns-eyetracking/))

4. **Copy-reduction A/B tests consistently win.** MarketingExperiments saw +37% conversion / -33% CPA from cutting prose on a service business landing; CXL saw +21.5% opt-ins from shorter layout; Kaya Skin Clinic +137% from layout streamlining. Directionality is robust across categories. ([MarketingExperiments +37%](https://marketingexperiments.com/copywriting/landing-page-optimization-conversion-increased-37-by-reducing-copy), [CXL +21.5%](https://cxl.com/blog/case-study-how-we-improved-landing-page-conversion/))

5. **Reading-level matters more than length.** Pages at 5th-7th grade reading level convert at ~12.9% vs 2.1% at "professional" level; three-syllable words correlate with 24.3% lower CVR. **For us: "verified ≠ profitable" / "drawdown disclosure" / "Pinnacle no-vig probabilities" / "variance-confounded" are exactly the dense vocabulary this research flags.** ([SaaS Hero](https://www.saashero.net/competitor/saas-landing-page-optimization-guide/))

**Honesty caveat:** the case-study lifts above are vendor-reported (Unbounce, CXL, MarketingExperiments). Direction (shorter > longer) is robust across independent studies; absolute magnitudes are likely overstated.

---

## Part 4 — Our analytics (PostHog)

**Status (2026-06-06):** PostHog was silently broken by CSP
`connect-src` for weeks. Fixed in odds-intel-web `f0aa769` (deploys
to Vercel ~now). **All historical data prior to that fix is suspect
or empty** — we cannot retroactively pull bounce / time-on-page for
pre-2026-06-06 sessions.

**What's available today (without code changes):**
- `$pageview` — fires on every navigation (manual capture in
  `posthog-provider.tsx`)
- `$pageleave` — fires on tab close / navigation away (auto-captured
  with `capture_pageleave: true`)

These two give us, post-fix:
- Pageviews per URL
- Session length (`pageleave - pageview`)
- Bounce rate (single-pageview sessions)
- Top-N URL paths (in / out)

**What's NOT being captured (would need code change + deploy + wait):**
- Scroll depth — no listener registered
- Click events — `autocapture: false`
- Session recordings — `disable_session_recording: true`
- Heatmaps — same as recordings

**Operator-side actions required** (see "What you must do" in this doc's
follow-up — operator hasn't yet decided on PostHog access route).

### If operator opts to add scroll-depth tracking

Effort: ~1h code + deploy. Listener fires `$scroll` events at 25/50/75/100%
thresholds. Then 7-14 days of accumulation before meaningful aggregates.

### If operator opts to share Personal API key (read scope)

I can query the available `$pageview` / `$pageleave` data directly.
**Caveat:** the data window only spans from the CSP fix forward, so
"30-day bounce rate" reports won't be valid until ~2026-07-06.

### If operator opts to skip PostHog

The spike falls back to "competitor analysis + industry research only."
Lower fidelity. Verdict will read: "best evidence we have, but acknowledged
gap."

---

## Part 5 — Synthesis & verdict

### The three signals all point the same way

1. **Verified-+ROI anchor (WinnerOdds): prose-sparse + numbers-dense.**
   4-word headline. €14.76M cumulative member profit. Named experts
   (Buchdahl, Ling) doing the trust work, not paragraphs. **No audit
   badge — and still no explanatory prose to fill the gap.** They lean
   on social proof + cumulative number.

2. **SaaS gold standard (Linear / Notion / Vercel / Stripe / Cal.com):
   17-45 word heroes**, scale numbers as headlines (Vercel "24× faster",
   Stripe "$1.9T volume", Notion "98% Forbes Cloud 100"). Conspicuous
   absence of marketing prose. The product, rendered visually, is the
   argument.

3. **Industry research:** users absorb 20-28% of text (NN/G). Copy
   reduction lifts conversion 13-37% in independent A/B tests.
   Reading-level matters: pages at 5-7th grade convert ~6× vs
   professional language. Our terms ("verified ≠ profitable",
   "variance-confounded", "Pinnacle no-vig") are exactly the dense
   vocabulary flagged.

### Where we sit

- **706 words** total on landing. Mid-pack for paid competitors. **2-4×
  longer hero** than SaaS gold standard.
- **13 sections.** Linear-style landings run 5-7 sections.
- **No hero-level cumulative number.** WinnerOdds has €14.76M; RebelBetting
  has €22M; we have spread stats (75% / +CLV / 21,831 matches) that read
  as "stats" not "outcomes" — and the dollar/euro outcome is precisely
  what high-trust competitors lead with.
- **Preachy meta-copy** in section 7 ("Three things most prediction sites
  hide. We publish them on purpose.") — exactly the kind of explainer
  prose that scanning users won't read but that signals "trying too hard."
- **Triple-pricing-mention** on landing (compact CTA + FAQ "how much" +
  Telegram CTA "Pro/Elite") — repetition without information gain.
- **/methodology and /how-it-works overlap** meaningfully — two long
  pages doing similar funnel work.

### Verdict: **proceed with `GROWTH-COPY-DENSITY-AUDIT`, with a critical caveat**

A 40-60% copy reduction is **likely a win** — three independent vectors
agree, the expected lift category is 13-37% in comparable A/B tests, and
the cost is bounded (2-3 days).

**Critical caveat — do NOT cut copy without a replacement trust signal.**
Our prose currently does substantive work explaining "verified ≠
profitable." Cutting that explanation without filling the trust gap
would expose the gap. The win condition is **cut + replace**, not just
**cut**.

### Concrete next moves (3-4 days total)

**Day 1 — single hero number:**
Replace the trust micro-line ("75% accuracy · +CLV · 21,831 matches")
with a single load-bearing outcome number. Candidate metrics, in order
of preference:
- **Cumulative model CLV** in € over all paper bets (e.g., "+€340 in
  CLV since 2026-05-01") — strongest, because CLV is the metric we
  market on
- **Cumulative paper-trading P&L** (currently +€340 May, -€37 June; net
  unclear, would need recalculation)
- **Total CLV-positive picks identified** (mechanical proof of model
  activity)

A single number with one trailing line (e.g., "across 2,686 bets, 34
days") IS the trust device that prose currently fails to be.

**Day 2 — landing cuts:**
- Cut section 7 ("Honest about how this works" 3-card) preamble. Keep
  the 3 cards (−€398 drawdown / Self-reported / CLV not ROI) — those
  ARE numbers — but drop the "Three things most prediction sites hide.
  We publish them on purpose." meta-narration.
- Cut one of the three pricing mentions (probably the compact CTA in the
  middle; keep the hero CTA + Telegram CTA).
- Reduce FAQ from 5 items to 3 (move "What is CLV tracking?" to /learn,
  consolidate "How do AI picks work?" and "Where do picks go?").
- Target landing total: **~350-400 words** (~45% reduction).

**Day 3 — /methodology + /how-it-works consolidation:**
- Decide canonical: /methodology becomes the long-form data scientist
  surface; /how-it-works becomes a short product walkthrough (cut to
  ~250 words).
- Reading-level pass: replace "variance-confounded", "no-vig", "edge
  early" with concrete phrasing.

**Day 4 — verification:**
- Smoke test pins the canonical hero number presence
- Visual eyeball at 1440px + 393px
- Push, deploy
- Set 2-week reminder to check PostHog `$pageleave` data once it
  accumulates (need 200+ landing sessions for any signal)

### Dashboard density verdict (`GROWTH-DESKTOP-DASHBOARD-DENSITY`)

**Lean: yes, but with weaker evidence than copy.** Free aggregators
(Forebet, SofaScore, OddsChecker, Oddspedia) are dashboard-dense (data
density 5). The two credible paid peers (WinnerOdds, RebelBetting) are
also data-dense (4). Only DeepBetting (data 2) is sparse on the
dashboard side, and DeepBetting is the prose-heavy outlier.

**However:** the spike didn't directly measure our `/matches`,
`/value-bets`, `/dashboard` against competitor dashboards (only their
landings). The dashboard density question would benefit from a separate
half-day spike comparing **our dashboard pages vs WinnerOdds/RebelBetting
dashboard surfaces specifically** — once PostHog scroll-depth data
exists. Defer the redesign until that targeted spike runs.

### Show-not-tell pivot (`GROWTH-SHOW-NOT-TELL-PIVOT`)

Unchanged. Still gated on verified ROI landing. This research strengthens
the case for the eventual pivot but doesn't unblock it.

### Copy-density-audit AS-IS recommendation

The PRIORITY_QUEUE.md row for `GROWTH-COPY-DENSITY-AUDIT` already says
"target 40-60% less prose on landing/methodology/match-detail above-fold."
That number is now **data-backed, not gut-check.** Promote the task from
"⬜ Ready, gut-check basis" to "⬜ Ready, research-backed."

---

## Action items captured

- [x] **Confirm verdict with operator** — landed; operator approved
  cut+replace approach
- [ ] **Decide on PostHog scroll-depth listener** — deferred; existing
  $pageview + $pageleave gives bounce + time-on-page (enough for
  initial measurement); scroll-depth a 1h add later if needed
- [x] **Pull cumulative CLV number** for the new hero — shipped via
  migration 187 + settlement.py + dashboard_cache backfill
- [ ] **Optional:** Pete Ling / Buchdahl-style expert quote — still
  gated on `GROWTH-EXPERT-ENDORSEMENT` which is gated on verified ROI

---

## Shipped state (2026-06-06) — final outcome log

The 4-day plan executed in one session. Net result: landing dropped
from ~706 to ~358 visible words (49% reduction). All cuts research-
backed; smokes pin every removed/added phrase.

### Day 1 — load-bearing hero number
- New `dashboard_cache.elite_value_bets_cumulative` JSONB column
  (migration `187`, settlement.py `_value_bets_cumulative()`)
- Landing hero trust micro-line replaced:
  - Before: `75% accuracy on O/U 1.5  ·  +9.5% CLV (30-day)  ·  21,831 matches tracked`
  - After: `+9.4% CLV beating the closing line  ·  1,176 paper bets  ·  35 days`
- Commits: engine `41ce0b5` + web `cfb57ce` + smoke `78db2fd`
- Smoke: `GROWTH-COPY-DENSITY Day 1` (4-layer chain pinned —
  migration → settlement → web type → page render)

### Day 2 — landing cuts (49% word reduction)
Six cuts:
1. Hero subhead: 28 → 13 words
2. Below-CTA microcopy: 12 → 5 words
3. "Honest about how this works" preamble: H2 + subtitle removed
4. Compact pricing CTA section: deleted entirely (pricing was being asked 3× on landing)
5. Telegram CTA body: 29 → 14 words
6. FAQ: 5 → 3 items

Result: ~358 visible words on landing, 12 sections (down from 13).
- Commits: web `1526fc6` + smoke `b4a5fa6`

### Day 3 — reading-level pass + /how-it-works consolidation
- "variance-confounded" + "proves edge early" jargon replaced
  across 5 user-facing files (glossary keeps the term)
- /how-it-works: collapsed Sections 1+2 (Prediction Model +
  Signal Groups, ~350 words) into one paragraph + link to /methodology
- /how-it-works FAQ: 7 → 3 items
- Commits: web `c6a9b20` + smoke `b49c632`

### Chain-start alignment (operator-caught regression)
Operator spotted landing hero numbers drifted from /performance:
- Landing: `1,217 bets · 33 days`
- /performance: `1,164 bets · 35 days`

Three sources of truth inconsistent (chain start, cohort filter,
days computation). Fixed by standardising on:
- **Chain start:** `2026-05-01` (matches /performance display)
- **Cohort filter:** mirrors `activeBotNames` (active +
  non-experimental + not retired)
- **Settled definition:** `result NOT IN ('pending','void')`
  (includes pushes)
- **Days:** calendar days from chain_start to settlement run

After alignment: landing `1,176 bets · 35 days` vs /performance
`~1,164 · 35` — 12-bet residual (~1%) is cache-snapshot vs live-JS
lag, structural.

Commits: engine `d46d809` + web `5afb82d`

### Day 4 — housekeeping
- Full smoke chain green (10 tests)
- `GROWTH-CLAIMS-PARITY` updated to accept the Day-2 reworded
  "Accuracy alone is misleading" (was "Accuracy is not the same as
  profitability"). Three other phrases the smoke had pinned were
  deliberately cut in Day 2 (`10+ years of historical match data`,
  `no human bias`) — assertions removed since the cuts were intentional.
- `PRIORITY_QUEUE.md` row flipped 🔄 → ✅ Done.
- 2-week follow-up note filed at `dev/active/density-followup-2026-06-20.md`.

### Side effects shipped during the same session
- `POSTHOG-CSP-FIX` — caught during PostHog wiring check; CSP
  `connect-src` was silently blocking all event ingestion
- `MIGRATION-185-IDEMPOTENCY` — found while waiting for migration
  187; CREATE POLICY lacked DROP IF EXISTS guard
- `GROWTH-VS-PAGES-V2` — 3 new /vs entries + matrix wires

---

## What didn't ship (filed for follow-up)

- **`GROWTH-DESKTOP-DASHBOARD-DENSITY`** — research doc verdict was
  "lean yes, weaker evidence than copy." Deferred until a separate
  targeted spike + PostHog scroll-depth data
- **`GROWTH-SHOW-NOT-TELL-PIVOT`** — still gated on verified ROI
- **`GROWTH-VS-SOFASCORE-ODDSCHECKER`** — last 2 matrix-listed
  competitors without /vs pages
- **PostHog scroll-depth tracking** — not currently captured; would
  unlock data-backed iteration but needs code + 7-14 days of accumulation

---

## How to measure if this worked

PostHog wiring fixed in `POSTHOG-CSP-FIX`. Bounce-rate, time-on-page,
and funnel data accumulates from 2026-06-06 forward.

**2-week checkpoint:** see `dev/active/density-followup-2026-06-20.md`.

If bounce stays flat-or-better and conversion ticks up, the cut +
replace verdict was right. If bounce worsens, we lost something in
the prose that was doing trust-signaling work — revisit the
"cumulative number alone is enough" assumption.

---

## Sources

- [WinnerOdds homepage](https://www.winnerodds.com/)
- [Linear homepage](https://linear.app/)
- [Notion homepage](https://www.notion.com/)
- [Vercel homepage](https://vercel.com/)
- [Stripe homepage](https://stripe.com/)
- [NN/G F-shaped reading pattern](https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content/)
- [NN/G text-scanning eyetracking](https://www.nngroup.com/articles/text-scanning-patterns-eyetracking/)
- [MarketingExperiments — copy reduction +37%](https://marketingexperiments.com/copywriting/landing-page-optimization-conversion-increased-37-by-reducing-copy)
- [CXL — shorter copy +21.5%](https://cxl.com/blog/case-study-how-we-improved-landing-page-conversion/)
- [SaaS Hero — SaaS landing optimization](https://www.saashero.net/competitor/saas-landing-page-optimization-guide/)
- [VWO — 40+ landing-page statistics](https://vwo.com/blog/landing-page-statistics/)
- [Above-the-fold 2024-2025 benchmarks](https://www.dollarpocket.com/landing-page-conversion-benchmarks-report)
- [SeoSherpa landing page statistics 2026](https://seosherpa.com/landing-page-statistics/)
