# Task 1 — Where is the profitable edge in soccer betting?

**Date:** 2026-08-28 · **Scope:** web research only. No implementation, no assessment
of our own system (that is Task 2).

A note on sources before anything else. Roughly half the material available on this
topic is published by people selling a tool, a tipping service, or a sportsbook.
Those sources are marked ⚠️ below and their numbers should be read as advertising
until independently checked. The peer-reviewed material is far more pessimistic than
the vendor material, and the gap between them is itself one of the findings.

---

## 1. The consensus starting point: these markets are efficient

The academic literature is close to unanimous that soccer betting markets are hard
to beat and that most claimed systems do not survive contact with costs.

* Market efficiency requires that all information relevant to outcome probabilities is
  already in the quoted odds; the literature broadly finds this holds well enough that
  "no odds-based betting strategy will yield statistically significant long-term
  profits" ([Weak Form Efficiency in Sports Betting Markets](https://myweb.ecu.edu/robbinst/PDFs/Weak%20Form%20Efficiency%20in%20Sports%20Betting%20Markets.pdf)).
* Work on the German top flight found no way to beat the market once betting fees are
  included ([Forecasting soccer matches with betting odds](https://www.sciencedirect.com/science/article/pii/S0169207024000670)).
* A 2025 study of second-by-second in-play markets found volume spikes after goals but
  **no evidence that prices respond to recent team performance** — i.e. the obvious
  in-play "overreaction" trade is not visible in the data
  ([Betting on momentum in contests](https://onlinelibrary.wiley.com/doi/10.1111/ecin.70008)).

**Implication:** the default expectation for any new strategy should be "no edge",
and the burden of proof sits with the strategy. This matches what we found ourselves
this week with double chance and Asian handicap.

---

## 2. Where edge demonstrably does exist — ranked by strength of evidence

### 2.1 Price discrepancy against a sharp benchmark (strongest evidence)

This is the mainstream professional approach and the only one with large-sample
published results. Take a sharp book's price, strip its margin ("de-vig"), treat that
as fair value, and bet wherever a softer book is offering better than fair.

* Pinnacle is the standard benchmark — lowest margin, highest limits, and it
  *welcomes* sharp action to sharpen its own line
  ([Sharpest Sportsbooks 2026](https://valuebetfactory.com/betting-education/sharpest-sportsbooks)).
* A 14-season study of **31,247 EV bets** at level stakes reported **+3.6% realised ROI**
  (against +3.8% expected) from betting soft books when they beat de-vigged Pinnacle
  ([Devig explained](https://www.sharkbetting.com/blog/devig-explained)).
* An independent long-run analysis of Pinnacle pre-closing odds across Europe's main
  leagues, tens of thousands of bets, found level-stakes return **near 3.6%**, close to
  theoretical expectation.
* ⚠️ Vendor-published: Pinnacle Odds Dropper claims 1M+ tracked bets, **5.10% yield**,
  **76% of bets beating the closing line**
  ([POD](https://www.pinnacleoddsdropper.com/)). Treat the level as marketing; the
  *shape* (mid-single-digit yield) agrees with the independent numbers.

**This is the best-evidenced edge in soccer betting, and it is worth roughly 3–5%.**
Note it is not a forecasting edge at all — no model is required. The edge is
*relative pricing*, and the "skill" is execution: speed, book access, and not being
limited.

### 2.2 Markets that receive less pricing attention

Consistent across practitioner and industry sources, though without the large-sample
academic backing of 2.1:

* **Corners, cards, player props** — "corner prices are often softer than goals prices,
  and edges on this market can be larger than the equivalent total goals edge"
  ([Small Markets, Real Edges](https://blog.20bet.com/betting-guide/small-markets-betting-guide-corners-cards-props/)).
  Books dedicate less modelling effort to individual player lines, especially
  mid-table and secondary leagues
  ([Soccer Player Props guide](https://www.betsmart.co/articles/soccer-player-props-betting-guide)).
* **Lower divisions** — books prioritise major competitions; lower leagues get less
  pricing attention, particularly early in the week
  ([Betting Market Inefficiency](https://www.soccertipsters.com/blog-detail/425/betting-market-inefficiency-why-lower-leagues-offer-hidden-value.html),
  [5 Leagues with Inefficient Betting Markets](https://thewagertheorem.com/inefficient-betting-markets-football/)).
* **Counterweight:** CLV as a signal "gets weaker in niche markets like lower-division
  soccer" — if the market itself is thin, beating its close means less
  ([CLV Betting Guide](https://www.sharpfootballanalysis.com/sportsbook/clv-betting/)).
  So softer prices come bundled with a less trustworthy yardstick and lower limits.

### 2.3 Timing — early lines and the team-news window

* Syndicates "attack lines as soon as they open", when pricing is least refined
  ([What Is a Sports Betting Syndicate?](https://www.boydsbets.com/sports-betting-syndicates/)).
* The other window is **60–120 minutes before kickoff**, when confirmed lineups land and
  soft books have not yet repriced
  ([Soccer Betting Reddit: What Actually Works](https://www.sportbotai.com/blog/soccer-betting-reddit-what-works)).
* Soft books mostly *follow* sharp books rather than pricing independently
  ([Soft vs Sharp Books](https://help.outlier.bet/en/articles/9922960-how-sportsbooks-set-odds-soft-vs-sharp-books)),
  which is precisely what creates the lag being exploited in 2.1.

### 2.4 Forecasting models (xG) — real but smaller and fragile than advertised

The most credible recent modelling result, and notable for its honesty:

* Wilkens (2026), **11 Bundesliga seasons**, xG → Skellam → isotonic calibration:
  **~10% ROI at average odds, ~15% at best available odds**, on 567 bets
  ([Can simple models predict football?](https://journals.sagepub.com/doi/10.1177/22150218261416681)).
* **The paper's own caveats matter more than the headline.** It states the results
  "represent an upper bound" and are "indicative of latent signal quality rather than
  readily realisable profits", citing: odds not available at meaningful volume,
  spreads/commissions/slippage, and bookmaker limits. It also reports **away bets at
  −17% ROI**, high season-to-season variability, and warns of overfitting risk from a
  small selective bet count.
* It concedes **bookmaker odds are better calibrated than the model** — the model wins
  only by capturing signal the market has not fully priced, not by being a better
  forecaster overall.

**Read:** a genuine forecasting edge exists in xG-type signals, but published numbers
are upper bounds measured at best-available prices with no execution friction, and the
same paper's honest framing suggests the realisable figure is a fraction of 10%.

### 2.5 Exchanges — structurally different, and the answer to the limits problem

* Betfair-style exchanges charge commission on **net winnings** (from ~2%) rather than
  embedding a margin in every price, and typically show better effective prices than
  bookmakers even after commission
  ([Betting exchange](https://en.wikipedia.org/wiki/Betting_exchange),
  [Exchange vs Bookmaker](https://football-bookie.com/articles/betting-exchange-vs-bookmaker/)).
* Critically: **exchanges do not limit winners**. A winning account generates more
  commission, so it is a good customer rather than a liability — the opposite of the
  bookmaker's incentive
  ([Betfair vs Bookmakers](https://traderline.com/education/exchange-betting-vs-bookmakers)).
* Stakes are bounded by available liquidity rather than by a risk desk, and positions
  can be traded out before or during the event.

---

## 3. The constraint that decides everything: you get limited

This is the recurring theme across every practitioner source, and it is the reason
most published edges are not realisable at size.

* Books track **CLV, not win/loss**, to identify sharp accounts — so a bettor is
  flagged by *how* they bet, before they have even won
  ([Why Sportsbooks Limit Winning Bettors](https://bodog.com/sports-betting/why-sportsbooks-limit-winning-bettors)).
* Limiting hits specific markets first — typically props — then spreads.
* Spreading action across books and varying stake patterns delays limiting but
  "rarely prevents it long-term"
  ([Sharp Bettor's Guide](https://www.bookmakersreview.com/betting/sharpest-sportsbooks/)).
* Market-maker books (Pinnacle, Circa) tolerate sharp action because they use it.

**Consequence:** the 3–5% de-vig edge in 2.1 is real but has a *shelf life* at any
given soft book. The strategy's practical ceiling is set by account longevity and
market access, not by the quality of the model.

---

## 4. Realistic expectations, stated plainly

* **~1–5% of bettors** are profitable long term; ~1% consistently so
  ([What Percentage of Sports Bettors Are Profitable](https://www.boydsbets.com/percentage-profitable-sports-bettors/)).
* **Typical professional ROI is 2–5%.** 4–10% is a good long-run result. Above 10% is
  "exceptional and difficult to sustain".
* **Sample sizes:** under 100 bets ROI is nearly meaningless; 100–300 is directional
  but volatile; **500–1,000+** before an edge claim means anything
  ([Variance in Sports Betting](https://punter2pro.com/variance-sports-betting-explained/)).
* CLV benchmarks: beating the close on **55–60%** of wagers looks promising; a
  sustained **1–2% CLV** edge over hundreds of bets indicates a real advantage.

---

## 5. What the research says the edge is NOT

Worth recording because these are the attractive-sounding dead ends:

* **Not a better score predictor.** Bookmaker odds are better calibrated than published
  models. Edge comes from pricing gaps, not superior forecasting.
* **Not in-play momentum.** Prices do not appear to respond to recent team performance,
  so "the market overreacted to that goal" is not supported.
* **Not favourite-longshot bias in Asian handicap.** AH implied probabilities do **not**
  show the bias, which is why those markets attract informed money
  ([market efficiency literature](https://www.sciencedirect.com/science/article/abs/pii/S2773161824000193)).
* **Not the popular markets.** 1X2 on major leagues is the most efficiently priced
  surface there is.

---

## 6. Summary — the ranked picture

| Edge source | Evidence | Realistic size | Main constraint |
|---|---|---|---|
| De-vig sharp benchmark vs soft books | **Strong**, 31k+ bets, ~3.6% | 3–5% | Account limits; needs multi-book access + speed |
| Exchange trading / lower margin | Strong structural | varies | Liquidity; requires exchange access |
| Small markets (corners, cards, props) | Practitioner consensus | claimed larger | Low limits; weak CLV yardstick |
| Lower leagues | Practitioner consensus | claimed larger | Thin markets; poor data; low limits |
| Timing (open + team-news window) | Practitioner consensus | — | Requires speed and monitoring |
| xG / statistical model | **Peer-reviewed**, ~10–15% | far less in practice | Author-stated upper bound; frictions; limits |

**The single clearest finding:** the best-evidenced edge in soccer is not forecasting
skill. It is **relative price discovery against a sharp benchmark**, worth mid-single
digits, and the binding constraint is execution and account access rather than
modelling quality.

---

*Task 2 (how this maps onto our system) deliberately not started — see the sequencing
the operator set. Task 3 (other sports) not started.*
