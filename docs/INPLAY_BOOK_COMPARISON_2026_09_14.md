# In-play price comparison — Unibet vs Coolbet vs Epicbet

**Status: RESEARCH ONLY. Nothing was written to the database.** Pre-analysis
material for an in-play strategy decision. Measured live on 2026-09-14, roughly
19:10–19:35 UTC, against matches that were actually in play.

## What was measured, and how

12 live fixtures spanning four league tiers, **86 synchronized snapshots**. For
each fixture the three books were read within ~5–6 seconds of each other, so
every comparison below is of three prices on the same game state — not three
prices minutes apart. Three separate collection runs were made; the headline
numbers agree across all three, which is the main reason to trust them at this
sample size.

Fixtures: Leeds–Newcastle, Villarreal–Betis, Inter–Udinese (top-5); Rio
Ave–Estrela, FCSB–Petrolul, Red Star–Metz, Celta B–Eibar, Jong PSV–Jong AZ (2nd
tier); Ludogorets–Septemvri, Maccabi–Hapoel TA, Waterford–Derry, West Ham
U21–Liverpool U21, Defensores–Madryn (minor).

### Transport — all three are reachable live today, with no new infrastructure

| Book | Live transport | Notes |
|---|---|---|
| **Epicbet** | `match.getFoByLeague` with `period:"live"`, then **`activeOdds.getLiveBetByMarketIds`** | Anonymous REST, direct from the Mac. Prices come back tagged `product: live_bet`. The prematch sibling `getPreMatchByMarketIds` returns stale prematch prices on a live match — using it would have been a silent error. |
| **Coolbet** | Existing session, sidebets with `matchStatus=LIVE` | Already supported in `coolbet_explorer.fetch_match_markets(live=True)` — but see the truncation bug below. |
| **Unibet-Site** | Injected fetch of `views/contest-page` on the operator's established tab | Returns `status: InPlay`, live prices with per-option timestamps, **and a live scoreboard** (score, match clock, phase, cards). Read-only; the operator's tab is never navigated. |

**Caveat on Unibet depth:** the contest-page returns 15 propositions while
reporting `propositionCount: 77`. Every endpoint variant probed for the other 62
returned 404. So Unibet's coverage figures below are a floor — a *transport*
limit we could not clear, not a proven book limit. Its O/U and handicap ladders
*are* complete within the propositions returned, so the line-vocabulary findings
do hold.

---

## 1. The thing that decides everything: there is no in-play sharp anchor

> **AMENDED 2026-09-14, same day — read with the "our stored Pinnacle is not
> Pinnacle" briefing.** Two corrections to this section, pulling opposite ways:
>
> 1. **This section is too absolute.** It concludes no live sharp anchor is
>    reachable. That is proven only for the *AF* feed. Pinnacle's own guest API
>    (`guest.api.arcadia.pinnacle.com`) is reachable from the operator's Mac —
>    Cloudflare blocks the VPS, and Estonian ISP DNS sinkholes the domain, but
>    neither is a real restriction. **Whether it serves in-play matchups is
>    UNTESTED, and it is the single question that decides whether in-play is
>    viable at all.** Test that before anything else here is acted on.
> 2. **The pre-match contrast below is too generous.** This section leans on
>    in-play lacking a sharp anchor that pre-match has. On lower-tier leagues
>    pre-match does not reliably have one either: AF-fed Pinnacle runs 9.18%
>    median 1X2 overround against ~6.16% real, the degradation concentrated in
>    exactly the lower tiers — which is most of the sample in this document.
>
> **No measurement in the rest of this file is affected.** Every number here is
> Coolbet vs Epicbet vs Unibet measured directly against each other; Pinnacle is
> not an input to any of them.
>
> 3. **"We have no in-play odds" was scoped to `odds_snapshots` and reads as a
>    general claim — it is not one.** `live_match_snapshots` holds **2.2M rows
>    across 35,493 matches and is still being written**, at 45s cadence. Its
>    `minute` and `score` are fully populated and trustworthy; its *prices* are
>    only 7.5% filled and come from the same stale AF feed. So the hazard flagged
>    for the 155k archive is really a live, growing 2.2M-row table — and the
>    state skeleton in it is genuinely valuable.
> 4. **AF's in-play feed is now measured stale**, not merely suspected: median
>    40s, p90 54s, max 662s, a ~34s server-side cache sawtooth, and 2 of 27
>    fixtures never refreshing at all.


