# OddsIntel — Tier Access Matrix

> # ⛔ NOT A TASK LIST — the unchecked items below are retired, 2026-09-18
>
> Match alerts, the weekly summary email and the dark-mode toggle sit on a tier surface that
> `ROADMAP.md:172` itself records as **"Deprecated — no paid product right now"**, and the
> match-detail pages they attach to were deleted by PRODUCT-COLLAPSE on 2026-06-24. This doc still
> describes the tier *rules*, which remain correct for the surfaces that exist. It holds no backlog:
> `PRIORITY_QUEUE.md` is the single master task list.


> **⚠️ DEPRECATED 2026-06-24** — The tiered product surface that this document
> described has been collapsed. The current public surface (landing /
> /picks / /performance) is **free for everyone with no tier gating**.
> Stripe checkout / upgrade / portal endpoints were deleted. Only the
> Stripe webhook is retained for the 2 legacy paid subscribers; their tier
> column is honored in DB but the Pro/Elite features they bought no
> longer have a frontend (refund or comp at operator discretion).
>
> The rest of this document is preserved verbatim as a historical record
> of what the tier system **was** between 2026-04-04 and 2026-06-24. If
> a paid product returns, design it from scratch — don't restore this.
>
> See `ROADMAP.md` → "Current System State (2026-06-24)" for the
> current truth.
>
> **Confirmed 2026-09-14 (PICKS-PAGE-SHOW-FORWARD-TEST):** `/picks` now serves
> the pre-registered sharp-edge forward test and stays **free and ungated,
> including signed-out**. This is not just inherited — it is forced. The same
> picks are broadcast to the public Telegram channel the moment they are
> generated, so gating them on the site would be theatre; and more importantly
> the pre-registration requires the PUBLISHED set and the RECORDED set to be
> identical, so a session-dependent cut would evaluate the stopping rules on a
> cohort no reader ever saw. The old auth-aware split (PICKS-USER-GATE,
> calibrated-only for anon vs calibrated+beta+active signed-in) went with the
> model path; the new rule has one cohort by construction (every qualifying leg,
> no daily selection cap since 2026-09-15). Pinned by
> smoke `PICKS-USER-GATE`.

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

> **ANON-AUTH (2026-06-10):** "Anonymous" users get a lazily-created Supabase
> anonymous user on their first save action (favorite, tracker pick) — so the
> Y/— for favorites + tracker picks below now reads "Y (anon user auto-created)"
> in practice. The user.id persists across the upgrade-to-real-account flow via
> `linkIdentity` so favorites + picks carry over. Anon users still can't vote or
> add match notes (RLS gate on `is_anonymous=false`). Match-favorite button
> triggers the upgrade modal after the 3rd favorite is added.

