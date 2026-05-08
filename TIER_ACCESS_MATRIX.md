# OddsIntel — Tier Access Matrix

Access levels for anonymous visitors, free signed-in users, and paid subscribers.

## Tier Overview

| Tier | Monthly | Annual | Founding Rate | DB Value | Description |
|------|---------|--------|---------------|----------|-------------|
| Anonymous | Free | — | — | — | No account, browsing only |
| Free | €0 | — | — | `free` | Signed-in, personalization + tools |
| Pro | €4.99 | €39.99/yr (€3.33/mo) | €3.99/mo (first 500) | `pro` | Deep match intelligence |
| Elite | €14.99 | €119.99/yr (€9.99/mo) | €9.99/mo (first 200) | `elite` | AI picks + track record |

### Pricing Strategy
- **Stage:** Early launch — optimizing for user acquisition, not ARPU
- **No free trial** — free tier IS the trial
- **Founding member rates** locked forever for first 500 Pro / 200 Elite subscribers
- **Price raise triggers:** Pro → €7.99 at 2K paid users; Elite → €24.99 at 6mo proven ROI

---

## Feature Matrix

### Match Browsing & Data

| Feature | Anonymous | Free | Pro | Elite |
|---------|:---------:|:------------:|:-------------:|:-------------:|
| Browse today's matches | Y | Y | Y | Y |
| Best available odds (single best price across all bookmakers) | Y | Y | Y | Y |
| H2H records & recent meetings | Y | Y | Y | Y |
| League standings & team form | Y | Y | Y | Y |
| Live scores (auto-refresh) | Y | Y | Y | Y |
| Venue & referee info | Y | Y | Y | Y |
| Signal intelligence grade (A/B/D) + match pulse (⚡/🔥/—) | Y | Y | Y | Y |
| Signal teasers on notable matches (2 hooks, no numbers) | Y | Y | Y | Y |
| Full odds comparison (13 bookmakers) | — | — | Y | Y |
| Odds movement chart (pre-match) | — | — | Y | Y |
| Match events timeline | — | — | Y | Y |
| Confirmed lineups + formation view | — | — | Y | Y |
| AI injury & suspension alerts | — | — | Y | Y |
| Team season stats (xG, clean sheets) | — | — | Y | Y |
| Post-match stats (shots, possession) | — | — | Y | Y |
| HT vs FT comparison | — | — | Y | Y |
| Player ratings | — | — | Y | Y |

### Personalization & Tools (Free Account Features)

| Feature | Anonymous | Free | Pro | Elite |
|---------|:---------:|:------------:|:-------------:|:-------------:|
| Favorite teams (star toggle) | — | Y | Y | Y |
| Favorite leagues (star toggle) | — | Y | Y | Y |
| "My Matches" filtered view | — | Y | Y | Y |
| Prediction tracker (log picks) | — | Y | Y | Y |
| Pick stats (hit rate, W/L, streak) | — | Y | Y | Y |
| Match notes (private journal) | — | Y | Y | Y |
| Community prediction voting | — | Y | Y | Y |
| Daily free AI value pick (1/day) | — | Y | Y | Y |
| Saved matches / watchlist | — | Y | Y | Y |
| Profile & preferences persistence | — | Y | Y | Y |
| Odds format preference (dec/frac/us) | — | Y | Y | Y |

### Signal Intelligence

| Feature | Anonymous | Free | Pro | Elite |
|---------|:---------:|:------------:|:-------------:|:-------------:|
| Signal intelligence grade (A/B/D) + match pulse | Y | Y | Y | Y |
| Signal teasers (1-2 hooks per notable match) | Y | Y | Y | Y |
| Intelligence Summary (top 3-5 signals, plain English) | — | 1 signal teaser + upgrade CTA | Full (all groups) | Full |
| Signal group accordion (Market, Form, Context, Injuries) | — | — | Y | Y |

### AI & Analytics

| Feature | Anonymous | Free | Pro | Elite |
|---------|:---------:|:------------:|:-------------:|:-------------:|
| Track record — hero (CLV avg, value bets count, coverage) | Y | Y | Y | Y |
| Track record — CLV education + system status + progress bar | Y | Y | Y | Y |
| Track record — early results (contextualized, collapsible) | Y | Y | Y | Y |
| Track record — prediction history (limited 20 rows, basic columns) | Y | Y | Y | Y |
| Track record — prediction history (full + best odds + CLV column) | — | — | Y | Y |
| Track record — prediction history (+ edge % column) | — | — | — | Y |
| Track record — feature comparison table | Y | Y | Y | — |
| Track record — today's picks (match + pick + confidence) | Y | Y | Y | Y |
| Track record — today's picks (+ best odds revealed) | — | — | Y | Y |
| Track record — today's picks (+ Kelly stake revealed) | — | — | — | Y |
| Value bets page — stats only (bet count, edge tiers, leagues) | — | Y | Y | Y |
| Value bets page — directional (match + market + edge tier, no selection) | — | — | Y | — |
| Value bets page — full (match + selection + odds + model prob + exact edge % + stake) | — | — | — | Y |
| Value bets page — **prematch bots only** (inplay hidden until tuning is done — see INPLAY-HIDE-VALUEBETS). Performance page is the place for inplay history; it tags inplay rows with a `live` badge. | — | Prematch only | Prematch only | Prematch only |
| Model probability + edge % per match | — | — | — | Y |
| CLV tracking per bet (Pro on track record, Elite on match detail) | — | — | Y | Y |
| Full bot ROI analytics + strategy breakdown | — | — | — | Y |
| Bankroll analytics dashboard (/bankroll) — cumulative chart, drawdown, ROI, CLV, model benchmark, per-league | — | — | — | Y |

