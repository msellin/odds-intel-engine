# #191 OWN research idea 1 — PROMO EV (2026-09-27)

**Question.** Are bookmaker promotions at the Estonian books we can bet (Coolbet, Unibet-Site, Epicbet,
Tonybet; Paf / Olybet / Optibet as candidates) a structural, repeatable +EV source for OUR money?
Public tipsters cannot use them, so any edge here is 🤖 OWN only.

**Short answer. Yes, and it is the only OWN lever whose sign follows from arithmetic rather than a model.**
It is small, variance-heavy and bounded by account restrictions. Realistic recurring EV at books we already
bet is **€150–500/month**. That estimate rests mostly on ONE promo, the Coolbet Football Combo Club
(Jalgpalli Komboklubi), which was never catalogued. Olybet could add **€100–450/month** if its daily
profit-boost challenge is offered to the owner's account, and one-off welcome offers add **€150–250 once**.
Compare the model/sharp track, where the audit's expected value is ≤ 0 (OWN_STRATEGY_AUDIT §5).

Method: read-only on the DB and code; public promo pages read logged out in the browser pane (cookies
declined, no login, no terms accepted). Every € figure below is an estimate. The inputs are measured
(margins, free-bet conversion, leaderboard counts) or read from the book's own T&Cs. Participant counts
are inferred from public leaderboards. Nothing here has been realised.

---

## 1. What we already hold

| Asset | State |
|---|---|
| `promo_terms`, `promo_ledger` (mig 356, 359) | Built 2026-09-15 (OWN Phase 2). **Both tables have 0 rows.** Nobody has entered a promo and nothing has been taken. |
| `workers/automation/promo_ev.py` | Formulas for odds boost (profit / full-odds), SNR / SR free bet, acca insurance and deposit bonus with rollover, all under stated terms. `consensus_fair_prob` Shin-de-vigs ≥4 fresh reference books, excluding the promo's own book. Sound; reused below. |
| `scripts/promo_ev.py` (fair / ev / terms / add-terms / --record), `scripts/promo_review.py` (monthly realised-vs-ΣEV kill rule) | Built and never used. |
| Web: `src/components/shadow-bots/promotions.tsx`, `src/lib/admin-money.ts` `loadPromos()` | Promotions panel on `/admin/bots?section=money`. It renders the empty state. |
| Boosted markets in `odds_snapshots` | **None.** No bookmaker or market name matches boost / enhanced / super. None of the Coolbet, Unibet-Site, Epicbet, Tonybet or Optibet feeds parse a boosted or special price (Optibet explicitly drops `isSpecialBets`). |
| `real_bets.combo_legs` | Exists, so the combos these promos need can be recorded. |

**Gap.** The Phase 2 plumbing is complete but has no inputs. Its design also assumed the value sits in odds
boosts and free-bet tokens. It does not. The largest recurring items are **turnover-for-free-bet clubs** and
**streak jackpots**, which `promo_ev.py` has no formula for yet (§4).

## 2. What history lets us measure (the promo-independent inputs)

Source: latest pre-kick-off price in the last 3 h, over the last 8 days (52,683 rows), for 1X2, O/U 2.5 and
BTTS. Each price is scored against Pinnacle de-vigged with `workers.model.devig`. The top-5-league cut uses
35 days, with the last price in the 6 h before kick-off.

**Book margins (median overround, all leagues):**

| Book | 1X2 | O/U 2.5 |
|---|---|---|
| Coolbet | 7.8% | 8.0% |
| Epicbet | 7.8% | 6.9% |
| Unibet-Site | 8.6% | 7.1% |
| Tonybet | 10.0% | 9.0% |

**Top-5 leagues (EPL, LaLiga, Bundesliga, Serie A, Ligue 1), 35 days:** Coolbet and Epicbet charge a
**3.0%** margin. The average selection loses 3.8% at Coolbet and 4.2% at Epicbet. The **best selection per
match loses 0.3% at Coolbet and gains 0.2% at Epicbet**. Unibet-Site charges 5.9%, with the best selection at
−2.4%. This matters because several promos require top-5 legs.

**Free-bet conversion.** For a stake-not-returned (SNR) token the expected cash per €1 of face value is
p×(o−1). It was measured at our four books' own prices against the Pinnacle fair price:

