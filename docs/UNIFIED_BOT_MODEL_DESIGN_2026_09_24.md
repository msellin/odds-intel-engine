# Unified bot model — design (#139, 2026-09-24)

Parent row: **#139 UNIFIED-BOT-MODEL-EPIC** in `PRIORITY_QUEUE.md`. Inputs:
`docs/BOTS_AUDIT_2026_09_24.md` (#137, the 20-bot inventory and 23 drift items) and
`docs/SHADOW_BOTS_AND_CONTROLS_AUDIT_2026_09_24.md` (#138, the control map — feeds phase 3).

## Why

A bot today is one of three kinds **by the table it happens to write** — `simulated_bets`
(the model bots behind /picks), `shadow_bets` (paper bots, incl. timing-cohort copies),
`picks_forward_test` (the pre-registered public sharp / consensus test) — and every page
reads a different subset: `/admin/bots` sees 2 of 20 active bots, `/admin/shadow-bots` shows
the forward-test bots at 0 settled, and the same bot has a different n / ROI / CLV on each.
Each capability has its own switch in its own place (`bots.show_on_picks`, the
`PLACEABLE_BOTS` constant, `coolbet_placer_bots.ui_place_enabled`, the Telegram path,
`maturity_label`). Nobody can answer "what is this bot, what does it watch, is it any good,
what is it allowed to do" in one place.

## The model

**Every bot is the same kind of object**: one row in `bots`, one row in `bot_config` (what it
is), one stream of picks in `bot_ledger` (what it did), one row in `bot_scoreboard` (how it
is doing, on the metric admissible for its family), one row in `bot_capabilities` (what it is
allowed to do).

**Lifecycle / capabilities** (owner's words: "by default they are all just accumulated and
monitored"):

| Capability | Meaning | Default | Today's control (phase 3 moves these behind one switch) |
|---|---|---|---|
| **collect** | generates picks, settled, scored | on for every active bot | `bots.is_active` / `retired_at` (not honoured by every writer — audit B4) |
| **publish** | reaches `/picks`, `/performance`, the public track record and Telegram | off | `bots.show_on_picks`, forward-test arms, Telegram sender |
| **real-money** | a placer may stake real money on it | off (and fleet-wide off today) | `PLACEABLE_BOTS` + `coolbet_placer_bots.ui_place_enabled` + `coolbet_session_state` |

A bot moves **collect → publish → real-money** only on the owner's action, and the page
shows the evidence next to each switch.

**Pre-registered ledgers are never rewritten.** `picks_forward_test` rows, their
`rule_version`s and the junk-anchor control are read by the unified views, never re-labelled
or re-scored in place.

## Data contract — phase 1 (engine: migration 410 + export job)

All views live in `public`, owned by `oddsintel_owner`, readable by `service_role`
(**not** anon — admin only; #072).

### `bot_ledger` (view) — one row per pick, all three sources

| column | type | notes |
|---|---|---|
| `source` | text | `'sim'` \| `'shadow'` \| `'forward_test'` |
| `pick_id` | uuid | the source row's id |
| `bot_name` | text | forward-test arms mapped exactly as `picks_public_all` does (`bot_sharp_1x2_v1`, `bot_sharp_ou_v1`, `bot_consensus_{b,c,d}_v1`); `arm='junk_anchor'` → `control_junk_anchor` |
| `bot_id` | uuid | NULL for forward-test rows / the control |
| `match_id` | uuid | |
| `kickoff` | timestamptz | `matches.date` |
| `pick_time` | timestamptz | sim/shadow `pick_time`; forward test `published_at` |
| `market`, `selection` | text | |
| `odds` | numeric | the recorded decision price (`odds_at_pick` / `odds`) |
| `bookmaker` | text | `recommended_bookmaker` / `bookmaker` |
| `result` | text | normalised: `pending` \| `won` \| `lost` \| `void` \| `push` (forward-test `outcome` NULL → `pending`) |
| `pnl_unit` | numeric | flat 1-unit stake: won → odds−1, lost → −1, void/push/pending → 0 (so every bot is comparable regardless of its stake scheme) |
| `clv_raw` | numeric | `clv` (raw price ratio — shown only as a secondary number) |
| `clv_mc` | numeric | `clv_margin_corrected` where the ledger has it (shadow, forward test); NULL for sim |
| `clv_pinnacle` | numeric | sim: `clv_pinnacle_devig`; shadow: `clv_pinnacle`; forward test: NULL |
| `is_inplay` | boolean | sim `match_minute_at_pick` OR `xg_source` set OR bot `inplay_%` (865 of 1,490 old in-play sim rows carry no minute); shadow `inplay_minute IS NOT NULL` |
| `model_version` / `rule_version` | text | whichever the source carries |

Rules — **one ledger per bot** (ANALYSIS_GOTCHAS §18; corrected 2026-09-24 by the genesis
research, `dev/active/unified-bot-model-genesis/ledgers.md` §6): a bot with any `simulated_bets`
rows is read from `simulated_bets` only (its shadow rows are timing copies — 398 of 531 pre-05-20
ones duplicate the sim pick); every other bot is read from ALL its `shadow_bets` rows, deduped
per (bot, match, market, selection), earliest wins. The first draft excluded clock-time (`HHMM`)
cohorts as "copies" — for 37 shadow-only bots those rows are the only record (11,447 picks).
Sim rows exclude combos; void rows stay. CLV statistics skip |clv| > 1 (§9) and report the
count as `clv_outlier_n`.

### `bot_scoreboard` (view) — one row per bot_name

> **CHANGED 2026-09-25 ([[#159]], migration 433).** `bot_scoreboard` is now a projection of the view
> `bot_performance` — the ONE per-bot ROI/CLV computation that /performance also reads. `roi_unit` = FLAT
> ROI at OUR books' price (`odds_at_pick_live`), `roi_public` beside it = the same at the best price
> available on ALL books (the /performance figure); `clv_pin_*` are REMOVED (legacy `clv_pinnacle_devig`)
> and replaced by `clv_anchor_n / _n_pinnacle / _n_consensus / _mean / _se / _t` (sharp-anchor close); forward-test
> rows count the [[#158]] record. `bot_config.admissible_metric` is `clv_anchor` for every non-in-play family.
> The column list below is the original contract.

`bot_name, display_name, source, scored_rule_version, earlier_version_picks, is_active, retired_at, maturity_label, family,
picks_total, pending, settled (won+lost), won, lost, void, roi_unit (sum pnl_unit / settled),
clv_mc_n, clv_mc_mean, clv_mc_se, clv_mc_t, clv_pin_n, clv_pin_mean, clv_pin_t,
clv_pin_se, clv_outlier_n, first_pick_at, last_pick_at, picks_7d, settled_7d` — `family` joined from `bot_config`.
**Pre-registered bots are scored on their current `rule_version` only** (a rule change is a new
population, migration 346); earlier versions stay in the ledger, counted in
`earlier_version_picks`, never pooled — which also drops the 8 `+DEGENERATE_JUNK_DAY1` control rows.

### `bot_config` (table, written by `scripts/export_bot_config.py`, daily + on deploy)

`bot_name pk, family, description, ledger, writer_job, cadence, markets text[],
prob_source, edge_floor text, edge_floor_source text, odds_min numeric, odds_max numeric,
gates jsonb ([{name, value, source}]), books text[], books_source, anchor,
placeable bool, published bool, telegram bool, admissible_metric text, exported_at`.
Built from the code that actually runs (BotConfig list in `pick_generator`, `bot_configs`,
`TRIGGER_CONFIGS`, `daily_pipeline_v2.BOTS_CONFIG`, `bot_registry.py`, the forward-test
constants), not from docs. A bot with no resolvable config gets a row with
`family = 'unknown'` — never silently missing.

**Families and their admissible metric** (the page shows only this one as the verdict):

| family | bots (today) | admissible metric |
|---|---|---|
| `model_sim` | `bot_v10_1x2`, `bot_high_roi_global_v2` | `clv_pinnacle` (de-vigged Pinnacle close) |
| `model_shadow` | `bot_coolbet_1x2_model_v1`, `bot_coolbet_ou_model_v1`, `bot_ou35_model_v1`, `bot_unified_gate_1x2_paper_v1` | `clv_mc` |
| `sharp_trigger` | 4 per-book triggers, `bot_trigger_1x2_sharp_tight_v1` | `clv_mc` |
| `sharp_generator` | `bot_trigger_1x2_sharp_v1`, `bot_trigger_ou_sharp_v1` | `clv_mc` |
| `inplay` | `bot_inplay_slowstate_v1`, `_afctl_v1` | `lift` (hit rate − de-vigged implied) — **no CLV, no min-odds** |
| `forward_test` | `bot_sharp_{1x2,ou}_v1`, `bot_consensus_{b,c,d}_v1` | `clv_mc` (pre-registered) |
| `control` | `control_junk_anchor` | `clv_mc` (the noise floor the forward test is read against) |

### `bot_capabilities` (view)

`bot_name, collect (is_active AND retired_at IS NULL), writing_7d (picks in the last 7 days —
retired bots keep collecting ON PURPOSE, owner decisions 2026-05-20 and 09-18, so "retired" ≠
"stopped"), publish (from bot_config.published /
show_on_picks), telegram, place_capable (bot_config.placeable), place_enabled
(coolbet_placer_bots.ui_place_enabled), fleet_placement_paused, fleet_real_money_armed`.

## Phases

1. **Data + page (no behaviour change)** — migration 410 (`bot_ledger`, `bot_scoreboard`,
   `bot_capabilities`, `bot_config` table), `scripts/export_bot_config.py` + a daily job;
   `/admin/bots` rebuilt on these: one row per active bot grouped by family (settings,
   capability chips, last pick, n, ROI, the family's admissible metric with t and a plain
   verdict), a detail drawer (full config with sources, recent picks), a Retired archive tab,
   a fleet status line. Old page removed.
2. **Shadow-bots page** becomes "today's picks to act on" (or merges into the bots page) and
   stops computing its own per-bot records.
3. **Switches** — publish / real-money toggles on the bots page (superadmin, audited), and
   the publishers / placers read the one flag. Needs #138's control map; forward test stays
   pre-registered.
4. **Clean-up** — per owner decisions: retirements, retired-bot shadow writes,
   `SHADOW_MODEL_VERSION`, the unified-gate calibrator.

Then **#140** (pre-registered slice analysis) runs on `bot_ledger`.

## Phase 5 — DB-level unification (owner, 2026-09-24: "this task also means db level refactor, cleanup, unified table for bets, bots, predictions")

The phase-1 views are the **schema of the target tables** — building them first proves the
shape against every existing row before any writer moves.

**Target tables**

| table | replaces | notes |
|---|---|---|
| `bots` (kept, extended) | `bots` + `coolbet_placer_bots` + code constants (`PLACEABLE_BOTS`, `BotConfig` lists, `TRIGGER_CONFIGS`, `BOTS_CONFIG`) | adds `family`, `config jsonb` (versioned: `bot_config_versions` keeps every change with a timestamp), capability flags `can_publish`, `can_place_real`, `lifecycle` |
| `picks` (new) | `simulated_bets`, `shadow_bets`, `picks_forward_test` | the `bot_ledger` columns + `stake`, `probability`, `edge`, `anchor_*`, `quote_age_min`, `cohort` (timing copies become a column, not extra rows in a separate life), `immutable bool` (forward-test rows: no UPDATE of decision fields — enforced by trigger) |
| `real_bets` (kept) | — | gains `pick_id → picks.id` |
| predictions | `predictions` (+ model-version tables) | audited in step 5a; unify only where two tables hold the same thing |

**Phase 0 — genesis first (owner, 2026-09-24: "you must understand 101% how everything works
today and why every table was created initially, then you know how to merge it").** The
target tables above are a *sketch*, not a decision. Before any `picks` / `bots` schema is
fixed, three archaeology write-ups trace each table's creating migration, every later column
and the reason for it, every deliberate separation ("why this is NOT in the existing table"),
and the incidents tied to it:
`dev/active/unified-bot-model-genesis/{ledgers,bots-and-controls,predictions}.md`. Each
separation becomes an **invariant** the merged design must preserve or explicitly retire with
the owner's OK — e.g. a placement safety layer that exists because of an incident is not
folded into one switch just because one switch is tidier. These write-ups, today's
reader/writer inventory (5a) and the phase-1 views reconciling against every live row are the
three inputs; the Phase 5 schema is rewritten from them and reviewed by two agents before a
single table is created. Phase 1 (read-only views + the page) proceeds in parallel because it
changes no data and commits to no target schema.

**Migration protocol (each step reviewed by 1–2 agents before the next):**
5a. Read-only inventory of every reader and writer of the old tables (engine jobs, scripts,
    web queries, views, smoke tests) and of `predictions`. 5b. Create `picks` + backfill from
    the three ledgers; reconciliation query must match row-for-row (counts, P&L, CLV) per bot.
5c. Dual-write: each writer writes `picks` alongside its old table, one writer per step, with
    a daily reconciliation check. 5d. Readers switch to `picks` (pages, settlement, CLV jobs,
    publishers). 5e. Old tables become read-only compatibility views; dropped after two weeks
    with zero readers. The pre-registered forward test is copied as immutable rows and its
    summary views are re-pointed only after the reconciliation proves equality.

**Review rule:** every sub-step ships with a smoke test and at least one independent review
agent (two for anything that writes or migrates data), and nothing proceeds on an open
defect.
