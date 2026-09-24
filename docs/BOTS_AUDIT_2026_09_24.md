# Bots audit — what every bot is, collects and is measured on (2026-09-24)

> **Parent row:** `PRIORITY_QUEUE.md` #137 (ADMIN-BOTS-PAGE-AUDIT-2026-09-24), step 1 —
> understanding + conclusions + plan. **Nothing was changed in either repo.** Shares its
> inventory with #138 (shadow-bots page). All numbers are from the live DB on 2026-09-24,
> read-only; queries are reproduced inline so they can be re-run.
>
> Direction: **🤖 OWN first** (which bots to follow with money), **👥 PICKS second**
> (the same bots and ledgers feed /picks and /performance).

---

## 0. The short version (read this if nothing else)

1. **`/admin/bots` reads only `simulated_bets`.** Of the 20 active bots, **only two
   write there** (`bot_v10_1x2`, `bot_high_roi_global_v2`). The other 18 — every trigger,
   instrument, real-money-capable, in-play and published bot — write `shadow_bets` or
   `picks_forward_test`, which this page never reads. They show as "0 bets, waiting for
   qualifying conditions", dimmed. The page is structurally blind to 90% of the live fleet.
2. **What it does show is mostly the graveyard.** 108 bots in `bots`, header says
   "108 bots configured"; **3,841 of the 4,294 non-void rows it loads (89%) belong to
   retired bots**, and the headline Total Bets / Hit Rate / P&L / ROI cards sum them all.
3. **The page contradicts itself.** P&L/ROI use *executable* odds (`execPnl`), Bankroll
   uses `bots.current_bankroll`, which settlement moves with *stored* (high-water) P&L.
   `bot_v10_1x2`: P&L column **+€201**, Bankroll column **€1,355** (+€355).
4. **No page shows a bot's configuration** — books, floors, anchor, freshness, ceiling,
   placeable or not. The only tool that tries (`scripts/bots_describe.py`) can state gates
   for 5 of 20 bots and mislabels the consensus and in-play bots as model-anchored.
5. **Real money is fully off.** `placement_paused = TRUE`, `real_money_armed = FALSE`, both
   placeable bots `ui_place_enabled = FALSE` (since 09-13/09-14). Last `real_bets` row
   2026-09-15. The "Real-money capable" family in SYSTEM_MAP reads as live; it is not.
6. **The honest scoreboard today (margin-corrected own-book CLV, the metric every
   pre-registration uses): no active bot is positive with a CI above zero.** The
   sharp triggers looked positive only while they priced off stale quotes; since the
   60-min freshness gate (2026-09-20) all four per-book sharp triggers are negative
   (−5.2% to −8.4%). `bot_v10_1x2` is the only bot with a positive de-vigged-Pinnacle
   CLV (+2.50%, n=335), and even it reads −1.3% on own-book margin-corrected CLV.
7. **94% of what we write to `shadow_bets` is for retired bots** (59,059 of 62,781 rows
   in the last 30 days), by design in three separate places. Nothing surfaces them.

---

## A. The page: `/admin/bots`

### A1. Where the data comes from

| Piece | File | Reads | Notes |
|---|---|---|---|
| Page | `odds-intel-web/src/app/(app)/admin/bots/page.tsx:41` | `getAllBets()` + `getAllBotsFromDB()` | superadmin gate (lines 26-38) |
| Bets | `src/lib/engine-data.ts:414-439` `getAllBets` | **`simulated_bets` only**, all rows, paged 5k up to 200k, via **`createSupabasePublic()` = the anon key** | no `shadow_bets`, no `real_bets`, no `picks_forward_test` |
| Bots | `src/lib/engine-data.ts:381-412` `getAllBotsFromDB` | every `bots` row incl. retired, cached 30 min (`unstable_cache`, 1800 s) | a retirement can take 30 min to show |
| Aggregation | `src/lib/bot-aggregates.ts` `buildBotStats` / `buildSummary` / `buildMarketStats` | client-side over the loaded rows | shared with /performance |
| Modal price columns | `src/app/api/admin/bot-book-odds/route.ts` | latest `odds_snapshots` row for Coolbet → else Unibet-Site, and Bet365 | **latest ever**, not at pick time, no `timestamp <= kickoff` bound |

