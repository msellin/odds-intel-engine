# GROWTH-INPLAY-POSITIONING-SPIKE — scoping doc

> Tier A #10 (2026-06-05). Spike output only — do NOT pivot positioning
> from this doc without explicit operator green-light. Purpose: decide
> whether to double down on in-play / live-match positioning, lead with
> in-play on the landing, build a `/live` page, and surface InplayBot
> as a Telegram strategy-trigger flow like InPlayGuru's.

## TL;DR

**Recommendation: build `/live` as a positioning surface AND a real Pro/Elite product, but do NOT pivot the landing's primary positioning away from CLV-first value-bets.**

We have the underlying infrastructure already (~600K live snapshots, 17 active in-play strategies, 874 in-play bets logged). We're not marketing it. A `/live` page + a Telegram in-play alert channel would:

1. Expose the asset already built (no marginal infra cost)
2. Open the door to the EE traffic InPlayGuru owns (UA / DE / HU = 56% of their visits)
3. Strengthen the "spans all 5 competitor tiers" claim in the matrix

But pre-match value bets remain the better business — CLV is verifiable on pre-match in a way that live betting CLV is not. **The play is "in-play is a feature, not a re-position."**

## What we have today

Inventory of the existing in-play capability (state as of 2026-06-05):

| Asset | What it is | Where it lives |
|---|---|---|
| **Live poller** | Tiered polling daemon (30s / 60s / 5min) — fetches scores, odds, stats, events from API-Football during 10-23 UTC | `workers/live_poller.py` + `workers/live_tracker.py` |
| **Live snapshots store** | 598,349 snapshots across 13,374 matches | `live_match_snapshots` table |
| **Live odds chart (FE)** | In-play odds drift chart on match-detail pages | `src/components/live-odds-chart.tsx` |
| **InplayBot ensemble** | 17 active in-play strategies, 874 settled bets, full per-bot ROI/CLV tracking | `workers/jobs/inplay_bot.py` + `bots` table |
| **Live xG signals** | xG-based momentum + dry-spell signals, BTTS-press signals | migration 178 + `match_signals` |
| **Telegram in-play alerts** | Already firing — Pro+ users get "Live value bet" alerts when an in-play strategy triggers | `workers/jobs/inplay_bot.py` line 518 |
| **`/value-bets` live section** | Separate "Live now" panel on /value-bets that auto-refreshes every 60s | `src/app/(app)/value-bets/page.tsx` + ValueBetsLiveSection component |

**Bottom line:** the engine is there. The marketing surface is not.

## Why the in-play niche has real demand

From the competitor research (2026-06-04 teardowns):

- **InPlayGuru: ~1M monthly visits** with "Telegram alerts on live triggers" as the entire product. EE-heavy: Ukraine 24%, Germany 17%, Hungary 15% = 56% of traffic.
- **No US-sports equivalent dominates the same space** the way InPlayGuru owns it in football.
- **In-play markets are structurally less efficient** than pre-match — the bookmaker's price moves second-by-second and lags real-state changes (a goal scored doesn't instantly re-price expectation). That's where the model edge lives.

This is a known niche with one dominant player and a Eastern-European tilt that current US/UK-centric competitors don't touch. We can credibly enter it.

## Three possible directions

### Direction A — Lead with in-play on the landing (NO)

Rewrite the landing H1 around live betting. "Live value bets the moment the model spots them." Lead with the Telegram-during-the-game positioning.

**Pros:** large addressable market; visceral "happening right now" framing converts well; we'd be directly competing with InPlayGuru on their turf.

**Cons:**
- CLV is much harder to compute honestly on in-play (closing line is undefined when the bookmaker keeps re-pricing live)
- Our actual edge is sharpest pre-match where we have more data
- Pivoting positioning right after a 7-task landing refactor is whiplash
- Live betting attracts a more recreational / volatile audience; our quality-first positioning fits worse

**Verdict: NO — would damage the moat we just built.**

### Direction B — Build `/live` as a SECONDARY surface (YES)

Keep the landing's value-bets/CLV positioning. Add a dedicated `/live` page that surfaces the in-play product without making it the primary identity. Treat in-play as a *second* product served from the same platform, not a re-position.

**Pros:**
- Exposes the asset we already built (zero marginal infra cost)
- Opens the door to InPlayGuru's audience without abandoning our existing one
- Reinforces the "we span all 5 competitor tiers" matrix claim — currently the in-play row shows ✅ but no destination
- SEO surface: "AI in-play football", "live football value bets", "in-play scanner"

**Cons:**
- Adds a navigation entry → small decision-tax for visitors
- Need to be honest about in-play CLV (different framing than pre-match)
- Risk of diluting the value-bets primary surface if `/live` looks similar but worse

**Verdict: YES — this is the recommendation.**

