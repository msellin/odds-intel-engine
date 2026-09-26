Parent: PRIORITY_QUEUE [[#191]] (OWN line-up), research idea 4. Findings only, no open work.

# OWN research 4: cross-book arbs and near-arbs at the books we can bet (2026-09-26)

Script: `scripts/analysis/own_cross_book_arbs.py`. It is read-only. CSVs go to `data/models/_research/own191_arbs/`.

**Verdict: do not build an arb or near-arb alert.** After phantom boards are removed, the four Estonian books
produce about **5 clean true arbs a day** (17 a day if you also count the "stale-leg" ones). At €50–200 per leg they
are worth **€60–240 a month (clean only)**, or at most **€700–2,800 a month** when the stale-leg ones are counted.
Those stale-leg arbs carry the highest palp and account-limit risk. In **100%** of the non-phantom arbs a single leg
carries the whole edge: on median it sits **+7.6%** above the peer consensus, while the hedge leg is a normal
negative-EV price. So an "arb" here is a one-book value bet plus a hedge that gives most of the value back. That
single-book signal belongs in the OWN SHARP line (exchange or consensus as the anchor), not in an arb product.

## Method (fixed before the run)

- **Window.** Kickoffs from 2026-09-20 to now: 7 days inside the full-resolution retention window (§59/§64).
  Tonybet data starts 2026-09-23 14:00, and the exchange from 2026-09-24.
- **Rows.** `odds_snapshots` for Coolbet, Unibet-Site, Epicbet and Tonybet, pre-match only (`timestamp < kickoff`,
  not live). Quarantined boards are already removed (§79). (match, book) pairs that appear in
  `data_quality_findings` are dropped.
- **Markets.** 1X2, BTTS, DNB, AH and full-time O/U. The key is market plus line, so a quarter line only ever meets
  the same quarter line. O/U 0.5 is dropped (§80). AH is stored from the home side's perspective (§53). A
  convention check shows every book's home probability within ±0.001 of its peers' median on the same AH or DNB
  line, and only 0.3–0.5% of pairs differ by more than 0.15, so the lines really do match.
- **Assembly.** Each book's quotes are assembled with the shared `odds_assembly.assemble` (120 s window; §62/§63).
  At each instant a book counts only if its latest quote is at most **N = 15 min** old. N = 60 min is run as a
  sensitivity check, because Coolbet re-writes a fixture only every ~77 min and Unibet-Site every ~60 min, against
  30 min for Epicbet and Tonybet. The legs must come from at least 2 books.
- **Opportunities.** An opportunity is one distinct (match, line, legs' books, legs' prices). The first run counted
  raw episodes and double-counted the same prices every time a quote aged out and back in: 1,287 episodes collapse
  to 699 opportunities.
- **Phantom guard.** The fair price is the median de-vigged quote of the AF-fed peer books (Pinnacle, Bet365,
  1xBet, …; at least 2 peers, each at most 3 h old). The fallback is the liquid exchange mid, and failing that the
  opportunity is marked "unverified". A leg's edge is odds × p_fair − 1; the leg with the biggest edge is the OFF leg.
  - clean: OFF-leg edge ≤ 5%
  - stale-leg: 5–15%
  - phantom: > 15%
  - **First-run lesson:** judging fairness by the median of the live Estonian books hid Epicbet's flat ~1.87/1.87
    template boards. Example: Landvetter v Torslanda, BTTS yes at 1.89, where eight other books priced it at ~1.52.
    That showed up as a "−9.6% arb, lasting 36 h".

## Results: N = 15 min (primary)

| class | true arb (< 0) | near (0 to 1%) |
|---|---|---|
| clean | 35 | 82 |
| stale-leg | 86 | 60 |
| phantom (> 15% off) | 92 | 22 |
| unverified (no peer, no exchange) | 216 | 106 |

| kickoff day | arbs, all classes | arbs, clean + stale-leg | of which clean | near-arbs, clean + stale-leg |
|---|---|---|---|---|
| 09-20 | 72 | 14 | 3 | 8 |
| 09-21 | 46 | 18 | 2 | 4 |
| 09-22 | 12 | 7 | 2 | 3 |
| 09-23 | 59 | 10 | 3 | 6 |
| 09-24 | 62 | 19 | 7 | 26 |
| 09-25 | 59 | 18 | 5 | 38 |
| 09-26 | 119 | 35 | 13 | 57 |

Clean plus stale-leg true arbs: **n = 121**, across 48 matches.

- **Margin:** p10 / p50 / p90 = −3.2% / **−1.1%** / −0.1%.
- **Markets:** BTTS 42, O/U 38, 1X2 25, AH 16.
- **Book pairs:** Coolbet+Epicbet 50, Epicbet+Unibet-Site 27, Epicbet+Tonybet 17, Coolbet+Tonybet 11,
  Coolbet+Unibet 10.
- **OFF book:** Epicbet in **55%** of cases, then Coolbet 26, Unibet-Site 19, Tonybet 10.
- **Duration.** The median arb survives **22 min**, until the next scrape that breaks it; its median total time held
  is 28 min. That is one scrape interval, so these durations are only a floor set by our scrape cadence. We cannot
  see whether they really last 30 seconds or 30 minutes.
- **Timing.** Median time before kickoff is 5 h; p10 is 26 min.
- **Stale leg?** Only half the time. The OFF leg is the oldest-fetched leg in 50% of cases. Its fetch age is ~0
  (median), but its *value* had been unchanged for a median 5 min and a p90 of **11 h**, so the tail is exactly the
  §88 pattern of a stale value re-fetched. The OFF book re-pricing is what ends 31% of the arbs.

**Near-arbs** (0 to 1%, clean plus stale-leg): n = 142. Mostly AH (59) and O/U (34), with the OFF leg a median
+4.3% above consensus. They are not profitable as pairs. They only help if the OFF leg is taken alone, which is
again a value bet.

**Phantoms are still common after board_guard.** On 09-26 there were 119 raw arbs but only 35 real ones. The
unverified set (no peer coverage) includes "arbs" of **−20% to −48%**, all on Epicbet 1X2 boards. Counting them
would add a fictitious ~€7.4k a month at €50 per leg.

**Sensitivity at N = 60 min:** 203 non-phantom arbs (72 clean) with a median margin of −0.74%. More of them end at
kickoff (23%), so a looser liveness rule mostly adds thinner arbs close to kickoff.

## € a month (non-phantom true arbs)

Profit per arb = cap × min(odds) × (−margin), counting each opportunity once and scaling 7 days to 30.

| staleness allowance | €50 per leg | €200 per leg | clean only, €50 / €200 | turnover a month at €200 |
|---|---|---|---|---|
| N = 15 min | €694 | €2,775 | **€60 / €238** | €187k |
| N = 60 min | €919 | €3,676 | €161 / €646 | €305k |

The yield is about 1.5% of turnover, and that is before any failed legs. Four risks sit on top:

- **Limits.** Taking the off-market side on every arb reads as an arber or sharp profile. Our target books are
  Coolbet, Unibet, Epicbet and Tonybet, all soft Estonian books, and the stale-leg arbs involve a leg that is 5–15%
  above the market. Such books typically limit accounts like this within weeks. That would also cost the OWN SHARP
  line its accounts.
- **Palps.** The stale-leg class, where the OFF leg is 5–15% above consensus and its value was often unchanged for
  hours, is the typical void-on-obvious-error territory. A voided leg leaves the other leg as a naked −EV bet.
- **Leg risk.** Our stored price is between 0 and 77 min old at the moment of the alert. The first leg is placed
  on a price we have not re-confirmed, and in 31% of cases the OFF book is the one that moves next.
- **Capital.** With four funded accounts, €187k turnover a month at a €200 cap is about €6k a day at stake.

## Betfair Exchange side (not placeable from Estonia, reported separately)

This compares a book's back price with the exchange LAY. Liquid means a spread of 5% or less and at least €1k
matched. Both quotes must be at most 15 min old. The data covers 3 days.

- **2% commission:** 63 selections where a book backs above the exchange lay, about 21 a day. Median margin is
  −1.05%. Coolbet accounts for 30 and Tonybet 17; 1X2 for 36. Median lay size is €140.
- **5% commission:** 19 such selections, about 6 a day, with a median margin of −1.6%.

We cannot lay from Estonia, so there is no arb here for OWN. But **a book price above the exchange lay is a value
signal on that one book**. It is the same one-leg edge as above, measured against the only live sharp price we hold
(§88).

## What an OWN alert would need, if it is ever revisited

- **Faster scraping.** Every 1–5 min on the candidate fixture, for both books. Today Coolbet is every ~77 min and
  Unibet every ~60 min per fixture, so we cannot measure real arb lifetimes.
- **Pre-placement check.** A live re-fetch of both legs just before placing, plus each book's max-stake read from
  the betslip.
- **Near-kickoff capture.** The p10 arb is 26 min before kickoff, where the local books are quickest to be off.
- **A phantom and wrong-board guard in the alert itself.** Epicbet 1X2 boards still produce −20 to −48% "arbs"
  after board_guard.

Recommended instead: point the effort at the one-book signal it keeps finding, meaning an Estonian price above the
exchange lay or the peer consensus. That signal is already the OWN SHARP line in `own-bot-lineup-synthesis.md`.
