# The Odds API (TOA) — Monthly Cost Estimate for OddsIntel

**Date:** 2026-06-05
**Question:** Should OddsIntel subscribe to TOA to complement AF Ultra's 3h-stale Pinnacle?
**TL;DR:** **No, not at the price levels TOA charges for our market mix.** Soccer's three must-have markets (totals, BTTS, AH) are *not* on the cheap `/odds` endpoint — they require the per-event `/events/{id}/odds` endpoint, which billing-wise punishes us once per fixture per poll. At 50–200 fixtures/day × 32 polls/day, even the "Pinnacle-only, h2h-only" minimum scenario lives on the $59/mo 100K tier; the targeted scenario lands at $119/mo; comprehensive needs $249/mo. AF Ultra is already $39/mo and gives 13 books — TOA only buys us "fresher Pinnacle" which their docs openly admit is **scraped from Pinnacle's public website "which may incur a delay"**. We do not recommend subscribing.

---

## 1. Executive Summary

- **TOA billing rule:** `credits = markets × regions` per request (1 region = 10 bookmakers).
- **The trap for soccer:** Soccer `totals`, `btts`, and `spreads` (Asian Handicap) are **not** featured markets on `/odds`. They require `/events/{eventId}/odds`, billed *per event* at `unique_markets_returned × regions`.
- **Pinnacle is EU region** and TOA discloses: *"Odds are from public website which may incur a delay"* — so we are paying for delayed scraped odds, not direct feed.
- **Bet365 is NOT available for soccer on TOA** — coverage limited to AFL/NRL on the AU region.
- **Realistic monthly cost** for the use case you actually want (Pinnacle + 4 books, 1x2 + OU + BTTS, every 30 min): **$119/mo (5M tier)**.
- **Verdict:** Skip it. AF Ultra already does the heavy lifting; TOA's Pinnacle is delayed-scraped anyway; we are CLV-curators not odds-comparators.

---

## 2. TOA Credit Model — Exact Rule (Quoted from docs)

From `the-odds-api.com/liveapi/guides/v4/#usage-quota-costs-2`:

> **GET /odds** endpoint: `cost = [number of markets specified] x [number of regions specified]`
> Examples: 1 market × 1 region = 1 credit; 3 markets × 3 regions = 9 credits.

> **GET /events/{eventId}/odds** endpoint: `cost = [number of unique markets returned] x [number of regions specified]`
> "A count of unique markets in the API response is used." Each event is a separate call.

> **Bookmaker substitution:** "Every group of 10 bookmakers is the equivalent of 1 region."

> **/events listing endpoint:** "This endpoint does not count against the usage quota." (Free fixture enumeration.)

> **Historical odds:** `10 × markets × regions` (10× multiplier).

**Featured markets on `/odds` (cheap path):** `h2h`, `spreads`, `totals`, `outrights` only — but the docs explicitly state: *"`spreads` and `totals` markets are mainly available for US sports and bookmakers at this time."* For soccer, only `h2h` and `outrights` are practically usable on `/odds`.

**Additional markets requiring `/events/{id}/odds` (expensive path):** `btts`, `draw_no_bet`, `h2h_3_way`, soccer `totals`, soccer `spreads` (AH), corner/card markets, double_chance.

---

## 3. Fixture Volume & Polling Math

| Variable | Value |
|---|---|
| Active fixtures/day | 50–200 (use **125 avg**) |
| Polls/day (07-22 UTC every 30 min) | 32 |
| Days/month | 30 |
| Polls/month | 960 |
| Fixture-polls/month (125 × 960) | **120,000** |

Note: a "fixture-poll" is one call to `/events/{id}/odds` for one match at one poll cycle. The `/odds` endpoint is a single call returning all upcoming fixtures for a sport at once — its cost does not scale with fixture count, only with markets × regions.

---

## 4. Pinnacle's Region — and the Bet365 Gotcha

