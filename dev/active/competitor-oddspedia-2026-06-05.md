# Oddspedia — Competitor teardown (2026-06-05)

## TL;DR

Flagged as our "closest twin" — they do odds comparison + AI value-bet detection +
predictions in one place, which is structurally what we do. **But** the business model is
completely different: **free + affiliate-driven**, not subscription. ~1.89M visits/mo
(Similarweb Jul 2025), 250+ bookmakers, free SmartBet value-bet tool, affiliate revenue +
widget syndication network. **Not a direct subscription competitor** but a strong
top-of-funnel SEO + product competitor for "odds comparison" and "value betting" intent.

## Snapshot

| | |
|---|---|
| Site | oddspedia.com |
| Traffic | **1.89M visits/mo** (Similarweb Jul 2025), 12:13 avg session, 48.6% organic |
| Revenue model | **Free + affiliate** (bookmaker referral commissions + widget syndication 50/50 split) |
| Bookmakers compared | **~250** (rated "safe gambling sites") |
| Pricing | Free (no subscription tier) |
| Key tool | **SmartBet** — AI value-bet detection, 100+ parameters/match, free |
| Methodology claim | "Proprietary model" + "professional bettors" + AI |
| Coverage | Multi-sport (football, tennis, basketball, NFL etc), hundreds of leagues |

## What they do better than us

1. **250 bookmaker coverage.** We track 13. They aggregate the entire affiliate-friendly
   bookmaker universe. For pure odds comparison, they win — and the "best odds for this
   pick" use case is where their volume comes from.
2. **Widget syndication network.** Publishers and affiliates embed Oddspedia widgets on
   their own sites; clicks split 50/50. This creates a **distributed traffic
   acquisition channel** we can't easily replicate. They've productized their data as
   embeddable widgets.
3. **Free + zero friction.** No paywall on SmartBet, value bets, odds comparison. The
   floor is "consume forever for free." Our floor is "free tier limited to N picks/day."
4. **12-minute avg session.** Casual bettors hang around to compare odds + browse picks.
   They've built genuine browse-time engagement, not just a transactional landing page.
5. **Multi-sport breadth.** They cover football + tennis + basketball + NFL etc. We're
   football-only by design.
6. **SEO + organic traffic.** 48.6% organic, hundreds of programmatic odds pages indexed
   for "[bookmaker] vs [bookmaker]", "[market] best odds", "[match] odds" queries.

## What we do better than them

1. **CLV-tracked.** They don't publish CLV. They display predictions + odds but don't
   measure whether their predictions beat the closing line. We do — it's our headline
   metric.
2. **Honest variance + drawdown disclosure.** They don't surface drawdown or worst-week
   variance. We publish it (€-398 drawdown, methodology section 5b).
3. **Subscription product + accountable picks.** They're a free reference; we sell a
   product where every pick is timestamped and tracked. Free vs paid is a tradeoff but
   it also means **they have no incentive to be honest about pick quality** — affiliate
   model rewards engagement, not pick accuracy.
4. **Per-bet AI explanation (Elite).** They have SmartBet (the detection) but no
   per-pick rationale. We explain *why*.
5. **Live in-play bots + xG tracking.** They're prematch-focused odds comparison. Our
   in-play tracker and live xG are differentiated.
6. **Telegram-native delivery.** They're a destination site. We push to your phone.

## Strategic threat assessment

### Threat to GROWTH-SEO-CONTENT-ENGINE (MEDIUM)

Oddspedia's SEO surface is **odds-comparison-intent** ("[bookmaker A] vs [bookmaker B]",
"[match] best odds"). Our SEO surface is **pick-intent + comparison-intent**
("[match] value bet", "where to bet [pick]"). Some overlap, but they're not chasing
exactly the same queries as us.

Mitigation: focus our SEO on **action-intent** queries Oddspedia doesn't optimize for —
"value bet today + alert", "telegram football picks", "where to place [pick]".

### Threat to subscription growth (LOW)

They don't sell subscriptions. We're not fighting for the same wallet. They convert via
affiliate clicks to bookmakers; we convert via paying subscribers. Different funnel
entirely.

### Threat to "value bet detection" positioning (HIGH)

SmartBet is structurally similar to our value-bet engine. If a user wants "AI-detected
value bets, free, no signup," they get SmartBet. We have to articulate the **clearer edge**
in *our* detection:

| Their SmartBet | Our value-bet engine |
|---|---|
| Free, no account | Paid (€4.99-14.99/mo) for full surface |
| AI + 100 parameters | XGBoost + Poisson + 13-book live odds |
| One number (their "value %") | CLV-tracked + multi-bookmaker drift + bot consensus |
| No accountability for picks | Every pick timestamped, CLV-measured |
| No drawdown disclosure | Honest -€398 drawdown published |

The pitch isn't "we detect value too" — it's **"we detect value AND we can prove
historically our detection beat the closing line."** SmartBet can't prove that.

## Action items

### 1. Build `/vs/oddspedia` comparison page (1 day)
High-intent query potential: "oddspedia alternative", "oddspedia smartbet vs", "is
oddspedia worth it". The pitch: "Oddspedia gives you everything — you have to figure
out which signals to act on. We give you the curated picks with measured edge." Position
us as the **decision layer** on top of their **information layer**.

### 2. Consider partner-widget play — defer to V2
Their widget syndication network is a real distribution model. Long-term, we could offer
embeddable "value bet of the day" widgets to football-stats sites (FBref, WhoScored
audiences). Not now — needs the verified-ROI narrative first.

### 3. Steal their "free SmartBet" framing for our free tier (1h copy edit)
Current free tier is "limited X picks/day." Reframe as "**Free Smart Picks** — our
algorithm's best 3 detected value bets every day, with the CLV evidence behind each one."
The qualitative framing makes the free tier feel like a product, not a teaser. We don't
need to give away more; we need to *position* what we give away differently.

### 4. Don't compete on bookmaker breadth (re-affirm)
250 bookmakers is a different game. Our 13 are the books our users can actually access
in EU. Don't chase Oddspedia on aggregator depth.

## Update to PRIORITY_QUEUE GROWTH-COMPETITOR-RESEARCH section

Add to "Analysed direct so far":
> **Oddspedia.com (2026-06-05, 1.89M/mo, free + affiliate-driven, 250 books)** — Closest
> structural twin (odds + predictions + value-bet tool) but completely different revenue
> model (affiliate + widget syndication, not subscription). Free SmartBet is the closest
> product competitor to our value-bet engine but lacks CLV/drawdown accountability.
> Action: build `/vs/oddspedia`, reframe free tier as "Smart Picks" product not teaser.
