# SportBot AI — Competitor teardown (2026-06-05)

## TL;DR

The closest "AI vs the line + chat" competitor. Their product is structurally what
GROWTH-CHAT-AI-SPIKE is scoped to be: **conversational match analysis**, 60-second
"research" replacing 2+ hours, edge alerts, bankroll tracker with Kelly. Pricing
$18.99-39.99/mo (subscription) or $299-999 lifetime. **Published ROI: -27% over 30 days**
(22W-30L at 1.71 avg odds). They explicitly reject "accuracy" as a vanity metric — same
honesty stance as us, one step less advanced (ROI, not CLV). **No founders disclosed** —
credibility gap. Validates the $20-40/mo price point for chat AI but also shows
chat alone isn't a profitability driver.

## Snapshot

| | |
|---|---|
| Site | sportbotai.com |
| Founded / founders | Not disclosed (red flag) |
| Sports | Soccer (5 top leagues + UCL + WC), NBA, NFL, NHL, EuroLeague, Tennis, Afr/Asian |
| Pricing | **$18.99 Pro / $39.99 Premium** monthly · **$299 / $999 lifetime** tiers |
| Free | Single analysis, limited features |
| Headline ROI | **-27% / -13.3u over 30 days** (22W-30L at 1.71 avg odds) — publicly published |
| AI model | Not disclosed; claims "AI analyzes" 50+ bookmaker odds + injuries + form |
| Honesty framing | "Accuracy is a vanity metric. ROI is what makes money" |

## Pricing structure (lifetime tiers are unusual)

| Tier | Price | What you get |
|---|---|---|
| Free | $0 | 1 analysis, limited features |
| Pro | $18.99/mo | 10 daily analyses, 50 AI queries |
| Premium | $39.99/mo | Unlimited analyses, edge alerts |
| Lifetime Pro | **$299 one-time** | 250 analyses/mo + AI Coach |
| Lifetime Premium | **$999 one-time** | Unlimited + advanced auditing |

**Implication of lifetime tiers:** they need front-loaded cash. $999 lifetime ÷ $39.99/mo
= 25 months payback. They're either confident in retention OR they need the cash now.
Either way, lifetime pricing is a signal — and one we should consider for our own
Elite tier if growth is constrained.

## What they do better than us

1. **Conversational chat UX.** "Ask the AI about this match" interface is their core
   product. We have per-bet AI explanations but not a chat surface. This is exactly what
   GROWTH-CHAT-AI-SPIKE roadmaps.
2. **"AI Coach" — behavioral coaching layer.** Weekly audits for tilt, loss-chasing,
   overstaking, bankroll mismanagement. Genuinely novel. We don't have this.
3. **Multi-sport breadth.** Soccer + NBA + NFL + NHL + EuroLeague + Tennis. We're
   football-only.
4. **Bankroll tracker with Kelly sizing built in.** They've productized the bet-log
   workflow. We have suggested-stake-per-bet but not a "log your bets here" surface.
5. **Lifetime pricing tier.** Cash-forward business model; we don't offer this.
6. **iOS app.** We're web + Telegram only. Native app is a different distribution channel.
7. **Edge alerts.** Push notifications when a high-edge opportunity appears. We do this
   via Telegram; they presumably push via app — different channel, same idea.

## What we do better than them

1. **CLV — not just ROI — as honest scoreboard.** They've climbed from "accuracy" to
   "ROI" (good); we're one step further to "CLV" (better — variance-resistant). Their
   -27% ROI over 30 days is statistically meaningless (sample too small), but they
   publish it anyway. We can publish CLV that's actually meaningful at smaller samples.
2. **Methodology transparency.** Our `/methodology` page documents the model, the data
   sources, the drawdown. Theirs doesn't disclose what AI model they use, what data they
   ingest, or who built it. We win on transparency.
3. **Named team / engineering credentials.** Operator background is on the site. Theirs is
   anonymous — a red flag for a paid betting product.
