# OddsIntel — Signal UX Roadmap

> How to surface our 58-signal engine in the UI to build trust, drive engagement, and convert Free → Pro → Elite.
> Synthesised from 4 independent UX/product reviews (2026-04-29).
> Tasks tracked in PRIORITY_QUEUE.md (IDs: SUX-1 through SUX-12).

---

## Core Philosophy

**Progressive revelation, not progressive hiding.** Every user sees the same matches. Depth of analysis increases by tier.

| Tier | Experience | Mental State |
|------|-----------|-------------|
| **Free** | "Something interesting is happening" | Curiosity |
| **Pro** | "I understand what's happening" | Insight |
| **Elite** | "I know what to do" | Conviction |

All 4 reviewers unanimously agreed: the biggest opportunity is making our invisible 58-signal engine visible. The signal meter alone changes perceived value from "another odds site" to "an intelligence engine that happens to show odds."

---

## The 3-Layer Signal Visibility Model

| Layer | Free | Pro | Elite |
|-------|------|-----|-------|
| **Signal Existence** | X/58 count + grade (A/B/C/D) | Same + signal group icons colored | Same + full signal breakdown |
| **Signal Data** | Locked with 1-2 teaser hooks per match | Raw signal values, plain-English labels | Raw values + model interpretation |
| **Model Output** | No prediction | Directional lean only (Home/Away/Even) | Exact %, edge %, "Why This Pick" reasoning |

---

## Phase 1 — Signal Meter + Match Pulse (foundation)

**Goal:** Make the engine's work visible on every match card. Zero new data needed — signals already exist.

### 1.1 Match Intelligence Score (SUX-1)

Every match card displays:
- **Signal count:** "42/58 signals" — implies serious computation
- **Grade:** A/B/C/D mapped from data tiers (A=Tier A, B=Tier B, etc.)
- **Visual:** Segmented bar with 6 segments (one per signal group), filled=available, hollow=missing

```
┌─────────────────────────────────────────────────┐
│  Arsenal vs Chelsea          Tomorrow 15:00      │
│  ██ ██ ██ ██ ██ ░░   52/58 signals  ·  Grade A  │
│  1.85  ·  3.40  ·  4.20      [View Analysis →]  │
└─────────────────────────────────────────────────┘
```

**Why it works (all 4 reviewers agree):**
- Anchors perceived value — users see real analytical depth
- Explains confidence variance — Grade A match > Grade D match
- Creates curiosity — "Why is this match Grade A?" → click → hit tier gate

### 1.2 Match Pulse Indicator (SUX-2)

A composite "is this match interesting?" signal on match cards. Values: **Routine / Interesting / High Alert**.

Derived from existing signals:
- `model_disagreement` (high = uncertain)
- `bookmaker_disagreement` (high = market uncertainty)
- `importance_diff` (high = motivation mismatch)
- `steam_move` (present = sharp money)

**Key rule:** Only ~15-20% of matches get a visible badge. If every match has badges, they lose impact. Scarcity makes them compelling.

```
┌─ Arsenal vs Chelsea ────── Tomorrow 15:00 ──┐
│  ⚡ Sharp movement   ·   ⚠️ High uncertainty │
│  Grade A  ·  52/58 signals                   │
│  1.85  ·  3.40  ·  4.20                     │
└──────────────────────────────────────────────┘
```

### 1.3 Free-Tier Signal Teasers (SUX-3)

On notable matches (30-40%), show 1-2 teaser hooks. No numbers, no context — just hooks:

- "Odds shifted significantly overnight"
- "High bookmaker disagreement"
- "Away team declining form"
- "2 key absences confirmed for Away"

**Conversion trigger:** "You're seeing 2 of 42 signals. Upgrade to Pro to see full analysis."

---

## Phase 2 — Match Detail Signal Views (Pro value unlock)

**Goal:** Give Pro users raw signal data organized as an analytical workspace. They explore, form opinions, then want Elite to validate.

### 2.1 Summary Tab — The Killer Feature (SUX-4)

Default view on match detail. Cherry-picks the most interesting signal from each group in plain English. Readable in 30 seconds:

```
Grade A · 52/58 signals

FORM: Arsenal trending up (2.1 PPG, improving). Chelsea declining away (0.9 PPG).
MARKET: Sharp money moved toward Home 2h ago. Bookmakers largely agree.
CONTEXT: Arsenal fighting for title. Chelsea mid-table, nothing to play for.
NEWS: Chelsea missing 2 key players. Arsenal full strength.

[Pro] Directional lean: Home ↑↑
[Elite] Model: Home 58.2% · Edge: +6.1% · [Why this pick →]
```

