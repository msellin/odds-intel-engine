# TELEGRAM-SIGNUP-FUNNEL-2026-08-19

## The question

Telegram channel (@oddsintelpicks, 24 subs, growing organically) already
delivers the picks. What can we offer that makes a subscriber sign up on
the website? Sign-up unlocks retention, attribution, and future
monetisation — but only if the hook is worth clicking.

## Verdict

**Yes to the funnel. No to using shadow bots as the hook** — they're
unproven and could damage trust if they lose over the first month.

Instead, drop three existing higher-tier features to sign-in-free tier.
All three are things Telegram fundamentally can't deliver.

## The three hooks

### 1. Live in-play picks feed (killer feature)
- Real-time picks + odds updating during matches
- Telegram can't do this — a broadcast channel would spam with hundreds
  of updates per Saturday
- `/live` page already exists (FE-LIVE), currently Pro-tier gated
- Change: gate from `isPro` → signed-in

### 2. AI-explained picks
- "Why did the model like this pick" — Gemini-generated 300-500w rationale
- Telegram messages max out at ~200 chars, natural fit for site
- `/api/bet-explain` already exists, currently Elite-only
- Change: gate from `isElite` → signed-in

### 3. Personal pick tracker ("My Picks")
- Mark picks as "I bet this / I skipped this" → your personal ROI
- Telegram doesn't know who placed what
- Zero risk of being blamed for losses (user chose)
- High stickiness + gold data on which picks convert to real bets
- Partial infrastructure exists via `_settle_user_picks` in
  workers/jobs/settlement.py
- Change: expose a "My Picks" tab, wire the tracker UI

## Why NOT use shadow bots as the hook

- `bot_sweep_1x2_home_v1`, `bot_sweep_1x2_draw_v1`, `bot_sweep_btts_yes_v1`
  are literally labelled experimental — no settled data yet
- In-sample backtest signal could evaporate on fresh data (that's the
  whole reason they're in Phase 1 shadow observation)
- Tier 2-3 markets are hard to actually bet at the shown price (soft
  book stake caps)
- Serving these as "signed-up exclusive picks" and they go -8% over 4
  weeks = signed-up users feel scammed. Sign-up incentive turns into
  churn engine.
- Preserve shadow bots for internal validation only. Revisit if any of
  them promote to paper beta (MODEL-EVIDENCE-CHECKPOINT-2026-11-01).

## Tiered ladder (proposed)

- **Anonymous** (Telegram click-through): today's picks list, no history,
  no in-play
- **Signed-in (free)**: full pick list + live in-play + AI explanations +
  personal tracker + match detail deep-dive
- **Paid Pro (future)**: push notifications matching custom filter,
  portfolio simulator, real-time odds-movement alerts, priority alerts
  before market close, tighter/faster inplay coverage

## The Telegram message that would actually convert

Don't oversell. Be specific and honest:

> **New: free accounts get live in-play picks + AI reasoning +
> personal bet tracker.**
> Telegram keeps the daily picks. The site adds the depth.
> oddsintel.app/signup

## Tradeoff to accept

Every feature dropped to "free signed-in" is a feature you can't
charge for later. Keep the *notification/alerts/portfolio* layer for
the future paid tier. Tracker + AI + inplay is enough to move people
off Telegram.

## Cheap first step (~30 min)

1. Change tier gate on `/live` from `isPro` → `isSignedIn`
2. Change tier gate on `/api/bet-explain` from `isElite` → `isSignedIn`
3. Add "Live picks" nav item, hidden for anonymous
4. Post the Telegram message above
5. Watch signup rate + `/live` traffic in PostHog over the following
   week — decides whether to invest in phase 2 (personal tracker UI)

Phase 2 (~3-4h) if phase 1 gains traction: build the "My Picks"
tracker UI on top of the existing user_picks / _settle_user_picks
plumbing.

## What NOT to build until we see phase-1 signal

- Custom notification prefs (paid-tier future)
- Portfolio simulator (paid-tier future)
- Alerts / SMS / push (paid-tier future)
- New shadow / experimental bot content (unvalidated)