`odds_snapshots` holds **zero** live rows for any of these three books. The only
in-play odds this system has ever stored are in `odds_snapshots_inplay_archive`:
155,048 rows, 2026-05-07 → 2026-08-21, **all from a single bookmaker string
`api-football-live`**.

That feed is not a bookmaker. `parse_live_odds` in
`workers/api_clients/api_football.py:1599` hardcodes `"bookmaker":
"api-football-live"` — AF's `/odds/live` returns one unattributed price per
market with no book identity. **There is no path to a live Pinnacle price
through AF.**

Per `docs/MARKET_DATA_MAP.md`, a market needs a sharp anchor before an edge
number means anything — it is why BTTS is dead. In-play fails that test for
*every* market: we cannot compute an honest in-play edge or CLV today, for any
selection, at any book. Everything below is about **price quality**, which is a
different and lesser thing than edge.

---

## 2. Margin — who charges what

Overround, tail-trimmed to markets where every outcome is priced ≤ 15.0. The
trim matters: on a dead longshot Coolbet clamps the hopeless side at 50.0 where
Epicbet writes 300.0, and untrimmed that single convention swings the raw margin
by several points without changing any price a bettor would take.

| market | Unibet | Epicbet | Coolbet |
|---|---|---|---|
| 1x2 | 7.79% (n=44) | 6.41% (n=43) | **4.96%** (n=43) |
| over/under | 6.84% (n=240) | 6.44% (n=369) | **5.16%** (n=294) |
| draw-no-bet | 7.05% (n=52) | 6.70% (n=51) | **4.78%** (n=46) |
| asian hcp (2-way) | *not offered* | **5.56%** (n=267) | 6.09% (n=114) |
| handicap (3-way) | 10.18% (n=129) | **7.22%** (n=52) | 9.74% (n=122) |
| both teams to score | 7.58% (n=39) | **6.94%** (n=38) | 7.14% (n=37) |
| double chance | 13.39% (n=53) | 10.26% (n=42) | **9.91%** (n=40) |

**Coolbet is the tightest book on the core markets** (1x2, O/U, DNB). **Epicbet
owns the derivative markets** (Asian handicap, 3-way handicap, BTTS). **Unibet is
last or near-last on every single market.**

### Every book widens as the league gets smaller

| tier | Unibet | Epicbet | Coolbet |
|---|---|---|---|
| top-5 | 6.67% | **5.05%** | **5.05%** |
| 2nd tier | 7.52% | **6.91%** | 7.03% |
| minor | 8.43% | **7.23%** | 8.18% |

---

## 3. Best price — head-to-head, takeable odds only (1.10–10.0, all three quoting)

| market | n | Unibet | Epicbet | Coolbet |
|---|---|---|---|---|
| 1x2 | 130 | best 34.6%, −2.77% | best 63.8%, −1.31% | **best 76.2%, −0.68%** |
| over/under | 325 | best 46.8%, −1.68% | **best 59.4%, −0.94%** | best 55.4%, −1.10% |
| handicap (3-way) | 136 | best 16.2%, −3.58% | **best 72.8%, −1.17%** | best 59.6%, −1.59% |
| both teams to score | 70 | best 21.4%, −2.14% | **best 67.1%, −0.57%** | best 55.7%, −0.68% |
| draw-no-bet | 70 | best 10.0%, −3.55% | best 42.9%, −1.40% | **best 75.7%, −0.36%** |
| double chance | 100 | best 14.0%, −2.74% | **best 78.0%, −0.62%** | best 49.0%, −1.29% |

("best X%" = share of markets where this book held the top price, ties counted;
"−Y%" = average shortfall against the best available price.)

### The sharpest pattern in the whole dataset

Best-price share, all markets pooled, by league tier:

| tier | n | Unibet | Epicbet | Coolbet |
|---|---|---|---|---|
| top-5 | 385 | **20.8%** | 69.1% | 64.7% |
| 2nd tier | 251 | 36.3% | 52.6% | 59.4% |
| minor | 195 | **43.1%** | 67.7% | 52.8% |

**Unibet's weakness is concentrated exactly where the liquidity is.** It holds
the best price on one market in five in the top-5 leagues, and more than twice
as often in minor leagues. Whatever Unibet is doing in-play, it is not competing
on the big games.