- Pinnacle is in the **EU region**. The EU region also bundles Marathonbet, Unibet (FR/IT/NL/SE), Betsson, Bwin (likely), 1xBet, William Hill (EU), etc. So **requesting region=eu gets us Pinnacle + ~10–15 other EU books in one credit-unit**.
- Or we use the `bookmakers` parameter to filter to a specific list. Per docs, **every 10 bookmakers = 1 region**, so 1–10 bookmakers = 1 region-equivalent.
- **Bet365 caveat (quoted):** *"Only available on paid subscriptions. Coverage currently limited to h2h, spreads and totals for AFL and NRL."* → **Bet365 cannot be fetched for soccer via TOA at all.** This kills the "AF + TOA gives me Bet365 too" story.

---

## 5. Three Scenarios — Math

### Scenario A: **Minimal — Pinnacle h2h only, every 30 min**

Strategy: filter to `bookmakers=pinnacle`, markets=`h2h`, single `/odds` call per poll (no per-event calls).

- Credits per poll: `1 market × 1 region-equivalent (1 bookmaker counts as 1 region for billing)` = **1 credit**
- Polls/month: 960
- **Monthly credits: 960** → fits the **free 500/mo? NO** (overshoots). Needs **20K tier @ $30/mo**.
- Wait — re-check the bookmaker rule: "every group of 10 bookmakers = 1 region". 1 bookmaker is still billed as 1 region floor. Confirmed: 1 credit/poll.
- **Verdict: 20K tier at $30/mo covers it with 20× headroom.**

But this gets us only Pinnacle h2h, no totals, no BTTS, no AH. That's basically useless for OddsIntel.

### Scenario B: **Targeted — Pinnacle + 4 books, 1x2 + OU + BTTS, every 30 min**

Strategy: We need `h2h` (featured), `totals` (event endpoint only for soccer), `btts` (event endpoint only). 5 books (Pinnacle, Marathonbet, Unibet, Betsson, Bwin) = 1 region-equivalent.

- **h2h via `/odds`:** 1 market × 1 region = 1 credit per poll × 960 = **960 credits/month**
- **Totals + BTTS via `/events/{id}/odds`:** 2 unique markets × 1 region = 2 credits **per fixture per poll**.
  - 125 fixtures × 960 polls = 120,000 fixture-polls × 2 credits = **240,000 credits/month**
- **Total: ~241,000 credits/month**
- Cheapest tier: **5M @ $119/mo** (100K is too small).
- **Verdict: $119/mo.**

### Scenario C: **Comprehensive — all EU books, h2h + totals + BTTS + AH (spreads), every 30 min**

Strategy: region=eu (~15 books, billed as 2 regions since >10), 4 markets total.

- **h2h via `/odds`:** 1 market × 2 regions = 2 credits/poll × 960 = **1,920 credits/month**
- **Totals + BTTS + AH via `/events/{id}/odds`:** 3 unique markets × 2 regions = 6 credits per fixture-poll
  - 125 × 960 × 6 = **720,000 credits/month**