| Odds band | 1.5–2 | 2–3 | 3–5 | 5–10 |
|---|---|---|---|---|
| Conversion (all 4 books) | 39–40% | 51–55% | 65–68% | 75–78% |

At odds 4–8 the conversion is **71.6–74.3%**. So a SNR token is worth about **0.72 × face** at our books
before any wagering condition.

**Profit boosts.** A boost on the profit part breaks even at **+12% to +16%** (median, odds 2–10) at
Coolbet, Epicbet and Unibet-Site, and at +16% to +19% at Tonybet. On 1X2 at odds 1.5–3.0:

| Boost | EV |
|---|---|
| +10% | −1.5% to −3.1% |
| +25% | +4% to +6% |
| +50% | +16% to +18.5% |

A "10% extra" boost is negative. 25% and above is positive.

**Near-fair legs.** Of short prices (1.30–2.50), selections within 2% of fair make up:

| Book | Share |
|---|---|
| Coolbet | 9.4% |
| Epicbet | 14.4% |
| Unibet-Site | 10.5% |
| Tonybet | 7.2% |

That is roughly 35–65 legs a day per book, enough to build accumulators from. ⚠️ Their measured mean of
+1–2% is inflated by selection on noise (winner's curse), and the sharp-trigger audit puts the independent
edge of such legs at "undetermined" (#150). Plan on legs at about −1%, not at +1%.

**Acca insurance, modelled on real margins.** The per-leg EV (e) decides everything:

| Setup | EV per stake |
|---|---|
| 3 legs × 1.50, refund as a token worth 0.70–0.80, e = −2.4% | +24% to +28% |
| same, e = −6% | +14% to +18% |
| 4 legs × 1.80, e = −6% | about 0 |
| 5 legs from 1.3–2.0 favourites at all-league margins (simulation) | −€1.0 to −€1.8 per €10 |

**Use the minimum number of legs at the shortest prices allowed.**

## 3. Catalogue — recurring promos per book (read 2026-09-26/27, logged out)

EV/month assumes the tier we recommend, with legs picked at best-available prices (≈ −1% per leg, −3% per
3-leg acca). The conservative figure assumes −8% per acca and a 0.65 net token value.

### Coolbet (StayCool OÜ; account held)

| Promo | Terms that decide the sign | EV estimate |
|---|---|---|
| **Football Combo Club (Jalgpalli Komboklubi)** — weekly, "until further notice" | 3+ leg accas on top-5 leagues only, ticket ≥ 2.50, opt in on the slip, all legs settled by Sunday 23:59. Weekly real-money turnover pays a free bet: €50 → €15, €150 → €45, €450 → €100, €1,000 → €200. One per week. The free bet is SNR, single only, min odds 1.50, **5× wagering**, credited Monday 18:00, **expires Sunday of the same week**. | Free bet worth 0.65–0.74 × face after the 5× wagering at a 3% top-5 margin. Per week: **€150 tier +€17–28**, **€450 tier +€29–58**, **€1,000 tier +€50–114**. Per month: **€75–120 / €125–255 / €215–495**. The €150–450 tiers carry the best return per euro of risk. |
| **Double Up (Duubelda)** — weekly, Monday–Sunday | One pre-match bet a day on a pick from Coolbet's curated Double Up section, odds ≥ 2.00, markets Winner/Total. Day 1 stake €5–20; each later day stakes all of the previous day's winnings. 7 wins in a row share **€10,000** (+€500/week rollover if nobody wins), wagered 1× at 1.50. | The public leaderboard `/s/sb-campaigns/leaderboard/1/daily` showed **9 players alive at day 5–6**, which implies ~500 entrants and ~2 winners a week. P(7 wins) ≈ 0.47⁷ ≈ 0.5%. EV ≈ €10k × 0.0047 × E[share] ≈ €17. The €5 chain costs ≈ €2 (1 − 0.93⁷). **≈ +€15/week (range +€5–25), €20–100/month.** A lottery: about a 22% chance of any payout in a year. |
| **Triple Up (Kolmekordista)** — weekly, Friday–Sunday | Odds ≥ 3.00, stake €10–30 rolled over. 3 wins share **€6,000**; 2 wins enter an AirPods Max draw. | Leaderboard 3 showed **358 day-1 winners → ~1,300 entrants**, ~27 final winners at ≈ €220 each. EV ≈ €4.6 − €2.2 chain cost ≈ **+€2.4/week, about €10/month.** Marginal. |
| Coolbet League / 6-scores predictor, NFL predictor | Free to play | Positive but negligible. Only worth doing if automated. |
| Nations League leaderboard, VIP | Turnover race / invitation only | Skip. |
| 100% welcome up to €300 | New customers only | Not available (account held). |

### Unibet-Site (Kambi; account held)

| Promo | Terms | EV estimate |
|---|---|---|
| **Acca insurance (Kindlusta oma kombo)** | ≥ 3 legs, ticket ≥ 1.50. If **at most one** leg loses, the stake comes back as a free bet. That free bet must itself be a ≥ 3-leg combo at ≥ 1.50. **The maximum refund, and whether the offer can be reused, show only in the logged-in rewards section.** | Per use at minimum legs and short prices: **+14% to +28% of stake**. At a €10 weekly cap that is ≈ €10/month; if it can be used daily up to €10–20, **€60–80/month**. Needs the owner to look at the rewards page. |
| 2UP (early payout at a two-goal lead) | A **separate, priced market** per its own T&Cs | Not a promo. |
| Power Sub, add-selections, Bet Builder | Features | Not a promo. |

### Epicbet (Ducks In A Row OÜ; collected; account status to confirm)

| Promo | Terms | EV estimate |
|---|---|---|
| **Triple Crown** — weekly, €30,000 | Day-1 stake €5–10 at odds 3.00–4.00 (singles or combos, pre-match or live); each later day stakes the previous winnings. 3-day streak shares **€5k**, 5-day **€10k**, 7-day **€15k**; only the highest level reached pays. **Restart the same day after a loss.** Prizes paid as bonus, wagered 1× at 1.60 within 60 days. | This week (Saturday): **897 participants, 27 at 3 days, 0 at 5 days**. Pool per participant ≈ €11–17 when the 5-day tier pays. Chain cost about €2–3/week (we can pick the best selection priced 3–4). **About €30–45/month.** |
| Kuldne koefijaht (King of Odds) — weekly €2,500, top 5 by winning odds | Min €5 bet; prizes are bonus money wagered 1× at 1.80 | 556 participants; the top odds this week were ~260. A €5 ticket at ~250–300 wins ~0.3% for ≈ €800 → ≈ +€1/ticket net. **€10–20/month**, lottery. Optional. |
| Bet & Get spins | €50–200+ weekly turnover at odds ≥ 2.50 buys 50–200 spins at €0.10; spin **winnings need 25× wagering in casino** | About €0 after wagering, negative after the turnover cost. **Skip.** |
| 100% welcome up to €200, EpicShare, missions | New customers / social | Welcome only if no account exists yet. |

### Tonybet (Osaühing Tonybet; collected, placeable since 2026-09-23)

| Promo | Terms | EV estimate |
|---|---|---|
| **Combo Boost** — permanent | Accas of ≥ 3 legs, each ≥ 1.20, no Asian lines. Total odds × 1.05 (3 legs), 1.07 (4), 1.10 (5), 1.12 (6), 1.15 (7), 1.20 (8), 1.25 (9), 1.27 (10) … up to 2.00 (20). Winnings paid as real money. Not with free bets; void if 3+ legs are on Tonybet's own "Boosted Odds". | Positive only when every leg is within ~1% of fair (5 legs) or ~2.4% (10 legs). A 10-leg acca: e = −1% → **+15%**, e = −2% → +4%, e = −3% → −6%. Only 7.2% of Tonybet's short prices are within 2% of fair. A composer can build these, but the leg edges are noisy. **Paper first.** About €10–45/month at €10 stakes; hit rate ~1–2%. |
| Sport welcome: deposit ≥ €20, turn over 5× the deposit at ≥ 1.5 → free bet = deposit (max €100) as a ≥ 3-leg combo | New customers only, 14 days | One-off ≈ **+€35–55** at a €100 deposit, if the owner has no Tonybet account yet. |
| Bettors' tournament (monthly, €5k in free bets), daily/weekly predictors, daily quests | Tournament points = 100×(odds−1) of each winning bet, ranked in three stake bands | Small, depends on the field. Skip. |

### Olybet (OB Holding 1 OÜ; NOT collected; no account known)

| Promo | Terms | EV estimate |
|---|---|---|
| **Daily profit-boost challenge (Kasumivõimenduse väljakutse)** | Daily turnover → a 25% profit boost on the next bet: €100 → max stake €50; €250 → €150; €500 → €250; €1,000 → €500. Bets settled before 23:59. **"Only for selected customers"**; min odds, leagues and max stake per the personal *My campaigns* page. | At −3% legs, the €250 tier gives the boosted €150 bet at odds ~3 ≈ +€19, against a turnover cost of €5–12. **About +€8–15/day, or €240–450/month, if offered.** Very sensitive to Olybet's margin, which we do not collect. |
| Combo King | 5+ legs, each ≥ 1.30; winnings +4% (5 legs) … +25% (10) … +125% (30) | Same arithmetic as Tonybet's Combo Boost. |
| Insured multi-bets | 5+ legs, each ≥ 1.50, stake ≥ €5; refund 20% (5 legs) … 100% (10+ legs) when exactly one leg loses; **unlimited use, no opt-in** | Could stack with Combo King (stacking not stated). At 10 legs × 1.50, e = −1% → ≈ +20%; e = −3% → ≈ 0. Needs Olybet prices. |
| OlyBoost (daily boosted specials), Bet Builder challenge (up to €100 in free bets) | Per boost | Unpriced. Mostly outrights and specials. |
| Welcome SCORE225: up to €200 + €25 free bet; SPORT25 risk-free €25 + €10 free bet | New customers | One-off ≈ **+€60–120**. |

### Paf (Pafer AS, Kambi; NOT collected; no account known)

| Promo | Terms | EV estimate |
|---|---|---|
| "€15 Free Bet — play for €50" (listed as *Available*) | Probably personalised or recurring. Terms not shown logged out. | If weekly: 15 × 0.7 − 50 × 5% ≈ **+€8/week, €35/month**. |
| Combo Boost (up to 25% profit on combos), Boosted Odds (Golden Hour, Friday 18–19; Super Saturday), €10k monthly free-bet draw (2,000 winners) | Terms not read | Small. Kambi margins are about 6% on top-5 leagues. |
| Welcome betting: 100% up to €100, **6× wagering at ≥ 1.80**, plus 4 × €5 free bets | New customers, 30 days | One-off ≈ **+€40–85**. |

### Optibet

The promotions page shows no offers to a logged-out visitor. Unknown.

## 4. Verdict and recommendation

**Verdict.** Promotions are a real, repeatable +EV source, and structurally ours:

1. The EV comes from the book's subsidy, not from predicting football.
2. Every input is either measured (margins, conversion, near-fair legs) or read from the T&Cs.
3. Public readers cannot copy it.

It is also bounded:

- Weekly caps and one-per-week limits.
- Unhedgeable variance: several items are lotteries, and the Combo Club's €1,000 tier swings about ±€400 a week.
- Every T&C carries a "bonus abuse / irregular betting pattern" clause, so accounts that only chase promos
  get restricted. Mixing in normal-looking top-5 accas is the natural cover, and the Combo Club
  requires exactly that.

EV at books we already hold: **€150–500/month**. The €1.1–6.8k/year ceiling in the audit was for a model edge
that measures ≤ 0; this is **€2–6k/year at a positive expected sign**.

### Build list, ranked by €/month per build-day

| # | Build | Direction | Estimate | Expected €/month |
|---|---|---|---|---|
| 1 | **Combo Club assistant (Coolbet), manual placement.** Thursday–Saturday: propose 3-leg top-5 Coolbet accas with every leg ≥ −1% vs the v2 anchor (Pinnacle Shin de-vig), ticket ≥ 2.50, all settled by Sunday, sized to the chosen tier (start at **€150/week**, move to €450 after 4 weeks of ledger). Monday: propose the free-bet single (odds 4–6, max p×(o−1) vs fair, ≥ 1.50) plus the 5× wagering plan. Log both through `promo_ev --record`. Add a `turnover_club` formula to `promo_ev.py` (tiers, token value net of wagering). Telegram alert. Reuses the Coolbet odds and `consensus_fair_prob` we already have. | 🤖 OWN | ~1 day | €75–255 |
| 2 | **Streak-game helper.** Daily pick for Coolbet Double Up (≥ 2.00; limited to Coolbet's curated Double Up menu, which needs one extra fetch of that category), Coolbet Triple Up (≥ 3.00) and Epicbet Triple Crown (3.00–4.00, any market, same-day restart): the qualifying selection with the highest fair p × odds, at minimum stake. | 🤖 OWN | ~0.5 day | €50–150 |
| 3 | **Promo watcher (collection only, public endpoints).** Daily: diff Coolbet `/s/bonuses/promotions/available/public/et` → Telegram on a new or changed sport promo. Also poll `/s/sb-campaigns/leaderboard/{1,3}/daily` and the Epicbet Triple Crown / Koefijaht counts, so N and winner counts become measurements instead of the inferences in §3. Seed `promo_terms` with the rows in §3 (`scripts/promo_ev.py add-terms`). | 🤖 OWN | ~0.5 day | Enabler |
| 4 | **Owner actions, no code (⚖️).** (a) Check the Unibet rewards page for the acca-insurance cap and frequency. (b) Decide on opening Olybet, Paf and Tonybet accounts for the one-offs (≈ €150–250 total), and check Olybet *My campaigns* for the profit-boost challenge. (c) Confirm there is no active sport bonus at Coolbet, because the Combo Club excludes players who hold one. | 🤖 OWN | Owner time | Unlocks €10–80 (Unibet) + €100–450 (Olybet) |
| 5 | **Only if 4(b) shows the profit-boost challenge.** An Olybet price sweeper (BetConstruct), built like the Optibet sweeper, so the boosted bet and the turnover legs can be priced. | 🤖 OWN | 1–2 days | €240–450 conditional |
| 6 | **Tonybet Combo Boost / Olybet Combo King paper test.** Composer of 8–10-leg accas from legs within 1% of fair, recorded as paper with EV before the bet, and judged on leg-level CLV against the independent close. Legs chosen on noisy edges regress. | 🤖 OWN | ~0.5 day on top of #1 | Unknown, paper first |

**Do not build:**

- Boosted-odds scrapers per book. Caps are €10–25, boosts are few, and they would need a price per special:
  ≈ €10–40/month for 1–2 days of work each.
- Bet & Get spins, casino offers and reload deposit bonuses (casino wagering eats them; `promo_ev` already
  refuses deposit bonuses without their rollover base).
- Turnover leaderboards and predictors.

**Kill rule already exists:** `scripts/promo_review.py` stops a promo after two consecutive months more than
1.5 sd below ΣEV. Every item above goes through `promo_ledger` with its EV computed **before** the bet.

**Open questions that change the numbers.**

- Is Coolbet's 5× wagering on the free bet's face value or on its winnings? That is the range 0.74 vs 0.65 × face.
- The Unibet insurance cap and frequency.
- Is Olybet's profit boost offered to this account?
- Real Double Up entrant counts (the watcher, build #3, answers this in 2 weeks).
- Account-restriction risk. It is unmeasured, like stake limiting in general (audit Phase 3 item 4).

## Reproduce

- Margins, free-bet conversion and boost break-even: the SQL in §2 (the DISTINCT ON latest price per match,
  book, market and selection within 3 h of kick-off; `workers.model.devig` on Pinnacle), 8 days ending
  2026-09-26. The top-5 cut uses the 5 `leagues.id` for EPL, LaLiga, Bundesliga, Serie A and Ligue 1, over
  35 days.
- Public endpoints, read-only and logged out: Coolbet `/s/bonuses/promotions/available/public/et` and
  `/s/sb-campaigns/leaderboard/{1,3,4}/daily`; Tonybet `/api/v2/page/contents/get?pageUrl=/ee/promotions/<slug>`.
- T&C pages: Coolbet `/et/jalgpalli-komboklubi`, `/et/duubelda-10000`, `/et/kolmekordista-6000`; Unibet
  `/promotions/sportsbook-promotions/{acca-insurance,2up}`; Epicbet `/et/{triple-crown,kingofodds,panusta-ja-saa-tasuta-spinne}`;
  Olybet `/profit-boost`, `/et/{combo-king,multi-bets-insured,olyboost}`; Paf `/en/pakkumised`.
