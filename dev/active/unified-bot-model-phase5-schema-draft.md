Parent row: #139 in PRIORITY_QUEUE.md

# Phase 5: target schema draft (for two independent reviewers, before any table is created)

**Status:** DRAFT, 2026-09-24. This is design only. No migration, no DB write and no code change
has been made. The owner decisions are in `dev/active/unified-bot-model-phase5-decisions.md`. Every
place that depends on one of them is marked **[D1]…[D6]**. **[VIP]** marks overlap with the #148
paid-tier work now being built.

**Inputs** (read in this order): `docs/UNIFIED_BOT_MODEL_DESIGN_2026_09_24.md` §Phase 5;
`docs/UNIFIED_BOT_MODEL_5A_INVENTORY.md`; `dev/active/unified-bot-model-genesis/{ledgers,bots-and-controls,predictions}.md`;
migrations 410, 411, 413–419.

**Invariant ids used here**
- `LG-n`: ledgers.md §6, items 1–20.
- `In`: bots-and-controls.md §4, I1–I26.
- `P*`, `PR-L*`, `F*`, `C*`, `T*`, `B1`, `PP1`: predictions.md §5. Their `L1–L3` are renamed `PR-L1–3` so they do not collide.
- `Hn`: inventory §0 headline findings 1–9.
- `NMn`: ledgers.md §7, "what a naive merge would break", items 1–21.

**Facts re-verified for this draft (SELECT-only, 2026-09-24 ~20:30 UTC)**
- **No UUID collides** across `simulated_bets`, `shadow_bets` and `picks_forward_test` (0/0/0 pairwise), so keeping the source ids as `picks.id` is safe.
- **Row counts:** sim 4,675 (2 pending: the first EV5/EV8 picks); shadow 167,951; forward test 762; bots 112, 24 not retired.
- **The EV bots' stored `edge_percent` is not the quantity they gate on.** The EV5/EV8 row stores `edge_percent = 0.0809` (probability points, re-derived by `store_bet`), while the bot's gate and label use EV = p·odds − 1 = **0.157**. This is LG-12 happening live.
- **The EV bots' `model_version` is the global tag.** The row carries `v20260712`; the pricing model appears only in `reasoning` (`[combined r1x2_comb_v1]`). This is I22/PR-L2 happening live.
- **Sim `clv_pinnacle` changed meaning in place on 2026-09-07.** Before 09-07 it is raw; since then it is de-vigged (settlement.py:3930 comment). Since 04fff33f the settler writes `clv_pinnacle_devig` too. Pre-09-07 settled sim rows: 4,194, of which 2,913 have `clv_pinnacle` and 3,010 have `_devig`, and 2,216 disagree. After 09-07: 96 settled, 95 with both, 11 disagreeing (unexplained; reviewer item R-Q7).
- **Rows with no `odds_at_pick_live`:** 661 of 4,290 settled sim rows and 6,547 of 166,389 settled shadow rows.
- **The shadow upsert writers** (`pick_generator.py:329`, `pick_trigger_matcher.py:208`, `ou35_model_shadow.py:142`) rewrite `odds_at_pick`, `odds_at_pick_live`, `calibrated_prob`, `edge_percent`, `recommended_bookmaker` and **`model_version`**. They leave `pick_time` alone, so `pick_time` stays the first sighting.

---

## 0. Design principles (each traces to invariants)

1. **Keep every separation that exists today, but make it a named column plus a DB rule instead of a
   choice of table.** Today the table choice gives three guards: OWN picks are never public (I13),
   the forward test never mixes into bot cohorts (I17, LG-1), and sim is the only ledger that feeds
   a bankroll (I19). Each must come back as an explicit constraint *before the first cross-source row
   is copied in*.
2. **Restatements are additive** (LG-10). The backfill copies stored values verbatim, including
   known-bad history (the 182 quarantine voids with non-zero pnl, the 410 NULL-pnl shadow voids,
   `edge_percent` rounding, and the pre-09-07 raw `clv_pinnacle`). Normalised values are computed in
   views or placed in new, clearly named columns.
3. **One definition, in one place, for every derived rule:** the dedup rule (earliest `pick_time`,
   LG-8), the arm→bot mapping (R10), the public cohort [D6], and the EV label [VIP]. Each becomes one
   SQL function or view that everything else reads.
4. **Real money and the control plane are not part of this merge.** `real_bets`,
   `coolbet_session_state`, `coolbet_placer_bots`, `control_changes` and `placer_heartbeats` were
   rebuilt with guards in migration 413 on the same day this draft was written. Moving them now would
   violate bots §5.7 (a moved switch loses its fail mode). Phase 5 changes only **where the placers
   read picks from**.
5. **Predictions stay separate** (predictions.md §7). `picks` carries its own frozen probability
   snapshot (PR-L1) and a nullable `prediction_id` for a future sub-epic 5P.

---

## 1. What stays separate, and why

| Object | Verdict | Why (invariant) |
|---|---|---|
| `real_bets` | **Keep.** Gains `pick_id uuid REFERENCES picks(id) ON DELETE RESTRICT`. `simulated_bet_id` and `shadow_bet_id` are kept as deprecated columns until 5e. | Real money: rows are never deleted, tri-state `placed_real`, one real row per pick (I9, LG-19). The 09-13 wrong-FK incident is the template for the cut-over risk (bots §5.5). RESTRICT (instead of SET NULL) makes a placed pick undeletable. |
| `coolbet_session_state` (fleet KILL / ARM / publishing / sweeping) | **Keep, untouched** | I3, I4, I5, I8, I10, I24, bots §5.7 (fail-open vs fail-closed defaults). |
| `coolbet_placer_bots` (per-bot real-money eligibility plus lock, mig 413) | **Keep, untouched.** The design doc's "`bots` replaces `coolbet_placer_bots` + `PLACEABLE_BOTS`" is **superseded**, see §8. | I1 (as re-cast by 413), I2, I26; guards `coolbet_placer_bots_guard`, `_no_delete`, `_no_truncate`. |
| `control_changes`, `placer_heartbeats` | Keep | Append-only audit (413); I24. |
| `predictions`, `model_calibration`, `model_versions`, MFV, `match_signals` | Keep; out of scope (sub-epic 5P) | P1–P6, F1–F7, C1–C2, predictions §6.1–6.4. |
| `published_picks` | **[D4]** Recommended: leave alone | PP1. It is not a bot ledger, and no web reader uses it. |
| `picks_board` | Keep; must never be read as a ledger | Migrations 354/355, smoke PICKS-BOARD-SETTLEMENT. |
| `pick_triggers`, `candidate_funnel`, `book_fair_probs` | Keep | T1, T2, B1. Working sets and telemetry, not picks. |
| `leg_clv_sharp` | Keep as a side table. `leg_id` is already the pick UUID. `ledger` becomes derivable from `picks.source_ledger`; drop that column in 5e. | Migration 386: one sharp-CLV definition, with settlement untouched. |
| `user_pick_marks` | Keep. `pick_id` remains a soft reference (all 715 rows hold shadow ids, which are preserved). | Migration 278: "no FK so marks survive rows migrating tables". |
| `user_picks` | **[D5]** Recommended: retire separately (6 rows) | Not a bot table (bots §2.11). |
| `bot_config` (export, mig 410) | Keep as a **mirror with a drift test**, not a control | bots §5.6 (a stale export must never place or publish). |
| `coolbet_placement_attempts`, `price_verifications`, `bet_telegram_alerts`, `manual_placement_queue` | Keep. Their pick references stay valid because the UUIDs are kept. The hard FKs (`price_verifications.shadow_bet_id`, the `bet_telegram_alerts` / `manual_placement_queue` `simulated_bet_id`) are re-pointed to `picks(id)` in 5e. | LG-19, NM13. |

---

## 2. Extended `bots`

Every existing column is kept: `name` (identity, I18 / LG-16), `display_name` (display only),
`strategy`, `description`, `strategy_description`, `is_active`, `retired_at`, `retired_reason`,
`maturity_label` with its CHECK (I15), `show_on_picks` (kept until 5e as a compatibility column,
then derived), `starting_bankroll` and `current_bankroll`. The `bots_maturity_retired_invariant` and
`trg_bots_updated_at` triggers are kept.

**New columns**