### Engagement & Social Proof (ENG-* tasks, docs/ENGAGEMENT_PLAYBOOK.md)

| Feature | Anonymous | Free | Pro | Elite |
|---------|:---------:|:------------:|:-------------:|:-------------:|
| "X analyzing this match" counter (ENG-1) | Y | Y | Y | Y |
| Community vote split display (ENG-2) | Read-only | Vote + read | Vote + read | Vote + read |
| AI match preview (ENG-3) | Y (top 3) | Y (top 3) | Y (all) | Y (all + signals) |
| Daily email digest (ENG-4) | — | Previews + CTA | + value bet count + alerts | + full picks |
| Betting glossary /learn/[term] (ENG-5) | Y | Y | Y | Y |
| Bot consensus display (ENG-6) | Count only | Count only | + which markets | + full breakdown |
| Methodology page (ENG-7) | Y | Y | Y | Y |
| Watchlist signal alerts (ENG-8) | — | Kickoff reminders | Signal change alerts | Custom conditions |
| Personal bet tracker (ENG-9) | — | 10 bets/mo, W/L | Unlimited + ROI + CLV | + per-league + Model vs You |
| Weekly performance email (ENG-10) | — | Model stats | + personal stats | + per-league + CLV |
| "What Changed Today" widget (ENG-11) | Headlines | Headlines | Signal details | Edge impact |
| Model vs Market vs Users (ENG-12) | Bars only | Bars only | + percentages | + historical accuracy |
| Shareable pick cards (ENG-13) | Y | Y | Y | Y (+ edge %) |
| Weekly prediction pages (ENG-14) | Match + score | Match + score | + odds + confidence | + edge + stake |
| Market inefficiency index (ENG-15) | — | Labels only | + edge %s | + per-market |
| "Ask AI" expanded chat (ENG-16) | — | 3 Q/day | 20 Q/day | Unlimited |

---

## Conversion Hooks

The free tier features are designed to drive signups and eventual paid conversion:

| Free Feature | Conversion Hook |
|-------------|-----------------|
| Favorites + My Matches | Creates daily habit, makes upsells contextual ("upgrade for stats on your team") |
| Prediction tracker | Builds switching cost; "Your accuracy: 54% \| AI: 63% — upgrade" nudge |
| Daily value pick | Proves AI works; 1 free/day creates desire for all picks (Elite) |
| Community voting | Social proof + FOMO; "crowd says X, but sharp money says Y — upgrade" |
| Match notes | Emotional investment in platform; power user retention |

---

## Database Tables

### Existing
- `profiles` — user profile with `tier`, `preferred_leagues`, `preferred_markets`, `favorite_teams`
- `user_notification_settings` — notification preferences

### New (migration: `20260428_free_user_features.sql`)
- `user_picks` — prediction tracker (user_id, match_id, selection, odds, result)
- `saved_matches` — watchlist (user_id, match_id)
- `match_notes` — private notes per match (user_id, match_id, note_text)
- `match_votes` — community 1X2 vote (user_id, match_id, vote)
- `daily_unlocks` — 1 free value pick per day (user_id, unlock_date)

All new tables have RLS policies: users can only read/write their own data.
`match_votes` is an exception — all users can read all votes (for consensus display).

### Planned (Engagement & Growth)
- `match_previews` — AI-generated match previews (match_id, preview_text, generated_at) — ENG-3
- `match_view_counts` — rolling page view counter per match for "X analyzing" — ENG-1

---

## Route Protection

| Route | Access |
|-------|--------|
| `/` | Public (landing page) |
| `/matches` | Public |
| `/matches/[id]` | Public (pro sections gated in UI) |
| `/login`, `/signup` | Public |
| `/track-record` | Public (hero, CLV education, status, progress, early results, feature comparison). Pro: full history + CLV. Elite: + edge %. Bot P&L superadmin only |
| `/how-it-works` | Public |
| `/my-picks` | Authenticated (login modal if not signed in) |
| `/profile` | Authenticated |
| `/value-bets` | Authenticated (shows ValueBetsGate with modal for anon; TierGate for non-Elite) |
| `/learn/[term]` | Public (SEO glossary pages — ENG-5) |
| `/methodology` | Public (model explanation — ENG-7) |
| `/predictions/[league]/[week]` | Public (SEO prediction pages — ENG-14, tiered detail) |

---

## Implementation Status

- [x] Favorite teams & leagues + "My Matches" tab
- [x] Prediction tracker (pick button + /my-picks dashboard)
- [x] Daily value bet teaser (1 free unlock/day)
- [x] Match notes (auto-save on match detail)
- [x] Community sentiment voting (1X2 poll)
- [x] Saved matches DB schema (frontend TBD)
- [x] Updated landing page (full rewrite, 23 items)
- [x] SQL migration for all new tables (in odds-intel-engine/supabase/migrations/)
- [x] Track record public (model accuracy, no login required)
- [x] Login modal (replaces page redirects — openLoginModal() from anywhere)
- [x] Value bets gate (blurred preview + sign-in modal for anon users)
- [x] How it works page (/how-it-works — tier comparison, 58 signals, FAQ)
- [x] Profile page redesign (dynamic leagues, auto-save, quick-add)
- [x] Confidence tier filter on track record (All / Confident 50%+ / Strong 60%+)
- [x] Tooltips: odds, date, data coverage, interest score, edge %, match detail signals
- [ ] Match alerts & notifications (email/push)
- [ ] Weekly performance summary email
- [ ] Dark mode / theme persistence toggle
- [x] Stripe integration for paid tier upgrades (checkout + webhook + portal, profile upgrade buttons)
- [x] STRIPE_WEBHOOK_SECRET — configured in Vercel
- [x] Tier-aware data API (B3 — strip fields by tier in Next.js layer)
