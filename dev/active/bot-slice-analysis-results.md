Parent row: #140 in PRIORITY_QUEUE.md

# #140 BOT-SLICE-ANALYSIS — results (run 2026-09-24)

Pre-registration: `dev/active/bot-slice-analysis-prereg.md`, written before any slice was scored and
not edited afterwards. Script: `python3 scripts/analysis/bot_slice_analysis.py [--json out.json]`
(read-only). Direction: 🤖 OWN first, 👥 PICKS second.

## For the owner, in plain words

**The question:** could a tweak (only certain leagues, an odds range, home or away only, closer to
kickoff, a bigger edge, a fresher price) turn one of the losing bots into a break-even or winning one?

**The answer: no tweak we can defend.** We tried 52 subsets, each chosen in advance. We picked
them on each bot's older picks and then checked them on its newer picks. **None passed.** Only 10
subsets were even positive on the older picks, and only 5 of those stayed positive on the newer ones.
That is about what coin-flipping gives you.

Three things matter more than that "no":

1. **Most of the real-money-capable bots can't be judged yet.** Seven of the 11 have fewer than 60
   picks carrying a usable closing price. Most of them are 2 to 15 days old. We aren't saying they
   are bad. There is simply too little data to say anything.
2. **For the sharp-trigger bots, the "CLV" number on /admin/bots is misleading.** These bots bet
   when Coolbet or Unibet is slow to move its price. Their CLV is measured against that same book's
   closing price. When the book never moved, which is the very thing the bot is betting on, that
   CLV comes out as the bookmaker's margin (about −6 to −8%), whatever the bet was worth. For
   example, 35 of 40 Coolbet sharp picks got exactly this treatment. Measured against the wider
   market, the same bots read **+2% to +7%**. But that wider-market figure includes Pinnacle, and
   Pinnacle is the price these bots trigger on, so it flatters them. **For these bots, we don't
   know whether they are winning or losing.** Fixing the yardstick comes before any tweak.
3. **Two bots are just bad, and no subset rescues them.** `bot_ou35_model_v1` was negative in all
   15 of its tested subsets (CLV −6.7%, very firmly). `bot_unified_gate_1x2_paper_v1` was negative
   in every subset. Its known calibrator bug explains why: fix the bug, don't filter the picks.