⚠️ **#072 interaction.** The page reads `simulated_bets` with the **anon** client, so
`simulated_bets` must stay anon-readable (confirmed: `has_table_privilege('anon',
'simulated_bets','SELECT') = true` after today's lockdown). An admin page is one of the
reasons the paper ledger is still public. It should use `createServerServiceClient()`.

### A2. What every number means

| Where | Label | Computed as | Basis / caveat |
|---|---|---|---|
| Header | "Paper trading · Started 2026-04-27 · €1,000/bot · N bots configured" | N = `allBotsDB.length` | **N = 108 incl. 88 retired.** The €1,000 is false for 14 of 20 active bots (`starting_bankroll`/`current_bankroll` = 1). |
| Toggle | "Quality only (post-2026-05-06)" | drops `pick_time < 2026-05-06` | cutoff is a May pipeline change; irrelevant to every bot created since |
| Card | Total Bets | non-void rows, **all bots incl. retired** | 4,294, of which 3,841 retired |
| Card | Settled / Hit Rate | won+lost, all bots | mixes 50 bots' histories |
| Card | Total P&L / ROI | Σ `execPnl` / Σ stake over settled, all bots | exec basis (see A3) |
| Table | Bets | non-void count per bot | `simulated_bets` only → 0 for 18 active bots |
| Table | Settled / Won / Lost / Hit Rate | per bot | — |
| Table | P&L (€) / ROI% | Σ execPnl / Σ stake | **exec odds** |
| Table | Bankroll | `bots.current_bankroll` | **stored `pnl`** (settlement), not exec — disagrees with P&L |
| Table | ordering | retired last; then settled bots by ROI desc | ROI ranks at any n — a 4-bet bot can top the list |
| Modal | cohort rows "All-time" / "Since 2026-05-24" | same aggregates, split at `MODEL_BATCH_CUTOFF` | cutoff is the May model rollout; meaningless for September bots |
| Modal | CLV | `avg(simulated_bets.clv)` | **raw price ratio, no de-vig** (`settlement.py:613`) — break-even ≈ the closing book's ~8% margin, not 0 (SYSTEM_MAP §1). Not the de-vigged `clv_pinnacle_devig` the promotion rule requires. |
| Modal | Bankroll chart | running sum from €1,000 | hardcoded 1000 regardless of `starting_bankroll` |
| Modal | "Coolbet*" column | latest Coolbet, else Unibet-Site | header tooltip/footnote still say *"Unibet odds — Coolbet runs on the same Kambi platform"* — false since Unibet left Kambi (2026-09-06) and Coolbet is not Kambi |
| Modal | Bet365 column | latest Bet365 snapshot | latest, not at pick time |
| Modal | Model% | `calibrated_prob ?? model_probability` | — |
| Market breakdown | Prematch / Live | split on `bot.startsWith("inplay_")` | the live rig bots are `bot_inplay_*` and are in `shadow_bets` anyway → "Live" is 100% retired `inplay_*` bots |
| Footer | "All bets shown — no cherry-picking" | — | false: 18 active bots' bets are not shown at all |

### A3. ROI / CLV basis, which bets count

- **Which bets:** every `simulated_bets` row, any bot, any status. Voids excluded from
  counts. **Retired bots included in every headline card**, hidden only in the table
  (collapsed "N retired" row).
- **ROI basis:** `execPnl` (`engine-data.ts:190-208`) re-prices a winning single at
  `odds_at_pick_live` if present, else `odds_at_pick`. It corrects the STALE-BEST-ODDS
  high-water mark for pre-match bots. **For the retired in-play bots it produces nonsense**
  — e.g. `inplay_c` stored ROI −7.5% vs exec −64.5%, `inplay_l` +10.8% vs +41.8% —
  because `odds_at_pick_live` means something different there.
- **Paper vs real:** paper only. `real_bets` is never read.
- **Dedup:** none needed within `simulated_bets` (unique per bot/match/market/selection).

### A4. What the page actually shows today (active bots with data)

```sql
-- simulated_bets per bot, exec vs stored basis
SELECT b.name, count(*) FILTER (WHERE result IN ('won','lost')) st, ... FROM simulated_bets ...
```

| Bot | settled | ROI stored | ROI exec (page) | raw CLV (page modal) | de-vigged Pinnacle CLV (n) | last pick |
|---|---|---|---|---|---|---|
| `bot_v10_1x2` | 401 | +12.6% | **+7.1%** | +7.47% | **+2.50% (335)** | 2026-09-22 |
| `bot_high_roi_global_v2` | 52 | +25.2% | **+21.5%** | +7.40% | +3.98% (48) | 2026-09-20 |

Both have **dried up** (#129: book-set shrink under them; the PICKS anchor was widened
today, volume should return — watch it).

---

## B. Inventory

### B1. Every bot in `bots` — counts

| Status | n | Detail |
|---|---|---|
| Active (`retired_at IS NULL`) | **20** | table B2 |
| Retired | **88** | 49 of them have `simulated_bets`; 47 have `shadow_bets` |
| Retired but still WRITING `shadow_bets` in the last 7 days | **18** | see B4 |

Retired, by date of retirement (name — `simulated_bets` / `shadow_bets` rows):
- **2026-05-24…06-01 (first cull):** ou15_defensive 40/740 · btts_conservative 44/541 ·
  high_roi_global 14/288 · proven_leagues 14/395 · inplay_a2 · inplay_c_home · inplay_p 193 ·
  dnb_away_value 1/10 · dnb_home_value · ou25_global 72/1,840 · sweden_over25 0/64 ·
  under25_specialist 0/6 · dc_strong_fav 34/19,990 · dc_value 126/41,189 · ou25_specialist 0/70 ·
  ou35_attacking 35/115 · dc_specialist 62/40,029 · lower_1x2 57/2,554 · aggressive 713/2,646 ·
  draw_specialist 4 · inplay_f 3
- **06-06…06-24:** acca_coolbet, acca_proven, acca_value, combo_proven_system, combo_system,
  aggressive_v2 61/473, ah_away_dog 91/2,052, high_alignment 544/**10,556**, inplay_n 68,
  inplay_j 87, ah_home_fav 148/3,147
- **07-04…08-26:** inplay_i, inplay_p_v2, inplay_e, greek_turkish, ou_specialist,
  inplay_btts_press_v1, inplay_c, inplay_d, no_pin_shadow_v1, acca_leg_shadow, opt_away_british,
  opt_away_europe, opt_ou_british, pin_1x2_draw_tier4_v1, no_pin_home_v1, sweep_1x2_draw_v1,
  sweep_1x2_home_v1
- **09-03…09-14 (in-play cull + OWN verdict + CLV retirement):** inplay_a/b/g/h/l/m/o/q,
  inplay_btts_dryspell_v1, btts_all 214/2,167, btts_v2, sweep_btts_yes_v1, conservative,
  opt_home_lower, proven_leagues_v2, pin_1x2_home_v1, sweep_ou25_v1, sweep_ou35_v1,
  coolbet_value_v1, coolbet_trigger_v1, dnb_specialist, summer_specialist, 1x2_specialist,
  ou_1h_paper_shadow_v1, wide_1x2_model_v1, wide_ou_model_v1, coolbet_trigger_1x2_v1,
  trigger_1x2_model_v1, unibet_trigger_1x2_v1, 1h_1x2_paper_shadow_v1, coolbet_trigger_ou_v1,
  team_total_paper_shadow_v1, trigger_ou_model_v1, unibet_trigger_ou_v1, corners_paper_shadow_v1
- **09-22…09-24 (splits):** v10_all (split into v10_1x2 + v10_ou), consensus_anchor_v1 (split into
  B/C/D), **v10_ou (retired today, #077)**, **sharp_forward_test_v1 (retired today, split into
  sharp_1x2 + sharp_ou, #122)**

### B2. The 20 active bots — one row each

Legend — **Table**: sim = `simulated_bets`, shadow = `shadow_bets`, pft = `picks_forward_test`.
**Books** = where it looks for a price. `PLACEABLE_BOOKS` = Coolbet + Unibet-Site
(`best_price_router.py:47`). `ACCESSIBLE` = Coolbet, Unibet-Site, Epicbet, Tonybet
(`daily_pipeline_v2.py:1170-1185`). `publishable` = every book except `_NON_OFFERS`
(`daily_pipeline_v2.py:1152-1167`). **Record** = settled n; ROI at exec odds (flat €10 for
shadow via `shadow_bot_scoreboard`); **mc-CLV** = `clv_margin_corrected` mean (own-book close,
margin removed — the pre-registered decision metric) with t; for sim bots also de-vigged
Pinnacle CLV. **Shown on /admin/bots?** — only if it writes `sim`.

| # | Bot | Maturity | Writer (cadence) | Table | Market | Probability source | Edge floor (source) | Odds gate | Other gates | Books | Placeable / real $ / on /picks | Record (settled · ROI · mc-CLV) | On /admin/bots |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `bot_v10_1x2` | calibrated | `morning_pipeline` 04:00 + `betting_refresh_interval` :05/:35 (`daily_pipeline_v2`); also timing-cohort shadow :10/:40 | sim (+ shadow timing) | 1x2 | production model, calibrated `cal_prob` | tiered 3–12% by league tier & fav/long (`BOTS_CONFIG`, `daily_pipeline_v2.py:82-98`) | 1.30–4.50, min_prob 0.30 | Pinnacle veto, cross-drift veto, outlier anchor ×1.25 (Pinnacle else median ≥3 **publishable**, #129 today), odds lag ≤6 h / age ≤48 h, OU-PIN-REQUIRED n/a | best of **publishable** books (since #005, 09-22) | not placeable · feeds `bot_coolbet_1x2_model_v1` candidates · **on /picks (show_on_picks)** + Telegram | sim 401 · +7.1% · Pinnacle-devig CLV **+2.50%** (n=335); shadow mc-CLV −1.31% (t=−1.7, n=307) | **yes** |
| 2 | `bot_high_roi_global_v2` | beta | same as #1 | sim (+ shadow timing) | 1x2 home/away, Spain/Australia/Iceland | production model | 6/9% (T1) 5/8% (T2-3) (`daily_pipeline_v2.py:701-731`) | 1.50–5.50, min_prob 0.28 | same pipeline gates | publishable | not placeable · not on /picks (`show_on_picks` false) · on /performance (beta) | sim 52 · +21.5% · Pinnacle-devig +3.98% (48); shadow mc −1.02% (n=27) | **yes** |
| 3 | `bot_coolbet_1x2_model_v1` | experimental | `coolbet_model_1x2_shadow` :10/:40 + on every Coolbet/Unibet sweep (`pick_generator.on_odds_written`) | shadow | 1x2 **home only** | `pipeline` = `simulated_bets.calibrated_prob` of **calibrated** bots (= `bot_v10_1x2` only) | selection-aware registry floor 10% home (`coolbet_placer.min_edge_for_pick`) | ≥2.80 | `_own_outlier_ok` (Pinnacle else median ≥3 ACCESSIBLE, ×1.25, today); anchor ratio 1.5625×; book quote ≤180 min | PLACEABLE_BOOKS (Coolbet **and Unibet-Site** despite the name) | **in PLACEABLE_BOTS**, `ui_place_enabled` **FALSE** since 09-14; placement paused | 19 · +10.4% · mc **−4.19%** (n=11) · real: 6 bets −€60 | no |
| 4 | `bot_coolbet_ou_model_v1` | experimental | `coolbet_model_ou_shadow` :10/:40 + sweeps | shadow | O/U 2.5/3.5 | `pipeline` from calibrated bots — **none left** (`bot_v10_ou` retired today, last O/U sim pick 09-13) | 8% (registry) | ≥1.80 | no own-outlier check for O/U | PLACEABLE_BOOKS | in PLACEABLE_BOTS, toggle **FALSE** since 09-13 | 33 · −34.9% · mc +0.75% (n=7) · real: 23 bets −€17.30 · **last pick 09-13 — dead** | no |
| 5 | `bot_coolbet_trigger_sharp_1x2_v1` | experimental | `pick_triggers` :05 (windows) → `pick_trigger_matcher` :15/:45 | shadow | 1x2 | Shin-de-vigged Pinnacle (latest pre-KO, **no anchor freshness cap**) | 3% (`pick_triggers._SHARP_MIN_EDGE_BY_MARKET:130`) | ≥1.01 | book quote ≤60 min (`FRESHNESS_MAX_AGE_MIN`, since 09-20); outlier cap `max_odds = min_odds×1.6`; anchor ratio / ≥4-book median (#113); **no 8% ceiling** | Coolbet | paper | 257 · −1.5% · **pre-09-20 +2.10% (t=1.6) → post-09-20 −5.17% (t=−1.8, n=13)** | no |
| 6 | `bot_coolbet_trigger_sharp_ou_v1` | experimental | same | shadow | O/U 2.5 | Pinnacle Shin | 3% | ≥1.01 | same | Coolbet | paper | 71 · −11.1% · pre +2.61% → **post −8.43% (t=−6.4, n=6)** | no |
| 7 | `bot_unibet_trigger_sharp_1x2_v1` | experimental | same | shadow | 1x2 | Pinnacle Shin | 3% | ≥1.01 | same | Unibet-Site | paper | 252 · +0.8% · pre +1.89% → **post −7.72% (t=−3.1, n=49)** | no |
| 8 | `bot_unibet_trigger_sharp_ou_v1` | experimental | same | shadow | O/U 2.5 | Pinnacle Shin | 3% | ≥1.01 | same | Unibet-Site | paper | 55 · −14.0% · pre +1.25% → post −7.27% (n=5) | no |
| 9 | `bot_trigger_1x2_sharp_v1` | experimental | `pick_generator` on every Coolbet/Unibet sweep | shadow | 1x2 | Pinnacle Shin (`_candidates_from_sharp`) | 3% explicit (`bot_configs._SHARP_EDGE_FLOOR`) | ≥1.01 | **8% ceiling**; outlier cap ×1.6; anchor ratio; book quote ≤**180** min (not 60) | PLACEABLE_BOOKS | paper | **all pre-09-20 picks voided (phantom)**; since: 42 · −29.3% · mc −2.23% (n=31) | no |
| 10 | `bot_trigger_ou_sharp_v1` | experimental | same | shadow | O/U 2.5 | Pinnacle Shin | 3% | ≥1.01 | same as #9 | PLACEABLE_BOOKS | paper | 7 · −49.3% · mc −8.05% (n=5) | no |
| 11 | `bot_trigger_1x2_sharp_tight_v1` | experimental (INSTRUMENT) | `pick_trigger_matcher` :15/:45 | shadow | 1x2 | Pinnacle Shin | **2%** (`1x2_tight`) | ≥1.01, **≤2.50** | book quote ≤60 min; outlier cap; anchor ratio | Coolbet, Unibet-Site, Epicbet, Tonybet (pooled; per-book cohorts since today) | paper; pre-registered: promote only on mc-CLV > 0 at n≥300 | 263 · +1.4% · **mc −3.74% (t=−4.8, n=212)** | no |
| 12 | `bot_unified_gate_1x2_paper_v1` | experimental (INSTRUMENT) | `pick_generator` | shadow | 1x2 all selections | `predictions` + per-selection calibrator (`pick_triggers._fit_calibrator`) | **flat 10%** explicit | ≥2.80 | anchor ratio; ≤180 min | PLACEABLE_BOOKS | paper, never published | 153 · **−23.3%** · **mc −6.74% (t=−7.3, n=78)** (2 days old) | no |
| 13 | `bot_ou35_model_v1` | experimental | `ou35_model_shadow` :10/:40 | shadow | O/U 3.5 | own isotonic fit on raw `over35` predictions | 8% (`OU35_MODEL_EDGE_FLOOR` env default) | none in code (registry says 1.80) | none | **Coolbet only** | paper; #133 retire decision open | 452 · −11.4% · **mc −5.27% (t=−20.1, n=201)**; 65 settled since 09-20 have **no** mc-CLV | no |
| 14 | `bot_inplay_slowstate_v1` | experimental | `oddsintel-inplay-collector.service` (VPS, continuous) | shadow | in-play U2.5 / leader | book's own de-vigged prob | none (locked triggers) | ≤2.20 | — | Epicbet | paper; STOP n=1,000 if lift < 0 | 600 · −4.1% · CLV inadmissible (page still shows mc −43.7%) | no |
| 15 | `bot_inplay_slowstate_afctl_v1` | experimental | same | shadow | same | AF live aggregate | — | ≤2.20 | — | api-football-live | control arm | 313 · +0.3% | no |
| 16 | `bot_sharp_1x2_v1` | testing | `publish_picks_forward_test` :05/:35 | pft (arm=live, market=1x2) | 1x2 | Pinnacle Shin, anchor overround ≤4% | 3% **multiplicative** (`P×odds−1`) | **cap ≤4.0** | anchor & bet quote ≤60 min apart; ratio 0.20 | publishable minus `EXCLUDED_BOOKS` (`publish_picks_forward_test.py:323`) | **published** Telegram + /picks | since v4: 62 · +7.5% · **mc −2.88% (t=−4.1)** | no (on /performance) |
| 17 | `bot_sharp_ou_v1` | testing | same | pft (live, O/U) | O/U 2.5 | same | 3% mult. | ≤4.0 | same | same | published | 22 · −13.8% · mc −2.87% (t=−2.8) | no |
| 18 | `bot_consensus_b_v1` | beta | same | pft (arm=consensus_anchor, grade B) | 1x2 + O/U | de-vigged ≥5-book consensus; v2 today: 3% under Shin, additive AND power | 3% mult., ceiling 6% | 1.20–1.60 | same alignment | same | published | 4 · −25.5% · mc −4.09% | no |
| 19 | `bot_consensus_c_v1` | testing | same | pft (grade C) | 1x2 + O/U | same | 3%, ceiling 6% | ≤4.0 | same | same | published | 31 · −19.5% · mc −7.12% | no |
| 20 | `bot_consensus_d_v1` | testing | same | pft (grade D) | 1x2 + O/U | same | 3%, ceiling 8% | ≤4.0 | same | same | **recorded, not sent** since 09-23 | 17 · −11.2% · mc −5.07% | no |

Also in `picks_forward_test` and **not a bot**: `arm='junk_anchor'` — the negative control
(shuffled anchor). v4: 571 settled, ROI −1.7%, **mc −3.04% (t=−17)**. The published live
arm (−2.88%) is **statistically indistinguishable from the control** on the pre-registered
metric; the pre-registration's first stop is "n=200 with mc-CLV < −2%" and the live arm is
at n=84 and −2.9%.

### B3. Code paths that produce bots — per family

**(a) The pipeline — `workers/jobs/daily_pipeline_v2.py`** (`run_morning` / `run_betting`)
- `BOTS_CONFIG` (`:81`) holds **35 bot dicts; only 2 are active in the DB** (`bot_v10_1x2`,
  `bot_high_roi_global_v2`). Only 10 carry `is_active: False` in code — the other 23 are
  retired in the DB alone, so the file reads as if they were live. Live-mode loop skips
  retired bots via the DB (`:3537-3543`, `_bot_active` built at `:2694-2712`).
- **Shadow timing cohorts** (`scheduler.job_shadow_run_interval`, :10/:40, cohort = `HHMM`):
  same code with `shadow_mode=True`, which **deliberately evaluates every bot incl. retired**
  (`SHADOW-RETIRED-OK`, 2026-05-20, `:3538-3541`) — 48 runs/day into `shadow_bets`.
- `_run_no_pin_shadow_pass` (`:4360`) / `_run_sweep_shadow_pass` (`:4711`): write only for
  `bot_no_pin_*` / `bot_sweep_*`, **all retired** → both run every cohort and write nothing.
  Dead code still executing.
- Book set: publishable (PICKS). Outlier anchor: publishable (#129 today).

**(b) `pick_generator` + `bot_configs.py`** (declarative BotConfig; runs on every
Coolbet and Unibet-Site sweep via `on_odds_written`, plus the two mirror jobs at :10/:40)
- `CONFIGS`: `bot_coolbet_1x2_model_v1`, `bot_coolbet_ou_model_v1` (prob_source `pipeline`).
- `TRIGGER_CONFIGS`: `bot_trigger_1x2_model_v1` + `bot_trigger_ou_model_v1` (**retired —
  still in the list, skipped by `_bot_id`**), `bot_trigger_1x2_sharp_v1`,
  `bot_trigger_ou_sharp_v1`, `bot_unified_gate_1x2_paper_v1`.
- Books: `PLACEABLE_BOOKS` = **Coolbet + Unibet-Site only** — Epicbet and Tonybet are never
  priced by these bots, although they are ACCESSIBLE and the tight instrument uses them.
- Retirement: `_bot_id` requires `retired_at IS NULL` (`pick_generator.py:132-153`).
- ⚠️ Upsert (`:321-341`) **overwrites `odds_at_pick`, `edge_percent`, book on every re-run
  while `pick_time` stays the first one.** The recorded price is the *last clearing* price
  before kickoff, not the decision price at `pick_time`, and no `decision_quote_age_min` is
  written (`shadow_bot_scoreboard.decision_age_known_n = 0` for #3, #4, #9, #10, #12).

**(c) `pick_triggers` (Stage A, :05 hourly) → `pick_trigger_matcher` (Stage B, :15/:45)**
- `BOOK_MARKET_BOTS` (`pick_trigger_matcher.py:31-60`): 4 per-book sharp bots + tight over
  4 books. Freshness ≤60 min on the book quote for every strategy (`:294-298`). No edge
  ceiling (only the ×1.6 outlier cap). Upsert also overwrites price/edge, but keeps
  `decision_quote_age_min` from the first decision.

**(d) Stand-alone paper modules** (`workers/jobs/*_paper_bot.py`, `ou35_model_shadow.py`)
- `corners_paper_bot` / `team_total_paper_bot` / `first_half_1x2_paper_bot`: picks at
  08/12/16/20 :20-27, settle hourly. All three bots are **retired** but `_bot_id` looks up
  by name with **no retirement check** (`corners_paper_bot.py:116-119`,
  `team_total_paper_bot.py:82`, `first_half_1x2_paper_bot.py:51`) — they keep writing,
  per the owner's 2026-09-18 call ("rows are near-free and genuinely out-of-sample").
  Books: Coolbet/Epicbet/Unibet-Site/Tonybet (v2 rule today). Sharp edge ≥0%.
- `ou35_model_shadow` (active bot #13): same name-only lookup; Coolbet only.

**(e) In-play rig** — `oddsintel-inplay-collector.service` on the VPS (moved 09-22), not
the scheduler.

**(f) The published forward test** — `scripts/publish_picks_forward_test.py`, :05/:35.
Writes `picks_forward_test` only; bots #16-#20 are handles over it (ledger split by
`arm` / `market` / `grade` in views).

**(g) Real-money paths** — `scripts/place_coolbet_ui.py` (Mac) and
`best_price_router.route`, both behind `placement_gate` (`PLACEABLE_BOTS` at
`placement_gate.py:77`). Today: paused, not armed, both toggles off.

### B4. Retired bots still writing `shadow_bets` (last 7 days)

| Why they still write | Bots |
|---|---|
| Pipeline timing cohorts evaluate retired bots on purpose (`SHADOW-RETIRED-OK`) | high_alignment (~900 rows/wk), dc_value, dc_specialist, dc_strong_fav, ah_home_fav, ah_away_dog, lower_1x2, opt_home_lower, proven_leagues(_v2), summer_specialist, greek_turkish, conservative, aggressive(_v2), ou15_defensive, ou25_global, high_roi_global |
| Paper modules with a name-only `_bot_id` (owner's call 09-18) | 1h_1x2_paper_shadow_v1 (200/wk), team_total_paper_shadow_v1 (500/wk), corners_paper_shadow_v1 (490/wk) |

Last 30 days: **retired bots 59,059 rows (52,778 timing-cohort + 6,281 paper modules) vs
active bots 3,722.** The "recovery criterion" these rows were kept for
(*≥30 bets at ≥3% ROI in shadow*) has no job that reads it; nothing surfaces them.

### B5. What books each bot actually watches (one view)

| Book set | Defined | Used by |
|---|---|---|
| publishable (deny-list) | `daily_pipeline_v2.py:1152-1167` | pipeline bots #1, #2 (price + #129 outlier anchor); forward test uses its own near-identical `EXCLUDED_BOOKS` |
| ACCESSIBLE (4 Estonian) | `daily_pipeline_v2.py:1170-1185` | `_own_outlier_ok`, retired no-pin/sweep passes |
| PLACEABLE_BOOKS (2) | `best_price_router.py:47` | #3, #4, #9, #10, #12 |
| per-book hardcode | `pick_trigger_matcher.py:31-60` | #5-#8 (one book each), #11 (4 books) |
| 4 own books hardcode | `*_paper_bot.py` | retired corners / team-total / 1H |
| Coolbet only hardcode | `ou35_model_shadow.py` SQL | #13 |
| Epicbet / AF-live | inplay collector | #14, #15 |
| Pinnacle (anchor, never bet) | `ANCHOR_BOOK` / `_SHARP_ANCHOR_BOOK` | every sharp bot |

Seven different book sets for twenty bots, none visible on any page.

---

## C. Drift — where page, code, registry, SYSTEM_MAP and DB disagree

| # | What | Says | Reality |
|---|---|---|---|
| C1 | /admin/bots coverage | index blurb "All bots — fires, ROI, CLV, maturity, market mix" (`admin/page.tsx:13`); footer "All bets shown"; `performance-leaderboard.tsx:460` "/admin/bots shows every bot including in-play ones" | reads `simulated_bets` only: 2 of 20 active bots; no maturity, no fires, no shadow/in-play |
| C2 | /admin/bots header | "€1,000/bot starting bankroll", "N bots configured" | 14 active bots have bankroll 1; N=108 includes 88 retired |
| C3 | P&L vs Bankroll | same page | exec vs stored basis — v10_1x2 +€201 vs €1,355 |
| C4 | Modal "Coolbet*" column | "Unibet odds — Coolbet runs on the same Kambi platform" | returns latest Coolbet else Unibet-Site; neither is Kambi; price is latest-ever, not at pick |
| C5 | Modal CLV | labelled "CLV" | raw price ratio, break-even ≈ +8%; the promotion rule forbids it |
| C6 | Live/prematch split | `isLiveBot = startsWith("inplay_")` | the live rig is `bot_inplay_*` in shadow_bets; the "Live" table is all retired bots |
| C7 | Retirement self-enforcing | SYSTEM_MAP §2 "A DB retirement only became self-enforcing on 2026-09-14" | true for pick_generator + matcher only; pipeline shadow cohorts and 4 job modules write for retired bots (B4) |
| C8 | Real-money family | SYSTEM_MAP §2 row for `bot_coolbet_1x2_model_v1`: "Real money, per-bot toggle"; §4a "Coolbet UI placer — live, hourly 06-21 UTC" | toggle FALSE since 09-14, `placement_paused` TRUE, `real_money_armed` FALSE, execute launchd jobs unloaded |
| C9 | `bot_coolbet_ou_model_v1` | registry: re-enable "on positive CLV over a post-fix window" | its only candidate source (calibrated O/U sim picks) no longer exists — it cannot produce a post-fix window at all |
| C10 | "ALL SHARP TRIGGERS REFUSE A STALE QUOTE (60 min)" | SYSTEM_MAP §2 | true for the 5 matcher bots; the 2 generator sharp bots (#9, #10) use the router's 180-min freshness and record no quote age. Neither path caps the **Pinnacle anchor's** age — the time-alignment defect SYSTEM_MAP §1 calls load-bearing |
| C11 | Edge ceiling on sharp bots | §2a: "BotConfig.edge_ceiling 8% on sharp_devig" | only #9/#10 have it; the 4 per-book sharp bots and tight have only the ×1.6 outlier cap |
| C12 | ACCESSIBLE set | SYSTEM_MAP §4e: "(Coolbet/Betano/Unibet/Epicbet)" | Coolbet/Unibet-Site/Epicbet/Tonybet |
| C13 | Registry `twin=` | registry comment says twin back-refs "were dropped with them" | 3 O/U sharp specs still point at retired twins (`bot_coolbet_trigger_ou_v1`, `bot_unibet_trigger_ou_v1`, `bot_trigger_ou_model_v1`) |
| C14 | "The eight are deliberately still active … follow-up migration retires them, 18 → 10" | registry `:131-135`, SYSTEM_MAP §2 | the 4 model ones were retired on CLV; the 4 per-book sharp ones are still active with no planned follow-up |
| C15 | Forward-test one-liners | registry `bot_sharp_1x2_v1` / `bot_sharp_ou_v1`: "top 8/day" | cap dropped 2026-09-15 (PICKS-NO-DAILY-CAP) |
| C16 | `TRIGGER_CONFIGS` | carries `bot_trigger_1x2_model_v1`, `bot_trigger_ou_model_v1` | both retired; run every sweep and exit at `_bot_id` |
| C17 | `bots_describe.py` | "the tool to see every bot's gates" (§4d) | states 5 of 20; labels consensus B/C/D and both in-play bots `model`; header "7 declared via BotConfig" counts retired configs |
| C18 | Forward-test ledger on admin | SYSTEM_MAP §2 surfaces: "/admin/shadow-bots (both arms and all rule versions, via `picks_forward_test_arm_summary`)" | no web file references `picks_forward_test_arm_summary`; junk control and grade D are on **no** admin page |
| C19 | `show_on_picks` | — | TRUE on retired `bot_v10_ou` and `bot_sharp_forward_test_v1` (harmless, the view filters retired; noise) |
| C20 | `bot_coolbet_1x2_model_v1` name | "Coolbet" | prices at Coolbet **and** Unibet-Site (10 of 19 settled picks were Unibet-Site) |
| C21 | `bot_ou35_model_v1` odds floor | registry 1.80 | no odds floor in `ou35_model_shadow.py` |
| C22 | Same bot, three records | `bot_v10_1x2` | /admin/bots: n=401, ROI +7.1%, raw CLV +7.47%; /admin/shadow-bots: n=359, ROI +6.3%, mc-CLV −1.42%; SYSTEM_MAP: n=335, Pinnacle-devig CLV +2.50%, ROI +7.3% (n=400). Different tables (sim vs shadow timing cohorts), different CLV definitions, no label says which |
| C23 | `SHADOW_MODEL_VERSION` | `daily_pipeline_v2.py:3169-3185` says the stale pin was fixed by auto-select | the VPS `.env` still pins `SHADOW_MODEL_VERSION=v20260705`, older than production `v20260712`, so auto-select never runs — the A/B slot again scores production's predecessor (350 prediction rows in the last 2 days). Not a bot, but it is collected data nothing can use |

---

## D. Conclusions for the owner (plain English)

### D1. Which bots have real evidence behind them

Honest answer: **none is proven to beat the market today.** The yardstick every
pre-registration in this repo uses is "margin-corrected CLV" — did we get a better price
than the closing price, after taking out the bookmaker's cut. By that yardstick:

- **`bot_v10_1x2` (model, 1x2)** — the only one with a *positive* number that survives a
  confidence interval: +2.50% against the de-vigged Pinnacle close, n=335. But (a) all of
  it comes from July-September, (b) measured against our own books' close it is −1.3%,
  and (c) it has been starved of picks since mid-September (#129 fixed the cause today).
  **Worth watching, not yet worth money.**
- **`bot_high_roi_global_v2`** — +3.98% vs Pinnacle but only 48 bets, 5 of them pre-August;
  too small to say anything. Also starved.
- **Sharp triggers (the four per-book ones)** — looked like the best bots in the system
  (+1.9% to +2.6%) **only because they were pricing off stale quotes**. Since the 60-minute
  freshness gate on 2026-09-20 every one is negative (−5% to −8%). Small post-fix n, but
  the direction is unanimous. **Do not follow.**
- **The published picks (`bot_sharp_1x2_v1`/`_ou_v1`)** — −2.9% on the pre-registered
  metric, the same as the shuffled-anchor control (−3.0%). On course to hit its own stop
  rule at n=200. The consensus arm is worse (−5% to −9%). This is a 👥 PICKS problem the
  owner should see before readers do.

### D2. Dead, untestable or mislabelled

| Bot | Verdict | Why |
|---|---|---|
| `bot_coolbet_ou_model_v1` | **dead** | no candidates since 09-13; its source bot was retired today; still "real-money capable" in the registry |
| `bot_coolbet_1x2_model_v1` | **starved + off** | toggle off since 09-14, fed only by v10_1x2's pending picks; mc-CLV −4.2% on n=11 |
| `bot_ou35_model_v1` | **losing, clearly** | mc-CLV −5.27% at t=−20 on n=201; #133 already asks to retire it; also lost CLV coverage after 09-20 |
| `bot_unified_gate_1x2_paper_v1` | **instrument, early and bad** | −6.7% mc-CLV in two days; keep only if the draw/away question still matters |
| `bot_trigger_1x2_sharp_tight_v1` | **instrument, failing its own test** | mc-CLV −3.7% at n=212 against a pre-registered bar of >0 at n=300 |
| `bot_trigger_1x2_sharp_v1` / `_ou_sharp_v1` | **untestable until they have rows** | whole history voided 09-20; 49 settled since, negative |
| `bot_inplay_slowstate_v1` + control | **fine as an experiment** | but the page shows a CLV for them that the design says is inadmissible |
| consensus B/C/D | **too new** | 4 / 31 / 17 settled |

### D3. Data we collect that nothing uses

- **~59,000 shadow rows/month for retired bots** (B4). Settled every night, read by no
  page, no job, no decision.
- **The junk-anchor control** (586 rows) — the one number that says whether the published
  picks beat noise — is on no admin page.
- **`clv_pinnacle`, `clv_live`, `clv_pinnacle_live`, `clv_margin_corrected`, `closing_margin`,
  `decision_quote_age_min`** — computed per shadow row; /admin/bots shows none of them.
- **The shadow-model A/B slot** (C23) — scoring an older model than production.
- **`_run_no_pin_shadow_pass` / `_run_sweep_shadow_pass`** — run every cohort, write nothing.

### D4. The biggest confusions the page creates today

1. It looks like "the bots dashboard" and shows 2 of 20 live bots.
2. Every headline card is dominated by bots retired months ago.
3. P&L and Bankroll disagree on the same row (exec vs stored).
4. "CLV" is the raw ratio, which is positive for almost everything (break-even ≈ +8%), so
   losing bots look like they beat the close.
5. "0 bets — waiting for qualifying conditions" on bots that have hundreds of settled bets
   in `shadow_bets`.
6. No config anywhere: which books, which anchor, which floor, which freshness, placeable
   or not, published or not.
7. The "Coolbet*" column claims to be a Kambi proxy and shows a price from any time.
8. Cohort splits and the quality toggle use May dates that mean nothing for September bots.
9. The same bot has a different n, ROI and CLV on /admin/bots, /admin/shadow-bots and
   SYSTEM_MAP, with no label saying which table or which CLV.
10. "Paper trading · €1,000/bot" hides that some bots were real-money-capable and that
    real money is currently fully off.

---

## E. Refactor plan for `/admin/bots` (not implemented)

**Principle:** one row per **active** bot, built from the registry + DB, answering four
questions in order: *what is it* (config), *is it running* (last pick, cadence), *what is
its record* (one honest metric, labelled), *what can it do* (paper / published / placeable
/ armed). Retired bots move to a separate, collapsed archive.

| # | Change | Why | Effort |
|---|---|---|---|
| E1 | **Engine: one view `bot_board`** — per active bot: config (from a new `bots` JSON column or a registry export), last pick, picks 7d, settled n, ROI at exec odds, **mc-CLV mean/CI/t**, Pinnacle-devig CLV where it exists, fresh-quote share, pre/post era split where a gate changed (e.g. 2026-09-20). Union over `simulated_bets`, `shadow_bets_unique` (excluding timing cohorts) and `picks_forward_test` (incl. junk control) | one source; stops three surfaces computing three numbers | 1 d (migration + smoke) |
| E2 | **Export the config**: `scripts/export_bot_config.py` writes registry + BotConfig + matcher + job constants to a `bot_config` table (books, anchor, edge floor + its source, odds floor/cap, ceiling, freshness, outlier gates, placeable, published). Fix `bots_describe.py` to read it | config becomes visible and diffable; answers "which books does it watch" | ½–1 d |
| E3 | Page: replace the four headline cards with **active-only** counts (bots running / picked today / settled 7d) and a fleet status line (placement paused? armed? which toggles on) | cards currently describe the graveyard | 2 h |
| E4 | Page: one table, one row per active bot, grouped by family, columns: Family · Anchor · Market · Books · Floors (with source tooltip) · Placeable/Published · Last pick · n · ROI · **mc-CLV ± CI** · verdict chip | the owner's question, directly | ½ d |
| E5 | Remove: raw "CLV", Bankroll column (or show it from the same exec basis), €1,000 header, quality toggle, May cohort rows, "Coolbet*/Kambi" column + footnote, "All bets shown" footer, `isLiveBot` split | each is wrong or meaningless today | 2 h |
| E6 | Modal: config block first (from E2), then record split at the bot's own gate-change dates (not global May cutoffs), then picks with **decision price, quote age, close, mc-CLV** | turns the modal into the audit tool | ½ d |
| E7 | Retired archive tab: name, retired date + reason (`bots.retired_reason`), final record, "still writing? (source)" flag | makes B4 visible; supports D3 cleanup decision | 2 h |
| E8 | Switch reads to `createServerServiceClient()` | lets #072 revoke anon on `simulated_bets` | 30 min |
| E9 | Merge overlap with `/admin/shadow-bots`: /admin/bots = **what each bot is and how it's doing**; /admin/shadow-bots = **today's picks to act on**. Delete duplicated per-bot aggregates from one of them | two pages computing different records for the same bot (C22) | decided in #138 |

Engine-side cleanups this audit implies (each small; file separately or fold into #137 step 3):

| Cleanup | Effort |
|---|---|
| Decide the retired-bot shadow writes (B4): stop timing-cohort evaluation of retired bots, or keep and surface them | owner decision, then 30 min |
| Remove retired configs from `TRIGGER_CONFIGS` and the dead `_run_no_pin_shadow_pass` / `_run_sweep_shadow_pass` calls | 1 h |
| Fix SYSTEM_MAP/registry drift C7-C18 in one doc commit | 1-2 h |
| Owner decisions: retire `bot_coolbet_ou_model_v1` (no source), `bot_ou35_model_v1` (#133), the 4 per-book sharp triggers (post-09-20 negative) — or keep as instruments with a stated stop rule | 30 min once decided |
| Pinnacle-anchor freshness on the sharp triggers (C10) — the same 60-min alignment the published rule already uses | ½ d, changes a measured series |
| Pick upsert keeps first-decision price (or writes a second row) so recorded odds = decision odds (B3 b) | ½ d |
| Remove the stale `SHADOW_MODEL_VERSION` pin from the VPS `.env` (C23) | 5 min |

Total for the page: **~2½–3 days** (E1-E8), plus the engine cleanups (~1½ d) and the
owner decisions.

---

## F. For #138 (`/admin/shadow-bots`)

What that page will need from this inventory, and what was noticed in passing:

- **It already reads the right tables** (`shadow_bets_unique`, `shadow_bot_scoreboard`,
  `real_bets`, `coolbet_placer_bots`, `coolbet_session_state`), active bots only — it is
  the better base. `/admin/shadow-bots` + E1's view should become the single per-bot
  record.
- **Ledger-backed bots are missing from its scoreboard.** #16-#20 write
  `picks_forward_test`, not `shadow_bets`, so they have no scoreboard row; no admin page
  shows the forward test by arm, grade, rule version or the junk control (C18).
- **`labels.ts` is out of date:** it names retired `bot_v10_ou` and
  `bot_sharp_forward_test_v1`, and has no label for `bot_inplay_slowstate_v1` (the live
  arm), `bot_unified_gate_1x2_paper_v1` or the three consensus bots.
- **In-play mc-CLV is displayed** (−43.7% for the live arm) although the rig's design says
  CLV is inadmissible in play — needs a "hit-rate minus de-vigged prob" metric instead.
- **Scoreboard mixes eras.** No split at 2026-09-20 (sharp freshness) or 2026-09-24
  (per-book tight cohorts, #001 orientation-strict matchers, #123 BTTS/AH guard) — the
  sharp triggers' pooled numbers hide a sign flip.
- **"Decision fresh" is 0/0 for the five pick_generator bots** because the generator
  writes no `decision_quote_age_min` (B3 b).
- **`shadow_bets_unique` keeps the EARLIEST row per (bot, match, market, selection)** — for
  the pooled tight bot that means the earliest book wins, and for v10_1x2 the earliest
  timing cohort; worth stating on the page.
- **Real-money state** (paused / not armed / both toggles off) is readable from
  `coolbet_session_state`; the page should lead with it so a green-looking bot is never
  read as "being staked".
- **Pick price is the last clearing price, not the decision price** (upsert, B3 b/c). Any
  "copy this pick" UI shows a price that may have been updated since the pick first fired.

---

### Reproduce

```bash
cd /Users/margussellin/www/odds-intel-engine
# active-bot shadow record, era split, margin-corrected CLV with t
python3 -c "from workers.api_clients.db import execute_query as q; print(q('''
SELECT bot_name, pick_time >= '2026-09-20' post,
       count(clv_margin_corrected) n, avg(clv_margin_corrected) mc,
       avg(clv_margin_corrected)/stddev_samp(clv_margin_corrected)*sqrt(count(clv_margin_corrected)) t
  FROM shadow_bets_unique WHERE bot_retired_at IS NULL AND result IN ('won','lost')
 GROUP BY 1,2 ORDER BY 1,2'''))"
# retired vs active shadow volume, 30 d
#   SELECT (b.retired_at IS NOT NULL), (s.shadow_cohort ~ '^[0-9]{4}$'), count(*)
#     FROM shadow_bets s JOIN bots b ON b.id=s.bot_id
#    WHERE s.created_at > now()-interval '30 days' GROUP BY 1,2;
# fleet money state
#   SELECT placement_paused, real_money_armed FROM coolbet_session_state;
#   SELECT * FROM coolbet_placer_bots;
```
