Parent row: **#139 UNIFIED-BOT-MODEL-EPIC** (`PRIORITY_QUEUE.md`). Research input to the design
(`docs/UNIFIED_BOT_MODEL_DESIGN_2026_09_24.md`), phase 3 (switches) and phase 5 (DB unification).

# Bots and their controls: where each one came from and why (2026-09-24)

**Scope.** This is the WHY: when each table, flag and code constant was created, the reason
written down at the time, the incidents that shaped it, and the safety properties that
must survive a merge. The WHAT (every reader and writer today) is covered separately by
`docs/BOTS_AUDIT_2026_09_24.md` (#137) and the #138 control map. This doc cites those rather
than repeating them.

**Method.** `git log --diff-filter=A` / `-S` over migrations, code and docs; the migration
headers (in this repo they are the design record, so most quotes below come from them);
`docs/RELIABILITY_LEDGER.md`; `PRIORITY_QUEUE.md` rows; commit bodies. The DB check is
SELECT-only against the VPS on 2026-09-24. Quotes are short. The source is in brackets.

---

## 0. The short version

1. **`bots` was built on 2026-04-27 as a paper-trading bankroll table.** Every column added
   since then fixed a problem with what *customers* saw. `retired_at` hid bots from
   /performance, `retired_reason` explained the hiding, `maturity_label` badged them,
   `show_on_picks` curated /picks and `display_name` renamed them. None of these columns
   was designed as a *control*, but several became one by accident. The clearest case is
   `maturity_label`, which today gates six different things (§2.2).
2. **Real money has one layer per incident that taught us we needed it.** Each layer covers
   a different way things failed, so they are not redundant copies of each other:
   the code whitelist (a default bot name is not a guard), the DB toggle (flip without a
   deploy, but only ever downwards), the kill switch (stop now), the arming switch (an env
   var being absent is not a pause), launchd agents unloaded on the Mac (a loaded `--execute`
   agent is armed), and the per-pick caps.
3. **Publishing has no single switch today, and every attempt to share one with placement
   caused an outage.** Placement and publishing were split twice (08-27 and 09-15). The
   rule is written down in `RELIABILITY_LEDGER §9b`: split the flag, not the branch.
4. **Which table a bet is written to is itself a safety guard.** OWN bots write only
   `shadow_bets`, and /performance is built only from `simulated_bets`. The pre-registered
   forward test was *deliberately* kept out of both bot ledgers (migrations 342, 345). A
   single `picks` table (design phase 5) removes both guards unless it puts something back.
5. **Retirement and "stop collecting" are two different decisions, on purpose.** Retired
   bots keep writing shadow rows under SHADOW-RETIRED-OK (2026-05-20), which the owner
   restated on 2026-09-18: *"retired bots keep writing, so the accumulated picks can be
   analysed later."* The design doc's `collect = is_active AND retired_at IS NULL`
   contradicts that decision.

---

## 1. Timeline

| Date | Change | Why (one line) | Source |
|---|---|---|---|
| 04-27 | `bots`, `simulated_bets` created | paper trading with a Kelly bankroll per bot | `001_initial_schema.sql`, 69ab9176 |
| 04-29 | `user_picks` | customer's own bet tracker (free-tier feature) | mig 016 |
| 05-10 | `bots.retired_at` | hide merged/dropped bots from public /performance | mig 081 |
| 05-10 | `real_bets` | SELF-USE-VALIDATION: log manual real bets *parallel to* paper | mig 092 |
| 05-13 | `shadow_bets` | BET-TIMING-MONITOR: evaluate every bot at every timing window, no bankroll | mig 101 |
| 05-17 | `bots.retired_reason` | PERF-HONEST-HEADLINE: say why a bot was retired, publicly | mig 104 |
| 05-17 | bankroll normalised to €1k | 10k default gave one bot 10× weight in portfolio ROI | mig 107 |
| 05-20 | SHADOW-RETIRED-OK | retired bots still write shadow rows so recovery is measurable | b4467dee |
| 05-22 | un-retire all (117, 122) | more analysis volume; owner overrode "never re-enable" | migs 117, 122 |
| 05-22 | `bots.strategy_description` | plain-English thesis, separate from the changelog `description` | mig 119 |
| 05-27 | `bots.maturity_label` | performance-page tiers (calibrated/beta/experimental) | mig 134 |
| 05-28 | `profiles.telegram_chat_id` | per-user Telegram value-bet alerts (Pro/Elite) | mig 141 |
| 05-29 | `manual_placement_queue`, `bet_telegram_alerts` | tap-to-place from Telegram, edit alert in place | migs 153, 154 |
| 06-01 | `COOLBET_RECORD_ALLOWED_MATURITY` env | CHERRY-PICK-PLACER: placer stakes only a maturity subset | 3957272f |
| 06-11 | `coolbet_session_state` (+device_id, +placement_paused) | Coolbet session observability; a kill switch that needs no restart | migs 242–244 |
| 06-12 | `jwt_current`; `simulated_bets.user_placed_at/skipped_at` | JWT bootstrap across hosts; ✅ Placed / ⏭ Skip buttons | migs 245, 248 |
| 06-13 | **incident**: beta `bot_high_alignment` auto-placed real money | – | PQ BOT-MATURITY-REVIEW-WEEKLY |
| 06-17 | `coolbet_daemon_commands` | Telegram heal button → Mac daemon queue | mig 254 |
| 06-21 | trigger: `is_active=false` ⇒ `maturity_label='retired'` | 28 stale labels, and one bug from checking the label without `is_active` | mig 257 |
| 08-22 | `user_pick_marks` | operator ticks picks they placed by hand on /picks | mig 278 |
| 08-24 | `bot_config_history` | €1,270 real money lost on sweep bots; make every config recoverable | mig 281 |
| 08-27 | `coolbet_placement_attempts` | one row per ATTEMPT, so "placed nothing" ≠ "had nothing" | mig 288 |
| 08-27 | SIGNAL-PAUSE-DECOUPLE | daemon self-pause had muted Telegram for 4 days / 12 picks | RELIABILITY_LEDGER §9b |
| 08-28 | `EXECUTE_ALLOWED_BOTS` (code) | "a default bot name is NOT a guard" | 18fe686b |
| 09-06 | 2 unearned `calibrated` labels demoted | the label gates the public channel; one bot had 0 bets | mig 304 |
| 09-08 | `coolbet_placer_bots` + `PLACEABLE_BOTS` | runtime per-bot toggle that can only reduce the code whitelist | mig 310, 71015cea |
| 09-09 | `daemons_paused` | calm Imperva from the dashboard; a separate job from the money kill switch | mig 318 |
| 09-09 | `bot_registry.py` | one machine-checked map of what every bot is | 44bd7dfa |
| 09-09 | `real_bets.placed_real` | real vs paper vs legacy rows in the money ledger | mig 325 |
| 09-11 | `bot_configs.py` + `pick_generator` | two near-identical placer modules had drifted "in ways that cost real bets" | ee521550 |
| 09-14 | real money paused (strategic) | OWN-PATH-VERDICT kill criterion met | mig 343 |
| 09-14 | `picks_forward_test` + handle bot | pre-registered PICKS test, kept OUT of bot ledgers | migs 342, 345 |
| 09-14 | retirement made self-enforcing (2 lookups) | retired bots kept generating under every surface | RELIABILITY_LEDGER "A retirement that only changes the DB…" |
| 09-14 | `maturity_label` CHECK | typo `'experiment'` put an OWN bot on /performance | mig 352 |
| 09-15 | `publishing_paused` | the OWN pause had silently muted the customer channel | mig 353 |
| 09-15 | `real_money_armed` + `placement_gate.py` + `real_bets.shadow_bet_id` | "armed under pause"; an env var being absent is not a pause; a FK had been swallowing ledger rows | mig 354, 6fdc37fa |
| 09-15 | `bots.show_on_picks` | a pick reached Telegram but not /picks, and nothing decided which was right | mig 356 |
| 09-17 | unique `real_bets(shadow_bet_id)` | one real row per shadow pick | mig 361 |
| 09-22 | `bots.display_name`, `testing` label | rename for readers without breaking attribution | mig 375 |
| 09-22/23/24 | consensus/sharp arms as bot identities | "users need to see all the picks they receive… under the correct bot" | migs 372, 380, 402 |
| 09-24 | `record_manual_real_bet()` | atomic hand-placed logging; an index would need deleting real-money rows | mig 407 |

---

## 2. Each object: birth, evolution, deliberate decisions, incidents

### 2.1 `bots`

**Birth.** `001_initial_schema.sql` (69ab9176, 2026-04-27, "Live paper trading pipeline").
The columns were `name, strategy, description, starting_bankroll 10000, current_bankroll,
is_active`. It was a **paper-trading account**: the bankroll exists because simulated
stakes are fractional Kelly on it (`stake = kelly × 0.15 × bankroll`, mig 107).

**How rows come into being** (three mechanisms, which is part of today's drift):
- `supabase_client.ensure_bots(BOTS_CONFIG)` **inserts a row automatically** for any name in
  `daily_pipeline_v2.BOTS_CONFIG` or `inplay_bot.INPLAY_BOTS` (bankroll 1000). Here the code
  creates the DB row.
- Standalone paper jobs and every bot since September get their row from **a migration**
  (310, 312, 316, 322, 326, 345, 347, 357, 370, 372, 375, 380, 402…).
- There is no reverse path. Nothing in code deletes or retires a row. Retirement is always
  a migration (32 retire/unretire migrations so far).

**Column evolution.**

| Column | Added | Reason at the time |
|---|---|---|
| `retired_at` | 081, 05-10 | "hide merged/dropped bots from the public /performance page while keeping them visible in the admin dashboard" |
| `retired_reason` | 104, 05-17 | "short prose for why a bot was retired (shown on the public /performance 'Retired Strategies' section)". Today it is also the audit trail |
| `strategy_description` | 119, 05-22 | plain-English thesis. "Separate from description (changelog/technical notes)" |
| `maturity_label` | 134, 05-27 | performance-page tiers (see §2.2) |
| `show_on_picks` | 356, 09-15 | curation of /picks (see §2.3) |
| `display_name` | 375, 09-22 | "DISPLAY ONLY — never join, filter or key on this. `bots.name` is the identity" |
| trigger `bots_maturity_retired_invariant` | 257, 06-21 | `is_active=false` ⇒ `maturity_label='retired'` (+ `retired_at` if NULL) |
| CHECK on `maturity_label` | 352 / 375 | the free-text typo incident |

**Deliberate decisions.**
- `bots.name` is the identity key. `simulated_bets`, `shadow_bets`, `real_bets`,
  `picks_public_all`, `ENGINE_BOT_FLOORS`, `coolbet_placer_bots` (by name, no FK) and
  `bot_config_history` (by name) all reference it. A rename breaks attribution. That is why
  `display_name` exists (375).
- Bots that do not stake are given **bankroll 1, not 10,000** (345): "A 10,000 sitting here
  would be counted by every bankroll rollup as if it were capital at risk." There is a
  `CHECK (starting_bankroll > 0)`, so 0 is not allowed.
- Splitting an identity is how mixed records get exposed. `bot_v10_all` was split into
  `bot_v10_1x2` / `bot_v10_ou` (375) because its two halves sat on opposite sides of zero.
  The sharp bot was split by market (402) and consensus by grade (380). The rule these
  followed: "Split in the VIEWS, not the ledger" (380, 402: "BOOKKEEPING ONLY").
- Re-activation keeps the label it is given. The retired-label trigger fires only on
  `is_active → false`, "because re-activation is an explicit operator choice with deliberate
  label semantics" (257).

**Incidents.** 117/122 un-retired bots and overrode a "never re-enable" note (owner choice,
recorded). 257: 28 inactive bots still carried live labels, and "the 2026-06-13
bot_high_alignment incident traced back to maturity_label being checked without an
accompanying is_active check".

### 2.2 `maturity_label`: one column, six jobs

**Birth.** 134 (5b8a1507, 2026-05-27, PERF-OVERHAUL). The values were
`calibrated/beta/experimental/active` and the purpose was chips on /performance. 151
(05-29) added semantics: *calibrated = backtest-validated with league whitelists; beta =
limited live history; testing = no confirmed signals; active = default, no chip;
experimental = hidden from performance page*.

**What it gates today** (each was added later, for a different reason):

| Job | Where | Since |
|---|---|---|
| Hides a bot from /performance (web allowlist `{calibrated, beta}`) | `odds-intel-web/src/lib/bot-aggregates.ts:348` | 05-27; allowlist since 09-16 (PERF-PUBLIC-IS-CALIBRATED-OR-BETA) |
| Excluded from the headline ROI/CLV in `dashboard_cache` (`!= 'experimental'`) | `settlement.py:3326–3532` | combo era |
| Promotes a pick to the **public Telegram channel** (`bool_or(maturity_label='calibrated')`) | `coolbet_signaler.py:141, 209–222` | SIGNALER-MATURITY-SHADOWING 08-28 |
| **Real-money placer loaders** (`COOLBET_RECORD_ALLOWED_MATURITY`, Mac `.env` = `calibrated`) | `coolbet_placer.py:299–319, 583, 718, 2659` | CHERRY-PICK-PLACER 06-01 |
| **Input to other bots**: `pick_generator` sources its model probabilities from `simulated_bets` of bots whose label is in `cfg.maturity` (default `("calibrated",)`) | `pick_generator.py:101, 405` | 09-11 |
| "Pro" value-bet cohort in `dashboard_cache` (`calibrated`) | `settlement.py:3489` | growth era |

Migration 304 says it plainly: *"`maturity_label = 'calibrated'` is not cosmetic… It is a
claim that a strategy is proven."* 352 says: *"Public-surface gate."*

**Incidents.**
- 06-13: a `beta` bot auto-placed real money (−€56 over 50 real bets). The fix was a weekly
  review with PROMOTE/DEMOTE verdicts rather than a structural gate.
- 09-06 (mig 304): `bot_dnb_specialist` was `calibrated` with **zero bets ever**. Its first
  pick would have gone to the public channel with a "calibrated" label, and the operator
  stakes by hand on those signals.
- 09-14 (mig 352): the typo `'experiment'` meant `'experimental'` did not match, so an OWN
  paper bot rendered on the customer leaderboard. The CHECK constraint came from this.
- Drift today: the web has **three different "public" label sets**:
  `bot-aggregates.PUBLIC_MATURITY_LABELS = {calibrated, beta}`,
  `upcoming-picks.PUBLIC_MATURITY_LABELS = [calibrated]` (+ signed-in `[calibrated, beta,
  active]`), and `engine-data.HEADLINE_MATURITY_LABELS = [calibrated, beta, active]`.
  `active` is no longer a legal value (352 CHECK).
- Knock-on effect: `bot_coolbet_ou_model_v1` has had no candidates since 09-13 (#137).
  Generation stopped when the O/U calibrator was removed (mig 335). Since then its O/U source
  cannot come back: `bot_v10_all` was `calibrated`, but its O/U half became `bot_v10_ou` as
  `beta` (mig 375, outside the default `("calibrated",)` source cohort) and was retired on
  09-24. Splitting and re-labelling one bot changed what *another* bot is able to generate.

### 2.3 `show_on_picks` (and why "publish" has no single switch)

**Birth.** 356 (622f461a, 2026-09-15, PICKS-PAGE-CURATION). The trigger was
Ludogorets II v Fratria, a `bot_v10_all` pick that *"reached the public channel while being
absent from /picks. The channel and the page disagreed, and nothing in the system had an
opinion about which was right."* Design points:
- DEFAULT FALSE: *"A new bot must be opted IN… everything published unless someone
  remembers to switch it off — is how the Ludogorets pick went out."*
- *"This does NOT gate /performance… any future code that filters the leaderboard on this
  column is a bug."* /performance is where results are **measured**, /picks is what is
  **offered**.
- The column comment claims it governs /picks **and the public Telegram channel**.

**What it actually governs today.** Only the model arm of the `picks_public_all` view
(`WHERE b.show_on_picks AND b.retired_at IS NULL …`, re-created in 361/368/372/373/375/380/402)
and therefore /picks and `/api/v1/upcoming`. **No engine Python reads it.** The public
Telegram model-arm sender (`coolbet_signaler`) still gates on `maturity_label='calibrated'`.
So the Ludogorets case is still structurally possible: a calibrated bot with
`show_on_picks=false` posts to Telegram but not /picks, and a beta bot with `true` appears on
/picks but not Telegram. Today the two coincide only because `bot_v10_1x2` is both.

**The flag means different things for forward-test bots.**
`bot_sharp_1x2_v1`/`bot_sharp_ou_v1` have `show_on_picks = TRUE` (402, following 356's "so
the page's two sources are described by one switch"). `bot_consensus_b/c/d_v1` have `FALSE`
(372: *"Setting it TRUE would publish it twice"*), even though B and C are published. For
all five bots the flag has no effect, because their rows reach the public surfaces through
`picks_forward_test` plus the `PUBLISHED_ARMS` constant and grade rules. The actual
publish controls for the forward test are:
- `PUBLISHED_ARMS = ("live", CONSENSUS_ARM)` (`scripts/publish_picks_forward_test.py:241`);
  the junk-anchor control is "never sent".
- Grade D is "recorded, NEVER sent" (381).
- `publishing_paused` (§2.7), read by the scheduler job (PUBLISHER-PAUSE-GATE 09-15).

The web code comment `bot-aggregates.ts:340` ("show_on_picks … which nothing reads yet") is
stale: the view has read it since 361.

### 2.4 Retirement: `is_active`, `retired_at`, and "collect"

**The original intent (05-10, mig 081).** Retirement was a *display* concept: hide from
public, keep in admin.

**SHADOW-RETIRED-OK (b4467dee, 2026-05-20).** Retired bots keep producing shadow rows *on
purpose*: *"so the retirement-note recovery criterion ('≥30 bets at ≥3% ROI in
shadow_bets') is actually measurable. They never produce live simulated_bets"*
(`daily_pipeline_v2.py:3537`). **Owner re-affirmed 2026-09-18** (e1074e64): *"retired bots
keep writing, so the accumulated picks can be analysed later"*. That is why that day's work
stamped `model_version` on every shadow writer ("the one property that cannot be backfilled").

**The opposite lesson (09-14, RELIABILITY_LEDGER "A retirement that only changes the DB").**
`pick_generator._bot_id` and `pick_trigger_matcher._bot_id` used a bare `WHERE name=%s`, so
a retired bot *"carried on writing shadow_bets underneath"* while disappearing from every
page. *"Retirement was always verified by looking at the thing that hides retired
bots."* The fix was to add `retired_at IS NULL` to those two lookups, the in-play collector
(`inplay_collector.py:238`) and the placer's `load_picks` (09-24). **Four standalone
modules still use bare lookups** (`corners_paper_bot.py:118`, `first_half_1x2_paper_bot.py:51`,
`ou35_model_shadow.py:42`, `team_total_paper_bot.py:82`). #137 B4 records this as "owner's
call 09-18".

**How to reconcile the two.** Nobody objected to retired bots *writing*. The 09-14 problem
was that the writing was **invisible**. Two independent properties have been folded into one
column:

| Property | Today's carrier | Intended default for a retired bot |
|---|---|---|
| listed / counted as a live strategy | `retired_at IS NULL` | no |
| keeps collecting shadow rows | nothing explicit (writer-by-writer behaviour) | **yes** (owner 05-20, 09-18) |

The volume is large. 59,059 of 62,781 shadow rows in 30 days are retired bots (#137 B4), and
the recovery-criterion job they were kept for **does not exist** ("has no job that reads it").
Whether to keep collecting them is an open owner decision (#137 "Waiting on owner"). It must
not be decided by a view definition.

### 2.5 `real_bets`

**Birth.** 092 (ef2a671d, 2026-05-10, SELF-USE-VALIDATION Phase 2): *"Real-money bets placed
manually at accessible bookmakers, parallel to simulated_bets (paper trading)."* It has
superadmin-only RLS and `bookmaker REFERENCES accessible_bookmakers` (still enforced today).
It records `captured_odds` vs `actual_odds` and a generated `slippage_pct`.

**Evolution.**

| Change | Mig | Why |
|---|---|---|
| `combo_legs`, `system_type` | 118 (05-22) | manually placed combos (combos retired 09-08; rows kept) |
| phantom row voided + NOT EXISTS guard | 123 (05-23) | auto path wrote `ticket=None` next to the real manual bet |
| `edge_pct_taken`, `clv` | 125 (05-23) | how our actual prices did vs pick and close |
| `closing_*`, `clv_pinnacle`; `clv` vs own book | 300, 332 (09-11) | stored CLV was vs "whichever of ~13 books sorted last"; real bets are at a direct book |
| `placed_real` (tri-state) | 325 (09-09) | the table mixed REAL, PAPER (paper daemon) and LEGACY rows. *"TAG, do NOT delete (deleting money records is irreversible)"* |
| `shadow_bet_id` FK | 354 (09-15) | real-money bots pick from `shadow_bets`, but the id went into `simulated_bet_id` (FK → `simulated_bets`): *"Money moved; the ledger did not know"* |
| unique `(shadow_bet_id) WHERE NOT NULL` | 361 (09-17) | one real row per shadow pick. NOT a general unique key: *"a repeat bet on the same selection is a legitimate thing to do"* |
| `record_manual_real_bet()` | 407 (09-24) | double-click race. An index was rejected because it would require deleting 5 real-money duplicate groups, *"the owner's call, not a migration's"* |

**Deliberate decisions to keep.** `placed_real` has three states because of
RELIABILITY_LEDGER §5 ("A placement you cannot confirm is not a placement that did not
happen"). `NULL` counts as **exposure** (`placed_real IS NOT FALSE`), so an uncertain click is
never retried. The manual path writes `NULL` on purpose (407). Money rows are never deleted
by migration. The table carries **two pick FKs** because picks live in two tables.

### 2.6 `coolbet_placer_bots` (per-bot real-money toggle)

**Predecessors.** First `COOLBET_RECORD_ALLOWED_MATURITY` (env, 06-01, label-based). Then
`EXECUTE_ALLOWED_BOTS` (code, 18fe686b, 08-28): *"Only bot_coolbet_value_v1 may be placed with
--execute… a default bot name is NOT a guard — --bot could name anything and --execute would
have honoured it. Experimental bots can now run the entire pipeline… with no path by which
an unproven strategy reaches the account."* Then the env flag `COOLBET_UI_MODEL_EDGE_OU`.

**Birth.** 310 (71015cea, 2026-09-08, COOLBET-PLACER-CONTROL): *"Flipping a bot on or off
meant an edit + deploy. This table makes it a runtime toggle."* The safety model is stated in
full:
> *"this table can only ever REDUCE what places, never expand it… effective allowlist =
> PLACEABLE_BOTS ∩ (rows here WHERE ui_place_enabled = true)… on ANY DB error the placer
> reads this as the EMPTY set… an attacker or a mistake in this table can turn placement OFF
> but can never turn on a bot the code does not already trust."*

Also in 310: deny-all RLS for anon/authenticated (*"a direct PostgREST call under the anon
key could flip a toggle… sidestepping the superadmin check"*); `ON CONFLICT DO NOTHING`
(*"the seed must NOT clobber a value a human has since changed"*); the web route is
**UPDATE-only, never INSERT** (`api/admin/coolbet-placer-bots/route.ts:18, 85`).

**Evolution.** 314 (09-08) deleted the value_v1 row, *"removes it from the /admin control
block and drops it from the effective allowlist… even though it stays in PLACEABLE_BOTS."*
So deleting the row is also a way to switch off. 343 (09-14) set 1x2 to OFF.

**Incident, the note and the flag disagreed** (343): `bot_coolbet_1x2_model_v1` *"carries the
note 'OFF pending dry-run' from 2026-09-08 while `ui_place_enabled` reads TRUE… the flag is
what the placer reads."* The web toggle writes `ui_place_enabled` and `updated_at`, **not**
`note`. There is no reason field and no audit row for a flip.

### 2.7 `coolbet_session_state` (singleton): fleet switches in a Coolbet-named table

**Birth.** 242 (73732528, 06-11): observability for the FlareSolverr/Coolbet session, *"one
row… doesn't store the JWT or cookies"* (the JWT was later added under 245, with anon RLS
dropped). Session and auth columns (login, heartbeat, device_id 243, jwt 245, mac-daemon /
prekickoff heartbeats 251/252, auto-login 255, Imperva cookies 269) are **transport state**.
Four columns are **fleet-level control switches**:

| Switch | Mig | Default | On read error | Why it is its own column |
|---|---|---|---|---|
| `placement_paused` (+`_at`, `_reason`) | 244, 06-11 | false | **CLOSED** (paused) since 09-15 | *"Why a DB flag, not an env var: env-var flip requires Railway service restart… flipping it via Telegram /pause takes effect on the next cron tick."* KILL switch |
| `daemons_paused` | 318, 09-09 | false | – | *"distinct from placement_paused (the real-money kill switch)"*: reduce Imperva footprint. *"a button cannot launchctl the Mac — this DB flag is the seam the daemons read."* |
| `publishing_paused` | 353, 09-15 | false | **OPEN** (not paused), deliberately | *"a decision to stop staking our own money (OWN) is not a decision to stop publishing picks (PICKS)"* |
| `real_money_armed` | 354, 09-15 | **false** | **CLOSED** (not armed) | ARMING switch. *"They are separate because their defaults differ: a fresh row is NOT paused (nothing to stop) and NOT armed (nothing may start)."* *"The owner arms it explicitly, with a reason… never a migration, never a deploy side effect."* |

Who writes each switch (this is a design choice too):
- `placement_paused`: Telegram `/pause` `/resume` (web webhook direct UPDATE) plus the daemon
  self-pause (marker `"daemon self-pause"`, auto-clearable).
- `publishing_paused`: `/pausepicks` `/resumepicks`.
- `real_money_armed`: **only** `coolbet_state.set_real_money_armed()`, which refuses to arm
  without a reason. No web or Telegram path writes it.

**Incidents.**
- 08-27 and 09-14/15 (§9b): `placement_paused` also muted the customer channel, twice.
- 09-14 (343): `placement_paused_reason` still read "Imperva recovery", which is *"how a
  pause gets casually cleared by whoever next fixes the transport"*. The reason now spells
  out that this is a strategic stop.
- 09-15 (§14): `is_placement_paused()` failed OPEN while `ui_place_enabled_bots()` failed
  CLOSED: *"Same money, two reads, opposite defaults."*

### 2.8 `PLACEABLE_BOTS` and `placement_gate.py`

`PLACEABLE_BOTS = {"bot_coolbet_ou_model_v1", "bot_coolbet_1x2_model_v1"}`
(`workers/automation/placement_gate.py:77`). It moved there from `scripts/place_coolbet_ui.py`
on 09-15 and is re-exported so the smoke pins keep working.

**Why it is a code constant, not a DB flag** (said three times, by three authors):
- 18fe686b (08-28): a default is not a guard.
- 310: the DB may only *reduce* it.
- `placement_gate.py`: *"Adding a bot here is a code review, not a config change; that is the
  point."*
- `bot_registry.py`: *"PLACEABLE_BOTS stays hardcoded… as a defense-in-depth safety set — this
  registry does not re-derive it, the drift test only asserts the two agree."*

**`placement_gate.py`** (6fdc37fa, 09-15). Its docstring lists four defects found that day:
the kill switch failed open; the pause was read *after* the stake was typed; the router
iterated `PLACEABLE_BOTS` rather than the intersection; the VPS manual-place drain read no
pause at all (paper only because `execute=False` was a literal); and two `--execute` launchd
jobs were loaded. Its answer is *"not a fourth copy of the checks — it is ONE function that
every path must call FIRST, that FAILS CLOSED on every read"*. The order is: pause → armed →
allowlist → kickoff cutoff → daily caps. It raises; it never returns False (*"so that it
cannot be dropped like a False"*). Publishing is **deliberately excluded**: *"the two must
never be re-unified."* The smoke tests are `PLACEMENT-GATE-FAIL-CLOSED`,
`-ARMED-REQUIRED`, `-ALL-EXECUTORS` and `ROUTER-NO-ALLOWLIST-BYPASS`.

### 2.9 `ROUTER_ALLOW_REAL`, the Mac `.env`, and launchd

**Why the executors are on the Mac.** Imperva blocks Coolbet login from cloud IPs (245:
*"Railway can't (Imperva 403's /s/auth/login from cloud IPs)"*). Real-money executors
therefore run on the operator's Mac (residential IP), while the control plane (web, DB) runs
on the VPS. Every Mac-side switch has to be a **DB flag the Mac polls** (318: "the seam") or
a Mac-local thing (env, launchd) that the dashboard cannot change.

**`ROUTER_ALLOW_REAL`.** Introduced 09-10 (7f59c1c6, BEST-PRICE-ROUTER execute path) as the
"owner-gated cutover": `--execute` degrades to report-only unless the env var is truthy. The
09-12 cutover set it to `true` in `.env`. Two audits then recorded it as "unset". On 09-15 it
turned out the router *"ran in real mode every 30 minutes and staked nothing only because its
pick loader found zero candidates"* (§14). The lesson: **"an env var's ABSENCE on one host is
not a pause. It is an accident that has not happened yet"** (354). `real_money_armed`
replaced it as the "is money allowed at all" switch. The plan was to keep the env var *"as an
additional refusal for one release, then removed"* (own-implementation-plan 0.A).

**Reality today (Mac `.env`, read 2026-09-24):** `ROUTER_ALLOW_REAL=true` and
`COOLBET_RECORD_ALLOWED_MATURITY=calibrated`. So the env var currently **adds no protection
at all**. The router is held back by `real_money_armed=false`, `placement_paused=true`, the
empty allowlist, and its launchd agent being unloaded (`best-price-router.plist` and
`coolbet-ui-placer.plist` sit in `~/Library/LaunchAgents/paused/`).
`RELIABILITY_LEDGER` still lists it as "owner gate — deliberately unset", which is stale.

**launchd is a layer too.** A loaded `--execute` agent counts as "armed" (§14).
`coolbet_control --status` reads the OS and prints `CAN_STAKE`. RELIABILITY §3 ("editing a
config file is not deploying it") applies: the repo plist is not the running plist
(`LAUNCHD-DRIFT-SEMANTIC`).

### 2.10 Bot configuration that lives in code

| Constant | File | Birth | Why it is in code |
|---|---|---|---|
| `BOTS_CONFIG` (35 entries, 25 without `is_active: False`) | `workers/jobs/daily_pipeline_v2.py:81` | 04-27 (69ab9176) | original pipeline; its rows are created by `ensure_bots` |
| `CONFIGS`, `TRIGGER_CONFIGS` (`BotConfig`) | `workers/automation/bot_configs.py`, `pick_generator.py` | 09-11 (ee521550) | *"Adding a bot is an entry in CONFIGS… deliberately NOT a new job file — the two mirrors this replaced… had already drifted apart in ways that cost real bets."* Floors deliberately omitted: *"the 1x2 edge floor had six independent copies once"* |
| `BOTS: list[BotSpec]` | `workers/registry/bot_registry.py` | 09-09 (44bd7dfa) | *"the facts about a bot were scattered (bots table, the web SHADOW_BOTS list, coolbet_placer's PLACEABLE_BOTS, the scheduler) with no one place required to stay true."* Drift test `SYSTEM-MAP-REGISTRY-NOT-DRIFTED` |
| standalone paper bots (`BOT_NAME` + module) | corners / 1H 1x2 / team-total / ou35 / inplay_collector | 09-08..09-15 | one-off experiments, each its own scheduler job |
| `INPLAY_BOTS` | `workers/jobs/inplay_bot.py` | May | in-play betting (retired 08-21) |
| `PUBLISHED_ARMS`, grade rules, `WEAK_MAX_EDGE`… | `scripts/publish_picks_forward_test.py` | 09-14.. | **pre-registered** rules. Code changes are versioned as `rule_version` |
| `PLACEABLE_BOTS` | `placement_gate.py` | see §2.8 | code review is the safety property |
| web lists: `EXPERIMENTAL_BOT_NAMES`, `LEDGER_BACKED_BOTS`, `BOT_SHORT_LABELS`, `INPLAY_CONTROL_BOTS`, `ENGINE_BOT_FLOORS` (generated) | `odds-intel-web/src/lib/…` | various | each covers a gap in what the DB can express |

Env-level bot config also exists: `SHADOW_MODEL_VERSION` (the VPS pins a stale model, #137
C-item), `BOT_COHORT_OVERRIDES` (05-25: *"a deploy-time env change instead of a code edit"*),
`OPERATOR_PICK_ALERTS` (09-11, off), `SHADOW_TELEGRAM_ENABLED`, `INPLAY_TELEGRAM_ENABLED`,
`TELEGRAM_PUBLIC_CHANNEL`.

**Dead mechanism to learn from: `bot_config_history`** (281, 08-24). It was created after
the operator *"placed EUR 1,270 of real money on these bots 2026-08-22..24 and lost"*, so
that *"every config is recoverable"*. It has 16 rows for 8 bots, written only by that one
migration, and **all 8 bots are now retired**. No code writes it. A versioned-config table
that is filled by hand in migrations does not survive, because config actually lives in code
and nothing forces the two to stay in step.

### 2.11 The Telegram and side tables

- `profiles.telegram_chat_id` (141, 05-28, USER-TELE-NOTIFY): per-user alerts for Pro/Elite.
  **0 users** have one today, and there is no paid product (CLAUDE.md 2026-09-21). This is a
  subscription mechanism, not a bot control.
- `bet_telegram_alerts` (154) and `manual_placement_queue` (153), both 05-29: tap-to-place
  from Telegram. Last alert 09-11. The queue is empty and has never been drained for real
  (`MANUAL_PLACE_EXECUTE = False`).
- `simulated_bets.user_placed_at / user_skipped_at / signal_message_id` (248, 06-12): the
  operator's "I placed it" marker. **`pick_generator` excludes picks the operator has
  marked** (`pick_generator.py:410`). A human action therefore changes what a bot sources.
- `user_pick_marks` (278, 08-22): per-user ticks on /picks. 715 rows, last 09-13.
- `coolbet_daemon_commands` (254, 06-17): Telegram → Mac heal queue.
- `coolbet_placement_attempts` (288, 08-27): *"one row per ATTEMPT, always, whatever the
  outcome… a placer that quietly places nothing looks identical to one with no picks to
  place."* Reconciled against `real_bets` by `REAL-BETS-ATTEMPTS-RECONCILED`.
- `user_picks` (016, 04-29): the customer's own tracker. 6 rows, last 05-28. Its web surface
  was deleted by PRODUCT-COLLAPSE (06-24), but `settlement.py` and `weekly_digest.py` still
  read it. **Not a bot table.** Leave it out of the unification (drop separately).
- `inplay_bot_stats` (095, 05-11): tried/fired counts per in-play strategy, replacing
  rotating Railway logs.

### 2.12 The pre-registered forward test and the bot ledgers

342 (b72afef7, 09-14) explains why it did not reuse `simulated_bets`:
> *"`simulated_bets` is bot-scoped and carries staking/Kelly semantics. These picks are
> published to readers, not staked by a bot. Attaching them to a synthetic bot row would put
> them in every bot-cohort query ever written and silently re-contaminate the track record we
> just finished cleaning."*

345 (f0a4fac9) registered the rule as a bot *"as a HANDLE, not a writer"*, with a read-only
projection shaped like `shadow_bets`. A bot row exists only because the registry drift test
demands every published strategy be in `bots`. `alignment_gap_minutes` is recorded at publish
time because *"Recomputing it later… cannot work: snapshots are pruned."*

---

## 3. What the DB actually holds (SELECT-only, VPS, 2026-09-24)

- **`bots`**: 108 rows. 20 not retired, and all 20 are `is_active`. No rows are active-but-
  retired or inactive-but-not-retired, so the 257 trigger holds. Labels: retired 88,
  experimental 13, testing 4, beta 2, calibrated 1 (`bot_v10_1x2`).
- **Registry vs DB**: `bot_registry.BOTS` equals the 20 non-retired rows exactly, in both
  directions.
- **`show_on_picks = true`**: 5 rows. Active: `bot_v10_1x2`, `bot_sharp_1x2_v1`,
  `bot_sharp_ou_v1`. Retired: `bot_v10_ou`, `bot_sharp_forward_test_v1`.
- **Code configs whose DB row is retired but still active in code:**
  - 23 `BOTS_CONFIG` entries (the SHADOW-RETIRED-OK set, e.g. `bot_high_alignment` wrote 855
    shadow rows in 7 days).
  - `TRIGGER_CONFIGS` `bot_trigger_1x2_model_v1`, `bot_trigger_ou_model_v1`. These are
    skipped by `pick_generator`'s retired-aware `_bot_id`.
  - Every code-config name has a row. No row lacks a config, except the forward-test handles,
    which have no writer by design.
- **Retired bots writing in the last 7 days**: 21 bots. The three standalone paper bots
  retired 09-14 still write through bare `_bot_id` lookups: team_total 500, corners 490,
  1h_1x2 200.
- **`coolbet_placer_bots`**: 2 rows, both `ui_place_enabled=false`.
  `bot_coolbet_ou_model_v1` (OFF 09-13, OU-CALIBRATOR-DOMAIN-MISMATCH);
  `bot_coolbet_1x2_model_v1` (OFF 09-14, OWN-PATH-VERDICT). The effective allowlist is ∅.
- **`coolbet_session_state`**: `placement_paused=true` (reason: OWN-PATH-VERDICT strategic
  closure). The row's `placement_paused_at` still shows 09-09 08:01 even though the reason
  was rewritten 09-14 by 343, so the timestamp tracks the original pause, not the current
  reason. `real_money_armed=false` ("disarmed by migration 354"). `publishing_paused=false`.
  `daemons_paused=false`.
- **`real_bets`**: 143 `placed_real=TRUE` (last 09-13) and 849 `NULL` (last 09-15). 0 FALSE.
- **`bot_config_history`**: 16 rows, 8 bots, last 08-24, all retired.
- **`profiles.telegram_chat_id`**: 0 set. `manual_placement_queue`: 0 rows.

---

## 4. Invariants that must survive the unification

Each is stated as an invariant, with its source.

**Real money**
- **I1.** The set of bots that can *ever* stake is a code constant that only changes through
  code review. Runtime state can only narrow it. (18fe686b; mig 310; `placement_gate.py:72–77`;
  `bot_registry.py` header)
- **I2.** The per-bot real-money toggle is reduce-only. The UI updates existing rows and never
  inserts. Any read error reads as the empty set. (mig 310; web route; `ui_place_enabled_bots`)
- **I3.** Two fleet switches with *opposite safe defaults* are both required. KILL
  (`placement_paused`, default not paused, fails CLOSED) and ARM (`real_money_armed`, default
  FALSE, fails CLOSED). They are separate because "nothing to stop" and "nothing may start"
  are different safe states. (mig 354)
- **I4.** Arming is an owner action with a written reason. It is never done by a migration, a
  deploy side effect, or the same click that enables a bot. (mig 354; `set_real_money_armed`)
- **I5.** A strategic pause records its reason in words that stop a transport fix from
  clearing it. (mig 343)
- **I6.** Every executor calls ONE gate first. The gate raises rather than returning False,
  and fails closed on every read. A new money path inherits it or it does not ship.
  (`placement_gate.py`; RELIABILITY_LEDGER §4, §14; smoke `PLACEMENT-GATE-ALL-EXECUTORS`)
- **I7.** Per-pick gates (kickoff cutoff, daily caps, exposure / already-placed using
  `canon_bet`, account verify) stay after any bot-level switch. A bot switch never replaces
  them. (§4; 001accbf)
- **I8.** "May money move?" is never decided by something being absent (an env var, an empty
  list that happens to be empty, an unloaded agent that happens to be unloaded).
  (§14; mig 354)
- **I9.** Money rows are never deleted by migration. The tri-state `placed_real` holds, with
  NULL counted as exposure. One real row per shadow pick. Manual logging is atomic.
  (migs 325, 361, 407; §5)

**Publishing**
- **I10.** Publishing and placement are separate switches with separate failure defaults:
  publishing fails OPEN, placement fails CLOSED. No shared flag between an OWN decision and a
  PICKS decision. (mig 353; §9b; `placement_gate.py` docstring)
- **I11.** A bot reaches customers only if someone opted it in. Default is off. (mig 356)
- **I12.** /performance measures every public-eligible bot whatever its record. The publish
  switch must never filter the leaderboard. (mig 356 comment)
- **I13.** OWN bots never reach customer surfaces. Today this is guaranteed *structurally*:
  OWN bots write only `shadow_bets`, and /performance derives from `simulated_bets`. The
  check is the ledger, not a UI string. (smoke `OWN-BOTS-OFF-CUSTOMER-SURFACES`)
- **I14.** The channel and /picks must agree on what was published. One decision drives both.
  (mig 356's Ludogorets reason; §2.3 shows this does not hold today)
- **I15.** The label that promotes a pick publicly must be earned by evidence, and be
  CHECK-constrained. (migs 304, 352)
- **I16.** The junk-anchor control and grade D are never sent. They are part of the
  pre-registered design. (migs 344/381; `PUBLISHED_ARMS`)

**Ledgers and identity**
- **I17.** Forward-test rows are immutable and are never aggregated into a bot-cohort query as
  if they were staked. The published test is read against its control. (migs 342, 345)
- **I18.** `bots.name` is the identity. `display_name` is display only. Splits happen in
  views/ownership, never by rewriting ledger rows. (migs 375, 380, 402)
- **I19.** Bankroll rollups do not count non-staking bots as capital. (mig 345)
- **I20.** Retirement is self-enforcing at the *writer*, and verified at the target rather
  than at the switch. (RELIABILITY_LEDGER "A retirement that only changes the DB")
- **I21.** Retired ≠ stop collecting. Owner decisions 05-20 and 09-18 say retired bots keep
  writing, so a retired bot's later evidence exists. Any change needs a new owner decision.
  (b4467dee; e1074e64)
- **I22.** Every shadow pick carries what produced it (`model_version`, rule). This cannot be
  backfilled. (e1074e64)
- **I23.** A new writer to an existing table must satisfy that table's constraints (CHECKs,
  FKs), and its failure count must be visible. (§15; the in-play cohort CHECK and the
  `real_bets` FK both ate writes silently)

**Operations**
- **I24.** Switches the Mac must obey are DB flags it polls. The web cannot reach launchd.
  (mig 318)
- **I25.** A config file edit is not a deploy. What runs is what counts, and drift is checked
  mechanically. (§3; `LAUNCHD-DRIFT-SEMANTIC`)
- **I26.** Real-money control tables deny anon/authenticated. Writes go only through
  superadmin server routes. (mig 310; #072)

---

## 5. What a naive unification would break

1. **Collapsing the real-money stack into `bots.can_place_real`** (design phase 5 lists
   `PLACEABLE_BOTS` under "replaces"). This removes I1: a DB write alone could make any bot
   stake. It also loses I3/I4 if the fleet switches are folded in. Keep the code whitelist,
   and keep the DB flag as a reduce-only intersection.
2. **One "publish" toggle that writes `show_on_picks` only.** The Telegram model arm reads
   `maturity_label='calibrated'`, and the forward test reads `PUBLISHED_ARMS`/grade. A
   page-level "publish OFF" would leave Telegram posting (the Ludogorets shape, I14).
   Conversely, setting TRUE for forward-test bots is either inert or (per 372) a double
   publish. The publishers must *read* the one flag before it is shown as a control.
3. **`collect = is_active AND retired_at IS NULL`** (design doc). This silently reverses I21
   for 21 bots, or, if views are just built that way, hides ~59k rows a month that are still
   being written, which is exactly the 09-14 blind spot (I20). Make collect its own explicit
   state.
4. **Turning `maturity_label` into a UI badge.** It is also the public-Telegram promotion
   gate, the /performance allowlist, the headline exclusion, the Mac placer filter
   (`COOLBET_RECORD_ALLOWED_MATURITY`) and the **source cohort for `pick_generator`**.
   Re-labelling a bot during a clean-up would change what other bots generate and what gets
   posted.
5. **A single `picks` table (phase 5)**:
   - It drops I13's structural guard (OWN = `shadow_bets`, customer = `simulated_bets`) unless
     it is replaced by a DB-enforced one. For example, a CHECK/trigger that a pick of an OWN-
     family bot can never be `publishable`, plus a view that is the only source for
     /performance.
   - It re-opens exactly the contamination 342/345 designed against (forward-test rows inside
     "every bot-cohort query ever written"). Every existing aggregate (settlement's
     `dashboard_cache`, weekly review, sweeps, `shadow_bot_scoreboard`) must filter on source
     before the first forward-test row is copied in.
   - `real_bets` has two pick FKs (`simulated_bet_id`, `shadow_bet_id`) and a unique index on
     one of them. The 09-13 FK incident (a pick id put in the wrong FK column; the ledger
     insert failed silently) is the template for what goes wrong in the cut-over.
   - Timing-cohort copies become a column (design). Every current reader of
     `shadow_bets_unique` relies on the view excluding them (#137 B4).
6. **Treating the exported `bot_config` table as a control.** If anything that places or
   publishes reads it, a stale export becomes RELIABILITY §3 ("edited but not deployed") in
   reverse. Until the writers read the DB, it has to be a **mirror with a drift test**, not a
   source. `bot_config_history` (§2.10) shows what happens to a config table kept alongside
   code with no enforcement.
7. **Moving the fleet switches out of `coolbet_session_state` into a new table** without
   carrying their state and fail-mode. Placement readers fail CLOSED (safe: a missing column
   pauses), but `publishing_paused` fails OPEN. A move that loses the current value unpauses
   a paused channel silently, and a half-moved reader reads the wrong row.
8. **A web toggle with no reason field.** It reproduces the 343 note-vs-flag disagreement.
   Every capability flip needs who/when/why in an append-only log. That is the audited
   version the design asks for, and the thing `bot_config_history` tried to be.
9. **Deleting `ROUTER_ALLOW_REAL` in the name of tidiness while it is `true`.** This is
   harmless today only because the DB gate holds. Worse is presenting it as a layer on the
   new page, because it adds no protection while it is `true` (and RELIABILITY_LEDGER still
   says "deliberately unset").
10. **Auto-creating bot rows from code** (`ensure_bots`) inside a new registry flow. This
    bypasses the opt-in default and the migration review that every bot since September got.
    A new bot should arrive as `collect` only, with no publish, no real money and the
    `experimental` label.
11. **Deleting `real_bets` duplicates or legacy NULL rows** as part of the clean-up. Mig 325
    and mig 407 both explicitly refused to do this. It is the owner's call.
12. **Using the same click for "enable real money for this bot" and "arm the fleet".** This
    breaks I4 and removes the independence of the layers.

---

## 6. Recommendation: which should be ONE switch, and which layers must stay separate

### ONE switch per bot (in `bots`, set on the bots page, audited with who/when/why)

| Capability | Replaces | Condition before it becomes authoritative |
|---|---|---|
| **collect** (`collecting` / `collecting_archived` / `stopped`) | `is_active` + writer-by-writer retirement behaviour + SHADOW-RETIRED-OK | Every writer (pipeline shadow pass, the 4 bare-`_bot_id` modules, `ensure_bots`) reads it. `retired_at` stays as the "listed as a live strategy" and history field. Default for retired bots = `collecting_archived` until the owner decides otherwise (I21) |
| **publish** | `show_on_picks` + the Telegram `calibrated` gate + web public-label sets | `picks_public_all`, `coolbet_signaler` and /performance's public set all read it. For forward-test bots the flag *mirrors* `PUBLISHED_ARMS`/grade and is read-only on the page, because the rule is pre-registered (I16, I17). Changing it means a new `rule_version`, not a click |
| **real-money eligible (per bot)** | `coolbet_placer_bots.ui_place_enabled` (+ drop `COOLBET_RECORD_ALLOWED_MATURITY`) | Keep reduce-only semantics: effective = code whitelist ∩ this flag, fail closed to ∅, update-only from the UI (I1, I2) |
| **public badge** | `maturity_label` as a *display* claim | Split its hidden jobs out first. The pick-generator source cohort becomes an explicit `source_bots` entry in the bot's config. The headline exclusion keys on family/publish, not the label |

### Separate layers that stay separate on purpose

1. **`PLACEABLE_BOTS`** (code). The only way to widen who may ever stake. The registry drift
   test keeps it equal to `real_money=True` specs.
2. **`real_money_armed`** (fleet ARM, default false). Owner-only, reason required, not
   writable from the bots page's per-bot controls. At most a separate, confirmed action.
3. **`placement_paused`** (fleet KILL, fails closed, strategic reason text).
4. **`publishing_paused`** (fleet PICKS kill, fails open). It must never be merged with (3).
5. **`daemons_paused`** (transport footprint, not money).
6. **Per-pick gates** in `placement_gate` + `place_coolbet_ui` (cutoff, caps, exposure,
   account verify).
7. **Physical layer**: launchd `--execute` agents loaded or unloaded, shown by
   `coolbet_control --status → CAN_STAKE`. The page should *display* it, not control it.
8. **Ledger boundary** between OWN and customer records, as a DB constraint if the tables are
   merged (I13, I17).
9. **Forward-test immutability** (trigger on decision fields; `rule_version`).

### Clean-up to do alongside (each is small and has a source)

- Retire `ROUTER_ALLOW_REAL` deliberately. Either remove it along with the stale
  RELIABILITY_LEDGER line, or invert it into an explicit refusal (`ROUTER_REFUSE_REAL`). Its
  value on the Mac today is `true`, so it protects nothing.
- Add `retired_at IS NULL` (or the new `collect` state) to the four bare `_bot_id` lookups
  once the owner decides B4.
- Delete or repurpose `bot_config_history`. Do not build `bot_config_versions` next to it
  without a writer that runs on every deploy.
- Correct the stale comments: `bot-aggregates.ts:340` ("nothing reads yet"), the mig-356
  column comment (claims Telegram), 372's "would publish it twice".
- Collapse the three web public-label sets into one derived from the publish flag.
- `user_picks`, `profiles.telegram_chat_id`, `manual_placement_queue`,
  `bet_telegram_alerts`: dormant customer/Telegram mechanisms. List them for deletion
  separately and do not fold them into the bot model.

### Design-doc conflicts to raise with the owner before phase 3

- (a) `collect` definition vs I21.
- (b) `publish` sourced only from `show_on_picks` vs the Telegram `calibrated` gate.
- (c) phase 5 "`bots` replaces `PLACEABLE_BOTS`" vs I1.
- (d) phase 5 single `picks` table vs the 342/345 isolation and the
  `OWN-BOTS-OFF-CUSTOMER-SURFACES` structural guard. It needs a named replacement guard in the
  migration plan.
- (e) `bot_config` export is a mirror, not a control, until the writers read it.
