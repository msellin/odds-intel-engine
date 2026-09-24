Parent row: **#139 UNIFIED-BOT-MODEL-EPIC** in PRIORITY_QUEUE.md.

# /admin/bots — control panel spec (phase 3 "switches", 2026-09-24)

Owner: *"ideally I would like to control all the stuff via the bots page, there should be some panel
to turn things on and off … [the page should look] more like a real admin dashboard like the pages we
see in the web."*

**Builder:** implement §2–§5 in the order of §6. **Reviewer:** check every rule in §1.3 and §2.9 and the
smoke tests in §6. This spec extends `dev/active/bots-board-ux-spec.md` (the read-only board). It does
not replace it. Row visuals, verdicts, forest bars and formatting stay as that spec defines them.

**Inputs this is built on.** Read these before changing anything:
- `docs/UNIFIED_BOT_MODEL_DESIGN_2026_09_24.md` (capabilities collect → publish → real money).
- `dev/active/unified-bot-model-genesis/bots-and-controls.md`: invariants **I1–I26** (§4) and the
  "ONE switch vs separate layer" recommendation (§6). This spec cites them as *I-n*.
- `docs/SHADOW_BOTS_AND_CONTROLS_AUDIT_2026_09_24.md` §B, the control map.
- `WORKFLOWS.md` § Pause semantics, and `docs/COOLBET_OWN_BETTING.md` (placement gate).

**The rule behind every decision below.** The panel gives the operator a *handle* on each layer. It
never *merges* layers. Nine separate safety layers exist, and each one exists because of an incident
(bots-and-controls §6, "Separate layers that stay separate on purpose"). The page shows all nine side
by side so the operator can see the whole chain. It lets the operator move the ones meant to be moved
at runtime, and each of those stays a separate control with its own failure default.

---

## 1. Control inventory

Verdict key:
- **(a)** plain toggle on the page. One click plus a confirmation popover, audited, reason optional.
- **(b)** toggle that needs a typed confirmation and a written reason (≥ 10 characters), audited.
- **(c)** read-only on the page, with an explanation of why and of how it *is* changed.
- **(d)** never on the page.

Where a control's two directions are not equally safe, the safe direction (pause, disarm, disable) gets
the lighter verdict. **Making it easy to stop is the point; making it easy to start is the risk.**

### 1.1 Fleet level

