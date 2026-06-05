# Lighthouse / PageSpeed Benchmark

**Owner:** OddsIntel
**Last code review:** 2026-06-05
**Status:** Code-level analysis complete; live PageSpeed scores pending operator runs (anonymous API rate-limit prevented automated capture).

---

## 1. How to capture scores

Open each URL below at <https://pagespeed.web.dev/>. Each test runs Lighthouse on Google's datacenter so the numbers are reproducible — **don't run Lighthouse locally**, your network + CPU skew the result.

Click "Analyze" and copy the **Mobile** scores for each of the 4 categories (Performance, Accessibility, Best Practices, SEO). Optionally also capture **Desktop**, but mobile is the one that matters — Google ranks on mobile-first indexing.

| Page | URL | Why test |
|------|-----|----------|
| Landing | https://oddsintel.app/ | The conversion funnel front door. Hero + competitor matrix + one-screen-proof = the heaviest mix of components. |
| Matches | https://oddsintel.app/matches | The free-tier killer feature. Heavy data table with crests + odds. |
| Value Bets | https://oddsintel.app/value-bets | Pro-tier paywall surface. Server-side gating means SSR latency matters. |
| Pricing | https://oddsintel.app/pricing | Decision page; if this is slow the conversion drops. |
| Live | https://oddsintel.app/live | Live-updating ticker; client-side fetch frequency could blow TBT. |
| Accuracy | https://oddsintel.app/accuracy | Stats charts; heavy on calculations + numbers. |
| /vs hub | https://oddsintel.app/vs | Competitor-comparison landing; tested for SEO surface. |
| World Cup | https://oddsintel.app/world-cup | High-engagement microsite; bracket + AI ghosts = heaviest interactive page. |

Optionally pick **one fixture page** for a programmatic-SEO sample:
- Predictions: https://oddsintel.app/predictions/premier-league
- Fixture page: any URL from `/predictions/[league]/[fixture]` after visiting the league hub

Paste the results into the matrix below (replace the `?` placeholders).

---

## 2. Score matrix

Lighthouse categories scored 0-100. Google's thresholds: **≥90 good**, **75–89 needs improvement**, **<75 poor**.

| Page (Mobile) | Performance | Accessibility | Best Practices | SEO |
|---|---|---|---|---|
| / | ? | ? | ? | ? |
| /matches | ? | ? | ? | ? |
| /value-bets | ? | ? | ? | ? |
| /pricing | ? | ? | ? | ? |
| /live | ? | ? | ? | ? |
| /accuracy | ? | ? | ? | ? |
| /vs | ? | ? | ? | ? |
| /world-cup | ? | ? | ? | ? |
| /predictions/premier-league | ? | ? | ? | ? |

### Reference competitors (run if comparison context matters)

| Site | Performance (mobile) | Notes |
|------|----------------------|-------|
| sofascore.com | reportedly 85–95 | The free-stats benchmark; we are not in their CDN league but their depth-vs-our-honesty positioning still matters. |
| winnerodds.com | ? | Picks-only product; lighter page weight expected. |
| inplayguru.com | ? | EE-heavy traffic, lightweight UI. |
| deepbetting.io | ? | Verified-ROI competitor; similar product surface to us. |
| forebet.com | ? | The programmatic-SEO competitor we now mirror; their score is the bar we need to beat for Google to prefer our pages. |

---

## 3. Code-level analysis (what was reviewed, what to expect)

Reviewed without running Lighthouse, using the patterns in the codebase. **These are predictions, not actual scores** — confirm via PageSpeed runs above.

### Performance — likely 75–85 mobile

**Positives in our code:**
- Next 15 App Router with SSR + ISR (every page revalidates on a sensible window, none are fully dynamic per request).
- `next/font/google` Inter + JetBrains_Mono with built-in font-display:swap (no FOIT).
- 112KB total public/ assets (mostly favicons + PWA icons). Tiny.
- Only 7 `"use client"` components in `src/app/` — the vast majority are server-rendered.
- Edge / CDN delivery via Vercel.
- Tailwind purged to ~25KB CSS (typical for our component count).