- **Total: ~722,000 credits/month**
- Cheapest tier: **5M @ $119/mo** (still fits with headroom — TOA's tiering is steep but generous in this band).
- At 200 fixtures/day worst case: 200 × 960 × 6 = 1.15M credits. Still inside 5M.
- **Verdict: $119/mo, but burn rate uncomfortable near league cup season spikes.**

---

## 6. Tier Recommendation Table

| Scenario | Markets | Books | Monthly Credits | Cheapest Tier | Cost |
|---|---|---|---|---|---|
| A. Minimal (Pinnacle h2h) | h2h | Pinnacle only | ~960 | 20K | **$30/mo** |
| B. Targeted | h2h, totals, BTTS | Pinnacle + 4 | ~241K | 5M | **$119/mo** |
| C. Comprehensive | h2h, totals, BTTS, AH | ~15 EU books | ~722K (1.15M peak) | 5M | **$119/mo** |
| (15M tier is $249/mo — not needed unless we want historical odds backfill, which is 10× multiplier.) | | | | | |

Free tier (500/mo) **does not cover any usage scenario** for OddsIntel — it would burn out in ~16 hours of polling even for h2h-Pinnacle-only.

---

## 7. Hidden Gotchas

1. **Soccer `totals` and `btts` are NOT featured markets** — they require `/events/{id}/odds`, which is per-fixture billing. This is the single biggest cost driver. The pricing page's "1 credit per market per region" copy makes it sound cheaper than it is for soccer.
2. **Pinnacle is scraped, not direct.** Docs explicitly say *"Odds are from public website which may incur a delay"*. We are paying $30–$119/mo for delayed scraped Pinnacle when AF Ultra already gives us Pinnacle (also via scrape, but at $39/mo with 12 other books bundled).
3. **Bet365 not available for soccer.** Only AFL/NRL. Killing one of the key "useful additions" on your list.
4. **`unique markets returned` is unpredictable.** If a bookmaker temporarily lists alternate_totals, alternate_btts, or extra lines, your credit cost balloons. You cannot deterministically cap per-fixture cost.
5. **Live odds = same endpoint, same credits.** No separate live tier. If we ever want sub-minute polling during in-play, credit usage scales linearly.
6. **5M → 15M jump is 2× ($119 → $249).** No middle tier — once we exceed 5M (e.g., add live in-play polling), we more than double our bill.
7. **No annual discount disclosed.** Monthly cancellation allowed per FAQ.
8. **Historical odds = 10× multiplier.** Any backfill experiment burns a tier instantly. (E.g., 1 month of historical Scenario B = 2.4M credits.)
9. **Per-call return size unknown.** No documented page size limit; `/odds` returns "all upcoming events for the sport" in one call — fine for soccer aggregate but the response could be hundreds of KB.
10. **No SLA disclosed.** Free trial = the 500-credit Starter tier, not time-bounded.

---

## 8. Honest Recommendation for OddsIntel

**Do not subscribe to TOA at this time.**

Reasons stacked:

1. **Pinnacle on TOA is also scraped, with a disclosed delay.** Our hypothesis that TOA would give us *fresher* Pinnacle than AF's 3h refresh is weakened by their own admission. At best we get a different scrape with different latency; not a guaranteed improvement.
2. **AF Ultra at $39/mo already provides Pinnacle + 12 other books.** TOA at $30/mo (minimum useful tier) gives us Pinnacle h2h *only* — strictly less data than AF.
3. **The market mix we actually want (h2h + totals + BTTS) costs $119/mo on TOA** because soccer totals/BTTS hit the expensive `/events/{id}/odds` endpoint per fixture. That's 3× our current AF spend for a complementary feed, not a replacement.
4. **Bet365 — the one EU book we'd most want — is not available for soccer on TOA.** Eliminates a major upside.
5. **OddsIntel is a CLV-curator, not an odds-comparison product.** We don't need 15 books shown to users; we need one trusted line (Pinnacle) at low staleness. The right fix for "Pinnacle is 3h stale on AF" is either (a) increase AF polling frequency for the odds job from 3h to 30min — AF Ultra allows it within rate limits, or (b) scrape Pinnacle's public site directly (same source TOA uses), free, with our own poller.

**If we ever do subscribe**, the only defensible scenario is the **20K tier at $30/mo to add Pinnacle h2h cross-checking** as a sanity layer against AF's Pinnacle line — but that's a redundancy buy, not a feature buy.

**Better $30 spend:** add a Pinnacle public-site scraper job to `workers/` (1 file, ~50 LOC, 30-min cron) and route the result through `signals` as a Pinnacle-fresh confirmation flag. Zero recurring cost, same data source as TOA.

---

## 9. Sources

- https://the-odds-api.com/liveapi/guides/v4/#usage-quota-costs-2
- https://the-odds-api.com/sports-odds-data/bookmaker-apis.html (Pinnacle delay disclosure, Bet365 AFL/NRL-only)
- https://the-odds-api.com/sports-odds-data/betting-markets.html (btts/totals/spreads soccer availability)
- https://the-odds-api.com/#get-access (pricing tiers)
- https://the-odds-api.com/liveapi/guides/v4/#get-event-odds (per-event billing rule)
