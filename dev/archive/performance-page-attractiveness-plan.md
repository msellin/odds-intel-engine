# Performance page attractiveness — make /performance sell without lying

**Status**: planning · **Owner**: Margus / Claude · **Target**: ship changes that pass their data gate over 2026-06-02 → 06-07

## The thesis

`/performance` today is honest but flat. A new visitor sees a single all-time aggregate that mixes:

- v1-era bots that bled before we knew which signals worked
- Retired strategies whose tail bets still settle into the headline
- A pre-match cohort dragged by calibration miss (−4.5% ROI)
- An in-play cohort crushing it (+41% ROI)

The page averages these into one beige number and the visitor leaves.

**Goal**: every visible change must either (a) surface a real bright spot we're currently hiding, or (b) frame the data so the *trajectory* is legible. Nothing fabricated, nothing fake.

## The rule

Each proposed change goes through four gates:

1. **Claim** — what story this tells / why it sells
2. **Data check** — exact SQL or query, threshold that must hold
3. **Ship gate** — condition that must be true *in production data right now* to justify shipping. If data doesn't pass the gate, change is shelved.
4. **Implementation** — what to code, only after gate passes

If the data doesn't sell the story, we don't ship the visual that pretends it does.

## Changes (in validation order — cheapest signal first)

### Change 1 — Split hero into "Pre-match" + "In-play" tiles

- **Claim**: The single "System ROI" tile averages two very different products. Splitting them surfaces the in-play story we currently hide.
- **Data check**: query last-30d ROI split by `bots.name LIKE 'inplay_%'`. Need: in-play n ≥ 50, ROI ≥ +10%; pre-match ROI ≥ −5%. Both cohorts must have positive CLV (sharp picks).
- **Ship gate**: in-play ROI − pre-match ROI ≥ 10pp on last 30d AND in-play n ≥ 50.
- **Implementation**: extend `dashboard_cache` with `prematch_*` and `inplay_*` rollups (settlement.py); two new tiles in `performance-hero.tsx` replacing single "System ROI". Old combined number moves to a small "Combined" line below.
- **Selling angle**: lets visitor pick the cohort that matches their style. In-play tile becomes the hook even when pre-match is negative.

### Change 2 — Headline ROI uses 30-day rolling window, not all-time

