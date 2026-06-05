# Forebet — Competitor teardown (2026-06-05)

## TL;DR

**13.5M visits/mo** (Mar 2026, Similarweb) makes Forebet the SEO behemoth of football
predictions — ~675× our traffic. **Not a tipster**: they self-describe as a free
data/analytics publisher. Monetization is **display ads + bookmaker affiliate links**
(est. $4M/yr revenue per RocketReach). **Direct SEO threat** to our
GROWTH-SEO-CONTENT-ENGINE — they own the long tail of "[Team A] vs [Team B] prediction"
Google queries via programmatic pages. **Not a direct product competitor** for paid
subscribers (they're free) — but a *massive* funnel competitor for top-of-funnel intent.

## Snapshot

| | |
|---|---|
| Site | forebet.com |
| Founded | 2009 (16+ years) |
| HQ | Nairobi, Kenya |
| Funding | Unfunded, bootstrapped |
| Traffic | **13.5M visits/mo** (Similarweb Mar 2026, -13% MoM) |
| Revenue (est.) | **$4M/yr** (RocketReach) |
| Coverage | 700-850 football leagues + 2,400 sports leagues; 2,250+ predictions/week |
| Pricing | **Free** (display ads + affiliate links) |
| Methodology | Poisson + "Beyond Poisson" + regression on historical data |
| Accuracy claim | "52-58%" (independent tests) up to "68% on top-5 European 1X2" |

## What they do better than us

1. **SEO dominance.** Programmatic prediction pages for every fixture in 700+ leagues,
   indexed since 2009. 16 years of compounding link equity. We can't catch this organically
   in 1-2 years — we'd need a different strategy (better content, not more content).
2. **League breadth.** 700-850 football leagues vs our 280+. They've solved data ingestion
   at scale (likely API-Football + scraping at scale).
3. **Brand recognition.** "Forebet predictions" is a Google search term in its own right
   (auto-complete suggestion). 16 years of brand history is a moat we don't have.
4. **Free + ad-supported.** Zero friction to consume their predictions. We have a paywall
   on Pro features (correct trade-off for us — but for top-of-funnel, free beats paid every
   time).
5. **Kelly Criterion "value bets" section.** Free version of what we sell. Lower quality
   (no live odds drift, no per-bookmaker comparison), but free.

## What we do better than them

1. **Multi-bookmaker live odds.** We track 13 bookmakers in real-time and identify pricing
   inefficiencies. Forebet displays one set of "model probabilities" against generic
   bookmaker odds — their value-bet identification is much cruder.
2. **CLV-tracked picks.** We measure closing line value on every pick. They measure
   accuracy (hit rate). Accuracy is the wrong metric — 68% accurate on 1.40 odds loses
   money.
3. **Honest drawdown disclosure.** Methodology page now shows our worst drawdown (-€398
   over 9 days). Forebet shows aggregate "accuracy %" — nothing about variance or risk.
4. **Telegram pre-kickoff delivery.** They're a website you visit; we push to your phone
   before the line moves.
5. **Per-bet AI explanation (Elite tier).** They show probabilities; we explain why.
6. **In-play / live xG / match-detail intelligence.** They're prematch-only. We have
   in-play bots and live xG tracking.

## Strategic threat assessment

### Threat to GROWTH-SEO-CONTENT-ENGINE (HIGH)

Our per-fixture SEO pages compete directly with Forebet's per-fixture prediction pages
for the same Google queries: "[Team A vs Team B prediction", "[league] tips today",
etc. Forebet has 16 years of link equity and 13.5M/mo traffic momentum. We cannot win on
**volume** — we have to win on **content depth**:

- Their per-fixture pages: Poisson percentages + correct-score grid + ad-stuffed
- Our per-fixture pages: live odds drift + value-bet flag + bot consensus + lineups + xG
  + per-bookmaker comparison + Telegram alert CTA

Our angle: **"more decision-useful per page, fewer pages."** Don't try to index 850
leagues — index the 50-100 league/fixture combos where we beat Forebet on actual betting
utility. Pursue **comparison-intent queries** rather than prediction-intent queries
("WHERE to bet [pick]" rather than "WHAT to bet [match]"). Our value-bet detection +
multi-book comparison is uniquely positioned for "where to bet" intent.

### Threat to paid acquisition

If a casual searcher lands on Forebet first (likely — they outrank everyone), they get
"good enough free predictions." We need to:
- Be findable on **adjacent queries** Forebet doesn't dominate: "value bet today", "CLV
  betting tool", "telegram football tips bot", "kelly criterion calculator soccer"
- Have a **differentiated landing** that makes Forebet feel like a free toy when compared
  side-by-side

### Threat to product positioning

None. Forebet is not a tipster service. They don't sell subscriptions. They don't claim
edge over the market. They're a free reference tool. Different product entirely.

## Action items

### 1. Don't try to outscale Forebet on programmatic pages (re-affirm)
GROWTH-SEO-CONTENT-ENGINE is already scoped correctly (depth, not breadth). This research
re-affirms that direction. Don't be tempted to chase 850-league coverage.

### 2. Build the "vs Forebet" /vs page (1 day)
SEO benefit: high-intent comparison query. Target: "forebet alternative", "forebet
premium", "is forebet good for value bets". The honest pitch is **"Forebet predicts the
match — we tell you where to bet it."** Position us as the action layer on top of their
information layer (or our own).

### 3. Steal their Kelly-Criterion-value-bets URL pattern (1h research)
Forebet has `/en/values` — a dedicated value-bet page that ranks high in SERPs. Check
ours (`/value-bets`) for parity on the same kind of structured data + headlines that
Forebet uses. If they're capturing "value bets today" queries, we should be too.

### 4. Watch their MoM traffic drop (-13.16% in March 2026)
Three months of decline could indicate a Google algo penalty (programmatic-prediction
content has been a target of Google's "spammy SEO" rollouts). If they're declining, the
SERP real estate they hold becomes available. Re-check in 30 days.

## Update to PRIORITY_QUEUE GROWTH-COMPETITOR-RESEARCH section

Add to "Analysed direct so far":
> **Forebet.com (2026-06-05, 13.5M/mo, $4M/yr est rev, free + ad-supported)** — Not a
> direct product competitor (free, no subscription). Massive SEO funnel threat — owns
> programmatic prediction pages since 2009. Decline pattern (-13% MoM Mar 2026) worth
> monitoring. Action: build `/vs/forebet` page, position as action layer on top of their
> info layer.
