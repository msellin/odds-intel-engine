# DeepBetting — Deep-dive synthesis (2026-06-05)

Combines yesterday's strategic teardown + today's API payload findings into a single
actionable doc. Supersedes `deepbetting-api-payload-findings.md` for forward work.

## Executive summary

DeepBetting wins on **trust theater** (third-party Bet-Analytix verification, multi-sport
breadth, French/Brazilian audience reach). They lose on **actual edge**: visible free
picks show **-0.99% ROI over 92 settled bets** at 63.0% hit rate / 1.621 avg odds —
basically a coin-flip product priced at €35-60/mo. New API payload findings expose three
exploitable angles:

1. Their **`/results` endpoint may be callable without auth** — if so, we can scrape
   their entire paid pick history and publish "DeepBetting €60/mo for X% ROI" with receipts
   on `/vs/deepbetting`.
2. Their **`confidence` field (1/2/3)** is exposed publicly. We can backtest whether
   their level-3 picks actually outperform level-1 — if not, it's marketing fluff and we
   can call it out.
3. Their **real pick volume is ~22/day across all sports** (3× higher than the visible UI
   suggests). Marketing claim: "high-volume curator." Reality: spray-and-pray.

## What's new since yesterday's teardown

| Finding | Source | Strategic use |
|---|---|---|
| Free-pick ROI = **-0.99%** (92 settled bets) | Operator's UI paste + my calc | Receipts for /vs/deepbetting and `/compare` content |
| **22 picks/day** (paid+free combined) | `/results` payload, 18 days × 11/day footballl + 11/day US sports | "More picks ≠ better picks" anti-positioning |
| `confidence` (1/2/3) exposed in JSON | API payload | Future backtest: does confidence correlate to ROI? |
| `/results` returns paid pick metadata | API payload | If un-authed, full historical scrape is trivial |
| Multilingual content (fr/en/es/pt) | All analysis fields in payload | Confirms target markets: France, Brazil, LatAm — NOT English-first |
| `forecast_profit` schema (odds/0/1) | Settlement field | Clean ROI math: `sum(profit) - count(won+lost)` |

## ROI snapshot (visible free picks, 2026-05-23 → 2026-06-04)

```
Total picks listed:    100
Settled (won+lost):    92
  Won:                 58 (63.0%)
  Lost:                34
  Push:                6  (stake refunded)
  Postp.:              2  (void)
Avg odds on wins:      1.571
Avg odds on losses:    1.708
Avg odds all settled:  1.621
Total stake (1u flat): 92 u
Net P&L:               -0.91 u
ROI:                   -0.99%
Edge vs implied:       -0.2pp
```

**Interpretation:** Their picks are basically priced fair. They hit 63% on ~1.62 odds when
implied probability says they should hit 63.3%. Zero edge, slightly negative ROI from vig.
This is *also* what DeepBetting's own Bet-Analytix page shows (-3.7%, slightly worse on a
larger sample). They charge €35-60/mo for a product with verified negative ROI.

## Action items (ranked by leverage)

### 1. Test `/results` without auth — 15 min
Open a clean browser tab (incognito, no cookies). Hit deepbetting.io/results or the API
URL the operator captured. If it returns the same payload without auth, we have:
- 400+ settled picks of historical ROI data
- Per-confidence-tier breakdown
- Per-market breakdown (Moneyline / Spread / O-U / BTTS / DNB)

Output: `dev/active/deepbetting-results-scrape.csv` + `scripts/analyze_deepbetting_results.py`.

### 2. Update `/vs/deepbetting` page with the ROI number — 30 min
Current page says "verified track record via Bet-Analytix" in their `whereTheyWin`
section. Honest move: keep that as a "they win on verification" point BUT add a new
oddsIntelWins entry:

> **Their verification reveals a -0.99% to -3.7% ROI.** Verified does not mean profitable.
> Our paper-trading chain (2,686 settled bets in 34 days) is +€340 in May despite a
> -€398 worst-drawdown — we're showing a positive trajectory, not a verified-negative one.

