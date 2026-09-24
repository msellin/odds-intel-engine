# COOLBET-SCRAPE-FEASIBILITY — Research

**Status:** initial framing complete. Final decision needs ~30 min of network-tab inspection by user (instructions below).

## TL;DR

Modern sportsbooks like Coolbet are SPA front-ends backed by JSON APIs. The odds you see in the browser were fetched from one of those APIs — meaning there's almost certainly an undocumented but stable JSON endpoint we *could* query. The question isn't really "is it technically possible" (it almost always is), it's:

1. **How fragile** is the endpoint? (Auth-required? Tied to session cookies? Rate-limited?)
2. **What's the legal/TOS risk**, and does it matter for our personal-use scope?
3. **How much engineering** for a maintainable integration?

Until we inspect the network tab on Coolbet, we can't answer (1). The other two we can reason about now.

## Architecture (informed assumption)

Coolbet runs an in-house platform (per the INPLAY-AUTO-ESTONIAN research — they explicitly aren't on Kambi). In-house ≠ open API, but in-house typically means:

- **Frontend** is a single-page app (React/Vue/Angular) that fetches odds via XHR/fetch calls returning JSON
- **Backend** for the betting menu is some REST or GraphQL service — usually a small number of endpoints serving the bulk of the homepage / sportsbook view
- **WebSocket** for live odds updates (faster than polling REST)

For pre-match odds, REST/GraphQL is the most common pattern. Pre-match odds update every few minutes; not the same urgency as in-play. They're typically pulled from `/api/v1/...` or similar on the same domain or a subdomain like `api.coolbet.com`. Sometimes a CDN-cached static JSON file.

## Three viable engineering approaches

| Approach | Effort | Maintenance | Risk |
|---|---|---|---|
| **A. Direct JSON endpoint scrape** | 1-2 days | Low if endpoint is stable | Medium — endpoint URL or schema can change; account flagging unlikely if rate-limited |
| **B. Headless browser (Playwright)** | 3-5 days | Higher — browser updates, DOM changes | Higher — looks more like a bot, easier to detect; resource-heavy |
| **C. Hybrid (auth via browser, then JSON via session)** | 2-3 days | Medium | Medium — best of both, but session expiry handling adds complexity |

**Recommendation if we proceed:** start with Approach A. The vast majority of public sportsbooks expose their pre-match odds through a JSON endpoint that returns a clean structured response. The first ~30 min of Phase A research will tell us if A is viable.

## Risk profile

Real-money personal use changes the risk calculus a lot:

- **TOS violation:** Most sportsbooks' TOS prohibit "automated access" / "scraping." But we'd be using the data to *inform our own placements at Coolbet* — not redistributing or commercial-scraping. Different legal framing than commercial scrapers.
- **Account flagging:** Coolbet would likely flag accounts that hit their odds endpoints from a non-browser User-Agent at high rate from a server IP. Mitigations: realistic UA string, polite rate-limiting (1 request / 30s for pre-match is plenty), residential proxy if needed (probably not).
- **IP blocking:** Less likely for low-volume polling than for high-volume scraping. Coolbet doesn't see large-scale residential traffic from EU sportsbook scrapers because the Estonian retail market is tiny.
- **Detection vectors:** TLS fingerprint, browser cookie behaviour, request timing. Approach A might be flagged on first request; Approach C basically can't be flagged by these vectors.

Net: **medium risk if cautious, high risk if careless.** The downside is "Coolbet flags the betting account, asks you to remove the integration, possibly restricts the account." Worth it only if Coolbet odds give us a meaningful edge that isn't already approximated by AF's existing book set.

## What needs to happen for a real go/no-go

User does ~30 minutes of network-tab inspection:

1. Open Coolbet sportsbook in Chrome
2. Open DevTools → Network tab, filter "Fetch/XHR"
3. Click on a featured football match
4. Note the XHR calls that fire — request URL, request headers (especially auth-related ones), response body structure
5. Click on a different match — do the same calls fire, or are they cached?
6. Refresh the page — do the calls fire on every refresh or are they cached?
7. Save 2-3 example response JSONs to disk for the engineering side

What we're looking for:

- Is the odds endpoint **unauthenticated** (works without a logged-in session)? If yes → Approach A is easy.
- Is the response **JSON** (vs. server-rendered HTML)? If yes → cheap to parse.
- Does the endpoint return **all leagues' odds in one call** or **one match at a time**? If all-in-one → 1 call/minute is plenty; if per-match → we'd need ~50-100 calls per refresh cycle, which is a much heavier polling profile.

Once we have that information, the rest is straightforward engineering. Write up the findings here, and I can sketch the integration in another 1-2 hours.

## Decision tree

```
network tab inspection reveals:
├─ Unauthenticated JSON endpoint, all leagues in one call
│   → Approach A, low risk, ~1 day build, ship as COOLBET-SCRAPE-BUILD
├─ Authenticated but session-bearer, JSON
│   → Approach C, medium risk, ~2 day build
├─ Per-match endpoints, many calls
│   → Approach A still works but polling is heavier (1 req/3s budget)
│   → Worth it only if odds clearly differ meaningfully from AF books
└─ DOM-rendered, no JSON endpoint
    → Approach B (Playwright) — last resort, high maintenance
    → Only if Coolbet odds are dramatically different from AF
```

## Alternative — accept what we have

We currently measure **executable CLV** via `scripts/coolbet_clv_report.py` (shipped same day). First run: mean +0.68%, median -0.72% — i.e., not strongly beating Pinnacle's close at Coolbet's prices. Two interpretations:

1. **Coolbet's pre-match odds are very close to Bet365/Pinnacle**, so the marginal information value of scraping Coolbet pre-match is small.
2. **We're systematically picking from a -EV slice of Coolbet's menu** because our model uses AF's best price (Bet365/Pinnacle) which is sometimes better than Coolbet's price. Knowing Coolbet's actual price *would* let us skip bets where Coolbet doesn't match the price we modelled on. That's the real value of scraping.

The second interpretation is the operative one. If we don't scrape, we'll keep occasionally placing bets that look +EV on Bet365 but are -EV on Coolbet because Coolbet's price is meaningfully worse.

So the scrape's value lies specifically in **filtering out bets where the Coolbet price has drifted away from Bet365's** before we manually place them. That's a meaningful operational improvement — but it's roughly worth "the % of bets where Coolbet diverges from Bet365 by more than the model's edge" × "average loss avoided." Without data on the Coolbet/Bet365 divergence rate, we don't know if this is worth €5/month or €200/month.

## My recommendation

1. **Do the network-tab inspection (~30 min)** to find out if Approach A is viable
2. **Run COMBO-RESEARCH-PHASE-A in parallel** (~2h, the other research task) — it tests Coolbet's combo/SGM pricing which uses the same site inspection workflow
3. **Decide based on findings:** if Coolbet exposes a clean JSON endpoint AND COMBO-A reveals SGM mispricing, the case for scraping Coolbet jumps significantly because the scrape feeds both single-bet filtering AND SGM enumeration. If neither pans out, the marginal value of the scrape stays small and we'd just keep the executable CLV report as the diagnostic and not build the integration.