**Likely drag points (where the score will lose points):**
1. **Third-party JS** — Sentry (`@sentry/nextjs`) + PostHog (`posthog-js`) + Vercel Analytics. Combined ~80-150ms TBT cost. Sentry alone is ~50KB gzipped.
2. **External badge `<img>` tags** on the landing page (twelve.tools, wired.business, aiboom.tools). 3 SVGs cross-origin; can't be Next/Image-optimized without rehosting them. Mild LCP risk if they're above the fold (they're not — footer area).
3. **Match-detail + value-bets** pages fetch a lot of joined data server-side. SSR latency could push TTFB > 600ms in cold start, costing 5-10 perf points.
4. **PostHog autocapture** — by default captures every click, scroll, etc. Inflates JS execution time. Worth checking if autocapture is selectively disabled.

### Accessibility — likely 90–100

**Positives:**
- Most icons have `aria-hidden` and labels.
- Buttons use the `<button>` element with explicit text.
- Lucide icons throughout — accessible by default.
- Color contrast on the dark theme tested visually; no obvious low-contrast text.

**Possible drag points:**
- Some tap targets are still below 44px even after GROWTH-MOBILE-P1-BATCH (modal close at ~32px, button xs at ~32px mobile).
- Color contrast on `text-muted-foreground` chains may dip below 4.5:1 against `bg-card/40`. Worth checking via the Lighthouse report's color-contrast audit.
- `<select>` triggers using Radix should be fine; verify on /value-bets.

### Best Practices — likely 90–100

**Positives:**
- HTTPS everywhere.
- No console errors expected in production builds (Sentry catches them anyway).
- Modern image formats where possible (SVG + small PNG).

**Possible drag points:**
- 3rd-party cookies from PostHog + Stripe checkout may show up.

### SEO — likely 90–100

**Positives:**
- robots.txt and sitemap.xml both auto-generated.
- Every page has metadata + canonical + OG title/description.
- JSON-LD structured data on per-fixture pages (Phase 1 of GROWTH-SEO-CONTENT-ENGINE) + match detail pages.
- Mobile viewport meta tag present (Next 15 default).

**Possible drag points:**
- Some `/learn/[slug]` glossary pages may lack OG images (text-only previews on social shares).
- /world-cup deep pages haven't been audited for canonical correctness.

---

## 4. Top 3 actionable fixes (predicted, refine after live scores land)

Pick whichever applies to a page that scores <80 Performance.

### Fix 1 — Defer PostHog initialization to first user interaction

Currently `PostHogProvider` initializes in `app/layout.tsx`, which runs on every page. If autocapture is enabled, that's 80-150ms of TBT on every navigation. Defer-on-interaction (init only after first click/scroll/keypress) cuts this to zero for bounce visitors and only pays the cost for engaged users.

Reference: `src/components/posthog-provider.tsx`.

### Fix 2 — Convert team-logo `<img>` to Next/Image

`src/components/league-accordion.tsx` line 73 renders team crests via `/api/logo?url=...&w=20` proxy. Wrapping in `next/image` with sizes + priority hints would add lazy/intersection-observer loading at the framework level. The proxy already does the resizing, so the only gain is automatic LCP candidate detection. Worth ~3-5 perf points on /matches and /predictions pages.

### Fix 3 — Pre-cache the most common Supabase queries

Pages like `/matches` and `/value-bets` SSR-fetch joined data per request. ISR helps but cold-cache miss can push TTFB to 800ms+. Add an explicit `unstable_cache` wrapper around `getPublicMatches()` / `getValueBets()` with 60-300s revalidate. Saves 10-15 perf points on bot crawls (Lighthouse always lands a cold cache).

---

## 5. Re-run schedule

- After any large refactor (e.g. a layout change, a new third-party script, an image format swap), re-run the matrix.
- Operator should re-run quarterly even without changes — Lighthouse audits update.
- If a page drops below 75 Performance, add to the next priority sprint.

---

## 6. Connection to the engine queue

Filed as `GROWTH-LIGHTHOUSE-BENCHMARK` in `PRIORITY_QUEUE.md`. This doc is the deliverable. Status updates when:
- Operator captures live scores → updates the matrix
- Top 3 fixes are validated as accurate via the matrix → can convert each into its own queue task
