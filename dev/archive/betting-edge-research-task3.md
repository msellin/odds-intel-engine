# Task 3 — Is soccer the right sport? Research across sports and betting options

**Date:** 2026-08-28 · **Input:** web research + our own history across three sports.

The operator's framing was "maybe soccer isn't the easiest one after all". The research
says that instinct is half right — soccer is structurally one of the *harder* sports to
model — but it also says sport choice is not our binding constraint. Our own history
across CS2 and tennis makes that concrete.

---

## 1. What the research says about picking a sport

### Structural factors that make a sport easier to model

* **Scoring frequency.** Low-scoring sports amplify randomness. NBA averages ~93.6
  scoring events per game against ~8.5 for college football; soccer sits at the extreme
  low end, where "one bounce decides the game"
  ([Scoring dynamics across professional team sports](https://arxiv.org/pdf/1310.4461)).
* **Individual vs team.** Individual sports are more predictable because there are far
  fewer interacting variables — this is the structural argument for tennis over soccer.
* **Sample size.** Sports with tens of thousands of games and millions of scoring events
  support robust estimation; sports with short seasons do not.

On these criteria **soccer is close to the worst case**: low-scoring, team-based, and
famous for one-goal margins. Tennis is materially better on all three.

### Where inefficiency has actually been documented

| sport / market | finding |
|---|---|
| **Tennis (ATP)** | Favourite-longshot bias present and exploitable for statistically significant abnormal profit across 45,813 matches / 18 years ([CBS study](https://research.cbs.dk/en/studentProjects/empirical-study-of-information-efficiency-in-betting-markets-evid/)). A hierarchical Markov model returned **3.8% ROI over 2,173 ATP matches** ([Penn](https://repository.upenn.edu/server/api/core/bitstreams/02989028-f5d6-4fe7-b6a1-016631040e9c/content)). |
| **Esports (CS2/Dota/LoL)** | Both favourite-longshot *and reverse* bias documented ([Li et al. 2024](https://journals.sagepub.com/doi/10.3233/JIFS-232932)). Markets are thinner, move faster, information arrives unevenly — errors are "brief but frequent". |
| **College football / basketball, MLB** | Statistically significant inefficiencies; longshots notably bad. |
| **NBA, NHL** | Inefficiencies **cannot** be demonstrated. |
| **Niche (table tennis, minor tours, lower divisions)** | Operators deliberately keep these because they carry "higher hold" and bettors have less data access; pricing inputs update slower ([LSports](https://www.lsports.eu/blog/beyond-mainstream-betting/)). |

### Prediction markets — a different animal

Polymarket and Kalshi list overlapping events at frequently different prices, and
**neither limits winners**. Cross-platform and platform-vs-sportsbook arbitrage is
reported at 100+ opportunities/day across 990+ matched markets
([ArbBets](https://getarbitragebets.com/blog/best-prediction-market-arbitrage-tools)).
Caveats are serious and non-obvious: fees can eat a 3¢ gross edge entirely, settlement
is USDC-on-Polygon vs USD, and **differing resolution criteria can lose both legs of a
"riskless" trade**. The stated killer is capital logistics, not pricing.

---

## 2. Our own history — we have already tried two other sports

This matters more than any of the above, because it is evidence about *us*.

**CS2 (esports) — built, then deleted two days ago.** 23,910 lines of Python, 163 files,
33 tables, 311 MB, 74 smoke tests. Removed 2026-08-26 (migration 286). Notably it had
been *half*-removed since 2026-08-05, with nothing scheduled for three weeks while
~60 permanently failing smoke tests masked real ones. The research above says esports
is genuinely one of the softer markets — so this was plausibly the right sport
abandoned for reasons unrelated to its edge.

**Tennis — built, ran two weeks, abandoned.** `scripts/tennis/` still holds 16 scripts
including a backtest, value scanner, settlement and a Coolbet placer. It logged **38
value bets** between 2026-06-25 and 07-07 with:

* average edge **+8.63%**
* **average CLV +7.62%** — *positive*, and CLV is the metric that matters
* books used: **`betfair_ex_eu`** (20), williamhill (12), betway (6)
* **result: all 38 still `open`** — never settled, so we never learned the answer

Two weeks and n=38 is far below the n≈78 CLV threshold, let alone 500 bets for ROI. We
abandoned it before it could say anything — and it was showing the right sign.

---

## 3. The finding that matters most is not a sport

The tennis scanner sources its prices from **The Odds API**, and its own docstring says
that API gives *"100% Pinnacle coverage"*. Our tennis rows carry
`fair_source = 'odds_api_pinnacle'` and include **`betfair_ex_eu`** — the Betfair
**Exchange**.

That is the fix for both of Task 2's biggest problems, already integrated in our
codebase:

* **Task 2 Finding 1** — our API-Football "Pinnacle" is not sharp (8% margin vs the real
  ~2–3%; Bet365 matches it on Brier over 2,327 fixtures). The Odds API carries genuine
  Pinnacle.
* **Task 2 Finding 3** — we have no exchange prices, and our "Betfair" rows are the
  Sportsbook (overround 1.0958). The Odds API carries the Exchange.

Current pricing puts Pinnacle and Betfair Exchange on the **Business tier ($99/mo)**;
the free tier is now NBA/MLB h2h only ([The Odds API](https://theoddsapi.com/faq)). Our
tennis code ran on a more generous free allowance in June, so the cost is new — but
$99/mo against a compromised anchor on a real-money bot is not a close call.

**We do not need a new sport to fix soccer. We need the data source we already wrote a
client for.**

---

## 4. Ranked assessment of the options

| option | edge evidence | our readiness | main obstacle |
|---|---|---|---|
| **Fix soccer's anchor via The Odds API** | n/a — fixes existing strategy | **client already written** | $99/mo; adapt tennis client to soccer |
| **Tennis** | Strong: documented bias, 3.8% ROI study, structurally more predictable | 16 scripts, schema, +7.6% CLV on n=38 | needs settlement finished; sample restart |
| **Betfair Exchange as venue** | Strong structural: no limiting, ~2% on net winnings | legal in Estonia; we have client access | account + integration |
| **Esports** | Documented bias both directions; thin fast markets | **just deleted 24k lines** | rebuilding what we removed |
| **Prediction markets** | Arb opportunities reported daily | none | resolution-criteria risk; capital logistics |
| **Niche (table tennis, minor tours)** | Softer by operator design | none | data access is the whole game |
| **NBA / NHL** | Inefficiency *not* demonstrable | none | research says don't |

---

## 5. What I would conclude

**Soccer is a hard sport, but sport choice is not why we are struggling.** We are
struggling because our fair-value anchor is not sharp and we can only place at one
book. Both are fixable without changing sport, and one of the fixes is already written.

**Tennis is the strongest genuine alternative** — better structurally, documented
inefficiency, and our own two-week run showed **positive CLV**. But note what actually
killed it: not evidence of failure, but abandonment at n=38. Restarting it without
finishing settlement would repeat that exactly.

**The pattern worth naming:** we have now built and abandoned three verticals — CS2
(deleted), tennis (dormant at n=38), and soccer's DC/AH extensions (scoped and
rejected in a day). The constraint is not which sport has edge. It is that we start
new verticals faster than we finish measuring the last one. The cheapest possible win
is to finish one measurement to n≈78 CLV before starting anything new.

---

*Research complete. Roadmap next, per the operator.*
