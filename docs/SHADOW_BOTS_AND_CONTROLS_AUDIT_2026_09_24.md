# Shadow-bots page + the bot control map (2026-09-24)

> **CHANGED 2026-09-25 (#162 W4.6):** the retired money paths cited below — the paper Mac daemon (`coolbet_mac_daemon`), `coolbet_daemon_healthcheck`, the VPS manual-placement drain, the API placer (`coolbet_placer.place_all_bets` / `place_all_inplay_bets` / `place_bet_by_id` / `_place_bet_api`, CLI `scripts/place_coolbet_bets.py`) and web `getPlaceableBets` / `getRealBets` / `real-money-tier.ts` — are DELETED. Live executors: `scripts/place_coolbet_ui.py` + `workers/automation/best_price_router.py` (`placement_gate.py`, smoke `RETIRED-MONEY-PATHS-GONE`).

> **CHANGED 2026-09-26 (#162 W6.8):** `/admin/real-bets` (cited below) is RETIRED — it redirects to the Real money tab of `/admin/bots` (`/admin/bots?section=money`).

> **CHANGED 2026-09-25 (#162 W4.3):** `BOT_THRESHOLDS` (cited below) is deleted — every placer applies `workers/automation/placement_floor.pick_clears`, the stricter of the bot's own floor and the market floor. See the W4.3 banner in `docs/COOLBET_OWN_BETTING.md`.

> **CHANGED 2026-09-24 (#139 IA P6/P7):** `/admin/shadow-bots/[bot]` is RETIRED — it redirects to `/admin/bots?bot=<name>&tab=picks` (the sheet carries Bet made + current prices; the model-edge "Min odds" was dropped, #139 finding b). `/admin/shadow-bots` is now the Pick queue (picks + Place only): its safety strip, scoreboard and promotions were removed (promotions moved to /admin/real-bets).


> **CHANGED 2026-09-24 (#139 IA moves P2/P3):** the legacy `/api/admin/coolbet-placer-bots` and `/api/admin/coolbet-daemons-pause` routes and their `CoolbetPlacerToggle` / `CoolbetDaemonsPause` components were DELETED. Per-bot real-money eligibility is switched on /admin/bots only; the footprint pause (now "Coolbet sweeping") on /admin/feeds only — both through the audited `admin_set_control`. Owner decision the same day: the footprint pause stops odds sweeping, never real bets. The text below is the point-in-time audit.



> **Review corrections (independent verifier, 2026-09-24) — read these before the body.**
> 1. The arm→bot mapping is written out **five** times, not four: the live view `clv_sharp_legs` has its own `CASE`.
> 2. "Only shadow_bets bots can ever stake" is **wrong**: `scripts/place_coolbet_bets.py --execute` → `place_all_bets(execute=True)` (`coolbet_placer.py:2077`) loads `simulated_bets` for any active bot and passes only the pause + armed check — never `PLACEABLE_BOTS` or `ui_place_enabled`. Two more paths clear a strategic pause: the Telegram `coolbet-resume` button and `coolbet_browser_sync.py --resume-placement`.
> 3. The 150 unlogged "bet placed" marks are all 08-22..08-25, not "mostly to Sep 13".
> 4. Shadow switched to the ≤60-min own-book close only on 2026-09-22 (#024); older shadow rows still carry the unbounded close (e.g. 85 of the LEAD bot's 113 pre-09-20 rows).
> 5. The LEAD bot's +2.10% is not "stale quotes before the 09-20 gate": pre-09-20 rows WITH a fresh close run **−7.06%** (n=28); the positive mean comes from unbounded-close rows, 32 of them clv=0 (price vs itself). Read it as fresh-close vs stale-close, not before/after a gate.
> 6. `/pausepicks` also skips `write_board` (the /picks watchlist) and the candidate funnel, not just the ledger rows.
> 7. `bot_v10_1x2` +1.33% vs +2.50%: fully explained by the four #002 voids (+119.9%, +77.9%, +1.7%, +200.4%).
> **Parent row:** `PRIORITY_QUEUE.md` #139 UNIFIED-BOT-MODEL-EPIC, step 1b (absorbs #138).
> Builds on `docs/BOTS_AUDIT_2026_09_24.md` (#137). That doc's 20-bot inventory (§B2), its
> code-path families (§B3) and its drift list C1–C23 are **not repeated here**. This doc
> references them.
> **Read-only.** Nothing in either repo or the DB was changed. All numbers come from the
> live DB on 2026-09-24 via SELECT; queries are reproduced at the end.
> Direction: **🤖👥 BOTH.** 🤖 OWN: the page the operator copies real bets from, plus every
> switch that can stake money. 👥 PICKS: every switch that puts a pick in front of readers,
> and the numbers they are shown.

---

## 0. The short version

1. **The owner's screenshot, explained.** `/admin/shadow-bots` lists every non-retired
   `bots` row (`queries.ts:216-220`) but scores them only from `shadow_bot_scoreboard`, a
   view over `shadow_bets` (`queries.ts:437-454`). The five published forward-test bots
   (`bot_sharp_1x2_v1`, `bot_sharp_ou_v1`, `bot_consensus_b/c/d_v1`) write
   `picks_forward_test`, which has **no `bot_id` column at all**. They show 0 settled,
   COLLECTING 0/300, and an empty "No picks yet" detail page. They have **144 settled
   picks** between them, and **14 pending picks with a future kickoff** (plus 12 junk-control rows)
   that are not in the page's "Today's picks" table either.
2. **A bot's identity depends on which table it writes.** A pick in `picks_forward_test`
   gets its bot name from a `CASE` over `arm`/`grade`/`market`. That mapping is written
   out **four times**: the `picks_public_all` view, `/performance`'s `PUBLISHED_ARM_BOTS`,
   `bot-aggregates.ts` `LEDGER_BACKED_BOTS`, and the publisher's funnel map
   (`publish_picks_forward_test.py:702`). A fifth, stale copy is the
   `picks_forward_test_shadow` view, which maps every row to the **retired**
   `bot_sharp_forward_test_v1`.
3. **"Published" has four different switches, and `bots.show_on_picks` is only one of them.**
   `show_on_picks` controls **only the model arm on /picks**. The sharp and consensus
   arms reach /picks through a hard-coded `arm` list in the view: `consensus_b/c` have
   `show_on_picks = FALSE` and are published anyway. The model arm reaches **Telegram** on
   a different switch, `maturity_label = 'calibrated'` (`coolbet_signaler.py:141,221`).
   **/performance** uses `maturity_label IN (calibrated, beta)` plus the hard-coded list.
   The **anchored public ledger** (`ledger/*.json` + `/api/v1/track-record`) holds
   **simulated_bets only**, so the forward test, which is the product, is not in it.
4. **Real money is off, and six separate things keep it off:** `placement_paused = TRUE`
   (a strategic closure since 09-14), `real_money_armed = FALSE`, both
   `ui_place_enabled = FALSE`, both `--execute` plists parked in
   `~/Library/LaunchAgents/paused/`, and the Mac daemon has not ticked since 09-10. But
   **`ROUTER_ALLOW_REAL=true` is set in the Mac's engine `.env`**. The env opt-in is
   already on, so loading the router plist and opening the three DB gates would stake.
   The Telegram `/resume` command clears `placement_paused` with one message, even though
   the reason recorded on the pause says not to clear it for a transport fix.
5. **Only shadow-ledger bots can ever stake.** Both placers load from `shadow_bets_unique`
   (`place_coolbet_ui.py:641-668`). A model pick gets there only if it is mirrored into a
   `bot_coolbet_*_model_v1` shadow row, and that mirror reads only `maturity_label =
   'calibrated'` sim bots (`pick_generator.py:405`). A published forward-test pick has no
   path to real money, and has no path to the manual **Place** logger either.
6. **Manual "I placed this" is split across two tables that do not reconcile.**
   `real_bets` (via **Place** → `record_manual_real_bet`) holds 2 manual rows ever.
   `user_pick_marks` state=2 ("bet placed", the tick icon) holds **292 rows. 150 of them
   have no matching `real_bets` row**, mostly from the 08-22…09-13 manual real-money
   period. That real money exists in no ledger that settles or scores it.
7. **Three different "closing line" definitions.** `shadow_bets` and `real_bets` use
   `get_book_close` (at the pick's own book, **≤60 min before KO, no fallback**).
   `picks_forward_test` and `simulated_bets` use `get_closing_odds` (own book, **no
   freshness bound**). A margin-corrected CLV (mc-CLV) exists only on shadow and
   forward-test rows. `simulated_bets` has four other CLVs; `real_bets` has raw and
   Pinnacle only. **The money we actually staked cannot be scored on the pre-registered
   metric.**
8. **The page's LEAD / TRACK labels point at stale-era artefacts.** Today LEAD =
   `bot_coolbet_trigger_sharp_1x2_v1` (mc-CLV +1.35%, n=126). Its whole positive mean is
   from before the 09-20 freshness gate (pre +2.10%, n=113; post **−5.17%**, n=13). The
   other two CANDIDATEs have the same shape. `bot_inplay_slowstate_v1` gets a **RETIRE**
   verdict (mc-CLV −43.7%, n=504) on a metric its own design calls inadmissible.
9. **The same bot shows a different record on each page, and nothing labels which.**
   `bot_v10_1x2`: /admin/bots n=401 exec ROI +7.1% raw CLV +7.47%; /admin/shadow-bots
   n=355 ROI +5.0% mc-CLV −1.57% (timing-cohort copies); /performance n=397 raw CLV
   +7.33%; Pinnacle-de-vigged CLV **+1.33%** (n=331). The #137 doc reported +2.50%
   (n=335); that figure **no longer reproduces**, probably because of the #002 voids.
   On the /performance leaderboard, model rows show **raw** CLV (break-even ≈ +8%) and
   forward-test rows show **margin-corrected** CLV (break-even 0), in the same column.
10. **`publishing_paused` also stops recording the pre-registered ledger.** *(FIXED 2026-09-24, owner decision: the pause now skips only the Telegram send — see WORKFLOWS.md § Pause semantics.)*
    `job_publish_picks_forward_test` returns before `claim()` (`scheduler.py:2651-2655`).
    So `/pausepicks` stops the Telegram send **and** stops the live arm, the consensus
    arm and the junk control from being written to `picks_forward_test`. A publish switch
    and a collect switch are fused into one flag.

---

## A. `/admin/shadow-bots` and `/admin/shadow-bots/[bot]`

### A1. Where the page's data comes from

Files (web): `src/app/(app)/admin/shadow-bots/page.tsx` (91 lines),
`src/lib/shadow-bots/{queries,verdict,labels}.ts`, `src/components/shadow-bots/*`.
The superadmin gate is `page.tsx:35-42`, using the service client.

| # | Read | Source | Filter | Cache |
|---|---|---|---|---|
| 1 | bot list | `bots` (`queries.ts:216-220`) | `retired_at IS NULL`. **This alone decides which bots appear** | 60 s |
| 2 | placer toggles | `coolbet_placer_bots` (`:228`) | all rows (2) | 60 s |
| 3 | today's real bets | `real_bets` (`:229-238`) | `placed_at >= UTC midnight` | 60 s |
| 4 | pending pre-match picks | `shadow_bets_unique` (`:239-246`) | `bot_retired_at IS NULL`, `result='pending'`, KO ≥ now, limit 1500 | 60 s |
| 5 | pending in-play picks | `shadow_bets_unique` (`:249-256`) | `inplay_minute IS NOT NULL`, limit 300 | 60 s |
| 6 | promotions | `promo_terms` + embedded `promo_ledger` | active | 60 s |
| 7-10 | live quotes | `odds_snapshots` per book: Coolbet, Unibet-Site, Epicbet, Tonybet (`:185, 373-434`) | pick fixtures × markets, `is_live=false`, last 12 h, best price inside a 15 s burst of the newest row | 60 s |
| 11 | scoreboard | `shadow_bot_scoreboard` (`:437-454`) | `bot_id IN (active ids)` | 60 s |
| — | safety flags | `coolbet_session_state` (`loadSessionState`, `:524-546`) | id=1, **fresh on every request, fails closed** | none |
| — | pick marks | `user_pick_marks` (`fetchUserPickMarkStates`) | this user | none |

**Nothing on the page reads `picks_forward_test`, `simulated_bets` or `picks_board`.**

The views it rests on (definitions pulled with `pg_get_viewdef`):
- `shadow_bets_unique` = `DISTINCT ON (bot_id, match_id, market, selection)` ordered by
  `pick_time`, i.e. **the earliest row wins**, plus `bots.retired_at/is_active/name` and a
  derived `closing_fresh`. For `bot_v10_1x2` this means the earliest *timing cohort*
  copy: 3,305 of its 3,310 shadow rows are cohort copies across 82 cohorts.
- `shadow_bot_scoreboard` = over `shadow_bets_unique` with `bot_retired_at IS NULL` and
  `result IN (won,lost)`:
  - `settled_n`
  - `settled_roi` at `COALESCE(odds_at_pick_live, odds_at_pick)`, flat €10
  - `clv_n / clv_mc_mean / clv_mc_sd` over `clv_margin_corrected`
  - `decision_fresh_n` (`decision_quote_age_min ≤ 60`) and `decision_age_known_n`
  - **No era split**, and in-play bots are not excluded.

### A2. Which bots it lists, and why the forward-test bots read 0

- The **list** comes from `bots WHERE retired_at IS NULL`. On purpose, nothing is
  hard-coded (the "visibility invariant", `page.tsx:14-16`). That gives 20 rows today.
- The **numbers** come from `shadow_bets`. The 5 forward-test bots have no `shadow_bets`
  rows, and `picks_forward_test` has no `bot_id` to join on, so their scoreboard row
  falls back to zeros (`scoreboard.tsx:64-71`): n settled 0, n CLV 0, verdict
  `COLLECTING 0/300 CLV`, track OPEN, "paper".
- What those 5 bots actually hold (from `picks_forward_test`, all rule versions):

| Bot (view mapping) | rows | settled | mc-CLV (settled) | unsettled |
|---|---|---|---|---|
| `bot_sharp_1x2_v1` (live, 1x2) | 72 (v1 8 + v4 64) | 70 | v4 −2.88% (n=62); v1 −11.10% (n=8) | 2 |
| `bot_sharp_ou_v1` (live, O/U 2.5) | 24 | 22 | −2.87% | 2 |
| `bot_consensus_b_v1` (grade B) | 5 | 4 | −6.89% / −3.77% | 0 |
| `bot_consensus_c_v1` (grade C, v1+v2) | 38 | 31 | −7.62% (1x2), −6.33% (O/U) | 7 |
| `bot_consensus_d_v1` (grade D, v1+v2) | 27 | 17 | −11.12% (1x2), −2.09% (O/U) | 6 |
| *(junk_anchor control, not a bot)* | 594 | 579 | v4 −3.16% (1x2) / −2.85% (O/U) | 12 |

- `bot_v10_1x2` (a `simulated_bets` bot) **does** have a scoreboard row. It is built
  from its *timing-cohort shadow copies* (n=355), not from its own ledger (n=397), so
  its page n and ROI are never the numbers /admin/bots or /performance show.
- `bot_high_roi_global_v2` is the same: shadow n=36 against sim n=52.

### A3. What each section, column and chip means

**Safety strip** (`safety-strip.tsx`), sticky:

| Chip | Source | Today | Note |
|---|---|---|---|
| placement | `placement_paused` | **PAUSED** (since 09-09, strategic reason set 09-14) | tooltip shows the reason |
| publishing | `publishing_paused` | live | see §0.10 for what it also stops |
| daemons | `daemons_paused` | running | the Mac footprint daemons. The last tick was **2026-09-10**, so "running" means only that the flag is not set |
| real money | `real_money_armed` | disarmed | no UI or CLI sets it (`set_real_money_armed` has **no caller**) |
| executors | count of `ui_place_enabled` | 0 bots on | links to the scoreboard toggles |
| today | `real_bets` today: `placed_real=TRUE` count/stake vs **hard-coded** 80 / €800 (`safety-strip.tsx:11-12`) + "manual" = `placed_real IS NULL` | 0 | caps are display defaults. The engine reads `COOLBET_MAX_*` env (`place_coolbet_ui.py:196-197`) |
| DB GATES | `!paused && armed && ≥1 toggle` | closed | honestly labelled as the DB half only. It cannot see launchd or `ROUTER_ALLOW_REAL` |

**Today's picks** (`picks-table.tsx`, `picks-row.tsx`). One row per pending
`shadow_bets_unique` pick of an active bot. Today that is **72 rows, 45 (63%) of them
from `bot_unified_gate_1x2_paper_v1`**, an instrument that is never placeable and never
published and that tracks NEGATIVE.

| Column | Computation | Source of the parameter |
|---|---|---|
| Best placeable | max quote at a `BOOK_CHIP` book with `placeable: true` = Coolbet, Unibet-Site (`labels.ts:56-63`) | Epicbet shows greyed. **Tonybet is fetched but has no `BOOK_CHIP` entry, so it is silently dropped** (`picks-table.tsx:61`) |
| Decision | `decision_quote_age_min`: FRESH ≤60 / STALE / UNKNOWN | the 5 `pick_generator` bots always read UNKNOWN (#137 B3b) |
| Shown age | age of the Best-placeable quote | — |
| Break-even | `1 / prob`, prob = `calibrated_prob ?? model_probability` | — |
| Gate floor | `1/(prob − threshold)`, raised to the odds floor | threshold = `botEdgeThreshold()` = **hand-kept `BOT_EDGE_THRESHOLDS` map** in `coolbet-edge.ts:85-106`, **default 0.08**. Odds floor/cap = generated `ENGINE_BOT_FLOORS` (from the registry) → else per-market floor |
| Live edge | `prob − 1/price` | — |
| Verdict | `pickVerdict()` (`verdict.ts:178-236`): BLOCKED (KO<3 min) → SKIP (no price / quote ≥30 min / no prob / below break-even / above cap) → PLACE (≥ gate) → THIN; in-play is always SKIP | placement pause and toggles are **context only** (muted "auto off"), not blockers, since 2026-09-15 |
| Action | tri-state mark (`user_pick_marks`) + **Place €X** when not in-play, not the control arm, and a placeable quote exists; enabled on PLACE/THIN | see A5 |

Wrong parameters feeding the gate floor today:
- `bot_v10_1x2` and `bot_high_roi_global_v2` have **no entry** in `BOT_EDGE_THRESHOLDS`,
  so they get 0.08. Their real floor is tiered 3–12% (`BOTS_CONFIG`).
- `bot_unified_gate_1x2_paper_v1` also defaults to 0.08; its real floor is a flat 10%.
- The consensus and sharp forward-test bots' caps (1.60 / 4.0) are **missing** from
  `ENGINE_BOT_FLOORS` (`oddsCap: null`). This does not matter today only because those
  bots have no rows on this page.
- **Three sources for one bot's floor:** `BOT_EDGE_THRESHOLDS` (TS, by hand),
  `ENGINE_BOT_FLOORS` (generated from `bot_registry.py`), and `place_coolbet_ui.py:56`
  `BOT_THRESHOLDS` (Python).

**Which bots work** (`scoreboard.tsx`). One row per active bot:

| Column | Source |
|---|---|
| n settled | `settled_n` |
| n CLV | `clv_n` |
| CLV (mc) ± CI | `clv_mc_mean` ± 1.96·sd/√n (cluster-naive) |
| t | computed |
| fresh | `decision_fresh_n` |
| ROI | `settled_roi` |
| Track | see A4 |
| Verdict | see A4 |
| Real money | a `CoolbetPlacerToggle` if the bot has a `coolbet_placer_bots` row, else "paper" |

Rows are sorted LEAD first, then placer bots, then by n settled (`:97-102`).

### A4. VERDICT / TRACK / LEAD, and whether they are honest

The rules (`verdict.ts`):
- **VERDICT** (pre-registered, `botVerdict` `:278-303`):
  - n_CLV < 300 → `COLLECTING n/300 CLV`
  - CI lower bound > 0 → PROMOTE
  - mean < −2% → RETIRE
  - otherwise OBSERVE
- **TRACK** (not a verdict, `botTrackKind` `:357-363`):
  - whole CI < 0 → NEGATIVE (shown "CI < 0", row dimmed)
  - n ≥ 30 and mean > 0 → CANDIDATE
  - otherwise OPEN
- **LEAD** (`leadBotName` `:374-386`): the CANDIDATE with the most legs. Exactly one
  bot, or none.

Today, recomputed from the view:

| Bot | n CLV | mc-CLV ± CI | Verdict | Track |
|---|---|---|---|---|
| `bot_coolbet_trigger_sharp_1x2_v1` | 126 | +1.35 ± 2.43 | COLLECTING | **LEAD** |
| `bot_coolbet_trigger_sharp_ou_v1` | 49 | +1.26 ± 2.90 | COLLECTING | CANDIDATE |
| `bot_unibet_trigger_sharp_ou_v1` | 40 | +0.18 ± 2.95 | COLLECTING | CANDIDATE |
| `bot_unibet_trigger_sharp_1x2_v1` | 170 | −0.88 ± 2.63 | COLLECTING | OPEN |
| `bot_v10_1x2` | 308 | −1.57 ± 1.47 | **OBSERVE** | NEGATIVE |
| `bot_trigger_1x2_sharp_tight_v1` | 212 | −3.74 ± 1.54 | COLLECTING | NEGATIVE |
| `bot_ou35_model_v1` | 201 | −5.27 ± 0.51 | COLLECTING | NEGATIVE |
| `bot_unified_gate_1x2_paper_v1` | 78 | −6.73 ± 1.81 | COLLECTING | NEGATIVE |
| `bot_coolbet_1x2_model_v1` | 11 | −4.19 ± 3.36 | COLLECTING | NEGATIVE |
| `bot_trigger_ou_sharp_v1` | 5 | −8.05 ± 3.64 | COLLECTING | NEGATIVE |
| `bot_inplay_slowstate_v1` | 504 | **−43.69** ± 1.26 | **RETIRE** | NEGATIVE |
| `bot_inplay_slowstate_afctl_v1`, `bot_high_roi_global_v2`, `bot_trigger_1x2_sharp_v1`, `bot_coolbet_ou_model_v1` | 0–31 | — | COLLECTING | OPEN |
| 5 forward-test bots | **0 (wrong)** | — | COLLECTING 0/300 | OPEN |

Is it honest?
- **The rule itself is sound and well labelled.** mc-CLV at break-even 0, ROI shown only as
  a cross-check, the two populations kept separate (migration 360), LEAD explicitly "not
  an endorsement". The problems are in the *inputs* the rule is fed.
- **Era mixing makes LEAD misleading.** All three CANDIDATEs are positive only because of
  picks priced off stale quotes before the 2026-09-20 freshness gate:
  - CB sharp 1x2: pre +2.10% (n=113), post −5.17% (n=13)
  - CB sharp O/U: pre +2.61% (43), post −8.43% (6)
  - UB sharp O/U: pre +1.25% (35), post −7.27% (5)

  The page points the operator's eye, and sorts the picks table, towards the bot whose
  current regime is the most negative. The scoreboard needs a per-bot "since last gate
  change" split. #137 made the same point for /admin/bots (E6).
- **In-play RETIRE is on an inadmissible metric.** The rig's design says CLV does not
  apply in play (#137 F). The page nevertheless prints RETIRE in red on −43.7%. The
  control arm reads COLLECTING 0/300 forever, because it can never have a CLV.
- **The forward-test bots are shown as "no data".** This is the owner's complaint. It is
  also the *published* product, sitting at mc-CLV −2.9% against the junk control's
  −3.0%. The row that should be the most scrutinised is the one rendered empty.
- **"n CLV" for the model bots is the timing-cohort copy, not the bot's ledger.** The
  scoreboard's `bot_v10_1x2` is a different population from its `simulated_bets` record
  (see A7).
- **Minor labelling bugs:**
  - `FRESH_TITLE` and the "Decision" header say "under 30 min", but the code uses 60
    (`picks-row.tsx` FRESH_TITLE, `picks-table.tsx:177`). `DECISION_FRESH_MAX_MIN` was
    fixed and the tooltips were not.
  - The `LOGGED-PICKS-INVISIBLE` comment in `queries.ts:231-235` says "there is no
    unique index yet". `real_bets_one_per_shadow_pick` (UNIQUE on `shadow_bet_id`) now
    exists, and `record_manual_real_bet` takes an advisory lock.

### A5. The Place action and the real-money toggle

- **Place €X** (`place-action.tsx`) is a two-step confirm with **editable price and
  stake** (#022 b, 2026-09-24). It POSTs to `/api/admin/real-bet`
  (`route.ts:17-115`), which checks superadmin, checks the book against
  `accessible_bookmakers` (not banned or inactive), then calls
  **`record_manual_real_bet`** (migration 407) with:
  `p_match_id, p_market, p_selection, p_bookmaker, p_actual_odds, p_stake,
  p_captured_odds, p_notes, p_bot_id, p_simulated_bet_id, p_shadow_bet_id`.
  - The function takes an advisory lock per selection, checks for the same (match,
    market, selection) already logged *today* (→ 409 `already_placed`), and inserts with
    **`placed_real = NULL`** (manual, unconfirmed). It then `revalidatePath`s the page.
  - **It stakes nothing.** The operator places the bet by hand at the book; this only
    records it.
  - The row then settles in `_settle_real_bets_for_matches` and gets `clv` +
    `clv_pinnacle` (no mc-CLV, see C3).
  - **It is shown only for picks that have a Coolbet or Unibet-Site quote.** A pick best
    priced at Epicbet or Tonybet, both of which the operator can bet at by hand, cannot be
    logged from this page. Forward-test picks cannot be logged either, because they have
    no `shadow_bet_id`.
  - In use since it shipped: **2 rows** (both 2026-09-15).
- **The tick mark** (`pick-bet-mark.tsx`, `/api/me/pick-marks`) is the operator's *other*
  "I placed this" record: `user_pick_marks(user_id, pick_id, state, marked_at)`,
  0 → 1 reviewed → 2 bet placed.
  - Counts: **715 marks** (423 reviewed, 292 placed). All of them point at `shadow_bets`
    ids.
  - **150 of the 292 "placed" marks have no `real_bets` row**, mostly Aug 22–Sep 13 on
    now-retired bots (`coolbet_value_v1`, `sweep_*`, `pin_*`, `no_pin_*`).
  - Nothing settles, scores or totals the marks. This is the real-money record from the
    period when the owner copied shadow picks by hand (memory: "real-money on shadow
    signals 2026-08-22").
- **Real money toggle** (`CoolbetPlacerToggle` → `/api/admin/coolbet-placer-bots`):
  UPDATE-only on `coolbet_placer_bots.ui_place_enabled`. It is rendered only for bots
  that already have a row (2), and the API 404s on any other name (`route.ts:85-99`).
  The engine intersects the toggle with `PLACEABLE_BOTS`, so the UI can only turn things
  *off*, or turn back on a bot the code already trusts. Both rows are FALSE, with notes
  dated 09-13 and 09-14.
- **Daemons pause** (`CoolbetDaemonsPause` → `/api/admin/coolbet-daemons-pause`): flips
  `daemons_paused`. This is the Imperva footprint switch, not a money switch.

### A6. The detail page `/admin/shadow-bots/[bot]`

`[bot]/page.tsx` (1,004 lines):
- **Reads:** `bots` row, then `shadow_bets_unique WHERE bot_id` (limit 5000), then
  `odds_snapshots` (4 books, 12 h) for pending picks, then `real_bets WHERE bot_id AND
  placed_real IS NOT FALSE`.
- **Prose:** a hand-written `ALLOWED` map of **29 bots, 19 of them retired**. It has no
  entry for `v10_1x2`, `high_roi_global_v2`, `unified_gate`, either in-play bot, or any of
  the 5 forward-test bots; those get a generic header. The file-header comment still says
  "Restricted to the four known shadow bot names", which is stale.
- **Metrics that disagree with the index page:**
  - "Avg CLV" = mean of **raw `clv`** (break-even ≈ the book margin). The index uses
    mc-CLV.
  - The decision rule is **`MIN_SETTLED_FOR_DECISION = 50` and 14 days, ROI ≥ 3% good /
    ≤ −8% bad** (`:15-16, 345-360`). The index uses the pre-registered n ≥ 300 mc-CLV
    rule. Same bot, two pages, two different verdict rules.
  - ROI is computed in JS at exec odds over all settled rows. That is the same basis as
    the view, but computed separately.
- **Forward-test bots** read "No picks yet". `bot_v10_1x2` shows its cohort copies.
- The detail comment for `bot_coolbet_ou_model_v1` says real money is "gated behind env
  COOLBET_UI_MODEL_EDGE_OU=1". That flag was **removed**; `coolbet_placer_bots` replaced
  it (`place_coolbet_ui.py:93-96`).

### A7. Same bot, different numbers on different pages

| Bot | /admin/bots (`simulated_bets`, exec) | /admin/shadow-bots (`shadow_bot_scoreboard`) | /performance (public) | Honest decision metric |
|---|---|---|---|---|
| `bot_v10_1x2` | n 401, ROI +7.1%, "CLV" raw +7.47% | n 355, ROI +5.0%, **mc-CLV −1.57%** (NEGATIVE), OBSERVE | n 397, "CLV" raw +7.33% (Elite only) | Pinnacle-devig +1.33% (n=331) today; #137 reported +2.50% (n=335), not reproduced. Other columns on the same rows: `clv_pinnacle` +5.29%, `clv_pinnacle_live` +0.16% |
| `bot_high_roi_global_v2` | n 52, ROI +21.5% | n 36, ROI +10.2%, mc −1.60% | n 52 (beta) | Pinnacle-devig +3.98% (n=48) |
| `bot_sharp_1x2_v1` | absent | **0** | pooled v1+v4: n 70, mc shown as CLV | pre-registered = v4 only: n 62, mc −2.88% |
| `bot_consensus_d_v1` | absent | **0** | **shown** (hard-coded list, `published > 0`) although it is "recorded, not sent" since 09-23 | 17 settled, mc −11.1% (1x2) |
| `bot_coolbet_trigger_sharp_1x2_v1` | absent | n 257, **LEAD** +1.35% | absent (experimental) | post-09-20 −5.17% (n=13) |
| `bot_inplay_slowstate_v1` | absent | **RETIRE** −43.7% | absent | hit-rate minus de-vigged prob (not computed anywhere) |

Why they differ:
- **Different tables.** sim versus shadow timing-cohort copies (earliest cohort wins).
- **Different CLV definitions.** Raw ratio, Pinnacle raw, Pinnacle de-vigged, Pinnacle
  live, and margin-corrected own-book.
- **Different close functions** (C3).
- **Different era and rule-version pooling.**
- **The /performance leaderboard mixes definitions in one column:** model rows feed
  `avgClv = mean(clv)` (raw, `bot-aggregates.ts` `buildPublicBotStats`) and
  forward-test rows feed `clvMarginCorrected` (`performance/page.tsx:~424`). Both are
  labelled CLV.

---

## B. The control map: every place a bot capability is decided today

Legend for **Type**: **code** = needs a deploy · **DB** = a row or column (migration or
SQL) · **UI** = an admin button · **env** = a `.env` value on a host · **host** =
launchd or systemd state on a machine · **TG** = a Telegram bot command.

### B1. Exists / active / retired

| Control | Type | Where | Read by | Changed by | Today |
|---|---|---|---|---|---|
| `bots` row | DB | table `bots` (UNIQUE name) | everything | migration | 108 rows |
| `bots.retired_at` | DB | — | shadow-bots page, `picks_public_all`, `pick_generator._bot_id` (`pick_generator.py:151`), `pick_trigger_matcher.py:99`, `inplay_collector.py:238`, placer `load_picks` (`place_coolbet_ui.py:663`), `getPublicCohortBotNames` | migration or SQL | 20 NULL |
| `bots.is_active` | DB | trigger `bots_maturity_retired_invariant`: `is_active → false` sets `maturity_label='retired'` and `retired_at` if NULL (**but setting `retired_at` alone does NOT flip `is_active`**) | pipeline live-mode `_bot_active` (`daily_pipeline_v2.py:2694-2712`), placer | migration or SQL | 20 true |
| `bots.maturity_label` ∈ {experimental, testing, beta, calibrated, retired} (CHECK) | DB | — | **also a publish switch** (B3) and a **real-money source filter** (`pick_generator.py:405`) | migration or SQL | experimental 13, testing 4, beta 2, calibrated 1 |
| Registry `BOTS` list | code | `workers/registry/bot_registry.py:74-280` | smoke drift tests, `gen_frontend_floors.py` → web `ENGINE_BOT_FLOORS` | deploy | 20 specs |
| Code paths that **ignore retirement** | code | pipeline shadow timing cohorts (`SHADOW-RETIRED-OK`, `daily_pipeline_v2.py:3538-3541`); `ou35_model_shadow.py:42`, `corners_paper_bot.py:116-119`, `team_total_paper_bot.py:82`, `first_half_1x2_paper_bot.py:51` (name-only lookup); `TRIGGER_CONFIGS` still lists 2 retired bots | — | deploy | ~59k retired-bot shadow rows / 30 d (#137 B4) |
| Signaler candidate query | code | `coolbet_signaler.py:103-160`: **no `retired_at` filter** on `simulated_bets` | model Telegram | deploy | harmless today, because the pipeline does not write sim rows for retired bots |

### B2. Collects / generates picks

| Family | Job / trigger (where scheduled) | Writes | Per-bot on/off switch today |
|---|---|---|---|
| Pipeline (`bot_v10_1x2`, `bot_high_roi_global_v2`) | `morning_pipeline` 04:00 + `betting_refresh_interval` :05/:35 (`daily_pipeline_v2`) | `simulated_bets` | DB retirement; `BOTS_CONFIG` dict (code); env `DISABLE_PAPER_BETTING` (all bots, `kill_switches.py`) |
| Pipeline timing cohorts | `job_shadow_run_interval` :10/:40 (`scheduler.py:3491`) | `shadow_bets` (cohort `HHMM`) | none. Runs retired bots on purpose |
| `pick_generator` (`CONFIGS`, `TRIGGER_CONFIGS` in `bot_configs.py`) | `on_odds_written` after every Coolbet/Unibet-Site sweep (`pick_generator.py:660`) + mirror jobs :10/:40 | `shadow_bets` | DB retirement; config list (code) |
| `pick_triggers` → `pick_trigger_matcher` | :05 hourly → :15/:45 | `shadow_bets` | DB retirement; `BOOK_MARKET_BOTS` (code, `pick_trigger_matcher.py:31-60`) |
| `ou35_model_shadow` | :10/:40 | `shadow_bets` | none (env `OU35_MODEL_EDGE_FLOOR` changes the floor only) |
| In-play rig | `oddsintel-inplay-collector.service` (VPS systemd) | `shadow_bets` (`inplay_minute` set) | DB retirement; systemd unit; env `DISABLE_INPLAY_STRATEGIES` |
| Forward test (5 bots + junk) | `job_publish_picks_forward_test` :05/:35 (`scheduler.py:3967`) | `picks_forward_test` (+ `picks_board`, candidate funnel) | **`publishing_paused`** (DB/TG), which also stops recording. `RULE_VERSION` / `CONSENSUS_RULE_VERSION` constants (code). **No per-bot switch, and no retirement check**: the arm/grade → bot mapping never consults `bots` |
| Paper modules (corners, team total, 1H) | 08/12/16/20 :20-27 | `shadow_bets` | none. Retired bots, still writing |

Other ledgers written by these jobs that are not bots: `picks_board` (watchlist),
`published_picks` (06:45 accuracy log, `publish_daily_picks.py`), and the candidate
funnel.

### B3. Published: /picks, /performance, the public track record, Telegram

| Surface | What decides that a bot appears | Type | Where | Today |
|---|---|---|---|---|
| **/picks, model arm** | `bots.show_on_picks AND retired_at IS NULL`, single, pre-match, not postponed | DB | view `picks_public_all` (migration 402, second UNION branch) | TRUE: `v10_1x2`, `sharp_1x2`, `sharp_ou` (active) + `v10_ou`, `sharp_forward_test_v1` (retired, filtered out) |
| **/picks, sharp and consensus arms** | `p.arm IN ('live','consensus_anchor') AND NOT (grade='D' AND telegram_message_id IS NULL)`. **Does not read `show_on_picks`.** Bot name comes from the `CASE` in the view | code (view SQL) | view `picks_public_all` (first branch) | consensus B/C published with `show_on_picks=FALSE` |
| /picks watchlist | every leg at or above break-even | code | `picks_board` via `picks_board_public` | — |
| **/performance leaderboard** | sim bots: `!retired && maturity ∈ {calibrated, beta} && !LEDGER_BACKED_BOTS` (`bot-aggregates.ts:348-374`); forward-test bots: hard-coded `PUBLISHED_ARM_BOTS` list, shown when `published > 0` (`performance/page.tsx:398-404`) | code + DB | web | `v10_1x2`, `high_roi_global_v2`, the 5 forward-test bots **incl. grade D** |
| /performance hero, history, `/api/v1/track-record` | `maturity ∈ {calibrated, beta, active}`, not retired, not `inplay_%` (`engine-data.ts:1493, 1522`; `track-record/route.ts:48`) | code + DB | `simulated_bets` only | `v10_1x2`, `high_roi_global_v2` |
| **Anchored public ledger** (`ledger/*.json`, GitHub-signed + `.ots`) | `maturity_label = 'calibrated'`, `simulated_bets` only (`export_track_record_snapshot.py:137-141`) | code + DB | engine script | `v10_1x2` only. **The forward test is not in it** |
| **Telegram public channel (@oddsintelpicks), model arm** | any bot in the (match, market, selection) group is `calibrated`, market in `_PUBLIC_MARKETS` = {1x2, o/u, over_under_25, btts}, clears `clears_edge_floor()` (**the placer's per-market floor, not the bot's own**), `signaled_at IS NULL`, and `publishing_paused` false | DB + code | `coolbet_signaler.py:79-222, 292, 568`; `betting_pipeline.py:86-120` | `v10_1x2`. **`show_on_picks` is not read**, so /picks and Telegram use different switches for the same arm |
| **Telegram public, sharp arm** | every `claim()`ed live-arm row (`scheduler.py:2717-2733`) | code | scheduler | on |
| **Telegram public, consensus arm** | claimed rows except grade D (`scheduler.py:2745-2762`) | code | scheduler | B, C on; D off since 09-23 |
| Telegram operator (private) | per-pick alerts: env `OPERATOR_PICK_ALERTS` (`telegram.py:91-117`); shadow summaries: `notify_shadow_picks` + env `SHADOW_TELEGRAM_ENABLED`, called **only from retired-bot passes** (`daily_pipeline_v2.py:4641, 4902, 5213, 5452, 5660`) | env | — | effectively dead for active bots |
| Channel target | env `TELEGRAM_PUBLIC_CHANNEL`, `TELEGRAM_BOT_TOKEN` (`telegram.py:216-217`) | env | VPS | not read in this audit (VPS `.env` read was denied) |
| Global publish kill | `publishing_paused` via TG `/pausepicks` `/resumepicks` (web `telegram/webhook/route.ts:235-265`) | DB / TG | read by signaler and forward-test job | live |

### B4. Real-money placement

Every check that must pass before a real stake:

| # | Gate | Type | Where | Fails | Today |
|---|---|---|---|---|---|
| 1 | Bot in `PLACEABLE_BOTS` | code | `placement_gate.py:77` = {`bot_coolbet_ou_model_v1`, `bot_coolbet_1x2_model_v1`}; registry `real_money=True` must match (smoke) | closed | 2 bots |
| 2 | `coolbet_placer_bots.ui_place_enabled` | DB / UI | read by `ui_place_enabled_bots()` (`placement_gate.py:80-95`); written by the scoreboard toggle (UPDATE only) | closed (empty set) | both FALSE |
| 3 | `placement_paused` | DB / TG | `coolbet_state.is_placement_paused` (`:310`, fails closed); set by TG `/pause` and `/resume` (`webhook/route.ts:198-228`) and `set_placement_paused` (`:440`) | closed | **TRUE** (strategic, OWN-PATH-VERDICT 09-14) |
| 4 | `real_money_armed` | DB | `is_real_money_armed` (`:338`); **no UI, no CLI, no caller of `set_real_money_armed`**, so SQL by hand (**CHANGED 2026-09-24, #139 phase A:** owner-only two-step Arm on /admin/bots via `admin_arm_real_money`, audited in `control_changes`) | closed | FALSE (migration 354) |
| 5 | Router opt-in `ROUTER_ALLOW_REAL` | env | `best_price_router.py:481`; `coolbet_control.py:277` | — | **`true` in the Mac engine `.env`**. VPS not checked |
| 6 | An `--execute` executor loaded | host | Mac launchd: `com.oddsintel.coolbet-ui-placer` (`place_coolbet_ui.py --all-enabled --execute`, hourly 06-21) and `com.oddsintel.best-price-router` (`-m workers.automation.best_price_router --execute`, :20/:50) | — | **both in `~/Library/LaunchAgents/paused/`, not loaded** |
| 7 | Kickoff cutoff 3 min, daily caps 80 / €800 | code / env | `place_coolbet_ui.py:167, 196-197` (`COOLBET_MAX_*` env) | closed | — |
| 8 | Per-match exposure | code | `exposure_conflict` (callers) | — | — |
| 9 | Books | code | `PLACEABLE_BOOKS = ("Coolbet","Unibet-Site")` (`best_price_router.py:47`) | — | 2 books |
| 10 | Candidate source | code | `load_picks` = `shadow_bets_unique` of the named bot, not retired, `is_active`, pending, future KO (`place_coolbet_ui.py:641-668`) | — | **only shadow-ledger bots can ever stake** |

The executors:
- **`scripts/place_coolbet_ui.py`** (Mac). It calls `assert_run_may_place()` and
  degrades to a dry run on refusal (`:977`). Per pick it calls `assert_may_place` via
  `coolbet_ui_placer.py:1588`.
- **`best_price_router.route`** (Mac). It chooses Coolbet or Unibet-Site and reads
  `effective_allowlist()` only when real (`:515-516`). It calls `assert_may_place` before
  the Unibet dispatch (`:305`). **`unibet_placer.py` has no gate of its own**; it relies on
  the router.
- **`coolbet_mac_daemon.py`**. Hard-coded `execute=False` (`:657-659`). Last tick
  2026-09-10.
- **`_drain_manual_placement_queue`** (VPS, every 10 s, `scheduler.py:1801, 3223`).
  Record-only (`execute=False` literal). Fed by the TG "Record at Coolbet" button.
- **`coolbet_control.can_stake()`** (`:288-303`) is the only function that answers "can
  anything stake?", on the Mac only: gates 3, 4 and 2 plus (plist loaded OR
  `ROUTER_ALLOW_REAL`).

How the model-derived picks reach real money: `bot_v10_1x2` (sim) → mirrored by
`pick_generator` `prob_source='pipeline'` into `bot_coolbet_1x2_model_v1` shadow rows,
**only from bots whose `maturity_label = 'calibrated'`** (`pick_generator.py:101, 405`).
So **`maturity_label` is also a real-money source switch.** Relabel `v10_1x2` as beta and
the placer loses its only candidate source. `bot_coolbet_ou_model_v1` already has no
calibrated O/U source (#137 C9).

### B5. Manual "I placed this" logging

| Path | Writes | Link to the pick | Settles and scores? | Use |
|---|---|---|---|---|
| `/admin/shadow-bots` **Place** → `record_manual_real_bet` | `real_bets` (`placed_real NULL`, notes "manual via shadow-bots") | `shadow_bet_id` (UNIQUE), `bot_id` | yes, raw CLV + Pinnacle | 2 rows |
| TG "Record at Coolbet" → `manual_placement_queue` → `place_bet_by_id` | `real_bets` | `simulated_bet_id` | yes | legacy |
| `/admin/real-bets`, `/api/admin/record-combo` | `real_bets` | optional | yes | — |
| Account reconciler (`reconcile_account_to_real_bets`, `place_coolbet_ui.py:507`) | `real_bets` (`placed_real TRUE`) | matched by selection | yes | ui-placer era |
| Tick mark (`/api/me/pick-marks`) | `user_pick_marks` state 2 | `pick_id` (a shadow id in practice) | **no** | 292 rows, 150 with no `real_bets` row |
| `simulated_bets.user_placed_at / user_skipped_at` (TG buttons) | `simulated_bets` | itself | no | excluded from mirror candidates (`pick_generator.py:410-411`) |

A published forward-test pick has **no** manual-log path of any kind.

### B6. Settlement and CLV: which ledger gets which CLV

All of these run from `job_settlement` at 21:00, 23:30 and 01:00 (`scheduler.py:3896-3900`)
and from `settle_ready_matches`:

| Ledger | Function | Close used | CLV columns written | Bankroll |
|---|---|---|---|---|
| `simulated_bets` | `_settle_pending_bets` (`settlement.py:3792`) | `get_closing_odds(own_book)`, **no freshness bound** | `clv` (raw own-book), `clv_pinnacle` (odds×devig−1), `clv_pinnacle_live`, `clv_live`; `clv_pinnacle_devig` via backfill scripts. **No `clv_margin_corrected` column** | **updates `bots.current_bankroll`** with stored `pnl` (`:3998`, also `:2606`) |
| `shadow_bets` | `_settle_pending_shadow_bets` (`:4016`); the paper modules settle their own rows (`corners_paper_bot.py` etc.) | **`get_book_close(recommended_bookmaker)`, ≤60 min pre-KO, no fallback** | `clv`, `clv_pinnacle`, `clv_live`, `clv_pinnacle_live`, `closing_margin`, **`clv_margin_corrected`**, `closing_minutes_before_ko` | none (flat €10 `pnl`) |
| `picks_forward_test` | `settle_picks_forward_test` (`:1810`); all arms settled identically (by design, the junk control) | `get_closing_odds(bookmaker)`, **no freshness bound** | `clv` (raw), **`clv_margin_corrected`** (4 dp) | none (1 unit; outcome `push` for voids) |
| `real_bets` | `_settle_real_bets_for_matches` (`:1581`) → `real_bet_closing` (`:1538`) | `get_book_close` via `_VENUE_SNAPSHOT_BOOK`, ≤60 min | `clv`, `clv_pinnacle`, `closing_*`. **No mc-CLV** | — |
| `picks_board` | `settle_picks_board` (`:1951`) | — | outcome only | — |

### B7. The capability × control matrix, per active bot

Column key:
- **Ledger**: sim = `simulated_bets`, sh = `shadow_bets`, pft = `picks_forward_test`.
- **/picks**: M = model arm via `show_on_picks`; A = arm hard-coded in the view.
- **/perf**: L = leaderboard; H = hero/API.
- **TG pub**: public-channel eligibility.
- **Anch**: in the anchored `ledger/`.
- **Real-$ chain**: PLACEABLE_BOTS / `ui_place_enabled` / what feeds it.
- **Place btn**: can be hand-logged from /admin/shadow-bots.
- **Score shown on shadow-bots**: what the page shows.

| Bot | is_active / maturity | Ledger | /picks | /perf | TG pub | Anch | Real-$ chain | Place btn | Score shown on shadow-bots |
|---|---|---|---|---|---|---|---|---|---|
| `bot_v10_1x2` | ✓ calibrated | sim (+sh cohorts) | **M** (show_on_picks T) | L, H | ✓ (calibrated) | ✓ | source for `coolbet_1x2_model` | no (no sim pending on page; cohort rows are ~0 pending) | cohort copies |
| `bot_high_roi_global_v2` | ✓ beta | sim (+sh) | ✗ (F) | L, H | only if a calibrated bot agrees | ✗ | — | as above | cohort copies |
| `bot_coolbet_1x2_model_v1` | ✓ experimental | sh | ✗ | ✗ | ✗ | ✗ | **in PLACEABLE_BOTS; toggle F** | yes | own |
| `bot_coolbet_ou_model_v1` | ✓ experimental | sh | ✗ | ✗ | ✗ | ✗ | **in PLACEABLE_BOTS; toggle F; no source** | yes | own |
| 4 per-book sharp triggers | ✓ experimental | sh | ✗ | ✗ | ✗ | ✗ | not placeable | yes | own |
| `bot_trigger_1x2_sharp_v1`, `_ou_sharp_v1` | ✓ experimental | sh | ✗ | ✗ | ✗ | ✗ | not placeable | yes | own |
| `bot_trigger_1x2_sharp_tight_v1` | ✓ experimental | sh | ✗ | ✗ | ✗ | ✗ | not placeable (pre-reg instrument) | yes | own |
| `bot_unified_gate_1x2_paper_v1` | ✓ experimental | sh | ✗ | ✗ | ✗ | ✗ | not placeable | yes (45 pending rows!) | own |
| `bot_ou35_model_v1` | ✓ experimental | sh | ✗ | ✗ | ✗ | ✗ | not placeable | yes | own |
| `bot_inplay_slowstate_v1` | ✓ experimental | sh (in-play) | ✗ | ✗ | ✗ | ✗ | no in-play placer | no (in-play) | own; RETIRE on inadmissible CLV |
| `bot_inplay_slowstate_afctl_v1` | ✓ experimental | sh (in-play) | ✗ | ✗ | ✗ | ✗ | control | no | 0 CLV |
| `bot_sharp_1x2_v1` | ✓ testing | pft live/1x2 | **A** (show_on_picks T, not read) | L (injected) | ✓ | ✗ | none possible | no | **0 (wrong)** |
| `bot_sharp_ou_v1` | ✓ testing | pft live/O/U | **A** | L | ✓ | ✗ | none | no | **0** |
| `bot_consensus_b_v1` | ✓ beta | pft consensus/B | **A** (show_on_picks **F**) | L | ✓ | ✗ | none | no | **0** |
| `bot_consensus_c_v1` | ✓ testing | pft consensus/C | **A** (F) | L | ✓ | ✗ | none | no | **0** |
| `bot_consensus_d_v1` | ✓ testing | pft consensus/D | only rows sent before 09-23 | **L (shown although unpublished)** | ✗ since 09-23 | ✗ | none | no | **0** |

Global switches over all of the above:
- `placement_paused` TRUE
- `real_money_armed` FALSE
- `publishing_paused` FALSE
- `daemons_paused` FALSE
- `ROUTER_ALLOW_REAL` true on the Mac
- executor plists parked
- `DISABLE_PAPER_BETTING` (VPS, not read)

---

## C. Data-model facts the unified design must respect

### C1. The ledgers side by side

| Concept | `simulated_bets` (62 cols) | `shadow_bets` (38) | `picks_forward_test` (28) | `real_bets` (26) |
|---|---|---|---|---|
| bot identity | `bot_id` FK | `bot_id` FK | **none**: `arm` + `grade` + `market` → view `CASE` | `bot_id` (nullable) |
| uniqueness | (bot, match, market, selection) | (**shadow_cohort**, bot, match, market, selection). A bot's pick exists once per cohort; `shadow_bets_unique` keeps the earliest | (match, market, selection, **arm**) | `shadow_bet_id` partial unique; same-day selection dedupe in `record_manual_real_bet` |
| price | `odds_at_pick` (+ `odds_at_pick_live`, `odds_at_open`, `odds_drift`) | `odds_at_pick` (+ `_live`). **Upserts overwrite the price and edge while keeping `pick_time`** (#137 B3b/c) | `odds` + `odds_quoted_at` (**frozen at claim**) | `captured_odds`, `actual_odds`, `slippage_pct` |
| fair probability | `model_probability`, `calibrated_prob` | same | `p_sharp` (+ `anchor_odds` jsonb, `anchor_overround`, `anchor_quoted_at`, `alignment_gap_minutes`) | — |
| edge | `edge_percent` (numeric(5,2) until mig 367) | `edge_percent` | `edge` (multiplicative `P×odds−1`) | `edge_pct_taken` |
| book | `recommended_bookmaker` | `recommended_bookmaker` | `bookmaker`, `anchor_bookmaker` | `bookmaker` (e.g. 'Unibet', mapped to the 'Unibet-Site' feed) |
| rule/model version | `model_version` | `model_version`, `strategy_profile` | **`rule_version`** (pre-registered) | — |
| timing | `pick_time`, `timing_cohort`, `signaled_at` | `pick_time`, `shadow_cohort`, `decision_quote_age_min`, `inplay_*` | `published_at`, `kickoff_at` | `placed_at` |
| result | enum `bet_result` pending/won/lost/void | same | **text** NULL/won/lost/void/push | text |
| stake / pnl | **Kelly** (v10_1x2 €1.35–16.92, mean 7.04), `bankroll_after` | **flat €10** | **1 unit** (pnl −1.0 on loss) | actual EUR |
| CLV | 5 flavours, no mc | 6 flavours incl. **mc** | raw + **mc** | raw + Pinnacle |
| publish trace | `signaled_at`, `signal_message_id`, `admin_offered_at` | — | `telegram_message_id`, `grade`, `grade_reasons` | — |
| anon read | **yes** (needed by /admin/bots, #072) | no | no (views are) | no |

### C2. Stake and bankroll semantics

- `bots.starting_bankroll` / `current_bankroll` mean something **only for sim bots**.
  Settlement moves them with stored (high-water) P&L. Every other active bot sits at 1.00,
  or at 1000 for the in-play and tight bots, and never moves.
- /performance invents a bankroll for the forward-test rows:
  `1000 + pnlUnits × 10` (`performance/page.tsx`, `PICKS_FORWARD_TEST_*`).
- Every ROI a page shows is **flat-stake at exec odds**. Stored Kelly P&L survives only
  in the Bankroll column (#137 C3).

### C3. Settlement paths and closing lines

See B6. The unified design must pick **one** close definition for the decision metric.
Today shadow and real use a fresh (≤60 min) own-book close; forward test and sim use an
unbounded one. So a "single scoreboard" over all three ledgers is not comparable until
this is settled, or unless each row is labelled with its close basis
(`closing_minutes_before_ko` exists on shadow, sim and real; pft has none).

### C4. Bot identity for the forward test

`picks_forward_test` rows become bots only by convention:

| Copy of the arm/grade/market → bot mapping | Where |
|---|---|
| 1 | view `picks_public_all` `CASE` (migration 402) |
| 2 | `performance/page.tsx` `PUBLISHED_ARM_BOTS` |
| 3 | `bot-aggregates.ts` `LEDGER_BACKED_BOTS` |
| 4 | `publish_picks_forward_test.py:702` funnel map |
| 5 (stale) | view `picks_forward_test_shadow` → retired `bot_sharp_forward_test_v1`; referenced only in `bot_registry.py` prose |

The `CASE`'s `ELSE 'bot_sharp_1x2_v1'` catches any future live-arm market. The junk
control has no bot at all.

### C5. Pre-registration constraints (must not be broken)

- `dev/active/picks-forward-test-preregistration.md:109-111`: *"Locked. Any change to the
  rule, the stopping criterion or the success criterion after the first pick is published
  invalidates the test and starts a new one."*
  - Stops: n=200 mc-CLV < −2%; n=400 mc-CLV < 0; n=800 ROI CI < 0 (`:258-260`).
    *CHANGED 2026-09-25 ([[#156]]): the n=200/400 stops were amended to sharp-anchor CLV relative to the junk control (prereg AMENDMENT 1); n=800 unchanged.*
  - Each version's n is **not carried forward**.
  - The live arm must never gain an edge ceiling (`publish_picks_forward_test.py:127`).
  - The junk arm must be selected from the same pool, with the same cadence and room,
    and settled by the same code (`settlement.py:1810-1817`).
- `RULE_VERSION = "sharp_edge_v4_2026_09_15"` (`publish_picks_forward_test.py:37`) and
  `CONSENSUS_RULE_VERSION = "consensus_edge_v2_2026_09_24"` (`:107`) are **code
  constants** written onto every row.
  - **Consequence for a unified model:** a bot's *config* (floor, cap, anchor, books) is
    part of the pre-registered rule for these five bots. It must stay versioned and
    immutable per `rule_version`, never an editable DB field.
- **Enforced by convention only.** `picks_forward_test` has no immutability trigger
  (only the unique index), and no DB constraint ties a row to a rule definition.
  Protection today is the smoke suite plus review.
- `bot_trigger_1x2_sharp_tight_v1` has its own pre-registration
  (`own-sharp-tight-preregistration.md`: promote only on mc-CLV > 0 at n ≥ 300).
- The public surfaces make claims about the ledger: "Bitcoin-anchored", "every pick
  logged before kickoff" (`/performance` metadata), and a `published_at` that is never
  modified. Any migration that rewrites historical rows or re-maps bot names on public
  rows changes a published record.

### C6. Other state a unified model has to absorb or deliberately leave out

- `picks_board`: the watchlist, first-seen targets frozen.
- `published_picks`: the accuracy log, not a bot.
- `candidate_funnel`: why each near-floor leg was or was not published.
- `user_pick_marks`: the operator's placed/reviewed marks.
- `manual_placement_queue`.
- `coolbet_session_state`: 41 columns, of which 5 are fleet switches.
- `promo_terms` / `promo_ledger`.
- `ledger/*.json` + `.ots` in git.

---

## D. First sketch of a unified model

> **Input to the #139 design doc, not a decision.** Every choice below is open for the
> owner.

### D1. One bot definition

One `bots` row per bot. Today's scattered switches become explicit, typed columns (or a
`bot_capabilities` table with history):

| Field | Values | Replaces |
|---|---|---|
| `lifecycle` | `collecting` → `published` → `retired` (`published` implies `collecting`) | `retired_at`, `is_active`, the lifecycle half of `maturity_label` |
| `ledger` | `simulated` · `shadow` · `forward_test` (+ `arm`, `grade`, `market` selector for forward-test bots) | implicit "which table it writes"; the four arm → bot copies (C4) |
| `publish_picks` | bool | `show_on_picks` (model arm) + the view's arm list (sharp and consensus) |
| `publish_telegram` | bool | `maturity='calibrated'` group rule (model) + scheduler code (sharp and consensus) + the grade-D skip |
| `publish_performance` | bool | `maturity ∈ {calibrated,beta}` + `PUBLISHED_ARM_BOTS` |
| `in_public_ledger` | bool | `maturity='calibrated'` in `export_track_record_snapshot.py` |
| `display_label` | experimental · testing · beta · calibrated | the display half of `maturity_label` (kept as a reader-facing chip only) |
| `real_money_enabled` | bool, with audit fields (who, when, why) | `coolbet_placer_bots.ui_place_enabled` |
| `real_money_eligible` | code-side allowlist, unchanged | `PLACEABLE_BOTS`. **Keep this as a code review gate.** The DB can only narrow it |
| `config` | versioned JSON: anchor, books, edge floor + source, odds floor/cap, ceiling, freshness, outlier gates, market, stake rule, `rule_version` | `BOTS_CONFIG`, `bot_configs.py`, `BOOK_MARKET_BOTS`, registry, `BOT_EDGE_THRESHOLDS`, `ENGINE_BOT_FLOORS`, `place_coolbet_ui.BOT_THRESHOLDS` (E2 of #137). **Exported from code, not edited in the DB**, for any pre-registered bot |
| `decision_metric` | `mc_clv_own_book` · `hitrate_minus_devig` (in-play) · none (control) | the implicit "every bot is judged on mc-CLV" |
| `era_start` | timestamp of the last gate or rule change | stops LEAD and verdicts pooling across regimes |
| `source_bot` | FK, for mirror bots | the hidden `maturity='calibrated'` real-money source filter |

Fleet-level switches stay global and **separate**, as they are now:
- `placement_paused`: the kill switch.
- `real_money_armed`: owner-only. It needs a real, audited control, not SQL.
- `publishing_paused`. **Split it** into "stop sending" and "stop recording", or document
  that it deliberately stops the pre-registered ledger.
- `daemons_paused`.

Enforcement rules the sketch implies:
- **Every generator checks `lifecycle != retired`, in one helper.** Today four job
  modules and the timing cohorts do not. If retired-bot shadow data should keep flowing
  (the owner's 09-18 call), make it an explicit lifecycle value such as
  `retired_collecting`.
- **The forward-test publisher resolves its bots from `bots`** rather than hard-coding
  arms. The arm → bot mapping moves into one table (`bot_id`, `arm`, `grade`, `market`),
  or `bot_id` is added to `picks_forward_test` and backfilled once.
- **The owner's "by default all accumulated and monitored" maps to
  `lifecycle = collecting`.** Publish and real money are opt-in flags on top of it.

### D2. One read view: `bot_ledger` (union of the three, plus a real-money join)

Minimum columns:
- identity: `bot_id`, `bot_name`, `ledger` ('sim' / 'shadow' / 'pft'), `source_row_id`,
  `rule_version` / `model_version`
- the pick: `match_id`, `market` (normalised: `o/u` → `over_under_25` and so on),
  `selection`, `decided_at`, `kickoff_at`
- prices: `decision_odds`, `decision_quote_age_min`, `exec_odds`,
  `book` (normalised to the snapshot feed name)
- probability and edge: `fair_prob`, `fair_prob_source` (model / sharp / consensus /
  book), `edge` **with its unit stated**. Sim and shadow use additive `p − 1/o`; the
  forward test uses multiplicative `p·o − 1`.
- result: `result` (normalised; push → void), `pnl_flat_10`
- closing line: `close_odds`, `close_book`, `close_min_before_ko`, `close_fresh` (≤60)
- CLV: `clv_raw`, `clv_mc` (NULL where the ledger has none), `clv_pinnacle_devig`
- flags: `is_inplay`, `published` (`telegram_message_id` or `signaled_at` present),
  `is_control` (junk)
- real money, from `real_bets`: `real_stake`, `real_odds`, `placed_real`; plus
  `marked_placed` from `user_pick_marks`

Dedup rule stated in the view: one row per (bot, match, market, selection). For the
shadow ledger, choose the earliest cohort (today) or the cohort-less row; the design must
choose. Timing-cohort copies are excluded by default. Per-bot "model A/B" copies need
their own flag.

### D3. One scoreboard, one metric, one label

`bot_board` over `bot_ledger` (this is #137 E1, widened to the forward test):
- n settled and ROI at flat exec odds
- **mc-CLV mean / CI / t, only where `close_fresh`**
- Pinnacle-devig CLV as a secondary column
- **the same numbers since `era_start`**
- a verdict from the bot's own `decision_metric` and pre-registration (n=300 for OWN
  instruments; n=200/400/800 for the forward test)
- the junk control rendered beside the arms it controls

Every page reads it: /admin/bots, /admin/shadow-bots, /performance (public subset) and
SYSTEM_MAP. That removes C22 and A7 as a class of bug.

### D4. Page split (to decide in #139 step 2)

Option A (from #137 E9):
- `/admin/bots` = registry and controls: config, lifecycle, the publish and real-money
  toggles, the scoreboard.
- `/admin/shadow-bots` = "today's picks to act on", across **all three ledgers**, with the
  forward-test picks included and Place able to log any of them.

Option B: one page with tabs. In either case, the detail page reads `bot_ledger` and the
bot's config, and loses the hand-written `ALLOWED` prose map (or moves it to
`bots.strategy_description`).

### D5. Migration hazards

1. **The pre-registered forward test.**
   - Do not change `RULE_VERSION` / `CONSENSUS_RULE_VERSION` semantics, the selection
     order (live claims before consensus), `daily_room()`, the junk arm's pool, or its
     settlement path.
   - Adding `bot_id` to `picks_forward_test` is a schema change on a pre-registered
     ledger. It must be additive only and must not touch outcome, pnl or CLV. Say so in
     the pre-registration doc.
   - Moving "publish" to a per-bot flag must not let the flag silently drop live-arm
     picks, because the stop rules count *published* rows.
2. **Public figures move.**
   - Any change to which rows or which CLV feed /performance, `/api/v1/track-record` or
     `ledger/*.json` changes numbers readers have seen.
   - Unifying the CLV column (raw → mc) will turn `bot_v10_1x2`'s public "+7.3%" into
     "−1.6%" (shadow copies) or "+1.3%" (Pinnacle devig). That is the honest direction,
     but it needs a dated methodology note.
   - The anchored ledger's scope (calibrated sim only) is a published claim. Widening it
     to the forward test is a new claim, not a fix.
3. **The Mac placer.**
   - `ROUTER_ALLOW_REAL=true` is already set on the Mac.
   - Any refactor that renames `coolbet_placer_bots`, changes `load_picks`'s source or
     moves the allowlist must keep **fail-closed on every read**
     (`placement_gate.py` contract, smoke `PLACEMENT-GATE-ALL-EXECUTORS`).
   - Never let a DB flag widen past the code `PLACEABLE_BOTS`.
   - A shared-checkout Mac means a half-deployed refactor is live on the next launchd
     tick, if a plist is ever reloaded.
4. **The `maturity_label` split.** Today it drives /performance, the model-arm Telegram,
   the anchored ledger, the track-record API **and the real-money mirror source**
   (`pick_generator.py:405`). Relabelling a bot during the migration can silently switch
   off Telegram or the placer's only candidate source.
5. **`publishing_paused` coupling.** If it is split into send and record, the scheduler
   job must still claim before it sends (`PUBLISH-CLAIM-BEFORE-SEND`), or it will
   double-post to 62 subscribers.
6. **Retired-bot writers.** Making "retired" self-enforcing everywhere stops about 59k
   rows a month that the owner explicitly chose to keep (09-18). Decide before enforcing.
7. **The `simulated_bets` anon grant.** /admin/bots still needs it (#137 A1). A unified
   view must be service-role only, or #072 regresses.
8. **Ghost records.** 150 "placed" marks with no `real_bets` row. A unified real-money
   record should either backfill them as `placed_real = NULL`, with the operator
   confirming odds and stake, or state that they are excluded.
9. **Registry and SYSTEM_MAP drift tests** (`SYSTEM-MAP-REGISTRY-NOT-DRIFTED`,
   `EVERY-REGISTRY-BOT-IS-VISIBLE`, `FLOORS-ONE-SOURCE-CROSS-LANGUAGE`) pin today's
   shapes. They must move with the model, not be deleted.

### D6. Owner decisions this surfaces

- **a.** Should `publishing_paused` also stop the forward-test *recording*? (Today: yes,
  by accident.)
- **b.** Should grade D appear on /performance? It is recorded but not sent.
- **c.** Should the forward test enter the anchored public ledger?
- **d.** Which CLV is "the" public CLV? And does the model arm's public number switch to
  it?
- **e.** In-play decision metric: build "hit-rate minus de-vigged prob", or hide
  CLV / verdict for in-play?
- **f.** Retired-bot collection: keep as `retired_collecting`, or stop?
- **g.** Real-money arming: give `real_money_armed` an audited control, or keep it
  SQL-only on purpose? And should Telegram `/resume` be able to clear a *strategic*
  `placement_paused`?
- **h.** Should Place log picks at Epicbet and Tonybet, and forward-test picks?

---

### Reproduce

```bash
cd /Users/margussellin/www/odds-intel-engine
q() { python3 -c "from workers.api_clients.db import execute_query as q; [print(r) for r in q('''$1''')]" 2>&1 | grep -v pool; }
# the page's scoreboard, verbatim
q "select bot_name, settled_n, clv_n, clv_mc_mean, clv_mc_sd, settled_roi from shadow_bot_scoreboard order by 1"
# forward-test ledger by arm / rule / grade / market
q "select arm, rule_version, grade, market, count(*), count(*) filter (where outcome in ('won','lost')) st, avg(clv_margin_corrected) mc, count(telegram_message_id) tg from picks_forward_test group by 1,2,3,4 order by 1,2,3,4"
# publish / placement switches
q "select placement_paused, publishing_paused, daemons_paused, real_money_armed, mac_daemon_last_tick_at from coolbet_session_state"
q "select * from coolbet_placer_bots"
q "select name, maturity_label, show_on_picks from bots where retired_at is null order by 1"
# views that decide what is public
q "select pg_get_viewdef('picks_public_all'::regclass, true)"
# LEAD era split
q "select bot_name, pick_time >= '2026-09-20' post, count(clv_margin_corrected), avg(clv_margin_corrected) from shadow_bets_unique where bot_name like '%trigger_sharp%' and result in ('won','lost') group by 1,2 order by 1,2"
# manual real money that never reached real_bets
q "select count(*), count(*) filter (where exists (select 1 from real_bets r where r.match_id=s.match_id and r.market=s.market and r.selection=s.selection)) from user_pick_marks u join shadow_bets s on s.id=u.pick_id where u.state=2"
# Mac executors (host state)
ls ~/Library/LaunchAgents/paused/ ; launchctl list | grep oddsintel
```