| bot | settled picks | CLV today (the bot's own yardstick) | best surviving subset | what it means in practice |
|---|---|---|---|---|
| bot_coolbet_1x2_model_v1 | 19 | too few to say (Pinnacle CLV +0.6% on 18) | none (too few to test) | wait for ~100 picks |
| bot_coolbet_ou_model_v1 | 33 | Pinnacle CLV −5.5% | none (too few to test) | stays locked |
| bot_coolbet_trigger_sharp_1x2_v1 | 260 | own-book −6.2% (only 40 usable; 35 = "book never moved") · wider market +6.0% | not testable | yardstick broken; don't switch on and don't retire yet |
| bot_coolbet_trigger_sharp_ou_v1 | 71 | own-book −4.1% (16 usable) | not testable | same as above, and too young |
| bot_ou35_model_v1 | 455 | Pinnacle CLV **−6.7%** (t −21) | **none**: 0 of 15 subsets even positive | no tweak helps; retirement candidate |
| bot_trigger_1x2_sharp_tight_v1 | 263 | own-book −5.9% (75 usable) · wider market +2.5% | none (2 subsets testable, both negative) | yardstick broken |
| bot_trigger_1x2_sharp_v1 | 47 | too few (2 days old) | none (too few to test) | wait |
| bot_trigger_ou_sharp_v1 | 10 | too few (1 day old) | none (too few to test) | wait |
| bot_unibet_trigger_sharp_1x2_v1 | 256 | own-book −6.1% (52 usable) · wider market +7.2% | not testable | yardstick broken |
| bot_unibet_trigger_sharp_ou_v1 | 56 | own-book −5.2% (14 usable) | not testable | too young |
| bot_unified_gate_1x2_paper_v1 | 168 | Pinnacle CLV −4.9%, own-book −6.9% | **none**: every subset negative | fix the calibrator bug, not a filter |
| bot_inplay_slowstate_v1 (not placeable) | 611 | hit rate vs fair odds +1.5 pts (t 0.9) | none; **near miss**: non-featured leagues | not bettable by our placers anyway; see "one thing worth watching" |
| bot_v10_1x2 (not placeable) | 397 | Pinnacle CLV −3.8% (t −4.8), even though ROI is +8.1% | none | the +8% ROI is luck-sized; CLV says it is not beating the market |

**The one thing worth watching.** The in-play bot (`bot_inplay_slowstate_v1`) did better outside our
48 "featured" leagues:

| picks | older half | newer half |
|---|---|---|
| outside the featured leagues | beat the fair odds by +7.0 pts (187 matches) | +3.9 pts (234 matches) |
| inside the featured leagues | −10.5 pts | −11.4 pts |

After correcting for the 52 tries this just misses (corrected p = 0.10), so it is not a finding. It is
also an in-play bot, and our placers only bet before kickoff. The cheap next step is to score this one
hypothesis on picks made **after today**. It is a single test, so no correction is needed. Re-run the
script in about 2 weeks. Don't build anything on it before then.

## Technical detail

### What was tested

* Bots and metrics exactly as pre-registered (§3, §5 of the prereg). Designated metrics:
  * sharp bots: `clv_mc_fresh`, meaning `clv_margin_corrected` on `closing_fresh` rows;
  * model shadow bots: `clv_mc_fresh` **and** `clv_sharp`, which is `leg_clv_sharp`, the executable
    price × the Shin de-vigged Pinnacle close (close ≤ 60 min old);
  * `bot_v10_1x2`: `clv_pinnacle_devig` re-priced at `odds_at_pick_live`;
  * in-play: `1{won} − calibrated_prob`.
* Unit = match. Discovery = older half of each bot's metric-bearing picks, holdout = newer half.
  Minimum 30 matches in discovery and 20 in holdout. One-sided t on discovery; **one Holm family of
  52 tests** across all bots and metrics.
* 86 further cells were below the size minimum and were not tested (listed in the JSON).
* Data-fault exclusion (§79, quarantined or flagged (match, book)): 10 picks in total (tight 2,
  Unibet sharp 1x2 2, unified gate 1, in-play 5). CLV outlier guard |x| > 1: 1 pick (v10), 1 (unified gate).

### Per-bot overall (designated metric, match-clustered)

| bot · metric | picks | matches | mean | t | discovery | holdout | ROI exec disc / hold |
|---|---|---|---|---|---|---|---|
| ou35_model · clv_mc_fresh | 73 | 73 | −4.6% | −8.5 | −3.9% | −5.4% | −39.4% / +14.4% |
| ou35_model · clv_sharp | 447 | 447 | −6.7% | −21.5 | −7.0% | −6.4% | −9.2% / −15.3% |
| sharp_tight · clv_mc_fresh | 75 | 75 | −5.9% | −6.9 | −7.0% | −4.8% | −11.8% / −4.5% |
| unified_gate · clv_mc_fresh | 92 | 82 | −6.9% | −8.9 | −7.0% | −6.8% | +9.9% / −55.7% |
| unified_gate · clv_sharp | 111 | 93 | −4.9% | −2.7 | −3.8% | −6.1% | +22.1% / −50.1% |
| inplay_slowstate · hit−p | 606 | 556 | +1.5 pt | +0.9 | +1.4 | +1.3 | −2.9% / −4.3% |
| v10_1x2 · clv_pin_live | 369 | 369 | −3.8% | −4.8 | −5.4% | −2.1% | +6.4% / +5.6% |

Too few metric-bearing picks (< 60) to slice at all: coolbet_1x2_model (7 / 18), coolbet_ou_model
(4 / 33), coolbet_sharp_1x2 (40), coolbet_sharp_ou (16), trigger_1x2_sharp (36), trigger_ou_sharp (8),
unibet_sharp_1x2 (52), unibet_sharp_ou (14).

### Top of the ranked list (all 52 are in the JSON)

| bot | family · cell | disc matches / mean / t / p | p_Holm | hold matches / mean | ROI disc / hold | verdict |
|---|---|---|---|---|---|---|
| inplay_slowstate | featured league · other | 187 / +7.0 pt / 2.92 / 0.0019 | 0.101 | 234 / +3.9 pt (p 0.050) | +4.4% / −1.6% | near miss (fails Holm; would pass holdout) |
| inplay_slowstate | edge band · low | 101 / +9.5 pt / 2.30 / 0.012 | 0.604 | 123 / +2.8 pt | +7.8% / −1.6% | near miss (fails Holm and holdout: 2.8 < 50% of 9.5) |
| inplay_slowstate | region · europe | 179 / +2.4 / 0.83 / 0.20 | 1.000 | 137 / −1.7 | | — |
| v10_1x2 | edge band · high (> +0.305 EV) | 62 / +0.3% / 0.16 / 0.44 | 1.000 | 111 / +4.3% | +23.8% / +21.6% | — |

Every other tested cell had a discovery mean ≤ +1.4 pt. For `bot_ou35_model_v1`, all 15 tested cells
were negative (best −5.4%). For `unified_gate`, all 5 were negative (best −3.8%). For `sharp_tight`,
both were negative.

Caveats on the family:
* **Duplicated in-play cells.** The in-play bot's locked triggers make four cells identical: `under`,
  `over_under_25`, minute 30–59 and 0 goals (197 matches each). So are three others: `1x2`, minute
  ≥ 60 and ≥ 2 goals. Holm counted them as 52 tests. With the duplicates collapsed m = 47, and the
  best near miss has p_Holm = 0.0019 × 47 = **0.089**. It still fails.
* **The in-play pick-level mean differs from the match-level mean.** Unclustered it is −0.0 pt; per
  match it is +1.5 pt. Several legs on one match dilute it. The prereg fixed the match as the unit.
* **Power, as predicted in the prereg:** a slice needed roughly +5 to +6 pt on discovery to pass.
  The null means "no large edge in any single pre-defined subset", not "no edge".

### Proposed new bot configs

**None.** No slice survived, so none is proposed. The in-play near miss becomes a single
pre-registered forward check, not a bot:

* **H:** `bot_inplay_slowstate_v1` picks in leagues with `leagues.priority IS NULL`, made after
  2026-09-24 12:00 UTC, have a match-clustered mean of `1{won} − calibrated_prob` > 0.
* **Test:** one-sided t at α = 0.05, one test with no correction, once ≥ 150 matches have settled.
  Report the featured-league complement beside it.
* Even if it passes, it is 👥 PICKS-only until an in-play placement path exists. It would be a new
  bot through #139's lifecycle (collecting → published), never an edit of this one.

### Data and method defects found

1. **`shadow_bets.clv_pinnacle_live` has been NULL on every settled shadow pick since ~2026-09-03.**
   Weekly count of `clv_pinnacle_live`: 1,950 of 2,385 in the week of 08-24, 703 of 2,116 in the week
   of 08-31, and 0 of 8,450 since 09-07, while `odds_at_pick_live` is present on 98%. The cause is in
   `workers/jobs/settlement.py`: `_PENDING_SHADOW_BETS_SQL` (line ~82) does not select
   `sb.odds_at_pick_live`, so `bet.get("odds_at_pick_live")` at line ~4148 is always None. `clv_live`,
   which reads the same field, is also mostly missing: 1,500 of 8,450 since 09-07. `leg_clv_sharp` covers the same quantity, which is why this analysis was
   unaffected. It is the same class as the 04fff33f `clv_pinnacle_devig` fix on the sim path. **Not
   fixed here (read-only task); needs a row.**
2. **For stale-book triggers, own-book `clv_margin_corrected` equals −margin by construction.** Among
   `closing_fresh` rows, raw `clv` is exactly 0 on:

   | bot | rows with raw `clv` = 0 |
   |---|---|
   | Coolbet sharp 1x2 | 35 of 40 |
   | tight | 51 of 76 |
   | Unibet sharp 1x2 | 25 of 52 |
   | unified gate | 62 of 94 |
   | ou35 | 47 of 73 |

   These rows score `1/(1+m) − 1` ≈ −6 to −8% whatever the bet was worth. They are the book not
   re-pricing, which is exactly what a sharp trigger bets on. The `bot_scoreboard.clv_mc_*` shown
   for these bots on /admin/bots inherits the same artefact. Candidate replacement: a
   **leave-Pinnacle-out** consensus close (the stale-window study's grader). `leg_clv_sharp.clv_cons`
   excludes only the leg's own book, so it still contains the trigger's anchor (§67). It is also
   only filled for the last ~7 days (`cons_days=7`, retention §59), so its history is short.
3. **Fresh own-book closes are scarce.** `closing_fresh` covers 15–30% of each sharp bot's settled
   picks, mostly after the 2026-09-22 settlement fix. That is why seven capable bots could not be
   sliced.
4. `bot_v10_1x2`: ROI +8.1% alongside Pinnacle CLV −3.8% (t −4.8), and `clv_sharp` +3.0% on the 170
   rows that have a fresh close. The two Pinnacle CLV definitions disagree in sign. The stored
   `clv_pinnacle_devig` close has no age bound (see migration 386's note), so the fresh-close subset
   may be the better reading. This is not resolved here.

### Files

* `dev/active/bot-slice-analysis-prereg.md`: pre-registration.
* `scripts/analysis/bot_slice_analysis.py`: re-runnable, read-only.
* This file.