### Direction C — Add an "in-play strategy builder" like InPlayGuru's (NOT YET)

InPlayGuru's killer feature is *user-defined strategy triggers* (e.g., "alert me when a team is losing 0-1 in minute 60 with 15+ shots and odds > 2.0"). The user builds the strategy; the alerts fire automatically.

**Pros:** highest user-engagement loop in the entire competitor set. Personalization moat.

**Cons:**
- Substantial product surface (form builder, signal vocabulary, alert routing)
- Risk of user-built strategies being garbage but firing alerts anyway — quality erosion
- Our model picks > user strategies, almost always. Letting users build worse strategies and feel ownership over them dilutes our brand

**Verdict: NOT YET — re-evaluate after `/live` lands and we have usage data on what users actually want.**

## Concrete `/live` page scope (Direction B)

If we build it:

### Sections (top to bottom)

1. **Hero** — H2 *"Live value bets, the moment the model spots them."* Sub-line: *"While the match is happening, the model recomputes edge every 30 seconds and pings you when value appears."* Distinguishes from pre-match: same edge framework, faster cycle.

2. **Currently-live grid** — every active in-play value bet from the InplayBot ensemble, refreshed every 60s. Pre-existing `ValueBetsLiveSection` component handles this — reuse, don't rebuild.

3. **"How it works" — 3 cards** — *In-play data ingest (30s polling)*, *Signal-based strategies (17 strategies)*, *Telegram alert on edge detection*.

4. **In-play track record** — 874 in-play bets logged with ROI/CLV per strategy. Honest about the "in-play CLV is noisier than pre-match" caveat (it really is — closing-line definition is fuzzier when the price re-prices continuously).

5. **Tier gate** — Free users see the page exists + a "what you'd see as Pro/Elite" teaser. Pro gets the live grid. Elite gets the grid + edge % + Telegram delivery.

6. **Footer Telegram CTA** — *"Live alerts straight to your phone — Pro and Elite only."*

### Implementation estimate (Direction B only)

- New route `src/app/live/page.tsx` — 4-6h (mostly reusing ValueBetsLiveSection + adding hero + how-it-works copy)
- Nav link "Live" added next to "Matches" / "Pricing" — 30 min
- In-play track-record query helper (per-strategy ROI/CLV over last 30 days, in-play only) — 2-3h. Probably new function in `engine-data.ts`
- Smoke pin + competitor matrix update (the in-play row now points to `/live`) — 1h
- Landing competitor matrix gets a small update — "Live in-play tracker" row already ticked ✅ for us; can now optionally link to `/live` for proof

**Total: 1-2 days.** Worth filing as `GROWTH-LIVE-PAGE-BUILD` follow-up task.

## Key honest caveats

1. **In-play CLV is harder than pre-match CLV.** Pre-match has a defined "closing line." In-play has a continuously moving line, so the "did we beat the closing price" question is fuzzier. Doc must say this explicitly — don't claim CLV parity with pre-match.

2. **EE traffic isn't a free lunch.** InPlayGuru's UA/DE/HU traffic comes from years of localized content + Eastern European betting culture. Just publishing `/live` in English doesn't capture it. Real EE play requires localization (`GROWTH-LOCALIZATION-FR-ES` is already in Tier D; an EE-language variant would be a sibling task).

3. **In-play attracts recreational users.** Pre-match bettors who care about CLV are mathematically literate; in-play bettors lean more recreational and impulsive. The audience-fit risk is real — we'd attract users who don't appreciate our CLV moat.

4. **The Telegram delivery channel is already live for in-play.** `workers/jobs/inplay_bot.py` line 518 already broadcasts in-play picks to Pro+ users. We don't need to build anything to start delivering value here — just market the fact.

## Recommendation summary

| Question | Answer |
|---|---|
| Should we lead with in-play on the landing? | **NO** — would damage the CLV moat |
| Should we build a `/live` page? | **YES** — exposes existing asset, opens EE door, ~1-2d work |
| Should we add user-defined strategy triggers like InPlayGuru? | **NOT YET** — re-evaluate after `/live` has usage data |
| Telegram in-play alerts? | **Already shipped** — just need to market the fact (covered by `GROWTH-TELEGRAM-FRONT-AND-CENTER` and the new `/live` page) |
| Localization for EE? | **Defer** — `GROWTH-LOCALIZATION-FR-ES` exists in Tier D; EE-language sibling task can be filed when ready |

## Followup task to file

`GROWTH-LIVE-PAGE-BUILD` (1-2 days) — Direction B above. Build `/live` as a Pro/Elite product surface. NOT a landing-positioning change.

**Gating:** Ready now. Could ship before or after the verified-ROI window. Doesn't depend on Bet-Analytix/SBC since in-play CLV is a different conversation.
