# Pinnacle Public-Site Scraper — Feasibility Spike

**Date:** 2026-06-05
**Author:** Spike investigation (no code written, only research)
**Status:** Decision document — read before any implementation

---

## 1. Executive summary

**Verdict: NO-GO as a "free, just ship it" scraper. CONDITIONAL-GO only if we accept ToS risk and budget proper maintenance.**

The technical path is easy — Pinnacle's public site is a Next.js SPA backed by a clean, JSON-only internal API at `guest.api.arcadia.pinnacle.com`, and the data is roughly 2–3 orders of magnitude fresher than AF's 3h cycle (every few seconds vs every 3 hours). The blocker is non-technical: Pinnacle's Terms explicitly prohibit "scraping, harvesting, monitoring, indexing, or otherwise obtaining data… through any automated means," and their official API was deliberately closed to the public in July 2025 — i.e. they have publicly stated they do not want unsanctioned bulk access. Building this would put us in ToS-violation territory for a tool whose entire value prop is provably honest CLV math. Recommendation: do **not** ship a scraper. Instead, write a short outreach email to `api@pinnacle.com` requesting research/handicapping API access (the documented path for our exact use case), and in parallel test whether tightening AF's poll cadence around kickoff buys us most of the freshness win at zero legal risk.

---

## 2. Technical feasibility

