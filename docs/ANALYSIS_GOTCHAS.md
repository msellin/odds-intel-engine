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

**Why this was nearly missed:** shadow rows are written with
**`source='xgboost'`, not `source='ensemble'`**. A check for version overlap
filtered on `source='ensemble'` returns **zero**, which looks exactly like "the
infrastructure does not exist". It does. Query `source='xgboost'`.

## 2. `predictions.source` vocabulary

| source | what it is |
|---|---|
| `ensemble` | the blended production probability the bots actually bet on |
| `xgboost` | raw XGB component — **and where shadow/candidate versions land** |
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
`pick_time` in 283 to match how both admin pages dedupe). Measured drift between
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

**Always apply the production guard** (`soft <= Pinnacle x 1.30` for OU, `x 1.35`
for 1X2). With it, the same search returned 0 of 82 positive slices instead of 14.

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

## 13. Not every book prices every market — Epicbet has no `double_chance`

`odds_snapshots` gained **Epicbet** on 2026-08-27 (EPICBET-ODDS-INGEST), the
second EMTA-licensed book the operator can actually place at. It does **not**
write `double_chance` rows: the bulk league listing we ingest from only carries
market groups 45 (1X2), 15 (Match Total Goals), 69 (Both to Score) and 19 (Goals
Handicap). Coolbet writes DC, Epicbet does not.

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

## Re-runnable analysis scripts (all committed 2026-08-26)

| script | answers |
|---|---|
| `clv_gate_report.py` | rank every bot by CLV; flags CLV/ROI disagreements |
| `clv_variant_backtest.py` | which CLV definition actually predicts ROI |
| `devig_calibration_backtest.py` | Shin vs proportional, on calibration |
| `promotion_gate_simulation.py` | Monte-Carlo of the graduation gate |
| `lineshop_replay.py` | point-in-time replay of any bot config |
| `clv_slice_search.py` | search market x tier x odds for a positive-CLV slice |
| `coverage_expansion_probe.py` | would the best model work in leagues it does not bet |
| `anchor_comparison_backtest.py` | Pinnacle vs consensus anchors |
| `bookmaker_sharpness_rank.py` | per-book calibration, paired |
| `book_bias_probe.py` | per-book directional bias by probability band |
| `favourite_band_probe.py` | does the favourite-longshot bias beat the vig |
| `discretion_bleed_report.py` | placed vs untouched picks, day-clustered |
| `ou_line_integrity_audit.py` | is a book's OU quote priced for its stated line |
| `model_version_clv_scoreboard.py` | paired model-version comparison |
