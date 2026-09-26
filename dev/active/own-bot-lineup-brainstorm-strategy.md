Parent row: [[#191]] OWN-BOT-LINEUP-AND-ADMIN-BOTS-CLEANUP-2026-09-26 (PRIORITY_QUEUE.md). Task (a2), strategy brainstorm.

# #191: which OWN bots, and how each one gets judged

**2026-09-26 · research and thinking only. Nothing in production changed.** Direction: 🤖 OWN throughout.
The only new evidence here is one cheap read-only backtest (§3.1) and one measurement of book margins
(§2.2). Everything else cites existing findings.

**Rules this memo follows (from the owner):**
* An OWN bot prices **only** at books we can bet from Estonia: Coolbet, Unibet-Site, Epicbet and Tonybet
  today, with Paf, Ninja and Optibet possible later.
* One bot per **market × method**. The book is a column you GROUP BY; it is not a separate bot.
* An OWN bot never takes over an existing bot, and it can never take over a PICKS bot. PICKS bots price
  at global books. **What an OWN bot inherits is a rule, which is then re-tested at the Estonian price.**
* Judge every bot on **independent CLV at the executed price** (`clv_cons`: the close of ≥ 5 books,
  excluding Pinnacle, the leg's own book and our other Estonian books). Always report ROI and wins
  against the number implied by the consensus close alongside it.

---

## 0. Summary

| # | OWN bot (market × method) | status | rule, in one line | expected picks/day | decide at |
|---|---|---|---|---|---|
| 1 | **1X2 · SHARP** (`bot_own_1x2_v1`, r1) | exists, keep | best Estonian price is EV ≥ 3% over the v2 anchor, < 3 h to kick-off, ≥ 3 confirming books | ~10–20 | 80 matches (~1 week); per book 60–80 |
| 2 | **O/U 2.5 · SHARP** | new, paper | the same rule on O/U 2.5, with the exchange in the anchor | ~1–5 | 120 matches (weeks to months) |
| 3 | **1X2 · MODEL** (NEW+ checked by the sharp anchor) | new, paper | NEW+ EV ≥ 3% at the best Estonian price, **and** the v2 anchor EV ≥ 0, odds 1.30–3.50 | ~10–16 | must beat bot 1 on the same matches, which needs 120+ |
| — | O/U · MODEL (`ou_comb_v1`) | **not started** | NEW+ O/U is Pinnacle wherever Pinnacle prices the match, and the backtest came out negative (§3.1) | — | only after the early-price test (§4.6) |
| — | AH / BTTS / corners / 1H / cards | **not started** | no evidence at Estonian books, and several negative results (§5) | — | — |

**The honest headline.** No bot has shown value that survives at the Estonian books against an
independent close (#150, #172). Bot 1 is the only rule that survived a holdout (#182). It is still a
forward paper test, not a money rule: ROI was −18% ± 10 on the same legs.

A MODEL bot on the current models can only do two things at a soft book:
* **filter** sharp picks, or
* run **early**, before Pinnacle moves.

Nothing we hold gives a model an information edge large enough to beat a soft book's 6–10% margin
by itself (§2).

---

## 1. The recommended starting line-up

### Bot 1: OWN 1X2 · SHARP (`bot_own_1x2_v1`, rule r1, live since 2026-09-26 20:13 UTC)

**Rule (as built, `workers/jobs/own_bots.py`):**
1. Fair value comes from the v2 anchor:
   * Pinnacle and the Betfair Exchange blended, or either one alone;
   * a conflict between Pinnacle and the exchange gives **no price**;
   * otherwise, a consensus of ≥ 5 books that excludes the book being priced.
2. The book's price must clear EV ≥ 3% and the price gates of `own_bet_board.build`:
   * an 8% ceiling;
   * the outlier cap;
   * the wrong-fixture guard;
   * a quote no more than 60 minutes old;
   * the market-split guard (gap under 0.08, applied across the whole match);
   * the de-vig robustness check (the pick must clear under both Shin and power).
3. Kick-off must be between 3 minutes and 3 hours away.
4. Confirmation: at least 3 other books (not Pinnacle, the exchange or our own books) with a complete
   line no more than 180 minutes old, whose median fair probability is ≥ 0.97 × the anchor's.
5. Take the best clearing Estonian book and record it on the pick.

**Why it could work at soft books.** The #182 filter study found that timing carries the value:
* Picks made in the last 3 hours before kick-off: **+4.9%** against the independent close (n 190,
  holdout +4.7%, Holm p < 0.001).
* Picks made earlier revert.
* The confirmed subset scored +5.7% [+3.9, +7.6] (n 134, discovery +5.6%, holdout +5.8%).

The mechanism is the textbook soft-book one. A soft book's line lags the global market, and near
kick-off there is too little time left for the lag to be corrected, or for the Pinnacle noise the
trigger fired on to revert. #186 adds a caveat: our books are *fresher than API-Football's copy* of the
global market, not better informed. That is why the market-split guard exists.

**Judge.**
* Primary: `clv_cons` at the recorded price.
* Secondary: the exchange close wherever the match is liquid (§4.1).
* Always report alongside: ROI; wins against the number the consensus close implies; placeability
  (§4.2).

**n to decide.** The per-leg SD of independent CLV is about 7.8% (stale study §8). Seeing +3% at 80%
power needs ≈ (2.8 × 7.8 / 3)² ≈ **53 matches**, so use 80 because legs cluster by match. Seeing +2%
needs ~120. A per-book verdict needs 60–80 per book, which is 2–3 weeks at Coolbet.

**Picks per day.**
* #182: 5.6 / 8.1 / 5.0 / 0.1 per day at Coolbet / Unibet / Epicbet / Tonybet, legs as the old bots
  fired them.
* §3.1 backtest, at one instant per match about 25 minutes before kick-off: 23 legs a day beat an
  independent consensus by ≥ 3% at the best Estonian book, before the confirmation and split guards.
* Best estimate: **10–20 a day** once de-duplicated per selection. Replace this with the real count
  after the first 7 days; the bot has written 1 pick so far.

### Bot 2: OWN O/U 2.5 · SHARP (new, paper)

**Rule.** Bot 1's rule applied to `over_under_25`:
* the v2 anchor (the exchange covers `over_under_25` in `_EX_MARKETS`);
* EV ≥ 3%;
* kick-off < 3 hours away;
* the same market-split guard (measured on the match's 1X2) and de-vig robustness check;
* confirmation by ≥ 3 books;
* the handicap line must be NULL or exactly 2.5. This drops 1xBet's 0.25-line rows (#121).

**Why it could work.** It is the same lag mechanism as bot 1. Our O/U margins are 6–9%, a little lower
than 1X2 (§2.2). **The evidence is thin:**
* #182 had only 60 graded O/U legs: Coolbet −3.3%, Unibet +4.2%, Epicbet +3.0%.
* The per-book O/U triggers in #172 were +2.7% / −2.2% on n ≈ 25.
* In §3.1, **almost no** O/U 2.5 quote beats the independent consensus by ≥ 3% close to kick-off.
  One leg did in 1.4 days, and only 0.3–1.3% of quotes did at KO−60 (§2.2). The O/U line is more
  efficient than 1X2 at our books.

**Judge.** Same as bot 1. The exchange close is especially important here, because O/U 2.5 is the
exchange's second-most liquid market.

**n.** 120 matches to see +2%. **Picks per day: ~1–5.** This bot mostly exists to find out whether the
rule transfers. If fewer than 30 picks arrive in 4 weeks, close it as "no volume" rather than leave it
running unjudged.

### Bot 3: OWN 1X2 · MODEL (NEW+, checked by the sharp anchor) (new, paper)

**Rule (pre-register before building; see §3):**
* `r1x2_comb_v1` EV ≥ 3% at the best Estonian price that clears the board's price gates, as in bot 1;
* **and** v2-anchor EV ≥ 0 at the same book: the sharp market does not disagree;
* odds 1.30–3.50, because the longshot margin at our books is 10–17% (§2.2);
* any time ≥ 3 minutes before kick-off;
* **the first qualifying instant is the pick** (first write wins);
* the timing window is recorded on every pick, so the < 3 h / 3–24 h split is a GROUP BY.

**Why it could work, and why it might only be bot 1 wearing a hat.**
* NEW+ **is** market-driven. It is a logit over ratings, a de-vigged consensus of ~18 books and
  Pinnacle (`workers/model/combined_1x2.py`). Its log-loss beats Pinnacle by 0.008 (0.945 vs 0.953,
  n 194). Close to kick-off, its edge at a soft book is mostly the soft book sitting off the consensus.
  That is a sharp signal under another name.
* The only information NEW+ has that the market does not is the rating component. #090 shows what that
  buys: the rating predicts the direction of Pinnacle's move, but only by ~0.4 pp of probability.
* So the one genuinely MODEL-shaped question is: **does model agreement rescue the sharp picks that
  #182 says revert?** Sharp picks > 3 h out scored −0.9% / −1.2% in #182. If the model agrees with the
  sharp side, the move may be information rather than Pinnacle noise.
* That is testable. It is also the only reason to run bot 3 separately from bot 1.

**Judge.** `clv_cons` at the executed price, **plus the incremental test**: on matches where both bots
could fire, (bot 3 ∧ bot 1) against (bot 1 ∧ ¬model-agree). A bot 3 whose value equals bot 1's is
retired as redundant, not promoted.

**n.** 120 matches for the vs-0 test. The incremental test needs several hundred, so this is a
6–10 week bot.

**Picks per day.** §3.1: 16 a day at the ~25-minute instant (m ≥ 3 ∧ sharp ≥ 0). A bot that scans
all day will see more.

---

## 2. MODEL at Estonian books: what can it realistically exploit?

### 2.1 The arithmetic

| quantity | value | source |
|---|---|---|
| NEW+ against Pinnacle (1X2 log-loss) | 0.945 vs 0.953 (n 194), with Pinnacle as an input | AF_PREDICTIONS_TONYBET_FAIR |
| information the rating has that the opening price lacks | ~0.4 pp of probability per side (O/U) | per-market design, #090 |
| α of the model against de-vigged Pinnacle | 0.0000, five times | CLAUDE.md, "Research before you train" |
| soft-book EV against an independent consensus, averaged over all quotes | 1X2 **−8.3 to −10.6%**, O/U **−6.4 to −8.8%** | §2.2 |
| pure rating model (`r1x2_d8plus_v1`) EV ≥ 3% at the best Estonian book | CLV **−7.7%** [−9.3, −6.3], n 86 | §3.1 |

A model that is roughly level with Pinnacle, betting into a book that charges 6–10%, needs the book to
be **wrong by more than its margin**. When a soft book is that wrong, a sharp comparison finds it
anyway. The model adds value only where the sharp comparison cannot see:
* where there is no sharp line;
* where the sharp line is stale (§88);
* or before the sharp line has moved.

Everywhere else, a "model edge" at a soft book is either the soft book's lag, which bot 1 already
captures, or the model's own error. The pure rating model's −7.7% is that error, measured.

### 2.2 Soft-book margins by side (favourite–longshot shading, new measurement)

EV of each book's quote at KO−60 min against the independent consensus at the same instant (≥ 5 books,
Pinnacle and our four books excluded). Finished matches from about 2026-09-23 to 09-26, 1X2 ≈ 1,000–1,100
quotes per book. Negative numbers are the tax per unit staked.

| 1X2 | favourite < 2.0 (home / away) | 2.0–3.5 (home / draw / away) | longshot > 3.5 (home / draw / away) | all | share of quotes ≥ +3% |
|---|---|---|---|---|---|
| Coolbet | −5.5 / −4.7 | −6.7 / −7.5 / −8.1 | −12.0 / −9.6 / −11.3 | −8.3 | 2.9% |
| Epicbet | −5.9 / −6.4 | −6.4 / −10.0 / −7.8 | −15.9 / −8.0 / −12.7 | −8.6 | 2.9% |
| Unibet-Site | −5.9 / −7.4 | −7.6 / −10.0 / −9.1 | −9.6 / −11.6 / −14.8 | −9.7 | 2.1% |
| Tonybet | −5.7 / −6.9 | −8.3 / −10.8 / −9.6 | −14.8 / −12.2 / −17.5 | −10.6 | 1.7% |

| O/U 2.5 | over | under | share of quotes ≥ +3% |
|---|---|---|---|
| Coolbet | −7.1 | −7.6 | 0.5% |
| Epicbet | −6.2 | −6.6 | 1.3% |
| Unibet-Site | −6.7 | −6.6 | 0.6% |
| Tonybet | −7.8 | **−9.7** | 0.3% |

What this means for a MODEL bot:
1. **All four books shade longshots hard.** Favourites cost 5–7%; longshots cost 10–17%. A model bot
   should not look above ~3.5. This matches #182's odds bands: b1 < 2.0 had ROI +6.9% and CLV +2.5%;
   b3 > 3.5 had CLV +5.7% but ROI −13%. Above 3.5, an apparent edge is more often a feed fault (§67)
   than value.
2. **Draws are taxed 8–12% and are not a model target anyway.** In the literature, draw skill is 1.4%
   for a model against 3.0% for the bookmaker, and neither has discrimination.
3. **Tonybet is the most expensive book on every side, and on the under in particular.** This confirms
   §87. It will rarely be the best price. Keep it in the best-of set, and do not give it a bot.
4. **O/U is priced more evenly than 1X2** (under 1.3% of quotes clear +3%). There is little soft-book
   error for any method to find close to kick-off.

### 2.3 The four levers, taken sceptically

| lever | what we know | verdict for MODEL |
|---|---|---|
| **Books that lag** | They lag: 63–75% are unchanged at the first scrape after a ≥ 3% Pinnacle move (stale study §8, §10). But the lag is not worth money on a fresh move (−2.4% against the independent close). It is worth money only in the last 3 hours (#182). | This is a **SHARP** lever. A model adds nothing to lag detection. |
| **Margin shading by side** | Measured in §2.2. | A **floor and band** lever for every method, not an edge source. MODEL bots cap odds at 3.5; SHARP bots report by band. |
| **Model + sharp agreement** | §3.1: NEW+ alone is +0.3–0.5% (nothing). NEW+ ∧ anchor-agree is +2.9–3.1%. Sharp ≥ 3% alone is +3.9%. The split by model agreement, among sharp-selected legs, is +4.1 vs +3.5 (n 29 / 17, noise). | Near kick-off, **agreement is a relabelled sharp signal**. The open question is agreement **early** (bot 3's reason to exist). |
| **League tiers where Pinnacle is thin** | 706 of 2,465 NEW+ rows are group "C" (consensus, no Pinnacle) and 864 are "none". §88: AF-Pinnacle is stale where the market moves late, which is youth, reserve and cup football. | This is the one place a model's rating could matter. It is also where our books have the most wrong-fixture faults (Epicbet 78 excluded pairs in 7 days). Run it as a pre-registered **cell** of bot 3 (group C against P&C), never as its own bot. |

---

## 3. Candidates → backtest plan

**The principle.** An OWN bot inherits a *rule*, never a ledger. Every candidate is re-applied to
the best Estonian price **at pick time**, using only quotes timestamped at or before T (the #172 method,
no look-ahead). It is judged on `clv_cons` at that price. Candidate bots that fire at global books
(PICKS, the tight arm, the forward-test arms, the consensus arms) contribute their rule, never their
picks.

**Backtest on synthetic triggers, not on bot legs.** #172 and #182 graded legs that some bot happened
to fire. That population depends on which bots were running, it has 54% ungradeable legs (§7 of #182),
and the recorded prices carry a look-ahead (#172 §5). The next backtests should instead scan **every
finished match** in the full-resolution window. Retention keeps full intraday history for about 7
days (§59), so each scan covers roughly 7 days. At fixed instants (KO−24 h, −12 h, −6 h, −3 h, −1 h,
−25 min), each scan applies the rule to `odds_snapshots ≤ T`. The window rolls forward, so re-running
every week grows n. §3.1 is the first run of exactly this, using the NEW+ row's own write time as T.

### 3.1 First cheap backtest: NEW+ (and the pure rating model) at Estonian books, run 2026-09-26

**Method.**
* **Pick instant T.** The prediction row's `updated_at`, and only rows written before kick-off. The row
  holds the probability known at T. NEW+ is re-applied every 30 minutes and the row is overwritten, so
  the only point-in-time instant available is the **last** pre-kick-off write, a median of 25 minutes
  before kick-off.
* **Population.** Gated `r1x2_comb_v1` rows on finished matches: 550 matches from 2026-09-24 18:00 to
  09-26 18:05, about 2.0 days. Plus `r1x2_d8plus_v1` rows (505 matches) and `ou_comb_v1` O/U 2.5 rows
  (397 matches, 1.4 days).
* **Price.** The best Estonian quote from a complete set no more than 60 minutes old at T.
* **Sharp edge.** EV against `compute_anchor` at T. This is Pinnacle when it is tight, otherwise a
  consensus of ≥ 5 books that excludes the priced book.
* **Judge.** Consensus close at kick-off, ≥ 5 books, excluding Pinnacle and all four Estonian books.
* **Bootstrap.** Clustered by match.
* **Split.** Discovery/holdout at the median T.
* **Status.** Exploratory, stated as such. The expectation below was written before the run, in this
  session's notes, and was not committed first. The cells were fixed before looking. The scratch script
  was not committed (the memo only), so the method here is what reproduces it.

**Expectation stated before the run:**
* NEW+ EV ≥ 3% alone: about 0 to +1%, because NEW+ is mostly consensus.
* The pure rating model: clearly negative (−3 to −6%).
* Agreement cells: positive, but only because they are sharp.

| cell (EV cap 8%) | model | n legs | CLV_ind [95% CI] | disc / hold | ROI | wins / expected | per day |
|---|---|---|---|---|---|---|---|
| control: every leg, best Estonian price | NEW+ | 1,650 | −7.3 [−7.7, −6.8] | | −9.4 | 550 / 550 | |
| m ≥ 3% | NEW+ | 56 | **+0.5 [−1.1, +2.0]** | −0.2 / +0.9 | +1.9 | 24 / 22.6 | 28 |
| m ≥ 0 ∧ sharp ≥ 0 | NEW+ | 67 | +3.1 [+1.8, +4.4] | +3.3 / +3.1 | +4.1 | 27 / 25.5 | 33 |
| m ≥ 3 ∧ sharp ≥ 0 | NEW+ | 32 | +2.9 [+0.8, +4.8] | +2.8 / +3.0 | +1.4 | 12 / 12.1 | 16 |
| sharp ≥ 3% (no model) | — | 46 | +3.9 [+2.9, +4.8] | +3.8 / +4.0 | −24.5 | 13 / 13.8 | 23 |
| sharp ≥ 3 ∧ m ≥ 0 / ∧ m < 0 | NEW+ | 29 / 17 | +4.1 / +3.5 | | −24 / −25 | | |
| m ≥ 8% (outliers) | NEW+ | 40 | +11.3 [+0.5, +21.6] | | −20.5 | 10 / 10.2 | |
| m ≥ 3% | **rating only** | 86 | **−7.7 [−9.3, −6.3]** | −7.7 / −7.7 | −3.1 | | |
| m ≥ 3% ∧ < 3 h | rating only | 24 | −5.5 | | −24.9 | | |
| m ≥ 3% | O/U `ou_comb_v1` | 26 | **−2.4 [−5.3, −0.1]** | | −0.5 | | 19 |
| m ≥ 0 ∧ sharp ≥ 0 | O/U `ou_comb_v1` | 18 | +2.7 [+1.4, +4.3] | +3.6 / +1.6 | −18.6 | | 13 |
| sharp ≥ 3% | O/U | 1 | — | | | | < 1 |

**What it shows, and one warning that matters more than the numbers.**
1. **At T ≈ KO−25 min, CLV is almost exactly the edge against the consensus at T.** The consensus
   barely moves in the last 25 minutes: the move is −1.3 to +0.8 pp in every cell. So this backtest
   measures whether our book sat above the consensus 25 minutes before kick-off. It does **not**
   measure whether the model or the trigger predicted anything. The number is worth only as much as
   the consensus close is efficient. **An independent sharp judge, the exchange close, is required**
   before any of these cells means money (§4.1).
2. **The NEW+ model adds nothing near kick-off.** Alone it is +0.5%. With agreement it is +2.9%, below
   sharp-alone at +3.9%. The model-agreement split within sharp-selected legs is inside the noise.
3. **The pure rating model loses the margin** (−7.7%, identical in discovery and holdout). A MODEL bot
   on raw ratings at soft books is a closed question (§5).
4. **O/U NEW+ alone is negative** (−2.4%). `ou_comb_v1`'s served probability *is* Pinnacle wherever
   Pinnacle prices the match (registry), so an O/U MODEL bot is a Pinnacle bot under another label
   there.
5. **ROI and CLV disagree again** for sharp ≥ 3% (CLV +3.9%, ROI −24.5%, 13 wins against 13.8
   expected). This is the same pattern as #182. At n 46 it is noise-compatible, and it is also exactly
   what a phantom-price effect would look like.

### 3.2 Per bot: candidates, what each uses underneath, backtest, and pre-registration

#### Bot 1: 1X2 · SHARP

| candidate rule | underneath | inherit? |
|---|---|---|
| #182 rule c1 ∧ a1 (already `bot_own_1x2_v1` r1) | v2 anchor (Pinnacle + exchange, else consensus without the book) | **yes, this is the rule** |
| `bot_coolbet_trigger_sharp_1x2_v1` / `bot_unibet_trigger_sharp_1x2_v1` | Pinnacle only (not v2), no timing window, per book | rule already absorbed; ledger **not** inherited |
| `bot_trigger_1x2_sharp_tight_v1`, `bot_trigger_1x2_sharp_v1`, `bot_sharp_1x2_v1` | Pinnacle or tight, global best-of-books | **no.** PICKS or global; the tight arm's value sat at Epicbet and was −2.4% at the books we can execute at (#172) |
| `bot_sharp_aligned_v1` (recorded twin) | the Estonian quote and the Pinnacle quote ≤ 5 min apart | test as a **cell** (freshness alignment); do not inherit |
| `bot_consensus_pinconf_v1` | consensus v2 plus Pinnacle EV ≥ 0 | the consensus arms read ≤ 0 at our books (#172: consensus_d −5.3%); no |

* **Backtest.** Re-run `scripts/analysis/own_bot_filter_study.py`, but with the **v2 anchor** instead
  of Pinnacle only (exchange since 09-24), on **synthetic triggers** at fixed instants rather than bot
  legs.
* **Improvements to try:**
  * edge floor {3%, 5%};
  * window {< 1 h, 1–3 h}. #182: +8.6 vs +1.8, where the late CLV concentrates;
  * odds band {< 2.0, 2.0–3.5};
  * market split on / off;
  * de-vig robustness on / off.
* **Family:** 2 × 2 × 2 × 2 × 2 = 32 cells, Holm.
* **Pre-registration:** discovery/holdout split by kick-off date; choose on discovery and confirm on
  holdout. The confirmation bar is #182's: holdout ≥ 50% of the discovery point estimate, and the Holm
  p on discovery < 0.05.
* **Forward:** the live bot stays on r1 until a winning cell is confirmed. Changes go in as r2 on the
  same bot (§84).

#### Bot 2: O/U 2.5 · SHARP

| candidate rule | underneath | inherit? |
|---|---|---|
| bot 1's rule, moved to O/U | v2 anchor (the exchange includes O/U 2.5) | **yes, as the starting rule** |
| `bot_coolbet_trigger_sharp_ou_v1` / `bot_unibet_trigger_sharp_ou_v1` | Pinnacle only, per book | the rule is absorbed; ROI −25 to −29% on n ≈ 25 is worth watching |
| `bot_ou_sharp_early_v1` | power-de-vigged Pinnacle, **≥ 12 h out**, EV 5–15%, global books | test as the **early** window cell. It contradicts #182's 1X2 timing result, but #090 says O/U information shows up early |
| `bot_ou_sharp_2anchor_v1` | beats Pinnacle **and** a leave-one-out consensus by ≥ 2% | test as the confirmation variant |
| `bot_sharp_ou_v1`, `bot_trigger_ou_sharp_v1` | PICKS or global | **no** |

* **Backtest:** synthetic triggers at KO−24 / −12 / −3 / −1 h / −25 min, O/U 2.5 only (the line with an
  exchange and a Pinnacle close).
* **Family:** window {≥ 12 h, 3–12 h, < 3 h} × confirmation {LOO consensus ≥ 2%, ≥ 3 books at 0.97} ×
  floor {3%, 5%} = 12 cells, Holm.
* **Split:** the same discovery/holdout split as bot 1.
* **Expectation stated now:** volume is the binding constraint (§2.2: ≤ 1.3% of quotes clear +3%).
  Expect most cells to be "too few", and the early window to be the only one with n.

#### Bot 3: 1X2 · MODEL

| candidate rule | underneath | inherit? |
|---|---|---|
| `bot_v10_1x2_newplus_v1` | **NEW+** (`r1x2_comb_v1`), EV ≥ 3%, odds 1.30–3.00 | yes, as the base rule |
| `bot_combined_1x2_ev5_v1` (VIP #1) | NEW+, EV ≥ 5%, Pinnacle required, 1.30–6.00 | as the floor-5% cell; it was −1.4% at our books on n 36 (#172) |
| `bot_combined_1x2_v1` | NEW+ with v10 thresholds (fav 6% / long 9%, min_prob) | as a cell only if the family has room; otherwise dropped |
| `bot_rating_1x2_v1` | **rating only** (`r1x2_d8plus_v1`) | **no.** §3.1 −7.7% |
| `bot_v10_1x2`, `bot_coolbet_1x2_model_v1`, `bot_unified_gate_1x2_paper_v1` | **old** v10 or calibrated ensemble, **not the latest model** | **no.** Wrong model, and unified_gate is −9.8% at our books (#172) |

**Backtest.** Two parts, because the data forces it.
* **(a) Near kick-off, run now (§3.1).** Its answer: NEW+ adds nothing over sharp at T ≈ KO−25 min.
* **(b) Early, needs point-in-time NEW+ probabilities that we do not store.** `rating_1x2_predictions`
  is latest-only and is overwritten every 30 minutes, sometimes after kick-off. There are two ways to
  get them:
  1. **Reconstruct.** The rating-only probabilities are fixed before the match (predict-then-update;
     the daily row is point-in-time for any T after that day's write). `combiner_1x2_params` keeps
     every fit with its `fitted_at`. Pinnacle and the consensus at T can be rebuilt from
     `odds_snapshots` (same sets as `compute_anchor`). So NEW+(T) = the combiner fit valid at T,
     applied to (rating, consensus at T, Pinnacle at T). This is feasible for the 7-day
     full-resolution window only.
  2. **Log forward.** An append-only history of NEW+ and `ou_comb_v1` rows. This is a new queue item;
     it is a code change and is not done here.

**Family for (b)** (1X2, NEW+ against the best Estonian price):
* floor {3%, 5%};
* window {< 3 h, 3–24 h};
* agreement {none, v2 anchor EV ≥ 0};
* odds {1.30–3.50, all};
* = 16 cells, Holm.

Plus one pre-registered **incremental** contrast: among sharp ≥ 3% candidates **in 3–24 h**, compare
the model-agree subset with the model-disagree subset. That tests "does model agreement rescue the
early sharp picks that revert". If the answer is no, bot 3 closes as redundant with bot 1.

**Split.** Discovery/holdout by kick-off date. Choose on discovery; confirm on holdout at #182's bar.
**Expected (stated now):** near kick-off ≈ bot 1. Early, NEW+ alone is negative (−2 to −5%, the margin
minus ~0.4 pp of information). The agreement contrast has a positive point estimate and is
underpowered at 7 days.

#### O/U · MODEL: explicitly not in the starting line-up

The candidates are `bot_v10_ou_comb_v1` (`ou_comb_v1`, EV ≥ 3%), `bot_coolbet_ou_model_v1` (old model,
toggled off) and `bot_trigger_ou_model_v1`. `ou_comb_v1` is Pinnacle wherever Pinnacle prices the
match. Its only model-only rows are the ones without Pinnacle, and near kick-off it scores −2.4% (§3.1).
The only open question is the **early-price** one from #090: the rating predicts where Pinnacle's O/U
moves, so any O/U edge exists only at early prices. That is §4.6, not a bot.

---

## 4. Other dimensions, ranked by value against cost

| rank | dimension | data we hold | value | cost |
|---|---|---|---|---|
| 1 | **Exchange close as judge; exchange in the anchor** | `exchange_quotes` since 2026-09-24 07:34: 707 1X2 matches, 114 with ≥ €5k matched, 28 with ≥ €50k; 418 finished matches have a quote ≤ 30 min before kick-off | **highest.** It is the only grader independent of the AF feed and of the consensus that a1 and NEW+ lean on. It breaks the §3.1 caveat | low, since the reader is running |
| 2 | **Placeability and quote survival near kick-off (book-specific lag)** | `odds_snapshots` next-sweep survival. The stale study measured 33–65% still on the board at the next sweep | **high.** Bot 1's CLV concentrates < 1 h before kick-off, exactly where phantom prices live | low |
| 3 | **Timing inside the 3-hour window** | #182 legs plus synthetic scans | high. +8.6% (< 1 h) against +1.8% (1–3 h) | low (a cell of bot 1) |
| 4 | **Per-book personality (margin by side)** | §2.2, per book and per band | medium. Sets the odds caps and explains Tonybet | done. Re-measure monthly |
| 5 | **Tonybet's Sportradar fair as a second anchor** | `book_fair_probs`, latest-only since 2026-09-23, 1,473 1X2 matches. Log-loss 0.935 against Pinnacle's 0.922 | medium. A fair price where Pinnacle is missing or stale (§88), and a tie-breaker for a Pinnacle-vs-exchange conflict | medium. Needs history (open / T−3h / close) first |
| 6 | **Opening lines** | `is_opening` rows at every Estonian book (1,100–1,470 matches in 7 days) | medium for MODEL: the only place #090 says a rating edge can exist | medium. Needs point-in-time model probabilities (§3.2 bot 3b) |
| 7 | **League tier / where Pinnacle is thin** | NEW+ `sources` C / none; `leagues` | low to medium. Most model-shaped, and the most exposed to faults | low (a cell) |
| 8 | **Market-move freshness** | stale study and its replication | **low. Answered negatively twice** | — |
| 9 | **Lineup release** | `matches.lineups_fetched_at` is **overwritten after the match** (median 83 h *after* kick-off on 1,125 matches), so there is no point-in-time release time | low now. #186's late-information leagues suggest it matters, but we cannot measure it | high (new collection) |

**Pre-registration sketches:**
1. **Exchange grader.** Hypothesis: bot 1's legs have CLV > 0 against the Shin-de-vigged exchange
   close (mid of back and lay, ≤ 30 min before kick-off, market matched ≥ €5k).
   * Sample: every bot 1–3 leg on a liquid match.
   * Test: one-sided vs 0, Holm across the three bots, n ≥ 80 liquid matches per bot.
   * Also report agreement with `clv_cons` (the correlation). If they disagree in sign, the exchange
     wins.
2. **Placeability.** For each synthetic bot-1 trigger, check whether the next sweep of that book within
   45 minutes and before kick-off still offers ≥ 99% of the price.
   * Report the share, per book and per window (< 1 h / 1–3 h).
   * Re-grade CLV assuming the price is lost whenever it did not survive.
   * Bar: after that adjustment, CLV lower bound > 0. Otherwise the < 1 h cell is phantom.
3. **Timing.** Part of the bot 1 family (§3.2).
4. **Per-book margins.** Descriptive; no test.
5. **Tonybet fair as an anchor.**
   * When: once `book_fair_probs` keeps history and n ≥ 2,000 fixtures (#154 idea 3).
   * H1: on matches with no fresh Pinnacle, EV against the Tonybet fair is ≥ as predictive of
     `clv_cons` as EV against a consensus that excludes Tonybet. Paired test.
   * Tonybet is **never** priced against its own fair: its price is always below it.
6. **Opening lines (MODEL early).**
   * Sample: NEW+ or `ou_comb_v1` probability at the Estonian **opening** price, against `clv_cons` at
     the close.
   * Family: the bot 3 early cells plus an O/U 2.5 pair.
   * Condition: only once point-in-time probabilities exist for ≥ 14 days of fixtures.
   * Expected: negative or zero, and the move component positive (it replicates #090).
7. **League tier.** One cell in bot 3's family: group C against P&C.
8. **Freshness.** No new test. Log fresh-move events and grade them against the exchange later
   (stale study §9).
9. **Lineups.** Record the time we **first** see a published XI (a new append-only column) before
   testing anything.

---

## 5. What to STOP: configurations whose tests are already answered negatively

| stop doing | evidence |
|---|---|
| **Pure-model bots at soft books** (rating-only, old ensembles, calibrated-model triggers) | §3.1 rating-only −7.7% [−9.3, −6.3]. The model-anchored 1X2 triggers were retired at CLV −8.4 to −9.2% on placeable books (registry, SHADOW-BOT-VERDICTS-2026-09-14). `bot_unified_gate_1x2_paper_v1` −9.8% [−12.9, −6.6], ROI −29.5% (#172). `bot_ou35_model_v1` −6.9% (#172) |
| **Sharp 1X2 triggers taken more than 3 hours out** (as OWN) | #182 c2 −0.9%, c3 −1.2%. The fair-price move reverts |
| **Fresh-move / stale-window triggers** | Stale study: −2.4% against the independent close, no better than a standing gap (control −2.9%). The replication agrees (Coolbet −6.0% [−9.1, −2.2]) |
| **Backing the locals against AF-Pinnacle** | #186: the "+18%" is API-Football latency. The exchange sided with the locals on 17 of 17 matches |
| **Big edges as a signal** (EV ≥ 6–8%) | #182 f3 fails the holdout, ROI −22%. §3.1 NEW+ m ≥ 8% has ROI −20.5%, and rating-only m ≥ 8% has CLV −10%. Keep the 8% ceiling |
| **A per-book Tonybet bot**; Tonybet as a trigger book | §87: its price is never above its own fair, margin 8–12%. It is the most expensive book on every side (§2.2). ~0.1 picks a day |
| **Per-book bots in general** | Owner rule, plus MERGE-TRIGGER-BOTS-2026-09-11. The book is a GROUP BY |
| **Corners, team totals, 1H 1X2 at our books** | #172: −4.6% / −2.9% / −3.0% at Estonian prices, CIs below 0. The literature finds no pre-match edge |
| **Draw targeting** | Foulley (2021); the per-market design, "Draw — NOT a head" |
| **API-Football predictions as any input** | §87: 1.527 log-loss, ≈ 1.16 excluding its 0% calls, worse than guessing |
| **Judging a Pinnacle-triggered bot on Pinnacle's close, or any outlier bot on its own-book close** | §85. Circular by construction (stale study: +5.6% against Pinnacle's close, −2.4% against the independent one) |
| **Adding the Pinnacle-age filter (e1) on top of the 3-hour window** | #182 §4. It halves volume and adds no CLV |
| **The consensus arms as OWN candidates** | #172: consensus_d −5.3% [−10, −1]; c −2.3% |
| **Inheriting any recorded price from before 2026-09-25 15:16 UTC** | §85 / #179: the price was rewritten on re-sweeps. Re-price from snapshots at pick time |

---

## 6. Risks

1. **Stake limits at soft books.**
   * Coolbet (and Unibet once it has an account reader) will limit an account that consistently beats
     the close. That is the expected end state of a working bot 1.
   * Limits cannot be seen in `odds_snapshots`, so a paper record is an upper bound on what can be
     staked.
   * Mitigations:
     * keep stakes flat and small (the real-money ladder);
     * spread across books (the best-of-four logic already does this);
     * record every refusal and partial acceptance from the placer as data.
   * Unibet real money stays blocked until a bet-history reader exists (#172 §6).
2. **Phantom prices.** This is the biggest risk, because bot 1's value sits exactly where phantoms
   live:
   * **Near-kick-off quotes that are gone at placement.** 33–65% survive to the next sweep.
   * **Recorded prices that equal the next snapshot, 20–80 minutes after the pick.** #172 §5 found this
     look-ahead; it needs its own row if one does not exist yet.
   * **A stale anchor.** §88: AF-Pinnacle's value is a median 120 minutes old. This inflates edges;
     the market-split guard and the exchange in v2 are the defences.
   * **Wrong-fixture and mirrored lines.** Epicbet had 78 excluded pairs in 7 days.
   * **Longshot "edges" that are feed faults** (ROI −20% on m ≥ 8%).
   * **Rule:** no OWN bot goes to real money on CLV alone. It also needs:
     * the placeability-adjusted CLV (§4 sketch 2) with a lower bound > 0;
     * the exchange-graded CLV > 0 on liquid matches;
     * ROI not significantly below the number the consensus close implies.
3. **The multiple-comparison trap.**
   * This memo proposes three bots with 16–32 candidate cells each, plus nine dimensions.
   * Holm runs **within each bot's family**, fixed before the run.
   * Across bots, report how many families were tested.
   * The per-book breakdown is **always descriptive**. Picking the best book after the fact is the same
     trap as picking the best market (§47).
   * A cell that wins on discovery gets exactly one holdout confirmation. If it fails, it does not get
     re-tuned on the holdout.
   * Every run appends its family and outcome to the bot's rule history, so the count is auditable.
4. **Circular judges.**
   * `clv_cons` shares books with a1's confirmation and with NEW+'s consensus input.
   * Near kick-off, CLV ≈ the edge at T (§3.1), so it measures the consensus's efficiency, not the
     pick.
   * The exchange close is the only fix, and 2 days of it exist.
5. **ROI against CLV.** Three samples now show positive CLV with negative ROI: #182 −18%, §3.1 sharp
   −24.5%, and the per-book O/U triggers −25 to −29%. Each is individually compatible with noise.
   Together they are the thing to watch. Log wins against the number the consensus implies on every
   bot and report it next to CLV.
6. **Retention and small n.**
   * Full intraday history exists for about 7 days (§59), so every backtest is a rolling 7-day window
     and every verdict needs forward accumulation.
   * NEW+ and `ou_comb_v1` have about 2 days of point-in-time rows.
   * Nothing here is a verdict. It is a design to collect the right evidence the fastest way.

---

## 7. Reproducing §2.2 and §3.1

This is read-only, about 30 seconds. The scratch script was not committed; its method is:
1. **Load.** Gated `rating_1x2_predictions` / `ou_model_predictions` rows with
   `updated_at < matches.date`, on finished matches.
2. **Snapshots.** All non-live `odds_snapshots` for those matches within the 30 hours before kick-off,
   `handicap_line` NULL or 2.5.
3. **Sets.** `workers.utils.anchor.sets_from_rows` at T with a 180-minute lookback.
4. **Close.** `compute_anchor(sets at KO without the four Estonian books, exclude_book='Pinnacle',
   min_books=5)`, keeping only rows where `source == 'consensus'`.
5. **Price.** The best Estonian leg from a set no more than 60 minutes old.
6. **Edges.** EV = odds × p − 1 against the model, against the anchor at T without the priced book,
   and against the consensus at T without our books.
7. **Bootstrap.** 4,000 resamples over matches, seed 191.

**§2.2** uses the same close logic at a fixed KO−60 instant, per book instead of best-of.
**Next step:** promote the script to `scripts/analysis/own_bot_synthetic_backtest.py` with the §3.2
families as its pre-registered docstring (task (b) of #191).