| Question | Finding |
|---|---|
| HTML or SPA? | Next.js SPA — `pinnacle.com` HTML is a shell; all odds load via XHR after hydration. |
| Internal JSON API? | **Yes.** Host: `guest.api.arcadia.pinnacle.com`. Verified live endpoints: |
| | `GET /0.1/sports/29/leagues` → soccer leagues list (sport ID 29 = soccer) |
| | `GET /0.1/sports/29/matchups?withSpecials=false&brandId=0` → all soccer fixtures (matchup IDs, kickoff, hasLive, hasMarkets) |
| | `GET /0.1/matchups/{id}/markets/related/straight` → moneyline/spread/total/team_total with `prices`, `limits.maxRiskStake`, `cutoffAt`, monotonic `version` |
| Response format | Clean JSON. Sample below. |
| Auth | `x-api-key` header required for full coverage. Key is embedded in the Next.js bundle on `pinnacle.com/` and rotated periodically. Standard pattern: load homepage in Playwright/Chromium, capture `x-api-key` from outgoing XHRs, persist, reuse for ~24h, refresh on 401. (Sample reference: `pretrehr/Sports-betting`'s `get_pinnacle_token()` uses headless Chrome + 30s sleep.) |
| Anti-bot | **No Cloudflare challenge observed on the API host** during this spike (multiple unauthenticated `WebFetch` calls returned JSON cleanly). The www host does some redirects (http→https→trailing slash). No captcha, no JS challenge encountered on API. Risk: detection could be turned on at any time; status today is not a guarantee. |
| Geo | Estonia (our likely runner location) is **not** in the documented account-restriction list. Pinnacle confirmed elsewhere they do **not** geo-block the website itself, only account creation in certain jurisdictions. EU IPs load the same content as US. Railway runs in `us-west` / EU regions — both should work. |

**Sample response (`/markets/related/straight`):**
```json
[{
  "cutoffAt": "2026-06-06T18:30:00+00:00",
  "key": "s;0;m", "type": "moneyline", "period": 0,
  "limits": [{"amount": 125, "type": "maxRiskStake"}],
  "matchupId": 1631599633, "status": "open",
  "version": 3632162695,
  "prices": [
    {"designation": "home", "price": -130},
    {"designation": "away", "price": 374},
    {"designation": "draw", "price": 204}
  ]
}]
```
Note: `version` is a monotonic counter that increments on every line move — perfect for change detection and staleness measurement.

---

## 3. Terms of Service analysis

**Direct quote (paraphrased from search-surfaced ToS, www.pinnacle.com/en/termsandconditions/curacao was 502 at spike time, will need a human re-read before any go-ahead):**

> "Users may not access, use, or attempt to access the Service, web pages, or APIs through any automated means including bots, crawlers, spiders, scrapers, or data-mining tools for the purpose of copying, collecting, harvesting, monitoring, indexing, or otherwise obtaining data or content from the Service."

> "Use of software or automated systems such as harvesting bots, robots, spiders, and screen scrapers to access or collect information is strictly prohibited and deemed fraudulent activity."

> "Users may not circumvent, disable, or interfere with any security-related features, access controls, rate limits, or technical protections, and cannot create or use any account, API key, or other credential for the purpose of engaging in scraping or unauthorized automated access."

> "Pinnacle reserves the right to suspend or terminate any account or credentials associated with such activity and to pursue all remedies available at law or in equity, including claims for breach of contract and violations of applicable computer misuse and anti-hacking laws."

**Reading:** This is not ToS-grey. It is ToS-black for our use case. Even if we never log in, "harvesting bots… screen scrapers to access or collect information" maps directly onto what a 30-min cron Pinnacle scraper does. The CFAA reference in the last clause is rhetorical but signals their willingness to escalate. Our business model (a paid product partly built on this data) makes us a much more attractive enforcement target than a hobbyist sharing line drops on Discord.

The fact that Pinnacle deliberately closed the public API in July 2025 and now gates it behind `api@pinnacle.com` review for "high-value bettors, commercial partnerships, academics, and pregame handicapping projects" is the loudest possible signal that they do not want what we'd be building.

---

## 4. Existing scraper landscape

| Project | Stars | Last commit | Approach | Maintenance signal |
|---|---|---|---|---|
| `pretrehr/Sports-betting` (Python) | 516 | **Dec 2023 (~2.5y stale)** | Headless Chrome → grab `x-api-key` → hit guest.api.arcadia | Most popular reference impl but unmaintained. No issue/PR mentions of Cloudflare so far → API likely still works. |
| `iliyasone/ps3838api` (Python) | 6 | Mar 2026 | Direct `x-api-key` calls; assumes you have a token | Actively maintained but ultra-low-star → bus-factor 1. Useful as a reference, not a dependency. |
| `Austerius/Pinnacle-Scraper` (Python) | low | ~2020 | Scrapy + Selenium, esports only | Abandoned; don't use. |
| Commercial wrappers (RapidAPI `pinnacle10`, `pinnodds.com`, `sharpapi.io`, `oddspapi.io`) | n/a | Active | All third-party scrapers reselling the same `arcadia` data via paid tiers ($15–$300/mo). | They exist precisely because direct scraping is fragile + ToS-hostile. They're our cleaner alternative if we want this data. |

**Takeaway:** No "just clone this" repo with active maintenance + community fixes. We would own a fork from day one. Pinnacle's site is a moving target (the `0.1` API prefix and `version` field show they iterate), so expect breakage 2–3 times per year.

---

## 5. Integration effort estimate

| Component | LOC | Effort |
|---|---|---|
| `workers/api_clients/pinnacle_public.py` — token harvester (Playwright) + REST client (httpx) + retry/rate-limit | ~150–250 | 0.5–1 day |
| `workers/jobs/pinnacle_public_poller.py` — 30-min cron, walk leagues→matchups→markets, dedupe by `version` | ~80 | 0.5 day |
| Wiring into `odds_snapshots` table (bookmaker_id mapping, `captured_at`, ON CONFLICT) | ~40 | 0.25 day |
| Smoke test (one fixture, assert JSON shape + presence of moneyline) | ~30 | 0.25 day |
| Playwright in Railway runtime (chromium install, ~200MB image bloat) | infra | 0.25 day |
| **Subtotal initial build** | **~300–400 LOC** | **~2 dev-days** |
| Maintenance: scraper breakage 2–3×/yr, each ~2–4h to diagnose+patch | — | **~1 dev-day/yr ongoing** |
| Silent-failure detection: `version` not advancing for >N minutes → page (per `feedback_silent_failures.md`) | ~30 LOC in pipeline_utils | included |

**Total: ~2 days build + ~1 day/yr ongoing**, modest. The cost-research agent's "50 LOC, 30-min cron" estimate is too low — it ignores token rotation, Playwright, error paths, and the silent-failure detection our org-rule demands.

**Hidden infra cost:** Playwright/Chromium in Railway pushes our worker image from ~150MB to ~400MB and adds ~3s cold-start. Manageable but not zero.

---

## 6. Freshness comparison vs API-Football

| Source | Update cadence | Median staleness at kickoff (measured 2026-06-05) |
|---|---|---|
| AF Ultra (Pinnacle line via `/odds`) | Docs say "every 3 hours" | **60 min** (confirmed in our audit) |
| Pinnacle public API (`arcadia`, `version` field) | Third-party measurements: **3–5 seconds** when site is in-tab; we'd poll on 30-min cron so effective is ~30 min worst-case, ~0–5 min if we poll smarter (e.g. T-30/T-15/T-5 before each fixture's `cutoffAt`) | **<5 min** with smart cadence |

