# Task: add an Epicbet price column to the /admin/shadow-bots INDEX page

## What to build

The per-bot **detail** page (`odds-intel-web/src/app/(app)/admin/shadow-bots/[bot]/page.tsx`)
shows three live book prices per pending pick — `Now CB` (Coolbet), `Now UB`
(Unibet-Site), `Now EB` (Epicbet). The **index** page
(`odds-intel-web/src/app/(app)/admin/shadow-bots/page.tsx`) has the same
"Upcoming picks" table but still shows only CB and UB.

Add the Epicbet column there, matching the detail page.

Reference implementation — read these commits first, they contain the reasoning:
- `odds-intel-web` **1c99d6b** — added `Now EB` to the detail page
- `odds-intel-web` **4ed5f56** — why the UB column reads `Unibet-Site`, not Kambi
- `odds-intel-web` **(latest)** — the row-ceiling fix described below

## Why Epicbet is worth a column

Measured 2026-09-11 (`scripts/book_dimension_sweep.py`, n=17,891 series over 7 days,
paired t-tests, outlier-guarded): Coolbet and Epicbet each hold the best price on
~55% of series, and the split is **by market** — Coolbet better on 1x2 (t=3.6),
double-chance (t=5.6) and BTTS (+0.70%, t=8.8); Epicbet better on Asian handicap
(+0.83%, t=12.3), O/U 2.5 (t=5.8), O/U 1.5 (t=9.2) and corners (t=5.5). On the O/U
mirror's own gate Epicbet holds the best price on 65% of 1,429 series (t=+8.5).
Epicbet also quotes 672 upcoming fixtures to Coolbet's 534. The operator places by
hand off these screens, so a third column is real money, not decoration.

## ⚠️ THE TRAP — read this before writing the query

**A naive `.in("bookmaker", [..., "Epicbet"])` will silently break the two columns
that already work.**

PostgREST caps responses at `db-max-rows = 10,000` (see `ALL-BETS-CEILING-DEAD`).
The existing query orders **newest-first**, so when a response exceeds the cap the
rows dropped are the **oldest** — which are Coolbet's and Unibet's, because their
sweeps are far smaller than Epicbet's. The failure mode is a blank `Now CB` column,
with no error anywhere.

Measured 2026-09-11 on the index page's own filters (upcoming 48h, non-live, 12h
window):

| book | rows |
|---|---|
| Epicbet | 308,687 |
| Coolbet | 46,810 |
| Unibet-Site | 12,095 |
| **total** | **367,592** — 37x the cap |

Epicbet quotes 110+ markets per fixture, which is where the volume comes from.

**The same trap already bit the detail page.** Adding Epicbet there took
`bot_1h_1x2_paper_shadow_v1`'s fetch to 33,988 rows (3.4x the cap); eight bots were
over it. The fix was to constrain the query to the markets actually displayed:

```ts
.in("market", pendingMarkets)   // distinct lowercased markets of the shown picks
```

That took the worst case to 2,482 rows. **Do the same here** — the index table
renders a known set of picks, so derive the distinct `(market)` values from the
upcoming rows and filter on them. Verify with a direct DB count before and after;
do not assume it fits.

## Constraints that already apply on this page — keep them

1. **`Unibet-Site` only.** Never `Unibet` or `Unibet-Kambi` — unibet.ee left the
   Kambi API on 2026-09-06 and Kambi disagrees with the site on 91% of quotes,
   reading *higher* on 29%. Smoke `UB-COLUMN-NOT-PLACEABLE` enforces this and will
   fail the build if a non-placeable feed reappears in a bookmaker allowlist.
2. **Lowercase both sides of the key.** `shadow_bets` stores `1x2` AND `1X2`;
   `odds_snapshots` stores only `1x2`. The case split is load-bearing — do NOT
   normalise it in the DB (ANALYSIS_GOTCHAS §23). Use the page's existing
   `oddsKey()` builder; do not write a second one.
3. **Newest-wins per key** — rows arrive newest-first, so the first row per key wins.
4. **12h recency window** stays. A price older than that should render `—`, not
   mislead.
5. **Header and row grid templates must stay identical, gap included.** The detail
   page's header was missing the rows' `sm:gap-3`, so every label sat right of its
   own data. Smoke `SHADOW-DETAIL-THREE-BOOKS-AND-BET-MADE` pins this on the detail
   page; the index page already has the gap — don't lose it when adding a column.

## Worth considering (your call, say which you chose and why)

A stale quote is worse than no quote. On the detail page a book's price can be hours
behind its peers on the *same fixture* while the feed as a whole is healthy —
Unibet was 19h and 34h behind on two of nine picks on 2026-09-11, which was enough
to make it look like the best-priced book when it was not (ANALYSIS_GOTCHAS §34).
Consider showing the per-quote age, or dimming a quote more than ~6h behind the
freshest peer on that fixture. Neither page does this yet.

## Definition of done (project protocol — `CLAUDE.md`)

- [ ] Mark the task `🔄 In Progress` in `PRIORITY_QUEUE.md` **before** writing code
- [ ] A smoke test in `odds-intel-engine/scripts/smoke_test.py`. Run only yours:
      `python3 scripts/smoke_test.py -f YOUR-TEST` — never the full suite locally,
      CI is the gate. Assert the row-ceiling guard (the market filter) is present,
      not just that "Epicbet" appears in the file (§41: a file-wide substring
      assertion is not a test of the thing you changed).
- [ ] Verify the test FAILS without your change, not just that it passes with it
- [ ] `npx tsc --noEmit` clean in `odds-intel-web`
- [ ] Docs updated in the SAME commit (`PRIORITY_QUEUE.md` at minimum)
- [ ] Push to `main` in both repos — deploys are automatic
- [ ] **Check CI after pushing.** Smoke tests run on every push and a red suite is
      not "done". `gh run list --workflow="Smoke Tests" --limit 3`

## Note on the repo

Another agent may be working in `odds-intel-engine` at the same time. **Do not use
`git add -A`** — stage the specific files you changed (ANALYSIS_GOTCHAS §46: a
blanket add sweeps another session's in-progress work into your commit; it happened
on 2026-09-11 and misattributed ~400 lines plus a migration).