Most users read this and move on. Power users drill into signal group tabs.

### 2.2 Signal Group Sections (SUX-5)

Accordion cards (not tabs — better for mobile) in priority order:

1. **Key Signals** (summary, always open)
2. **Market** — odds comparison, steam moves, disagreement, volatility, overnight shift
3. **Form & Strength** — ELO, form PPG, form slope, venue splits, rest days
4. **Context** — fixture importance, importance asymmetry, league meta stats, referee tendencies
5. **News & Injuries** — injury count, players out, lineup confidence, news impact
6. **Live** (during match only)

**Tab renaming for clarity** (per reply 4):
- "Team Quality" → "Form & Strength"
- "Information" → "News & Injuries"
- "Model" → not a separate tab for Free/Pro; model output is Elite-only, folded into Summary

### 2.3 Plain-English Signal Translation (SUX-6)

Never show raw numbers without context. Translate everything:

| Raw Signal | Translation |
|-----------|------------|
| `odds_volatility: 0.73` | "Volatile — odds are shifting" |
| `form_slope_home: 0.4` | ↑↑ Strongly improving |
| `form_slope_home: 0.1` | ↑ Improving |
| `form_slope_home: -0.1` | ↓ Declining |
| `bookmaker_disagreement: 0.15` | "HIGH — bookmakers can't agree" |
| `model_disagreement: 0.08` | "Our models strongly agree" |
| `elo_home: 1842` | "Top 15 in Europe" or percentile |
| `fixture_importance: 0.85` | "Title decider" |
| `fixture_importance: 0.15` | "Nothing to play for" |

### 2.4 Signal-Based Conversion Hooks (SUX-7)

**Free → Pro triggers:**
1. Contextual teasers that fire only when genuinely interesting (not every match)
2. "+3 signals updated" badge — Free sees badge but can't see what changed
3. Post-match reveal — one retrospective insight: "Our signals detected sharp movement toward Home 4h before kickoff. Home won 2-0."

**Pro → Elite triggers:**
1. **Model conclusion lock** at bottom of every signal group: "Our model analyzed all 52 signals. See the full probability breakdown." The user just spent time reading signals, forming an opinion — the itch to see if the model agrees is the conversion.
2. **Signal divergence alert**: "Our signals and the market disagree on this match. Elite members can see our model's take." (This IS where edge lives.)
3. **Weekly email**: "You would have found 3 value bets today" — count without revealing which matches.

---

## Phase 3 — Signal Timeline + Engagement

**Goal:** Turn the static match page into a living analysis that builds toward kickoff. The retention play.

### 3.1 Signal Timeline Component (SUX-8)

Vertical stepping-line (like GitHub commit history) showing signal events chronologically:

```
┌─ Signal Timeline ────────────────────────────┐
│  ● NOW                                        │
│  │  Lineups confirmed — prediction updated    │
│  │  Signal meter: 52/58 → 55/58              │
│  ● 2h ago                                     │
│  │  ⚡ Steam move detected on Home Win        │
│  ● 6h ago                                     │
│  │  News scan: "Chelsea confirm Mudryk out"   │
│  ● 14h ago                                    │
│  │  First odds published — 13 bookmakers      │
│  ○ Upcoming                                   │
│     Next odds snapshot in 1h 42m              │
│     Live signals start at kickoff             │
└────────────────────────────────────────────────┘
```

**Tier visibility:**
- **Free:** Timeline structure with event count badges ("4 updates today") but content locked except live events
- **Pro:** All signal updates with values
- **Elite:** How each update affected the model's prediction ("Lineup confirmation moved Home probability from 54.1% to 57.8%")

**"Upcoming" section** is the engagement hook — creates a reason to return.

### 3.2 Signal Delta (SUX-9)

Show what changed since user's last visit:

```
Since you last checked:
+ Steam move toward Away
+ Lineups confirmed
→ Prediction shifted: Home 58% → 52%
```

Creates habit, trust, and engagement. Requires tracking last-visited timestamp per user per match.

### 3.3 Post-Match Signal Reveal (SUX-10)

After settlement, show one interesting retrospective signal to Free users:

"Our market signals detected sharp movement toward Home 4h before kickoff. Home won 2-0."

Low effort, high conversion value — retrospective proof that signals have value.

---

## Phase 4 — Elite Intelligence Layer

**Goal:** Resolve uncertainty. Give Elite users the model's conclusions with full transparency.

### 4.1 "Why This Pick" Reasoning Card (SUX-11)

Natural language summary referencing specific signals:

```
Why Home Win (+4.2% edge):
✔ Market moved strongly toward Home
✔ Home form trending up (+0.8 PPG)
✔ 2 key Away players missing
✔ Model agreement: High (Poisson + XGBoost aligned)
Confidence: High (48/58 signals, Tier A)
```