| Column | Type / values | Default | Keeps | Notes |
|---|---|---|---|---|
| `family` | text, CHECK in the 410 family list (`model_sim`, `model_shadow`, `sharp_trigger`, `sharp_generator`, `inplay`, `forward_test`, `control`, `unknown`) | `'unknown'` | Design §families | Backfilled from `bot_config.family`. Written by migration only. 14 bots are `unknown` today; they must be resolved in 5b (R-Q1). |
| `audience_class` | enum `customer_eligible` \| `internal` \| `pre_registered` \| `control` | `'internal'` | **I13, I17, LG-1, LG-2, I10** | **The replacement for "which table it writes".** Backfill: bots with any `simulated_bets` rows → `customer_eligible`; forward-test handles → `pre_registered`; `control_junk_anchor` → `control`; all others → `internal`. Changes by migration only: guard trigger, same pattern as 413 (`oddsintel.migration`). |
| `pick_ledger` | enum `sim` \| `shadow` \| `forward_test` | derived at backfill | **LG-6** (one ledger per bot) | Which source's rows are this bot's picks. During the mirror period a shadow row for a `sim` bot is routed to `pick_observations`, never to `picks` (§4). |
| `publish_tier` | enum `none` \| `public` \| `vip` | `'none'` | **I11, I14, [VIP], [D6]** | Replaces `show_on_picks` as the publish decision. CHECK: `publish_tier <> 'none'` ⇒ `audience_class = 'customer_eligible'` (I13, the same rule 413 enforces as "only sim-ledger bots can be shown on /picks"). For `pre_registered` bots it is a **read-only mirror** of `PUBLISHED_ARMS` / grade, set by migration (I16; bots §6). Changed only through a new `admin_set_control(control => 'publish_tier')` branch, which extends 413's `show_on_picks` branch with a reason, a typed confirmation and an audit row. **[VIP] #148 is adding a `vip` flag now. Coordinate so that it is this column (or maps 1:1 onto it), not a second boolean.** |
| `collect_state` | enum `collecting` \| `collecting_archived` \| `stopped` | `'collecting'` | **I20, I21**, bots §5.3 | Backfill: retired bots → `collecting_archived` (keeps SHADOW-RETIRED-OK). Every writer reads it; the four bare `_bot_id` lookups (corners, team_total, fh, ou35) are fixed to read it. `stopped` needs an owner decision per bot. |
| `locked_population` | boolean | false | **LG-4, LG-5, LG-3** | TRUE for pre-registered populations: the forward-test handles, `bot_trigger_1x2_sharp_tight_v1`, `bot_unified_gate_1x2_paper_v1`, the two sharp-anchor-sweep per-book bots, and **[VIP] `bot_combined_1x2_ev5_v1` / `_ev8_v1`** (pre-registered in #141 B4). Stamps `picks.immutable` at insert. Changed by migration only. |
| `prereg_ref` | text | NULL | LG-4 | Path of the pre-registration doc plus its current `rule_version`. |
| `stake_scheme` | enum `kelly_bankroll` \| `flat10` \| `unit` | per ledger | **I19**, NM7, NM18 | Only `kelly_bankroll` bots feed `current_bankroll`. |
| `config` | jsonb | NULL | bots §5.6 | Mirror of `bot_config`, written by `export_bot_config.py` with `config_exported_at`. **Not read by any placer or publisher.** Drift test: smoke `BOT-CONFIG-MIRROR-FRESH`. |
| `config_exported_at` | timestamptz | NULL | | |

**Config versions:** reuse `bot_config_history` (16 rows, from mig 281). Do not create a second
table. `export_bot_config.py`, which already runs daily at 03:40, appends a row whenever
`md5(config)` changes. That gives the table the writer it never had (bots §2.10, §6).

**`ensure_bots`** (supabase_client.py:43) must insert new bots as `audience_class='internal'`,
`publish_tier='none'` and `maturity_label='experimental'` only (bots §5.10, R13).

---

## 3. `picks`

> **Naming, for reviewers:** consider `bot_picks` instead of `picks`. The prefix `picks` is already
> used by `picks_board`, `picks_forward_test`, `picks_public_all`, `picks_forward_test_public` and
> the `/picks` route, so a grep for "picks" is useless and the inventory warns about name collisions
> (H9). This draft says `picks` for readability.

**Grain:** one row per pick, meaning **one per (bot, match, market, selection)**. This holds only
under **[D1] option B**; §3.9 gives the variant for option A. Re-evaluations go to
`pick_observations` (§4.1).

### 3.1 Identity and provenance

| Column | Type | Source (sim / shadow / forward test) | Keeps |
|---|---|---|---|
| `id` | uuid PK | source id, verbatim | NM13, H6, LG-19. Zero collisions verified. |
| `source_ledger` | enum `sim` \| `shadow` \| `forward_test`, NOT NULL | constant per source | Provenance; makes `leg_clv_sharp.ledger` derivable; lets 5e compatibility views reproduce each old table exactly. |
| `bot_id` | uuid NOT NULL → `bots(id)` **ON DELETE RESTRICT** | bot_id / bot_id / **derived** (see right) | LG-15, I18, R10. For forward-test rows it is set **only** by a BEFORE INSERT/UPDATE trigger that calls one function, `forward_test_bot_id(arm, grade, market)`. A direct write is refused. This replaces the three drifting copies of the arm→bot CASE (inventory §1.5). **Reviewer attention (LG-15 / NM11):** migrations 380/402 said "split in the views, not the ledger". This keeps the rule in ONE function and the stored value is only a cache of it, so a grade re-tier re-derives it and logs to `pick_change_log`. If a reviewer rejects storing it, fall back to `bot_id` NULL for forward-test rows plus the same function inside every view. |
| `match_id` | uuid NOT NULL → `matches` **ON DELETE RESTRICT** | ✓ | R7: today a match delete cascades to shadow and sim picks; `cleanup_match_dupes` must re-point first. |
| `market`, `selection` | text NOT NULL | ✓ | Same canonical vocabulary (inventory §1.1). CHECK against the canonical list, **fully VALIDATED, never NOT VALID** (NM15, I23). |
| `cohort` | text NOT NULL | `shadow_cohort` family tag or named window / `timing_cohort` / `arm` | NM14. The writer family. |
| `timing_slot` | text NULL, CHECK `^[0-9]{3,4}$` | HHMM when the primary row is itself an HHMM row (13,365 keys where the copy *is* the pick, H1) | LG-7, NM1 |
| `arm`, `grade`, `grade_reasons` | forward test only | —/—/✓ | LG-2, I16. **Grade is not in any unique key** (NM12). |
| `rule_version` | text | NULL / NULL / ✓ | **LG-5**. Kept separate from model identity (inventory §3.1). |
| `pipeline_model_version` | text | `model_version` / `model_version` / NULL | PR-L2, PR-L3. The global tag as stored today (history), **not FK-able**. |
| `pricing_model_version` | text | parsed / parsed / `rule_version` | **I22, PR-L2**; coordinator item (2). This is the model that actually priced *this* pick, e.g. `r1x2_comb_v1`, `v20260830`, `pinnacle_shin_devig+selcal1`. Backfill: parse the `[combined r1x2_comb_v1]` / `[rating …]` prefix from `reasoning`; for the pre-#141 families, map from `prob_source`. **NULL where it cannot be proven. Never guess** (predictions §6.11). New rows: NOT NULL, enforced by an insert trigger for `created_at >= cutover`. The #141 session is fixing the tag in the writer; this column is where the fix lands. |
| `calibrator_rev` | text NULL | `+selcal1` suffix split out | PR-L3, C2 |
| `prob_source` | text NOT NULL | from bot family and config (`model_cal`, `rating_1x2`, `combined_1x2`, `pinnacle_shin`, `book_devig:<book>`, `consensus:N`, `inplay_book`) | inventory §3.1 ⚠ on `probability` |
| `prediction_id` | uuid NULL | — | 5P hook only. No FK until prediction history exists (P1). |
| `pick_time` | timestamptz NOT NULL | pick_time / pick_time / published_at | R4: sim writes a **naive** `datetime.now()`. Verify the zone against `created_at` before the backfill (R-Q2). |
| `created_at`, `updated_at` | timestamptz | ✓ / ✓ / published_at | |

### 3.2 Decision (frozen when `immutable`)

| Column | Type | Source | Keeps |
|---|---|---|---|
| `odds` | numeric(8,4) > 1 | odds_at_pick / odds_at_pick / odds | LG-10. **Mirrors the source exactly during 5c**, including the upsert writers' overwrite (R8), so reconciliation is exact and the pre-registered `sharp_tight` population is not redefined mid-test (LG-5). |
| `odds_first` | numeric | = `odds` at backfill (the true first value is already lost for upserted rows) | R8, LG-10, additive. Set on INSERT, never updated. This is the price `real_bets` provenance should cite. |
| `odds_exec` | numeric NULL | odds_at_pick_live / odds_at_pick_live / = odds | **[D3]** the "actually on offer" price |
| `bookmaker` | text | recommended_bookmaker / recommended_bookmaker / bookmaker | |
| `book_universe` | enum `accessible` \| `all_books` | accessible / accessible / all_books | ledgers §4.3: the forward test prices across every book by the owner's 09-14 ruling. Without this column the two "best price" figures would be compared as if they were the same thing. |
| `stake`, `stake_scheme` | numeric > 0; enum | Kelly € / 10.00 / **1** | NM7, I19 |
| `kelly_fraction` | numeric NULL | ✓ / ✓ / — | |
| `probability_raw` | numeric [0,1] NULL | model_probability / model_probability / NULL | PR-L1. **Frozen at INSERT** from cutover on; `news_checker`'s later rewrite goes to `extra.news_adjusted_prob`. Backfill copies the current value, with `extra.prob_raw_rewritten = news_triggered`, because the pre-rewrite value is lost (H-inventory §1A note). |
| `probability` | numeric [0,1] NOT NULL | calibrated_prob / calibrated_prob / p_sharp | PR-L1, with `prob_source` saying what it is |
| `edge` | numeric(8,6) | edge_percent / edge_percent / edge | **LG-11**: history copied verbatim, including the pre-09-22 `numeric(5,2)` rounding. |
| `edge_kind` | enum `prob_points` \| `expected_roi`, NOT NULL | sim → `prob_points` (99.8–100% per month); shadow → `expected_roi` for the 7 ROI-form bots (`bot_pin_1x2_home_v1`, `bot_coolbet_value_v1`, `bot_sweep_ou25_v1`, `bot_sweep_ou35_v1`, corners / team-total / 1H paper), otherwise `prob_points`; forward test → `expected_roi` | **LG-12, NM5.** Rows that match neither form within 1e-4 of `probability − 1/odds` or `probability·odds − 1` are listed in the backfill report rather than forced (R-Q3). |
| `ev_at_pick` | numeric **GENERATED ALWAYS AS** (`probability * odds - 1`) STORED | — | **[VIP]** T2 ("derived, not stored": this is a generated column, not a writer-supplied number). This is the quantity the EV5/EV8 bots gate on (0.157 on the first pick, against a stored `edge` of 0.0809). ⚠ It uses `odds`, which upserts can rewrite. For locked populations the value is frozen together with `odds`. |
| anchor group: `anchor_bookmaker`, `anchor_odds` jsonb, `anchor_overround`, `anchor_quoted_at`, `odds_quoted_at`, `alignment_gap_minutes`, `quote_age_min`, `pair_gap_hours`, `kickoff_at_pick` | NULLs where the source has none | forward test ✓; shadow `decision_quote_age_min`, `pair_gap_hours`; `kickoff_at_pick` = forward test `kickoff_at`, **NULL for sim/shadow history** (NM16: matches.date moves on reschedule; do not fabricate) | **LG-14.** Writers stamp `kickoff_at_pick` from cutover on. |
| `is_inplay` | boolean NOT NULL | `xg_source IS NOT NULL OR match_minute_at_pick IS NOT NULL OR bot LIKE 'inplay_%'` / `inplay_minute IS NOT NULL` / false | **LG-20, NM6** (865 rows) |
| `inplay_minute`, `inplay_score_home`, `inplay_score_away` | | match_minute_at_pick, score_*_at_pick / inplay_* / — | |
| `meta_clv_score` | numeric NULL | ✓ / ✓ / — | ⚠ no meta-model version is stored. Add `meta_model_version` NULL (R-Q4). |
| `published_tier` | enum `none` \| `public` \| `vip`, NOT NULL | see §3.5 | **I14, I11, LG-17, [VIP], [D6]** |
| `immutable` | boolean NOT NULL | false / false / true; plus `bots.locked_population` | LG-3, LG-4, H3 |
| `decision_snapshot` | jsonb NULL | — / — / the verbatim original row (`to_jsonb(picks_forward_test.*)`) | LG-3, LG-14. The reproducible pre-registered record (inventory §3.3). |
| `extra` | jsonb | sim analytics (`reasoning`, `dimension_scores`, alignment fields, `news_*`, `lineup_confirmed`, `odds_at_open`, `odds_drift`, `xg_source`, `pin_cross_drift_shadow_flag`, `strategy_profile`, `ai_explanation`), `shadow_run_id` | inventory §3.3. Readers keep working through the compatibility view. |

Dropped (not copied): `af_*_prob`, `af_agrees` (0 rows ever); `combo_legs`, `combo_size`,
`system_type` (0 sim rows; `real_bets` keeps its own); `timing_cohort` as "assigned window" (all
'all' since 05-20; the raw value is kept in `cohort` for sim rows).

### 3.3 Settlement and CLV (mutable, including on immutable rows, each change logged)

| Column | Source | Keeps |
|---|---|---|
| `result` | new enum `pick_result` {pending, won, lost, void, push, half_won, half_lost}. sim/shadow copied **as is** (push is stored as void, LG-10); forward test `COALESCE(outcome,'pending')` | NM9. Sim and shadow writers keep writing `void` for pushes until a separate decision says otherwise. |
| `pnl` | native, verbatim (182 quarantine voids non-zero, 410 shadow voids NULL, forward-test 2-dp) | **R1, NM7, NM8.** Not normalised. |
| `pnl_unit` | **view-computed**, not stored: won → `odds_basis − 1`, lost → −1, anything else → 0 | [D3] picks `odds_basis`. Recommended: `COALESCE(odds_exec, odds)`, plus a `basis_fallback` flag. |
| `settled_at` | sim ✓ (7 rows) / NULL (shadow has none, NM17) / ✓ | Trigger `set_pick_settled_at` (a port of 366) for every source from cutover. History NULL means "settled before tracking". |
| `void_reason` | ✓ / ✓ / NULL | **LG-18.** The `quarantine%` prefix is protected: the resettle and regrade paths skip it (smoke QUARANTINE-VOIDS-SURVIVE-THE-RESETTLER). |
| `closing_odds`, `closing_bookmaker`, `closing_minutes_before_ko`, `closing_margin` | ✓ | |
| `clv_raw` | `clv` (own-book raw) | LG-13 |
| `clv_live` | ✓ / ✓ / — | |
| `clv_mc` | — / clv_margin_corrected / clv_margin_corrected | LG-13. Sim has none; computing it in the backfill needs `closing_margin`. Optional (R-Q5). |
| `clv_pinnacle_raw` | sim `clv_pinnacle` **for rows settled before 2026-09-07 only** / — / — | **LG-13, NM4, [D2]** |
| `clv_pinnacle_devig` | sim `clv_pinnacle_devig`, else sim `clv_pinnacle` for rows settled on or after 09-07 / shadow `clv_pinnacle` / — | [D2] option A: recompute missing pre-09-07 values from `odds_snapshots` where a Pinnacle close survives; otherwise leave NULL |
| `clv_pinnacle_live` | ✓ / ✓ | Verify that both paths de-vig (settlement.py:3948 and :4150 both do since 09-07; earlier sim rows need checking, R-Q6). |
| `bankroll_after` | sim only | NM18. Kept (sim only, compatibility) and scheduled for retirement in 5e (PERF-NO-HIGH-WATER-BANKROLL-COLUMN). |

### 3.4 Constraints and indexes

- `UNIQUE (bot_id, match_id, market, selection)`. This is **[D1]-dependent** and only valid under option B.
- `UNIQUE (match_id, market, selection, arm) WHERE source_ledger = 'forward_test'` keeps claim-before-send (**LG-17**) and the tie rule "live claims first".
- CHECKs, all **VALIDATED** (NM15; the 374 lockout came from a NOT VALID CHECK):
  - `odds > 1`
  - `stake > 0`
  - `probability BETWEEN 0 AND 1`
  - `timing_slot` shape
  - `source_ledger = 'forward_test'` ⇔ `arm IS NOT NULL`
  - `stake_scheme = 'unit'` ⇔ `source_ledger = 'forward_test'`
  - `is_inplay` ⇒ no CLV is used (enforced in the views, not as a CHECK, because the settler writes CLV today)
- Indexes:
  - `(bot_id, pick_time)`
  - `(match_id)`
  - `(match_id) WHERE result = 'pending'`
  - `(source_ledger, pick_time)`
  - `(published_tier, pick_time) WHERE published_tier <> 'none'`
  - `(pricing_model_version, pick_time)`
- **No `cohort` allow-list CHECK.** Silent rejections have happened three times (mig 112, 116, 362). Instead, writers must record their failure count (I23), and smoke `PICKS-WRITER-FAILURES-VISIBLE` checks it.

### 3.5 Publishing: `published_tier` stamped per pick [VIP] [D6]

- A **BEFORE INSERT** trigger sets `published_tier` from the bot:
  - `customer_eligible` bot → `bots.publish_tier`;
  - `pre_registered` → `public` if `arm` is in the published list and `grade <> 'D'` (the SQL twin of `PUBLISHED_ARMS`, I16), otherwise `none`;
  - `internal`, `control` → `none`.

  It **refuses** a supplied value that disagrees. Changing a bot's tier later affects **future picks
  only**, so switching a bot off cannot hide its past record (I12's intent, restated under [D6]).
- **One decision drives both the channel and the page** (I14, the Ludogorets incident). The Telegram
  senders (`coolbet_signaler` public model arm, the publisher, and the #148 VIP/private sender) read
  `picks.published_tier`. They stop reading `maturity_label='calibrated'`. The views read the same
  stamp.
- **Deliveries:** new append-only table `pick_deliveries(pick_id → picks ON DELETE RESTRICT, channel
  enum {public_channel, vip_channel, operator_dm, pro_dm}, message_id, sent_at, PK (pick_id, channel))`.
  - It replaces `simulated_bets.signal_message_id` and `picks_forward_test.telegram_message_id`, which are backfilled into it (inventory §3.1 ⚠ "different channels"). The ids go into separate named channels.
  - It keeps claim-before-send: the pick row must exist before any delivery row (LG-17).
  - It removes the forward-test "NULL → value once" immutability exception.
  - **[VIP]** routing per tier is a delivery rule: `vip` → `vip_channel` only, never `public_channel`. A CHECK via trigger enforces `published_tier` against `channel`.
  - It also makes #148's second leak **visible**: `send_telegram_to_users` DMs every new bet of any bot, including experimental ones, to Pro/Elite users. This becomes `pro_dm` rows, so it is auditable, and it can be gated on `published_tier`.
- **EV label [VIP]:** view-derived only. `CASE WHEN ev_at_pick >= 0.08 THEN 'EV8' WHEN ev_at_pick >= 0.05 THEN 'EV5' END`, in one function `pick_ev_label(ev)`. Nothing new is stored, per the owner.
- **Public views** (no base-table grant, LG-1, #072). All are recreated over `picks`, keeping the **same names and column lists** as today:
  - `picks_public_all`
  - `picks_forward_test_public`
  - `picks_forward_test_summary`
  - `picks_forward_test_summary_by_market`

  One new public view:
  - `picks_public_record`: the single public cohort [D6]. It admits `published_tier = 'public'`, **or** `published_tier = 'vip' AND result <> 'pending' AND match finished`, and in every case `NOT is_inplay` and not postponed.

  Every public view **names what it admits**: arms by name, junk and grade D excluded by name, and VIP pending picks excluded explicitly (LG-2, I16; migrations 344/371).
- **[VIP] pending-pick leak:** `picks` has no anon grant and no permissive RLS. But the leak #148 found is on **`simulated_bets` today**: anon SELECT plus `Public read USING (true)`, and `/performance` ships pending bets to browsers. **#148 must close it on `simulated_bets` before launch; Phase 5 cannot close it early.** The 5e compatibility view `simulated_bets` must be created **without** the anon grant, and before 5e every anon reader of `simulated_bets` (`engine-data.ts` et al.) must already have moved to `picks_public_record` (d5).

### 3.6 Triggers on `picks`

| Trigger | When | What it does | Keeps |
|---|---|---|---|
| `picks_derive` | BEFORE INSERT/UPDATE | Sets forward-test `bot_id`; stamps `published_tier` and `immutable`; sets `odds_first` on INSERT; refuses a new-row `pricing_model_version` NULL after cutover | R10, I14, LG-4, I22 |
| `picks_one_ledger_per_bot` | BEFORE INSERT | Refuses a row whose `source_ledger ≠ bots.pick_ledger` (routes it to `pick_observations` when called from the mirror) | **LG-6** |
| `picks_audience_guard` | BEFORE INSERT/UPDATE | `published_tier <> 'none'` only when `bots.audience_class IN ('customer_eligible','pre_registered')` | **I13** (the structural guard that the table split used to give) |
| `picks_immutable` | BEFORE UPDATE | When `OLD.immutable`: rejects any change to §3.1/§3.2 decision columns (match, market, selection, odds, odds_first, bookmaker, probability*, edge*, anchor group, arm, rule_version, pricing_model_version, pick_time, kickoff_at_pick, stake). `grade` may go NULL → value once; a re-tier needs `oddsintel.migration`. Settlement columns are allowed. | **LG-3, H3, inventory §3.4** |
| `picks_no_delete` | BEFORE DELETE / TRUNCATE | Refuses when `immutable` or when a `real_bets` row references the pick; otherwise requires `oddsintel.migration` | LG-3, I9. Today's cleanup scripts delete freely (`backfill_canonicalize_vocab`, `cleanup_ou_*`); from now on they must run as a migration or be retired (R-Q8). |
| `picks_change_log` | AFTER UPDATE | Writes an append-only `pick_change_log(pick_id, changed_at, actor, column, old, new)` row for **every** change to result, pnl, void_reason, grade, bot_id or any CLV column | **R11** (a regrade clears then re-settles, which must be visible, not look like tampering); LG-10 |
| `set_pick_settled_at` | BEFORE UPDATE | Port of 366 | NM17 |
| (not ported) `trigger_notify_inplay_bet_fired` | — | **Dropped.** Its in-play consumer is dormant (in-play betting was retired 08-21). | inventory §1.3 |

### 3.7 Operator state (split out, because it is mutable)

`pick_operator_state(pick_id PK → picks, signaled_at, user_placed_at, user_skipped_at,
admin_offered_at)`, backfilled from sim. The web Telegram webhook writes `user_placed_at` on
**sibling rows**; that logic moves to this table. `pick_generator.py:410` excludes marked picks
("a human action changes what a bot sources", bots §2.11) and must read this table after d3.
`admin_offered_at` has no live writer any more, because `/admin/place` was deleted (§8).

### 3.8 Grants

`picks`, `pick_observations`, `pick_change_log`, `pick_operator_state`, `pick_deliveries`:
`REVOKE ALL FROM PUBLIC, anon, authenticated`; `GRANT` to `service_role` and the owner. RLS is ON
with **no permissive policy** (the opposite of the `simulated_bets` / `bots` "Public read" policies;
H7, NM10, R9). **`bots` keeps its anon SELECT** until d5, because `getAllBotsFromDB` reads it; then
it gets a `bots_public` view (#072 pattern). This change must be added to smoke
`ANON-LEAST-PRIVILEGE`.

### 3.9 Variant if [D1] is option A (copies stay in `picks`)

- Add `is_primary boolean NOT NULL`.
- The unique key becomes `(bot_id, match_id, market, selection) WHERE is_primary`, plus `(bot_id, match_id, market, selection, cohort, COALESCE(timing_slot,''))`.
- **Every** view and reader must add `WHERE is_primary`. Smoke `PICKS-READERS-FILTER-PRIMARY` must grep for it, which is the discipline option B makes unnecessary.
- 5b gets 0.5 d shorter; d1–d5 each get about 0.1 d longer for the filter audit.

---

## 4. Supporting tables

### 4.1 `pick_observations` [D1 option B]

This is the timing-copy archive. Each row is a re-evaluation of a pick.

**Columns**
- `id` uuid PK: the source shadow id, kept, because `real_bets.shadow_bet_id`, `leg_clv_sharp` and `user_pick_marks` may point at a copy. **Check this in 5b before choosing** (R-Q9).
- `pick_id` → `picks` NULL. It is NULL for the ~60 sim-bot shadow keys that the sim path never took.
- `bot_id`, `match_id`, `market`, `selection`, `cohort`, `timing_slot`, `observed_at` (= pick_time).
- `odds`, `odds_exec`, `bookmaker`, `probability`, `edge`, `edge_kind`, `pipeline_model_version`.
- Settlement copy: `result`, `pnl`, the CLV columns. These are mirrored because the old settler settles every copy, and reconciliation needs them.

**Rules**
- A copy **never adds to n** (LG-7).
- Rows from retired bots are kept (LG-9). The retired status comes from joining `bots`, never from filtering at the source.
- `UNIQUE (cohort, bot_id, match_id, market, selection)` matches today's `uq_shadow_bet_per_cohort`.
- Grants: private.
- Size: about 157k rows, about 100 MB. BRIN index on `observed_at`.

**Routing rule** (one SQL function `route_shadow_row()`, used by both the backfill and the mirror trigger):
- the bot's `pick_ledger = 'sim'` → observation;
- else, if no `picks` row exists for the key → `picks` (earliest wins, **LG-8**);
- else → observation.

**Backfill difference:** the backfill sorts by `pick_time`, so it is exactly "earliest". The live
mirror is arrival-ordered, and `pick_time` is `now()` at write, so the two are equivalent except for
clock skew. The reconciliation job alerts if a later-arriving row carries an earlier `pick_time`.

### 4.2 `pick_change_log` (§3.6), `pick_operator_state` (§3.7), `pick_deliveries` (§3.5)

All three are append-only or write-rarely, and private.

---

## 5. Invariant → schema mapping

"Retire" means the invariant is dropped and **needs the owner's OK**. "Stale" means it was already
changed by an owner decision or a fix; see §8.

### 5.1 Ledgers (ledgers.md §6)

| Id | Invariant (short) | How the schema keeps it |
|---|---|---|
| LG-1 | Public visibility is DB-enforced, not a reader filter | No base grant (§3.8); public views name what they admit (§3.5) |
| LG-2 | Controls and grade D are never public | `published_tier` trigger (§3.5); views exclude by name |
| LG-3 | Pre-registered rows are never deleted; decision fields are never updated | `picks_immutable` + `picks_no_delete`; annotations via label columns plus `pick_change_log` |
| LG-4 | Pre-registration covers more than the forward test | `bots.locked_population` → `picks.immutable`, including sharp_tight, unified_gate, sweep per-book, EV5/EV8 |
| LG-5 | A rule change is a new population | `rule_version` / `pricing_model_version` in every scoreboard GROUP BY; `odds` mirrors writers exactly during 5c |
| LG-6 | One ledger per bot | `bots.pick_ledger` + `picks_one_ledger_per_bot` + routing (§4.1) |
| LG-7 | Re-evaluations are observations | `pick_observations` [D1-B], or `is_primary` [D1-A] |
| LG-8 | Dedup = earliest `pick_time`, one definition | `route_shadow_row()` is the only implementation; backfill and mirror share it |
| LG-9 | SHADOW-RETIRED-OK | `collect_state = collecting_archived`; observations are kept |
| LG-10 | Restatements are additive | Verbatim copy; `odds_first`, `odds_exec` and the clv_* columns are additive; `pick_change_log` |
| LG-11 | `edge_percent` history is not backfilled | `edge` copied verbatim; `ev_at_pick` is generated, a new column rather than a rewrite |
| LG-12 | Two edge quantities never share an unlabelled column | `edge_kind` NOT NULL |
| LG-13 | CLV columns keep their definition in their name | `clv_raw`, `clv_mc`, `clv_pinnacle_raw` (pre-09-07 sim only), `clv_pinnacle_devig`, `clv_pinnacle_live`; `leg_clv_sharp` separate |
| LG-14 | Decision-time provenance is frozen | anchor group + `kickoff_at_pick` + `decision_snapshot`; NULL rather than a fabricated value for history |
| LG-15 | A bot is the unit of P&L and capability | Separate `bots` rows kept; forward-test ownership from one function (reviewer attention, §3.1) |
| LG-16 | `bots.name` is the join key | Unchanged; `display_name` stays display-only |
| LG-17 | Claim before send | `pick_deliveries` FK to the pick; forward-test partial unique key |
| LG-18 | `void` is not one thing | `void_reason` kept, quarantine protected; `push` and half results added for new writes only |
| LG-19 | Placement lineage | `real_bets.pick_id` FK RESTRICT; UUIDs kept; `UNIQUE (pick_id) WHERE pick_id IS NOT NULL` replaces `real_bets_one_per_shadow_pick` |
| LG-20 | In-play is judged without CLV | `is_inplay` stored with the complete rule; views null out CLV for in-play; the in-play family's metric is `lift` |

### 5.2 Bots and controls (bots-and-controls.md §4)

| Id | How kept |
|---|---|
| I1 | **Stale → re-cast by owner decision 4 (mig 413):** the code list `PLACEABLE_BOTS` is gone. What remains is the code **rule** `placement_path_reason` plus the DB list `coolbet_placer_bots` (rows inserted OFF only, by migration). Phase 5 changes the rule's input from "writes `shadow_bets`" to "`audience_class = 'internal'` AND `pick_ledger = 'shadow'` AND pre-match AND a supported book". Keep `CONTROL-PLACEMENT-PATH-RULE-AGREES` (SQL = Python = web `placement-path.ts`). |
| I2 | Untouched (413's `admin_set_control` + guard triggers) |
| I3–I5, I8 | Untouched (`coolbet_session_state`) |
| I6, I7 | Untouched (`placement_gate`, `_place_bet_api`). In d3 the placers read `picks` through one view, `placeable_picks`, which already filters `audience_class`, `is_inplay` and kickoff. |
| I9 | `real_bets` untouched except `pick_id`; RESTRICT; no money rows deleted |
| I10 | `publish_tier` (bots) and `coolbet_placer_bots` stay separate. `audience_class` makes them disjoint: a `customer_eligible` bot has no placement path; an `internal` bot cannot publish. |
| I11 | `publish_tier` defaults to `none`; `ensure_bots` inserts `none` |
| I12 | **Restated under [D6], needs owner OK.** The old rule: "/performance lists every public-eligible bot, and the publish switch never filters the leaderboard". The new rule: the record = picks stamped public/VIP **at pick time**, so switching a bot off never removes its past (same intent: losers cannot be hidden), but a never-published `customer_eligible` bot no longer appears on /performance. Under [D6-B] the old rule is kept via `maturity_label`. |
| I13 | `audience_class` + `picks_audience_guard` + bots CHECK (§2, §3.6). Smoke `OWN-BOTS-OFF-CUSTOMER-SURFACES` is re-pointed to assert that the trigger refuses. |
| I14 | Per-row `published_tier` read by every sender and every view; `pick_deliveries` |
| I15 | `maturity_label` CHECK kept. Its **six hidden jobs** are split out (bots §2.2): /performance allowlist → [D6]; headline exclusion → `picks_public_record`; Telegram promotion → `published_tier`; Mac placer `COOLBET_RECORD_ALLOWED_MATURITY` → dropped with the dormant API placer; `pick_generator` source cohort → explicit `config.source_bots`; "Pro" cohort in `dashboard_cache` → retired with the paid-tier leftovers. **Each split is a separate reader change in d3/d5, not a schema change.** |
| I16 | Forward-test `published_tier` from arm/grade (SQL twin of `PUBLISHED_ARMS`); `pre_registered` tier is read-only |
| I17 | `picks_immutable`; `source_ledger`; 5e compatibility views re-expose each old table **filtered to its own `source_ledger`**, so every "bot-cohort query ever written" keeps seeing exactly what it saw before (bots §5.5) |
| I18 | `bots.name` unchanged; forward-test ownership is derived |
| I19 | `stake_scheme`; `current_bankroll` updated from `kelly_bankroll` rows only; smoke BOT-BANKROLL-DRIFT re-pointed |
| I20 | `collect_state` read by every writer, including the 4 bare lookups |
| I21 | Retired → `collecting_archived` by default; `stopped` only by an owner decision |
| I22 | `pricing_model_version` NOT NULL for new rows, plus `rule_version` |
| I23 | Validated CHECKs; mirror-trigger failures raise (they are never swallowed, so the legacy write fails loudly); writer failure counters |
| I24–I26 | Untouched |

### 5.3 Predictions (predictions.md §5)

| Id | How kept |
|---|---|
| P1, P2, P4, P5, P6 | Untouched (predictions stays separate) |
| P3 | **Stale** (mig 419, #147): shadow and candidate versions are now `source='ensemble_shadow'/'xgboost_shadow'`. No `picks` impact. |
| PR-L1 | Frozen `probability` / `probability_raw` snapshot on the row; `news_checker`'s rewrite moves to `extra` |
| PR-L2 | `pipeline_model_version` frozen (part of the immutable set for locked rows; for others it mirrors the writer, because the upsert writers rewrite it today, R-Q10) |
| PR-L3 | Free-text version columns, no FK to `model_versions` |
| F1–F7, S1, C1, C2 | Out of scope (5P) |
| T1, T2 | `pick_triggers` / `candidate_funnel` untouched; `ev_at_pick` is generated, not supplied |
| B1 | Untouched |
| PP1 | [D4] |

### 5.4 Inventory headline findings

| Id | Disposition |
|---|---|
| H1 | [D1] |
| H2 | LG-6 routing |
| H3 | `picks_immutable` allow-list (§3.6) |
| H4 | **Partly stale:** fixed going forward by 04fff33f; history is [D2] |
| H5 | Done (2cf14433, mig 409) |
| H6 | UUIDs kept; hard FKs re-pointed in 5e |
| H7 | §3.8 |
| H8 | `odds_first` + RESTRICT |
| H9 | §1 |

---

## 6. Migration path

### 6.0 Prerequisites (before 5b; each is small and already has an owner or a row)

1. Owner answers D1–D6.
2. **R6:** fix the team_total and 1H double settler (two settlers race on the same rows). This must happen before any mirror, because the mirror would faithfully copy the race.
3. **[VIP] #148 closes the `simulated_bets` pending-pick leak** on the old table. Phase 5 is weeks away.
4. **#141 fixes the pricing-model tag in the writer**, so new rows carry it. `pricing_model_version` then has a source.
5. Resolve the 14 `family='unknown'` bots in `bot_config`, and export the EV5/EV8 bots (they have no `bot_config` row yet; the export runs at 03:40).
6. Owner call on B4 (the 4 bare `_bot_id` lookups) → sets `collect_state`.

### 6.1 Step 5b: create and backfill (no behaviour change). **2–2.5 days** including two reviewers.

1. **Migration A:**
   - the enums;
   - the new `bots` columns plus their backfill and guard trigger;
   - `picks`, `pick_observations`, `pick_change_log`, `pick_operator_state` and `pick_deliveries` (private);
   - the functions `forward_test_bot_id`, `route_shadow_row`, `pick_ev_label`;
   - the triggers (§3.6).

   Test it inside a **rolled-back transaction** first (handover §4 rule).
2. **Backfill script** (`scripts/backfill_picks_phase5.py`, idempotent, `ON CONFLICT (id) DO NOTHING`), in this order:
   1. forward test, with `decision_snapshot`;
   2. sim;
   3. shadow, sorted by `pick_time` and routed through `route_shadow_row()`;
   4. operator state and deliveries;
   5. `real_bets.pick_id = COALESCE(simulated_bet_id, shadow_bet_id)`, but **only when that id exists in `picks`**. If it landed in `pick_observations`, list it (R-Q9).

   Triggers run with `oddsintel.migration='on'`, so historical NULL `pricing_model_version` values are admitted.
3. **Reconciliation** (`scripts/reconcile_picks_phase5.py`, and smoke `PICKS-RECONCILES-LEGACY`). The tolerance is **0** unless stated otherwise.

   | Check | Rule |
   |---|---|
   | Rows | For each source: count(source) = count(picks where `source_ledger`) + count(observations from that source). The three sums together = 4,675 + 167,951 + 762 at run time. |
   | Per-bot vs 410 `bot_ledger` | Primary picks: n, pending, won, lost, void. |
   | P&L | SUM(`pnl_unit`) at the decision price, exactly the 410 basis, before [D3] is applied. |
   | CLV | mean `clv_raw`, `clv_mc`, `clv_pinnacle_devig` |
   | Native P&L | SUM(`pnl`) **including NULLs counted separately**; the 182 and 410 void anomalies must reproduce exactly |
   | Checksum per source | `md5(string_agg(id \|\| result \|\| coalesce(pnl::text,'∅') \|\| odds::text \|\| coalesce(clv::text,'∅'), ',' ORDER BY id))` over legacy vs the mapped picks+observations columns |
   | Forward test | **Field by field** for all 762 rows, and `decision_snapshot` = `to_jsonb(row)` |
   | `real_bets` | links resolved = 812 + 145; 35 unlinked stay NULL |

4. **`bot_ledger_v2`** view on `picks` (+ `bot_scoreboard_v2`): diff against the 410 views, with 0 differing rows.

**Rollback:** `DROP` the new objects and the new `bots` columns (nothing reads them yet) and `UPDATE real_bets SET pick_id = NULL`.

### 6.2 Step 5c: keep `picks` in step with the old tables. **2.5–3 days.**

**Recommended change from the inventory's plan (reviewers please rule).** Instead of seven
writer-by-writer dual-writes (c1–c7, 4–5 days), put **AFTER INSERT / UPDATE / DELETE mirror
triggers on the three legacy tables**, which upsert into `picks` / `pick_observations` using the
same mapping functions as the backfill. Why:
- It covers **every** write path in one place, including the ones a writer-by-writer plan misses: the GitHub-Actions `match_status_sweeper` (R5), the three paper bots' own `settle_picks`, `results_check._regrade`, `news_checker`, the web Telegram webhook, `backfill_odds_at_pick_live` (every 30 min) and every hand-run one-off script.
- Reconciliation stays exact by construction.
- No writer code changes until 5e.

**Trigger behaviour**
- It is **not** exception-swallowing. If the mirror fails, the legacy write fails, and the failure is loud (I23). The immutability trigger on `picks` therefore also protects the legacy forward-test table from decision-field edits, which is the desired effect.
- Mapping: `news_checker`'s sim `model_probability` rewrite → `extra.news_adjusted_prob`; sim `signal_message_id` → `pick_deliveries`; `user_placed_at` and the related columns → `pick_operator_state`.
- A legacy DELETE of an immutable or placed pick is **refused** (a behaviour change for cleanup scripts, R-Q8).

**Sub-steps**, each with a reviewer:
- c1: `picks_forward_test` mirror;
- c2: `simulated_bets` mirror;
- c3: `shadow_bets` mirror (the volume step: about 48 re-evaluations per pick per day, in bulk inserts; measure the trigger cost on a copy first);
- c4: daily job `reconcile_picks` at 04:30 (the §6.1 checks, over the last 3 days plus all-time counts) with a Telegram alert on any drift;
- c5: `clv_sharp.py` reads `picks` (the id is unchanged; low risk).

**Rollback:** `DROP TRIGGER` on the legacy tables (instant; the legacy tables stay the source of
truth throughout 5c), then re-run the idempotent backfill to heal any gap.

*Alternative (the inventory's c1–c7):* writer-level dual-write. Same end state; more code; misses
paths unless each is found. Estimate 4–5 d.

### 6.3 Step 5d: readers switch to `picks`, one at a time. **6–7 days.**

The old tables are still written (and mirrored) throughout, so each reader switch is a
**code revert** to roll back. Smoke tests are rewritten **per step** (R12; ~130 executable
references; regenerate the list at each step, don't trust line numbers).

| Sub-step | Readers (from the 5a inventory, updated to today) | Direction | Days |
|---|---|---|---|
| **d1 admin web** | `/admin/bots` (`lib/bot-board.ts`, `api/admin/bot-ledger`, `bot-board-model.ts`, `picks-table.tsx`, `bot-sheet.tsx`, `bot-perf-charts.tsx`, `fleet-strip.tsx`; re-point to `bot_ledger_v2` / `bot_scoreboard_v2`, then swap the names); `/admin/shadow-bots` (read-only "pick queue": `lib/shadow-bots/queries.ts` on `shadow_bets_unique` / `shadow_bot_scoreboard` / `shadow_bets_own_book_clv`, **this is where the EV5/EV8 bots become visible there**); `/admin/real-bets` (`getRealBets`: `paper:simulated_bet_id` → `pick_id`); `/admin/ops` (`getStalePendingBets`); Overview (`lib/admin-overview.ts`:158, `lib/admin-jobs.ts`:128, both read pending `simulated_bets`, so all ledgers are missed); `api/admin/bot-book-odds`; `lib/real-money-tier.ts`; `lib/bot-controls/placement-path.ts` (`PLACEMENT_LEDGER`); `upcoming-picks.ts:308` `user_pick_marks` | 🤖 | 1 |
| **d2 engine monitoring** | `health_alerts` (:102, :138, :371, :606 on sim; :916/:987 on `bot_ledger`, already multi-ledger since f2fab119); `write_ops_snapshot` (:6103–6418); `coolbet_daily_summary`; `results_check.run`; `settle_reconcile`; `live_poller._refresh_active_bets`; `book_price_fidelity`; `trigger_calibrator_check`; `weekly_bot_review`; `threshold_check` (still filters `market='o/u'`, fix it while here); `observatory_metrics`; `settlement.compute_model_evaluations` / `run_post_mortem` | 🤖 | 0.5 |
| **d3 placement and signalling** | `coolbet_placer.load_qualified_*` / `place_bet_by_id` (the manual queue is live); `coolbet_signaler.load_signal_candidates` (+ its tier gate: `maturity_label` → `published_tier`, I14); `coolbet_prekickoff_alert`; `pick_generator._candidates_from_pipeline` (+ `config.source_bots` replaces the maturity source cohort; `pick_operator_state` exclusion); `store_real_bet` (writes `pick_id`); `record_manual_real_bet` RPC (mig 407, takes `pick_id`); web `api/admin/real-bet`; `place_coolbet_ui.load_picks` (**PAUSED, switch it anyway**, R14); `placement_gate` / `placement_path_reason` (+ the SQL twin in 413 + `placement-path.ts`); `coolbet_control.placement_readiness`; `best_price_router` (PAUSED) | 🤖 OWN, 2 reviewers (money) | 1.5 |
| **d4 training** | `fit_platt`, `fit_platt_live`, `train_b_ml3`, `validate_meta_b_ml3`, `aln1_tune_analysis` read `source_ledger='sim'` first, so labels stay identical; `probability_raw` is frozen from cutover | 🤖 | 0.5 |
| **d5 public** | `write_dashboard_cache` (settlement :3289–3639); `getCalibratedHeadlineStats`, `/api/v1/track-record`, `app/page.tsx` headline; `getAllBets`; `getPublicPerformanceExtras`; `getModelV2Stats`; `getRecentSettledBets` (**today it has no cohort filter**); `getTrackRecordStats`; `getAllBotsFromDB` → `bots_public`; `_our_stats` (landing competitor table, GH 02:00); `export_track_record_snapshot` (**signed OpenTimestamped public ledger**: keep row ids and field names, add `schema_version`); `dump_oddsintel_picks_csv`; `performance-client.tsx` / `performance-hero.tsx`; `methodology/page.tsx` text; `picks/page.tsx`; **[VIP]** the EV-label and settled-only rendering on /performance. **All of these move to the one `picks_public_record` view [D6].** Figures are diffed before and after; any change is recorded on /methodology. | 👥 PICKS, 2 reviewers | 2 |
| **d6 pre-registered surfaces** | `picks_public_all`, `picks_forward_test_public`, `_summary`, `_summary_by_market`, `_arm_summary`, `clv_sharp_legs`, `forward-test-picks.ts` `fetchPublicPicks`, `/api/v1/upcoming` → recreated over `picks` **only after c1 has reconciled identically for ≥ 7 days**. Same names, same column lists, same anon grants (smokes PICKS-FORWARD-TEST-SURFACE, PUBLISHED-ARM-HAS-A-RECORD, ANON-LEAST-PRIVILEGE). | 👥 PICKS, 2 reviewers | 0.75 |
| d7 smokes | ~1 d spread across d1–d6; new tests `PICKS-IMMUTABLE-FORWARD-TEST`, `PICKS-AUDIENCE-GUARD`, `PICKS-ONE-LEDGER-PER-BOT`, `PICKS-EV-LABEL-ONE-DEFINITION`, `PICKS-VIP-PENDING-NOT-PUBLIC` | | (incl.) |

### 6.4 Step 5e: writers move to `picks`, old names become views. **3–3.5 days** + 14-day soak.

1. **Writers switch, one group per sub-step** (this is where the inventory's c1–c7 land), lowest
   blast radius first. Each writer drops its legacy write and writes `picks` directly. The legacy
   mirror trigger then goes idle for that writer. Rollback: code revert (the mirror resumes).
   - e1: standalone paper writers (corners, team_total, fh, **`inplay_collector` has its own systemd unit, restart it too**);
   - e2: upsert writers (`pick_generator`, `pick_trigger_matcher`, `ou35_model_shadow`), keeping mirror-exact `odds` semantics for locked populations;
   - e3: `bulk_store_shadow_bets` (HHMM → `pick_observations` directly; retired bots per `collect_state`);
   - e4: `store_bet` + `news_checker` + `coolbet_signaler` + web Telegram webhook (`user_placed_at` → `pick_operator_state`; **web deploy in the same step**);
   - e5: settlement: `_settle_pending_bets`, `_settle_pending_shadow_bets`, the paper bots' `settle_picks`, `fix_stale_live_matches`, `void_ungradeable_1h_bets`, `resettle_wrongly_voided_bets`, `_apply_clv_autovoid`, `results_check._regrade`, **`scripts/match_status_sweeper.py` (GitHub workflow, R5)**, `backfill_odds_at_pick_live`, the bankroll update;
   - e6: `publish_picks_forward_test.claim` / `attach_message_id` (→ `pick_deliveries`) / `settle_picks_forward_test` / `_void_forward_test_on_dead_matches`.
2. **Rename and freeze:** `simulated_bets` → `simulated_bets_legacy`, and the same for `shadow_bets` and `picks_forward_test`. Revoke writes and add a raising BEFORE trigger. Create compatibility views with the old names, each **filtered to its own `source_ledger`** (I17). `shadow_bets` is `picks ∪ pick_observations` for shadow; `shadow_bets_unique` is recreated with an **explicit column list** (NM20, smoke SHADOW-VIEW-COLUMN-DRIFT). **No anon grant on the `simulated_bets` view** ([VIP] leak). The ~110 one-off readers keep working; the one-off **writers** fail loudly (intended).
3. Re-point the hard FKs (`price_verifications`, `bet_telegram_alerts`, `manual_placement_queue`) to `picks(id)`. Drop `real_bets.simulated_bet_id` / `shadow_bet_id` after the soak.
4. **Soak:** 14 days with zero reads of the `_legacy` tables (`pg_stat_user_tables.seq_scan/idx_scan` deltas). Then drop the legacy tables, `shadow_bot_scoreboard`, `picks_forward_test_shadow` (dead), the `inplay_bet_fired` trigger, and (with owner sign-off) the backup tables, `user_picks` [D5] and `bots.show_on_picks`.
5. **Doc ripple:** SYSTEM_MAP (+ `bot_registry.py`), COOLBET_OWN_BETTING, ANALYSIS_GOTCHAS §18/§22/§23 (+ new ledger vocabulary), WORKFLOWS, TIER_ACCESS_MATRIX ([VIP]), MODEL_WHITEPAPER (`edge_kind`, `pricing_model_version`), RELIABILITY_LEDGER, methodology page.

**Rollback during the soak:** rename `_legacy` back and drop the compatibility views. But any row
written only to `picks` since e1 is missing from legacy, so ship `scripts/sync_picks_to_legacy.py`
(the reverse mapping; tested in e1) as the rollback tool. **After the drop there is no rollback
except the nightly backup** (3-day local, 90-day remote).

### 6.5 Estimate

| Step | Days |
|---|---|
| 6.0 prerequisites (not Phase 5 work; owned elsewhere) | — |
| 5b create + backfill + reconcile | 2–2.5 |
| 5c mirror triggers + reconcile job | 2.5–3 |
| 5d readers d1–d7 | 6–7 |
| 5e writers + rename + compatibility views + doc ripple | 3–3.5 |
| Review rounds (2 reviewers on money, public and migration steps; about 15–20% overhead, partly already inside the figures above) | ~1.5 |
| **Total** | **≈ 15–17.5 working days (3–3½ weeks)** + 7-day d6 wait (overlaps d1–d5) + 14-day soak |
| [D1-A] | −0.5 d in 5b, +0.5 d across 5d |
| [D4-B] | +1 |
| [D6-A] | included in d5 |

---

## 7. Risks

| # | Risk | Step | Mitigation |
|---|---|---|---|
| R1 | Void pnl anomalies make sums differ | 5b | Copied verbatim, normalised only in `pnl_unit` |
| R2 | Sim CLV column ambiguity | 5b | [D2]; split by settle date 2026-09-07 |
| R3 | Price basis differs between admin and public | 5b/d5 | [D3]; `basis_fallback` flag |
| R4 | Naive sim `pick_time` | 5b | R-Q2 check before the backfill |
| R5 | GitHub-Actions writer (`match_status_sweeper`) | 5c/e5 | The mirror trigger covers it by construction |
| R6 | Double settler race | 6.0 | Fix before 5c |
| R7 | Match delete cascades picks | 5b | RESTRICT; fix `cleanup_match_dupes` |
| R8 | Upsert writers rewrite price and model tag on placed picks | 5b | `odds_first`; RESTRICT from `real_bets` |
| R9 | anon grants | 5b/d5/5e | Private tables; `*_public` views only; no anon on compatibility views |
| R10 | Three arm→bot mappings | 5b | One function |
| R11 | Regrade looks like tampering | 5b | `pick_change_log` |
| R12 | ~130 smokes pin table names | 5d | Per-step rewrite |
| R13 | `ensure_bots` resurrects or opts in | 5b | Insert-safe defaults |
| R14 | PAUSED placer un-paused mid-migration | d3 | Switch it anyway; its plist reads `placeable_picks` |
| **R15 (new)** | The mirror trigger's cost on bulk shadow inserts, or a mirror bug blocking a legacy write (the trigger is not swallowing, by design) | 5c | Measure on a copy; stage it (forward test first, shadow last); `DROP TRIGGER` is the instant rollback |
| **R16 (new)** | [VIP] a pending VIP pick reaches a public surface during the cut-over, where two readers use different rules | d5 | #148 closes the leak on legacy first; `picks_public_record` is the only public source; smoke `PICKS-VIP-PENDING-NOT-PUBLIC` |
| **R17 (new)** | Stamping `published_tier` per row changes the public record's meaning ([D6-A]) | d5 | Before/after figures on /methodology; signed-ledger `schema_version` |
| **R18 (new)** | Parallel sessions (#141, #148) add bots or columns to `simulated_bets` while 5c runs; the mirror silently drops a new column | 5c–5e | The mirror function lists columns explicitly, and smoke `PICKS-MIRROR-COVERS-ALL-COLUMNS` fails when a legacy column is unmapped |
| **R19 (new)** | A shadow copy id referenced by `real_bets`/marks lands in `pick_observations`, not `picks` | 5b | R-Q9; FK to picks is only filled when resolvable; list the rest |

---

## 8. Stale items found in the invariants / inventory / design (as of 2026-09-24 evening)

1. **`PLACEABLE_BOTS` is gone** (mig 413, owner decision 4). The eligibility list is now the DB (`coolbet_placer_bots`, 11 rows, all OFF, the O/U bot locked) plus the code rule `placement_path_reason`.
   - Stale: bots-and-controls I1, §2.8, §5.1, §6 "Separate layers 1"; design doc Phase 5 "`bots` replaces … `PLACEABLE_BOTS`"; inventory §3.5 and d3 ("retire `coolbet_placer_bots` and `PLACEABLE_BOTS`").
   - Correct now: `coolbet_placer_bots` **stays**.
2. **The legacy writer routes were deleted:** `/api/admin/coolbet-placer-bots` and `/api/admin/coolbet-daemons-pause` and their components. Also deleted: `/api/admin/record-combo`, `/admin/place`, `/admin/cs2`, and the LoL and Tennis pages.
   - Stale inventory rows: §1A (`coolbet_placer_bots` web writer), §1D (`record-combo` writer; `/admin/place` readers and writers at engine-data.ts:711/784/798/950; the `admin/cs2` bots reader), §3.6 writer list, and d1 (`/admin/place`).
   - Effect: `simulated_bets.admin_offered_at` now has **no writer**.
3. **`/admin/shadow-bots/[bot]` is retired.** It is now a redirect to the `/admin/bots` sheet (IA move P7), and `/admin/shadow-bots` itself is read-only. The inventory's d1 readers at `[bot]/page.tsx:283/304/475` no longer exist.
4. **`bot_v10_ou` was retired on 2026-09-24.** Its row still carries `show_on_picks = true`; that is harmless, because `picks_public_all` filters `retired_at`, but it should be cleared. Stale: inventory §0.2 and bots §2.2 (the source-cohort knock-on), design-doc family table.
5. **New bots from #141 write `simulated_bets`:** `bot_rating_1x2_v1` (0 rows yet), `bot_combined_1x2_v1` (0), `bot_combined_1x2_ev5_v1` (1, first pick 2026-09-24 20:05), `bot_combined_1x2_ev8_v1` (1).
   - Stale: inventory §1.1 "which ledger each active bot writes: sim only: none"; the "20 active" counts (now 24); design-doc family table (`model_sim` lists 2 bots).
   - The EV bots **have no `bot_config` row yet** (the export runs at 03:40).
   - The /admin/shadow-bots page cannot see them.
   - Their stored `edge_percent` is probability points, while their gate is EV (LG-12).
   - Their `model_version` is the global tag (I22).
6. **Sim CLV-Pinnacle "no live writer" (inventory H4, §3.2) is fixed** (04fff33f: the settler writes `clv_pinnacle_devig`). Also, sim `clv_pinnacle` has been de-vigged **since 2026-09-07** (CLV-PINNACLE-LIVE-TWO-DEFINITIONS). So ledgers.md §2.3 / NM4 ("`clv_pinnacle` is still raw in sim") is true **only for rows settled before 09-07**.
7. **Predictions invariant P3 / ANALYSIS_GOTCHAS §1** ("shadow versions live under `source='xgboost'`") is stale since mig 419 (#147): they now use `ensemble_shadow` / `xgboost_shadow`.
8. **`health_alerts` "no picks for 48h"** now reads all ledgers via `bot_ledger` (f2fab119). Its inventory entry (:916/:974 "sim") is stale, and the monitor is **a new reader of the 410 view**.
9. **The Coolbet footprint pause (`daemons_paused`)** means "odds sweeping only, never real bets" (owner, 2026-09-24). bots-and-controls §2.7 and I-list text describing it as a possible placement blocker are stale; `coolbet_control.can_stake()` lists it as a warning.
10. **`maturity_label` job 4** (`COOLBET_RECORD_ALLOWED_MATURITY` on the Mac placer loaders) applies only to the API placer, which mig 413 declares "no longer a supported executor". The label's remaining live jobs are 5 (bots §2.2).
11. **The design doc's `collect = is_active AND retired_at IS NULL`** is still in `docs/UNIFIED_BOT_MODEL_DESIGN_2026_09_24.md` (`bot_capabilities`), although the genesis research (I21) and `bot_capabilities.writing_7d` already work around it. It should get a correction banner.
12. **Web admin readers not in the 5a inventory** (added since): `lib/admin-overview.ts:158` and `lib/admin-jobs.ts:128` (pending `simulated_bets` only, so they miss pending shadow and forward-test picks; the same shape as the health-alerts false alarm); `lib/bot-controls/placement-path.ts` (`PLACEMENT_LEDGER = "shadow_bets"`); `api/admin/feed-control`.

---

## 9. Open questions for the reviewers

- **R-Q1.** 14 bots have `family='unknown'` in `bot_config`. Resolve them before `bots.family` becomes NOT NULL-meaningful.
- **R-Q2.** Is the sim `pick_time` zone (naive write) consistent with `created_at` on every row?
- **R-Q3.** Which rows match neither edge form? List them before forcing `edge_kind`.
- **R-Q4.** Add `meta_model_version` for `meta_clv_score`?
- **R-Q5.** Compute `clv_mc` for sim in the backfill (needs `closing_margin`), or leave it NULL?
- **R-Q6.** Did the sim `clv_pinnacle_live` values written before 09-07 use the raw definition?
- **R-Q7.** What explains the 11 post-09-07 sim rows where `clv_pinnacle` ≠ `clv_pinnacle_devig`?
- **R-Q8.** Refusing legacy DELETEs changes the cleanup scripts' behaviour. List which scripts are still meant to be runnable.
- **R-Q9.** Does any `real_bets.shadow_bet_id` or `user_pick_marks.pick_id` point at a shadow row that routes to `pick_observations`? Measured 2026-09-24 against "earliest per key": **0** `real_bets` and **1** `user_pick_marks` row point at a non-earliest copy. That measurement does not cover the extra rule that shadow rows of sim-ledger bots also route to observations, so re-measure with `route_shadow_row()`. Then decide whether the reference goes to the observation, or whether that copy's UUID is promoted to be the pick.
- **R-Q10.** Should upsert writers keep rewriting `pipeline_model_version` on non-locked rows? That breaks PR-L2 today.
- **§3.1** Store forward-test `bot_id` (cached, trigger-derived) or derive it in views only (LG-15)?
- **§6.2** Mirror triggers (recommended) vs writer-by-writer dual-write?
- **§3** Name the table `picks` or `bot_picks`?
