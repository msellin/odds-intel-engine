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
`pick_time` in 283 to match how both admin pages dedupe). A second view,
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
| `coverage_expansion_probe.py` | would the best model work in leagues it does not bet |
| `anchor_comparison_backtest.py` | Pinnacle vs consensus anchors |
| `bookmaker_sharpness_rank.py` | per-book calibration, paired |
| `book_bias_probe.py` | per-book directional bias by probability band |
| `favourite_band_probe.py` | does the favourite-longshot bias beat the vig |
| `discretion_bleed_report.py` | placed vs untouched picks, day-clustered |
| `ou_line_integrity_audit.py` | is a book's OU quote priced for its stated line |
| `model_version_clv_scoreboard.py` | paired model-version comparison |