Explicitly connects: **signals → reasoning → outcome**. This is the trust builder.

Note: BET-EXPLAIN in PRIORITY_QUEUE.md already covers the LLM generation side. SUX-11 covers the UI/UX design and signal mapping.

### 4.2 CLV Tracking Dashboard (SUX-12)

Historical chart of closing line value across all predictions. Shows:
- Running CLV% over time
- Win rate
- ROI if user followed all value bets

Post-match notification: "Your bet beat the closing line by 2.1%." Reinforces that this is an intelligence tool building long-term profitability.

---

## What NOT to Show (all 4 reviewers agree)

### Never Reveal (Any Tier)
- Raw feature weights or XGBoost importance scores
- Exact blending formula between models
- Raw Poisson lambda values
- Training data sources or hyperparameters
- Pre-kickoff edge before odds update (prevents front-running)

### Translate, Don't Expose
- Model disagreement → "Our models strongly agree / see this differently / mixed signals"
- ELO ratings → percentile or "Top N in Europe" (raw 1842 means nothing)
- Form slope → arrows (↑↑/↑/→/↓/↓↓)
- Odds volatility → "Stable market / Volatile"
- Signal contribution (Elite) → relative impact bars, never coefficient values

### Responsible Gambling Guardrails
- No "guaranteed" or "sure bet" language
- Frame edge as "analytical advantage" not "profit opportunity"
- Show losing predictions too — honesty builds trust
- Show Grade C/D with lower confidence — "we don't have enough data" is credible
- Persistent responsible gambling link
- No flashing colors, countdown timers on odds, or "BET NOW" patterns

---

## Differentiators vs. Competitors

All 4 reviewers identified these as unique positioning:

1. **Signal Transparency** — Showing WHY a prediction exists, not just what it is. Rare in the industry.
2. **Match Intelligence Score** — No competitor has a visible "analysis depth" indicator. Brand asset.
3. **Living Analysis** — Signal timeline that evolves toward kickoff vs. static predictions posted once.
4. **Honest Uncertainty** — Showing Grade C/D with lower confidence. Counterintuitively builds trust.
5. **Post-Match Learning Loop** — CLV tracking + retrospective signal analysis closes the feedback loop.

---

## Reviewer Consensus Matrix

| Feature | Reply 1 | Reply 2 | Reply 3 | Reply 4 | Verdict |
|---------|:-------:|:-------:|:-------:|:-------:|---------|
| Match Intelligence Score | ✅ | ✅ | ✅ | ✅ | **Do — Phase 1** |
| Match Pulse / Interest indicator | ✅ | — | ✅ | ✅ | **Do — Phase 1** |
| Summary tab (key signals) | ✅ | ✅ | — | ✅ | **Do — Phase 2** |
| Signal group sections | ✅ | ✅ | ✅ | ✅ | **Do — Phase 2** |
| Plain-English translations | — | ✅ | — | ✅ | **Do — Phase 2** |
| Free-tier teasers (scarcity) | ✅ | ✅ | ✅ | ✅ | **Do — Phase 2** |
| Signal Timeline | ✅ | ✅ | ✅ | ✅ | **Do — Phase 3** |
| Signal Delta ("what changed") | — | ✅ | — | — | **Do — Phase 3** (unique, high value) |
| Post-match signal reveal (Free) | — | — | ✅ | ✅ | **Do — Phase 3** |
| "Why This Pick" reasoning | ✅ | ✅ | ✅ | ✅ | **Do — Phase 4** |
| CLV dashboard | ✅ | ✅ | ✅ | ✅ | **Do — Phase 4** |
| Signal contribution chart | ✅ | — | — | ✅ | **Defer** — risks exposing methodology |
| Push notifications for signals | ✅ | ✅ | ✅ | — | **Defer** — after core UX is built |
| Match Momentum live chart | — | — | ✅ | — | **Defer** — nice-to-have for live |
| Gamification (badges, streaks) | ✅ | ✅ | — | — | **Skip** — risks feeling like gambling site |

---

## Dependencies

- **Phase 1** has no blockers — signal data already exists in `match_signals` table
- **Phase 2** needs tier-aware data API (B3 in PRIORITY_QUEUE) to gate content by subscription
- **Phase 3** needs signal event logging (timestamp when each signal was computed/updated)
- **Phase 4** needs Stripe integration (STRIPE/F8 in PRIORITY_QUEUE) for Elite tier to exist
- **SUX-11** ("Why This Pick") builds on BET-EXPLAIN (#33) — share the LLM prompt work
