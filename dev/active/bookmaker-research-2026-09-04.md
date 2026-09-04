# Bookmaker research — Estonia — 2026-09-04

Commissioned to answer: which books can we reach from Estonia, what do they
cover, how good are their odds, do they complement Coolbet, and how automatable
are they for (a) odds fetching and (b) placing bets.

## Read this first — three limits on what follows

1. **The research browser geolocates to Riga, Latvia — not Estonia.** Every
   reachability result below is Latvian, and geo-blocking / licensing gates can
   differ. `betano.ee` returned a Cloudflare 520 from here; that may or may not
   be what you see. **Reachability from Estonia must be confirmed by you.**
2. **No accounts were created, no logins, no deposits, no bets.** So
   bet-placement automation is assessed from observable auth mechanics only,
   never end to end.
3. **Trustpilot is close to useless for this sector.** Every operator scores
   1.3-3.5, and the complaint mix is dominated by KYC delays and losing
   players. It is included for relative context only.

The strongest evidence here is not from browsing at all — it is from our own
`odds_snapshots`, which already holds live prices from most of these books.

## 1. Complementarity with Coolbet — measured, not estimated

Last 45 days, 1X2 + O/U 2.5, latest pre-kickoff quote per book. **Coolbet prices
24,331 of 55,223 outcomes — 44%.** Everything else is a coverage gap.

| book | outcomes | shared w/ CB | **CB-gap** | beats CB | avg diff | best-of-2 uplift |
|---|---|---|---|---|---|---|
| 1xBet | 48,280 | 19,749 | **28,531** | 36% | **+0.56%** | **+3.79%** |
| Marathonbet | 48,117 | 19,855 | **28,262** | 29% | −0.97% | +2.61% |
| Betano | 43,088 | 18,717 | 24,371 | 39% | −0.78% | +2.48% |
| Superbet | 41,255 | 17,476 | 23,779 | 36% | −0.94% | +2.55% |
| Unibet | 37,844 | 17,142 | 20,702 | 29% | −2.30% | +2.60% |
| 10Bet | 35,917 | 15,916 | 20,001 | 34% | −0.78% | **+2.82%** |
| 888Sport | 10,205 | 5,117 | 5,088 | 23% | −2.96% | +1.78% |
| Epicbet | 6,113 | 3,054 | 3,059 | **47%** | **+1.19%** | **+3.71%** |

**CB-gap** = outcomes that book prices and Coolbet does not — pure coverage gain.
**uplift** = extra return per stake from taking best-of-two instead of Coolbet alone.

### What this actually says

- **Coolbet is the better-priced book.** Every mainstream book has a *negative*
  average difference against it — they are worse on the typical shared outcome.
  The positive uplift comes from taking the maximum, which is a selection
  effect, not a sign the other book is sharper.
- **The value is coverage, not price.** Betano and Marathonbet each roughly
  double the number of outcomes available. That matters more than the ~2.5%
  uplift, because 56% of outcomes have no Coolbet price at all.
- **Epicbet has the best prices per outcome** (beats Coolbet 47% of the time,
  the only EE-licensed book with a positive average diff) **and the worst
  coverage** — 6,113 outcomes against Coolbet's 24,331. This is why the +2.50%
  uplift headline did not move the aggregate: it applies to 3.3% of fixtures.
- **1xBet is the single best complement on the numbers and is not
  EE-licensed.** Noted for completeness, not as a recommendation.

## 2. Reputation — relative only

| book | Trustpilot | reviews | note |
|---|---|---|---|
| Unibet | 3.5 | 2,369 | best of the set |
| Tonybet | 3.5 | 1,698 | *paid Trustpilot subscription* |
| Optibet Eesti | 2.7 | 5 | too few to mean anything |
| **Coolbet** | **2.1** | 343 | complaints are KYC/withdrawal **speed**, not limiting |
| Paf | 1.6 | 99 | |
| Betano | **1.3** | 499 | see below |
| OlyBet | — | — | no Trustpilot profile found |