---

## 4. Coverage — the line vocabularies differ structurally

| | Unibet | Epicbet | Coolbet |
|---|---|---|---|
| O/U lines quoted (distinct, pooled) | 9 | **18** | 17 |
| O/U median lines per fixture | 4.0 | **5.0** | 3.0 |
| O/U whole-goal lines (2.0, 3.0 …) | **none** | 9 | 8 |
| 2-way Asian handicap | **not offered** | 11 lines, half **and** whole | 6 lines, whole-goal only |
| 3-way handicap | 5 lines, whole only | 4 lines, whole only | 6 lines, whole only |
| quarter lines (0.75, 1.25 …) | none | none | none |

Three things follow:

1. **Unibet quotes half-goal totals only** — 0.5, 1.5, 2.5 … 8.5, and no whole
   lines at all. Whenever the in-play total sits on a whole number, Unibet has
   no push line to offer and Epicbet/Coolbet do.
2. **Unibet offers no 2-way Asian handicap in-play** in anything we can reach —
   only the 3-way handicap, which is also its worst-margin market (10.18%).
3. **Nobody quotes quarter lines in-play.** `project_coolbet_limitations`
   records this for Coolbet pre-match; in-play it is true of all three, so the
   `AH-NO-QUARTER` filter is not a Coolbet-specific concession here.

---

## 5. Does in-play line shopping actually pay?

Best-vs-worst gap, all three books quoting the identical market/line/side:

| market | n | median gap | p90 | ≥2% | ≥5% |
|---|---|---|---|---|---|
| 1x2 | 130 | 2.27% | 10.00% | 62.3% | 19.2% |
| over/under | 325 | 1.89% | 6.25% | 47.7% | 15.1% |
| handicap (3-way) | 136 | 3.95% | 10.00% | 78.7% | 36.0% |
| both teams to score | 70 | 2.26% | 4.88% | 58.6% | 7.1% |
| draw-no-bet | 70 | 2.27% | 16.67% | 60.0% | 17.1% |

| tier | n | median gap | ≥2% | ≥5% |
|---|---|---|---|---|
| top-5 | 385 | 2.86% | 67.0% | 28.1% |
| 2nd tier | 251 | 2.27% | 57.0% | 13.1% |
| minor | 195 | 1.82% | 49.2% | 10.8% |

**Cost of book loyalty**, averaged over 831 matched markets:

- loyal to **Unibet** → you give up **2.64%** of price (median 1.82%)
- loyal to **Epicbet** → 1.06% (median 0.00%)
- loyal to **Coolbet** → 1.09% (median 0.00%)

Epicbet and Coolbet each hold the best price often enough that their *median*
shortfall is zero. Unibet's is not.

### Where the gaps live: the long side

The largest observed three-book gaps, all on markets every book was quoting:

| gap | fixture | market | prices |
|---|---|---|---|
| 33.3% | Maccabi–Hapoel TA 2-1 | 1x2 draw | Epicbet 10.0 / Coolbet 9.0 / **Unibet 7.5** |
| 18.2% | FCSB–Petrolul 2-1 | 1x2 draw | Epicbet 6.5 / Coolbet 6.0 / **Unibet 5.5** |
| 17.6% | Leeds–Newcastle 0-0 | hcp +1 away | Epicbet 10.0 / Unibet 9.0 / Coolbet 8.5 |
| 17.6% | Inter–Udinese 2-2 | DNB away | Epicbet 10.0 / Coolbet 10.0 / **Unibet 8.5** |
| 16.4% | Inter–Udinese 2-2 | over 7.5 | Coolbet 6.4 / Unibet 6.0 / Epicbet 5.5 |

The favourite side is priced near-identically everywhere. **All the dispersion
is on the long side** — the draw, the away DNB, the far totals — and Unibet is
the short book in almost every large gap.

---

## 6. Two defects found in our own stack

### (a) Coolbet's live board is truncated 4.7× by our own request

`coolbet_explorer.fetch_match_markets(live=True)` pins sidebets to `limit=13`,
with a comment asserting "the live page genuinely offers fewer groups". That is
empirically false. Measured on three fixtures while in play:

