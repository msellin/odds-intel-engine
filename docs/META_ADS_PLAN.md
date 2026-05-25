# OddsIntel — Meta Ads Plan

> Created 2026-05-08. Status: Ready to launch.

---

## Overview

- Platform: Facebook + Instagram (Meta Ads Manager)
- Total budget: €8/day (3 campaigns)
- Goal: Free signups → conversion to paid
- Launch trigger: Pixel installed + Meta Business set up

---

## Technical Setup Checklist

- [x] Meta Pixel component added to Next.js (`src/components/meta-pixel.tsx`)
- [x] Pixel loaded in root layout via `NEXT_PUBLIC_META_PIXEL_ID` env var
- [ ] Pixel ID obtained from Meta Events Manager
- [ ] `NEXT_PUBLIC_META_PIXEL_ID` added to Vercel environment variables
- [ ] Facebook Page created for OddsIntel
- [ ] Meta Business Account connected to Page
- [ ] Ad account created with payment method
- [ ] Pixel verified firing in Meta Events Manager

---

## Campaign Structure

| Campaign | Angle | Daily budget | Objective |
|----------|-------|-------------|-----------|
| A | "Beat the AI" — prediction tracker | €3/day | Traffic → Signup |
| B | Free daily pick | €3/day | Traffic → Homepage |
| C | Retargeting (visited but didn't sign up) | €2/day | Traffic → Signup |

Start all 3. After 5 days, kill whichever of A/B has lower CTR. Move its budget to the winner.

---

## Audience

### Campaigns A + B (cold audience)
- Age: 25–44, Men
- Locations: United Kingdom, Ireland, Germany, Netherlands
- Interests: Football, Soccer, UEFA Champions League, Premier League
- **Do NOT target:** Bet365, Betfair, William Hill — triggers gambling policy review

### Campaign C (retargeting)
- Custom Audience: Website visitors last 14 days who did NOT visit /signup or /welcome
- Requires Pixel to have been live for at least 3–5 days first

### Placements (all campaigns)
- Facebook Feed
- Instagram Feed
- Uncheck everything else (Stories, Reels, Audience Network)

---

## Ad Copy

### Campaign A — "Beat the AI"

**Ad A1 (curiosity hook):**
- Primary text: `Most football fans think they can predict matches better than an algorithm. So we built one to test that. Log your picks on any match — the AI logs its own. After the game, see who was right. Free forever.`
- Headline: `Does your gut beat an AI?`
- CTA: Sign Up
- URL: `https://oddsintel.app/signup`

**Ad A2 (challenge hook):**
- Primary text: `We built an AI that predicts football matches across Premier League, La Liga, Champions League and more. Log your own predictions against it. Who wins more? Find out free.`
- Headline: `You vs AI — who predicts better?`
- CTA: Sign Up
- URL: `https://oddsintel.app/signup`

### Campaign B — Free Daily Pick

**Ad B1:**
- Primary text: `Every day we publish one AI-powered football value pick. Premier League. La Liga. Champions League. Tracked publicly — wins and losses both. No signup needed to see it.`
- Headline: `Free AI football pick, every day`
- CTA: Learn More
- URL: `https://oddsintel.app`

**Ad B2 (anti-tipster angle):**
- Primary text: `Tired of tipsters cherry-picking their wins? We track everything — every prediction, every result, no hiding the losses. One free AI pick daily. Judge for yourself.`
- Headline: `No cherry-picking. Every pick tracked.`
- CTA: Learn More
- URL: `https://oddsintel.app`

### Campaign C — Retargeting

**Ad C1:**
- Primary text: `You checked out OddsIntel. The free tier has everything: match predictions, H2H records, odds comparison, AI daily pick. No card, no trial — just free.`
- Headline: `Still thinking about it? It's free.`
- CTA: Sign Up
- URL: `https://oddsintel.app/signup`

---

## Creative

In order of priority:
1. Screenshot of /matches page — dark UI, real product
2. Screenshot of match detail with signal grade
3. Shareable pick card (already built in the app)

No need for designed graphics to start. Real product screenshots outperform stock imagery for tools.

---

## Kill Criteria (check after 5 days)

| Metric | Pause ad if | Keep running if |
|--------|-------------|-----------------|
| CTR | < 0.8% | > 1.5% |
| Cost per click | > €1.50 | < €0.80 |
| Signups in 5 days | 0 after €20 spent | Any signup |

If zero signups after €30 total spend → pause all, rethink creative before spending more.

---

## What to Do in Meta Business (step by step)

See bottom of this doc — or follow the steps Claude gave in conversation on 2026-05-08.

### One-time setup
1. Go to business.facebook.com → Create account (or use existing)
2. Create a Facebook Page: "OddsIntel" (needed to run ads)
3. Business Settings → Ad Accounts → Create new ad account → add payment method
4. Events Manager → Connect Data Source → Web → Meta Pixel → name it "OddsIntel Pixel" → copy the Pixel ID
5. Add `NEXT_PUBLIC_META_PIXEL_ID=<your-pixel-id>` to Vercel environment variables (Production + Preview)
6. Redeploy (or it auto-deploys on next push)
7. Visit oddsintel.app, then check Events Manager → Test Events — confirm PageView fires

### Per campaign
- Ads Manager → Create → Traffic → name it → set budget → configure audience → create 2 ad variants → Publish