- **Claim**: All-time includes the v1-era drag the v2 model has already fixed. 30-day rolling tracks the actual current product.
- **Data check**: compute ROI at last-7d / last-14d / last-30d / all-time. Pick the window where (ROI improved vs all-time by ≥ 3pp) AND (n is large enough that one cold week can't tank it, ≥ 300 settled).
- **Ship gate**: last-30d ROI ≥ all-time ROI + 3pp AND last-30d settled n ≥ 300.
- **Implementation**: settlement.py adds `rolling_30d_*` fields to dashboard_cache; hero swaps headline ROI source; the all-time number stays as the small "since launch" line under it.
- **Selling angle**: rolling means the page improves visibly as the model improves. New visitor sees "+ X% in last 30 days" instead of a stale all-time number.

### Change 3 — Equity curve sparkline next to headline ROI

- **Claim**: Single number ≠ trajectory. A sparkline shows the page is "going up and to the right" even if the absolute ROI number is modest.
- **Data check**: query daily cumulative P&L for last 30d. Need a visible upward trajectory after 2026-05-24 (v2 model deploy). If the curve is flat or descending, abort.
- **Ship gate**: cumulative P&L on 2026-06-01 − cumulative P&L on 2026-05-02 > 0 (must be net positive over the period we'd render).
- **Implementation**: add `daily_pnl_last_30d` JSON array to dashboard_cache; tiny SVG sparkline in `performance-hero.tsx` next to the headline ROI tile. ~60 lines including the renderer.
- **Selling angle**: visual trajectory > static number. Even a small positive curve says "system is working".

### Change 4 — Maturity badges on bot leaderboard

- **Claim**: New visitors don't know which bots are "in beta" vs "validated". Surface the badge so the strong bots stand out and weak ones aren't read as failures of the system.
- **Data check**: count bots per maturity_label. Need at least 1 `calibrated` and ≥ 2 `active` bots to make the badging meaningful (otherwise everything is "beta" and the badge means nothing).
- **Ship gate**: ≥ 1 calibrated bot AND ≥ 2 distinct non-experimental maturity labels populated.
- **Implementation**: `PerformanceLeaderboard` shows a small coloured pill next to each bot name. Green = calibrated, blue = active, amber = beta, gray = testing. `maturityLabel` is already plumbed through (see `PublicBotStat.maturityLabel` in page.tsx). Pure CSS + one small badge component.
- **Selling angle**: turns "10 random bots, some losing" into "2 validated strategies + 8 in various R&D stages". Same data, different read.

### Change 5 — Recent wins reel ("Last 14 days · top model picks that beat closing line")

- **Claim**: Concrete recent wins > abstract aggregate. Lets a visitor see specific matches where the model called it right.
- **Data check**: query top 5 bets in last 14d by `clv_pinnacle DESC` where `result='won'`. Need: ≥ 5 results, each with CLV ≥ 5%, average odds ≥ 1.80 (so the wins look meaningful, not 1.05 sure-things).
- **Ship gate**: ≥ 5 qualifying wins in last 14d with avg CLV ≥ 5% AND avg odds ≥ 1.80.
- **Implementation**: new `RecentTopWins` component, fetched server-side, rendered between hero and leaderboard. Each row: match name, market, model pick, odds, CLV-beat. No P&L or stake (Pro-gated).
- **Selling angle**: tangible "look, we predicted this" moments. Free-tier visible (no stake/P&L) to drive curiosity.

### Change 6 — "Model v2 calibration improved" callout

- **Claim**: v20260531 retrain offline eval beat v20260524_market on 9/11 markets (−10% log_loss on 1X2). That's a story new users will respect, but only after we actually promote v20260531.
- **Data check**: confirm promotion happened (`MODEL_VERSION=v20260531` on Railway) AND ≥ 14 days of production bets under it.
- **Ship gate**: promotion landed AND ≥ 14 days settled OR — for *pre-promotion* version — render an "Upcoming model upgrade" tease using offline-eval numbers, ship-gate: offline eval shows v20260531 better than v20260524_market on ≥ 7 markets (already true: 9/11).
- **Implementation**: extend the existing "Model v2 · May 24" purple callout to a wider "Model improvements" strip with each retrain's headline number. Pre-promotion version is a teaser; post-promotion version cites real production data.
- **Selling angle**: shows we're actively improving, not stagnant. "Next model up 10% on 1X2" is genuinely compelling.

## Order of operations

For each change, in order:

1. Run the data check query
2. Print the numbers to chat
3. If ship gate passes → implement → smoke test → commit + push
4. If ship gate fails → mark as ⏸ in this file with the date and the failing number, move to next

Between changes, I check in with you so we don't ship 6 things blind.

## What we are deliberately NOT doing (yet)

- **No "growing X subscribers" social-proof number** — we don't have the subscriber count to make it meaningful. Wait until ≥ 100 users.
- **No testimonials** — nothing to quote yet.
- **No "guaranteed profit" framing** — would violate the "honest" promise and probably some regulators.
- **No vanity metrics** (matches tracked, leagues covered) on the hero. Tertiary content only.
- **No A/B testing infrastructure** — too early; one page, ship and iterate by hand.

## Acceptance

- ≥ 4 of the 6 changes pass their gates and ship
- No change ships that fails its data check
- Performance page time-to-first-paint stays ≤ 1.5s (no new heavy queries on the hot path — every new field comes from `dashboard_cache`)
- Smoke tests cover every new component + cache field
