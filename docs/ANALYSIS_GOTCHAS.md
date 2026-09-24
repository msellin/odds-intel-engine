# Analysis Gotchas — things that cost time to discover

Written 2026-08-26 during SHADOW-BOT-FIXES. Every entry here is something an
agent (or the operator) either guessed wrong, assumed did not exist, or lost time
rediscovering. Read this before writing an analysis query or claiming a
capability is missing.

---

## 1. Model A/B infrastructure ALREADY EXISTS — do not rebuild it

`SHADOW-INFERENCE (Phase B, 2026-05-24)` in `daily_pipeline_v2.py:2743`.
Set the **`SHADOW_MODEL_VERSION`** env var and the pipeline loads that bundle
alongside production, scores every match with both, and writes the candidate's
rows to `predictions` with `model_version=<shadow>`.

It is running today: `source='xgboost'` carries **both** `v20260705` and
`v20260712` with **4,770 shared match+market pairs** — a proper paired sample.

> **CHANGED 2026-09-24 (#147, migration 419):** shadow/candidate rows now have their OWN sources —
> **`ensemble_shadow`** and **`xgboost_shadow`** (existing rows moved by their `shadow=` reasoning marker).
> Most readers of `source='ensemble'` never filtered `model_version`, so since 2026-08-26 they had been
> mixing the candidate in (calibration fits, ML ETL, shadow passes, in-play, triggers, previews).
> `source='ensemble'` / `'xgboost'` = production only now; A/B = query both sources, as
> `compare_models.py` does. The paragraph below describes the state before that.

**Why this was nearly missed:** shadow rows are written with
**`source='xgboost'`, not `source='ensemble'`**. A check for version overlap
filtered on `source='ensemble'` returns **zero**, which looks exactly like "the
infrastructure does not exist". It does. Query `source='xgboost'`.

## 2. `predictions.source` vocabulary

| source | what it is |
|---|---|
| `ensemble` | the blended production probability the bots actually bet on |
| `xgboost` | raw XGB component (production only since #147, 2026-09-24) |
| `ensemble_shadow` / `xgboost_shadow` | the candidate (shadow) model's rows — #147, migration 419 |
| `poisson` | raw Poisson component (also all the `ah_*` markets) |
| `af` | API-Football's own prediction, not ours |
| `national_team_v1` | separate NT model |

Comparing an `ensemble` number against an `xgboost` number compares different
things. The bots bet the ensemble.

## 3. Market naming differs between tables

`predictions.market` uses `1x2_home` / `btts_yes` / `over25` / `under35` /
`ah_home_-1.00`. `odds_snapshots.market` uses `1x2` + `selection='home'`,
`btts` + `yes`, `over_under_25` + `over`. The bet tables add more variants
(`o/u`, `1X2`, `BTTS`, `combo`).

**Do not hand-roll the mapping.** `settlement.py` has `_normalize_bet_market()`
and `_normalize_bet_selection()`, and they handle the OU line extraction
(`"o/u"` + `"over 3.5"` -> `over_under_35`). Using them instead of a private dict
raised de-vigged-Pinnacle coverage in one backtest from 1,489 rows to 3,448.

## 4. Pinnacle quotes only 8 bet types through API-Football

> **⚠️ SUPERSEDED by §45 (2026-09-05)** — Pinnacle actually sends **19** bet
> types through API-Football, not 8. The "8 bet types" claim below was wrong;
> read §45 for the corrected list and the follow-on consequences. Kept here so
> older references to this section resolve.

Confirmed live against the AF endpoint: Match Winner, Asian Handicap, Asian
Handicap First Half, Goals Over/Under, Goals Over/Under First Half, Total-Home,
Total-Away, Away Team Total 1st Half.

Consequences:
* **BTTS has zero Pinnacle rows and always will.** Any BTTS bot is unvalidatable
  against a sharp line. This is not an ingestion bug — do not go looking for one.
* **double_chance likewise has zero Pinnacle rows**, but DC *is* exactly
  derivable: DC outcomes are unions of 1X2 outcomes and de-vigged 1X2
  probabilities partition the space, so `P(1X) = P(home) + P(draw)`. Implemented
  in `get_devigged_pinnacle_close_prob()`.

## 5. `shadow_bets` needs deduplication — always

The 30-min refresh writes **one row per `shadow_cohort` per pick per day**, ~48
rows for a single pick. Raw counts overstate n by roughly 16-50x.

Use the **`shadow_bets_unique`** view (migration 282, re-keyed to *earliest*
`pick_time` in 283 to match how both admin pages dedupe).

**It is not only n — the duplication is OUTCOME-CORRELATED, so it moves the ROI
itself** (added 2026-09-11 after `executable_shadow_eval.py` was found reading
the base table). Measured over 60 days on `bot_high_roi_global_v2`: winners
carried a mean **22.71 copies** against **7.55** for losers, so 18 distinct
settled picks at +33.3% presented as **"242 bets at +122.7%", t = 11.70** — a
t-stat sports betting does not produce, and the tell that something is
structural rather than skilful. Its executable-Coolbet number went from 92.3%
to **0.8%** once deduped. Two lessons beyond "use the view":

* **a bot whose duplication is EVEN hides the bug.** `bot_v10_all` dedupes
  2,179 rows to 221 picks with ROI moving only 25.8% → 25.6%, so a spot-check on
  the flagship says "fine". Check a bot with a suspiciously high ROI instead.
* **any per-pick JOIN is affected twice over.** The same eval matched each
  duplicate to the nearest book snapshot *to that copy's* `pick_time`, so the
  executable price silently averaged over the price path: `bot_v10_all`'s
  executable-Coolbet ROI moved 15.2% → **9.7%** on dedup. If you join anything
  time-sensitive onto shadow rows, dedupe FIRST. A second view,
`shadow_bets_deduped`, was added on 2026-09-02 by someone who had not read
this entry and dropped again the same day (migration 293) — the two were
verified identical, 0 rows differing. One definition beats two. Measured drift between
first and last cohort row is only +0.20%, so earliest-vs-latest barely moves a
number — but one definition beats two.

## 6. `%` in a SQL string breaks psycopg2

Queries are executed with a params list, so psycopg2 treats every `%` as a
placeholder — **including ones inside SQL comments**. A `%` in a comment raises
`IndexError: list index out of range` at execute time, which looks nothing like
the actual cause. Write `pct`, or escape as `%%`.

(Python docstrings above the query are fine — only the SQL literal matters.)

## 7. There is no pre-2026-05 out-of-sample period in the bet tables

`simulated_bets` / `shadow_bets` start 2026-05. A backtest with `--since
2025-01-01` returns **identical n** to `--since 2026-01-01`. For a genuine
out-of-sample window you must replay against `odds_snapshots` + `predictions`
(both go back to 2023) — see `scripts/lineshop_replay.py`.

Beware when you do: book coverage grew sharply in 2026-05 (AF Ultra era). Best-
of-N prices are genuinely worse in the older data (avg best odds 1.200 vs 1.27 on
the same slice), so an older window can look negative for coverage reasons rather
than strategy reasons.

## 8. CLV vs ROI — the variance numbers worth memorising

Measured on 425 settled shadow picks:

| metric | per-bet SD | bets for ±2% precision |
|---|---|---|
| ROI | 1.341 | **17,259** |
| de-vigged Pinnacle CLV | 0.090 | **78** |

~222x fewer bets for the same precision, and CLV runs close to 1:1 with ROI
across quartiles. **Gate on CLV.** The double-chance bots are the demonstration:
at n=2,436 ROI gave t=-1.83 (undecidable) while CLV gave t=-28.18 (decisive),
and `bot_dc_strong_fav` read *profitable* on ROI (+0.40%) at -4.02% CLV.

## 9. Odds outliers will dominate any unguarded search

A first pass of `clv_slice_search.py` reported `over_under_25` at odds 4.5+ with
**CLV +173.95%, t=+42.49**. A normal OU 2.5 price is 1.5-2.5; a 4.5+ quote so
labelled is a mislabelled line. It is fleet-wide, not one book — Marathonbet
2,413 such rows, Unibet 1,174, 888Sport 424 (to 26.0), Coolbet 163 (to 20.0).

**Always apply the production guard** (`soft <= Pinnacle x 1.30` for OU,
**`x 1.25`** for 1X2/BTTS/DC). With it, the same search returned 0 of 82 positive
slices instead of 14.

⚠️ **The 1X2/BTTS/DC ceiling changed 1.35 -> 1.25 on 2026-09-16**
(OUTLIER-CEILING-CALIBRATED), and the reason matters for anyone writing a search:
**the guard is not "tighter is safer".** Measured on 12,573 settled bets bucketed
by `taken_price / anchor` and controlled for odds level, the **1.05-1.25 band is
PROFITABLE in every odds bucket** (gap of +4 to +7 points over what the taken
price implies) — that band is line shopping working, not noise. The inversion
starts above 1.25: the 1.25-1.35 band runs -6.2 gap / -18.1% ROI at odds 2.2-3.2
and -14.3 / -56.0% at odds >3.2. A proposal to tighten to 1.10-1.15 was measured
and rejected; it would have deleted the only band that works. So clamp at 1.25,
and do not clamp lower without re-running the measurement.

## 9a. Four ways a per-book slice will lie to you (2026-09-16)

Written after a single investigation reached three confident conclusions and
**two of them were wrong.** Each error was cheap to catch and none of them was
caught before it had been stated as fact. The rules below are what would have
caught each one.

**(a) Do not infer a MECHANISM from an OUTCOME.** "Marathonbet shows +91.6% ROI,
therefore its feed is phantom" is not an inference, it is a hypothesis. The test
is whether its prices sit above the market median — they do not (-0.14%, at
consensus), and Bet365 likewise (+0.27%). *Rule: name the falsifier before the
conclusion, then measure it.*

**(b) Check that the cause precedes the effect.** "The Unibet profit came from
its feed being dead" — the feed died 2026-09-12; those 927 bets span months of it
running live. The real evidence was the implied-vs-actual gap, which is a
different claim entirely.

**(c) Never conclude from an UNCONTROLLED aggregate.** Bucketing bets by
`taken_price / anchor` suggested tightening the outlier ceiling to 1.10-1.15.
Controlled for odds level, the 1.05-1.25 band is the *profitable* one in every
bucket and that change would have deleted it. Longshots lose independently of
price quality and will masquerade as a price defect. *Rule: before acting on a
grouped table, control for the confounder most likely to have produced it.*

**(d) A per-book slice is dominated by whoever bet most, not by the book.** The
fidelity monitor's first run flagged Bet365 at +16.8 over 30 days (n=1,087).
931 of those were `double_chance` from three RETIRED bots, two of which
(`bot_dc_specialist`, `bot_dc_value`) emit **identical picks** and so counted the
same evidence twice. On the book's full sample Bet365 reads +2.9; with retired
bots excluded, **-6.0**. *Rule: exclude retired bots, require the effect across
>=2 bots AND >=2 markets, and state n per cell — a book-level defect cannot live
inside one strategy.*

**And the sample-size floor that would have prevented one published number:**
+290% ROI was quoted from **n=20**. Nothing per-book below roughly n=200 gets
stated without a confidence interval attached, or it does not get stated.


**(e) Query `shadow_bets_unique`, NEVER `shadow_bets`.** The base table stores
one row per RE-EVALUATION: the half-hourly refresh re-emits the same (bot, match,
market, selection) every pass and each is its own settled row. All-time that is
**162,191 settled rows for 20,444 real picks -- 7.9x**, worst case 51 copies of
one pick. **The view `shadow_bets_unique` already solves this** (DISTINCT ON the
four keys, ORDER BY pick_time, i.e. first emission) and the product reads it. It
also already carries `clv_margin_corrected`, so do not recompute margins by hand.

⚠️ **Recorded because I got this wrong on 2026-09-17 and briefly filed it as a P0
defect.** Querying the base table directly produced n inflated 7.9x, t-statistics
inflated ~2.8x, and an ROI for `bot_v10_all` of +5.83% where the deduped truth is
**+9.74% on n=573**. Note the ROI moved too -- re-emission is NOT uniform across
winners and losers, so the comforting "duplication cancels in a ratio" is false.
The fix already existed; the bug was in my query.

**(f) One bad table reference propagated into four wrong public claims.** Worth
recording as a shape, not just an instance. On 2026-09-17 a single habit --
querying `shadow_bets` instead of `shadow_bets_unique` -- produced, in order: a
fabricated "7.9x ledger defect" filed as P0; three bookmakers (10Bet, Unibet,
Marathonbet) accused of quoting phantom prices when deduped they read
**+1.7 / -1.8 / -5.5**, i.e. entirely normal; an ROI of +5.83% where the truth is
**+9.74%**; and a monitor built the same day that inherited the same bug. Each
error looked independently plausible and each reinforced the others.

*The tell:* several surprising findings in one session, all pointing the same
direction. That is more often one broken input than four discoveries. Check the
source of the data before building the fourth conclusion on the first three.


**(g) The published numbers come from `simulated_bets`, NOT `shadow_bets`.**
`dashboard_cache` (which `/performance` reads) is built from `simulated_bets`,
which has **zero duplication** (4,661 rows -> 4,661 picks). `shadow_bets` is the
paper-shadow fleet and is a different population entirely. An entire session's
conclusions were drawn from the wrong one.

**(h) ROI means `sum(pnl)/sum(stake)`, never `avg(pnl/stake)`.** The two agree
only under flat staking. Several bots are Kelly-variable (v10 stakes 1.35-17.38),
where mean-of-ratios over-weights small stakes: it reported **+10.94%** where the
published, stake-weighted truth is **+6.51%** -- and flipped the confidence
interval from "excludes zero" to "includes zero", i.e. from a publishable claim
to an inconclusive one.

**(i) `select ... limit 1` with no ORDER BY returns an arbitrary row.** This
produced a confident "dashboard_cache is 4 months stale" about a table whose
newest row was written that morning.


**(j) Kelly vs flat staking makes no measurable difference here — do not re-open
it on one bot's sample.** Settled 2026-09-17 on **20,281 `published_picks` with a
price**: flat −8.19%, Kelly −7.98%, a −0.21 point difference with Kelly
marginally ahead, and it holds across every odds band (−0.66 to +0.62) and every
market (−1.49 to +0.33). A single bot (v10) showed a +4.6 point flat advantage
with a clean-looking stake-quintile gradient that survived an odds control; the
fleet test then split 8-13 and the 20k-pick test found nothing. **A within-bot
staking gradient at n≈600 is noise.** Note also `published_picks` is the right
table for questions like this: 42,916 settled rows, `outcome` values are
**`hit`/`miss`**, not `won`/`lost`.


**(k) Any unguarded odds search will "find" a huge edge, and it will be garbage.**
Measured 2026-09-17 on 1.29M book-selection quotes: a Kaunitz-style cross-book EV
search with NO outlier guard returned **+75.21% ROI, t=+14.46** in its top band —
entirely mislabelled lines, stale quotes and wrong markets, with EVs running to
+900%. It dragged every book positive, including the two we execute at. Applying
the production **1.25 ceiling** (price vs vig-free consensus) removed all of it
and every band came back **negative**. Same lesson as §9 but worth restating with
the number: the guard is not a refinement, it is the difference between a result
and a fiction. Use ≥5 books for the consensus, not 3.


**(l) A consensus requirement silently selects the market you are trying to
test.** A cross-book study requiring ">=5 books" covered **27,725 of 55,248
fixtures** — half the universe — and the excluded half was precisely the thin
leagues the study was meant to evaluate. Anchor on de-vigged Pinnacle (510 of 663
leagues) instead of demanding a panel, and state the coverage.

**(m) In thin markets Pinnacle's price is a PLACEHOLDER, not a consensus.** Its
stake limits run **$2,000-7,500 on majors and $100-400 on lower tiers**. "Edge vs
de-vigged Pinnacle" behind a $150 limit is two soft prices disagreeing. A measured
thinness gradient (thin −1.79% vs thick −9.24%) is therefore **not** evidence of
exploitable softness until `limits[].amount` is captured and used as a gate. The
field is free in the guest API, no aggregator sells it, and as of 2026-09-17 it is
captured nowhere in this codebase.

**(n) Grep for the tool before proposing to build it.** `scripts/pinnacle_movement_research.py`
and `scripts/ops/egress_probe.py` already hit the Pinnacle guest API, and
AF-PINNACLE-NOT-PINNACLE-2026-09-14 already ran the paired real-vs-AF measurement
— while I spent hours reasoning about whether the feed was genuine and then
proposed running it as new work.


**(o) Pinnacle's stake limit re-encodes league tier and nothing more.** Tested
2026-09-17 on 1,269 settled bets across 57 leagues. The spread is real and huge
(La Liga max $20,000 vs Argentina Primera B $125) but bucketing ROI by it is
**non-monotonic**, and the decisive control is that the top limit bucket and the
top-5-league set are **the same 215 bets**. Within top-5, splitting by limit
separates nothing (+21.00% vs +28.35%, both t≈1.8). Do not re-open this as a
validity gate without a mechanism that is not league identity.

⚠️ And note how it nearly went the other way: a 29-league profile produced a
clean monotone gradient (−5.20% / +1.86% / +23.22%) that vanished at 168
leagues. **Profiling only the featured leagues selects exactly the confounder
you are trying to control for.**


**And the rule that keeps catching me:** every one of (a)-(e) was found only
after a confident wrong answer had already been written down. The measurement
that refutes a hypothesis is almost always cheaper than the one that supports
it -- run it first.

## 10. Comparing bookmakers by raw Brier is invalid

Each book prices a different slate, so Brier partly measures how hard its games
are. A raw ranking put Pinnacle **14th of 15**. Compared **pairwise on matches
both books price**, every one of the 15 is worse than or equal to Pinnacle.
Always pair on shared fixtures. (`scripts/bookmaker_sharpness_rank.py`.)

## 11. Trainer metrics that are not what they look like

`train_b_ml3.py` prints `precision=1.000 recall=0.978` at its chosen threshold.
That comes from `_pick_threshold(model, scaler, X, y)` — scored on the **full
training set**. It is in-sample and is not evidence. The honest metric is the
walk-forward AUC, and its **last fold** is the one resembling next week.

## 12. Heavy replay queries get OOM-killed silently

`model_version_clv_scoreboard.py` over all versions x all markets since 2026-05
died with no traceback, twice. Narrow the window or the version list. Also note
that piping a long-running script through `grep | tail` buffers everything —
output appears empty until exit, and is lost if the process dies. Redirect to a
file instead.

---

## 13. A book's market list describes OUR PARSER, not the book — corrected 2026-09-06

**This entry used to say "Epicbet has no `double_chance`". That was wrong, and
the way it was wrong is the reusable lesson.**

`odds_snapshots` gained **Epicbet** on 2026-08-27 (EPICBET-ODDS-INGEST). We read
its bulk league listing (`match.getFoByLeague`), which across 204 sampled
fixtures in 198 leagues has only ever emitted market groups 45, 15, 19, 69, 96,
2055, 98, 6, 65, 413, 5, 7, 67, 47. From that we concluded the book did not
offer DC, corners or cards.

The book offers all of them. They live on a different endpoint —
`match.getSidebets?marketType=main`, which returns **192 groups / 422 markets**
for a top fixture against the listing's handful. Double chance is group 96 and
was present on **12 of 12** fixtures sampled. Corners, cards, first-half
markets, team totals and correct score are all there too (241 distinct group
ids). EPICBET-SIDEBETS-CORNERS-2026-09-06 now reads it.

**The rule: "book X does not offer market Y" is almost never a fact about the
book.** It is a fact about the endpoint you asked, the parameters you sent, and
the groups your parser whitelists. Before writing that sentence anywhere — a
doc, a gotcha, a task's premise — open the book's own site, watch what the page
fetches, and confirm the market is genuinely absent rather than merely
unrequested. The same mistake cost us the Coolbet corners limit (`limit: 13`
where 60 was available) and the assumption that AF sends 8 bet types when it
sends 19.

So a query of the form "books that price DC" silently excludes Epicbet, and a
per-book coverage ratio computed across all markets will make Epicbet look
worse than it is. Compare books **pairwise on markets both actually price** —
the same trap as gotcha 10.

Two more Epicbet-specific facts worth knowing before writing a query against it:

* **It prices quarter OU lines (0.75, 1.25, 2.25 …); we drop them.** There is no
  `over_under_XX` column vocabulary for quarter lines, so only .5 lines land.
  Absence of a 2.25 row does not mean Epicbet did not quote it.
* **Reserve and youth fixtures are guarded, not matched.** `_squads_compatible`
  refuses to match "X Res." / "X U21" / "X W" against a first-team event, so
  those fixtures have Epicbet rows only when Epicbet itself lists the reserve
  side. This is deliberate — without it, one reserves fixture produced a fake
  +87% edge against Pinnacle. The Coolbet path does **not** yet have this guard
  (COOLBET-SQUAD-GUARD, open).

## 14. CLV is meaningless for in-play bets — do not gate on it

`clv_pinnacle_devig` compares the taken price against Pinnacle's **pre-match
close**. An in-play bet placed at minute 22 with a goal already on the board is
a different market entirely, so the comparison is not a closing-line value at
all.

The numbers announce themselves as nonsense once you look: `inplay_c` **+134%**,
`inplay_j` **+74%**, `inplay_n` **+66%** — while all three have ROI between
−7% and −28%.

Consequence: gotcha 8's advice ("gate on CLV", n≈78 for ±2%) applies to
**prematch only**. In-play has to be judged on ROI, which needs ~17,000 bets for
the same precision. **No in-play bot currently has a decisive record**, and any
claim that one does is measuring the artifact. Judge in-play on ROI plus the
real-vs-simulated agreement in `real_bets`, and say the sample is indecisive
rather than quoting a CLV.

Related: `recommended_bookmaker` is **NULL on all 1,246 settled in-play bets**,
so there is no book attribution outside the 55 that reached `real_bets` (all at
Coolbet). A per-book in-play query returns nothing and that is not a bug.

---

## 15. Double chance at Coolbet is DEAD — do not scope it again

Settled 2026-08-28 from two independent directions. **Do not re-open this
without new evidence that Coolbet's DC pricing itself has changed.**

**The market is priced worse than the edge available.** Coolbet's double-chance
quotes sit roughly **4-6% below de-vigged Pinnacle fair value**, which is simply
its margin on that market. Measured live over a 2-day window with same-window
(≤30min) pairing and a structural guard: **zero** qualifying picks at 2%, 3% or
5% edge; median edge **-5.8%**; the single best DC price in the whole sample
still **-3.2%**, i.e. worse than fair. There is no tail to fish in.

**History says the same thing.** All three retired DC bots posted negative CLV
against de-vigged Pinnacle — the very anchor a new bot would use:

| bot | n (CLV) | CLV |
|---|---|---|
| `bot_dc_value` | 113 | -3.63% |
| `bot_dc_specialist` | 54 | -5.71% |
| `bot_dc_strong_fav` | 31 | -3.69% |

Combined **n=198**, past the n≈78 CLV threshold, so this is a verdict rather
than a small sample. Their realised CLV matches the live probe's median almost
exactly — the same number arrived at from bet outcomes and from raw prices.

**Two traps that make DC look alive when it is not:**

* **Stale pairing invents edge.** Taking each book's latest row independently
  (no time window) produced apparent edges of **+55.6% / +50.7% / +45.4%** from
  quotes **10-17 hours apart**. One offered `x2` at 2.20 while Pinnacle's *away
  alone* was 2.26 — structurally impossible, since a DC price must be shorter
  than either leg it contains. Any DC probe without a pairing window will
  rediscover this "opportunity".
* **Pinnacle quotes no DC at all**, so it must be derived from de-vigged 1X2
  (gotcha 4). That derivation is exact and is not the problem — the problem is
  the price Coolbet offers.

Scoped and rejected on 2026-08-28: `COOLBET-DC-BOT-SCOPE-2026-08-28`.

---

## 16. You cannot compute AH CLV by fixing the handicap line

Pinnacle quotes a LADDER of Asian handicap lines simultaneously — **7 to 10+
distinct lines per match** — not one line that moves. So "the last Pinnacle
quote at handicap -1.0" is not a closing price: it is the last time that
*rung* was priced, which averages **12.78 hours before kickoff** (median 0.5h,
i.e. strongly bimodal — some rungs are quoted to the whistle, others are
abandoned early).

Measured 2026-08-28 on a Coolbet AH backtest: comparing the taken price to the
last quote at the same fixed line gave **+19.13% mean CLV at t=+7.75** — which
would be an enormous edge — while the same picks returned **-15.33% ROI**.
Gotcha 8 records that CLV normally runs ~1:1 with ROI, and that per-bet CLV sd
is ~9%; this measurement's sd was **24.3%**. Every diagnostic said the metric
was broken, not that a huge edge had been found.

**If you need AH CLV**, you must compare like for like — either the closing
price at whatever rung Pinnacle finished on, converted to a common basis, or
restrict to lines still actively quoted near kickoff. Do not fix the line and
call the last row a close.

The same shape has now appeared three times in one day: fixed-line AH "CLV",
stale cross-book pairing inventing +55% DC edges, and Asian-handicap rows
grouped without `handicap_line` producing a fake +17% favourite drift. **When a
number looks too good in this dataset, check what it is being compared against
before believing it.**

---

## 17. The Coolbet odds scraper is built on a DEAD endpoint, not a blocked one

Diagnosed 2026-08-28 after the feed died three times in one day (6h, 4.5h, and
80h historically). The reflex diagnosis is "Imperva is blocking us" and it is
**wrong**.

What is actually true:

| probe | result |
|---|---|
| `GET coolbet.com/en/sports` via plain `requests` | **HTTP 200** |
| `GET coolbet.com/s/search/v2` via plain `requests` | **HTTP 403** |
| Same `/s/search/v2` **from inside the logged-in browser** | **hangs** (aborts at 12s) |
| Match page HTML via plain `requests` | 200, but **6,058 bytes with zero odds** |

The third row is the one that matters. If this were an Imperva fingerprint
block, the request would succeed from inside a real Chrome with real cookies.
It does not — it hangs there too. **`/s/search/v2` is deprecated/tarpitted for
everyone**, so no amount of cookie refreshing, user-agent matching or
FlareSolverr can bring it back. Cookie age was never the cause.

Two supporting facts:

* **Coolbet is an SPA.** The match page is a ~6KB shell; odds are rendered
  client-side, so plain HTTP cannot read prices even on a 200.
* **Live prices stream over Socket.IO** at
  `wss://www.coolbet.com/s/pusher/socket.io/` — which is why `networkidle`
  never settles on a match page and why almost no XHR is visible.

**Consequence:** odds collection from Coolbet requires a browser that executes
JS. That is not a workaround, it is the only surface that exists. The UI placer
already does exactly this successfully (`read_outcomes` / `read_ou_grid`), and
on 2026-08-28 wrote 413 rows across 14 matches in a single pass while the API
scraper was returning nothing.

Before "fixing" the Coolbet feed again, check whether the endpoint still exists
rather than assuming the client is being blocked.

---

## 18. Pick ONE ledger per bot — do not union `simulated_bets` and `shadow_bets`

> **Title corrected 2026-09-02.** This used to read *"a bot's paper ledger is
> EITHER simulated_bets OR shadow_bets — never both"*, which contradicts its
> own body: pipeline bots write to both, deliberately. The old title cost a
> false P1 bug report (`BTTS-DUAL-LEDGER-VIOLATION`, filed and withdrawn the
> same day) when a dual-writing bot was read as a data-integrity violation.
> **27 of 41 bots write to both tables**, concurrently — that is the designed
> behaviour, not a defect. The rule is about which one you *read*.

Pipeline bots (`bot_v10_all`, `bot_btts_all`, `bot_opt_*`, `inplay_*`) write to
`simulated_bets`. Sweep and shadow bots (`bot_sweep_*`, `bot_pin_*`,
`bot_coolbet_value_v1`) write only to `shadow_bets`. Query one table and half
the fleet looks **dormant with zero activity** — which is exactly how
`bot_pin_1x2_home_v1` accumulated 104 settled picks at +13.1% ROI without ever
appearing in a weekly bot review (BOT-GATE-REACHABLE, 2026-08-28).

**Do not union the two to fix it.** Pipeline bots ALSO re-record into
`shadow_bets` as part of the timing-cohort experiment, so a union double-counts
— `bot_v10_all` has 357 sim rows and 319 shadow rows covering 85 of the same 93
matches in 30d. And the join that would catch the duplicates silently fails:
per gotcha 3, `shadow_bets` spells markets `1X2` / `O/U` / `over_under_25`
where `simulated_bets` spells them `1x2` / `o/u`, so joining on `market`
returns **zero overlap** and the two ledgers look independent when they are the
same picks twice.

Pick one source per bot: `simulated_bets` when it has rows, else
`shadow_bets_unique` (gotcha 5 — always the view, never the base table). Also
prefer `shadow_bets.clv_pinnacle` over `shadow_bets.clv`; the plain column is
anchored on the pick's own book and is not comparable to `simulated_bets.clv`.

---

## 19. A promotion gate scored on real money cannot promote anything

The weekly bot review emitted **zero PROMOTE and zero DEMOTE verdicts in 10
consecutive weeks**, and the reason was structural, not empirical: promotion
required 20+ settled `real_bets`, while `COOLBET_RECORD_ALLOWED_MATURITY=calibrated`
means only *calibrated* bots ever write to `real_bets`. A beta bot could not
earn the real-money history the gate demanded, because being beta is precisely
what stopped it placing real money. Every non-calibrated bot sat at real n <= 2
indefinitely.

The general shape, worth checking in any gate you write: **the evidence a gate
demands must be producible by something on the wrong side of the gate.** Two
corollaries that bit at the same time:

- Gating on CLV excludes in-play bots permanently, because in-play has no
  closing line (gotcha 14). Score them on a stiffer ROI bar instead of leaving
  them ineligible forever.
- Gating DEMOTE on `maturity == 'calibrated'` means a losing *beta* bot is
  never demoted — and beta is visible to every signed-in user on `/picks`.

Fixed 2026-08-28 with a paper-evidence path (n >= 100, ROI > +3%, CLV > +3%,
picks spanning >= 21d). The span requirement matters: `bot_pin_1x2_home_v1`
reached n=104 in **6 days**, and volume alone is not evidence of durability.

---

## 20. Line-shopping has only existed since 2026-04-28 — there is no long backtest

`odds_snapshots` goes back to **2023-01-27**, which makes a multi-year
line-shopping replay look available. It is not. Before **2026-04-28 the archive
holds Pinnacle and nothing else**, so there is no second book to shop against:

| book | first seen |
|---|---|
| Pinnacle | 2023-01-27 |
| Unibet / Betano / 10Bet / Marathonbet | 2026-04-28 |
| 888Sport | 2026-04-30 |
| Coolbet | 2026-05-20 |

A `lineshop_replay.py --start 2025-01-01` therefore returns exactly the same
numbers as `--start 2026-04-28`, and any picks it "adds" from the earlier period
are zero. Worse, the old output rendered that as a bare `(no qualifying picks)`
line, indistinguishable from a config that genuinely never fires — which is how
a 20-month replay of `bot_pin_1x2_home_v1` came back empty and looked like a
dead strategy rather than a missing archive. The script now warns explicitly
(`LINESHOP_DATA_START`).

The honest maximum for any line-shop bot is **four months**. That is not enough
to clear a t-gate: measured 2026-04-28 → 2026-08-26,

| bot | n | ROI | t |
|---|---|---|---|
| `bot_pin_1x2_home_v1` | 583 | +6.91% | +1.20 |
| `bot_sweep_ou35_v1` | 379 | +1.77% | +0.29 |
| `bot_sweep_ou25_v1` | 365 | +1.18% | +0.21 |

Related: the two sweep bots **claim ~+6.5% edge and realise ~+1.5%**, while
`bot_pin_1x2_home_v1`'s claimed and realised edge agree to two decimal places.
A claimed-vs-realised gap that large is a pricing bug, not variance — diagnose
it before reading anything into either sweep bot's ROI.

---

## 21. Some Coolbet odds rows are attached to the WRONG fixture date

Until 2026-08-31, `odds_snapshots` rows sourced from Coolbet could belong to a
fixture played on a **different day** than `matches.date` says.

`COOLBET-FUZZY-DATE-GUARD` was written to reject same-team different-day
candidates, but it read `ev["start"]` while Coolbet's `search/v2` names the
field **`match_start`** (the same name `fo-category` uses, and which the
fo-category parser already read correctly). `_parse_iso_start(None)` returns
`None`, and the guard is written as `if ev_start is not None:` — so it skipped
its own check on every candidate it ever saw. Measured over the whole snapshot
log: **217,518 match lines, every single one reporting `0 candidates rejected
on date`.** The guard had never once fired.

Worked example — Atlético Grau v FBC Melgar, 2026-08-31:

| Source | Kickoff (UTC) |
|---|---|
| API-Football (`matches.date`) | 2026-08-31 20:00 |
| Coolbet `match_start` | 2026-09-01 20:00 |

The match was postponed a day for Melgar's travel to Piura. Coolbet moved;
API-Football did not. We matched the moved event at name score **100**, stored
**82 price rows** against a night it is not played, and `bot_coolbet_value_v1`
raised a draw @ 3.14 off them.

**What this means for analysis:**

- Do not assume a Coolbet row in `odds_snapshots` was captured for a fixture
  played on `matches.date`. Rows written **before 2026-08-31** carry no date
  verification at all.
- This inflates apparent coverage: some "Coolbet prices this fixture" rows are
  really "Coolbet prices a fixture we have mis-dated".
- CLV and closing-line work is the most exposed — a "closing" price for a
  postponed match is not a close.
- Rows written **after** the fix are date-checked to ±6h.

**Related, still open:** API-Football does not always follow a postponement, so
`matches.date` can be stale even now. The fix makes that *visible* rather than
silent — `fuzzy_match_event` now logs a `DATE MISMATCH ... OUR fixture date is
probably stale` warning when a name-perfect candidate is rejected only on date
— but it does not correct the date. See `AF-STALE-FIXTURE-DATES-2026-08-31`.

---

## 22. Retired bots keep writing `shadow_bets` — market aggregates are 85% dead weight

`SHADOW-RETIRED-OK` (2026-05-20, `daily_pipeline_v2.py:3120`) deliberately keeps
retired bots producing shadow picks; only `simulated_bets` respects
`is_active`. So **any GROUP BY market over `shadow_bets` is dominated by bots
that stopped placing months ago.**

Measured 2026-08-31 over 30 days: `double_chance` alone was **59,488 of ~70,000
settled shadow rows (85%)** — every one of them from `bot_dc_specialist`,
`bot_dc_value` and `bot_dc_strong_fav`, all `is_active=false` with `retired_at`
in May/June 2026. Reading that table raw makes DC look like the portfolio's
biggest bleed when it carries **zero live exposure**.

Always join `bots` and filter `b.is_active = true` when the question is "what
are we actually betting". The unfiltered and filtered views disagree wildly:

| market | all shadow rows | ACTIVE bots only |
|---|---|---|
| 1x2 | +12.36% (n=10,503) | **+26.56% (n=4,733)** |
| over_under_25 | −0.02% (n=2,886) | **−6.17% (n=2,642)** |
| btts | −7.14% (n=1,593) | **−6.36% (n=1,502)** |
| double_chance | −6.39% (n=59,488) | **not bet at all** |

**Update 2026-09-03 (migration 298).** Measured: **128,243 of 143,602 rows
(89.3%)** of `shadow_bets` is retired-bot output, from 20 retired bots still
writing. Those writes are deliberate and must stay — `SHADOW-RETIRED-OK`
(2026-05-20) keeps them so the recovery criterion (">=30 bets at >=3% ROI in
shadow_bets") stays measurable. Do not "fix" this by stopping them; that
deletes the only evidence that can un-retire a strategy.

`shadow_bets_unique` now carries `bot_retired_at`, `bot_is_active` and
`bot_name`, so the choice is explicit at the point of use:

    WHERE bot_retired_at IS NULL       -- performance analysis (the default)
    WHERE bot_retired_at IS NOT NULL   -- alpha-recovery analysis

An unqualified aggregate over the view is ~89% dead strategies and will
mislead by more than its own sign: pooled shadow ROI reads -3.44% (t=-3.77),
live-bots-only reads **+8.19% (t=+2.49)**.


## 23. Do NOT normalise the `1X2` / `1x2` case split — it is load-bearing

It looks like a data-hygiene bug (older named bots write `1X2` / `O/U` / `BTTS`;
newer sweep and coolbet bots write `1x2` / `over_under_25` / `btts`). It is not
safe to "clean up".

Per gotcha 6, the vocabulary mismatch is exactly what stops a
`shadow_bets` ∪ `simulated_bets` union from double-counting: pipeline bots
re-record into both ledgers, and the join on `market` returns zero overlap
*because* the spellings differ. Normalise the stored labels and every existing
analysis that unions the two silently starts counting the same picks twice.

Normalise **in the query**, never in the table — `settlement.py`
`_normalize_bet_market(market, selection)` is the canonical mapping and also
extracts the OU line from `selection`. Aggregating on the raw column splits one
market across two rows and makes both look like different strategies.

## 24. A model head can lose to a coin and still look "slightly behind"

Log loss and Brier are meaningless in isolation. Score every head against a
**constant fixed at the observed base rate**:
`-(p·ln p + (1-p)·ln(1-p))` where `p` is the realised outcome rate.

On the 2026-08-17→31 holdout (n=6,717) the OU 2.5 head scored **0.7965 against
a no-skill baseline of 0.6743** — measurably worse than guessing the average.
BTTS failed the same test. Both had read as merely "a few percent behind the
incumbent" for months. `weekly_eval_and_compare.py` now prints this column and
flags failures with `!`; do not promote a head that carries the flag.

**Caveat found 2026-08-31:** no-skill failure offline does *not* automatically
justify killing the bots that trade it. `bot_sweep_ou25_v1` is −4.48% ROI on
n=2,301 but **CLV +0.05%, t=38.2**. The two measure different things — the
baseline test scores all matches, CLV scores only the filtered picks. Note
also that a t of 38 on a +0.05% mean is statistical, not economic,
significance; it will not cover the vig. Resolve the conflict before acting.

## 25. Cross-book price comparison must match on `handicap_line`

`odds_snapshots.selection` for `asian_handicap` is only `home` / `away` — the
line lives in a separate `handicap_line` column. Joining two bookmakers on
`(match_id, market, selection)` therefore compares a −0.5 quote against a −1.5
quote.

Measured 2026-09-02 on live Coolbet-vs-Epicbet data: the unmatched join
reports Epicbet **+17.9% to +22.6%** better on AH. Line-matched, the real
answer is **+0.86%**. Two soft books do not differ by 20% on the same line —
if you see a double-digit cross-book difference, the join has come unmatched.

Join with `IS NOT DISTINCT FROM`, never `=`: every non-handicap market carries
`handicap_line IS NULL`, and `NULL = NULL` drops the whole row silently, so a
plain equality quietly throws away 1X2, OU and BTTS.

Also reduce to one quote per book per outcome (`DISTINCT ON … ORDER BY
timestamp DESC`) before averaging, or fixtures that happened to be polled more
often dominate the result.

`scripts/book_uplift_report.py` does all three; smoke `BOOK-UPLIFT-REPORT`
range-checks the AH figure so an unmatched join fails rather than reporting a
flattering number. Sibling of gotcha 16 (AH CLV by fixed line is invalid).

## 26. Feature coverage must be split by `matches.status`, or it lies

A feature's overall MFV coverage tells you nothing about whether the model can
actually use it. Split by `matches.status` — **scheduled** rows are what
inference sees, **finished** rows are what training sees.

Measured 2026-09-02 (n=356 scheduled, 12,895 finished, since 2026-08-01):

| feature | SCHEDULED | FINISHED |
|---|---|---|
| `injury_severity_score_home` | **0.0%** | 2.8% |
| `xg_overperf_home` | **0.0%** | 6.7% |
| `team_avg_player_rating_home` | **0.0%** | 6.9% |
| `form_ppg_home` | 83.7% | 90.9% |
| `weather_temp_c` | 14.9% | 8.6% |

The first three are computed **post-match** and keyed to the settled match they
were derived from, so they are structurally absent for the fixture being
predicted. Their headline coverage (2–7%) looks merely weak; it is actually
**zero where it counts**, and every populated row is in the training set only.

Two consequences worth internalising:

1. **Backfilling such a feature makes the model worse, not better.** It raises
   training-side coverage while inference stays at zero, widening train/serve
   skew. `FEATURE-COVERAGE-BACKFILL-2026-08-21` proposed exactly that for all
   three and was stopped on this evidence.
2. **A near-zero model coefficient is not proof a signal is weak.** The
   2026-08-31 meta refit dropped these three as near-zero-coefficient. The
   honest reading is not "no predictive value" but "never present at serve
   time, so the model correctly ignored them".

Before proposing any feature work, run the status split. A feature that is
`0.0%` on scheduled rows needs re-keying (team + as-of-date, so the value
carries to the team's *next* fixture) or removing — never backfilling.

## 27. Comparing a rival's published odds to "best price we saw" needs a MATCHED book

Checking whether a tipster's claimed prices were ever reachable is the core of
the Forebet fraud case. The obvious statistic — *how often does the claimed
price exceed the best quote in `odds_snapshots`* — is **not comparable across
sources**, and the first version of
`scripts/verify_forebet_odds_cross_source.py` reported a number built on that
mistake.

The reason: Betaminic publishes the **Bet365** price, and Bet365 is the best
price on the market only **19.3%** of the time (measured over 39,410
match/market/selection groups, last 30d). So Betaminic's claimed odds sit below
best-of-books *by construction*, its exception rate is structurally suppressed,
and the resulting "Forebet is 1.9x the honest baseline" flattered our own case.
Any source that quotes a sharp book will look honest on this test; any source
that quotes a soft book will look guilty. It measures book choice, not honesty.

**Two things do survive:**

1. **Magnitude, not frequency.** Fuzzy fixture matching and snapshot timing
   overshoot by a few percent; they do not overshoot by 50%. Use the tail of
   `claimed / best`. Forebet claims **>1.5x the best price anywhere on 9.5%** of
   picks; Betaminic on **0.7%**. That gap is not a book-choice artifact.
2. **One shared reference book.** Measure every source against Bet365 and
   nothing else. Then split by won/lost — a bettor cannot systematically obtain
   better prices on the bets that happen to win, because the result is not
   knowable at bet time. Forebet: **18.1%** of winners carry a >1.5x-Bet365
   price against **8.4%** of losers (+9.8pp, p=2.4e-06). Betaminic: **0.8% vs
   0.7%** (p=0.77) — flat, which is what an honest record looks like.

Also note the winner/loser split is **not** clean on its own: Betaminic shows a
+12pp skew on the raw best-of-books version, because short-priced favourites
both win more often and are quoted more tightly. Always run the control.

Related: #25 (cross-book joins need `handicap_line`), #16.

## 28. A competitor's headline ROI is rarely measuring the same thing as ours

Before comparing any rival's ROI to ours, check three things. Every one of them
was wrong for WinnerOdds until 2026-09-02, all in the same direction (we
understated them, and so overstated our own lead).

**(a) Which markets.** WinnerOdds' `apuesta` field is a compact pick code that
fully identifies the market (`1`/`x`/`2`, `o2.5`/`u2.5`, `o3.5`/`u3.5`,
`ah±0/0.5_1/2`), and we were discarding it as `market="mixed"`. Their published
record is **47% Asian Handicap — a market we do not model at all** — plus 16%
OU 3.5. Only **690 of 1,852** bets are in our 1X2 + OU 2.5 cohort. Their ROI on
the comparable subset is **+7.67%**, not the +4.50% we were publishing.

**(b) Which staking.** They stake Kelly-style (mean €37 over the window); we
stake €10 flat. ROI is profit over turnover, so two staking schemes give two
different ROIs for the identical set of bets. Re-settle their picks at our flat
stake before comparing. Here it barely moved the number (+7.82% Kelly vs
+7.67% flat), which is luck, not a reason to skip it.

**(c) The settlement vocabulary.** Their statuses are
`WIN / LOOSE / HALF_WIN / HALF_LOSE / VOID` — note **`LOOSE`**, their spelling
of a loss. `scripts/audit_vs_winnerodds.py` exported the picks CSV by testing
for `"lose"`, so all **752 losses in the window silently became blank** and the
published CSV — the file behind the landing's "Verify" link — contained only
wins and voids. Recomputing ROI from it gave **+86%**. The aggregate JSON was
fine because `wo_summary` used the correct vocabulary; the two paths had drifted.
Derive both from one mapping.

**Market-level vs product-level is a real choice, not an oversight.** Restricting
to shared markets answers *"on the same bets, who prices better?"* — a claim
about model skill. It is not the same as *"which subscription returns more?"*,
where a rival's AH coverage is a feature rather than a confound. The landing
publishes the market-level comparison; see `COMPETITOR-PRODUCT-VS-MARKET` in
PRIORITY_QUEUE.md.

**Not everything is fixable.** Tipstrr's per-bet market detail is genuinely
paywalled — its data is (tipster × month) aggregate across all bet types, so no
amount of parsing makes it like-for-like. It was dropped from the landing
2026-09-02 rather than labelled, because it is a *marketplace*: any figure is a
consequence of which tipsters you list (we pooled 3 of 8 hand-written slugs, all
three inactive, out of thousands).

Related: #27 (cross-source odds verification), #3 (market vocabularies).

## 29. MAX-ever odds is a REACHABILITY test, never an EXECUTION price

`verify_forebet_odds_cross_source.py` prices each bet at `MAX(odds)` over every
snapshot we ever recorded, deliberately: when asking *"was this claimed price
reachable at all?"* the most generous possible benchmark makes the answer
unarguable.

Re-using that same number as the price a bettor would have GOT is a category
error, and it produces a result obviously too good to be true. Settling
Forebet's picks at MAX-ever rates them at **+37.66% ROI — better than the
+12.44% they claim themselves**. Nobody systematically catches the all-time
high of every line across sixteen books.

Execution prices come from the **closing line**: `DISTINCT ON (bookmaker)` the
last quote at or before kickoff, then aggregate across books. On the same 1,136
Forebet picks:

| priced at | ROI |
|---|---|
| Forebet's own claimed odds | +8.97% |
| best closing price across books (line shopper) | **−0.18%** |
| Bet365 closing | −6.54% |
| median closing price (typical single account) | −7.40% |

Publish the **best** closing figure, not the median — it is the most
favourable realistic assumption for the competitor, so the claim is
conservative and survives challenge. Two more rules that make the number
defensible: filter `timestamp <= m.date` (a post-kickoff in-play quote is a
different bet), and always state coverage — only ~60% of Forebet's picks
fuzzy-match to our fixtures, and a recomputed figure whose sample is unstated
invites exactly the "your data is thin" dismissal this work exists to remove.

Related: #27 (the reachability test itself), #28 (competitor ROI mismatches).

## 30. "Best odds" from `odds_snapshots` means best EVER unless you say otherwise

`odds_snapshots` is an append-only history. A query filtered only on
`match_id` returns every quote ever polled for that fixture — and pruning does
not save you, because `scripts/prune_odds_snapshots.py` only touches
`status='finished'` matches. A scheduled fixture carries its full history
(measured: ~15 days, mean 38.8 rows per match/book/market/selection, max 736).

So `MAX(odds)` over that result set is a **high-water mark, not an offer**.
This was live in the betting pipeline until 2026-09-02
(`STALE-BEST-ODDS`): `_load_today_from_db`, `_run_no_pin_shadow_pass` and
`_run_sweep_shadow_pass` all aggregated the unbounded history, so
`simulated_bets.odds_at_pick` and `recommended_bookmaker` recorded whichever
book had once peaked. Dandenong City v Preston Lions stored **3.70 at Betano**
across five consecutive refreshes while Betano was showing 2.82 and the best
accessible price was 10Bet 3.10.

Two things make it worse than it first looks:

- **Refreshes re-derive the stale peak.** `run_morning(skip_fetch=True, …)` on
  the 30-minute betting refresh re-runs the same query, so the bad price is
  rewritten rather than corrected.
- **It defeats the outlier guard.** `ODDS-OUTLIER-FILTER` anchors on
  `next((o for b, o in offers if b == "Pinnacle"), None)` — the *first*
  Pinnacle row in an unordered scan. With history in the set that is a stale
  Pinnacle price. On Dandenong it anchored to 2.81 (ceiling 3.79) and passed
  the stale 3.70; the live 2.73 caps at 3.69 and would have rejected it.

**The pattern to copy** (already used correctly by `_run_pin_1x2_shadow_pass`,
`_run_pin_ou_shadow_pass` and the Coolbet pass):

```sql
SELECT DISTINCT ON (match_id, market, selection, bookmaker) ...
  FROM odds_snapshots
 WHERE ... AND is_closing = false
 ORDER BY match_id, market, selection, bookmaker, timestamp DESC
```

`is_closing = false` does **not** bound recency — it excludes only the
settlement-written closing row.

**Scale, measured at fix time:** 34.2% of 1X2 selections on scheduled fixtures
had a historical max above the latest-per-book max, mean +6.3%, worst 2.27x.
On 488 settled bets over 90 days, ROI computed from stored `odds_at_pick` was
**+9.47%** against **+4.43%** at the best price actually live at pick time —
i.e. **roughly half our recorded edge on that cohort was stale-odds
inflation**, in the same shape we criticise Forebet for (#27, #29). Historical
`pnl` is settled from `odds_at_pick`, so the existing record is overstated and
the fix is not retroactive — see `STALE-ODDS-HISTORY-RESTATE`.

Related: #25, #29, #16.

## 31. `teams.league_id` is NOT the team's league — use `matches.league_id`

It has never matched: **zero of 27,605** fixtures over 90 days have
`teams.league_id = matches.league_id` for the home side. Not corruption — the
column simply does not mean what its name says.

`supabase_client.ensure_team()` creates every team with
`ensure_league(f"{country} / Unknown", tier=0)`, so each team is assigned a
per-**country** placeholder. All 11,633 teams point at one of 160 rows named
`Unknown`; **none** point at a named league. Cagliari and Atalanta share an id,
and it resolves to a league called "Unknown".

Consequences if you join on it:

- **Tier is always 0.** Any tier-based split silently collapses into one
  bucket, which looks like "no effect" rather than like an error.
- **League name is always "Unknown"**, so a per-league breakdown returns one
  row.

This has already cost real work: the cross-tier hypothesis in
`SWEEP-HOME-BOTS-CALIBRATION` could not be tested as its ticket described,
because the ticket assumed this column meant what it says. The workaround used
there — each team's *modal* league tier over 365 days of actual fixtures — is
the right shape if you genuinely need a per-team league.

**Use `matches.league_id -> leagues`** for a fixture's league, name and tier.
That column is populated and accurate.

Not dropped or repointed on purpose: "the team's league" is not well defined
(domestic, cups, continental), which is probably why it was given a placeholder
to begin with. The column is documented at the DB level (migration 297) instead.

Related: #26 (feature coverage must be split by status), #18.

## Re-runnable analysis scripts (all committed 2026-08-26)

| script | answers |
|---|---|
| `clv_gate_report.py` | rank every bot by CLV; flags CLV/ROI disagreements |
| `clv_variant_backtest.py` | which CLV definition actually predicts ROI |
| `devig_calibration_backtest.py` | Shin vs proportional, on calibration |
| `promotion_gate_simulation.py` | Monte-Carlo of the graduation gate |
| `lineshop_replay.py` | point-in-time replay of any bot config |
| `clv_slice_search.py` | search market x tier x odds for a positive-CLV slice |
| `odds_band_by_market.py` | CLV by market x odds band x bot x tier, with placebo (gotcha 47) |
| `coverage_expansion_probe.py` | would the best model work in leagues it does not bet |
| `anchor_comparison_backtest.py` | Pinnacle vs consensus anchors |
| `bookmaker_sharpness_rank.py` | per-book calibration, paired |
| `book_bias_probe.py` | per-book directional bias by probability band |
| `favourite_band_probe.py` | does the favourite-longshot bias beat the vig |
| `discretion_bleed_report.py` | placed vs untouched picks, day-clustered |
| `ou_line_integrity_audit.py` | is a book's OU quote priced for its stated line |
| `model_version_clv_scoreboard.py` | paired model-version comparison |

## 32. `odds_at_pick_live` was 1x2-only until 2026-09-03 — check coverage per market

`odds_at_pick_live` is the honest execution price (`odds_at_pick` is a
high-water mark that overstates 1x2 ROI by +5.80pp). But the backfill that
populates it joined `odds_snapshots` on raw `market`/`selection`, and the two
tables do not share a vocabulary:

    bets       market 'o/u'           selection 'under 2.5'
    snapshots  market 'over_under_25' selection 'under'

So O/U matched nothing: **1,713 of 1,860** settled 1x2 bets priced against
**0 of 1,111** settled O/U bets, with no error and a success report. Any
analysis filtered on `odds_at_pick_live IS NOT NULL` before 2026-09-03 was
therefore silently ~96% 1x2, whatever it claimed to measure.

Fixed in OU-LIVE-PRICE-BLIND-2026-09-03 and pinned by the smoke test of the
same name. Two lasting rules:

- **Restating anything for a date before 2026-09-03? Re-run the backfill
  first.** Numbers computed then are 1x2 unless proven otherwise.
- **Report per-market coverage, not just overall coverage.** 54.7% overall
  looked like ordinary snapshot gaps; it was one market at 92% and another at
  0%. An aggregate coverage number cannot show you a market that is entirely
  missing.

This is #3 (market vocabularies) with a silent failure mode attached — the
join predates that entry and nobody re-checked it.

## 33. Never compare markets or lines measured on different samples

`predictions` does not cover every market for every match. In the bot era the
over/under lines have **14,784 / 8,500 / 6,113** settled matches at 2.5 / 3.5 /
1.5 — overlapping but very different populations, with different leagues and
different date mixes.

Comparing a per-line statistic across those samples measures the samples, not
the model. Measured 2026-09-03, model P(over) minus actual:

    line   own sample        common sample (n=3,532)
    1.5    z = -2.9          z = -1.0
    2.5    z = +4.6          z = +1.2
    3.5    z = +6.1          z = -0.5

Identical test, identical code. The apparent "the model understates goals at
high lines and overstates at low lines" — a clean, mechanistic,
very tellable story — is entirely composition. It was filed as a P1
(OU-OVER-UNDER-ASYMMETRY) and withdrawn the same day.

**Rule: before comparing any statistic across markets, lines, leagues or
periods, restrict to rows present in all of the groups being compared, and
report that common n.** If the effect only exists on the per-group samples, it
is not an effect.

The same failure in the betting ledgers has a second face: comparing one
market *spelling* (`over_under_25`) against a pooled figure silently compares
different bot mixes. Pool every spelling — see #3 — and run a within-group
control before believing a cross-group difference.

## 34. "Latest quote per book" is not "live quote" — check the lag, not the age

`DISTINCT ON (match_id, market, selection, bookmaker) ... ORDER BY timestamp
DESC` (gotcha #29/#30's fix) returns the newest row each book wrote. If that
book's feed died, the newest row is its *last* row and stays newest forever.

Coolbet's bulk scraper died at 08:00 UTC on 2026-09-03. By 18:40 the pipeline
had raised **101 picks recommending Coolbet** on quotes up to 11h old. Nothing
was wrong with the query — the feed was dead and the query cannot tell.

**Do not reach for an absolute age cap.** The odds job runs 07-22 UTC while the
morning cohort picks at 06:00, so morning picks legitimately use quotes at
p95 = 10.0h and p99 = 16.4h old. A cap tight enough to catch an 11.5h-dead feed
deletes the morning cohort entirely.

Measure **lag against the other books on the same fixture**. Overnight all books
age together and the lag stays near zero; a dead feed falls behind its peers.
Coolbet was 11.5h stale while the market was 2.94h. At a 6h lag threshold this
drops 100 of 102 Coolbet quotes and one 152h outlier, and touches nothing else.

Related trap, same incident: a book can look fresh on `MAX(timestamp)` while its
bulk feed is dead, because a second writer (the UI placer) keeps touching a
handful of fixtures. `coolbet_feed_watchdog._hours_since_last_odds` measures
bulk-sweep *breadth* for exactly this reason — a naive MAX said 8.6h stale where
the truth was 11.7h.

## 35. A "holdout" is only held out if you check the training window

`weekly_eval_and_compare.py` scored candidate and baseline on the last 14 days
of settled rows and called it held out. `train.py` trains through the run date
unless `--cutoff` is passed, and the weekly cron never passed it — so the
candidate had memorised the test set and won by construction. Measured
2026-09-03: v20260903 trained through 09-03 and was scored on 08-20..09-03.

Fixed in WEEKLY-EVAL-HOLDOUT-NOT-HELD-OUT. Three rules that came out of it:

- **Read `model_versions.training_window_end`; start the day after.** Never
  assume the caller passed a cutoff.
- **Compute the window per comparison pair, not globally.** One global window
  is safe but throws away data that is honest for the other markets — it cut
  6,941 rows to 949 here. An underpowered verdict is its own kind of wrong.
- **Enforce a minimum row count.** A candidate trained through yesterday leaves
  a technically honest window of one day; the first run scored 8 matches and
  printed swings of +32.5%. Refusing a rigged verdict and emitting a
  meaningless one is the same failure.

Any promotion decision taken on this script before 2026-09-04 was measured
through the contaminated path — including the 2026-08-31 decision not to switch
O/U, which also went through the inverted metric in #34's sibling
WEEKLY-EVAL-OU-INVERTED.

## 36. XGBoost gain importance overstates binary flags — permute before believing it

The `_missing` indicators looked alarming on `feature_importances_`: 13-18% of
total importance across heads, and three of the O/U head's top six. That reads
as "the model is predicting from which data we happen to have, not from
football" — a real fragility, since coverage changes every time we add a book or
backfill a column.

Permutation ablation says otherwise. On the O/U head (n=6,847):

    permute all 15 _missing indicators          +0.0020 log-loss
    permute 3 real features, equal importance   +0.0086 log-loss

Actual dependence is **0.23x** that of real features carrying the same nominal
weight. Gain-based importance rewards low-cardinality binary splits out of
proportion to their predictive contribution — a property of the metric.

**Rule: never conclude a model depends on a feature from `feature_importances_`
alone. Permute it and measure the loss.** The two disagree most exactly where
binary flags are involved, which is where the scary-looking stories live.

Corroborated independently: the 2026-09-03 densification flipped many of these
indicators and moved the promoted head's holdout log-loss by 0.0001.

## 37. `is_live = false` does NOT mean pre-kickoff — and it never meant what we thought

**Corrected 2026-09-05.** This entry previously said API-Football "keeps serving
odds after a fixture starts without flipping `is_live`". That causal story is
**wrong**, and it would send a fixer to the AF ingester where there is nothing
to fix.

Verified against the whole table:

- `is_live = true`: **695,534 rows, 100% `bookmaker = 'api-football-live'`** — a
  pseudo-book, not a flag on real books.
- `is_live = false`: 74.5M rows, every real bookmaker.

So nobody ever writes `is_live` on real-book rows; it defaults false.
**`AND is_live = false` is not a pre-match filter — it only excludes the
`api-football-live` pseudo-book.** Keep it only where that is the actual intent,
and say so in a comment.

The contamination it was blamed for is real: **26.15%** of `is_live = false`
rows in a 7-day window have `timestamp > matches.date`. It splits cleanly by
source:

| feed | post-KO share |
|---|---|
| every AF-fed book (Superbet, SBO, Unibet, Betano, Pinnacle, Bet365, …) | **30–40%** |
| Coolbet, Unibet-Kambi, Epicbet (direct scrapes) | **0.0%** |

Flat across markets (28.6–36.1%), and not near-kickoff rounding: median post-KO
row is **165 minutes** past kickoff, and 1.91M rows are >3h past.

**The safe predicate, verified:** `minutes_to_kickoff > 0` returned
**10,882,152 rows with ZERO post-kickoff** in a 7-day check. It is already
backed by `idx_odds_snapshots_timing (match_id, minutes_to_kickoff)`, so it
needs no join and no new index. 0.23% of rows are NULL — handle them explicitly.
Use `o.timestamp <= m.date` when you want the authoritative version that
reflects reschedules (`minutes_to_kickoff` is computed at write time and goes
stale if a fixture moves).

**Where it actually bites.** Post-kickoff AF rows are mostly *frozen* copies of
the close: 76.3% are byte-identical to the last pre-KO price, and the
loser-vs-winner drift separation is only 0.41pp — so **label leakage is
negligible**. The damage is concentrated in `MAX(odds)` best-price arithmetic:
mean fabricated edge is only **+0.19pp**, but **p99 is +4.10pp**, more than a
whole edge threshold. It does not move a backtest's headline; it **manufactures
individual qualifying bets out of nothing**, ~1% of the time, from a loser-heavy
pool (1,109 losers vs 328 winners among selections whose post-KO max beat the
pre-KO best by ≥10%).

**Do not add a `pre_match` generated column.** A generated column can only read
its own row, so it could only be `minutes_to_kickoff > 0` — which already exists.
One derived from `matches.date` is not expressible, and a trigger-maintained one
would go stale on reschedule, reproducing this exact bug.

Good news worth recording: **the training path is clean.** All four odds queries
in `workers/model/train.py` already carry `os.timestamp < m.date`.

**FIXED 2026-09-08 (AF-ISLIVE-UNRELIABLE) — the production read paths are now
guarded.** `is_live = false` alone is not a pre-kickoff filter; **always pair it
with a kickoff bound** (`JOIN matches m` + `AND <odds_alias>.timestamp <= m.date`).
A genuine pre-match row always has `timestamp <= kickoff`, so the bound never
drops a valid row, and on a live/future fixture it is a harmless no-op — so it is
safe to add everywhere. Every `is_live = false` read in
`workers/api_clients/supabase_client.py` (the batched Pinnacle/OU/BTTS anchor
loaders behind OU-PIN-REQUIRED, the per-match disagreement/volatility signals,
and the bulk 1x2/OU/AH/BTTS signal builders — 13 statements) and in
`workers/model/pin_cross_drift_veto.py` (`get_live_pinnacle_drift`) now carries
`o.timestamp <= m.date`. `train.py` always had it and was not touched. Pinned by
smoke test `AF-ISLIVE-PREMATCH-GUARD`, which asserts no `is_live = false` appears
in a statement lacking `m.date` in those two files.

## 38. A stalled feed is usually a starved feed — check the pool before the fetcher

**2026-09-05.** Unibet-Kambi and Epicbet both stopped writing odds. Neither
fetcher was broken and neither touches API-Football. The actual cause was three
levels away:

`BudgetTracker.status()` held `self._lock` and then called `self.usage_pct()`,
which takes the same lock. `_lock` was a plain `threading.Lock`, so this was a
permanent self-deadlock — and the lock was never released. One HTTP request to
the scheduler's `/health` endpoint (which calls `budget.status()`) was enough.

Every AF-touching job then blocked forever in `can_call()` / `remaining()`:
`fetch_odds`, `odds_refresh`, `settle_ready`, `fetch_fixtures`, `injuries_morning`,
`fetch_enrichment` — six-plus jobs sat in `pipeline_runs` as `running` for 85–165
minutes. APScheduler's executor caps at `max_workers=12`, so those wedged jobs
consumed the pool, and the non-AF feeds simply never got a worker.

**What this means for diagnosis:**

- **The feed that stopped is usually not the feed that broke.** Unibet-Kambi and
  Epicbet were victims. Confirm a fetcher is actually running before debugging it.
- **`pipeline_runs` rows stuck in `running` > 5 min are the real signal.** The
  scheduler already logs `SCHEDULER WARNING max_instances blocked` and points at
  this — believe it.
- **`py-spy dump --pid <scheduler>` identifies the holder in one shot, and the
  evidence is destroyed by a restart.** Capture it *before* restarting. Here the
  stack named the deadlock exactly: `usage_pct (api_football.py:143)` under
  `status (api_football.py:195)`.
- **A healthy-looking scheduler proves nothing.** `systemctl` reported `active`,
  `NRestarts=0`, uptime days. Postgres was fine (24/100 conns, 0 ungranted locks).
  A deadlocked thread pool is invisible to every liveness check we had.
- **Coolbet was healthy throughout**, which is what made this look like a
  Kambi-specific problem. It isn't scheduler-hosted the same way.

Fix: `_lock` is now an `RLock`, and `status()` computes `usage_pct` inline so it
never re-enters. Pinned by the behavioural smoke test
`AF-BUDGET-STATUS-DEADLOCK`, which fails on a 5s join timeout if the bug returns.

## 39. Never measure calibration across a window that straddles a calibration change

**2026-09-05.** I measured `calibrated_prob` against realised win rate over
2026-05-04..2026-09-05 and reported **+6.21pp overconfidence (n=729, z=-3.39)**,
then derived a shrink factor k=0.884 and recommended applying it.

The number reproduced exactly on an independent recompute, which is precisely
why it was convincing. It was still wrong as a statement about the current
model: **ENSEMBLE-RECALIBRATION shipped on 2026-09-03**, inside the window.

Split on the fix date:

| window | n | predicted | actual | overconfidence |
|---|---|---|---|---|
| before 2026-09-03 | 694 | 0.4916 | 0.4265 | **+6.51pp** |
| on/after 2026-09-03 | 35 | 0.4312 | 0.4286 | **+0.26pp** |

The pooled figure was a pre-fix measurement wearing a current-date label.
Applying k=0.884 on top of the recalibration that already shipped would have
**double-corrected** the model.

This is gotcha #35's shape in a different costume: there, a holdout was only a
holdout if you checked the training window; here, a calibration measurement is
only current if you check the calibration-change dates inside its window.

**Rules:**

- Before quoting any model-quality metric, list the model/calibration changes
  that landed inside the sample window. `git log --oneline --since=<window start>`
  on `workers/model/` takes seconds.
- Split on every such date and report the segments, not the pool. If the
  post-change segment is too small to conclude, **say that** — do not fall back
  to the pooled number, which is the pre-change answer.
- The same contamination applies to anything else derived from that window. The
  realised-edge-at-closing figure (+1.26pp) came from the same pooled sample and
  is equally a pre-fix number.
- A figure reproducing exactly on recompute proves the arithmetic, not the
  sample. Both of my computations shared the same window bug.

Corollary for task hygiene: this was found while auditing stale 🔄 locks. Two of
four "In Progress" tasks were not in progress — one was **finished** on
2026-09-03 and never marked (ENSEMBLE-RECALIBRATION), and one was **moot**
because the whole BTTS market had been retired underneath it. A stale lock does
not just block other agents; it also broadcasts a false picture of the system's
current state, which is what put "the model is worse than a base rate" into my
own priority list on the day it had already been fixed.

## 40. Fixing a metric on the summary page does not fix it on the detail page

**2026-09-05.** SHADOW-PAGE-ROI-INFLATED was filed and half-fixed on 2026-09-04:
the shadow-bots index page got an `execOdds()` helper so ROI is priced at
`odds_at_pick_live` (the quote actually available at pick time) instead of
`odds_at_pick` (a MAX() high-water mark across the fixture's whole snapshot
history). The page totals were correct because they aggregate `summarise()`.

The per-bot **detail** page was never touched. It had zero references to
`execOdds` or `odds_at_pick_live` and computed `wonPnl` directly from
`odds_at_pick`. So the operator could click a bot showing +12.07% and land on a
page showing +17.04% for the same picks — with no indication which was right.

Correction on non-retired bots, n>=40 settled:

| bot | old basis | exec basis | delta |
|---|---|---|---|
| `bot_v10_all` | 17.04% | 12.07% | **-4.97pp** |
| `bot_opt_home_lower` | -0.86% | -4.72% | -3.86pp |
| `bot_sweep_ou25_v1` | 4.16% | 3.67% | -0.49pp |
| `bot_coolbet_value_v1` | 4.98% | 5.68% | **+0.70pp** |
| `bot_pin_1x2_home_v1` | 9.41% | 9.41% | 0.00pp |

**Rules:**

- When a metric is wrong, grep for **every** surface that computes it before
  closing the ticket — index, detail, API route, export, Telegram. A one-line
  `grep -c execOdds` per file would have caught this immediately.
- The correction is **not uniformly downward**. `bot_coolbet_value_v1` improved
  by +0.70pp and `bot_pin_1x2_home_v1` did not move at all. Do not assume a
  stale-price fix always lowers the number, and do not "sanity check" a fix by
  expecting every bot to drop.
- **Always label the basis next to the figure.** Both pages showed a bare "ROI"
  with no statement of which price it used, which is precisely why two different
  numbers for the same bot could coexist for a day without anyone noticing.

## 41. A file-wide substring assertion is not a test of the thing you changed

**2026-09-05.** Writing the smoke test for LANDING-PERF-ROI-BASIS I asserted:

```python
assert "execOdds(r.odds_at_pick, r.odds_at_pick_live)" in api_src
```

Then mutation-tested it by reverting the public headline back to
`Number(r.odds_at_pick ?? 0)` — **and the test passed.** The per-bet ledger loop
in the same file also calls `execOdds`, so the substring was still present while
the headline, the thing the test exists to protect, was broken.

The fix was to scope the assertion to the block that actually computes the
headline, find the variable the P&L accumulation reads, and assert *that
variable's declaration* is execOdds-derived. The mutation then failed correctly:

```
AssertionError: the headline ROI is computed from `odds`, which is not
execOdds-derived: const odds = Number(r.odds_at_pick ?? 0);
```

**Rules:**

- A substring check proves a string exists somewhere in a file. It does not
  prove the code path you care about uses it. When the same helper is called
  from several places, a file-wide check is guaranteed to be weak.
- Scope assertions to the block, function, or statement under test — slice the
  source between known anchors and assert inside the slice.
- **Mutation-test the specific revert, not a convenient one.** My first mutation
  replaced every occurrence and would have "passed" the mutation check; only
  reverting the single headline line exposed the weakness.
- This is the exact defect class SMOKE-SUITE-AUDIT is cataloguing (170 tests
  asserting only that a string appears in a source file). It is easy to write
  one by accident while fixing something else.

## 42. Our `edge` is probability points, not EV — every formula built on it must know that

**2026-09-05.** `daily_pipeline_v2.py:3474` computes:

```python
edge = cal_prob - ip        # ip = 1 / odds
```

That is a difference in **probability points**. It is NOT the standard
multiplicative edge `odds * prob - 1`, which is what almost every betting
reference means by "edge". Three separate call sites had each independently
assumed the standard definition, and all three were wrong — in two different
directions:

| site | had | correct | error |
|---|---|---|---|
| `/picks` public floor | `odds / (1 + edge)` | `1 / cal_prob` | too HIGH on 482/482, median +18.1% |
| admin shadow-bots gate | `(1 + thr) / prob` | `1 / (cal_prob - thr)` | too PERMISSIVE, median 11.2% |
| `telegram.py` shadow alert | `(1 + edge) / prob` | `1 / cal_prob` | 2.44 vs 2.22 |

**Consequences to keep in mind:**

- **Displayed "edge +11%" is not an 11% return.** The model's own EV is
  `edge * odds`, so 0.112 at 2.29 is ~26%. Anyone reading the number as ROI is
  reading it wrong — see TELEGRAM-EDGE-LABEL.
- **Break-even is `1/cal_prob`**, equivalently `1/(edge + 1/odds)`.
- **A threshold floor is `1/(cal_prob - threshold)`**, from solving
  `cal_prob - 1/odds >= threshold`. It is a different, stricter quantity than
  break-even, and mixing them up is how the real-money panel ended up permissive.
- **The error direction is not predictable.** The same unit confusion made the
  public floor too conservative and the operator's floor too loose. Do not
  assume a units bug errs safely.

If the pipeline's edge definition ever changes, every one of these is wrong
again — the smoke test asserts `edge = cal_prob - ip` is still present for
exactly that reason.

## 43. If a number appears on N screens, assume there are N implementations

**2026-09-05.** The rule "price a settled bet at the odds that were actually
available" was fixed **four times in one day**, and each surface was discovered
only after the previous fix had shipped as "complete":

1. admin shadow-bots index (fixed 09-04)
2. admin shadow-bots detail page — clicking a bot showed a *different* ROI
3. `/api/v1/track-record` — the public landing headline
4. `getCalibratedHeadlineStats` — `/performance`

Between fix 3 and fix 4, the landing page and `/performance` published
**+13.10% and +17.39% for the same bets**, because half the app had been
corrected. Fixing a shared rule on one surface actively *creates* an
inconsistency until every surface is found.

The same day, the min-odds floor turned out to exist as three different
formulas, none correct, wrong in **opposite directions** — the public floor too
high by 18.1%, the real-money admin gate too permissive by 11.2%.

**Rules:**

- Before fixing a displayed number, grep for every place it is computed — by the
  *arithmetic*, not the helper name. `grep -rn "odds_at_pick" | grep -iE "pnl|roi"`
  found surfaces that a search for `execOdds` or `bot-aggregates` missed entirely.
- Then check the copies are *reached the same way*. Two of the four surfaces
  bypassed the shared mapper and queried the table directly.
- Consolidate to one definition and **add a test that fails on re-duplication**
  (see `EXEC-ODDS-SINGLE-SOURCE`). Match on the distinctive body of the rule, not
  the function name, so a re-export wrapper passes and a second implementation
  does not.
- Duplicated *constants* are as dangerous as duplicated logic. The two public
  cohorts were byte-identical literals in separate files — they agreed by luck,
  and nothing would have caught them drifting.
- Cross-language pairs (TypeScript UI, Python workers) cannot share an import.
  Assert numeric agreement on real rows instead.

## 44. A stale price contaminates CLV *proportionally*, so it fakes an odds slope

**2026-09-05.** A CLV-by-odds table read as "our edge lives above ~2.8 and is
zero below it" — monotone, t=+4.16 and +5.78 in the top two buckets. It was
about to be used to change real staking.

The suspicion was Shin de-vig residual bias at longshots. **That was wrong**, and
two tests said so:

- **Placebo**: selections we did NOT pick, on the same matches, get *worse* with
  odds under Shin (−6.92% → −9.84%). A methodological longshot bias would have
  lifted them too.
- **Method sensitivity**: proportional de-vig tilts longshots UP (−6.03% →
  −3.38%) while Shin does not — Shin is correcting in the right direction, as
  `workers/model/devig.py` claims.

The actual cause was the stale price. `clv_pinnacle_devig = odds × devig(close) − 1`
was computed from `odds_at_pick`, a MAX() high-water mark. Because the error is
**multiplicative**, its absolute size grows with odds:

| bucket | stored | at executable price | shift |
|---|---|---|---|
| 1.0–1.8 | +1.29% | −1.46% | −2.75pp |
| 1.8–2.2 | +0.91% | −3.22% | −4.13pp |
| 2.2–2.8 | +0.53% | −3.07% | −3.60pp |
| 2.8–3.5 | +4.70% (t=4.16) | −0.99% (t=−1.10) | −5.69pp |
| 3.5+ | +16.57% (t=5.78) | +9.95% (t=3.64) | −6.62pp |

**Rules:**

- **A multiplicative price error masquerades as an odds effect.** Any metric of
  the form `odds × p − 1` inherits the price error scaled by odds, so a constant
  relative price bias becomes a *slope* in odds. Before believing any
  "edge varies with odds/line/price" finding, recompute it at the executable
  price.
- **Check what actually populates the column.** `clv_pinnacle_devig` is written
  only by a one-off backfill script, never by settlement — so it silently
  reflects whatever price basis that script used on the day it ran.
- **Placebo and method-sensitivity tests are cheap and they earn their keep.**
  Here they *exonerated* the suspected cause, which stopped a wrong fix.
- **Pre-register.** The predictions and decision rule were written to
  `dev/archive/devig-artefact-check-plan.md` before measuring, so "de-vig was
  innocent" could not be quietly reinterpreted as a win.


## 45. Pinnacle sends 19 bet types through API-Football, not 8

**2026-09-05.** Section 4 of this file said Pinnacle quotes only 8 bet types via
API-Football. Enumerated live from the bulk `/odds` response we already fetch,
it sends **19**:

Match Winner · Asian Handicap · Goals O/U · Goals O/U First Half · First Half
Winner · **Total - Home (id 16)** · **Total - Away (id 17)** · Asian Handicap
First Half · Corners O/U · Home/Away Team Total Goals (1st Half) · Corners AH ·
Home Corners O/U · Away Corners O/U · Total Corners (1st Half) · Cards O/U ·
Cards AH · Exact Score · Correct Score First Half.

BTTS and double_chance really are absent — that part of section 4 stands, and it
is still why `clv_pinnacle` is permanently NULL for BTTS.

**Why it matters:** we parsed 6 of the 19 and discarded the rest, in a response
we already pay for. `Total - Home` and `Total - Away` de-vig to Pinnacle's
(λ_home, λ_away). Our Poisson emits that same pair but is anchored only on 1X2
and the match TOTAL, never on the SPLIT — so this is a sharp per-fixture
reference on an axis nothing else constrains, at zero additional quota.

Validated on live data: λ_home + λ_away matched Pinnacle's own match-total λ to
within **±0.08 goals on 6 of 6 fixtures**, while the split itself varied widely
(λ_home 1.49–2.49 vs λ_away 1.02–1.64).

**Rule:** before concluding a feed lacks something, enumerate what it actually
returns. Both the "8 bet types" claim and the belief that we needed more data
sources survived unchallenged because nobody printed the response. The same
audit found `AF_ENDPOINT_FREQUENCY.md` and the `DATA_SOURCES.md` budget table
both materially out of date, so treat vendor-capability notes in this repo as
stale until re-probed.

**And keep team-specific lines in their own namespace.** The `Goals Over/Under`
parser carries a comment about a previous incident where a team-specific line
leaked into the full-time bucket and best-price selection compared it against a
full-match model probability, fabricating double-digit edges. Team totals are
stored as `team_total_{home,away}_{line}` for exactly that reason.

## 46. `git add -A` while a background agent is editing the tree misattributes commits

**2026-09-05.** Two background agents were fixing `docs/AF_ENDPOINT_FREQUENCY.md`
+ `DATA_SOURCES.md` and `workers/automation/coolbet_placer.py`, each told not to
commit. Meanwhile the main session ran `git add -A && git commit` for its own
unrelated work — twice.

Result: nothing was lost, but the history is wrong.

| Commit | Message says | Actually contained |
|---|---|---|
| `edd704f` | Coolbet sidebets limit + CLV rebuild | ...plus **both agents' in-flight edits** to `coolbet_placer.py`, `AF_ENDPOINT_FREQUENCY.md` and half of `DATA_SOURCES.md` |
| `8961ebc` | `fuzzy_match_event` KeyError fix | ...plus the **rest** of `DATA_SOURCES.md` |

So two commits describe changes they do not contain and contain changes they do
not describe, and one agent's doc rewrite is split across two unrelated commits.
In a repo where the commit message is the audit trail — and where CLAUDE.md
requires docs and code to land together — that is a real cost, even though every
line of work survived.

**Rules when running background agents that edit the working tree:**

- **Never `git add -A` while an agent is live.** Stage explicit paths:
  `git add workers/api_clients/api_football.py scripts/smoke_test.py`.
- Telling an agent "do not commit" does **not** protect you — it leaves the work
  in the tree precisely where a blanket `add -A` will scoop it up.
- Check `git status --short` before committing and confirm every listed path is
  yours. An unexpected file is the signal.
- Prefer giving agents `isolation: "worktree"` when they will edit files, so
  their changes cannot appear in your tree at all.
- If it happens: do not rewrite already-pushed history to tidy it. Record what
  actually landed where, as this entry does, and move on.

## 47. An odds-band effect is a BOT effect until you split by bot

**2026-09-06, ODDS-BAND-BY-MARKET-AUDIT.** The question was whether each market
has its own profitable odds range. The answer is that the conditioning variable
is wrong: **the same market and the same band point in opposite directions for
different bots**, decisively, on samples large enough to settle it.

1X2 at 3.5-5.0, de-vigged Pinnacle CLV at executable prices:

| bot | n | CLV | t (clustered on match) |
|---|---|---|---|
| `bot_v10_all` | 89 | **+6.42%** | +4.69 |
| `bot_high_alignment` | 34 | **+5.51%** | +3.27 |
| `bot_aggressive` | 379 | **-6.50%** | -9.53 |
| `bot_no_pin_shadow_v1` | 38 | -9.71% | -4.52 |
| pooled over model-driven bots | 630 | -3.44% | -5.14 |

The pooled row is the average of bots that disagree, so it describes none of
them. A price floor cannot express "this bot's long shots are good and that
bot's are not", which is what the data actually says — so a market-aware odds
floor is the wrong instrument for this finding.

**Three traps this audit walked into, all worth avoiding by default:**

- **A line-shop bot's CLV is its own entry rule.** `bot_pin_1x2_home_v1`,
  `bot_sweep_ou25_v1`, `bot_sweep_ou35_v1`, `bot_coolbet_value_v1` and
  `bot_pin_1x2_draw_tier4_v1` fire when a soft price beats the *de-vigged
  Pinnacle* probability by `_LINESHOP_TRUE_EDGE_MIN`. Measuring their de-vigged
  Pinnacle CLV re-measures the admission test. They read +2.5% to +8.6% in
  **every** band of **every** market, and any table pooling them with
  model-driven bots inherits that as a fake band effect. Over/under 3.5-5.0
  reads +7.08% fleet-wide and the cell is 85% line-shop rows; among
  model-driven bots over/under has no positive band at any price.
- **Centre within market before permuting.** A first placebo shuffled raw CLV
  and produced a null whose median max-|t| was **71.8** — because
  `double_chance` sits at -5.8% across every band, so any large subset of it
  inherits a huge |t| that has nothing to do with price. Centring within market
  drops the null's median max-|t| to 1.66 and makes the test mean something.
- **A one-cell grid is not a placebo.** `bot_dc_strong_fav` returned "p=0.0040"
  on a -0.03pp deviation, because only one of its cells reached min-n so the
  shuffled max-|t| collapsed to ~0. Require at least two surviving cells.

**Two data defects the audit surfaced:**

- **`simulated_bets.clv_pinnacle_live` is not the same quantity as
  `shadow_bets.clv_pinnacle_live`.** Nothing in the codebase writes the
  simulated one; `settlement.py` writes only the shadow column, from the
  **de-vigged** Pinnacle probability. The simulated column was populated once
  by rescaling the **raw** `clv_pinnacle` to the live price, so it still carries
  Pinnacle's overround — it reads **+8.40pp higher** than an honest recompute on
  the same 1,642 rows, with individual values as absurd as +129% (implying a
  Pinnacle "over 2.5" close of 1.19). `weekly_bot_review.py` pools the two via
  `COALESCE(clv_pinnacle_live, clv_live, clv_pinnacle, clv)` and is therefore
  averaging a de-vigged number with a vigged one.
- **`odds_at_pick_live` has never been backfilled for `asian_handicap`** — 0 of
  2,013 settled AH rows carry it, so AH cannot be judged on the basis every
  other market is judged on. The *anchor* is not the problem: pairing Pinnacle's
  home/away quotes at the same `handicap_line` is a clean 2-way de-vig and
  yields a true probability for 963 of those rows. Only the executable price is
  missing.

Related: #44 (a multiplicative price error fakes an odds slope — the reason this
had to be recomputed at executable prices before it could be read at all), #8
(gate on CLV), #33 (never compare across samples).

---

## 48. `edge_percent` is a rounded DECIMAL FRACTION — never reconstruct `cal_prob` from it

Two separate traps in one column name, both live.

**It is a fraction, not a percentage.** Despite being called `edge_percent`,
`simulated_bets.edge_percent` stores `0.11` for an 11% edge — measured range
0.02–1.36, median 0.11. Anything that multiplies or divides it by 100 is wrong.
This bit us on 2026-09-06: `engine-data.ts` divided it by 100 on its way into
`detectBetFlags`, so the `high-edge-uncalibrated` (> 0.20) and
`edge-implausibly-high` (> 0.50) guards **could never fire** — on the admin page
read before staking real money. The column name is the trap: it survives review
because the reader trusts the name over the data.

**It is also ROUNDED, so reconstructing the calibrated probability from it is
unsafe.** The engine computes `edge = cal_prob − 1/odds` in probability POINTS
(`daily_pipeline_v2.py:3474`), so it is algebraically true that
`cal_prob = edge + 1/odds`. But on 90 days of pre-match rows that reconstruction
has a **p99 absolute error of 1.98** — not imprecise, nonsense. Every
`breakEvenOdds`-style helper carries that reconstruction as a fallback.

**WHY it was rounded, found 2026-09-22 (EDGE-IS-DERIVED-NOT-STORED, queue #031):
the COLUMN was `numeric(5,2)`.** Two decimal places on a probability difference is
a granularity of one whole percentage POINT, and Postgres applied it silently on
every write — the writer was always correct, the column destroyed it. Measured over
all 3,672 non-combo picks carrying a calibrated probability, **3,661 (99.7%) sat
within 0.005 of `cal_prob − 1/odds`, i.e. exactly the residue of `round(x, 2)`**;
the only rows outside that were 2 retired in-play bots that stored a model-prob edge.

That was not merely an analysis nuisance. Every per-market edge floor is itself
specified to two decimals (1x2 pooled 0.13, 1x2 home-underdog 0.10, o/u 0.08,
AH/DNB 0.05), so `stored >= floor` held across the whole band `[floor − 0.005,
floor)`: **114 picks all time — 26 in 90d, 14 in 30d — cleared a floor their real
edge missed, and 0 were wrongly rejected.** Rounding half-up can only admit, never
reject, so the error ran entirely in the flattering direction. The readers acting
on it were the Telegram signaler (operator prompt AND public channel) and the
pre-kickoff catch-net; the placer itself re-derives at the live price before
staking, so it never staked one.

**Fixed 2026-09-22 in three places, and HISTORICAL ROWS WERE NOT BACKFILLED.**
Migration 367 widened the column to `numeric(6,4)` (what `shadow_bets` has had
since migration 101); `store_bet` now derives the value it stores from the same
`calibrated_prob` and `odds_at_pick` it writes in that row; every gate re-derives
on read via `coolbet_placer.model_edge`, and `picks_public_all`'s model arm derives
the edge it publishes. A backfill was deliberately NOT run: rewriting
`edge_percent` would rewrite what each bot is recorded as having cleared, which is
the evidence base for every floor we have set. **So for any row written before
2026-09-22, `edge_percent` is still rounded to two decimals — derive
`calibrated_prob − 1/odds_at_pick` in the query rather than reading the column.**

**Rule: always read `calibrated_prob` directly. Treat the `edge + 1/odds`
fallback as a last resort that must never be relied on**, and prefer returning
null over returning a reconstructed number. The fallback does not fire today
only because `calibrated_prob` is never NULL — which is one schema change away
from being false.

Related: gotcha 42 (our `edge` is probability points, not multiplicative EV) and
the MIN-ODDS-WRONG-FORMULA family, where the same confusion produced a
break-even price that was wrong on 478 of 478 picks.

---

## 49. A goal is not only `event_type = 'goal'`

`match_events` splits API-Football's single "Goal" bet type into four of our
own event types, and `detail` is always `"Normal Goal"`, so it cannot be used to
recover the distinction afterwards. Filtering on `'goal'` alone silently
undercounts.

Measured on **134,731 finished matches** with both events and a stored score,
comparing the event count against `score_home + score_away`:

| counting | matches agreeing | |
|---|---|---|
| `'goal'` only | 100,580 | **74.7%** |
| `'goal'`, `'penalty_scored'`, `'own_goal'` | 129,825 | **96.4%** |

Use `GOAL_EVENT_TYPES` / `GOAL_EVENT_TYPES_SQL` from
`workers/api_clients/api_football.py` rather than retyping the set.
`penalty_missed` is deliberately excluded — it is a shot, not a goal.

**Own goals count for the OTHER side.** `match_events.team` records who the
event happened *to*, so `event_type='own_goal'` with `team='home'` is a goal for
**away**. A match total does not care; any per-team split must flip it.

No production consumer is affected today — the only `match_events` readers under
`workers/` are `inplay_bot`'s red-card lookups. This entry exists because
NEW-MARKETS-LINESHOP hit it once already while settling first-half totals, and
anything deriving goals from events (1H totals, team totals, timing analysis,
in-play reconstruction) will hit it again.

Compounding factor worth knowing: `match_events` took **no writes at all between
2026-08-21 and 2026-09-06** (MATCH-EVENTS-SILENT-WRITE-FAILURE, since
backfilled), so any events-derived number computed in that window is suspect for
a second, unrelated reason.

## Goals are three event types, not one (verified 2026-09-07)

`match_events.event_type` splits goals into `goal` (~336k), `penalty_scored`
(~33k) and `own_goal` (~8k). Counting only `event_type = 'goal'` reconstructs the
stored full-time score for just **76.1%** of matches; counting all three reaches
**89.5%**. Any event-based score reconstruction (first-half scores, 1H settlement)
MUST use all three — the ready constant is `api_football.GOAL_EVENT_TYPES_SQL =
"('goal','penalty_scored','own_goal')"`. Status as of 2026-09-07: no production
reader has the bare-`'goal'` bug — `lineshop_new_markets.py` already uses all
three, and production settlement reads AF's halftime STATS (not events), so it is
unaffected. The residual ~10-37% gap is AF EVENT COVERAGE (many matches carry no
events at all), not a filter bug — that is the real first-half blocker, not the
goal-type count.

Also: cards live in TWO parallel columns — `yellow_cards_*`/`red_cards_*` (AF
path, ~97% populated) and `yellows_*`/`reds_*` (football-data CSV ingest, ~24%).
Query the `*_cards_*` names for AF-era coverage; the short names silently return
near-zero. And card settlement is an UNRESOLVED convention question (2026-09-07): the
established work (CARDS-SECOND-YELLOW) uses `points` (yellow=1, red=2, with the
second-yellow double-count fixed) because that is how bookmakers settle cards
O/U; but a NEW-MARKET auditor measured `events` (raw card count) agreeing better
with de-vigged Pinnacle P(over) (-0.6pp vs -3.1pp for points). These measure
different things and the answer is BOOK-SPECIFIC (do Coolbet/Betano/Unibet/Epicbet
settle cards O/U on points or on count?). Do NOT flip the default to `events`
without confirming the placeable books' actual settlement rule — see
CARDS-SETTLEMENT-EVENTS-DEF-GUARD.

## 50. Settlement is a resolver REGISTRY — an unknown market SKIPs, it is not "lost"

`settle_bet_result()` (workers/jobs/settlement.py) grades every bet — simulated,
shadow and real — through a **market→resolver registry** (SETTLEMENT-RESOLVER-REGISTRY,
2026-09-07). Two things to know before touching it:

1. **An unrecognised market returns `result='skip'` (pnl=None), NOT a loss.** The
   old if/elif chain initialised `won=False`, so any market it had no branch for
   was silently graded **lost** on the goal score — corners `over` AND `under`
   both came back `lost`. That is the exact trap the registry removes: unknown or
   ungradeable → skip + a deduplicated Telegram, and the row is left pending. So
   **if you add a market family, add a resolver** or it will pile up as pending
   (by design) rather than be mis-settled (the old failure).

2. **A resolver may need a non-goal statistic, passed via `stats=`.** Corners are
   settled from `stats={'corners_home','corners_away'}` (the corner COUNT, not the
   goal score). Callers that do not pass `stats` get `skip` for those markets —
   which is why the generic goals-based shadow/sim settlers leave `corners_ou_*`
   pending for `corners_paper_bot` to settle. The generic `_PENDING_SHADOW_BETS_SQL`
   also excludes `corners_ou_%` outright so those rows never even reach the
   registry there (no spurious "unsettleable" alerts for a market we settle on
   purpose).

Behaviour on all existing markets (1x2, o/u, over_under_*, asian_handicap, btts,
double_chance, draw_no_bet) is pinned byte-for-byte by the **SETTLEMENT-GOLDEN**
smoke fixture (`scripts/fixtures/settlement_golden.json`, 1,935 grid rows of the
pre-refactor verdicts). If you change a resolver and that test fails, you changed
a real settlement outcome — regenerate the fixture only if the change is intended.

**Cards is deliberately NOT registered** — the card count undercounts the books'
line by ~0.8/match (CARDS-SETTLEMENT), so a cards bet skips rather than
manufacturing edge on every under. The registry is where "which markets can we
grade" is documented in code.

## 51. `match_stats` cards: the SHORT-name columns are mostly NULL

`match_stats` has TWO sets of card columns and they are not interchangeable:

- **Use these (populated):** `yellow_cards_home`/`yellow_cards_away` (~97% non-null),
  `red_cards_home`/`red_cards_away` (~47%). These are the real per-match counts.
- **NOT these (mostly NULL):** `yellows_home`/`yellows_away`/`reds_home`/`reds_away`
  are ~78% NULL. A query on the short names silently returns near-zero and looks
  like "hardly any cards", not like an error.

And for SETTLEMENT specifically, the correct card total is neither of the above:
it is the **EVENTS** definition — the count of `yellow_card` + `red_card` rows in
`match_events` — pinned as `settlement.CARDS_SETTLEMENT_DEF` /
`cards_total_from_events()`. Measured 2026-09-08 (n=999 Pinnacle cards_ou
fixtures) the events def settles closest to the sharp line (mean 4.08 vs line
4.04); `points`/`yellow_red`/`yellow`-only undercount by −4.8 to −9.8pp and
manufacture phantom under-edge. See CARDS-SETTLEMENT-EVENTS-DEF-GUARD. Cards are
still deliberately unsettleable in the registry (thin anchor) — this is a
correctness guard, not a green light to bet cards.

## §52 — Line-shop "edge" is a measurement mirage; evaluate OOS + executable (2026-09-08)

The sweep/pin line-shop bots (`bot_sweep_ou25/35_v1`, `bot_pin_1x2_home_v1`,
`bot_coolbet_value_v1`) showed strong in-sample ROI (~+7% "on the page") and for
a while made it look like the ensemble model was pointless — you could just
line-shop the best soft price vs de-vigged Pinnacle. **It was a measurement
artifact, exposed only by honest evaluation.**

Two compounding biases inflated line-shop:
1. **Best-of-books selection.** Taking the MAX across soft books structurally
   selects whichever book is most *mispriced* (worst-calibrated) — you're
   betting into the book that's most wrong, and calling its error your edge.
   `daily_pipeline_v2` ~L4797: 57 of 58 live picks were −EV at Coolbet despite
   showing +7%. The edge measured is not the edge received.
2. **In-sample selection.** No held-out window, so any favourable cell (or the
   whole bot) can look good by construction.

Evaluated the honest way — **held-out out-of-sample split** (select on TRAIN,
validate on untouched TEST) at **executable prices** (`odds_at_pick_live`,
deduped) — via `scripts/bot_2d_audit.py`:
- line-shop dies: `pin_1x2_home` +14% train → **−32% test**; `sweep_ou35` +24%
  in-sample → **−1% test**; line-shop 1x2 at its live 3% gate → **−24% test**.
- **model-edge holds: `bot_v10_all` +28% (1x2) / +34% (O/U) out-of-sample.**

Lesson (same family as the 15%-floor overfit and the STALE-BEST-ODDS +4.29pp
inflation): **how you measure decides what you believe.** Never evaluate a
strategy on in-sample best-of-books ROI. Always: held-out OOS + executable
price + dedup. The model is the moat; line-shop was the mirage.

## §53 — `odds_snapshots.handicap_line` is stored HOME-perspective for BOTH selections (2026-09-08)

The AH edge-sweep first reported **+142% TEST ROI** — impossible. Root cause: the
AH line in `handicap_line` is the **home team's** line, and it is written that way
for the away selection too. `daily_pipeline_v2._ah_model_prob` computes the away
side as `1 − home_prob` of the *same home line*, and prediction rows are written
as `ah_{sel}_{home_line}`. Grading the away bet as if the stored line were the
away team's own handicap flips the sign and grades the away side against a
different line than it was priced on — manufacturing huge phantom edges.

**Correct grading:** `spread = −handicap_line`; with `margin = score_home −
score_away`, the **home** side wins iff `margin > spread`, the **away** side wins
iff `margin < spread`, push iff equal. Both selections use the SAME (home-perspective)
`handicap_line`. The join to predictions was correct all along — only the outcome
grading was wrong. Any future AH work must use the home-perspective convention.

(Result, once fixed: AH collapses to uniformly negative — see MARKET_DATA_MAP.md
edge-sweep verdict. This gotcha is why "ah_away_dog +2%" in bot_2d_audit's first
pass was a bug, not an edge.)

## §54 — Half-goals & match-stat signals: what's real, what's usable (2026-09-08)

From 1H-HT-GOALS (`matches.ht_score_*`/`h2_score_*` now stored) + the match_stats
coverage audit. Documented per owner request ("if you find signals that could
improve any market model, document it").

**The 2nd-half goal skew is real and robust — but market-known.** Over 3,837
matches: 2nd half averages **1.61 goals vs 1.30 in the 1st (+24%)**, **55.3% of
all goals fall after HT**, and 2H>1H in **44%** of matches vs 30% the other way,
**consistent across every tier** (2H>1H 41–47%). Useful as a *prior* in any
first-half/second-half model. NOT an edge by itself: flat-backing 1H 1x2 just
pays the vig (−9 to −13%), i.e. the books already price the skew.

**The 1H 1x2 market is SHARP.** First-pass discrimination: market AUC **0.63
(home) / 0.64 (away)** — 1H results are quite predictable and the market prices
them well. A naive first-half Poisson (rates fit on ~6.5k matches while the HT
backfill was still filling) scored only ~0.50 and added no OOS info. Verdict
PENDING a re-run once the full HT backfill (~128k) allows a non-starved rate fit
— but the bar is high (beat 0.63 + clear vig). See 1H-MODEL-EDGE-TEST.

**Match-stat features are ~31% covered and mostly POST-match.** corners, cards,
shots, possession, fouls, offsides, saves, passes ≈ 29–32% of finished matches
(rising toward ~40% as the cov=TRUE backfill runs); **xG and red cards ~15%
overall (~47% of stat-rows) — xG is top-leagues-only.** Two traps: (1) within
rows that exist most fields are ~90%+ populated — the gap is match_stats
*existing*, not fields empty; (2) **shots/possession/xG are match OUTCOMES, not
pre-match inputs** — they can only feed a pre-match model as aggregated team
rates/form, never as per-match features. Any model using them needs a fallback
for the ~60–70% of matches without a stats row.

## §55 — Model-edge ROI must use a SINGLE-BOOK executable price, never best-of-books (2026-09-08)

Validating a model-edge strategy by ROI at **best-odds-across-books + an edge
gate** silently re-creates the line-shop selection artifact (§52): the gate
selects whichever book is the outlier-high price, which is exactly the
stale/mispriced quote that loses OOS. **Proof by control:** the O/U 2.5 market we
bet profitably comes out NEGATIVE (−1.6%) on best-of-accessible calibrated
model-edge — a test that condemns a market we make money on is not measuring the
right thing. Switching to the **single-book Coolbet executable price** (the
actual placement venue), calibrated model-edge at edge≥8% shows 2.5 **+5.6%**,
and O/U 3.5 **+7.8%** (mirrors 2.5), O/U 1.5 dead.

**Two consequences.**
1. Any "market X is dead by ROI" verdict computed on best-of-books is unreliable
   and biased ~4–7pp LOW. This includes MARKET-EDGE-SWEEP's BTTS/DC/AH numbers
   (they used "idealized best-of-accessible"). After the correction AH/DC stay
   negative (dead holds), but **BTTS shifts to ~marginal** — its real problem is
   NO Pinnacle anchor + model AUC ≈ coin (discrimination), not a clean ROI-dead.
   Trust the *discrimination* facts (AUC model-vs-market), not best-of-books ROI.
2. **Nothing is fold-robust on Coolbet-executable yet — including the live 2.5 —
   because Coolbet history is only ~6 months** (n≈3.4k for 2.5). Confidence in
   ALL Coolbet-executable edges is n-limited; new lines (3.5) should accrue as
   paper/shadow bots and gate on fold-robustness before real money, exactly like
   the existing ones. Tool: `scripts/ou_lines_edge_test.py`.

## §56 — `coverage_statistics_fixtures=TRUE` is league-level and optimistic; corners is an AF ceiling (2026-09-08)

MARKET-DATA-AF-AUDIT predicted corners was "mostly a fixable collection gap" —
backfill the ~13k cov=TRUE fixtures missing corners and coverage jumps 31%→~40%.
**Empirically false.** Fetching all **19,677** cov=TRUE finished fixtures missing
corners (`scripts/backfill_match_stats_af.py`, `/fixtures?ids=` with inline
statistics) returned corner stats for only **748 — 96% came back empty.** The
`coverage_statistics_fixtures` flag is a LEAGUE-level promise, not a per-fixture
guarantee: within a cov=TRUE league, lower divisions / older / lesser fixtures
frequently have no statistics object at all. **The AF v3.9.3 docs confirm this explicitly** (/leagues coverage section): "values set to True do not guarantee 100% data availability", coverage "can vary from season to season", and for some competitions "the data available may differ depending on the match, including ... statistics". The flag is a league-level indicator, not a per-fixture guarantee — treating it as one was the audit's error. So corners AND cards are an **AF
ceiling (~32%), not a collection gap** — they can only grow via a non-AF source
(football-data CSV `HC`/`HY`/`HR`). Lesson: verify a coverage-flag claim by
actually fetching a sample before scoping a "cheap backfill" on it.

## §57 — Draws are a SHARP edge, not a model edge; our model structurally can't bet them (2026-09-09)

FAVLONG-SPLIT-FLOOR-BACKTEST decomposed 1x2 by selection and found the model bets
**zero draws**: across all bots 96 draw picks exist but **0 clear the 12% edge floor**
(home 599, away 227). The calibrated bot generates **no draw rows at all**.

**Why:** model edge = `cal_prob − 1/odds`. Draw odds are ~3.0–3.5 (implied ~29–33%),
but the model's calibrated draw prob is lower (draws are hard; calibration shrinks the
middle outcome), so `cal_prob − 1/odds` is rarely positive and never ≥12%. Our model
**systematically under-rates draws** — it cannot produce a draw pick that clears the gate.

**But the IDEALIZED draw edge is real** (robust +12→+21% in the 8–12% band). That basis
prices best-accessible odds against **de-vigged Pinnacle**, i.e. it measures the SHARP
edge (soft books mispricing draws vs Pinnacle), NOT the model edge. So:

**The draw edge exists but belongs to the SHARP-ANCHORED trigger bots** (which fair-value
against de-vigged Pinnacle — `pick_triggers` `sharp_*` strategies), NOT the model-edge
bots. When you work on the trigger bots, this is where draw profit lives. Do NOT try to
"fix" the model to bet draws — it's the wrong instrument for this edge. Model 1x2 edge =
home-underdogs; draw edge = sharp triggers. See BETTING_GATE_DECISIONS.md (1x2 by type)
and docs/BOOK_AGNOSTIC_EDGE_ENGINE.md (sharp anchor).

## §58 — ONE canonical market/selection vocabulary; validate at the EXECUTABLE per-book price (2026-09-10)

Two linked facts, both now enforced/tooled.

**(a) There is ONE vocabulary — `workers/canonical_market.py`.** The same bet was
written four ways (`1X2`/`home`, `1x2`/`home`, `O/U`/`over 2.5`, `over_under_25`/`over`),
which broke joins repeatedly (it is one of the five 2026-09-06 Coolbet ingest defects).
`canonical_market` is now the single source: `Market`/`Selection` str-enums + `normalize(market, selection)`
which collapses every observed spelling to one canonical `{family, market, selection, line}`
(handles the parametric `*_ou_<line>` families too — corners/cards/half). **Route every
reader/writer through `normalize`; do not hardcode the strings.** The smoke test
**MARKET-VOCAB-ENFORCED** fails the build if (i) any (market,selection) our active bots have
picked — or any Coolbet/Unibet-Site odds pair — can't be normalized (rogue/new vocab), or
(ii) the enums get re-declared outside `canonical_market.py`. **Phase 2 (gated, not done):
migrate the WRITERS to emit only canonical strings + backfill historical rows, then flip the
test to strict "DB contains only canonical values." That rewrites stored data and touches
grading + /performance, so it is a staged migration, not a one-shot.**

**(b) Validate bots at the EXECUTABLE per-book price (operationalizes §55).**
`scripts/executable_shadow_eval.py` attaches the Coolbet + Unibet-Site price (nearest
snapshot to pick_time) to every active bot's settled picks and reports per-book ROI vs the
recorded best-of-books ROI. The odds already exist in `odds_snapshots` (the sweeps fill them
per match, for any bot's fixture) — they were simply never attached to the general bots'
pick rows, which store one best-of-books `recommended_bookmaker` instead. Measured 2026-09-10:
**`bot_v10_all` 15.2% recorded (best-of-books) vs 7.8% executable-Coolbet** (n=1952, 69%
Coolbet-covered) — the reference price nearly doubles the honest number. Coverage bound:
Coolbet quotes ~87 and Unibet-Site ~132 of ~262 upcoming fixtures, so validation is
"executable performance on the covered subset" — which is the number that matters for real
money anyway. Use CLV, not ROI, at small n (per-bet return sd ≈ 1.42).

## §61 — `over_under_*` carried no numeric line at 13 of 16 books, and §25's key silently deleted every cross-book O/U comparison (2026-09-11)

**CORRECTED 2026-09-11, same day.** This entry first said "the books disagree on
a convention". They do not. It is **one fix applied to some code branches and not
others**, and the corrected version is the useful one — a convention split is
nobody's bug, an incomplete fix has an owner.

**The symptom.** §25 says a cross-book comparison must match on `handicap_line`.
Do that for O/U and you get **zero** rows across two camps, with no error:

| `handicap_line` on `over_under_*` | books |
|---|---|
| NULL | Coolbet, Pinnacle, and all 13 API-Football books |
| the line (`2.5`) | Epicbet, Unibet-Site, Unibet-Kambi |

`scripts/book_dimension_sweep.py` silently collapsed to 1x2-only — 525 series
instead of 859 — and looked like it had simply found nothing else to compare.

**The cause.** MARKET-LINE-ENCODING-LOSSY (2026-09-06) established that every
totals writer must store the line numerically, because
`str(float(line)).replace('.','')` is not reversible (`1.25` and `12.5` both give
`"125"`) — an ambiguity that had already settled **634 fabricated losing bets**.
That fix was applied to the SIDE totals branches and to the direct scrapers'
generic OU path. It **missed the main goals ladder** in `api_football.py`
(`elif bet_name == "Goals Over/Under"`) and `coolbet_explorer.py` (the `is_ou`
branch, which had `line_val` in hand and just did not pass it to `_add`). So:
`corners_ou_*`, `cards_ou_*` and `team_total_*` are 100 pct populated at every
book, `over_under_*` was 0 pct at the AF books — and only our own three scrapers,
whose OU path was generic, looked "different".

The existing smoke test `MARKET-LINE-ENCODING-LOSSY` drove each parser through a
**corners** market, so it passed throughout. §41, again: the test did not test the
thing that broke.

**Fixed** (writers, 2026-09-11): both branches now emit the number, so every new
row is unambiguous. `scripts/backfill_ou_handicap_line.py` backfills the 7.79M
historical rows whose name determines the line. New test: `OU-LINE-GOALS-LADDER`.

**251,969 rows are deliberately left NULL** — every 3-digit token without a
leading zero (`over_under_275`, `_225`, `_175`, `_125`, …) is genuinely
ambiguous, because a line below 1.0 keeps its leading zero (`str(0.75)` → `"075"`)
but `1.25` and `12.5` do not differ at all. 27.5 goals is absurd and 2.75 is
obviously intended — and "obviously intended" is exactly the reasoning that
produced the 634 bets, since that backtest also picked the reading that looked
obvious. **Honestly absent beats confidently wrong.**

**Two portable lessons.**
1. When books split cleanly into "ours" and "theirs" on some field, suspect a
   partially-applied fix before a convention difference. The split follows the
   CODE PATH, not the vendor.
2. The backfill belongs in a **script**, not a migration. Written as migration
   332 first: `migrate.yml` sets `statement_timeout=600000` inside a 15-minute
   job, and the single UPDATE was still running at 6 minutes on the first of two
   statements — it would have timed out, failed, and left the table half-done
   with the file unmarked. `odds_snapshots` is also hot (four writers), so one
   8M-row transaction stalls live ingestion. Batched, committed per batch,
   resumable, run out-of-band.

## §62 — An odds-band effect is a MARKET effect until you split by market (2026-09-11)

§47's sibling, found the same day. The 3.00-5.00 odds band looked like a decisive
**Coolbet** win (+1.46 pct per series, t=+3.4, n=245) on the three-book universe —
which is 1x2-only for the reason in §61 — and a decisive **Epicbet** win
(+0.37 pct, t=+3.4, n=3,267) on the two-book all-market universe. Both are
correctly computed. They are different markets wearing the same band label.

Neither survived as a *finding*: measured on the placer's actual gate (1x2 home,
odds >= 2.80) over 14 days, n=400, the gap is +0.34 pct at t=+0.9 — noise, exactly
as §60 predicts for a point estimate lifted off an underpowered slice. **Measure
the gate the money runs through, never a band average.**

What did survive, same run, same method: O/U 2.5 at odds >= 1.80 — the O/U
mirror's gate — is **Epicbet best on 65 pct of series, median +0.55 pct, mean
+0.89 pct, t=+8.5, n=1,429**, and it reproduces on the three-book subset
(58 pct, +0.82 pct median, t=+3.0, n=191). The market split is the real
structure: Coolbet prices 1x2 / double-chance / BTTS better, Epicbet prices
totals / corners / Asian handicap better.

One more guard that mattered: before an outlier filter, `asian_handicap` read
**Coolbet +6.63 pct, t=+9.8** — while Epicbet held the best price more often, a
contradiction that is the tell for §9. Dropping pairs more than 25 pct apart
(line mismatches and bad quotes, 1,279 of 19,137) flipped it to the true
**Epicbet +0.83 pct median, t=+12.3**. A mean and a best-price rate that
disagree is never a subtle finding; it is outliers.


## 59. Retention keeps the LATEST pre-kickoff row — and until 2026-09-11 it kept nothing else at the books we bet

Two facts to know before writing any query against historical `odds_snapshots`.

**(a) After 7 days, a price series is at most three rows.** `prune_old_simple`
keeps `is_opening`, `is_closing`, and the latest pre-kickoff row per
`(match, bookmaker, market, selection, handicap_line)`. Everything between is
gone. So any analysis of the intra-day price *path* — drift, velocity, "was a
better price available two hours earlier" — only works inside the 7-day window.
The model is unaffected: all four odds-derived training features
(`pinnacle_implied_*`, `pinnacle_implied_over25/under25`,
`ou25_bookmaker_disagreement`, `market_implied_btts_yes`) select the latest
pre-kickoff row per series, and where an `is_closing` row exists it **is** that
row — 453,291 of 453,311 = 100.0%.

**(b) Anchor flags are a function of sweep cadence, not of importance.**
`is_closing` is stamped at write time as `abs(minutes_to_kickoff) <= 15`. Any
book whose sweep does not happen to run inside that window gets no anchor. Until
2026-09-11 the direct-book writers used `<= 5` against a 30-minute sweep, so
**Epicbet had anchors on 0.09% of its rows, Coolbet 0.22%, Unibet-Site 0.00%** —
against Pinnacle's 39%. Consequences for anyone reading old data:

* our three bettable books have **one** surviving row per series before
  2026-09-11, and **no opening price at all** — do not try to compute own-book
  open-to-close drift over that period, the data does not exist;
* **52.6% of all price series carry no anchor of either kind** (measured on one
  day of finished matches: 48,247 of 91,701) and survive only via the
  anchorless fallback. A query filtering `WHERE is_closing` silently drops half
  the universe. CLV does not have this problem — `CLOSING-PRE-KO-FALLBACK`
  resolves against the surviving row, and coverage is 95-100% at our books.

**(c) In-play is a separate regime.** A post-kickoff row can satisfy neither
anchor flag nor the pre-kickoff fallback, so before 2026-09-11 retention deleted
**every** in-play row. In-play rows are now downsampled to one per minute per
series and kept indefinitely; the pre-2026-08-21 history lives at full
resolution in `odds_snapshots_inplay_archive` (155,048 rows, migration 329).
Query that table, not `odds_snapshots`, for anything before 2026-08-21.

> **See §64 (2026-09-15) for what (a) does to a RESULT.** A 30-day backtest that
> selects prices by recency is ~8 days of data plus 22 days of retention artifact:
> measured, half of one headline ROI came from pruned days, and 28% of its legs
> were reconstructions the live job could never have produced.

**(d) Never put a literal percent sign inside a SQL string in this repo.**
psycopg2 parses `%` as a parameter placeholder, so one in a *comment* raises
`IndexError` per batch — and `prune_old_simple` caught the exception and printed
"Would delete: 0 rows", which reads exactly like a clean database. Smoke
`ODDS-PRUNE-INPLAY-PROTECTED` now scans the module's SQL strings for it.

## 60. Compute the POWER before reporting a difference — or you will report noise three times

Added 2026-09-11 after giving the owner three different answers to one question
(1x2 odds floor 3.20, then 2.00, then 2.80) on **data that never changed**.

The data was not the problem and neither was any single analysis. The problem was
reporting a point estimate from an underpowered slice as if it were a finding.
Each subsequent test moved the estimate, so each re-run produced a new "answer",
and the owner correctly asked whether a fourth run would produce a fourth.

**The number that ends it:** returns at these prices have sd ≈ 1.5-1.6, and the
observed gap between the two gates was **0.33 pp of ROI**. Detecting that at 80%
power needs **2.5 MILLION bets per arm**. Even a 2 pp difference needs 102,000.
We had 1,475 and 1,984 — and the marginal band the whole thing hinged on was
n=377-509, which the owner rightly described as *"like a data of one match day"*.

**So the rule: before reporting that A beats B, compute the n required to detect
the difference you just measured.** One line:

```python
n_per_arm = 2 * ((1.96 + 0.84) * sd / observed_difference) ** 2
```

If that number exceeds what you have — and for ROI differences under ~2 pp it
always will — the honest output is **"indistinguishable"**, not the sign of the
point estimate. Say so and move to a metric that converges: CLV needs ~334
settled bets where ROI needs ~9,300.

**The second trap in the same episode: an ASYMMETRIC test.** The third answer
("keep 2.80") came from applying fold-robustness to the CHALLENGER only. Run on
both, neither gate was fold-robust (A: +2.9/−2.7/+28.0, B: +8.9/−0.3/+23.8).
Rejecting a change for failing a bar the incumbent also fails is status-quo bias
dressed as rigour. **Whatever test you apply to the proposal, apply to the
incumbent in the same run and print both.**

## §63 — `real_bets.clv` changed meaning on 2026-09-11: own-book close, fresh or NULL

**Before 2026-09-11** `real_bets.clv = actual_odds / closing_odds - 1` with
`closing_odds` from `get_closing_odds()` called with **no bookmaker** — whichever
of the ~13 API-Football books sorted last at kickoff. Every real bet is placed at
a direct book (Coolbet so far), so the number never answered the question it was
read for. Measured on 130 settled real bets: stored `clv` averaged **+4% to
+17%** per lead-time bucket, while the same bets against **Coolbet's own later
price** had a median of **0.0%**.

**Now (DIRECT-BOOK-CLV, migration 332):**

- `clv` — vs the last pre-kickoff, non-live price **at the bet's own book**, and
  only if that price was taken within `DIRECT_CLOSE_MAX_MIN` (60) minutes of
  kickoff. Otherwise **NULL** — a Coolbet price 5h before kickoff is not a close,
  and CLV against the placement snapshot itself reads 0% by construction.
- `closing_bookmaker`, `closing_minutes_before_ko` — which feed and how fresh.
  Tighten the bound in analysis (`closing_minutes_before_ko <= 20`) once
  NEAR-KICKOFF-CAPTURE has been writing for a few days.
- `clv_pinnacle` — de-vigged Pinnacle CLV, same definition as
  `shadow_bets.clv_pinnacle`, so real and paper bets sit on one scale.

**Rules:** never pool `real_bets.clv` rows settled before the backfill with
rows after it (the backfill rewrites all of them, so re-run it if in doubt:
`scripts/backfill_real_bets_direct_clv.py`). Own-book CLV and Pinnacle CLV answer
different questions — "did we bet too early at OUR book" vs "did we beat the
sharp market" — and a price drifting out with negative Pinnacle CLV means the
market moved against the pick, not that waiting would have been free money.
`scripts/direct_book_clv_report.py` prints both side by side.

`shadow_bets.clv` / `simulated_bets.clv` were deliberately NOT changed: other
work is mid-measurement on them, and changing a definition under a running
comparison pools two quantities.

---

## 61. `edge_percent` is a FRACTION — but three paper bots stored it ×100 until 2026-09-13

`shadow_bets.edge_percent` / `simulated_bets.edge_percent` hold a **fraction**
(0.127 = 12.7%). Every reader multiplies by 100 to display
(`coolbet_signaler.py:219`, `coolbet_placer.py:324/2010/2321/2600`,
`coolbet_prekickoff_alert.py:195`, `email_digest.py:161`).

`bot_corners_paper_shadow_v1`, `bot_team_total_paper_shadow_v1` and
`bot_1h_1x2_paper_shadow_v1` wrote `round(edge * 100.0, 4)` instead — medians of
1.65–2.83 against 0.127 for a normal bot, maxima up to 96.53.

**The trap:** an `edge >= 0.13` filter on those bots retained **97%** of their
picks instead of ~8%, so a whole sweep reported "no edge floor helps" on floors
that never bound. Nothing was visibly broken — the filter ran, returned rows,
and produced plausible-looking numbers.

**The tell, and it generalises:** if a cumulative floor barely changes `n` as you
raise it, the units are wrong — not the signal. Print `n` at every floor and
watch it fall; a floor that keeps 97% at "13%" is not a floor.

Fixed in code + migration 334. **The live generation gate was never affected** —
it compares the raw `edge` to `EDGE_FLOOR` before the write — so no bad pick was
ever made. Only the analysis was wrong.

## 62. `clv_pinnacle IS NULL` means the DE-VIG could not run — it does NOT mean Pinnacle has no price

Two genuinely different causes, and they were conflated for months:

* **Pinnacle really has no quote.** `btts` is the real case: 0 Pinnacle rows of
  3,076,350 BTTS snapshots, because API-Football's Pinnacle feed carries 8 bet
  types and BTTS is not one (gotcha 39). Permanently unmeasurable.
* **We never taught the de-vig the market name.** `get_devigged_pinnacle_close_prob`
  needs the full complement set from `_market_complement_selections`, and that
  helper returned `None` for anything outside `1x2` / `btts` / `over_under*`.
  `corners_*`, `team_total_*` and `1x2_1h` all fell through — **while Pinnacle
  was quoting them heavily** (66,313 snapshots on `corners_ou_95`, 133,152 on
  `team_total_home_15`, 118,272 on `1x2_1h`).

853 settled picks across three bots sat with no validator, and the recorded
explanation was the first cause when it was actually the second. Fixed
2026-09-13; `bot_corners_paper_shadow_v1` turned out to be **CLV +3.11% at
t=+12.66 on n=381** — a significant result nobody could see.

**Before writing off a market as unmeasurable, check
`odds_snapshots WHERE bookmaker='Pinnacle' AND market=...` yourself.** A NULL
column is evidence about our code first and the market second.

---

### O/U calibration has THREE eras — never pool across them

**Added 2026-09-13 (OU-CALIBRATOR-DOMAIN-MISMATCH).** Any O/U 2.5 / 3.5 analysis that
spans these boundaries is measuring a calibrator change, not a bot or a strategy:

| Era | State |
|---|---|
| → 2026-09-03 10:49 UTC | **No O/U calibration at all.** `model_calibration` held no `over_under_*` row, so `apply_platt` was a silent no-op and `cal_prob` was stage-1 Pinnacle shrinkage only. |
| 2026-09-03 10:49 → 2026-09-13 | **The domain-mismatched curve.** Fitted on raw ensemble probs, applied to Pinnacle-shrunk probs. Output range [0.3028, 0.6663], fixed point 0.4713. Inflated every probability below 0.4713 by 5–11pp and manufactured ~10× the pick volume. |
| 2026-09-13 → | Rows removed (migration 335). Back to stage-1 only, pending `OU-CALIBRATOR-REFIT-ON-SHRUNK`. |

Concretely, this invalidates lifetime O/U figures for every model-anchored O/U bot,
because their history straddles two incompatible regimes with no cohort marker — the
same shape as gotcha #39 (ENSEMBLE-RECALIBRATION) and the `+selcal1` precedent for 1x2
triggers. Report pre- and post- separately with explicit n; do not average them.

**This does NOT apply to 1x2.** 1x2 is fitted by a different script (`fit_platt.py`,
nightly, from `simulated_bets.calibrated_prob` — the correct domain). Verified: `1x2_home`
held `a=1.6081, b=−0.8604` continuously from 2026-08-30 through 2026-09-13 with no step
change on 09-03. 1x2 shows a similarly compressed range ([0.297, 0.679], fixed point
0.4766) but *chronically*, so it is a separate open question, not part of this incident.

---

### `simulated_bets.pnl` is priced at a snapshot nobody could take — use the executable price

**Added 2026-09-14**, after I summed the raw column mid-investigation and quoted an
inflated drawdown back to the owner. It is written down because the trap is silent:
`pnl` is a real, populated, sensible-looking column, and nothing about reading it warns
you.

`pnl` is settled from **`odds_at_pick`**, which STALE-BEST-ODDS (`5d8985a`) showed is the
highest price ever *seen* for that selection, not one that was ever *on offer*. The
honest basis is `COALESCE(odds_at_pick_live, odds_at_pick)` — the price actually
available when the pick was made.

Measured on the live ledger, all bots, settled only:

| basis | total P&L |
|---|---|
| stored `pnl` (= `odds_at_pick`) | **−€39.79** |
| recomputed at `odds_at_pick_live` | **−€487.15** |

**The raw column is €447 optimistic.** On `bot_v10_all` alone: +€339.73 / +6.65% ROI
stored, against +€158.57 / +3.10% at the executable price — and 288 of its 640 settled
picks have a live price differing from the pick price, mean **−7.34%**.

The UI is already right. `SHADOW-PAGE-ROI-INFLATED` (2026-09-05) converted
`/admin/shadow-bots` and the per-bot page to `execOdds()`, and
`docs/BETTING_GATE_DECISIONS.md` fixes executable price as the canonical method. So a
disagreement between the page and your query is the *query* being wrong, which is the
opposite of the instinct — I spent an hour treating the UI as the suspect.

```sql
-- correct
SUM(CASE WHEN result = 'won'
         THEN stake * (COALESCE(odds_at_pick_live, odds_at_pick) - 1)
         ELSE -stake END)
-- wrong, and quietly so
SUM(pnl)
```

Coverage is ~85% of settled rows, so keep the `COALESCE` fallback rather than dropping
uncovered picks — silently shrinking the population is its own bias.

---

### A feature that beats the market is reading the answer — run the canary

**Added 2026-09-14 (ELO-FORM-LEAK).** Before trusting any feature, any offline
eval, or any model comparison:

```bash
python3 scripts/leakage_canary.py --outcome home   # also: over, btts
```

It ranks every numeric `match_feature_vectors` column by |corr| with a settled
outcome and flags any **non-market** feature that reaches the strongest
market-derived one. The rule it encodes is hard: the de-vigged market is
thousands of informed participants pricing the same fixture with at least the
information we hold, so **no strictly pre-match feature can out-discriminate it**.
A column that does is not brilliant, it is leaking.

`elo_diff` scored |corr| **0.4116** against a market ceiling of **0.3658** and was
the model's largest input for four months. Nothing flagged it because nothing was
looking.

Two things the canary deliberately excludes, both learned by getting them wrong
first:
- **Market-derived columns** (`pinnacle_*`, `implied_*`, `opening_*`, drift, CLV…)
  are *expected* to approach the ceiling — they are the market.
- **Label columns** (`total_goals`, `match_outcome`, `score_*`…) live in the same
  table and trivially correlate. `total_goals` reads 0.761 against over-2.5
  because it *is* the total. Verified against the live bundle's `feature_cols.pkl`
  that none of them is actually a feature.

Current state: `over` and `btts` are clean; `home` flags only `elo_diff`, pending
the stored-row rebuild (master task #2). The smoke test `LEAKAGE-CANARY` pins that
known set, so a **new** leak fails CI.

---

### MFV coverage changed on 2026-09-14 — know which era your rows are from

**ELO-FORM-LEAK + the rebuild.** `match_feature_vectors` rows for matches from
2026-05-01 were rebuilt on 2026-09-14. Two consequences for any analysis:

1. **`elo_*` / `form_*` coverage legitimately DROPPED** ~8–10% (`form_momentum_*`
   ~43%, since it needs two form points). Those rows have no strictly-prior
   rating, so they have no honest pre-match value. A NULL there is correct, not
   missing data — do not impute it back.
2. **Pre-rebuild rows are preserved in `mfv_pre_elo_fix_backup`** (49,395 rows).
   Those are the **leaked** features every live model bundle was trained on. Keep
   them: they are the only baseline against which a retrained model can be
   compared, which is a stronger reason to retain them than rollback.

So `elo_diff` means two different things depending on when the row was written.
Before the rebuild it partly encodes the match result (AUC 0.7536 vs a market at
0.7270 — impossible for a pre-match feature). After, it does not (0.6134). **Never
pool the two.**

---

## ⚠️ CORRECTION 2026-09-14 — "disagreement AUC below 0.5 means anti-predictive" is WRONG

Several entries in this file (and in `MODEL_ANALYSIS.md`, `PRIORITY_QUEUE.md` and
the defect register) read a **model-minus-market disagreement AUC below 0.5** —
0.449 on O/U, 0.344 on 1x2, 0.3775 in the residual test — as evidence that our
disagreement with the market is *anti*-predictive, i.e. that fading our own model
would carry information.

**That inference is invalid.** Residual AUC below 0.5 is the **mechanical
signature of any model less informative than the benchmark it is differenced
against**, and carries no directional content. Verified by simulation
(`scripts/residual_auc_null_simulation.py`) on models constructed to be noisy,
shrunk copies of the market — **zero incremental information and no inverse
signal by construction**:

| construction | model AUC | residual AUC |
|---|---|---|
| `sigmoid(0.7·logit(mkt))` | 0.7204 | **0.2807** |
| `sigmoid(0.7·logit(mkt) + N(0,0.5))` | 0.6718 | **0.4049** |
| `sigmoid(0.5·logit(mkt) + N(0,0.8))` | 0.6011 | **0.3866** |
| `sigmoid(1.0·logit(mkt) + N(0,0.8))` | 0.6613 | **0.4830** |

Every observed figure sits on that curve. The correct reading is **"less
informative than the market"** — which the fitted blend weight of 0 already says,
more directly and without the extra inference.

Concretely: an unconstrained blend fit gives α = **−0.1075**, worth **+0.005%**
out of sample, against **+0.164%** from simply recalibrating the market alone.
The negative weight is measuring the de-vig, not an inverse model signal. **Do
not build a fade-the-model strategy on it.**

## 63. An exact-timestamp join across books measures WRITE GRANULARITY, not simultaneity

Sibling of §10. That one says an **unpaired** cross-book comparison measures
coverage. This one says a **paired but exact-timestamp** one measures how each
scraper happens to write rows.

`odds_snapshots` timestamps each **row** individually, not each sweep. Coolbet's
1X2 triple lands across ~100 milliseconds:

```
00a41a30  home  2026-09-14 09:20:15.928829+00
00a41a30  draw  2026-09-14 09:20:15.975788+00
00a41a30  away  2026-09-14 09:20:16.026222+00
```

So `GROUP BY match_id, bookmaker, timestamp` finds a complete triple in **0.1%**
of Coolbet timestamp-groups — against **99.9%** for Epicbet and **100%** for
Unibet-Site, which write their three rows under one timestamp. The difference is
not data quality. It is two ingest paths stamping rows differently.

**What it cost.** An audit concluded from this that only **5.9%** of co-priced
market-fixtures had quotes within 15 minutes, that we *"cannot compute a
trustworthy cross-book comparison at all, let alone act on one"*, and that the
OWN-path kill criterion needed **two weeks of new synchronised polling** before
it could be evaluated. Assembling each book's triple from a ±2 min window
instead:

| | fixtures with a triple from all three | aligned ≤15 min |
|---|---|---|
| exact-timestamp grouping | 17 | 5.9% |
| ±2 min assembly | **1,073** | **33.5%** |

The comparison was runnable the whole time. See
`docs/OWN_PATH_VERDICT_2026_09_14.md` and
`scripts/own_path_kill_criterion.py::assemble`.

**And do not reach for the schedule instead.** The obvious "fix" — put the
scrapers on one cron minute — would not have worked either: none of the three
writes at its cron minute. Each sweep smears over 10–25 minutes, and Coolbet's
Mac daemon writes near-continuously (~309 distinct write-minutes/day against the
48 a `:03/:33` cron implies). Aligning start minutes does not align per-fixture
write times.

**Do not read that 309 as per-fixture resolution — it is BREADTH.** Measured
2026-09-14 on the frozen path window, Coolbet writes on the most distinct minutes
of any of our 12 books and still has the **worst-observed price path of all of
them**: median **3 observations per price series** over a 4.5-hour span, median
**one** distinct price. Epicbet writes on the fewest minutes (49/day) and has the
best path (22 observations, 22.3 hours). "Coolbet writes continuously, so we see
it move first" is false, and it was the founding premise of a line-movement
hypothesis — see `docs/OWN_LINE_MOVEMENT_2026_09_14.md` §2.

**The rule:** before comparing books, assemble each book's market from a small
window (±2 min is ample), *then* align the assembled quotes across books. Never
join books on timestamp equality.

## 64. A "30-day backtest" on `odds_snapshots` is ~8 days of data plus 22 days of retention artifact (2026-09-15)

§59 says retention (`prune_old_simple`) keeps at most three rows per series after
7 days. This is what that does to a **result**, measured, because the first time
it bit nobody noticed until an adversarial re-derivation went looking.

**The mechanism.** The idiomatic "price just before kickoff" query is

```sql
SELECT DISTINCT ON (match_id, market, selection, bookmaker) ...
 WHERE o.timestamp < m.date - interval '45 minutes'
 ORDER BY match_id, market, selection, bookmaker, o.timestamp DESC
```

Inside the 7-day window that returns *the last price before the cutoff*. Outside
it, it returns **the only row retention chose to keep** — and retention chose the
latest pre-kickoff row. The query looks identical, the column means something
else, and nothing errors. Measured rows per `(match, book, market, selection)`:

| kickoff era | rows per series |
|---|---|
| pruned (>7d old) | **2.6 – 4.7** |
| intact (<7d old) | **13.1 – 20.2** |

**What it did to a live decision.** A 30-day backtest of the published picks rule
returned **ROI +16.56% (n=72)**. Decomposed by era:

| slice | n | ROI |
|---|---|---|
| pruned days, unreconstructable | 35 | **+28.40%** |
| intact days | 37 | **+5.35%** |

**Half the headline came from a data regime that no longer exists.** Worse, on
the two biggest contributing days a point-in-time replay — simulating the job's
real run times, its 6-hour snapshot window and its kickoff window — found **ZERO**
qualifying legs at any hour of the day. **20 of the 72 legs (28%) were
reconstructions the live job could not have produced.**

**Extending the window does not help; it makes the artifact bigger.** The same
rule over 90 days yields n=81, of which **72 fall in the last 30 days**. The
preceding 60 days contribute 9 legs. The recent window is not cherry-picked — it
is the only window that produces data, and that is itself the tell.

**The rules.**

1. **A backtest that selects a price by recency is only reconstructible inside
   the 7-day window.** Beyond it you are measuring retention's choice.
2. **Simulate the job's actual run times** — `now()` → a fixed `T`, the job's own
   snapshot window, the job's own kickoff window — rather than querying "the last
   row before kickoff". On the case above, the point-in-time arm shared only
   **19 of 37** legs with the recency query: the two methods select largely
   different bets, so one is not an approximation of the other.
3. **Report the intact-window n separately, always.** If the honest n is 37, say
   37. A number carried by rows the live system could never have seen is not a
   backtest of that system.
4. **Suspect any per-day rate that jumps at the 7-day boundary.** Here: **1.6
   legs/day** on pruned days against **4.6/day** on intact ones. That step is
   retention, not football.

**Sibling traps.** §59 (the retention mechanics themselves), §63 (an
exact-timestamp join across books measures write granularity, not simultaneity —
in the same analysis, **93.4%** of candidate legs had an alignment gap of exactly
0.0 minutes because AF writes every book in one bulk sweep, which made a 60-minute
alignment gate very nearly inert), and §60 (compute the power before reporting a
difference — the +16.56% carried a one-sided p of 0.127 and a minimum detectable
effect of ±40.9% at that n).

## 62. `odds_snapshots` writes a COMPLETE triple per fetch — never take `DISTINCT ON` per leg

Added 2026-09-17 after I made this exact error twice in one session, having
flagged the same class of mistake three times in other people's work that day.

**Measured:** for every bookmaker except Coolbet, **99.8–100% of fetch-rounds
write all three 1x2 legs at a single timestamp**. One fetch, one complete
simultaneous triple.

So this is wrong:

```sql
-- WRONG: takes each leg from whichever round was latest FOR THAT LEG
SELECT DISTINCT ON (match_id, selection) ...
  FROM odds_snapshots ORDER BY match_id, selection, timestamp DESC
```

It assembles home from one round and away from another, and the resulting
"overround" is a time-smear, not a market. It inflates, and it inflates MOST for
the books that re-price fastest — which is how I concluded our Pinnacle feed
"is not Pinnacle" and had to withdraw it. Measured properly (per timestamp), our
Pinnacle is 5.33% against a 4.92% external reference; measured the wrong way it
reads 9.15%.

**Right:** group by `(bookmaker, match_id, timestamp)` and keep the groups that
hold all three selections.

```sql
SELECT bookmaker, match_id, timestamp,
       sum(1.0/odds) - 1 AS overround
  FROM odds_snapshots
 WHERE market = '1x2' AND is_live IS NOT TRUE
 GROUP BY 1,2,3 HAVING count(DISTINCT selection) = 3
```

**COOLBET IS THE EXCEPTION and needs a window.** It writes one leg per
timestamp (0.1% complete triples per timestamp, sub-second apart), so it must be
assembled inside a small window — `own_path_kill_criterion.assemble()` does this
and its docstring already said "Coolbet needs ~100ms of tolerance". That note
was easy to read as a quirk; it is load-bearing.

### 62b. Coolbet quotes FINER PRECISION than other books — this is not corruption

**Corrected 2026-09-17, same day it was written. The original §62b claimed 29%
of Coolbet rows were "foreign values"; that was wrong and is withdrawn.**

Coolbet's API returns high-precision odds (`1.1524`, `1.0057`); its website
rounds them for display, and every other book in `odds_snapshots` arrives via
API-Football which rounds to 2dp. So **Coolbet is the only book whose stored
prices carry more than 2 decimals, and that makes its data more precise than the
rest, not corrupted.**

Verified by running `coolbet_explorer --match-id ... --dry-run` live and
comparing to stored rows for the same fixture: live API 1.157 / 1.007 / 1.106 /
1.194 against stored 1.1524 / 1.0057 / 1.1029 / 1.1893.

**Do not treat decimal precision as a data-quality signal.** The "every value
caps at 2.75" pattern that made it look like a different quantity is an artifact
of price level: fine precision runs at 52.0% of rows at odds 1.00–1.50, 19.9% at
2.50–2.99, and **0.3% above 3.00**, because the book quotes short prices finely
and long prices in round steps (3.00, 5.00, 12.00).

**Still genuinely open:** our stored Coolbet 1x2 overround on top-5 leagues is
7.20% against an external reference's 3.05%, and excluding the fine-precision
rows does not move it. That gap is real and unexplained; staleness is the
leading candidate. See `SHARP-BOOK-PRICE-DISCREPANCY` in PRIORITY_QUEUE.

### 62c. How to check you have not done this

Before trusting any overround, margin or "best price" number, run:

```sql
SELECT count(*) FILTER (WHERE n = 3)::float / count(*) AS complete_share
  FROM (SELECT bookmaker, match_id, timestamp, count(DISTINCT selection) n
          FROM odds_snapshots WHERE market='1x2' GROUP BY 1,2,3) s;
```

If that is near 1.0 for your books, group by timestamp. If it is near 0 (Coolbet),
assemble in a window. Do not mix the two strategies in one query.

---

## 65. Combining books by "each book's own latest quote" smears TIME and manufactures cheapness (2026-09-17)

§62 is about assembling a market **within** one book. This is the trap one level
up: assembling a market **across** books. They are independent, and fixing the
first does nothing for the second — this project fixed §62 and then walked
straight into this one within the hour.

The obvious combination looks like this and is wrong:

```python
best = {s: max(latest_market(obs[b], SIDES)[s] for b in BOOKS) for s in SIDES}   # WRONG
```

`latest_market` is correct per book. The defect is that **each book's latest
lands at a different wall-clock time**. Over 30 days of stored pre-match 1x2 the
median gap between two books' own latest quotes is **7.2 hours**. So that
dictionary mixes a live quote with a stale one and calls the result a market.

The apparent overround falls monotonically with the gap — this is the whole
finding in one table:

| the two books' quotes are... | n | best-of-3 overround |
|---|---|---|
| < 15 min apart (contemporaneous) | 293 | **6.55%** |
| 15 min – 2h apart | 828 | 6.07% |
| 2 – 12h apart | 1,610 | 5.16% |
| 12h+ apart | 1,157 | **4.37%** |

**2.18pp of pure artefact**, and it always points the flattering way, because
taking a max over two moments in time can only ever find a better price. It is
the best-of-books mirage (§52/§55) displaced from books into time: a market no
book ever offered, stitched from one live price and one stale one.

**What it cost.** It produced a top-league figure of **3.76%** that is really
**~3.1%**, an "18% of fixtures under 2%" that does not survive, and — worst — it
briefly made the strict 15-minute cross-book alignment in `own_margin_by_tier.py`
look like an over-strict flaw to be relaxed. That alignment was the correct
method all along. **If a stricter method gives a worse number, suspect your
relaxation before you suspect the strictness.**

### The rule

Use `best_across_books()` from `workers/utils/odds_assembly.py`. It assembles
each book independently, then **returns None** unless every contributing book's
anchor sits inside `CROSS_BOOK_MAX_GAP_S` (15 min). Dropping a fixture is
correct; a number built from stale legs is not. Guarded by the smoke test
`ODDS-ASSEMBLY-CROSS-BOOK`.

### Two things refuted while chasing this — do not re-derive them

- **Overrounds do not tighten toward kickoff.** Top leagues sit flat at ~3.1%
  from 24h out; all fixtures *widen* slightly (5.95% at 48–24h → 6.6% at 3–1h).
  There is no "wait and bet later" edge. The 2.66%-vs-3.75% difference that
  suggested one was this smear, not timing.
- **`leagues.tier` is not a major-league proxy.** **1,012 of 1,461** leagues are
  `tier=1`, including Andorran second divisions, Swiss regional groups, U19 and
  reserve sides. Any "by tier" analysis is measuring almost nothing. Filter by an
  explicit league list instead.

---

## 66. A bootstrap CI over a sample with ZERO losses is a fiction that always reads "significant" (2026-09-18)

Found while sweeping in-play triggers against collected Epicbet boards
(`scripts/inplay_trigger_sweep.py`). The sweep tested 288 cells and reported
**4 with a 95% CI strictly above zero** — which looked like the first positive
in-play result we had ever had.

All four were the same artifact. They were "back the leader at a two-goal lead":

| window | n | wins | win rate | avg odds | ROI | ROI if ONE more loss |
|---|---|---|---|---|---|---|
| 55'–69' | 36 | 36 | **100%** | 1.107 | +10.67% | +7.59% |
| 65'–79' | 33 | 33 | **100%** | 1.049 | +4.88% | +1.70% |
| 75'–89' | 22 | 22 | **100%** | 1.031 | +3.09% | **−1.60%** |
| 80'–94' | 17 | 17 | **100%** | 1.020 | +2.00% | **−4.00%** |

A bootstrap resamples the OBSERVED outcomes. If the sample contains no losses,
no resample can contain one, so the interval collapses to the spread of the
*odds* and can never cross zero. The CI is not measuring the risk of the bet; it
is measuring nothing at all. And a two-goal lead at 80' does not convert 100% of
the time — it converts ~97–99% — so at odds 1.02 the cell is roughly break-even
to negative in truth, which the "significant" CI actively conceals.

**Tell:** a cell with an extreme win rate (>95%) at short odds (<1.15), a
suspiciously narrow CI, and n in the tens. Short-priced near-certainties are
where this always bites, because they are exactly the bets whose loss is rare
enough to be absent from a small sample.

**Guard:** `summarise()` computes `losses` and refuses to treat a CI as
meaningful below **two observed losses** (`ci_ok`), printing `!CI` and excluding
the cell from the significant count. It also reports `roi_one_more_loss` — what
the cell becomes if a single winner had gone the other way — which is the only
honest sensitivity number for this shape. Pinned by
`INPLAY-SWEEP-CI-NEEDS-LOSSES`.

**With the guard applied the same sweep reads: 0 trustworthy positives out of
288 cells, against a noise expectation of ~7.2.** That is the real result, and
it is a clean negative rather than a discovery — which is the point of running
the check before believing the table. See also gotcha 8 (gate on CLV, not ROI)
and the 2026-09-14 in-play round, where a +9.0% cell died to a window-widening
check for a related reason.


## 67. An anchor-anchored bot SEARCHES FOR your data faults — its contamination rate is not the base rate (2026-09-20)

**SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES-2026-09-20.** Measured across every
`shadow_bets` cohort, the share of picks whose book price sits >1.5625× from
Pinnacle on the same selection:

| cohort | n | contaminated | % |
|---|---|---|---|
| `trigger_1x2_sharp` | 25 | 20 | **80.0%** |
| `team_total_paper` | 937 | 32 | 3.4% |
| `trigger_1x2_model` | 416 | 9 | 2.8% |
| `coolbet_trigger` | 1,466 | 24 | 1.7% |
| `unibet_trigger` | 1,051 | 15 | 1.6% |
| every timing cohort | — | ≤10 each | <1% |

**Do not read the 80% as "our odds are 80% broken".** The base rate is under 3%.
The sharp bot's edge definition is *literally* "this book disagrees hugely with
the de-vigged Pinnacle line", so it is a **search procedure for exactly the rows
that are most wrong**. Model-anchored bots meet the same bad rows and pass over
them.

**Two consequences for any analysis:**

1. **Never estimate feed quality from a bot's pick population.** It is the most
   biased sample of your own odds table that exists. Estimate it from the odds
   table directly, paired against an anchor.
2. **Every gate we own is a LOWER bound on `edge = p − 1/odds`**, and a wrong
   price only ever *inflates* the edge. So a price fault clears every floor in
   the system and can never trip one. On a near-true anchor the only gate with
   the right sign is a **ceiling** (`BotConfig.edge_ceiling`).

**A ratio guard is market-shaped, and that is a trap.** 1x2 prices span 1.02–101,
so a wrong fixture usually blows past 1.5625× — it caught 20 of 25. **O/U prices
are compressed into ~1.2–3.0, so it caught 0 of 6**, while Unibet-Site's O/U 2.5
on Hapoel Tel Aviv read over 2.20 / under 1.58 against Pinnacle's over 1.69 /
under 2.19 *and* Coolbet's over 1.62 / under 2.15 — the two-way market inverted,
unmistakably another fixture. **When you need a cross-book sanity test on a
compressed market, test the ORDERING (which side is favourite), not the ratio.**
Two real books cannot disagree about the favourite in the same market on the
same fixture.

**`inplay_slowstate` reads 91% contaminated and is an artifact of the test, not
a finding** — it compares in-play prices against a pre-match anchor. Exclude
`is_live` rows before running this comparison on anything.

**Voided rows are invisible to the scoreboard — but a void does NOT stay put
unless you name it correctly.** `shadow_bot_scoreboard` filters
`result IN ('won','lost')`, so `result='void'` removes a row from every
published number without deleting it. **However**, `settlement.
resettle_wrongly_voided_bets` runs nightly, re-grades every void on a finished
match, and **clears `void_reason` to NULL** on anything that no longer grades to
void. It skips only reasons that START WITH `quarantine`.

**So a deliberate quarantine MUST be written as `quarantine: <tag> — <why>`.**
The first pass of `SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES` used a bare
descriptive reason; all 31 rows were resurrected within hours and the +549.9%
ROI bot was back on the scoreboard by morning. It was caught only because a
verification replay returned 0 rows where it should have returned 31 — nothing
alerts on this. **Do not cite `KAMBI-CRITERION-CONTAMINATION-2026-09-05` as
proof the mechanism works**: those 14 rows survived because their matches are
postponed with NULL scores, which that pass skips anyway. Luck, not protection.

Reversible reasons (`postponed`, `no_ht_score`, corners unsettleable) must NOT
start with `quarantine` — they describe a state that can legitimately change,
and re-grading them is the pass's actual purpose.

**Do not delete rows** — `PRIORITY_QUEUE.md` says so explicitly, and they are the
only record of the upstream fault's reach. Smoke
`QUARANTINE-VOIDS-SURVIVE-THE-RESETTLER`.

---

## 68. A SINGLE reference book is not a reference — and a mirror leaves the draw leg alone (2026-09-22)

Written after `1X2-HOME-AWAY-INVERSIONS` was measured three times and got a
different answer each time. Two independent mistakes, both of which will be made
again by anyone comparing our stored prices against "the market".

**(1) Comparing one book against one other book cannot tell you which of the two
is wrong.** The first pass here asked "which books' 1x2 triples mirror
Pinnacle's?" over 120 days and got **377 pairs**. It is worthless. On fixture
`9abeb6cc` the answer included **thirteen different bookmakers** — 10Bet, 1xBet,
888Sport, Bet365, Betano, Betfair, BetVictor, Dafabet, Marathonbet, SBO,
Superbet, Unibet, William Hill — all "mirroring" Pinnacle on the same match.
Thirteen simultaneous independent parser bugs is not a hypothesis. **The
minority-of-one is the flipped row, so the reference has to be a CONSENSUS and
the thing you flag is the minority.**

Use the median of the *other* books' sum-to-1 normalised implied probabilities,
leave-one-out, with a quorum of 4+. Normalising is not optional: it is what makes
a 4%-margin exchange comparable with a 12%-margin retail book.

**And require a POWER guard.** On a pick'em fixture every triple mirrors itself,
so a mirror test has no power there and will happily flag ordinary disagreement.
Requiring the consensus to separate home from away by ≥ 0.15 in probability took
the count from 67 to 33; the 34 it dropped were not mirrors, they were fixtures
nobody can call. This single threshold moves the answer more than any other.

Measured properly: **33 of 251,923 (fixture, book) triples in 120 days, 0.013%**,
concentrated in the feeds we scrape ourselves (Coolbet/Epicbet/Unibet-Site/
Unibet-Kambi ≈ 0.13–0.26% each) against 0.008–0.021% on the API-Football books.
The earlier "not a defect in our scrapers specifically" conclusion was an
artifact of the bad reference; with a consensus, it **is** mostly our scrapers.

**Why the consensus is the right reference for OUR home/away and not just a
popularity contest:** the AF-fed books are keyed to the fixture by
`matches.api_football_id`, the same AF fixture `matches.home_team_id` was
populated from — so AF's "home" and ours agree by construction, and the books
that can drift are exactly the fuzzy-matched ones. If the fixture's own
orientation were wrong, every book would look mirrored at once and the test
would correctly refuse to single anyone out.

**(2) A home↔away mirror does not touch the DRAW leg — so a draw pick can never
be "struck on an inverted price".** Counting it turned 4 real hits into 17: one
`bot_coolbet_*` draw pick at 3.40 was re-evaluated hourly by the refresh job and
contributed **thirteen** duplicate rows on a single fixture. Filter to
`selection IN ('home','away')`, and de-duplicate on (fixture, bot, selection)
before quoting any count — the hourly re-evaluation cohort makes raw row counts
meaningless.

**The exposure question that matters is not "picks on an affected fixture".**
It is "picks whose `odds_at_pick` IS the inverted number". On these 33 triples
that is 161 → 4. The earlier "161 shadow_bets affected" figure in the ticket was
the first number and should never have been cited as exposure.

Tools: `scripts/audit_mirrored_1x2.py` (report-only), which runs the production
guard `workers/utils/mirror_guard.py` so the audit and the gate cannot drift into
two definitions of "mirrored".
## 69. A counterfactual that must pick a decision INSTANT has to be run at more than one (2026-09-22)

**BOOK-SET-COUNTERFACTUAL** (#005, `scripts/backtest_book_set_restriction.py`,
written up in `docs/BOOK_SET_COUNTERFACTUAL_2026_09_22.md`).

Any "what if we had priced off a different book set / used a different gate"
replay has to answer a question the real pipeline never had to: **at what moment
do you look up the price?** The pipeline re-prices hourly. A replay does not, so
it picks one instant per fixture and moves on.

I picked the defensible one — **kickoff − 6h, the median lead of the real
`bot_v10_all` 1x2 picks** — and got a clean, confident answer to the question the
exercise existed for: in **all four** market × gate-stack cells, the picks the
book-set restriction destroyed were *worse* calibrated than the ones it kept, i.e.
the selection-effect hypothesis was refuted. It reproduced. It had a sensible
mechanism. It was ready to write up.

Re-running the identical study at **kickoff − 12h** flipped the sign in **three of
the four**, and flipped the headline outcome comparison too:

| cell | Δgap at −6h | Δgap at −12h |
|---|---|---|
| v10 · 1x2 | +18.78pp (z +1.32) | +7.12pp (z +0.46) |
| v10 · O/U | **+5.44pp** (z +0.71) | **−10.06pp** (z −1.01) |
| placer · 1x2 | **+0.83pp** (z +0.15) | **−7.17pp** (z −1.21) |
| placer · O/U | **+2.01pp** (z +0.31) | **−14.04pp** (z −1.63) |

Post-cut O/U, all-books vs as-was: **3.2pp worse calibrated and −8.9pp ROI at −6h;
4.7pp BETTER and +14.8pp ROI at −12h.** Same fixtures, same books, same gates,
same calibrator — only the hour of the price changed.

Nothing reached |z| = 1.96 at either lead, which is the tell: **the "finding" was
always inside the noise, and the single-lead run merely dressed it in a
direction.** A one-lead study would have shipped "the hypothesis is refuted in
every cell" into the queue as a settled fact.

**Rules:**

- **Run every instant-dependent replay at two or more instants and report both.**
  If the sign moves, the answer is "no evidence", not whichever run you did first.
- Prices are not noise around a true value — they DRIFT (1x2 shortens ~11% by
  kickoff, 86% of the time). So the instant is a systematic factor, and choosing
  it by "what looks like the real lead time" hard-codes a bias you cannot see.
- **The comparison between arms can be robust while the outcomes are not.** In
  this study price (+2.1%), coverage (5.7–7.9% of selections with no accessible
  price) and volume (+52% to +76%) held at both leads, because both arms share
  the instant. Only the win-rate-dependent quantities moved. Separate the two
  kinds of claim explicitly and say which is which.
- Same family as #39: there, the window hid a regime change; here, the instant
  hides a price trend. Both make an arithmetically perfect number a statement
  about the method rather than about the system.

## 70. Raw `clv` breaks even at the closing book's MARGIN, not at zero (2026-09-22)

Found re-running [[#007]]/[[#024]]. The sharp anchor's headline result — **direct-book
CLV +9.21%, t=+5.1** — was quoted in the queue, in two agent reports and in this repo's
own task history as evidence the sharp anchor beat the market. It is raw `clv`, defined
as `odds / closing_odds − 1`, **with no de-vig on either side**. The closing quote still
carries the book's overround, so a bet that captured exactly zero value scores
`+margin`. On this population the closing book's median margin is **7.95%**, so the
break-even for that number was never 0 — it was roughly +8%.

`workers/jobs/settlement.py:1608` says so explicitly and migration 355
(`SHADOW-CLV-MARGIN-CORRECTED`) shipped the corrected column on **2026-09-15 — one day
after the run that produced the headline.** Nobody re-read the headline afterwards, so a
number that had already been superseded kept being quoted for a week as the reason to
keep accruing toward real money.

Corrected on the same picks the effect disappears and then inverts:

| | ticket window | all to 2026-09-22 |
|---|---|---|
| raw `clv` (break-even ≈ +8%) | +8.78%, t=+5.33 | +6.75%, t=+6.93 |
| margin-corrected (break-even 0) | **+0.86%, t=+0.57** | **−1.28%, t=−1.42** |
| + priced at the quote actually on offer | **−1.85%** | **−2.98%, t=−3.26** |

**Rules.**
- Never quote raw `clv` as a verdict. Use `clv_margin_corrected`; if you must show raw,
  print the book's margin beside it so the break-even is visible.
- A metric whose break-even is not zero is not a percentage you can compare to another
  percentage. This is the same class as §18 (a column's declared precision is part of
  the gate) — the *definition* is part of the number.
- When a migration changes how a metric is computed, **grep the docs for every figure
  derived from the old definition in the same commit** (the ripple-check rule in
  CLAUDE.md). Migration 355 changed the meaning of every CLV figure in the repo and
  none of them were restated.
- Two independent checks that both start from the stored column will agree with each
  other and both be wrong. Re-derive from `odds_snapshots` at least once.

**Related and worse:** on the same population `clv` is **exactly 0.0000 on 51 of 93
rows**, because the last snapshot we hold for our own book IS the quote we bet — we stop
polling Coolbet/Unibet-Site after placing. `closing_fresh` is true on **2 of 287**. Half
the sample carries no information at all, so even the corrected number rests on ~42 rows.
Before quoting own-book CLV again, check `closing_fresh`, not just `closing_bookmaker`.

---

## 71. A permutation test over NESTED subsets must be centred, or it re-measures the level

Found 2026-09-22 building `scripts/ou_odds_floor_sweep.py` ([[#073]]).

**The tell: a huge test statistic and a boring p-value in the same line.** The
first run reported `best |t| in grid = 7.17, family-wise p = 0.5917`. A |t| of
7.17 arising by chance 59% of the time is impossible, and that impossibility is
the signal — it means the statistic is measuring something the shuffle cannot
disturb.

**The mechanism.** Sweeping an odds FLOOR produces nested cells: `≥1.8` held 130
of 157 rows. Permuting the outcome across rows leaves a cell that big with
essentially the population mean every time. So its `t` was a restatement of *"the
O/U bot's CLV is −4.17%"* — true, already known, and nothing to do with price.
Every permutation reproduced it, so the observed max was never extreme.

**The fix is one line and it changes the answer completely.** Subtract the
population mean before permuting, so the test is on DEVIATIONS from the bot's own
level rather than on the level itself. Centred: `best |t| = 1.91, p = 0.3438` —
which is the honest reading (no band differs from the bot's average), and the
opposite of what an uncritical read of 7.17 would have suggested.

**The general rule:** a permutation null must break exactly the association you
are testing and nothing else. When cells are nested inside a population whose
mean is itself non-zero, shuffling within the population does not break "the
population has a mean" — so that mean has to be removed by construction.
`scripts/odds_band_by_market.py` already said this as *"centred within market"*;
§47 is the same trap from the other side (an odds-band effect is a BOT effect
until you split by bot). This is the third time this family has cost time.

**Cheap detector:** if a grid's best |t| barely moves between the observed data
and a shuffled draw, the statistic is dominated by something the shuffle is not
touching. Print one shuffled draw's max |t| next to the observed one.


## 72. `candidate_funnel` stores price and probability, never the edge — and the two sources' edges are different quantities (2026-09-23)

`candidate_funnel` ([[#082]], migration 384) is the rejected population every floor / grade /
de-vig question needs. Three rules for reading it:

1. **Derive the edge on read, per source.** `source='pipeline'` is a MODEL edge,
   `fair_prob − 1/odds` (probability points), against `threshold` in the same units.
   `source='publisher_*'` is a SHARP edge, `fair_prob × odds − 1` (multiplicative), against
   `threshold = MIN_EDGE`. They are never comparable (SYSTEM_MAP §1) — never pool them.
2. **It is truncated on purpose.** Only candidates within 5pp of their floor, or rejected by a
   later gate, are written. "How many candidates were far below the floor" is NOT answerable
   here — the counters in the run log are.
3. **One row per (day, source, bot, match, market, selection), latest decision wins.** A
   candidate accepted at 10:05 and dropped at 11:05 reads as dropped; `n_seen` says how many
   runs saw it. It starts 2026-09-23 — there is no history before that.
4. **`source='pipeline'` is the live betting run; `'pipeline_shadow'` is the shadow run**, which
   evaluates ALL bots including retired and off-cohort ones. Never pool them.
5. **Known limits:** a bot with several `strategies` keeps only the last strategy's verdict per
   candidate (the strategy is not stored); a consensus leg whose grade changes during the day can
   appear under two bots; `accepted` means it passed every gate, not that a bet was stored (the
   same-day dedupe can still skip it). A published leg is written once, as `selected`, and never
   re-labelled on later passes.

## 73. A consensus anchor and Pinnacle are PEERS, not substitutes — and they sit ~1 pt apart (#113, 2026-09-23)

`scripts/anchor_consensus_composition.py` (120 d, 24,625 fixtures): on outcome log-loss a
≥5-book consensus TIES AF-Pinnacle — so do 5 random books and the 5 SOFTEST books. That
does not make them interchangeable for EDGE work: outcome log-loss cannot see a 1–2 pt
price error. At decision time each anchor predicts its OWN close best (persistence), the
cross errors are symmetric, and a single soft book is clearly worse than both
(`scripts/anchor_closing_validation.py`, 1,717 fixtures, 7 d — 1X2 at KO−120: consensus→Pinnacle
close 1.65 pts vs Pinnacle→consensus close 1.68; Bet365→Pinnacle close 2.48). On 1,669 settled legs with both,
`clv_cons` runs 0.9 pts below `clv_sharp` (r = 0.976). Consequences: (1) an edge floor
tuned on one anchor must be re-derived for the other; (2) never pool `clv_sharp` and
`clv_cons`, and every reader that falls back must carry the source; (3) where Pinnacle is
missing, 51% of fixtures have only 1–2 books — no method makes an anchor there.


## 74. Weighting the consensus buys nothing; a 3–4-book close is usable for CLV only with its own label (#116, 2026-09-24)

**Weighting (`scripts/anchor_weighting_research.py`, rejected).** Five alternatives to the
equal-weight mean, on the SAME member books the resolver picks: accuracy weights (each
book's log-loss excess vs the leave-one-out consensus, estimated on a TRAIN window before
2026-08-15, scored on the 40-d HOLDOUT), margin weights (1/overround), de-correlation
weights, trimmed mean, median. 30 paired tests fixed up front (5 variants × outcome LL /
closing error vs Pinnacle close / vs a 3-held-out-book close × 1X2 and O/U 2.5), Holm.
* **Outcome log-loss: every variant ties equal weight in both markets** (1X2 n=10,272,
  O/U n=8,327; all |t| ≤ 2.2, none survives Holm). The forecast-combination puzzle holds.
* Closing error: accuracy weights move the anchor **0.026 pp (1X2) / 0.042 pp (O/U)**
  closer to the Pinnacle close on an error of 1.68 / 0.97 pp (t −4.9 / −8.2) — real but
  ~2–4% of the error and ~1/40 of any edge floor. Margin weights help 1X2 and HURT O/U;
  de-correlation hurts O/U; median/trim hurt vs held-out books in 1X2.
* Why accuracy weights look closer to Pinnacle: they up-weight **Marathonbet and 1xBet,
  whose residuals vs the consensus correlate +0.88 (1X2) / +0.84 (O/U) — effectively one
  opinion, and a Pinnacle-follower**. "Closer to Pinnacle" is partly "copies Pinnacle",
  which is circular for an anchor meant for fixtures WITHOUT Pinnacle. 888Sport~William
  Hill +0.65 is the next near-duplicate pair. Nothing else above +0.33.
* Decision: equal weight stays. Re-open only with a new mechanism, not by re-tuning.

**Thin close (`leg_clv_sharp.clv_cons_thin`, migration 394; `scripts/anchor_thin_consensus_quality.py`).**
* Coverage (7 d, 5,580 legs): 541 legs gain a thin close (295 on 3 books, 246 on 4), but
  **only 34 legs / 8 fixtures had no other CLV at all** — the rest already had `clv_sharp`.
  Mostly shadow-bot corners (338) and O/U (137). The legs still without any CLV are
  Asian handicap (unsupported) and team totals / DNB with neither Pinnacle nor 3 books.
* Closing quality by subsampling (≥5-book fixtures, 7 d; OPTIMISTIC, real thin fixtures
  are lower leagues): a 3-book close is 0.69 pp (1X2, p90 1.35) / 0.36 pp (O/U) from the full
  close; 4 books 0.50 / 0.26. Against the Pinnacle close the error grows 1.22→1.40 pp (3 books,
  +15%) and →1.31 (4 books, +7%) in 1X2; O/U 0.77→0.84 / 0.83. In CLV units a 3-book close
  adds ~1.7 pts (1X2) / 0.8 pts (O/U) of mean per-side noise vs the full close.
* Real thin fixtures that also have a Pinnacle close (small n: 1X2 63, O/U 221): thin→Pinnacle
  error 1.24–1.45 pp (1X2), 0.85–1.06 pp (O/U) — the same range as the subsample.
* Outcome log-loss (120 d): a random 3- or 4-book subset is worse than the full consensus
  by +0.0001 in 1X2 (t +2.6/+2.8) and ties in O/U — negligible.
* Agreement with `clv_sharp` (507 legs with both): r 0.979; mean offset −1.82 pts vs −0.87
  for `clv_cons` — but WITHIN market the thin offset matches the ≥5 one (corners −2.45 vs
  −2.26, O/U −0.56 vs −0.28, 1X2 −1.00 vs −1.01); the pooled gap is market mix (corners).
* **Verdict: trustworthy as a directional, per-leg CLV with its own label** — same bias
  as `clv_cons` within market, ~15% more error than a ≥5-book close. Report it SEPARATELY,
  by market, never averaged with `clv_cons` or `clv_sharp`, and never as a staking input or
  gate. Its practical value is small: it mostly duplicates legs that already have `clv_sharp`.

## 75. Positive CLV at max-of-books odds is line shopping until a market-only control says otherwise (#089, 2026-09-24)

Any strategy that takes the BEST price across many books beats a single sharp close on average, whatever
picked the bet. In the Wheatcroft replication, shots+corners bets at max odds showed +0.46% CLV against
the Pinnacle close, which looked like a residual edge. But goals (a rating with no skill) scored +0.71%,
and a **market-only** logistic betting the same way scored **+0.80%**, more than the model. **Rule:**
before attributing max-odds (or best-of-books) CLV to a model, run the same betting rule with the model
term removed; the model owns only the difference. At a single book's own price (Pinnacle pre-close) the
same model was −1.60%.

## 76. `match_stats` `_ht` columns held FULL-MATCH values for ~2026-08-31..09-24 — repaired, but results computed then are suspect (#111, 2026-09-24)

`parse_fixture_stats_halftime` fell back to the full-match block when a response had no half split,
which the batch `fixtures?ids=` response never has. Up to 650 of 652 rows a week had half-time
shots/corners equal to full time. Repaired on 2026-09-24 (1,002 rows re-fetched with the real split),
so queries run AFTER that date are clean. Any 1H corners / 1H cards / `_ht`-based result computed
between ~08-31 and 09-24 (`lineshop_new_markets.py`, `own_market_expansion_sweep.py`) must be
re-run before it is cited. **Check:** `count(*) FILTER (WHERE shots_home_ht = shots_home AND
shots_away_ht = shots_away AND corners_home_ht = corners_home)` per week should be ~0-1.

Separately: **xG on rows < 6 days old is incomplete by design** — AF publishes it 1-4 days after the
match and `job_xg_late_fill` fills it in. Do not read a recent xG gap as a coverage drop.

## 77. Historical Pinnacle rows are football-data ingests: `is_closing` is stamped AT kickoff, `is_opening` is NOT an opening price (#118, 2026-09-24)

Before live Pinnacle collection (~July 2026 for most leagues), our Pinnacle O/U rows came from
`scripts/ingest_football_data_csvs.py`. Per match there are typically two pairs:
* **`is_closing`** = FD's `PC>2.5`, **timestamped exactly at kickoff**. Any `o.timestamp < m.date`
  filter silently drops every historical close. #118's first run found only 453 "fresh closes" instead
  of ~4,000 because of this.
* **`is_opening`** = FD's `P>2.5`, which football-data collects on a Friday or Tuesday before the match.
  It is stamped kickoff − 7 days as a stand-in. It is a pre-close price, NOT the market's opening.

**Rule:** to get a historical close, select `is_closing` explicitly. Never treat `is_opening` in history
as an opening price or read its timestamp as real. A median "164 h before kickoff" on these rows is the
stand-in stamp, not a fact about when the price existed.

## 78. The Betfair-Exchange close is as sharp as Pinnacle's — and at the CLOSE a sharp trigger barely fires, so anchor choice cannot be settled on ROI (#119 step C, 2026-09-24)

`scripts/sharp_anchor_exchange_replay.py` (read-only, pre-registered in its docstring). Data: the
football-data CSV closes, **7,328 matches (1X2) / 7,299 (O/U 2.5), 13 leagues** (EPL, Championship,
League One/Two, La Liga, Segunda, Serie A, Bundesliga, Ligue 1, Eredivisie, Primeira, Süper Lig,
Jupiler), seasons 2024-25 + 2025-26 (to 2026-05). That is the whole overlap — the ~118k exchange rows
are 9.8k matches, and Pinnacle's CSV close exists on 7.3k of them.

**Read every book at the exchange row's own timestamp.** Each CSV row stamps all books identically;
Bet365 / William Hill / 1xBet / Pinnacle ALSO carry AF-live `is_closing` rows at other timestamps.
Joining on `o.timestamp = <exchange close ts>` keeps one instant and drops the AF rows.

**De-vig.** CSV exchange odds are BACK prices before commission: overround median **1.0065**
(p5 1.002, p95 1.013; 0.1% cross ≤1.000), against Pinnacle's **1.034**. Both through the same Shin
`devig()`; at ~100.7% Shin ≈ proportional, and crossed books fall back to proportional.

**Sharpness (paired outcome log-loss, lower = sharper).**

| market | PIN | EXC | BLEND | EXC−PIN mean / median / winsor, t | BLEND−PIN t |
|---|---|---|---|---|---|
| 1X2 | 0.98584 | 0.98618 | 0.98587 | +0.00034 / −0.00045 / −0.00002, t=+0.83 | +0.12 |
| O/U 2.5 | 0.67384 | 0.67432 | 0.67396 | +0.00048 / +0.00042 / +0.00012, t=+1.27 | +0.73 |

Same sign in both seasons, no season significant. The two anchors differ by >2 pp on some leg in
only 7.5% (1X2) / 6.5% (O/U) of matches (median max-leg gap 0.7 / 0.6 pp), and **on those
disagreement matches neither is right more often** (EXC−PIN LL t=+1.39 / +0.83). Blending gains
nothing measurable — a blend of two near-identical forecasts can only average their noise.

**Replay at the CLOSE** — live sharp-trigger rule (3% ≤ p_anchor − 1/price ≤ 8%, §9 guard), best CSV
close among soft books (1X2: Bet365, BetWin, Betfred, WH, 1xBet; O/U: Bet365 only), flat 1u:

| market | PIN | EXC | BLEND | AGREE (≤2 pp) |
|---|---|---|---|---|
| 1X2 n / ROI ± SE | 91 / +8.6% ± 13.1 | 44 / +20.0% ± 18.2 | 46 / +18.5% ± 18.2 | 36 / +16.6% ± 20.1 |
| O/U n / ROI ± SE | 47 / +3.0% ± 17.0 | 7 / +10.3% ± 39.0 | 8 / −2.1% ± 37.4 | 3 / −36% ± 64 |

Six pre-registered tests (3 variants × 2 markets, per-match profit difference vs PIN): all **Holm
p = 1.000** (raw p 0.75–0.94). Context: every selection at the best soft close returns −5.4% / −5.2%.
The published forward-test rule (p·price − 1 ≥ 3%, odds ≤ 4.0) fires more (1X2: PIN 495 / +11.0% ± 6.5,
EXC 318 / +12.7%, BLEND 312 / +18.3%, AGREE 270 / +19.8% ± 9.2) — descriptive only, same verdict.

**What this does and does not say.**
* The exchange close is a **peer** of Pinnacle's, not a better or worse anchor — extends the old
  "identical to 4 dp" note (DATA_SOURCES, CSV-FULL-EXTRACT) with t-stats, O/U, the blend and seasons.
* The "agree" filter cuts bets ~40-60% and the point ROI rises on 1X2 — but nothing is significant.
  **Do not read it as evidence that disagreement flags bad bets.** On O/U, 45 of PIN's 47 bets were
  on matches where the exchange disagreed >2 pp, i.e. at the close Pinnacle-only "edges" on Bet365 O/U
  are mostly Pinnacle-vs-exchange disagreement; they returned +7.6% ± 17.5 — uninformative.
* **CLOSE-to-close is the wrong instant for the live bots**, which decide hours earlier when soft
  prices are staler and the anchor noisier. At the close soft books sit on the sharp line, which is
  why the trigger fires on ~1% of matches. The stale-quote value of a second anchor (#119 B) can only
  be measured on pre-close quotes — i.e. the live `exchange_quotes` history from 2026-09-24 on.

## 78. Never de-vig with the proportional method — it is the worst-calibrated method, and Shin still UNDER-states favourites (#106, 2026-09-24)

On every finished match with a Pinnacle price (1x2 n = 26-35k), proportional de-vig loses to Shin on
log-loss in every market. On 1x2 it is worse in every bootstrap draw. By outcome band on the 1x2 close:
* favourites at a Shin probability of 0.60-0.72 won **67.1%** (Shin 65.2%, proportional 63.7%);
* longshots at 0.00-0.20 won **13.4%** (Shin 14.2%, proportional 15.1%).

**Rule:** use `workers.model.devig.devig` (Shin) for anything published. Proportional manufactures edge on
longshots and hides it on favourites. A "worst-method" robustness check must span only methods that are
not measurably worse than Shin (Shin, additive, power), or it rejects picks on a formula known to be
wrong. On a soft book's 8-10% margin the methods differ by a median 2.1pp of edge, against 0.6pp on
Pinnacle — so the consensus arm is where the choice bites.

## 79. Historical odds from our own scrapers contain other matches' boards (2026-09-24)

Before 2026-09-24 the Epicbet / Unibet-Site matcher paired events up to ±6 h from our
kickoff, allowed both of our teams to match the same side of a book event, and let one book
event pair with several fixtures. Result: whole boards of ANOTHER match stored under our
fixture (reviewed true positives: "Narva v Levadia" = Goias v Avai, another = Sevilla v
Barcelona, …) — every market, not just 1X2. `mirror_guard` only ever stripped 1X2. Since
#120: write-time `board_guard`, the 30-min `board_audit` read-back (moves offenders to
`odds_snapshots_quarantined`, reason prefix `board-audit` / `board-guard`), and the matcher
fixed at source. **Any analysis of Epicbet / Unibet-Site odds before 2026-09-24 must
exclude (fixture, book) pairs where the book's board is off a ≥4-book median in ≥2 markets,
or join `data_quality_findings`** — they read as the largest edges on the board.

## 80. Pinnacle `over_under_05` is first-half contaminated, and "over 0.5 is easy" is not an edge (#128, 2026-09-24)

* **Never read Pinnacle `over_under_05`.** All of it sits in 2026-04-27 → 05-10, before the strict
  "Goals Over/Under" match in `api_football.py`; in about half the (fixture) pairs the over-0.5 price is
  **at or above** the over-1.5 price, which is impossible for full-time goals. It reads as over 0.5
  winning 87.6% at an average 1.40 — a fake +20% edge. No AF book carries a clean FT O/U 0.5 after
  mid-May; clean 0.5 exists only from our scrapers (Coolbet ~Aug, Epicbet from 08-27).
* **Guard for any O/U ladder analysis:** over-odds must strictly rise with the line (0.5 < 1.5 < 2.5 <
  3.5) per (fixture, book); drop the pair if not. It removed 709 pairs in #128, and ~11% of Coolbet's
  0.5 rows before 2026-09-18.
* **The favourite–longshot shape in totals, measured:** at every line the short side loses least and
  the long side absorbs the margin (under 0.5 −39% to −42%, over 0.5 ≈ −0.5% to +1%). Over 0.5 is the
  line closest to fair and **still not profitable** — at 1.07 break-even is 93.5% against a ~92% hit
  rate. "Easy to predict" is priced in.
* **A pricing lapse is not a persistent edge.** Coolbet's over 0.5 barely tracked the match until
  2026-08-23 (corr 0.39 with a Pinnacle-derived fair price) and fully from 08-24 (0.90). Any backtest
  spanning that date will show a Coolbet over-0.5 "edge" that no longer exists. Split by period
  before believing it. Full result: `dev/active/ou-low-lines-bias-sweep.md`.
* **AF live `live_match_snapshots.live_ou_05_*` is a FIRST-HALF price, not full-time.** At minute
  5–10 / 0-0 the average over-0.5 is 1.41 and its implied 0.674 matches the HALF-TIME goal rate (0.671),
  not full-time (0.908). Settled on FT goals it reads +27% ROI at t = 9.6. The ladder check
  (over 0.5 < over 1.5 < over 2.5) catches only 27 of 207 such rows, so **do not use `live_ou_05_*` at
  all**; on `live_ou_25_*` about 1% of rows in the first 15 minutes look 1H-shaped (drop rows with a
  ladder violation), none after minute 45. The AF live parser accepts three generic market names
  (`api_football.py` "Goals Over/Under", "Over/Under", "Over/Under Line") and one of them evidently
  carries a 1H ladder for some bookmaker. The feed is retired (2026-08-21) but its history is still read
  by in-play analyses — including the retired in-play bots' "over 2.5 early +15.98%" postmortem, which is
  worth a spot-check before it is ever used to revive in-play.

## 81. Tonybet O/U labels now include whole and quarter lines (#130, 2026-09-24)

From 2026-09-24 Tonybet writes EVERY goals line, not only .5: `over_under_20` (2.0), `over_under_225`
(2.25), `over_under_075` (0.75), `over_under_55` (5.5), and the same for `over_under_1h_*` / `_2h_*`.
* **Never assume `over_under_%` means a .5 line.** Read the line from `handicap_line` or
  `workers/utils/odds_quality.ou_line_from_label`; grading a whole line strictly turns a push into a loss,
  and a quarter line is half-win/half-loss. Settlement already refuses 3-digit labels (`_UNSETTLEABLE`).
* Before 2026-09-24 no book stored non-.5 FT O/U lines in `odds_snapshots`, so a query over all books
  that suddenly finds `over_under_20` rows is seeing Tonybet only.
* **Tonybet in-play boards** (`inplay_book_quotes` book='Tonybet'): sides and scores are in the BOOK's
  orientation (home_team/away_team hold Tonybet's names); `minute` is NULL on most rows because Tonybet
  blanks its clock — join AF's minute on match_id + captured_at. Unchanged boards are not re-written, so
  the series is event-driven, not a fixed 120 s grid.


## 82. A missing-value presence flag is a coverage intercept, not the feature's signal (#141 round 3c, 2026-09-24)
`workers/model/combined_1x2.design()` turns each optional column into `(value.fillna(0), value.notna())`. The
0/1 flag lets the logit shift every row where the column is MISSING — and missingness is itself informative
(no API-Football lineups ≈ lower-coverage leagues). In round 3c's first selection run the "previous XI" arm
gained −0.0031 log-loss, and the whole gain sat on rows with NO XI data at all (−0.0071 on 5,180 such rows).
* **Always split a new feature's gain by "feature present / absent".** A gain on rows where the feature is
  absent is the presence flag (or a refit shift), never the feature.
* If the pre-registration says rows without the feature keep the old prediction, enforce it in code
  (`scripts/ab_1x2_lineups.py` now does; smoke `LINEUPS-3C-NO-XI-FALLBACK`). With the fix, round 3c
  failed on confirm — the "win" had been the artefact.

## 83. Before mid-July 2026 there is NO Pinnacle O/U closing price — "CLV vs Pinnacle close" there is circular (#149, 2026-09-24)
Measured on O/U 1.5/2.5/3.5 (`scripts/backtest_ou_comb_bots.py --o2`): the share of matches whose Pinnacle O/U history is a
SINGLE row (open = "close") is **95% in May, 88% in June, 31% in July, 6% in August, 1% in September**, and the median
"close" sits **8.5–10 h before kickoff** in May–June against ~5 min from August. Pinnacle is still well calibrated overall
every month — but a rule that SELECTS rows where a book beats Pinnacle's early price selects exactly the matches where
that early price was wrong and later moved, which a missing close cannot show. The tell: the sharp-anchored O/U arm read
**CLV +8.6% and ROI −10.2% [−15.6, −4.6]** on 1,585 May–mid-July picks (Pinnacle "close" 65.6% vs a 55.7% hit rate on the
short-odds AF picks); from mid-July, with real closes, the same arm reads CLV +2.8% / ROI +0.9%.
* **Never report a pre-mid-July CLV vs Pinnacle close** without first checking the close is a separate, late row
  (`ts_close` ≥ `ts_open` + something, and near kickoff). The 7-day retention (open + latest only) makes this worse for any
  older window. Check the same for 1X2 before trusting an old 1X2 CLV.
* **When CLV and a large-n ROI disagree by several standard errors, believe the ROI** and look for the broken anchor.