| | limit=13 | limit=60 | limit=300 |
|---|---|---|---|
| Inter–Udinese | 7 groups / 25 markets | 32 / 61 | **59 / 107** |
| Villarreal–Betis | 8 / 27 | 41 / 76 | **71 / 126** |
| Leeds–Newcastle | 8 / 27 | 40 / 75 | **71 / 126** |

This is the same truncation class as `COOLBET-CORNERS-NOT-FLOWING-2026-09-05`,
which was fixed for the pre-match path and left in place in-play on the strength
of that incorrect comment. Every Coolbet number in this document was collected
at `limit=300` for that reason — comparing Coolbet's depth to Epicbet's at
limit=13 would have measured our request, not the book.

### (b) Parallel Coolbet readers silently return another match's prices

Running more than one Coolbet reader through the shared FlareSolverr session
`coolbet_prod` returns a **different match's markets under our matchId**. Caught
directly: Botafogo SP / Goiás EC outcomes appeared inside the Leeds–Newcastle
1x2. It is rare (1–4 snapshots per run) and completely silent — the prices are
plausible numbers in the right shape, and the fuzzy name-matching downstream
would have mapped them to home/away without complaint.

Confirmed as a concurrency effect: with a single reader, six consecutive fetches
returned clean Leeds/Newcastle outcomes every time. All affected Coolbet blocks
were quarantined out of this analysis (`_foreign` in the scratch analyzer), never
repaired.

**Any future in-play collector must be a single Coolbet reader, or use separate
named FS sessions per process.** This belongs in the reliability ledger.

---

## 7. What could NOT be measured — and what a real rig needs

**Reaction lag is unresolved, and the numbers I got are not usable.** Sequential
per-book fetches produced irregular 5–25 s cycles; the best-fit cross-correlation
lag flipped sign between fixtures (Unibet leading in one, trailing in another),
which is what noise looks like.

**Price-discovery leadership is likewise inconclusive.** The "does consensus move
toward this book's deviation" beta flipped sign for Unibet (+1.40 → −0.17) as n
went from 21 to 29. n≈30 is far too small; no conclusion is available.

Both questions are *the* questions for in-play, and answering them needs a
purpose-built rig:

1. **Concurrent, not sequential** fetches (a threaded version reached ~5 s cycles
   with true simultaneity — but it must not use a shared Coolbet FS session).
2. **Sub-second cadence** on one fixture, not ~10 s across twelve.
3. **An independent event feed** to timestamp goals/cards, since Unibet's
   scoreboard in the contest-page is cached and its clock was observed frozen for
   minutes at a time.

---

## 8. Reading for strategy

1. ~~**In-play edge is not measurable today.**~~ **RETRACTED the same night —
   see `docs/INPLAY_STRATEGY_CANDIDATES_2026_09_14.md`.** The premise was wrong:
   **an in-play bet settles on the final score, which we already have**, so
   realised ROI is ground truth and needs no sharp anchor. An anchor would only
   reduce variance (CLV is a lower-variance proxy, not a prerequisite). 22
   triggers have since been backtested on 35,442 matches. The Pinnacle guest API
   is still worth testing for in-play, but it is **not a blocker**. 🤖 OWN
2. **The three-book spread is real but modest**: ~1.9–4.0% median, ≥2% on about
   half to three-quarters of markets. That is a price-improvement programme, not
   a source of alpha.
3. **Unibet should be deprioritised in-play**, except on minor leagues where it
   is genuinely competitive (43.1% best-price share vs 20.8% on top-5). It also
   has the narrowest ladder and no 2-way Asian handicap.
4. **Coolbet for the core markets, Epicbet for the derivatives.** Coolbet is
   tightest on 1x2/O/U/DNB; Epicbet on AH, 3-way handicap, BTTS, DC, and it has
   by far the deepest line ladders.
5. **Fix the `limit=13` truncation before any in-play work.** We have been
   looking at a quarter of Coolbet's live board.

## Reproducing

Scratch scripts (not committed) live in the session scratchpad:
`collect.py` (three-book fetchers + fixture resolution), `round.py` (synchronized
collection loop), `analyze.py` / `analyze2.py` / `analyze3.py` (normalisation,
margin, best-price, dispersion), `vocab.py`, `gaps.py`, `lead.py`, `lag.py`.
Raw snapshots in `snaps.jsonl` (+ `snaps_v1`, `snaps_v2_cb13` from the earlier
runs, retained because they independently reproduce the headline numbers).
