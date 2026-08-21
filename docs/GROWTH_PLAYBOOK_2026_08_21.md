# Growth Playbook: 29 → 100 Telegram Subs (Ban-Safe Edition)

> **Written 2026-08-21.** Goal: grow `@oddsintelpicks` from ~29 → 100 subs to unlock the TELEGRAM-PAID-TIER build trigger, without getting banned, shadow-banned, or hit with rate limits on any platform.

The single most common way betting/analytics accounts get killed:

> Post pick → link to Telegram channel → repeat on same platform → auto-flagged as "affiliate spam" or "gambling promotion" → account nuked.

Everything below is designed around that failure mode. **The rule of thumb:** you're publishing *sports data analysis*, not *betting picks*. Same content, framed differently. Frame right and every platform treats you as a data/dev account; frame wrong and you get banned.

---

## What triggers bans on each platform

Read this section before posting anywhere. It's the failure mode you're avoiding.

### Reddit

**What gets banned:**
- Direct picks with odds + stake + a channel invite in the same post
- Same link posted across ≥3 subs in a day → sitewide spam flag
- Newly-created accounts posting anywhere except confessional subs → shadow-banned within hours
- Ignoring subreddit-specific "no self-promo" rules (many major sports subs)
- Affiliate links

**What's fine:**
- Data-heavy posts with charts/tables — treated as analytics
- Answering questions in comments with domain expertise
- Your `/performance` page as a reference in comments (not top-level posts) after building comment karma
- Cross-references to public tools + GitHub repos (open-source signals goodwill)