4. **Honest negative-result publication.** They publish -27% ROI but don't explain it.
   We publish -€398 drawdown with educational context ("this is the cost of being a +EV
   bettor"). Same data, much better framing.
5. **Per-bet rationale, not just chat.** Every pick has an explanation tied to the
   actual model signals. Their chat is conversational but the picks themselves are
   delivered without per-bet rationale.
6. **Telegram delivery + EU bookmaker accessibility.** We're built for EU bettors with
   the books they can actually access. They're US-leaning ("$" pricing, NBA/NFL/NHL focus).
7. **Verified live engine (CLV-tracked, paper-trading record).** Our paper chain is
   timestamped and CLV-measured; their "track record" is just W/L outcomes.

## Strategic implications for GROWTH-CHAT-AI-SPIKE

The spike doc (`chat-ai-spike.md`) already concluded: build narrow Elite-only
single-match chat MVP, defer until v2-cohort/verified-ROI window. SportBot AI's existence
**confirms market demand at $20-40/mo** for this exact feature. Three concrete updates
to the spike plan:

### 1. Price point validation
$18.99 Pro / $39.99 Premium maps to our Pro €4.99 / Elite €14.99. We're 3-4× cheaper.
Either (a) we're underpriced and a chat tier could justify $10-15/mo premium, or
(b) they're overpriced and bleeding users. The -27% published ROI suggests bleeding.

### 2. AI Coach as a future feature concept
"Weekly behavioral audits — are you tilting? loss-chasing? overstaking?" — this is
**genuinely new product surface** I hadn't considered. Worth a separate spike post-v2.
Concrete prompt: "every Sunday, send the user a 200-word AI-written summary of their
betting behavior that week, calling out specific patterns." Low-effort, high-perceived-
value. Add to backlog.

### 3. Don't copy their anonymous-founder posture
Our trust play is named-operator + transparent methodology. Going anonymous would be
strategically wrong even if it's easier to ship.

## Action items

### 1. Build `/vs/sportbot-ai` page (1 day)
High-intent comparison query potential. The pitch:
> "SportBot AI charges $18.99-39.99/mo for the AI chat experience. We charge €4.99 Pro
> / €14.99 Elite for chat + the actual edge-detection engine + CLV tracking + Telegram
> alerts. Their published ROI is -27%. Ours is positive (+CLV measured). Same chat
> experience, profitable backend."

Counter-pitch (we don't lie): they have multi-sport coverage and the AI Coach behavioral
feature; we don't. Pick them if you bet NBA/NFL/NHL or want bankroll behavioral coaching
right now.

### 2. Add AI Coach to backlog (separate task, post-v2)
File new task: **GROWTH-AI-COACH-WEEKLY-AUDIT** — Sunday email/Telegram message
analyzing the user's last 7 days of bets, flagging tilt patterns, loss-chasing,
overstaking, market drift. Uses Gemini/Claude with prompt-template + bet history.
Estimated cost: ~$0.005 per audit × 200 users × 4 weeks/mo = $4/mo. Trivial cost,
high differentiation. Defer until N≥200 paying users.

### 3. Lifetime pricing as a growth lever — defer
Their $299 / $999 lifetime tiers are interesting cash-forward levers but only make sense
when we have verified ROI to back the value claim. Note for OUT-OF-BETA-CUTOFF window.

### 4. Update GROWTH-CHAT-AI-SPIKE doc
Add a "competitor reference" section noting:
> "SportBot AI ($18.99-39.99/mo) validates the price point and product scope. Their
> -27% published ROI shows chat alone doesn't print money — pair our chat with the
> proven +CLV detection engine for differentiation."

## Update to PRIORITY_QUEUE GROWTH-COMPETITOR-RESEARCH section

Add to "Analysed direct so far":
> **SportBot AI (sportbotai.com, 2026-06-05, US-leaning, multi-sport)** — Closest AI
> chat + bankroll competitor. Pricing $18.99/$39.99 monthly + $299/$999 lifetime tiers
> validates market demand for chat-AI at $20-40/mo. Published ROI -27% over 30 days
> (22W-30L). No founders disclosed (credibility gap). **AI Coach feature** (weekly
> behavioral audits) genuinely novel — added to V2 backlog as
> GROWTH-AI-COACH-WEEKLY-AUDIT. Action: build `/vs/sportbot-ai`, update
> GROWTH-CHAT-AI-SPIKE with their pricing as validation.