| # | Control | Stored today | Read by | Blast radius | Fails | Verdict |
|---|---|---|---|---|---|---|
| F1 | **Placement pause** (KILL) | `coolbet_session_state.placement_paused` (+`_at`, `_reason`) | `placement_gate.assert_run_may_place` / `assert_may_place` (every executor, run-level AND per pick), `coolbet_control.can_stake`, daily summary, health alerts | all real-money placement | CLOSED (paused) | **Pause: (a)**, reason optional (default text "paused from /admin/bots"). **Resume: (b)**, and the dialog shows the current reason in full. A reason containing "strategic" / "OWN-PATH-VERDICT" needs the extra typed phrase `RESUME STRATEGIC` (I5, mig 343: a strategic stop must not be cleared the way a transport fix would be). Resuming does not stake anything by itself, because arming is separate (I3). |
| F2 | **Real money armed** (ARM) | `coolbet_session_state.real_money_armed` (+`_at`, `_reason`) | `placement_gate` (after pause), `coolbet_control.can_stake` | every stake | CLOSED (not armed) | **Disarm: (a)**, one click, always available, even when the state read failed. **Arm: owner-only, two-step, out-of-band code** (§2.5). Not reachable from any per-bot control (I4, §5.12). It never shares a click with anything else. |
| F3 | **Publishing pause** (customer channel) | `coolbet_session_state.publishing_paused` (+`_at`, `_reason`) | `job_publish_picks_forward_test` send step (:05/:35), `coolbet_signaler` (model arm, via betting refresh) | every Telegram send to `@oddsintelpicks`. It does **not** stop recording (owner decision 2026-09-24): /picks and the ledger keep filling | OPEN (not paused) | **Pause: (b)**. Typed phrase `PAUSE PICKS` plus a reason, because a silent customer outage is the incident class this flag was born from (§9b, twice). **Resume: (a).** It is never shown in the same card row as F1 (I10). |
| F4 | **Coolbet footprint pause** | `coolbet_session_state.daemons_paused` | `coolbet_explorer`, `coolbet_feed_watchdog`, `inplay_coolbet_collector`, mac daemon, `coolbet_control` | Coolbet HTTP collection only, not money | CLOSED (paused) | **(a)** both directions, reason optional. It reuses the existing route `/api/admin/coolbet-daemons-pause`, moved behind the audit function (§2.2). Label: "Coolbet collection footprint — not a money switch". |
| F5 | **Feeds** (per-feed pause / run now) | `feed_controls` + audit `feed_actions` (mig 389) | `_run_job` (30 s cache), feed drain | one feed | skip (paused) | **(c) on the bots page, plus the existing (a) control embedded.** A compact "Feeds that bots depend on" list (Coolbet, Unibet-Site, Epicbet, AF odds) shows status dots and embeds the **existing** `FeedControls` component, which posts to the **existing** `/api/admin/feed-control`. There is no second write path. The full console stays `/admin/feeds`. |
| F6 | `PLACEABLE_BOTS` (code whitelist) | `workers/automation/placement_gate.py:77` | gate `effective_allowlist()` | who may *ever* stake | n/a | **(c)**. Shown as the list, with the text "Code whitelist. Widening it needs a code review and a deploy, by design (I1). This page can only narrow it." Source is `bot_config.placeable` (the export mirror, §5.6 of genesis). The page labels it "as of export HH:MM". |
| F7 | Daily caps (80 bets / €800), kickoff cutoff 3 min, exposure, account verify | code / Mac env `COOLBET_MAX_*` | `assert_may_place` | per pick | CLOSED | **(c)**, listed in the Real-money card as "per-pick gates, always on (I7)". Values come from `placement_gate.gate_status()` only if exported (§2.8). Otherwise "set on the Mac, not visible here". |
| F8 | Executors loaded (launchd `--execute` agents on the Mac) | host (`~/Library/LaunchAgents[/paused]`) | the OS | whether any placer runs at all | n/a | **(c)**, display only (I24: the web cannot reach launchd). Shows "Parked / Loaded / Not reported" from a Mac heartbeat column (§6 phase C). Until that exists: "Not reported — run `coolbet_control --status` on the Mac". It is never shown as "off" when unknown. |
| F9 | `ROUTER_ALLOW_REAL` (Mac env) | Mac `.env` | router | — | — | **(d)**. It adds no protection while `true`, and the owner is setting it `false` (#139 decision iii). Presenting it as a layer would be the §5.9 mistake. |
| F10 | `DISABLE_PAPER_BETTING`, `DISABLE_INPLAY_STRATEGIES`, `SHADOW_MODEL_VERSION`, `OPERATOR_PICK_ALERTS`, `SHADOW_TELEGRAM_ENABLED` | VPS env | pipeline | wide | — | **(d)**. Env on a host the web cannot read. A config file edit is not a deploy (I25). |
| F11 | `real_bets` rows, manual "Place" logging | `record_manual_real_bet()` | settlement | money ledger | — | **(d) on this page.** It stays on `/admin/shadow-bots` ("today's picks to act on"). Money rows are never edited here (I9). |

### 1.2 Per bot

| # | Control | Stored today | Read by | Blast radius | Verdict |
|---|---|---|---|---|---|
| B1 | **Retire** | `bots.is_active=false` (trigger 257 sets `retired_at`, `maturity_label='retired'`) + `retired_reason`. Today this is **migration-only** (32 migrations) | see "what retiring does" below. Varies by writer | the bot's listing everywhere; generation for *some* writers | **(b)**: type the bot name plus a reason. The reason is written to `retired_reason` (shown publicly on /performance "Retired strategies"). The dialog shows the **"what retiring this bot does"** preview computed from `bot_config.writer_job` / `ledger` (table below). Phase B. |
| B2 | **Un-retire** | same columns | same | re-lists the bot; re-starts retired-aware writers | **(b)**: type the name, give a reason, **and choose a maturity label** (`experimental` default, `testing` allowed; `beta` / `calibrated` not offered here, see B5). This follows 257's rule that re-activation is "an explicit operator choice with deliberate label semantics". It clears `retired_at` and sets `is_active=true`. It never touches `show_on_picks` or `ui_place_enabled`: a bot comes back as **collect only** (§5.10, I11). Phase B. |
| B3 | **Stop collecting (a retired bot)** | nothing explicit. Writer-by-writer behaviour (SHADOW-RETIRED-OK) | — | ~59k rows / 30 d | **(c)**. A "Still collecting (7 d)" chip, with the text "Retired bots keep writing on purpose (owner 05-20, 09-18). A per-bot collect switch needs every writer to read it first. This is not built yet." It becomes (b) only after the owner decides #137 B4 and the writers read a `collect` state (I21, genesis §6). |
| B4a | **Publish → /picks** (model arm) | `bots.show_on_picks` (mig 356, default FALSE) | view `picks_public_all`, model branch only (`simulated_bets` joined `bots`), so /picks and `/api/v1/upcoming` | what customers are offered on /picks | **Sim-ledger bots only: ON = (b), OFF = (a).** Label: **"Show on /picks"**, not "Publish". Under the switch there is always a line: "Telegram: follows `maturity_label = calibrated`, not this switch" (with the current state). Shadow-ledger bots: switch **disabled**, text "Shadow-ledger bot. It cannot reach customers by design (I13)". Forward-test bots: B4c. |
| B4b | **Publish → Telegram** (model arm) | **`maturity_label='calibrated'`** of any bot in the (match, market, selection) group, plus the placer's per-market floor (`coolbet_signaler.py:141, 209–222`). `show_on_picks` is **not** read | `coolbet_signaler` | the public channel | **(c)** until phase C. It is shown next to B4a. When they disagree (a calibrated bot with `show_on_picks=false`, or the reverse), the row gets an amber **"/picks ≠ Telegram"** chip and a "Needs a look" entry: "the Ludogorets shape (mig 356)", I14. |
| B4c | **Publish** (forward-test bots `bot_sharp_*`, `bot_consensus_*`) | `PUBLISHED_ARMS` + grade rules in `scripts/publish_picks_forward_test.py`, versioned as `rule_version`. `show_on_picks` is inert for them (TRUE for sharp, FALSE for consensus) | view arm branch, scheduler send | pre-registered public test | **(c)**. The pill reads "Pre-registered — published by rule `consensus_edge_v2_2026_09_24`. Changing this is a new rule version, not a click (I16, I17)." `control_junk_anchor`: "Never published (the control)". Grade D: "Recorded, never sent". |
| B4d | **/performance leaderboard + hero + anchored ledger** | web allowlist `maturity ∈ {calibrated, beta}` (`bot-aggregates.ts:348`); hero/API `{calibrated, beta, active}` (`engine-data.ts:1493`); anchored `ledger/` = `calibrated` (`export_track_record_snapshot.py:137`) | web, GitHub job | the public track record | **(c)**. Shown as "On /performance: yes/no (via maturity label)". It is **never** driven by the /picks switch (I12: "any future code that filters the leaderboard on this column is a bug"). |
| B5 | **Maturity label** (badge) | `bots.maturity_label` (CHECK) | **six jobs** (genesis §2.2): /performance allowlist, headline exclusion, Telegram promotion, Mac placer `COOLBET_RECORD_ALLOWED_MATURITY`, **`pick_generator` source cohort** (`calibrated` feeds `bot_coolbet_1x2_model_v1`), "Pro" cohort | Relabelling `bot_v10_1x2` removes the only real-money candidate source | **(c) in phase A.** Phase B: **(b)** with a mandatory **impact preview** that lists every job the change touches for *this* bot (computed server-side: e.g. "`bot_coolbet_1x2_model_v1` loses its only probability source"). Promotion to `calibrated` is refused unless the family's admissible metric has n ≥ 30 settled (I15; mig 304 demoted a calibrated bot with 0 bets). |
| B6 | **Real-money eligible** | `coolbet_placer_bots.ui_place_enabled` (mig 310, reduce-only) | `placement_gate.ui_place_enabled_bots()` ∩ `PLACEABLE_BOTS` | stakes for this bot, **only if** F1 resumed AND F2 armed AND executors loaded | **Only rendered for bots in the whitelist that have a seeded row.** **OFF: (a).** **ON: (b)**: type the bot name plus a reason, and the dialog shows the full chain with live state (whitelist ✓, this switch, pause, armed, executors, caps) and says "this alone stakes nothing". Refused server-side if the bot is retired, has no seeded row (UPDATE only, I2), or carries a lock (below). Other bots show "Not real-money capable — code whitelist" (c). |
| B6-lock | **Pinned OFF by evidence** | new `coolbet_placer_bots.locked_reason` (§2.3). Today it is only a smoke test (`OU-CALIBRATOR-DOMAIN-MISMATCH — real-money O/U bot is off`) | the page, the DB function | — | **(c)**: a lock icon with the reason. `bot_coolbet_ou_model_v1` is locked with "OU-CALIBRATOR-DOMAIN-MISMATCH: re-enable only on positive post-fix CLV. Needs a migration and a smoke-test change." Without a lock, a click would turn CI red *after* money could move. |
| B7 | **Display name** | `bots.display_name` (display only, I18) | pages | cosmetic | **(a)**, inline edit, audited. Phase B. Never `bots.name` (identity, I18). |
| B8 | Bot config (floors, books, markets, gates, odds band, cadence) | code: `bot_configs.py`, `BOTS_CONFIG`, `TRIGGER_CONFIGS`, forward-test constants → mirrored to `bot_config` | writers | what the bot picks | **(c)**, the drawer's Settings tab "Configuration (from code)". Text: "Exported from the running code at HH:MM. Changing it is a code change + deploy. For pre-registered bots, a new rule version." `bot_config` is a mirror, not a control (genesis §5.6). |
| B9 | Create / delete a bot, rename `bots.name`, `bankroll` | migrations; `ensure_bots` | everything | identity | **(d)** (I18, I19, §5.10). |

**"What retiring this bot does"** (B1 preview; derived from `bot_config.writer_job`, verified against
audit §B1/§B2; the builder hard-codes this map in `src/lib/bot-controls/retire-effects.ts`, and
smoke `RETIRE-EFFECTS-MAP-COVERS-WRITERS` checks every `writer_job` in `bot_config` has an entry):

| Writer | Retire effect |
|---|---|
| `pick_generator` (`CONFIGS`, `TRIGGER_CONFIGS`), `pick_trigger_matcher`, `inplay_collector` | **Stops generating** at the next call (retired-aware `_bot_id`) |
| `daily_pipeline_v2` sim bots | Stops `simulated_bets` at the next pipeline run. **Keeps writing shadow timing cohorts** (SHADOW-RETIRED-OK, on purpose) |
| `corners_paper_bot`, `first_half_1x2_paper_bot`, `team_total_paper_bot`, `ou35_model_shadow` | **Keeps writing** (bare name lookup, #137 B4, owner's call) |
| Forward test (`publish_picks_forward_test`) | **No effect on recording or sending.** The arm never consults `bots`. Retire is **disabled** for forward-test bots and the control: "Stopping a pre-registered test follows its stopping rule, a code change". |
| Real-money placer `load_picks` | Excluded from placement at the next run (safe direction) |
| /picks model arm, /performance | Hidden at the next page render |

### 1.3 Rules the reviewer checks

1. **No single click can let a bot stake real money.** Money needs F1 resumed, F2 armed and B6 on, with
   the whitelist and executors outside the page's reach. F1-resume and B6-on are each (b). F2-arm is
   two-step with an out-of-band code. No dialog performs more than one of these.
2. The safe direction is always one click and stays enabled when state is unknown (§2.7).
3. There is no "Publish" master switch until phase C makes the signaler, the view and /performance
   read one flag. Until then the switch says exactly what it moves ("Show on /picks").
4. Nothing on the page edits a pre-registered rule, the junk control, grade D, or forward-test rows.
5. Every write goes through one DB function that writes the audit row in the same transaction (§2.2).
   There are no direct `.update()` calls on control tables from the web.

---

## 2. Server design

### 2.1 Transport: API route handlers (not server actions)

The web app has **no** `"use server"` actions today. Every admin write is a route handler
(`/api/admin/coolbet-placer-bots`, `/feed-control`, `/coolbet-daemons-pause`). Keep that pattern:

- `src/lib/admin-auth.ts` (new). `requireSuperadmin()` and `requireOwner()` are extracted from the three
  copies. `requireOwner()` = superadmin **AND** `user.id ∈ OWNER_USER_IDS` (server env,
  comma-separated). This is the "owner-only" check for arming. It is not a UI hide.
- `POST /api/admin/controls`. One route, body is a discriminated union:
  `{ control, bot_name?, value, reason?, confirm_text?, expected }`. `control` ∈
  `placement_paused | publishing_paused | daemons_paused | real_money_disarm | placer_enabled |
  show_on_picks | retire | unretire | maturity_label | display_name`. `expected` is the value the
  client *believed* was current (optimistic concurrency, §2.6).
- `POST /api/admin/controls/arm/request` and `POST /api/admin/controls/arm/confirm` (§2.5).
- `GET /api/admin/controls/audit?bot_name=&limit=` for the Activity tab and log.
- The existing `/api/admin/coolbet-placer-bots` and `/api/admin/coolbet-daemons-pause` POSTs are
  re-pointed to the same DB function (their callers on `/admin/shadow-bots` keep working), then
  removed in phase B once shadow-bots stops rendering controls.
- Every write re-checks superadmin **server-side** inside the route (the page gate is not trusted), and
  the Postgres function is `EXECUTE`-granted to `service_role` only. anon/authenticated are denied (I26).

### 2.2 Migration 412: `control_changes` (append-only) + one write function

```sql
CREATE TABLE control_changes (
  id            bigserial PRIMARY KEY,
  created_at    timestamptz NOT NULL DEFAULT now(),
  actor         text NOT NULL,          -- email or 'telegram:<chat_id>' or 'engine:<module>' or 'migration:NNN'
  actor_user_id uuid,                   -- auth.users id when from the web
  source        text NOT NULL CHECK (source IN ('web','telegram','engine','cli','migration')),
  control       text NOT NULL CHECK (control IN (
                  'placement_paused','publishing_paused','daemons_paused','real_money_armed',
                  'placer_enabled','show_on_picks','retire','unretire','maturity_label','display_name')),
  bot_name      text,                   -- NULL for fleet controls; no FK (names outlive rows, I18)
  old_value     jsonb,
  new_value     jsonb,
  reason        text,
  outcome       text NOT NULL CHECK (outcome IN ('applied','noop','refused','conflict')),
  refusal       text,
  request_id    uuid
);
CREATE INDEX ON control_changes (bot_name, created_at DESC);
CREATE INDEX ON control_changes (control, created_at DESC);
-- append-only: a trigger raising on UPDATE/DELETE/TRUNCATE; REVOKE UPDATE, DELETE FROM everyone.
-- REVOKE ALL FROM anon, authenticated; GRANT SELECT, INSERT TO service_role (INSERT only via the fn).
```

`admin_set_control(p_control text, p_bot text, p_value jsonb, p_reason text, p_actor text,
p_actor_user_id uuid, p_source text, p_expected jsonb, p_request_id uuid) RETURNS jsonb`,
`SECURITY DEFINER`, owner `oddsintel_owner`:
1. `SELECT … FOR UPDATE` the target row (`coolbet_session_state WHERE id=1`, `bots WHERE name=`,
   `coolbet_placer_bots WHERE bot_name=`).
2. If the current value ≠ `p_expected`, write outcome `conflict` and return the current value.
3. Validate. Refusals are written as outcome `refused` with the reason, then returned:
   - `placer_enabled` ON: the row must exist (UPDATE only, I2), `locked_reason IS NULL`, the bot is not
     retired, the bot is in `bot_config.placeable`, and a reason is given. OFF is always allowed.
   - `real_money_armed`: this function may only set **false**. Arming uses `admin_arm_real_money`
     (§2.5).
   - `show_on_picks` ON: `bot_config.ledger = 'simulated_bets'` and the bot is not retired and not in
     family `forward_test` / `control`. A reason is required.
   - `retire` / `unretire`: family not `forward_test` / `control`. Un-retire needs a label ∈
     {experimental, testing}.
   - `maturity_label` (phase B): CHECK-legal. `calibrated` needs n ≥ 30 on the admissible metric
     (read from `bot_scoreboard`).
   - A reason is required for every (b) control. The function enforces it, not only the UI.
4. Apply the change, insert the audit row, return `{outcome, old, new, at}`. **One transaction**, so a
   change without an audit row cannot exist.

Also in 412:
- `ALTER TABLE coolbet_placer_bots ADD COLUMN locked_reason text`. Seed `bot_coolbet_ou_model_v1` with
  the OU-CALIBRATOR text (`ON CONFLICT`-safe UPDATE; it never enables anything).
- Backfill one `source='migration'` audit row per current fleet switch and per placer row, recording
  today's state and reason ("state at audit start"). The log then starts from a known state.

**Other writers join the log.** In the same phase, make these call the function (or insert an audit
row in the same statement):
- Telegram `/pause` `/resume` `/pausepicks` `/resumepicks` and the `coolbet-pause:` / `coolbet-resume:`
  buttons in `src/app/api/telegram/webhook/route.ts`. Use `source='telegram'` and
  `actor='telegram:<chat>'`.
- The engine setters `coolbet_state.set_placement_paused / set_publishing_paused /
  set_daemons_paused / set_real_money_armed`, including the daemon self-pause. Use `source='engine'`.
- Future control migrations insert a `source='migration'` row. This is a convention, checked by
  review, and noted in CLAUDE.md § Database Migrations.

Without this the Activity tab would show page clicks only and would lie by omission: the 343 note
disagreed with the flag because the log was not complete.

### 2.3 Where each control's state is read (the page's data)

Extend `loadBotBoard()` with one service-client read `loadControlState()`:
- the `coolbet_session_state` fleet columns (not the JWT or cookies: select named columns only)
- `coolbet_placer_bots(bot_name, ui_place_enabled, locked_reason, note, updated_at)`
- `bots(name, show_on_picks, maturity_label, retired_at, retired_reason, display_name)`
- the last 50 `control_changes`

These are service-role reads on the server only.

### 2.4 When a change takes effect (shown in the success toast AND under each control)

| Control | Takes effect | Mid-run behaviour |
|---|---|---|
| F1 placement pause | **Next gate check.** `placement_gate` runs at run start AND per pick, before `select_outcome` | A pick already past the gate (Coolbet POST in flight) completes. It is visible in `coolbet_placement_attempts`. The next pick is refused |
| F2 disarm | same as F1 | same |
| F2 arm | next gate check, but nothing runs while executors are parked (F8) | — |
| F3 publishing pause | forward-test job: next :05/:35 run's send step. Model signaler: next betting refresh (:05/:35) | A run already sending finishes its loop. Picks claimed while paused are **never** sent after resume (no burst) |
| F4 footprint pause | next daemon / collector tick (`is_daemons_paused` at start of run) | the current sweep finishes |
| F5 feed pause | ≤ 30 s (`feed_control` cache) | the running job finishes; the next is skipped |
| B6 placer enabled | next `effective_allowlist()` read: run start and per pick | as F1 |
| B4a show on /picks | **Immediately** on the next /picks render (live view, no cache) | picks already shown disappear or appear. Telegram is unaffected (B4b) |
| B1 retire | per the writer table (§1.2): generator next call (~30 min, after each sweep); pipeline next run; placer next run; pages next render | the in-flight run finishes with the bot included |
| B5 maturity | signaler next refresh; `dashboard_cache` next rebuild (:15/:45); `pick_generator` source cohort next call; Mac placer next run | — |

The success toast says "Applied. Takes effect at the next placement check" (or the right line from
the table), never just "Saved".

### 2.5 Arming real money: owner-only, two-step, out-of-band

Arming is the one "start money" switch, and the spec treats it that way:
1. The **Arm…** button appears only for `requireOwner()` users (server-rendered). For everyone else it
   reads "Owner only".
2. **Step 1, dialog.** The dialog shows a live checklist: placement paused? (armed-while-paused is
   legal but flagged), effective allowlist (bots with B6 on), executors (F8), caps (F7), last
   `coolbet_placement_attempts`. The owner types `ARM REAL MONEY` and a reason (≥ 20 characters) →
   `POST /arm/request`. The server inserts a row in `control_challenges` (new in 412: `id, actor_user_id,
   reason, code_hash, expires_at now()+5 min, consumed_at, attempts`) and sends a **6-digit code to the
   operator Telegram chat** (`TELEGRAM_CHAT_ID`, same bot token as the webhook). The code is hashed at
   rest and never returned in the HTTP response.
3. **Step 2.** The owner enters the code → `POST /arm/confirm` →
   `admin_arm_real_money(challenge_id, code, actor)`. That function checks the hash, expiry, the attempt
   count (max 3, then the challenge dies) and single use, then sets `real_money_armed=true` with the
   reason and writes the audit row, all in one transaction.
4. Telegram gets "🔴 REAL MONEY ARMED by <owner> — <reason>". While armed, every admin page shows the
   red top bar (§3.1).

This keeps I4 (an owner action with a written reason, never a side effect) and adds a second factor
the browser session alone cannot supply. **Disarm stays one click** for any superadmin, and from
Telegram (a `/disarm` command is added in phase A, audited).

### 2.6 Optimistic UI with rollback, and only for the safe direction

- **(a) controls** (pause, disarm, OFF, resume publishing, display name): the switch flips at once into
  a `pending` look (thumb spinner, 60% opacity, `aria-busy`). On `applied`, `router.refresh()` re-reads
  the truth. On `conflict`, it rolls back to the server's value and shows the toast "Changed by
  <actor> at HH:MM — refreshed". On an error, it rolls back and shows a red toast with the message.
- **(b) controls and arming: no optimistic state.** The dialog's confirm button shows a spinner. The
  switch changes only after `applied` plus a refresh. A dangerous control must never display a state it
  does not have.
- A double submit is harmless: `expected` makes the second one a `noop` / `conflict`, and the confirm
  button is disabled while a request is in flight.

### 2.7 Fail-closed rules (page and server)

| Situation | Page | Server |
|---|---|---|
| fleet state unreadable | F1/F2/F3 tiles read **"Unknown"** (never "Off" / "Running"). All *start* directions (resume, arm, placer ON, show ON) are disabled with the tooltip "state unreadable". **Pause, disarm and OFF stay enabled** | the function reads under `FOR UPDATE`. If the row is missing, start directions are refused and stop directions still apply |
| `coolbet_placer_bots` unreadable | B6 shows "?" and is disabled except OFF | ON is refused |
| `bot_config` stale (exported_at > 36 h) | amber "config export stale" banner. B6 ON and B4a ON are disabled (they depend on `placeable` / `ledger`) | same check in the function |
| audit insert fails | — | the whole transaction fails, so no change is applied without its audit row |
| Telegram notify fails | toast "Applied, but the Telegram notice failed" | the change stands. The notify outcome is logged to the server console. Notification is a courtesy, not a gate. **Exception: arming.** If the code cannot be sent, step 1 fails and nothing is armed |
| session expired mid-dialog | the dialog shows "signed out", and nothing is sent | 401 |

### 2.8 Telegram notification of every change

After an `applied` outcome, the route sends one line to `TELEGRAM_CHAT_ID` through a shared
`src/lib/telegram-operator.ts` (extracted from the webhook's `sendReply`):
`🔧 /admin/bots · <control> <bot?> · <old> → <new> · <actor> · "<reason>"`. Money-related changes
(F1, F2, B6) are prefixed 🔴 when they move toward money and 🟢 when they move away. Changes that came
*from* Telegram are not echoed back. `refused` and `conflict` outcomes are not sent (they are in the
log).

### 2.9 Security checklist (reviewer)

- There is a superadmin check in every route handler, and `requireOwner()` on both arm routes.
- There is no browser-side write to PostgREST. Service key is server-only.
- `admin_set_control` / `admin_arm_real_money` are `SECURITY DEFINER` with `SET search_path = public`
  and EXECUTE for `service_role` only.
- `control_changes`: anon/authenticated denied, UPDATE/DELETE trigger raises. It gets added to the smoke
  `ANON-LEAST-PRIVILEGE` negative list.
- Reasons are stored as text, rendered escaped, and capped at 500 characters.
- There is no per-bot route that can reach `real_money_armed = true`.

---

## 3. The "real admin dashboard" look

### 3.1 Admin shell (shared by every `/admin/**` page)

New `src/app/(app)/admin/layout.tsx`. Today each admin page renders its own `← Admin` link inside
`max-w-7xl`, and there is no shared nav.

```
┌────────────┬──────────────────────────────────────────────────────────────────────────────┐
│ ▣ OddsIntel│  [red bar only when ARMED: "REAL MONEY ARMED since 14:02 by owner — Disarm"] │
│   Admin    ├──────────────────────────────────────────────────────────────────────────────┤
│            │  Bots                                        [Activity] [How to read] [⋯]    │
│  Overview  │  20 active · 1 control · data 15:27 UTC                                       │
│ ▸ Bots     │                                                                              │
│  Feeds     │  (page content)                                                              │
│  Shadow    │                                                                              │
│  Ops       │                                                                              │
│  Real bets │                                                                              │
│ ───────────│                                                                              │
│ STATUS     │                                                                              │
│ ● Placement│                                                                              │
│   paused   │                                                                              │
│ ● Money off│                                                                              │
│ ● Picks on │                                                                              │
└────────────┴──────────────────────────────────────────────────────────────────────────────┘
```

- **Sidebar**, `lg:` and up: 232 px, `border-r bg-card/40`, sticky full height. Items use a lucide icon
  plus a label (`LayoutDashboard`, `Bot`, `Rss`, `Ghost`, `Activity`, `Banknote`). The active item gets
  `bg-accent text-foreground` with a 2 px left accent. At the bottom is a **status block**: three dots
  with words (Placement / Real money / Picks channel). Every admin page therefore shows the money and
  channel state (Vercel's project sidebar and Stripe's persistent test-mode indicator do the same).
- **Below `lg`:** the sidebar collapses to a horizontal, scrollable tab bar under the site nav, and
  the status block becomes three small dots at its right end.
- **Armed banner** (Stripe's test-mode bar, inverted): a full-width `bg-red-600 text-white` 36 px bar
  across all admin pages whenever `real_money_armed = true`, with an inline **Disarm** button.
  `prefers-reduced-motion` is respected, with no pulse.
- `/admin` (the Overview) keeps its cards. The CS2/LoL/tennis/place routes are not in the sidebar
  (CLAUDE.md: dropped from the index).

### 3.2 Page header

Title `text-2xl font-semibold` and a meta line (`20 active · 1 control · data 15:27 UTC`). Right-aligned
actions: **Activity** (opens the full audit log sheet), **How to read this** (existing), and an overflow
`⋯` menu (Copy page state as JSON; Open /admin/feeds; Open /admin/shadow-bots).

### 3.3 KPI cards

These are the existing fleet strip (bots-board-ux-spec §3), with six tiles. **Placement** and **Real
money** tiles become clickable. They scroll to and highlight the Controls card, without toggling
anything.

### 3.4 Fleet Controls card (between the KPI strip and the table)

It follows Vercel's settings pattern: each control is a row with a **title + one-line description +
"what reads this / takes effect"** on the left and the control on the right. The real-money section is
a separate **danger-zone card** (red border, as in Vercel's "Delete project" and GitHub's Danger Zone).

```
┌ Controls ──────────────────────────────────────────────────────────────────────────────┐
│ PICKS (customers)                                                                      │
│  Picks channel          Sending to @oddsintelpicks. Recording never stops.   [● On  ]  │
│                         Next send check :35 · last change 09-15 by telegram             │
│ COLLECTION                                                                             │
│  Coolbet footprint      Collection HTTP only — not a money switch.          [● On  ]  │
│  Feeds bots depend on   ● Coolbet ● Unibet ● Epicbet ● AF odds    [Pause/Run ▾] → Feeds │
└────────────────────────────────────────────────────────────────────────────────────────┘
┌ Real money ─────────────────────────────────────────── border-red-500/40 ─────────────┐
│  Layer                     State                             Control                   │
│  1 Code whitelist          2 bots (coolbet 1x2, coolbet O/U) read-only · deploy        │
│  2 Per-bot switch          0 of 2 on                         in the table ↓            │
│  3 Placement (kill)        ⏸ PAUSED — "OWN-PATH-VERDICT…"     [Resume…]                 │
│  4 Armed                   ● Not armed                       [Arm… (owner)]            │
│  5 Executors (Mac)         Not reported                      read-only                 │
│  6 Per-pick gates          cutoff 3 min · 80 bets · €800/day always on                 │
│  ► CAN STAKE: NO — blocked at layers 2, 3, 4                                           │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

The numbered **layer ladder** is the key visual. It shows every separate layer in gate order, and a
computed **CAN STAKE** line naming the blocking layers. This is the web counterpart of
`coolbet_control --status`. "Unknown" in any layer makes the line read "CAN STAKE: UNKNOWN" (amber),
never "NO". When the chain is fully open, the card background turns `bg-red-500/10` and the line reads
"CAN STAKE: YES — n bots".

Publishing and placement are in **different cards** (I10). The layout itself says they are not one
switch.

### 3.5 Data table with inline switches and row actions

The rows from bots-board-ux-spec §4 are kept. Changes:
- The **Caps column (76 px) is replaced by a Controls column (~168 px)** with two compact switches, so
  the grid keeps fitting 1,216 px:
  - `/picks` switch (B4a). It is enabled only for sim-ledger bots. It is a disabled grey stub, with a
    tooltip explaining why, for shadow, forward-test (shows a "rule" pill instead) and the control.
  - `€` switch (B6). It is rendered only for whitelisted bots. A lock icon replaces it when
    `locked_reason` is set.
  - The Telegram and /performance states stay as the small icons from ux-spec §4.7, next to the
    switches, and are read-only.
- **Row actions:** a `⋯` button at the far right (visible on hover and focus, always visible on
  touch), in the Linear style. Items: Open details · Show on /picks… · Real money… · Retire… (phase B)
  · Change label… (phase B) · Copy bot name. Disabled items keep their tooltip reason.
- **Filter bar** (Stripe list pattern): chips `All · Published · Real-money capable · Silent ·
  Needs a look` + a search input (by name/display name) + sort. Filter state lives in the URL
  (`?f=published&q=sharp`) so a view can be linked.
- Clicking a row (not a control) opens the detail sheet. The switch cells stop propagation.
- **Keyboard** (Linear): `j/k` move the row focus, `Enter` opens the sheet, `Esc` closes it. There is
  no keyboard shortcut for any start-money action.

### 3.6 Detail sheet (right side, replaces today's hand-rolled drawer)

A `Sheet` from the right, `sm:max-w-2xl`, full-screen bottom sheet on mobile. Sticky header: method
pill, display name, `bot_name` mono, capability icons with words. **Tabs:**

1. **Overview**: today's drawer top (3 mini tiles, forest bar, 12-week strip, record).
2. **Settings**: the **capability ladder** for this bot, as three stacked cards:
   `Collect → Publish → Real money`. Each card has the state, the switch or read-only reason, **the
   evidence next to the switch** (the family's verdict chip plus n; design doc: "the page shows the
   evidence next to each switch"), and a `What reads this` disclosure listing the readers from §1.2.
   - Collect card: active or retired, "still collecting 7 d", the Retire / Un-retire button (phase B),
     and the "what retiring does" preview.
   - Publish card: three lines, each with its own state. **/picks** (switch), **Telegram** (via label,
     read-only), **/performance** (via label, read-only). The amber "/picks ≠ Telegram" callout shows
     when they disagree.
   - Real-money card: whitelist ✓/✗, the switch or lock, and a mini copy of the fleet ladder so the
     operator sees that this switch is 1 of 6 layers.
   - Below the cards: "Configuration (from code)", the current fact cards and gates, labelled read-only
     with the export time.
3. **Performance**: the "Other metrics" block, weekly table, CLV detail.
4. **Picks**: the recent picks table (ux-spec §8 rules).
5. **Activity**: `control_changes` for this bot, newest first, as a vertical timeline (Stripe's
   "Events"): icon, `old → new`, actor, source badge (web / telegram / engine / migration), relative time
   with the UTC title, reason in full. Refused and conflict outcomes are shown muted, with their refusal
   text. The tab title carries a count badge.

The sheet URL is `?bot=<name>&tab=settings`, so it can be deep-linked from the Telegram notice (which
includes the link).

### 3.7 Confirmation dialogs (one component, two strengths)

`ConfirmControlDialog`:
- **(a) strength**: a Popover with the one-line consequence plus "Confirm". Reason input optional.
- **(b) strength**: a Dialog with a title in the imperative ("Turn on real money for Coolbet 1×2
  model?"), a consequence block (what reads it, when it takes effect, the live layer chain for money
  controls), a reason `Textarea` (required, counter), and a typed-confirmation `Input` whose placeholder
  shows the exact text (the bot name or the phrase). The confirm button stays disabled until both are
  valid. The destructive or money variant uses a `bg-red-600` button.

Arming uses its own two-step dialog (§2.5). Step 2 is a 6-box code input.

### 3.8 Components: what exists and what to add

`src/components/ui` (shadcn **base-nova**, built on `@base-ui/react`, already a dependency) **has**:
`badge, button, card, dialog, input, label, select, separator, table`.

**Add** with `npx shadcn@latest add <name>` (base-nova generates `@base-ui/react` wrappers, so there
should be **no new npm dependency**. The builder verifies `package.json` is unchanged or explains
why):
`switch`, `tabs`, `sheet`, `tooltip`, `dropdown-menu` (row `⋯`), `alert-dialog` (b-strength
confirms), `popover` (a-strength confirms), `textarea`, `skeleton`, and **toast**. For toast, prefer
base-ui's Toast wrapper. If the registry's toast pulls in `sonner`, that is the only allowed new dep
and it must be called out in the PR. The custom drawer in `bot-drawer.tsx` moves onto `Sheet`, which
brings focus trap, Esc and return focus for free (ux-spec §12).

New app components: `admin/admin-shell.tsx` (sidebar, status block, armed banner),
`bots/fleet-controls-card.tsx`, `bots/real-money-ladder.tsx`, `bots/control-switch.tsx` (the
optimistic/pending/locked/unknown states in one place), `bots/confirm-control-dialog.tsx`,
`bots/arm-dialog.tsx`, `bots/activity-timeline.tsx`, `lib/bot-controls/{client.ts,retire-effects.ts,
types.ts}`.

---

## 4. Copy rules for controls

- Name the switch after what it moves, not the capability you wish it moved: "Show on /picks", not
  "Publish".
- Every disabled control has a tooltip that says why *and* what would change it ("Code whitelist —
  needs a deploy").
- Unknown is written "Unknown" in amber. It is never "Off".
- Times are UTC with a relative title, as in the ux-spec §7 helpers.

---

## 5. Out of scope here (tracked, not built)

- One authoritative **publish** flag that `picks_public_all`, `coolbet_signaler` and /performance all
  read (engine + view change; it resolves I14 and genesis §5.2). This is phase C.
- A per-bot **collect** state read by every writer. This is phase C, after the owner decides #137 B4.
- Splitting `maturity_label`'s hidden jobs (`source_bots` in config, headline keyed on family). Phase C.
- Moving the fleet switches out of `coolbet_session_state`. This is phase 5, and genesis §5.7 lists its
  hazards.
- Config editing (floors, books). This stays code by design.

---

## 6. Phasing (each step: smoke test(s) + review agents; nothing proceeds on an open defect)

Owner rule: every step is reviewed by 1–2 independent agents. **Two agents** for anything that writes
money or control state or adds a migration. **One** for pure UI.

### Phase A: ships first

| Step | Contents | Smoke tests (add to `scripts/smoke_test.py`, run with `--filter`) | Review |
|---|---|---|---|
| A1 | Migration 412: `control_changes` (append-only trigger), `control_challenges`, `admin_set_control`, `admin_arm_real_money`, `coolbet_placer_bots.locked_reason` + OU lock seed, baseline audit rows. `src/lib/admin-auth.ts`. | `CONTROL-AUDIT-APPEND-ONLY` (DB: UPDATE/DELETE on `control_changes` raise) · `CONTROL-FN-REFUSES` (DB, in a rolled-back txn: placer ON for a locked, retired or unseeded bot → `refused`; `real_money_armed=true` via `admin_set_control` → refused; missing reason on a (b) control → refused) · `CONTROL-TABLES-NOT-ANON` (extends `ANON-LEAST-PRIVILEGE`) · the existing `OU-CALIBRATOR-DOMAIN-MISMATCH` and `PLACEMENT-GATE-*` must stay green | 2 agents |
| A2 | `/api/admin/controls` + audit GET. The Telegram webhook `/pause /resume /pausepicks /resumepicks`, the buttons, and a new `/disarm` all go through the function. The engine `coolbet_state.set_*` setters write audit rows. Operator notify helper. | `CONTROL-WRITES-ONLY-VIA-FN` (source: no `.update(` / `.upsert(` on `coolbet_session_state`, `coolbet_placer_bots`, `bots` in `src/app/api/**` except the rpc wrapper) · `CONTROL-TELEGRAM-AUDITED` (source: each webhook command calls the rpc) · `CONTROL-ENGINE-SETTERS-AUDITED` (source) · `CONTROL-ROUTE-SUPERADMIN` (source: every handler calls `requireSuperadmin` / `requireOwner` before parsing the body) | 2 agents |
| A3 | Arming, two-step (§2.5): routes, Telegram code, dialog. | `CONTROL-ARM-TWO-STEP` (source + DB: the arm path needs a consumed, unexpired challenge; the code is hashed; max 3 attempts) · `CONTROL-NO-SINGLE-CLICK-MONEY` (source: no per-bot route or component references `real_money_armed = true`; placer ON requires `confirm_text === bot_name` and a reason server-side) | 2 agents |
| A4 | Admin shell (sidebar, status block, armed banner). Fleet Controls card + Real-money ladder with CAN STAKE. Components added (§3.8). | `ADMIN-SHELL-STATUS-FAIL-UNKNOWN` (source: unreadable state renders "Unknown", start directions disabled) · `CONTROLS-PUBLISH-PLACEMENT-SEPARATE-CARDS` (source) · reviewer screenshots at 1440 and 375 | 1 agent |
| A5 | Table Controls column (/picks + € switches), row `⋯` menu, detail Sheet with Overview / Settings / Performance / Picks / Activity. `/admin/shadow-bots` placer toggle and daemons pause re-pointed to the function. | `PICKS-SWITCH-SIM-ONLY` (source + fn) · `PICKS-SWITCH-NOT-PERFORMANCE` (source: nothing in `bot-aggregates.ts` / `performance/**` reads `show_on_picks`, I12) · `CONTROL-SWITCH-OPTIMISTIC-SAFE-ONLY` (source: optimistic path only for OFF / pause / disarm) | 1 agent + 1 for the shadow-bots re-point |

**Docs in the same commits** (CLAUDE.md "When done"): `WORKFLOWS.md` § Pause semantics (the page and
`/disarm` as writers; the audit log), `docs/COOLBET_OWN_BETTING.md` + `docs/SYSTEM_MAP.md` (the arming
path now exists on the web, owner-only with a code; the layer ladder), `docs/RELIABILITY_LEDGER.md`
(the no-reason-field toggle pattern is closed by `control_changes`), `ROADMAP.md` system state,
`PRIORITY_QUEUE.md` #139 progress. Ripple-grep for `coolbet-placer-bots`, `set_real_money_armed`
("no UI, no CLI" in the audit §B4 becomes stale) and `retirement is always a migration`.

### Phase B

B1 Retire / un-retire with the effects preview (+ `RETIRE-EFFECTS-MAP-COVERS-WRITERS`,
`UNRETIRE-COMES-BACK-COLLECT-ONLY`). B2 maturity label with the impact preview and the n ≥ 30 rule for
`calibrated` (+ `MATURITY-CHANGE-IMPACT-PREVIEW`, `CALIBRATED-NEEDS-EVIDENCE`). B3 display name. B4
remove the old placer / daemon routes and the shadow-bots controls. B5 optional arm auto-expiry (Q2).
Review: 2 agents for B1/B2 (they change what generates and publishes), 1 for B3/B4.

### Phase C (engine work; each unlocks a switch)

C1 one `publish` flag read by the view, the signaler and /performance. The /picks switch becomes
**Publish** and the "/picks ≠ Telegram" state becomes impossible (I14). C2 `collect` state read by
every writer, after the #137 B4 decision. C3 the Mac reports executor status (loaded / parked plist +
`CAN_STAKE`) to a `coolbet_session_state` column every 5 min, and F8 shows it. C4 split
`maturity_label`'s hidden jobs. Each gets 2 reviewers.

---

## 7. Open questions for the owner (the build proceeds on the recommended default)

1. **Should the web be able to ARM real money at all?** Today only a hand-run SQL can do it.
   **Default: yes, owner-only, two-step with a 6-digit code sent to your Telegram chat.** Disarm is one
   click for any superadmin and via `/disarm`.
2. **Should arming expire on its own (e.g. after 7 days, re-arm with a fresh reason)?** This needs a
   small `placement_gate` change (`real_money_armed_until`). **Default: yes, 7 days, shipped in phase
   B**, so a forgotten arm cannot outlive the reason it was given for.
3. **Resume placement from the page, or Telegram only?** **Default: the page, with typed confirmation
   and a reason**, and an extra phrase when the current pause reason is strategic. Resuming stakes
   nothing without arming.
4. **The /picks switch before the unified publish flag exists.** Ship it now, honestly labelled "Show on
   /picks" with the Telegram state beside it, or wait for phase C? **Default: ship now as labelled**,
   with the "/picks ≠ Telegram" warning. It is what `show_on_picks` already does, and waiting keeps
   curation migration-only.
5. **Retire and un-retire from the page (today 32 migrations).** Should retiring also stop a retired
   pipeline bot's shadow writes? **Default: page retire yes (phase B, typed name + reason, audited); a
   retired bot keeps collecting** (your 05-20 / 09-18 decision, I21) until you decide #137 B4.
