# OddsIntel — Tier Access Matrix

Access levels for anonymous visitors, free signed-in users, and paid subscribers.

## Tier Overview

| Tier | Price | Internal Name | Description |
|------|-------|--------------|-------------|
| Anonymous | Free | — | No account, browsing only |
| Free | €0/mo | `scout` | Signed-in, personalization + tools |
| Pro | €19/mo | `analyst` | Deep match intelligence |
| Elite | €49/mo | `sharp` | AI picks + track record |

---

## Feature Matrix

### Match Browsing & Data

| Feature | Anonymous | Free (Scout) | Pro (Analyst) | Elite (Sharp) |
|---------|:---------:|:------------:|:-------------:|:-------------:|
| Browse today's matches | Y | Y | Y | Y |
| Best odds (1 bookmaker) | Y | Y | Y | Y |
| H2H records & recent meetings | Y | Y | Y | Y |
| League standings & team form | Y | Y | Y | Y |
| Live scores (auto-refresh) | Y | Y | Y | Y |
| Venue & referee info | Y | Y | Y | Y |
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

| Feature | Anonymous | Free (Scout) | Pro (Analyst) | Elite (Sharp) |
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

### AI & Analytics

| Feature | Anonymous | Free (Scout) | Pro (Analyst) | Elite (Sharp) |
|---------|:---------:|:------------:|:-------------:|:-------------:|
| Value bets page (all AI picks) | — | — | — | Y |
| Model probability + edge % | — | — | — | Y |
| CLV tracking (closing line value) | — | — | — | Y |
| Full track record & ROI analytics | — | — | — | Y |
| Bot-validated strategy results | — | — | — | Y |

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

---

## Route Protection

| Route | Access |
|-------|--------|
| `/` | Public (landing page) |
| `/matches` | Public |
| `/matches/[id]` | Public (pro sections gated in UI) |
| `/login`, `/signup` | Public |
| `/my-picks` | Authenticated |
| `/profile` | Authenticated |
| `/value-bets` | Authenticated + `sharp` tier (TierGate) |
| `/track-record` | Authenticated + `sharp` tier (TierGate) |

---

## Implementation Status

- [x] Favorite teams & leagues + "My Matches" tab
- [x] Prediction tracker (pick button + /my-picks dashboard)
- [x] Daily value bet teaser (1 free unlock/day)
- [x] Match notes (auto-save on match detail)
- [x] Community sentiment voting (1X2 poll)
- [x] Saved matches DB schema (frontend TBD)
- [x] Updated signup page with new free tier perks
- [x] Updated landing page pricing section
- [x] SQL migration for all new tables
- [ ] Match alerts & notifications (email/push)
- [ ] Weekly performance summary email
- [ ] Dark mode / theme persistence toggle
- [ ] Stripe integration for paid tier upgrades