| Feature | Anonymous | Free | Pro | Elite |
|---------|:---------:|:------------:|:-------------:|:-------------:|
| Favorite teams (star toggle) | Y (anon auto-created) | Y | Y | Y |
| Favorite leagues (star toggle) | Y (anon auto-created) | Y | Y | Y |
| "My Matches" filtered view | — | Y | Y | Y |
| Prediction tracker (log picks) | Y (anon auto-created) | Y | Y | Y |
| Pick stats (hit rate, W/L, streak) | — | Y | Y | Y |
| Match notes (private journal) | — (anon blocked by RLS) | Y | Y | Y |
| Community prediction voting | — (anon blocked by RLS — anti-vote-inflation) | Y | Y | Y |
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
| Performance — forward-test bot rows (sharp/consensus): sharp-anchor CLV + n + Pinnacle/consensus mix, own-book CLV beside it, earlier rule versions named ([[#156]], 2026-09-25); earlier-rule picks that pass the current rule on pick-time data counted in the current record, noted "re-checked" in the detail view ([[#158]]; `picks_forward_test_public` gains `record_rule_version` / `record_state`, the re-check table and record view stay private). Model-bot rows still show CLV as a direction arrow only (numbers Elite) | Y | Y | Y | Y |
| Track record — prediction history (+ edge % column) | — | — | — | Y |
| Track record — feature comparison table | Y | Y | Y | — |
| Track record — today's picks (match + pick + confidence) | Y | Y | Y | Y |
| Track record — today's picks (+ best odds revealed) | — | — | Y | Y |
| Track record — today's picks (+ Kelly stake revealed) | — | — | — | Y |
| Value bets page — stats only (bet count, edge tiers, leagues) | — | Y | Y | Y |
| Value bets page — full picks from CALIBRATED bots (side + odds + model % + edge %) | — | — | Y | Y |
| Value bets page — full picks from ALL active bots (calibrated + active + experimental incl. inplay) | — | — | — | Y |
| Value bets page — Kelly stake revealed | — | — | — | Y |
| Value bets page — Live picks section (inplay bots, auto-refresh every 60s, stale badge at >120s) | — | — | Y (calibrated cohort) | Y (all active) |
| Value bets page — rolling-30d hero stats (ROI + CLV + win rate + n settled) — Pro shows calibrated cohort, Elite shows all-active | — | — | Y | Y |
| Model probability + edge % per match | — | — | — | Y |
| CLV tracking per bet (Pro on track record, Elite on match detail) | — | — | Y | Y |
| Full bot ROI analytics + strategy breakdown | — | — | — | Y |
| Bankroll analytics dashboard (/bankroll) — page still exists but unlinked from nav as of 2026-06-02 (PRO-TIER-V2). Direct URL access only. | — | — | — | Y (direct URL) |

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

### World Cup 2026 Games (WC-PHASE-4 / WC-GROUP-PREDICTOR / WC-AI-GHOSTS)

| Feature | Anonymous | Free | Pro | Elite |
|---------|:---------:|:------------:|:-------------:|:-------------:|
| Browse `/world-cup` hub (schedule, groups, AI previews) | Y | Y | Y | Y |
| Knockout bracket picker (`/world-cup/bracket`) | Preview only | Save picks | Save picks | Save picks |
| Group standings predictor (`/world-cup/groups-predictor`) | Preview only | Save picks | Save picks | Save picks |
| Combined leaderboard (`/world-cup/bracket/leaderboard`) | Y | Y | Y | Y |
| AI ghost entries on leaderboard (5 named models) | Y | Y | Y | Y |
| Eligible for top-3 prize (1 month Elite, free) | — | Y | Y | Y |

Notes:
- AI ghost rows on the leaderboard (`wc_bracket_meta.ai_label IS NOT NULL`) are NOT eligible for prizes — the prize SQL filter is `WHERE ai_label IS NULL`. UI shows "🤖 — not eligible" footnote.
- Both games lock at the same instant: 2026-06-11 19:00 UTC (first WC kickoff). Lock is server-enforced in `saveBracketPick` + `saveGroupStandings` actions.
- Group-standings scoring resolves when each group's six fixtures all finish (~Jun 27). Bracket scoring runs continuously through the tournament.

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
| `/performance` | Public — hero, bot leaderboard (settled, **W/L, P&L, ROI** for everyone since [[#159]]), and **every row's detail view (chart + every pick) for every reader** since [[#159]]. Logged-in: full filterable history. Elite: + per-pick stake / edge / CLV number, Avg CLV column. `/track-record` is a redirect to this canonical URL. |
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

## /performance bot leaderboard (#159, 2026-09-25, owner-approved)

The "Pro unlocks W/L, P&L, charts" split on /performance **ended**: the bot detail view opens for every reader,
the same view Pro had. Numbers come from ONE engine view (`bot_performance`) on the PUBLIC basis — flat €10 at the
best price available when the pick was made on all books; CLV against the sharp closing line.

| Surface | Anonymous / Free | Pro | Elite / superadmin |
|---|---|---|---|
| Row: settled, W / L, ROI, P&L (€, flat) | ✓ | ✓ | ✓ |
| Row: CLV direction icon | ✓ | ✓ | ✓ + Avg CLV number column |
| Detail view (bankroll chart + every pick, sharp-CLV line with n and Pinnacle/consensus mix; header = the bot's `bot_performance` row read in the same request as the picks — the stake-weighted secondary is gone, every stake is flat since [[#155]]) | ✓ | ✓ | ✓ |
| Detail view: per-pick **EV** (`model_prob × odds − 1`) for EV-unit bots (EV5 VIP, newplus, the O/U sharp bots) in place of the pp edge ([[#155]]) | ✓ | ✓ | ✓ |
| Detail view: VIP bots — **EV8 / EV5 split** (n settled, flat ROI, sharp CLV per band; replaces the retired EV8 bot, [[#155]]) | ✓ | ✓ | ✓ |
| Detail view: per-pick CLV | direction only | direction only | the number |
| Detail view: per-pick stake, edge (pp-edge bots; EV bots show EV to everyone) | ✗ | ✗ | ✓ |
| VIP / hide_pending bots in the detail view | settled picks only | settled picks only | settled picks only |
| Full filterable history below the table | teaser (10) | ✓ (logged-in) | ✓ |

The detail view's picks come from `/api/performance/bot-legs` (service role, server-side): only bots /performance
lists (status TESTING / ACTIVE — VIP bots included when their status is public; never retired, never
experimental — [[#155]]), and for VIP + hide_pending bots SETTLED legs only (their pending picks are the paid product, #148).

## /performance work done + retired strategies ([[#157]], owner 2026-09-25)

Same for every reader (no tier split). Kept visibly APART from the headline, which stays today's ACTIVE
strategies only (BETA + CALIBRATED until [[#175]]) (`getPublicCohortBotNames`, `retired_at IS NULL`) — retired picks count in the totals and in the
retired section, never in the active ROI.

| Surface | Anonymous / Free / Pro / Elite |
|---|---|
| "The work behind it": picks tested, strategies, distinct selections, settled, retired / internal / on-page counts (`bot_public_work_done`) | ✓ |
| Collapsed "Show retired strategies": one row per FAMILY summing every retired bot (W-L, flat ROI at the price available at pick time, sharp-anchor CLV **number** with n, data-quality flag counts) + up to 2 representatives per family (chosen by sample and close coverage, never result) | ✓ |
| Family lesson line: soft-book triggers vs the sharp line vs vs our model (family CLV + n only; no experimental bot's own record) | ✓ |
| bot_v10_1x2 detail view: swap-window note (N of its picks priced 10 May–14 Sep while the 1X2 model was partly home/away-swapped, #065) | ✓ |

Retired-family CLV is shown as a number to everyone (unlike active model-bot rows, direction only below Elite):
it is the evidence for why a strategy was retired, and the owner approved the family-level CLV line (answer 3).

## Bot status → what the public sees ([[#155]], owner 2026-09-25)

**CHANGED 2026-09-26 ([[#175]], migration 462):** BETA and CALIBRATED merged into ONE status, **ACTIVE** — the
only status that counts in the headline totals. /performance labels it "counts in the totals above"; TESTING
reads "own record only — not in totals", and the hero ROI tiles say "ACTIVE bots only".

ONE status per bot decides distribution — the same for Anonymous, Free, Pro and Elite (VIP is the only
tier-dependent channel). Source: engine view `bot_distribution` (migrations 437 + 442); full table in
`docs/SYSTEM_MAP.md` "Lifecycle".

| Status | /performance | /picks | Public Telegram | Own record | Headline totals |
|---|---|---|---|---|---|
| EXPERIMENTAL | ✗ (admin only) | ✗ | ✗ | admin only | ✗ |
| TESTING | ✓ marked TESTING | ✓ (every pick) | ✓ only at EV ≥ 5% ([[#174]], 2026-09-26) | ✓ | ✗ |
| ACTIVE | ✓ "counts in the totals above" | ✓ | ✓ (every pick) | ✓ | ✓ (headline = ACTIVE only) |
| VIP · <status> | ✓ settled only | ✗ | ✗ (Pro/Elite DM + private channel) | ✓ | ✗ |

Pending picks of an EXPERIMENTAL bot are no longer anon-readable (`simulated_bets` "Public read" policy via
`bot_pending_public`, migration 442; before: 17 bots exposed). Every card shows its status label and its method
label (MODEL / SHARP / CONSENSUS).

## VIP picks (#148, 2026-09-24)
| Surface | Anonymous / Free | Pro / Elite |
|---|---|---|
| VIP bots (`bot_combined_1x2_ev5_v1` 1X2, `bot_ou_sharp_early_v1` O/U — migration 424) live picks | ✗ — hidden until settled (RLS, migration 420) | ✓ Telegram DM, each labelled EV8 / EV5 (and the private VIP channel once `TELEGRAM_VIP_CHAT_ID` is set) |
| VIP bot settled picks on /performance | ✓ | ✓ |
| Other bots' picks by Telegram DM | ✗ | ✗ — **since 2026-09-24 Pro/Elite DMs carry ONLY VIP picks** (owner) |
| A FREE bot's pick that VIP holds or would take (VIP FIRST, [[#164]]) | ✗ before kickoff — recorded, but held back from /picks, the watchlist, the public channel and every pending view; appears at kickoff | same — the VIP copy is what they get |

**VIP FIRST ([[#164]], owner 2026-09-25).** VIP never gives a pick up. A free bot's (or a published forward-test
arm's) pick that is VIP-HELD (a VIP / hide_pending bot has it pending) or IN VIP'S RANGE at its price and decision
time (1X2 NEW+ EV ≥ 5%; O/U 5–15% above Pinnacle with ≥ 12 h to kickoff) is still recorded and counted, but
`held_back_until` = kickoff hides it from every free surface until then. Decided once in
`workers/utils/vip_guard.py`; surfaces filter the stored column (migration 439). The detail view
(`/api/performance/bot-legs`) also never lists an EXPERIMENTAL bot (the #161 twin arms were readable, pending legs
included) and drops held-back pending legs for every bot.