**The one genuinely useful review**, on Betano: *"Minimum limits immediately
after a few bets… and even if you lose, they will give you limits."* If accurate,
Betano limits aggressively — which is the axis you actually care about, and it
is the book with the second-best coverage.

Coolbet's low score is not about limiting. Its complaints are verification and
withdrawal latency. That is consistent with your read that it can be trusted on
the dimension that matters here.

## 3. Automation — two very different questions

### (a) Odds fetching

| book | mechanism | difficulty | note |
|---|---|---|---|
| Betano, Unibet, Marathonbet, 10Bet, 888Sport, Superbet | **already ingested via API-Football** | **none — we have them** | see §4 |
| Epicbet | tRPC-style REST, `?input=<url-encoded JSON>` | done | `epicbet_explorer.py`, needs FlareSolverr from the VPS |
| Coolbet | REST + Imperva | done | Mac daemon, per-session cookies |
| **OlyBet** | **WebSocket** (`Websocket.worker.js`, `useWebsocketClient.js`), sportsbook in an iframe at `/en/sportsbook/en` | **hard** | push-based; efficient once connected but no simple REST endpoint to poll |
| **Optibet** | SPA (webpack chunks) behind **Cloudflare challenge-platform**; odds calls not observable without accepting the consent wall | **unknown** | needs a session with consent accepted to map |

### (b) Placing bets — this is where it gets decided

**OlyBet's login methods are Smart-ID, Mobile-ID, Google and OlyBet
credentials.** Smart-ID and Mobile-ID are Estonian eID and **cannot be
automated** — they require a physical device confirmation per authentication.
If OlyBet forces eID for an Estonian account, automated placement is off the
table regardless of how good the odds are.

This is the single most important automation finding in this document, and it
likely generalises to the other Estonian-native books. **Coolbet working with a
username/password session is probably why the placer exists at all.**

## 4. The finding that reframes the whole exercise

**We already receive Betano, Unibet, Marathonbet, 10Bet, 888Sport and Superbet
prices through API-Football.** Every coverage and price number in §1 comes from
that feed. So for the two books worth having, *scraping is not needed for odds*.

What is unverified is whether the AF-fed price equals the price on the real
`.ee` site. `BET365-EXECUTION-AUDIT-2026-08-21` found exactly that failure — AF
Bet365 odds were systematically inflated, CLV +10% against ROI −10%, and Bet365
was removed from the placeable set. **Betano and Unibet have never been
checked.** That audit is 2h and gates everything else — see
`UNIBET-BETANO-DIRECT-SCRAPERS` step 0.

## 5. Recommendation

1. **Run the AF-feed trust audit** (step 0) before building any scraper. If
   Betano/Unibet look like Bet365 did, they leave the placeable set and every
   published figure moves again.
2. **Answer the reachability question yourself** for Marathonbet, 10Bet,
   888Sport — they sit in `ACCESSIBLE_BOOKMAKERS` on the same unverified
   assumption Pinnacle did, and Marathonbet covers 99% of fixtures we bet.
3. **Do not build an OlyBet or Optibet integration yet.** WebSocket odds plus
   probable eID auth is the worst automation profile in the set, for books whose
   prices we cannot even evaluate because they are not in our feed.
4. **Treat Betano as odds-only, not a placement venue**, unless you can
   disprove the limiting report.
5. **Coolbet remains the placement venue.** Nothing found here displaces it.

## 6. Still open

- Reachability from Estonia (you)
- Whether OlyBet/Optibet accept password-only auth (needs an account — yours)
- Optibet's odds API (needs a consented session)
- Actual account limits at Betano/Unibet at your stake sizes

---

## 7. Addendum — "but Coolbet doesn't price 56% of outcomes, so wouldn't another book give us gate-clearing bets there?"

Good question, and the right one to ask of §1: coverage only matters if it turns
into *bettable* opportunities, not just data. Tested directly.