**Subreddits ranked by ban risk (LOW = safe, HIGH = one wrong post and you're out):**
| Sub | Ban risk | Safe format | Rule to follow |
|-----|:--------:|-------------|----------------|
| r/DataIsBeautiful | LOW | [OC] tag on original chart posts | Must be tagged [OC], no sales language, no channel link (top-level) |
| r/algotrading | LOW | Model discussion + numbers | No "come sub to my channel", explain the *method* |
| r/MachineLearning | LOW | Technical writeup | Show model architecture, no CTA at all |
| r/buildinpublic | LOW | Progress updates | Free to mention project + link (this is what the sub is for) |
| r/SideProject | LOW | Ship-day announcement | Same as buildinpublic |
| r/soccer | HIGH | Comments only, no self-promo posts | Explicitly banned self-promo in rules |
| r/SoccerBetting | MEDIUM | OK in daily-picks threads only, not top-level | Flair yourself + follow daily thread convention |
| r/SportsBook | MEDIUM | OK in weekly-picks threads only | Must post in the recurring thread, not standalone |
| r/FootballBetting | LOW-MEDIUM | Post allowed, but not repeated | 1 post per month max, no thread hijacking |
| r/SoccerPredictions | LOW | Post allowed | Smaller sub, less strict |
| r/programming | MEDIUM | Only if there's a technical hook | No commercial tone at all |

### X / Twitter

**What gets banned:**
- Aggressive "SIGN UP" language + link in bio + same post 10× same week → algorithm suppression
- Any post with the word "bet" or "gambling" + external link → auto-flagged in many regions
- Buying followers → account restricted
- Adding "join my Telegram" to every reply → treated as spam by other users, reported

**What's fine:**
- Model performance threads with screenshots (no CTA)
- Replying to other sports/analytics accounts with substantive commentary
- Bio can mention Telegram + link but not with a hard-sell verb
- Quote-tweeting sports news with a data angle

### Instagram / TikTok / Reels

**What gets banned:**
- Any explicit mention of odds, stakes, "betting", "wagering" in captions → shadow-banned within days
- Direct link to Telegram (Meta hates linking to competitors) → post suppressed
- Affiliate links in bio → account restricted

**What's fine:**
- "Sports data" or "sports analytics" framing (never "betting model")
- Text-on-video with model insights: "Model called Barcelona -1.5 at odds 2.10" → **too specific, still risky**
- Text-on-video with *general* insights: "Home teams in tier-3 leagues are systematically overpriced by ~2% — here's why" → safer, still educational

### Discord

Not a growth channel for us — high moderation cost, active engagement required. Skip unless someone else runs it for us.

### Facebook Groups

**Ban risk:** high. Groups have individual moderators who see any promotion as spam. Skip.

---

## 30-day plan (target: 29 → 50 subs by 2026-09-21)

The 50-sub trigger starts the paid-tier build. This is the plan to get there without spending money or losing accounts.

### Week 1 (Aug 22 – 28) — Reddit, analytics-first

| Day | Action | Where | Rule to follow |
|-----|--------|-------|----------------|
| Sat | Post the /performance page P&L chart on r/DataIsBeautiful with [OC] tag | r/DataIsBeautiful | Title: "How my football prediction model performed over 100 days [OC]"; no Telegram link in top-level, /performance link is fine. Answer questions in comments. |
| Sun | Answer 3 questions on r/SoccerBetting daily discussion thread | r/SoccerBetting | Substantive answers using our model's data, no channel plug. Build karma. |
| Mon | Post model insight on r/algotrading | r/algotrading | Title: "Beat a 10-model benchmark with a Poisson + XGBoost blend for football markets"; discuss the technical approach, link GitHub, not Telegram |
| Wed | Answer 3 more questions on r/SoccerBetting | r/SoccerBetting | Same as Sunday |
| Fri | Post on r/buildinpublic with weekly progress | r/buildinpublic | Title format: "Week N of building a football analytics tool — retired 16 bots, ROI is honest now"; Telegram link OK here, this sub is *for* self-promo |

Expected sub gain: **3-6**.

### Week 2 (Aug 29 – Sep 4) — X threads + one strong Reddit post

| Day | Action | Where | Rule to follow |
|-----|--------|-------|----------------|
| Sat | X thread on the head-to-head audit (5 competitors) | X | 5-tweet thread with the audit table as an image; final tweet has /performance link. NO "sign up" language. |
| Mon | X thread on the calibration table with the "5-15pp overconfident" honesty angle | X | "Most tipster sites hide their model errors. Here's ours [table] and here's why we still make money [Kelly sizing]" — the honesty is the marketing |
| Wed | Post the P&L toggle chart on r/DataIsBeautiful (different chart than week 1) | r/DataIsBeautiful | [OC] tag, different framing. Alternate: /performance's per-market breakdown |
| Fri | r/buildinpublic weekly update | r/buildinpublic | Free to mention channel |

Expected sub gain: **5-10**.

### Week 3 (Sep 5 – 11) — GitHub + niche discovery

| Day | Action | Where | Rule to follow |
|-----|--------|-------|----------------|
| Sat | Publish a short technical blog post on the model architecture (self-hosted on `/methodology` OR Substack) | Your own domain | No affiliate/gambling positioning — it's a data-science piece |
| Sun | Post the blog link on r/MachineLearning + r/algotrading | Reddit | Technical framing only, no channel plug |
| Wed | Answer questions in r/SportsBook weekly picks thread | r/SportsBook | Same karma-building rule as week 1 |
| Fri | r/buildinpublic weekly update | r/buildinpublic | — |

Expected sub gain: **5-8**.

### Week 4 (Sep 12 – 21) — HackerNews shot + push to 50

| Day | Action | Where | Rule to follow |
|-----|--------|-------|----------------|
| Sat | Submit to Hacker News: "Show HN: Open-source football prediction model with Bitcoin-anchored track record" | HN Show HN | Framing must emphasize *open source* + *verifiable*, not *betting*. Post at ~9am EST Tuesday for best time window. |
| Mon | Cross-post the HN thread to r/programming if it gets traction | r/programming | Only if HN got 20+ upvotes, otherwise skip |
| Wed | If not at 50 yet, seed 3-5 comments across sub-2000-sub sports analytics subs | Long-tail Reddit | r/SoccerPredictions, r/FootballBetting, r/dfs, r/sportsanalytics |
| Fri | Update `PRIORITY_QUEUE.md` with actual sub count + decide whether TELEGRAM-PAID-TIER build starts | — | — |

Expected sub gain: **6-15** if HN lands, **2-4** if it doesn't.

**Total expected trajectory:** 29 → 50-70 by day 30.

---

## Content templates that work

### For X — the honest data thread

```
1/ We're an open-source football prediction model with 100+ days of public track record.

Here's what actually happens when you publish everything, including the losses.

2/ +8.58% ROI on 797 verified pre-match picks (screenshot of /performance)

+9.98% mean CLV. 81% of picks beat the closing line.

Public JSON. Bitcoin-anchored ledger. No selection.

3/ The model is 5-15pp overconfident on mid-range picks (screenshot of calibration)

We know it. Kelly sizing + edge gate compensate.

Not fixing this quietly. Publishing it.

4/ Head-to-head against 5 public tipster sites:
- We beat 4 of 5 on ROI
- The one we lose to (Forebet) audit-verified: 57% of picks quote odds unreachable in any book

Screenshot of table.

5/ Everything's at oddsintel.app/performance and the API at /api/v1/track-record

Free Telegram picks channel: @oddsintelpicks
```

Notice: NO "sign up now", NO "join today", NO exclamation marks in the CTA. Just the URL as text.

### For Reddit — the analytics [OC] post

**Title:** "How my football prediction model performed over the last 100 days [OC]"
**Body:**
- Attach the P&L toggle chart as image
- One paragraph on methodology (Poisson + XGBoost blend)
- One paragraph on findings (which markets were profitable, which weren't)
- Link to /performance in the body (not comments — top-level is OK on DIB)
- End with: "Chart source data in `/api/v1/track-record`. Happy to answer methodology questions."

**No** "follow my Telegram" — that gets you flagged. The `/performance` link is enough.

### For Hacker News — the "Show HN" post

**Title:** "Show HN: OddsIntel — open-source football prediction model with a public ledger"
**Body:**
```
Hi HN — I've been building a football prediction model for the last 6 months.
Poisson + XGBoost blend, 100+ days of public track record, Bitcoin-anchored
timestamps on every daily snapshot so nothing gets rewritten.

Track record at oddsintel.app/performance — 8.58% ROI on 797 pre-match
picks. JSON API at /api/v1/track-record. Repo at github.com/msellin/odds-intel-engine.

What I'd love feedback on:
- Model calibration approach (we're publishing the ~10pp overconfidence
  problem, using Kelly to compensate — is there a cleaner approach?)
- The competitor audit — is the methodology fair? (See /performance
  head-to-head)
- Where else should this live? (currently free tier + planned paid Telegram)
```

Note: no "sign up", explicitly asking for technical feedback frames it as an open-source project not a promotion.

---

## What NOT to do

1. **Don't post the same link across 5 subs in one day** — sitewide spam flag. Space it out over the month.
2. **Don't run any paid ads yet** — Meta/Google policies block betting content in most jurisdictions; a rejected ad review flags the account. Ads become viable once you have a paying customer + can prove "sports analytics service" not "gambling".
3. **Don't buy Telegram members** — Telegram detects and purges within 48h. Channel gets flagged. You lose 3× what you paid for.
4. **Don't DM Reddit users** who commented on your posts. Ever. Instant shadow-ban.
5. **Don't cross-promote from `@oddsintelpicks` to another Telegram channel** you own until the paid channel exists — Telegram flags this as farming.
6. **Don't post the same X thread twice in a month** — algorithm dampens repeats. Different angle each time.

---

## When to consider paid ads

Not before you have:
1. **100+ subs** on the free channel (proves demand)
2. **At least one paying Pro subscriber** (proves conversion works)
3. **A landing page that's Meta-policy-safe** — no visible odds, no explicit "betting", positioned as "sports analytics" or "AI football insights"

Once those three land, the cheapest paid channel is **Reddit promoted posts** in the analytics subs (r/algotrading, r/DataIsBeautiful sponsored slots — €2-5 CPM). Telegram Ads officially launched a self-serve platform in 2024 but the minimum is €2K/campaign — skip until you have real revenue.

Meta/Google ads for gambling need certification in each jurisdiction. Not worth the setup cost until €500+/mo revenue.

---

## Metrics to track weekly

Just three numbers, tracked in a spreadsheet:

| Week | Sub count | Best post source | Notes |
|-----|:---------:|------------------|-------|
| 2026-08-21 | 29 | — | Baseline |
| 2026-08-28 | ? | ? | ? |
| ... | | | |

If week-over-week growth < 3 subs after 2 weeks, the content isn't landing — change the format, not the volume. Posting more of the same is what triggers bans, not what fixes low engagement.

---

## Summary

- **Content is the same** as what you'd publish anyway — /performance data, model insights, honest calibration
- **Framing is what changes** per platform — never "come subscribe", always "here's what the data shows"
- **Cadence is spread** — max 1 post/subreddit/week, max 2 X threads/week, max 1 HN attempt per feature
- **No paid ads yet** — the risk/reward is off at 29 subs
- **50-sub milestone** unlocks the paid tier build; 75 = advertise; 100 = launch. Filed in PRIORITY_QUEUE as TELEGRAM-PAID-TIER-2026-08-18.

If a post gets flagged, delete + wait 30 days before posting to that platform again. Don't argue with mods.