The freshness win is real and large. If we polled every fixture's market at `cutoffAt - 5min` once and again at `cutoffAt - 1min`, our CLV reference price would be effectively at-close — closing the entire 60-min staleness gap our audit flagged.

**Caveat — same-infra concern raised in prompt:** Pinnacle's public site and AF both ultimately read from Pinnacle's internal trading engine. If AF were just slow-polling Pinnacle's public site, scraping the same site ourselves would gain nothing. But AF's 3h doc'd cadence and our 60-min measured staleness strongly suggest AF batches by fixture window, not by Pinnacle-side throttling. The public site exposes per-market `version` ticks every few seconds — that's Pinnacle's real cadence. So the freshness gap is genuinely AF-side, and a direct scrape would close it.

---

## 7. Final recommendation

**Do not ship a public-site scraper.** Three reasons, in order:

1. **ToS prohibition is explicit and aimed exactly at this use case.** A paid product whose differentiator is "trusted CLV math" cannot have its core reference price sourced from a ToS-violating scrape. One enforcement letter and we pull the feature; one news story and we look like the LinkedIn-scraper guys.
2. **Pinnacle deliberately closed the public API in July 2025 and offered a sanctioned path: `api@pinnacle.com`** for "pregame handicapping projects" — that is literally us. Cost: probably free or low-fee. Time-to-yes: unknown but worth 1 email. This is the right first move.
3. **Maintenance + silent-failure risk** vs **alternative wins available now**: AF has a "force refresh" endpoint we may not be using maximally; we can also pre-kickoff burst-poll AF (T-30/T-15/T-5) using our existing $39/mo subscription and likely cut staleness from 60min → 15min without touching Pinnacle.com. That's 80% of the freshness win at zero legal risk and zero new infra.

**Recommended next steps (in order):**

1. **Email `api@pinnacle.com`** today — describe OddsIntel as a non-betting analytics product, request research API access for closing-line capture. Template: 6 lines, mention non-US jurisdiction, mention we resell analysis not raw odds. Owner: Margus.
2. **In parallel, run a 1-day spike on AF refresh cadence** — measure whether burst-polling at T-30/T-15/T-5 around each fixture cuts our Pinnacle line staleness from 60min to <15min. If yes, ship that. ~3h of work.
3. **If both fail** (Pinnacle says no, AF burst-poll doesn't help), revisit a paid third-party Pinnacle feed — `pinnodds.com` or `sharpapi.io` are ~$30–$50/mo, take ToS+maintenance off our plate, and are still cheaper than building+running our own scraper. Public-site scraping should be the very last option, not the first.

**If the user insists on shipping the scraper anyway**, gate it behind:
- A signed acknowledgement that ToS risk has been accepted at the business level,
- A separate Railway worker (containment if Pinnacle bans the IP),
- Silent-failure monitoring (`version` not advancing → page within 30 min),
- An "abandon button" — feature flag to disable Pinnacle-as-CLV-source and fall back to AF in <1 minute.

---

## Appendix: artifacts captured during spike

- robots.txt allows everything except `*/account/*` and `*/search/*` — but ToS overrides robots.txt for non-search-engine actors.
- Soccer sport ID = 29.
- Verified live matchup ID 1631599633 returned moneyline `home -130 / draw +204 / away +374`, version 3632162695, cutoff 2026-06-06 18:30 UTC.
- No Cloudflare challenge observed on `guest.api.arcadia.pinnacle.com` during 4 spike requests. (Sample size of 4 is meaningless for production — they could turn it on tomorrow.)
- Pinnacle's public-website mirror-URL strategy and "we don't geo-block the site" stance are well-documented; Estonia access is unrestricted.
