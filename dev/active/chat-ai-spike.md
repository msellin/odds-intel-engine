# GROWTH-CHAT-AI-SPIKE — scoping doc

> Tier A #9 (2026-06-05). Spike output only — do NOT build from this doc
> without explicit operator green-light. Purpose: scope what a conversational
> "ask our model about [match]" interface looks like, what it would cost,
> what risks it carries, and whether to build it now or later.

## TL;DR

**Recommendation: build a narrow, Elite-only, single-match scope MVP. ~3-4 days work. Defer broader chat (multi-match, free-form questions) until we have usage data.**

The wedge competitors (ParlaySavant, SportBot AI, Rithmm) exploit is real-time data plumbing — and we already have it. We're not building "chat-with-an-LLM-that-knows-football"; we're building "chat-with-our-model's-current-output." The constraint is the moat.

## Why now / why later

**Why now:**
- Competitors are normalizing chat interfaces in this space. By Q3 2026 a chat UI may be table stakes for premium AI-betting products.
- We already have the infra: Gemini Flash-Lite is wired in `bet-explain/route.ts`, news-checker, WC previews, AI-ghost picks. Cost economics proven (~$0.01/call).
- Match-detail pages already aggregate the exact data the LLM needs (predictions, odds, signals, lineups). Building chat is a thin layer on existing data flows.

**Why later (counterpoint):**
- 35 signups total — we don't have a user base to justify a complex feature.
- Chat is *easy to demo, hard to make useful.* The LLM must NOT hallucinate, must NOT fabricate stats, must NOT give betting advice (regulatory). One bad response screenshot ruins trust.
- Our actual moat is CLV-first honesty. Chat could dilute that if it sounds like every other AI chatbot.

## The MVP scope (if we build)

### What it IS
- Elite-only chat widget on `/matches/[id]` pages
- Scoped to **one specific match** per conversation — the match the user is currently viewing
- 5-10 turn conversation max, then "start a new chat"
- Pre-canned suggested questions (the user *picks* from a list rather than free-typing for v1):
  - *"Why is the model picking [selection] over [alternative]?"*
  - *"How does [home team]'s recent form compare to [away team]?"*
  - *"What's the biggest signal driving this prediction?"*
  - *"How confident is the model and what could go wrong?"*
  - *"Why is the edge negative on [selection]?"*

### What it ISN'T (v1 explicit non-goals)
- ❌ Free-form chat across multiple matches ("compare today's picks")
- ❌ Personalised bankroll / Kelly advice ("how much should I bet?") — regulatory landmine
- ❌ Sports-knowledge chat that doesn't use our data ("who's the best striker in PL?")
- ❌ Real-time chat during live games (different data envelope, defer to Phase 2)
- ❌ Voice / streaming / multi-modal — text in, text out

### Architecture sketch

```
User on /matches/[id]                              src/app/api/match-chat/route.ts
       │                                                       │
       │  POST  { matchId, question, conversation_id }         │
       ▼                                                       ▼
  Chat UI ──────────────────────────►  Auth + Elite gate (same pattern as /api/bet-explain)
   client                                              │
       ▲                                               ▼
       │                            ┌──────────────────────────────────┐
       │                            │  fetchMatchContext(matchId):     │
       │                            │   - matches row + teams + league │
       │                            │   - predictions (ensemble)       │
       │                            │   - odds_snapshots (latest)      │
       │                            │   - match_signals (top 10)       │
       │                            │   - lineups + injuries           │
       │                            │   - h2h + form                   │
       │                            │   - relevant simulated_bets      │
       │                            └─────────────────┬────────────────┘
       │                                              ▼
       │                            ┌──────────────────────────────────┐
       │                            │  build_prompt(context, question, │
       │                            │                conversation_log)  │
       │                            └─────────────────┬────────────────┘
       │                                              ▼
       │                            Gemini 2.5 Flash-Lite (existing model)
       │                                              │
       │                            ┌─────────────────▼────────────────┐
       │                            │  Cache: match_chat_log table     │
       │                            │   key (user_id, match_id,        │
       │                            │        question_hash)            │
       │                            │   so the same question on the    │
       │                            │   same match doesn't re-bill     │
       │                            └─────────────────┬────────────────┘
       │                                              ▼
       └──────────────────────────────────────  streamed response
```

### LLM context envelope (every call)

