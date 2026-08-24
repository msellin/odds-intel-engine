# PER-BOT-SWEEP-2026-08-24 — Context

## Status: SHIPPED 2026-08-24 — review 2026-08-31 with a week of forward data

## Artifacts

- `scripts/per_bot_backtest_sweep.py` — point-in-time replay harness
- `scratch_pit_odds_3h` — **scratch table left in the prod DB** (622,514 rows,
  17,750 matches). Rebuild/drop SQL below. Drop when done:
  `DROP TABLE scratch_pit_odds_3h;`

## Rebuild the inputs

```sql
CREATE TABLE scratch_pit_odds_3h AS
WITH mt AS (
  SELECT m.id, m.date, m.score_home sh, m.score_away sa, COALESCE(l.tier,1) tier
  FROM matches m JOIN leagues l ON l.id=m.league_id
  WHERE m.status='finished' AND m.date>='2026-05-01' AND m.date<'2026-08-22'
    AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL)
SELECT s.match_id, mt.date, mt.sh, mt.sa, mt.tier, s.bookmaker, s.market, s.selection, s.odds
FROM mt JOIN LATERAL (
  SELECT DISTINCT ON (o.bookmaker,o.market,o.selection)
         o.match_id,o.bookmaker,o.market,o.selection,o.odds
  FROM odds_snapshots o
  WHERE o.match_id=mt.id AND o.is_live=false
    AND o.market IN ('1x2','over_under_25','over_under_35','btts')
    AND o.bookmaker IN ('Unibet','Betano','Marathonbet','10Bet','888Sport','Pinnacle','Coolbet')
    AND o.timestamp < mt.date - interval '3 hours'     -- point-in-time, no look-ahead
  ORDER BY o.bookmaker,o.market,o.selection,o.timestamp DESC) s ON true;
```
Then export `/tmp/sel.csv` (per-selection pivot + settlement + overround),
`/tmp/preds.csv` (latest pre-kickoff ensemble prob), `/tmp/close.csv`
(Pinnacle closing prices) — queries in git history of this task.

## Findings

### 1. Only 3 of 8 bots were ever properly backtested
CONFIG-SWEEP-2026-08-19 tested MODEL-driven configs on markets
`1x2_home/draw/away, over_under_25_over/under, btts_yes/no`. It validated
`bot_sweep_1x2_home_v1`, `bot_sweep_1x2_draw_v1`, `bot_sweep_btts_yes_v1`.
The other 5 (`pin_*`, `ou*`, `no_pin_home`) use a LINE-SHOP mechanism the
sweep never modelled, justified by an ad-hoc simulation quoted in migrations
274/275/277 **whose script was never committed** — unreproducible.

### 2. The two analyses contradicted each other on tier 4
CONFIG-SWEEP report: "Every single winning config was tier_filter={2,3}…
not tier 4 (too noisy)." Migration 275 (two days later): tier-4 draws
"+6-18% ROI, n=348 ← ship". Deployment followed the unreproducible one.

### 3. The tier-4 draw number reproduces — and is still noise
My replay: n=339, +7.8%. But tier 4 is the ONLY positive cell of 8 tier sets
on a strategy that is −3.6% overall (n=2,772). Window split 16.1/−16.9/15.5.
De-vigged it is −10.0%. Live: −40.8%.

### 4. ROI-based config selection has NEGATIVE predictive value
448-config grid, select on W1+W2 positive → mean W3 (unseen) = **−9.2%**,
vs −5.8% for taking every config and −4.2% for the ones NOT selected.
Only 37% stayed positive. **This is why the backtests were all positive.**

### 5. CLV is the only selector that predicts
| selector (on W1+W2) | #configs | mean unseen-W3 ROI | % positive |
|---|---:|---:|---:|
| positive ROI | 35 | −9.2% | 37% |
| positive CLV | 156 | −2.2% | 31% |
| **CLV > +2%** | **99** | **+1.5%** | **47%** |
| no selection | 299 | −5.8% | 34% |

Caveat: for LINE-SHOP bots CLV-vs-Pinnacle-close is partly tautological
(they select on beating Pinnacle), so CLV is only a clean signal for the
model-driven bots. Closing-price coverage is 26% of rows.

### 6. Monotonic decay across the May-anchored window
All 8 bots at deployed configs: W1 +3.7% → W2 +2.0% → W3 −1.4% →
last 12 days −3.5% → live −0.2%. Tier mix is stable (75-82% tier 1-2)
so this is not a composition shift. The `/performance` page and landing
use the same 2026-05-01 anchor, so the public number is carried by
May–June and is not what the current regime delivers.