This re-uses the trust-theater finding but with a concrete number. Don't write it as a
"we beat them" — write it as "verification is necessary but not sufficient."

### 3. Add "More picks ≠ better picks" framing to landing — 1h
Most prediction sites publish 5-50 picks/day. We publish 50-200/day *value bets* but
the qualitative claim is different: "every pick has measured edge vs Pinnacle close." A
short paragraph on `/methodology` could land this:

> "DeepBetting publishes ~22 picks/day across all sports. Most prediction sites do
> similar volume. Our 50-200 value bets/day are a different unit: each one is identified
> *because* it has measured edge vs Pinnacle's closing line. We don't pick games — we
> identify pricing inefficiencies. Different product."

### 4. Backtest confidence-tier correlation — 2h
If we get the full /results scrape, compute ROI per confidence level (1, 2, 3). If high
confidence doesn't beat low confidence, write a methodology callout:

> "DeepBetting publishes a 1-3 confidence score on every pick. We tested it: their
> level-3 picks return X%, level-1 returns Y%. The confidence label is uncorrelated
> with outcome."

This is high-leverage *if* the data supports it. It's a direct credibility hit on their
product. If the data shows confidence DOES correlate (their algo works as advertised), we
just don't publish — no harm done.

## Update to /vs/deepbetting (proposed edits)

In `oddsIntelWins`:
```diff
+ "**Their verification surfaces a negative ROI.** DeepBetting's own Bet-Analytix-tracked record shows -3.7% ROI. Verified doesn't mean profitable. Our paper chain is +€340 in May 2026 with worst drawdown -€398 (transparently published).",
```

In `verdict.pickThem`:
```diff
- "...you want a verified Bet-Analytix track record now (not 'on the roadmap')..."
+ "...you want a verified Bet-Analytix track record now (their -3.7% ROI is at least independently confirmed)..."
```

The point isn't to trash them — it's to be honest. They have verification; we don't yet.
Their verification reveals they're a coin-flip product. That's the truth, and we tell it.

## What DeepBetting does that we should consider stealing

1. **Per-pick analysis sentence in 4 languages.** Their `analysis_en` / `analysis_fr` /
   `analysis_es` / `analysis_pt` field is 1 sentence per pick. We have the LLM-explanation
   feature but it's Elite-only. Consider: 1-sentence model rationale on every Free pick
   in the user's language — minimal cost, big trust signal.
2. **Confidence tier as a visible signal.** Users *love* seeing 1/2/3 stars even if it
   doesn't correlate. Our `confidence_score` / Kelly fraction is buried; should be visible
   on every value bet card.
3. **Multi-language SEO play.** Their fr/pt/es content drives Brazil + France traffic
   (per Similarweb). Our SEO is English-only. GROWTH-LOCALIZATION-FR-ES is already
   queued — this reinforces the priority.

## What we should NOT copy

- **Multi-sport breadth as a strategic priority.** Their NBA/MLB/NHL coverage looks
  impressive but the per-sport picks are even thinner (200 multi-sport in 18 days = ~3
  picks/sport/day across 4 sports). Each sport gets a tiny pick set. We're better off
  going deep on football (280+ leagues) than wide and shallow.
- **Tier proliferation (Free/Football/US Sports/Ultimate).** Four tiers create decision
  paralysis (already covered in GROWTH-TIER-SIMPLIFY-SPIKE).

## Open questions for next session

- Is `/results` actually callable without auth? (15-min test)
- Can we estimate their MRR? 20K visits/mo at 2-3% conversion (industry standard for
  prediction sites) × €35-60/mo = ~€14K-36K/mo MRR. Within 1 order of magnitude of where
  we'd be at 200-500 paying users.
- French audience — do they have local partnerships (forum mentions, podcast appearances)?
  GROWTH-PAID-ACQUISITION-CHANNEL-SCOUT should specifically check French betting media.