```yaml
system:
  - "You are OddsIntel's match-analysis assistant."
  - "Answer ONLY using the match data provided below. If the data does
     not support an answer, say so explicitly."
  - "Never give betting advice, stake recommendations, or guarantees."
  - "Never use phrases like 'banker', 'guaranteed', 'sure thing',
     'easy money', 'lock'. These are blacklisted."
  - "Reference specific numbers from the data, not vague intuitions."
  - "Keep responses under 150 words."

context:
  match:
    home: "Arsenal"
    away: "Manchester City"
    league: "Premier League"
    kickoff_utc: "2026-06-07T16:00:00Z"
  predictions:
    1x2_home: 0.31
    1x2_draw: 0.27
    1x2_away: 0.42
    over_25: 0.62
    btts_yes: 0.71
  odds:
    1x2_home: { best: 3.4, books: 13 }
    1x2_draw: { best: 3.6, books: 13 }
    1x2_away: { best: 2.1, books: 13 }
  top_signals:
    - { name: "elo_diff", value: -84, direction: "favours_away" }
    - { name: "form_5_diff", value: -2.4, direction: "favours_away" }
    - { name: "h2h_3y_home", value: 0.17, note: "Home win in 1 of 6 H2H" }
    - ... (top 7-10 only — full signal table is too much context)
  injuries:
    home: ["Saka (doubtful)"]
    away: []
  lineups: "not yet confirmed"
  value_bets:
    - { side: "away", odds: 2.1, model_prob: 0.42, edge: -0.119, decision: "skip" }
    - { side: "over_25", odds: 1.8, model_prob: 0.62, edge: 0.116, decision: "place" }
```

Token estimate: ~400 tokens for context, ~50-100 for question, ~150 for response = ~600 tokens/turn. At Gemini Flash-Lite pricing (~$0.075/1M input, $0.30/1M output), each turn costs **~$0.0001** (a hundredth of a cent). 10-turn conversations = $0.001 each. Even at 1,000 conversations/day, monthly cost is ~$30.

### The hard parts (where this can go wrong)

1. **Hallucination.** LLM might claim Saka is "definitely injured" when our data says "doubtful." Mitigation: post-process the response to ensure all team names, player names, and numbers in the response appear in the context payload.
2. **Regulatory.** Any sentence shaped like betting advice is a problem. The system prompt forbids it; need a denylist scrub on the output.
3. **CLV positioning erosion.** If the chat reads like every other "ask AI" gimmick, we dilute the CLV-first moat. Mitigation: every response footer includes "Reminder: CLV is the metric we publish, not vibes-based confidence."
4. **Cache invalidation.** Match data changes (odds drift, lineups land). Same question 2h later should generate a new response, not return a stale cached one. Cache key includes a `data_version` hash derived from the underlying odds/signal rows.

## What we'd ship

**Backend** (~6-8h):
- `src/app/api/match-chat/route.ts` — auth + Elite gate (copy from `/api/bet-explain`)
- `lib/match-chat/context-builder.ts` — pulls the context envelope from existing tables
- `lib/match-chat/prompt-template.ts` — system + few-shot examples + blacklist scrub
- `match_chat_log` table (migration) — caches by `(user_id, match_id, question_hash, data_version)`

**Frontend** (~6-8h):
- `src/components/match-chat-widget.tsx` — collapsible widget on match-detail page (Elite-only render)
- 5-question pre-canned suggestion strip (no free-form input v1)
- Streaming response display (existing pattern from `bet-explain`)
- "Ask another" button + 5-turn conversation cap

**Smoke + monitoring** (~2-3h):
- Smoke pin for the API route + the Elite gate + the denylist scrub
- Daily ops_snapshot field tracking `match_chat_calls_today` + `match_chat_users_today`
- Alert if a single user >50 calls/day (abuse pattern)

**Total estimate: 14-19 hours / 3-4 working days.**

## Phase 2 (NOT this MVP)

- Free-form text input (after we've calibrated against pre-canned q's for 2-3 months)
- Multi-match queries ("compare today's value bets")
- Live in-play chat (during games) — different data envelope, separate task
- Voice / streaming UX

## Open questions for operator

1. **Elite-only or also Pro?** I'd say Elite-only for v1 — the chat is the kind of premium UX that defines tier value, and 1 active Elite user means costs are bounded.
2. **Pre-canned questions only, or allow free-form?** Strong recommendation: pre-canned only for v1. Reduces hallucination surface ~90%.
3. **Should responses cite CLV explicitly?** Recommendation yes — every response can footnote *"For why the model thinks this matters, see CLV / Performance →"*. Reinforces the moat.

## Recommendation

**Build it after the v2-cohort/verified-ROI window** — same gating as
GROWTH-PRICING-AB and the other "post-OUT-OF-BETA" tasks.

Reasoning:
- Chat doesn't change the product's *substance* — it changes UX richness. The product story (CLV + edge + Telegram) is settled.
- 14-19h of work is real cost when we have 35 signups. Same time would build 2-3 SEO programmatic-prediction pages → directly raise top-of-funnel.
- Once we have N≥200 mature signups AND a verified-ROI claim, chat becomes high-leverage: it reinforces an already-credible product to users who already trust us, and competitors will be doing it anyway.

**File status:** queued in PRIORITY_QUEUE.md as still-Ready. When the gating clears, this doc is the build spec — no further scoping needed.