Universe: 9,034 fixtures since 2026-06-01 with a model probability and O/U 2.5
prices. **Coolbet prices 2,819; it does not price 6,215.** For that gap, applying
the same gate the placer uses (model edge >= 3%, i.e. `odds >= (1+edge)/prob` —
the `min_odds` rule):

| book — gap fixtures only | gate-clearing bets | ROI | t |
|---|---|---|---|
| Marathonbet | 3,759 | −6.86% | −3.99 |
| Superbet | 2,999 | −6.61% | −3.44 |
| Betano | 2,985 | −7.48% | −4.01 |
| 10Bet | 2,765 | −6.96% | −3.52 |
| Unibet | 2,726 | −6.58% | −3.28 |
| 888Sport | 781 | −2.43% | −0.64 |
| **best-of-all-seven** | **4,307** | **−4.46%** | **−2.73** |
| *Coolbet, on fixtures it DOES price* | *1,849* | ***−8.40%*** | *−3.37* |

**The extra coverage does not convert into profitable bets.** But the last row is
the point: **the naive gate loses money on Coolbet's own fixtures too, and by
more.** The gap fixtures are not uniquely bad — the 3% edge gate is simply not
profitable on its own, anywhere.

This matches the earlier `EVENT-DRIVEN-BETTING` result (betting every
gate-clearing O/U fixture returned −4.65%). Our live bots return **+8.19%** not
because the gate is good but because the bot configs, odds ranges, league filters
and cohort timing reject 94.7% of what clears it.

**So: adding books would add losing bets unless the full bot logic is applied to
the new fixtures too.** That is not tested here and is the only version of this
idea still open — but there is no evidence yet that the selective filters
transfer to leagues Coolbet does not cover, and some reason to doubt it, since
those filters were tuned on the fixtures we do bet.

**Revised recommendation:** the case for a second book is *price improvement on
fixtures we already bet* (+2.5% uplift, §1), not *new fixtures*. That is a
smaller prize than the coverage numbers suggest.

---

## 8. "Would adding Unibet automation help?" — the decision table

The only version of the second-book case that survives §7 is *price improvement
on bets we already place*. Measured directly: our 1,523 settled non-retired
pre-kickoff picks, ROI **+8.31%** (t=+2.40) as priced. What each book would add
if we could always take the better of the two:

| add this book | prices our picks | beats our price | new ROI | **gain** |
|---|---|---|---|---|
| **Marathonbet** | 1,494 / 1,523 | 17% | +9.89% | **+1.58pp** |
| **Betano** | 1,406 | 20% | +9.62% | **+1.30pp** |
| Superbet | 1,294 | 20% | +9.28% | +0.97pp |
| **Unibet** | 1,289 | **11%** | +9.12% | **+0.81pp** |
| 10Bet | 1,111 | 10% | +8.86% | +0.54pp |
| *all seven* | — | — | *+10.86%* | *+2.55pp* |

**Unibet specifically: +0.81pp — real, but the weakest of the four mainstream
candidates.** It beats our price on only 11% of picks, half as often as Betano
or Superbet, which matches its −2.30% average price difference in §1. Unibet is
the *best-reviewed* book in the set and the *worst-priced* one.

**Marathonbet is the best single addition (+1.58pp)** — best coverage of our
picks *and* a good hit rate. But its Estonian licensing is exactly what
`ACCESSIBLE-SET-VERIFY` is open on.

**Three reasons the gains above are upper bounds:**
1. They assume the AF-fed price equals the real site price — unverified, and the
   Bet365 failure mode was exactly this.
2. They assume no account limits. Betano is reported to limit aggressively after
   a few bets; Unibet is Kindred, which has the same reputation.
3. They assume perfect execution at the better price, with no latency loss.

**Verdict: adding Unibet alone is not worth a scraper.** +0.81pp against 1-2 days
of build plus ongoing maintenance and breakage. If one book is added, it should
be Marathonbet or Betano — and only after step 0 (AF-feed trust) and
`ACCESSIBLE-SET-VERIFY` come back clean.