### 7. Model versions drift across the window
Backtest predictions span v9a_202425 → v20260719 (8+ versions). Model-bot
backtests therefore measure old models, not the live v20260712/v20260719.

## Per-bot verdict inputs

| bot | BT n | BT ROI | BT CLV | windows+ | LIVE n | LIVE ROI |
|---|---:|---:|---:|---:|---:|---:|
| bot_pin_1x2_home_v1 | 692 | +7.3% | +15.6% | **3/3** | 62 | **+16.2%** |
| bot_pin_1x2_draw_tier4_v1 | 339 | +7.8% | +10.6% | 2/3 | 27 | **−40.8%** |
| bot_sweep_1x2_home_v1 | 411 | +2.1% | +5.0% | 2/3 | 78 | +11.0% |
| bot_sweep_ou25_v1 | 1005 | +1.7% | +11.1% | 2/3 | 99 | −1.9% |
| bot_sweep_1x2_draw_v1 | 614 | +1.1% | +3.1% | 2/3 | 41 | −0.3% |
| bot_sweep_ou35_v1 | 992 | −0.2% | +11.5% | 1/3 | 72 | +0.6% |
| bot_sweep_btts_yes_v1 | 240 | −1.7% | n/a | 2/3 | 30 | +0.6% |
| bot_no_pin_home_v1 | 187 | −6.2% | +9.1% | 1/3 | 66 | −10.6% |

## What shipped 2026-08-24

Engine (`workers/jobs/daily_pipeline_v2.py`):
1. `_get_bot_id_by_name` now filters `is_active`/`retired_at`. **This was a
   real hole** — retiring a bot in the DB did not stop its shadow pass firing,
   because every writer looked the id up by name only.
2. `_LINESHOP_TRUE_EDGE_MIN = 0.03` — de-vigged edge floor, applied uniformly
   to all three surviving line-shop bots. Both writers now divide the
   Pinnacle-implied probability by the market overround.
3. `_LINESHOP_TIERS = (1, 2)`.
4. OU bots: tier filter added (they had NONE — no `leagues` join at all) and
   a **side lock** so only the higher-edge side of a total is written.
5. `COALESCE(l.tier, 1)` removed everywhere — NULL-tier leagues were silently
   passing every tier-1 filter.
6. `bot_pin_1x2_draw_tier4_v1` removed from `_PIN_1X2_SHADOW_CONFIGS`.

DB (`supabase/migrations/281_per_bot_sweep_config_change.sql`):
7. `bot_config_history` table — pre- and post-change JSONB snapshots for all
   8 bots, so any config is recoverable. One live row per bot enforced by a
   partial unique index.
8. Both bots retired with full `retired_reason`.

Frontend (`odds-intel-web`):
9. `SHADOW_BOTS` backtest figures replaced with the reproducible replay
   numbers (the old ones came from the uncommitted simulation).
10. Upcoming-picks flags cut 5 → 1. Four are now enforced in config instead,
    which is strictly better — the bets are never generated. The survivor is
    the model-vs-market gap flag for model-driven bots only
    (SWEEP-HOME-BOTS-CALIBRATION-2026-08-22, still open).
11. Retired bots move to the retired section automatically via `retired_at`;
    the active ROI/CLV cards already excluded them.

Smoke: `PER-BOT-SWEEP-CONFIG-2026-08-24` added. Three existing tests updated —
`BOT-PIN-OU-SHADOW` was **already failing on main** (asserted a call signature
that changed in SHADOW-BOTS-MULTI-COHORT-2026-08-21).

## Still open

- `scratch_pit_odds_3h` left in the prod DB for re-runs — drop when done.
- Promotion gates: the CLV rule is documented in the writer docstrings and
  MODEL_WHITEPAPER §10c, but there is no automated gate check. That is
  `BOT-GRADUATION-GATES-TIERED-2026-08-22` Phase 2.
- The three model-driven bots were NOT re-gated. Their thresholds have no
  better-supported alternative, and `sweep_1x2_home`'s tier 3 slice is its
  better half, so the general tier-3 rule does not apply there.
- `bot_sweep_1x2_draw_v1` is the one to watch: its most recent backtest
  window is −23% to −59% at every edge threshold. Kill at n=100 if it holds.

## Review 2026-08-31

Compare per-bot ROI + CLV for picks with `pick_time >= 2026-08-24` against the
pre-change baseline. Expect LOWER volume (tier gates + de-vig cut roughly half
the line-shop picks) — judge on CLV, not ROI. One week is ~n=60-100/bot, which
is not enough for an ROI verdict and is exactly the sample size at which this
audit showed ROI selection to be anti-predictive.
